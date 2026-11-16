"""Trusted MCP-to-API bridge. A generation capability belongs to one admitted request.

The app-server and its MCP child are trusted processes outside the native shell's
network/FS sandbox. The child receives only an ephemeral private socket grant.
API tool tokens stay here, in the runner, and never enter Codex config or its child.
"""
import hmac
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import tempfile
import threading
import time

import httpx

from app.codex_policy import CodexFailure
from app.page_scope import POLICIES, tool_allowed
from app.research_tools import definitions
from .research_mcp import read_message, write_message


def refused(code='native_turn_mismatch'):
    return {'error': {'code': code, 'message': 'Research operation is unavailable'}}


class ResearchBridge:
    def __init__(self, native, parent=Path('/control')):
        self.native = native
        self.root = Path(tempfile.mkdtemp(prefix='research-', dir=parent))
        self.socket_path = self.root / 'mcp.sock'
        self.lock = threading.RLock()
        self.grant = self.capability = self.turn = self.scope = None
        self.request = self.session = None
        self.closed = False
        self.calls_disabled = False  # Only the isolated C1 retained-process benchmark sets this.
        self.slots = threading.BoundedSemaphore(4)
        self.ready = threading.Event()
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(str(self.socket_path))
        os.chmod(self.socket_path, 0o600)
        self.listener.listen(4)
        self.listener.settimeout(.25)
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def prepare(self, session, request, scope):
        with self.lock:
            self.revoke()
            self.session, self.request, self.scope = session, request, scope
            self.capability = secrets.token_hex(32)
            self.grant = self.root / (secrets.token_hex(16) + '.json')
            descriptor = os.open(self.grant, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, 'w') as file:
                json.dump({'socket': str(self.socket_path), 'capability': self.capability}, file)
            return self.config()

    def config(self):
        if self.grant is None:
            return {}
        return {'research': {'command': '/usr/local/bin/python', 'enabled': True, 'environment_id': 'local',
            'args': ['-I', '-B', '/app/runner/research_mcp.py', str(self.grant)],
            'startup_timeout_sec': 20.0, 'tool_timeout_sec': 30.0}}

    def bind(self, turn):
        with self.lock:
            if self.request != turn['request'] or self.session != turn['session']:
                raise CodexFailure('policy_refused')
            self.turn = turn
            self.ready.set()

    def revoke(self):
        with self.lock:
            self.capability = self.turn = self.scope = self.request = self.session = None
            self.ready.set()  # Wake pre-start callers; they will find their grant revoked.
            self.ready = threading.Event()
            if self.grant is not None:
                self.grant.unlink(missing_ok=True)
            self.grant = None

    def serve(self):
        while not self.closed:
            try:
                connection, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            # Requests are serialized by the bridge lock, not the native RPC lock.
            # The turn reader must remain free to receive native completion/interrupt.
            if self.slots.acquire(blocking=False):
                threading.Thread(target=self.handle, args=(connection,), daemon=True).start()
            else:
                connection.close()

    def handle(self, connection):
        try:
            with connection:
                connection.settimeout(25)
                with connection.makefile('rwb') as stream:
                    message = read_message(stream)
                    response = self.dispatch(message)
                    write_message(stream, response)
        except Exception:
            pass
        finally:
            self.slots.release()

    def dispatch(self, message):
        if not isinstance(message, dict):
            return refused()
        with self.lock:
            capability = message.get('capability')
            if (not isinstance(capability, str) or len(capability) != 64 or not capability.isascii() or not self.capability
                    or not hmac.compare_digest(capability, self.capability)):
                return refused()
            if message.get('operation') == 'catalog' and set(message) == {'operation', 'capability'}:
                allowed = POLICIES.get((self.scope or {}).get('page', {}).get('kind'), (None, []))[1]
                return {'tools': [{key: entry[key] for key in ('name', 'description', 'inputSchema')}
                                  for entry in definitions() if entry['name'] in allowed]}
            if self.calls_disabled:
                return refused('tool_not_allowed')
            ready = self.ready
        if message.get('operation') != 'call' or set(message) != {'operation', 'capability', 'name', 'arguments', 'callId'}:
            return refused('validation_error')
        ready.wait(timeout=20)  # A model can call immediately before turn/start's response is processed.
        with self.lock:
            if not self.capability or not hmac.compare_digest(capability, self.capability):
                return refused()
            turn, native = self.turn, self.native
            entry = native.mapping.get(self.session)
            if (turn is None or not turn.get('turnId') or native.closing or native.blocked_reason
                    or native.active_session != self.session or not entry or entry.get('state') != 'running'
                    or entry.get('request') != self.request or entry.get('thread') != turn['thread']
                    or (turn['thread'], turn['turnId']) in native.completed):
                return refused()
            name, arguments = message.get('name'), message.get('arguments')
            if not isinstance(message.get('callId'), str) or not message['callId'] or len(message['callId']) > 200:
                return refused('validation_error')
            if not tool_allowed(self.scope, name, arguments):
                return refused('tool_not_allowed')
            started = time.monotonic()
            outcome = {'stage': 'binding'}
            try:
                native.check_binding()
                if native.auth:
                    outcome['stage'] = 'permission'
                    native.auth.permitted_tool(arguments)
                outcome['stage'] = 'callback'
                with httpx.Client(timeout=15, trust_env=False, follow_redirects=False) as client:
                    reply = client.post(native.callback_url,
                        headers={'Authorization': 'Bearer ' + turn['token']},
                        json={'sessionId': self.session, 'requestId': self.request,
                              'name': name, 'arguments': arguments})
                outcome.update(status=reply.status_code, stage='callback_body')
                result = reply.json()
                if not isinstance(result, dict):
                    return refused('not_ready')
                outcome.update(stage='callback_done', success=reply.is_success)
                if isinstance(result.get('error'), dict):
                    outcome['code'] = result['error'].get('code')
                if native.auth:
                    result = native.auth.sanitize(result)
                return result
            except CodexFailure as failure:
                native.blocked_reason = failure.reason
                return refused(failure.reason)
            except Exception:
                return refused('not_ready')
            finally:
                from .app import log_tool_reply
                elapsed = (time.monotonic() - started) * 1000
                log_tool_reply({'params': {'tool': name, 'callId': message['callId'],
                                          'threadId': turn['thread'], 'turnId': turn['turnId']}},
                               turn, outcome, elapsed)
                turn['timing']['toolCalls'] += 1
                turn['timing']['toolMs'] += elapsed

    def close(self):
        self.revoke()
        self.closed = True
        self.listener.close()
        self.thread.join(timeout=2)
        shutil.rmtree(self.root)
