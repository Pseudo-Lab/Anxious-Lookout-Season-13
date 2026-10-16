"""PID-1 workspace executor. No auth/control/native mounts; Unix peers only."""
import json
import os
from pathlib import Path
import resource
import selectors
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import uuid

SOCKET = '/run/shell-executor/command.sock'
WORKSPACE = Path('/state/workspace')
RUNNER_UID = 10001
EXECUTOR_UID = 20002
MAX_REQUEST = 20000


def setting(name, default, minimum, maximum):
    value = int(os.environ.get(name, default))
    if not minimum <= value <= maximum:
        raise ValueError('Invalid executor limit')
    return value


def reply(connection, result):
    connection.settimeout(2)
    try:
        connection.sendall(json.dumps(result, ensure_ascii=True).encode() + b'\n')
    except (OSError, TimeoutError):
        pass


def failure(code, status='rejected'):
    return {'status': status, 'error': {'code': code}}


def clean_namespace():
    """Kill escaped sessions too. Safe only as PID 1 in our dedicated namespace."""
    if os.getpid() != 1 or os.getuid() != EXECUTOR_UID:
        raise RuntimeError('Dedicated executor namespace required')
    deadline = time.monotonic() + 5
    while True:
        try:
            os.kill(-1, signal.SIGKILL)
        except ProcessLookupError:
            pass
        while True:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                pid = 0
            if not pid:
                break
        # Only PID 1 may remain. Reap descendants adopted after double-fork.
        if not any(p.name.isdecimal() and p.name != '1' for p in Path('/proc').iterdir()):
            return
        if time.monotonic() >= deadline:
            os._exit(70)  # Never release a lease with unproved descendant cleanup.
        time.sleep(.01)


class Executor:
    def __init__(self):
        self.wall = setting('SHELL_WALL_SECONDS', 30, 1, 300)
        self.limit = setting('SHELL_OUTPUT_BYTES', 65536, 1024, 1048576)
        self.lease = threading.Lock()
        self.connections = threading.BoundedSemaphore(8)

    def execute(self, connection, request):
        if type(request) is not dict or set(request) != {'version', 'requestId', 'command', 'cwd'}:
            return failure('shell_invalid_request')
        if type(request['version']) is not int or request['version'] != 1:
            return failure('shell_invalid_request')
        try:
            if str(uuid.UUID(request['requestId'])) != request['requestId']:
                raise ValueError()
            if not isinstance(request['command'], str) or not 1 <= len(request['command']) <= 16384 or '\x00' in request['command']:
                raise ValueError()
            cwd = request['cwd']
            if not isinstance(cwd, str) or Path(cwd).is_absolute() or '\x00' in cwd:
                raise ValueError()
            directory = (WORKSPACE / cwd).resolve(strict=True)
            if not directory.is_dir() or not directory.is_relative_to(WORKSPACE):
                raise ValueError()
        except (ValueError, TypeError, AttributeError, OSError):
            return failure('shell_invalid_request')
        if not self.lease.acquire(blocking=False):
            return failure('shell_busy')
        started = time.monotonic()
        out, err = bytearray(), bytearray()
        truncated, reason, process = False, None, None
        try:
            process = subprocess.Popen([sys.executable, '-I', __file__, '--child', request['command']],
                cwd=directory, env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': str(WORKSPACE),
                                    'LANG': 'C.UTF-8', 'TMPDIR': '/tmp'},
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                start_new_session=True, close_fds=True)
            connection.setblocking(False)
            with selectors.DefaultSelector() as selector:
                selector.register(connection, selectors.EVENT_READ, None)
                for stream, buffer in ((process.stdout, out), (process.stderr, err)):
                    os.set_blocking(stream.fileno(), False)
                    selector.register(stream, selectors.EVENT_READ, buffer)
                while True:
                    if time.monotonic() - started >= self.wall:
                        reason = 'shell_timeout'
                        break
                    for key, _ in selector.select(.025):
                        if key.fileobj is connection:
                            # Disconnect or extra input cancels the entire lease.
                            connection.recv(1)
                            reason = 'shell_cancelled'
                            break
                        chunk = os.read(key.fd, 8192)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        remaining = self.limit - len(out) - len(err)
                        key.data.extend(chunk[:remaining])
                        truncated |= len(chunk) > remaining
                    if reason or (process.poll() is not None and len(selector.get_map()) == 1):
                        break
            exit_code = process.poll()
            status = 'timed_out' if reason == 'shell_timeout' else 'failed' if reason or exit_code != 0 else 'completed'
            result = {'requestId': request['requestId'], 'status': status, 'exitCode': exit_code,
                      'stdout': out.decode('utf-8', 'replace'), 'stderr': err.decode('utf-8', 'replace'),
                      'truncated': truncated, 'durationMs': round((time.monotonic()-started)*1000),
                      'wallSeconds': self.wall}
            if reason:
                result['error'] = {'code': reason}
            return result
        except Exception:
            return failure('shell_unavailable', 'failed')
        finally:
            clean_namespace()
            if process:
                process.wait(timeout=1)
                process.stdout.close()
                process.stderr.close()
            self.lease.release()

    def handle(self, connection):
        try:
            _, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != RUNNER_UID:
                reply(connection, failure('shell_peer_refused'))
                return
            connection.settimeout(2)
            raw = bytearray()
            while b'\n' not in raw and len(raw) <= MAX_REQUEST:
                part = connection.recv(min(4096, MAX_REQUEST+1-len(raw)))
                if not part:
                    return
                raw.extend(part)
            if len(raw) > MAX_REQUEST or raw.count(b'\n') != 1 or not raw.endswith(b'\n'):
                reply(connection, failure('shell_invalid_request'))
                return
            try:
                request = json.loads(raw)
            except (ValueError, UnicodeError):
                reply(connection, failure('shell_invalid_request'))
                return
            result = self.execute(connection, request)
            reply(connection, result)
            print(json.dumps({'event': 'shell_receipt', 'status': result['status'],
                              'code': result.get('error', {}).get('code'),
                              'durationMs': result.get('durationMs')}), flush=True)
        except (OSError, ValueError):
            pass
        finally:
            connection.close()
            self.connections.release()

    def serve(self):
        if os.getpid() != 1 or os.getuid() != EXECUTOR_UID:
            raise RuntimeError('Executor must be dedicated non-root PID 1')
        WORKSPACE.resolve(strict=True)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            if os.path.lexists(SOCKET):
                os.unlink(SOCKET)
            listener.bind(SOCKET)
            os.chmod(SOCKET, 0o660)
            os.chown(SOCKET, -1, RUNNER_UID)
            listener.listen(8)
            while True:
                connection, _ = listener.accept()
                if not self.connections.acquire(blocking=False):
                    connection.close()
                    continue
                threading.Thread(target=self.handle, args=(connection,), daemon=True).start()


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--child':
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
        resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
        os.execve('/bin/bash', ['bash', '--noprofile', '--norc', '-c', sys.argv[2]], dict(os.environ))
    elif len(sys.argv) == 1:
        Executor().serve()
    else:
        raise SystemExit(2)
