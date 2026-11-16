"""Persistent native MCP adapter; runner-only authority and write-ahead fence."""
import json
import os
from pathlib import Path
import shutil
import socket
import tempfile
import threading
import time
import uuid

import httpx

from app.codex_policy import CodexFailure
from app.page_scope import tool_allowed
from app.research import mutation_fingerprint
from app.research_tools import definitions
from .research_authority import AuthorityError, Bootstrap, Call, Epochs, PeerConnection
from .research_bridge import refused
from .research_fence import BINDING, MUTATIONS, MutationFence


def frame_call(frame):
    if (not isinstance(frame, dict) or frame.get('jsonrpc') != '2.0'
            or frame.get('method') != 'tools/call' or 'id' not in frame):
        raise AuthorityError('call_shape')
    params = frame.get('params')
    if not isinstance(params, dict):
        raise AuthorityError('call_shape')
    meta = params.get('_meta')
    if not isinstance(meta, dict) or not isinstance(meta.get('x-codex-turn-metadata'), dict):
        raise AuthorityError('call_metadata')
    native = meta['x-codex-turn-metadata']
    if meta.get('threadId') != native.get('thread_id'):
        raise AuthorityError('call_metadata')
    return Call.of(meta.get('threadId'), native.get('turn_id'), meta.get('callId'),
                   params.get('name'), params.get('arguments'))


class PersistentResearchBridge:
    def __init__(self, native, parent=Path('/control'), fence=None):
        self.native = native
        control = native.auth.control if native.auth is not None else parent
        self.fence = fence or MutationFence(control, os.environ['RUNNER_ACCOUNT_ID'], os.environ['RUNNER_STATE_ID'])
        self.fence_blocked = self.fence.pending() is not None
        self.root = Path(tempfile.mkdtemp(prefix='research-', dir=parent))
        self.socket_path = self.root / 'mcp.sock'
        self.bootstrap = Bootstrap(self.root)
        self.epochs, self.epoch, self.turn = Epochs(), None, None
        self.generation, self.bound_thread = None, None
        self.lock = threading.RLock()
        self.registered = threading.Event()
        self.closed, self.calls_disabled = False, False
        self.ended, self.completed = True, set()
        self.slots = threading.BoundedSemaphore(4)
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        self.listener.bind(str(self.socket_path))
        os.chmod(self.socket_path, 0o600)
        self.listener.listen(4)
        self.listener.settimeout(.25)
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def config(self):
        return {'research': {'command': '/usr/local/bin/python', 'enabled': True, 'environment_id': 'local',
            'args': ['-I', '-B', '/app/runner/research_mcp_persistent.py', str(self.bootstrap.path)],
            'startup_timeout_sec': 20.0, 'tool_timeout_sec': 30.0}}

    def process_started(self):
        self.process_lost()
        self.registered.clear()
        self.generation = str(uuid.uuid4())
        self.bootstrap.arm(self.native.process.pid, self.generation, self.socket_path)
        self.bound_thread = None

    def process_lost(self):
        self.revoke()
        with self.lock:
            self.ended = True  # Native process death proves model drain, never remote mutation drain.
            self.bound_thread = None
            peer = self.bootstrap.peer
        if peer is not None:
            try:
                peer.socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.bootstrap.close()

    def admission_ready(self):
        # Cached fence state: admission/state/interrupt readers do no disk I/O.
        if self.fence_blocked:
            return False
        with self.epochs.condition:
            epoch = self.epoch
            return epoch is None or (epoch.revoked and not epoch.inflight and not epoch.unsettled and self.ended)

    def prepare(self, session, request, scope):
        if not self.admission_ready():
            raise CodexFailure('unavailable')
        expected = self.native.mapping[session].get('thread')
        # MCP managers are thread-owned. A switch gets a fresh pre-model boot;
        # warm turns of the same thread retain both app-server and MCP child.
        if self.bound_thread is not None and (expected != self.bound_thread or not self.bootstrap.peer.alive()):
            self.native.reset_thread_tools()

    def before_model(self, turn):
        try:
            self._before_model(turn)
        except AuthorityError:
            raise CodexFailure('policy_refused') from None

    def _before_model(self, turn):
        result = self.native.rpc('mcpServerStatus/list', {'threadId': turn['thread'],
            'serverName': 'research', 'detail': 'toolsAndAuthOnly', 'limit': 100})
        if not self.registered.wait(2):
            raise CodexFailure('policy_refused')
        entries = result.get('data')
        expected = {entry['name'] for entry in definitions()}
        if (not isinstance(entries, list) or len(entries) != 1 or result.get('nextCursor') is not None):
            raise CodexFailure('policy_refused')
        entry = entries[0]
        tools = entry.get('tools')
        if (entry.get('name') != 'research' or entry.get('runtimeStatus') != 'connected'
                or entry.get('toolsError') is not None or not isinstance(tools, dict)
                or set(tools) != expected):
            raise CodexFailure('policy_refused')
        if self.bootstrap.phase == 'registered':
            self.bootstrap.catalogue_ready(self.generation)
        peer = self.bootstrap.activate(self.generation)
        with self.lock:
            self.bound_thread, self.turn, self.ended = turn['thread'], turn, False
            self.completed.clear()
            self.epoch = self.epochs.begin(self.generation, turn['session'], turn['request'],
                turn['token'], turn['scope'], turn['thread'], peer)

    def bind(self, turn):
        lost = False
        with self.lock:
            epoch = self.epoch
            if epoch is None or epoch.request != turn['request'] or epoch.session != turn['session']:
                raise CodexFailure('policy_refused')
            self.epochs.bind(epoch, turn['turnId'])
            if (turn['thread'], turn['turnId']) in self.completed:
                self.ended = True
                self.epochs.revoke(epoch)
            lost = epoch.peer_lost
        if lost:
            self.abort(turn['session'], turn['request'], turn['thread'], turn['turnId'])

    def abort(self, session, request, thread, turn):
        abort = getattr(self.native, 'abort_research_turn', None)
        if abort is not None:
            try:
                abort(session, request, thread, turn)
            except Exception:
                pass  # Never replay a model/tool after a transport failure.

    def note(self, message):
        """Original native stdout only. Never HTTP, DB or durable writes here."""
        with self.lock:
            epoch = self.epoch
            if epoch is None:
                return
            params = message.get('params')
            if not isinstance(params, dict):
                return
            if message.get('method') == 'turn/completed':
                turn = params.get('turn')
                identity = turn.get('id') if isinstance(turn, dict) else None
                if params.get('threadId') == epoch.thread and isinstance(identity, str):
                    self.completed.add((epoch.thread, identity))
                    if identity == epoch.turn:
                        self.ended = True
                        self.epochs.revoke(epoch)
                return
            if message.get('method') != 'item/started':
                return
            item = params.get('item')
            if not isinstance(item, dict) or item.get('type') != 'mcpToolCall' or item.get('server') != 'research':
                return
            try:
                call = Call.of(params.get('threadId'), params.get('turnId'), item.get('id'),
                    item.get('tool'), item.get('arguments'))
                self.epochs.observe(epoch, 'event', call)
            except AuthorityError:
                pass  # Mismatches grant nothing; duplicates already left a tombstone.

    def revoke(self):
        with self.lock:
            if self.epoch is not None:
                self.epochs.revoke(self.epoch)
            if self.bootstrap.phase == 'active':
                self.bootstrap.deactivate()

    def serve(self):
        while not self.closed:
            try:
                connection, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            if self.slots.acquire(blocking=False):
                threading.Thread(target=self.handle, args=(connection,), daemon=True).start()
            else:
                connection.close()

    def handle(self, connection):
        peer = PeerConnection(connection)
        adopted = False
        try:
            connection.settimeout(2)
            self.bootstrap.register(peer, peer.receive())
            adopted = True
            peer.send({'registered': True})
            self.registered.set()
            connection.settimeout(None)
            while not self.closed:
                message = peer.receive()
                peer.send(self.dispatch(peer, message))
        except Exception:
            pass
        finally:
            lost_turn = None
            if adopted:
                with self.lock:
                    if self.bootstrap.peer is peer:
                        epoch = self.epoch
                        if epoch is not None and not self.ended:
                            epoch.peer_lost = True
                            if epoch.turn is not None:
                                lost_turn = (epoch.session, epoch.request, epoch.thread, epoch.turn)
                        self.revoke()  # A retired peer's EOF cannot cancel the next generation.
            peer.close()
            self.slots.release()
            if lost_turn is not None:
                self.abort(*lost_turn)

    def dispatch(self, peer, message):
        with self.lock:
            if peer is not self.bootstrap.peer or not peer.alive() or self.bootstrap.phase not in {'registered', 'ready', 'active'}:
                return refused()
            if message == {'operation': 'catalog'}:
                return {'tools': [{key: entry[key] for key in ('name', 'description', 'inputSchema')}
                                  for entry in definitions()]}
            epoch, turn = self.epoch, self.turn  # Capture once, never look up the next request.
        if (not isinstance(message, dict) or set(message) != {'operation', 'frame'}
                or message['operation'] != 'call' or epoch is None):
            return refused('validation_error')
        if self.calls_disabled:
            return refused('tool_not_allowed')
        try:
            call = frame_call(message['frame'])
            self.epochs.observe(epoch, 'frame', call, peer=peer)
            work = self.epochs.claim(epoch, call, timeout=20)
        except (AuthorityError, TypeError, ValueError):
            return refused()
        return self.callback(epoch, work, turn)

    def callback(self, epoch, work, turn):
        native, started = self.native, time.monotonic()
        arguments, scope = json.loads(work.call.arguments), json.loads(work.scope)
        mutating = work.call.name in MUTATIONS
        intent, sent, confirmed = None, False, False
        outcome = {'stage': 'binding'}
        try:
            entry = native.mapping.get(work.session)
            if (native.closing or native.blocked_reason or native.active_session != work.session
                    or not entry or entry.get('state') != 'running' or entry.get('request') != work.request
                    or entry.get('thread') != work.call.thread):
                return refused()
            if not tool_allowed(scope, work.call.name, arguments):
                return refused('tool_not_allowed')
            native.check_binding()
            if native.auth:
                outcome['stage'] = 'permission'
                native.auth.permitted_tool(arguments)
            if mutating:
                mutation_id = str(uuid.UUID(arguments.get('mutationId')))
                fingerprint = mutation_fingerprint('tool:' + work.call.name, arguments)
                self.fence_blocked = True
                intent = self.fence.begin(work, mutation_id, fingerprint)
            outcome['stage'] = 'callback'
            with httpx.Client(timeout=15, trust_env=False, follow_redirects=False) as client:
                sent = True
                reply = client.post(native.callback_url,
                    headers={'Authorization': 'Bearer ' + work.token},
                    json={'sessionId': work.session, 'requestId': work.request,
                          'name': work.call.name, 'arguments': arguments})
            outcome.update(status=reply.status_code, stage='callback_body')
            result = reply.json()
            if not isinstance(result, dict):
                return refused('not_ready')
            error = result.get('error')
            from .app import REPLY_CODES
            applied = reply.is_success and 'error' not in result
            not_applied = (400 <= reply.status_code < 500 and isinstance(error, dict)
                           and error.get('code') in REPLY_CODES)
            if mutating and (applied or not_applied):
                self.fence.terminal({key: intent[key] for key in BINDING}, 'applied' if applied else 'not_applied')
                self.fence_blocked = False
                confirmed = True
            outcome.update(stage='callback_done', success=reply.is_success)
            if isinstance(error, dict):
                outcome['code'] = error.get('code')
            if native.auth:
                result = native.auth.sanitize(result)
            return result
        except CodexFailure as failure:
            native.blocked_reason = failure.reason
            return refused(failure.reason)
        except Exception:
            return refused('not_ready')
        finally:
            # Read-only calls have no remote mutation to settle. An intent write
            # failure keeps admission blocked even if HTTP was never sent.
            self.epochs.finish(epoch, work, confirmed=confirmed or not mutating or (not sent and not self.fence_blocked))
            if mutating and self.fence_blocked:
                self.abort(work.session, work.request, work.call.thread, work.call.turn)
            from .app import log_tool_reply
            elapsed = (time.monotonic() - started) * 1000
            log_tool_reply({'params': {'tool': work.call.name, 'callId': work.call.item,
                'threadId': work.call.thread, 'turnId': work.call.turn}}, turn, outcome, elapsed)
            turn['timing']['toolCalls'] += 1
            turn['timing']['toolMs'] += elapsed

    def snapshot(self, session):
        value = self.fence.status()
        if value is None:
            return {'researchStorageState': 'unknown'} if self.fence_blocked else {}
        if value['sessionId'] != session:
            return {}
        if value['phase'] == 'terminal' and self.native.mapping.get(session, {}).get('request') != value['requestId']:
            return {}  # A retained terminal receipt never becomes the next request's storage status.
        with self.epochs.condition:
            drained = self.native.active_session is None and self.ended and (self.epoch is None or not self.epoch.inflight)
        return {'researchFence': value, 'researchDrained': drained}

    def settle(self, session, binding, outcome):
        if binding.get('sessionId') != session or self.native.active_session is not None or not self.ended:
            raise AuthorityError('settlement_not_drained')
        with self.epochs.condition:
            if self.epoch is not None and self.epoch.inflight:
                raise AuthorityError('settlement_not_drained')
        result = self.fence.terminal(binding, outcome)
        self.fence_blocked = False
        with self.epochs.condition:
            if self.epoch is not None and self.epoch.generation == result['native']['generation']:
                self.epoch.unsettled = False
        return result

    def close(self):
        self.closed = True
        self.process_lost()
        self.listener.close()
        self.thread.join(timeout=2)
        shutil.rmtree(self.root)
