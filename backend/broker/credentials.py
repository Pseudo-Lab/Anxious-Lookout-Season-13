"""Operator ChatGPT login held by the broker: managed Codex app-server, file store (#60).

The operator logs in once with `codex login --device-auth` in the broker Pod. The
app-server refreshes on its own about five minutes before expiry; issuing access
with less than `margin` seconds left forces `account/read {refreshToken: true}`.
Only the access token leaves this module. The refresh token stays in auth.json.
"""
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
from urllib.parse import urlsplit

from app.codex_policy import CodexFailure
from ops.access_snapshot import selected_file

COMMAND = ['app-server', '--listen', 'stdio://',
           '-c', 'cli_auth_credentials_store="file"', '-c', 'forced_login_method="chatgpt"',
           '-c', 'analytics.enabled=false', '-c', 'otel.exporter="none"',
           '-c', 'features.daemon_auto_start=false', '-c', 'features.code_mode_host=false',
           '-c', 'features.plugins=false', '-c', 'features.apps=false', '-c', 'web_search="disabled"']


def loopback_override(value):
    """A mock OAuth endpoint for development checks only: http on a loopback address."""
    parsed = urlsplit(value or '')
    if parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1', '::1'} and parsed.port and not parsed.username:
        return value
    return None


class AppServer:
    """One stdio JSON-RPC session. Server-initiated requests are refused."""
    def __init__(self, binary, home, popen=subprocess.Popen):
        environment = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8', 'HOME': str(home), 'CODEX_HOME': str(home)}
        # Only a loopback mock may replace the refresh endpoint; any other value is ignored.
        override = loopback_override(os.environ.get('CODEX_REFRESH_TOKEN_URL_OVERRIDE'))
        if override:
            environment['CODEX_REFRESH_TOKEN_URL_OVERRIDE'] = override
        self.process = popen([binary, *COMMAND], env=environment, cwd=str(home), stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.replies, self.next_id, self.lock = queue.Queue(), 0, threading.Lock()
        try:
            threading.Thread(target=self.read, daemon=True).start()
            self.rpc('initialize', {'clientInfo': {'name': 'codex_broker', 'version': '1'}, 'capabilities': {'experimentalApi': True}})
            self.send({'method': 'initialized'})
        except BaseException:
            self.close()  # Never leave a second managed writer behind a failed start.
            raise

    def read(self):
        for line in self.process.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if 'method' in message and 'id' in message:
                self.send({'id': message['id'], 'error': {'code': -32601, 'message': 'unavailable'}})
            elif 'id' in message:
                self.replies.put(message)

    def send(self, message):
        self.process.stdin.write(json.dumps(message)+'\n')
        self.process.stdin.flush()

    def rpc(self, method, params, timeout=30):
        with self.lock:
            self.next_id += 1
            ident = self.next_id
            self.send({'id': ident, 'method': method, 'params': params})
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    message = self.replies.get(timeout=max(0.01, deadline-time.monotonic()))
                except queue.Empty:
                    break
                if message.get('id') == ident:
                    return message
            raise CodexFailure('unavailable')

    def alive(self):
        return self.process.poll() is None

    def close(self):
        try:
            self.process.stdin.close()
            self.process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            self.process.kill()
            self.process.wait(timeout=5)


class Credentials:
    def __init__(self, home, account, *, margin=1800, binary='/usr/local/bin/codex', server=AppServer, clock=time.time):
        self.home, self.account, self.margin = Path(home), account, margin
        self.home.mkdir(mode=0o700, exist_ok=True)  # Subdirectory of the volume: 0700 regardless of fsGroup.
        self.binary, self.server_factory, self.clock = binary, server, clock
        self.server, self.server_source = None, None
        self.lock, self.forced_at = threading.Lock(), None

    def source(self):
        path = self.home/'auth.json'
        try:
            info = path.lstat()
        except FileNotFoundError:
            raise CodexFailure('not_configured') from None
        return path, (info.st_ino, info.st_mtime_ns)

    def read(self):
        path, _ = self.source()
        try:
            token, account, expiry = selected_file(str(path), expected_uid=os.geteuid())
        except CodexFailure as failure:
            if failure.reason == 'auth_expired':
                return None
            raise
        if account != self.account:
            raise CodexFailure('policy_refused')
        return token, account, expiry

    def ensure_server(self):
        _, identity = self.source()
        # A new login or logout replaces auth.json; restart so the app-server reloads it.
        if self.server is None or not self.server.alive() or self.server_source != identity:
            if self.server is not None:
                self.server, previous = None, self.server
                previous.close()
            self.server = self.server_factory(self.binary, self.home)
            self.server_source = identity
        return self.server

    def access(self, *, force=False):
        """(token, accountId, expiresAt) with at least `margin` seconds left, or CodexFailure."""
        with self.lock:
            current = self.read()
            if force and self.forced_at is not None and self.clock() - self.forced_at < 60:
                force = False  # Any runner may report a 401; at most one forced refresh a minute.
            if current and not force and current[2] - self.clock() >= self.margin:
                return current
            server = self.ensure_server()
            if force:
                self.forced_at = self.clock()
            # A failed workspace-routing call after a successful refresh is not a refresh
            # failure; the file decides.
            server.rpc('account/read', {'refreshToken': True}, timeout=60)
            self.server_source = self.source()[1]  # The app-server itself rewrote auth.json.
            renewed = self.read()
            if not renewed or renewed[2] - self.clock() < self.margin or (force and current and renewed[0] == current[0]):
                raise CodexFailure('auth_expired')
            return renewed

    def logged_in(self):
        try:
            self.source()
            return True
        except CodexFailure:
            return False

    def close(self):
        if self.server is not None:
            self.server.close()
