"""Runner-private authorization broker; executor never receives API credentials."""
import hmac
import json
import os
from pathlib import PurePosixPath
import secrets
import socket
import struct
import threading
import uuid

from .shell_bridge import failure


def arguments_valid(arguments):
    return (type(arguments) is dict and {'command'} <= set(arguments) <= {'command', 'cwd'}
            and isinstance(arguments['command'], str) and 1 <= len(arguments['command']) <= 16384
            and '\x00' not in arguments['command'] and isinstance(arguments.get('cwd', '.'), str)
            and len(arguments.get('cwd', '.')) <= 1024 and '\x00' not in arguments.get('cwd', '.')
            and not PurePosixPath(arguments.get('cwd', '.')).is_absolute()
            and '..' not in PurePosixPath(arguments.get('cwd', '.')).parts)


class ShellBroker:
    def __init__(self, path, authorize, sanitize, executor='/run/shell-executor/command.sock', wall=30):
        self.path, self.authorize, self.sanitize = path, authorize, sanitize
        self.executor, self.wall = executor, wall
        self.lock, self.lease, self.turn = threading.RLock(), None, None
        self.connections = set()
        self.slots = threading.BoundedSemaphore(4)
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        path.unlink(missing_ok=True)
        self.socket.bind(str(path))
        os.chmod(path, 0o600)
        self.socket.listen(4)
        self.closed = False
        threading.Thread(target=self.serve, daemon=True).start()

    def activate(self, turn):
        self.revoke()
        with self.lock:
            self.turn, self.lease = turn, secrets.token_hex(32)
            return self.lease

    def revoke(self):
        with self.lock:
            self.lease, self.turn = None, None
            for connection in tuple(self.connections):
                try: connection.shutdown(socket.SHUT_RDWR)
                except OSError: pass

    def serve(self):
        while not self.closed:
            try: connection, _ = self.socket.accept()
            except OSError: return
            if not self.slots.acquire(blocking=False):
                connection.close()
                continue
            threading.Thread(target=self.handle, args=(connection,), daemon=True).start()

    def handle(self, connection):
        try:
            connection.settimeout(2)
            _, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != 10001:
                return
            with connection.makefile('rb') as stream:
                line = stream.readline(120001)
            if len(line) > 120000 or not line.endswith(b'\n'):
                return
            request = json.loads(line)
            result = self.execute(request)
            connection.sendall(json.dumps(result, ensure_ascii=True).encode() + b'\n')
        except Exception:
            try: connection.sendall(json.dumps(failure('shell_unavailable')).encode() + b'\n')
            except OSError: pass
        finally:
            connection.close()
            self.slots.release()

    def execute(self, request):
        with self.lock:
            if (type(request) is not dict or set(request) != {'lease', 'arguments'}
                    or not isinstance(request['lease'], str) or not self.lease
                    or not hmac.compare_digest(request['lease'], self.lease) or self.turn is None):
                return failure('tool_not_allowed')
            turn, lease = self.turn, self.lease
        if not arguments_valid(request['arguments']):
            return failure('shell_invalid_request')
        if not self.authorize(turn, request['arguments']):
            return failure('tool_not_allowed')
        # Authorization may block on HTTP. Recheck the same lease before opening
        # the executor connection, and keep the connection revocable thereafter.
        with self.lock:
            if self.lease != lease or self.turn is not turn:
                return failure('tool_not_allowed')
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.settimeout(self.wall + 7)
            try:
                connection.connect(self.executor)
            except OSError:
                connection.close()
                return failure('shell_unavailable')
            _, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != 20002:
                connection.close()
                return failure('shell_unavailable')
            self.connections.add(connection)
            identity = str(uuid.uuid4())
            payload = {'version': 1, 'requestId': identity, 'command': request['arguments']['command'],
                       'cwd': request['arguments'].get('cwd', '.')}
            encoded = json.dumps(payload, ensure_ascii=False).encode() + b'\n'
            if len(encoded) > 20000:
                self.connections.remove(connection)
                connection.close()
                return failure('shell_invalid_request')
        def expire():
            try: connection.shutdown(socket.SHUT_RDWR)
            except OSError: pass
        timer = threading.Timer(self.wall + 7, expire)
        timer.daemon = True
        timer.start()
        try:
            connection.sendall(encoded)
            with connection.makefile('rb') as stream:
                line = stream.readline(8 * 1024 * 1024 + 1)
            if len(line) > 8 * 1024 * 1024 or not line.endswith(b'\n'):
                raise ValueError('Invalid executor reply')
            result = json.loads(line)
            if type(result) is not dict or result.get('requestId', identity) != identity:
                raise ValueError('Wrong executor reply')
            return self.sanitize(result)
        finally:
            timer.cancel()
            with self.lock:
                self.connections.discard(connection)
                connection.close()

    def close(self):
        self.closed = True
        self.revoke()
        self.socket.close()
        self.path.unlink(missing_ok=True)
