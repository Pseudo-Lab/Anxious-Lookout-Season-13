"""Persistent MCP authority primitives, independent of native RPC and HTTP.

The runtime adapter must finish bootstrap/catalogue before turn/start and feed
unmodified native events and child frames into one Epochs instance. This module
does not grant API access, settle remote mutations, or start a native process.
"""
from dataclasses import dataclass, field
import array
import hmac
import json
import math
import os
from pathlib import Path
import secrets
import select
import socket
import stat
import struct
import threading
import time


class AuthorityError(ValueError):
    pass


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AuthorityError('duplicate_key')
        result[key] = value
    return result


def strict_json(data):
    def reject_constant(_):
        raise AuthorityError('invalid_number')
    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise AuthorityError('invalid_number')
        return result
    return json.loads(data, object_pairs_hook=_pairs, parse_constant=reject_constant,
                      parse_float=finite_float)


def canonical(value):
    # Serialize once. Work cannot retain references to caller-mutable arguments.
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(',', ':'), allow_nan=False).encode()


def _process_stat(pid):
    data = Path(f'/proc/{pid}/stat').read_text()
    # comm may itself contain spaces and parentheses; fields after its last ')'
    # start at field 3 (state). starttime is field 22.
    fields = data[data.rindex(')') + 2:].split()
    return int(fields[1]), int(fields[19])


class ProcessIdentity:
    """A live kernel process, never a numeric PID/argv/descendant heuristic."""
    def __init__(self, pid):
        self.pid, self.fd = pid, None
        self.lock = threading.Lock()
        try:
            before = _process_stat(pid)
            self.fd = os.pidfd_open(pid, 0)
            os.set_inheritable(self.fd, False)
            after = _process_stat(pid)
            self.parent, self.start = after
            if before != after or not self.alive():
                raise AuthorityError('process_changed')
        except Exception:
            self.close()
            raise AuthorityError('process_unavailable') from None

    def alive(self):
        with self.lock:
            try:
                return (self.fd is not None and not select.select([self.fd], [], [], 0)[0]
                        and _process_stat(self.pid) == (self.parent, self.start))
            except (OSError, ValueError):
                return False

    def close(self):
        with self.lock:
            if self.fd is not None:
                descriptor, self.fd = self.fd, None
                os.close(descriptor)


class PeerConnection:
    """Verify sender credentials on *every* chunk, including inherited-fd writes.

    SO_PEERCRED alone records the original connector, even after fd handoff.
    SO_PASSCRED/SCM_CREDENTIALS additionally identifies the actual writer. Fixed
    child code never forks or hands off descriptors; both checks remain required.
    """
    LIMIT = 4 * 1024 * 1024

    def __init__(self, connection):
        self.socket, self.buffer = connection, b''
        connection.set_inheritable(False)
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        self.credentials = struct.unpack('3i', connection.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))
        self.identity = None
        self.parent_identity = None

    def alive(self):
        identity, parent = self.identity, self.parent_identity
        try:
            if (identity is None or parent is None or not identity.alive()
                    or not parent.alive() or self.socket.fileno() < 0):
                return False
            poll = select.poll()
            ended = select.POLLHUP | select.POLLERR | select.POLLNVAL | select.POLLRDHUP
            poll.register(self.socket, ended)
            return not any(events & ended for _, events in poll.poll(0))
        except (OSError, ValueError):
            return False

    def receive(self):
        while b'\n' not in self.buffer:
            data, control, flags, _ = self.socket.recvmsg(
                min(65536, self.LIMIT + 1 - len(self.buffer)), socket.CMSG_SPACE(256))
            credentials, invalid = [], bool(flags & (socket.MSG_CTRUNC | socket.MSG_TRUNC))
            for level, kind, value in control:
                if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                    # Refuse descriptor handoff without leaking received fds.
                    descriptors = array.array('i')
                    descriptors.frombytes(value[:len(value) - len(value) % descriptors.itemsize])
                    for descriptor in descriptors:
                        os.close(descriptor)
                    invalid = True
                elif level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS and len(value) == 12:
                    credentials.append(struct.unpack('3i', value))
                else:
                    invalid = True
            if not data:
                raise EOFError()
            if invalid or credentials != [self.credentials]:
                raise AuthorityError('peer_changed')
            self.buffer += data
            if len(self.buffer) > self.LIMIT:
                raise AuthorityError('message_limit')
        if self.identity is not None and not self.alive():
            raise AuthorityError('peer_exited')
        line, self.buffer = self.buffer.split(b'\n', 1)
        return strict_json(line)

    def send(self, value):
        data = canonical(value) + b'\n'
        if len(data) > self.LIMIT:
            raise AuthorityError('message_limit')
        self.socket.sendall(data)

    def close(self):
        self.socket.close()
        identity, self.identity = self.identity, None
        if identity is not None:
            identity.close()


class Bootstrap:
    """One-use pre-model registration; caller serializes native lifecycle RPCs.

    `catalogue_ready` is an external native-RPC proof, not a child assertion.
    This state machine cannot learn/replace peers while ready or active.
    """
    def __init__(self, root):
        self.root = Path(root)
        info = self.root.lstat()
        if (self.root != self.root.resolve() or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o700):
            raise AuthorityError('bootstrap_directory')
        self.path = self.root / 'bootstrap.json'
        self.lock = threading.Lock()
        self.phase, self.parent, self.peer = 'closed', None, None
        self.nonce = self.generation = self.deadline = None

    def arm(self, parent_pid, generation, socket_path, timeout=20):
        with self.lock:
            if self.phase != 'closed' or not isinstance(generation, str) or not generation:
                raise AuthorityError('bootstrap_phase')
            parent = ProcessIdentity(parent_pid)
            nonce = secrets.token_hex(32)
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, 'wb') as target:
                    target.write(canonical({'socket': str(socket_path), 'nonce': nonce}))
                    target.flush()
                    os.fsync(target.fileno())
            except Exception:
                parent.close()
                raise
            self.parent, self.nonce, self.generation = parent, nonce, generation
            self.deadline, self.phase = time.monotonic() + timeout, 'bootstrap'

    def register(self, peer, message):
        with self.lock:
            # Reject unauthenticated peers before inspecting/adopting any PID.
            if (self.phase != 'bootstrap' or time.monotonic() >= self.deadline
                    or not isinstance(message, dict) or set(message) != {'operation', 'nonce'}
                    or message.get('operation') != 'register' or not isinstance(message.get('nonce'), str)
                    or not message['nonce'].isascii() or not hmac.compare_digest(message['nonce'], self.nonce)):
                raise AuthorityError('registration_refused')
            if peer.credentials[1] != os.getuid() or not self.parent.alive():
                raise AuthorityError('registration_refused')
            identity = ProcessIdentity(peer.credentials[0])
            try:
                if identity.parent != self.parent.pid or not self.parent.alive() or not identity.alive():
                    raise AuthorityError('registration_refused')
                # Remove the only bootstrap secret before publishing registration.
                self.path.unlink()
            except Exception:
                identity.close()
                raise
            self.nonce = None
            peer.parent_identity = self.parent
            peer.identity, self.peer, self.phase = identity, peer, 'registered'

    def catalogue_ready(self, generation):
        with self.lock:
            if (self.phase != 'registered' or generation != self.generation
                    or not self.parent.alive() or not self.peer.alive()):
                raise AuthorityError('bootstrap_incomplete')
            self.phase = 'ready'

    def activate(self, generation):
        with self.lock:
            if (self.phase != 'ready' or generation != self.generation
                    or not self.parent.alive() or not self.peer.alive()):
                raise AuthorityError('bootstrap_incomplete')
            self.phase = 'active'
            return self.peer

    def deactivate(self):
        with self.lock:
            if self.phase != 'active':
                raise AuthorityError('bootstrap_phase')
            self.phase = 'ready'

    def close(self):
        # Runtime must revoke/drain epochs before closing or opening another boot.
        with self.lock:
            self.phase, self.nonce = 'closed', None
            if self.peer is not None:
                self.peer.close()
            if self.parent is not None:
                self.parent.close()
            self.peer = self.parent = None
            self.generation = self.deadline = None
            self.path.unlink(missing_ok=True)


@dataclass(frozen=True)
class Call:
    thread: str
    turn: str
    item: str
    name: str
    arguments: bytes = field(repr=False)

    @classmethod
    def of(cls, thread, turn, item, name, arguments):
        if (any(not isinstance(value, str) or not value or len(value) > 200
                for value in (thread, turn, item, name)) or not isinstance(arguments, dict)):
            raise AuthorityError('call_shape')
        return cls(thread, turn, item, name, canonical(arguments))


@dataclass(frozen=True)
class Work:
    epoch: str
    generation: str
    session: str
    request: str
    token: str = field(repr=False)
    scope: bytes
    call: Call


class Epoch:
    def __init__(self, generation, session, request, token, scope, thread, peer):
        self.id = secrets.token_hex(32)  # Server-only, never passed to the child.
        self.generation, self.session, self.request = generation, session, request
        self.token, self.scope, self.thread, self.peer = token, canonical(scope), thread, peer
        self.turn, self.revoked, self.inflight, self.unsettled = None, False, 0, False
        self.peer_lost = False
        self.items = {}


class Epochs:
    """Bounded join + one-use permits. No HTTP/RPC/disk work under this lock.

    A frame arriving before its event stays with the original Epoch object.
    Callers keep that object; there is no lookup by 'latest turn'. `finish` must
    receive proof of remote completion; timeouts are explicitly unsettled.
    """
    def __init__(self, limit=256):
        self.condition = threading.Condition(threading.Lock())
        self.current, self.limit = None, limit

    def begin(self, generation, session, request, token, scope, thread, peer):
        with self.condition:
            if self.current is not None and (not self.current.revoked or self.current.inflight or self.current.unsettled):
                raise AuthorityError('previous_epoch_not_drained')
            epoch = Epoch(generation, session, request, token, scope, thread, peer)
            self.current = epoch
            return epoch

    def bind(self, epoch, turn):
        with self.condition:
            if (self.current is not epoch or epoch.turn is not None
                    or not isinstance(turn, str) or not turn or len(turn) > 200):
                raise AuthorityError('epoch_binding')
            epoch.turn = turn
            self.condition.notify_all()

    def observe(self, epoch, source, call, peer=None):
        with self.condition:
            if (self.current is not epoch or epoch.revoked or source not in {'event', 'frame'}
                    or not isinstance(call, Call) or call.thread != epoch.thread
                    or (epoch.turn is not None and call.turn != epoch.turn)
                    or (source == 'frame' and peer is not epoch.peer)):
                raise AuthorityError('call_refused')
            key = (call.thread, call.turn, call.item)
            if key not in epoch.items:
                if len(epoch.items) >= self.limit:
                    epoch.revoked = True  # Never evict tombstones to make room.
                    self.condition.notify_all()
                    raise AuthorityError('permit_limit')
                epoch.items[key] = {'call': call, 'event': False, 'frame': False, 'state': 'pending'}
            item = epoch.items[key]
            if item['state'] != 'pending' or item[source] or item['call'] != call:
                if item['state'] == 'pending':
                    item['state'] = 'cancelled'
                self.condition.notify_all()
                raise AuthorityError('duplicate_or_conflicting_call')
            item[source] = True
            self.condition.notify_all()

    def claim(self, epoch, call, timeout=0):
        deadline = time.monotonic() + timeout
        with self.condition:
            key = (call.thread, call.turn, call.item)
            while True:
                item = epoch.items.get(key)
                if (self.current is not epoch or epoch.revoked or item is None
                        or item['state'] != 'pending' or item['call'] != call):
                    raise AuthorityError('permit_refused')
                # Nonblocking pidfd/proc identity and Unix hangup checks belong
                # to claim itself. A delayed reader/revoke is not exit evidence.
                # No HTTP, native RPC or durable storage runs under this lock.
                if not epoch.peer.alive():
                    epoch.revoked = True
                    self.condition.notify_all()
                    raise AuthorityError('peer_exited')
                if epoch.turn is not None:
                    if epoch.turn != call.turn:
                        item['state'] = 'cancelled'
                        raise AuthorityError('permit_refused')
                    if item['event'] and item['frame']:
                        item['state'] = 'claimed'
                        epoch.inflight += 1
                        return Work(epoch.id, epoch.generation, epoch.session, epoch.request,
                                    epoch.token, epoch.scope, call)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    item['state'] = 'cancelled'
                    raise AuthorityError('permit_unmatched')
                self.condition.wait(remaining)

    def revoke(self, epoch):
        with self.condition:
            epoch.revoked = True
            for item in epoch.items.values():
                if item['state'] == 'pending':
                    item['state'] = 'cancelled'
            self.condition.notify_all()

    def finish(self, epoch, work, *, confirmed):
        with self.condition:
            key = (work.call.thread, work.call.turn, work.call.item)
            item = epoch.items.get(key)
            if (work.epoch != epoch.id or item is None or item['call'] != work.call
                    or item['state'] != 'claimed'):
                raise AuthorityError('work_mismatch')
            item['state'] = 'consumed'
            epoch.inflight -= 1
            if not confirmed:
                epoch.unsettled = True
            self.condition.notify_all()
