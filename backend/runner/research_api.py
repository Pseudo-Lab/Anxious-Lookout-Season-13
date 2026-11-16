"""Exactly one turn-grant file; no MCP process, callback credential or replay."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from app.codex_policy import CodexFailure, MODEL
from app.page_scope import shell_allowed
from app.research_credentials import enabled, locate
from .auth import private_directory, write_private

FIELDS = {'token', 'sessionId', 'requestId', 'apiOrigin', 'basePath', 'expiresAt'}
REDACTION_MARKER = 'research-api-used.json'


def validate(value, session, request):
    try:
        if not isinstance(value, dict) or set(value) != FIELDS:
            raise ValueError()
        conversation, request_id = locate(value['token'])
        parsed = urlsplit(value['apiOrigin'])
        if (str(conversation) != session or str(request_id) != request
                or value['sessionId'] != session or value['requestId'] != request
                or parsed.scheme != 'https' or not parsed.hostname or parsed.port not in {None, 443}
                or parsed.path or parsed.username or parsed.password or parsed.query or parsed.fragment
                or re.fullmatch(r'(?:/[A-Za-z0-9_-]+)*', value['basePath']) is None):
            raise ValueError()
        expiry = datetime.fromisoformat(value['expiresAt'].replace('Z', '+00:00'))
        if expiry.tzinfo is None or not 0 < (expiry - datetime.now(timezone.utc)).total_seconds() <= 1800:
            raise ValueError()
        return expiry
    except Exception:
        raise CodexFailure('policy_refused') from None


def strict_json(line):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate field')
            result[key] = value
        return result
    return json.loads(line, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Invalid number')))


class GrantFile:
    def __init__(self, root, auth):
        self.auth = auth
        self.directory = Path(root) / 'research-api'
        self.directory.mkdir(mode=0o700, exist_ok=True)
        private_directory(self.directory)
        self.path = self.directory / 'turn.json'
        self.current = None
        self.revoked = True
        self.expiry = None
        self.clear()  # Never adopt a previous file after restart.

    def prepare(self, session, body):
        self.clear()
        value = body.researchGrant
        if value is None:
            return
        if (not enabled('RUNNER_RESEARCH_API_ENABLE') or not shell_allowed(body.scope)
                or not getattr(self.auth, 'service', False) or not getattr(self.auth, 'external', False)):
            raise CodexFailure('policy_refused')
        expiry = validate(value, session, str(body.requestId))
        # Persist redaction/fingerprints before any sandbox can see the literal.
        self.auth.secrets.add(value['token'])
        self.auth.remember_secrets()
        write_private(self.auth.control / REDACTION_MARKER, {'version': 1})
        self.auth.research_api_redaction = True
        write_private(self.path, value)  # Fresh inode, fsync file+directory.
        self.expiry = expiry
        self.current = (session, str(body.requestId), None, None)
        self.revoked = False

    def bind(self, turn):
        if self.current is not None and not self.revoked:
            if self.current[:2] != (turn['session'], turn['request']):
                self.revoke()
                raise CodexFailure('policy_refused')
            self.current = (*self.current[:2], turn['thread'], turn['turnId'])

    def revoke(self):
        # Reader/interrupt path: immediate memory-only barrier. No RPC/disk/auth.
        self.revoked = True

    def clear(self):
        self.revoke()
        self.current = None
        self.expiry = None
        write_private(self.path, {'available': False})

    def active(self, session, request):
        current = self.current
        if (self.revoked or current is None or current[:2] != (session, request)
                or not current[2] or not current[3] or self.expiry is None
                or datetime.now(timezone.utc) >= self.expiry):
            return None
        return current

    def note(self, message):
        method = message.get('method')
        params = message.get('params') or {}
        if not isinstance(params, dict):
            self.revoke()
            return
        turn = params.get('turn')
        model_refused = (method == 'model/rerouted' or (method in {'turn/started', 'turn/updated', 'turn/completed'}
            and isinstance(turn, dict) and 'model' in turn and turn['model'] != MODEL))
        auth_refused = method == 'account/updated' and params.get('authMode') != 'chatgptAuthTokens'
        if model_refused or auth_refused or method == 'account/chatgptAuthTokens/refresh':
            self.revoke()
        if method == 'turn/completed':
            # Even before worker bind, a completion cannot be followed by a
            # queued turn/start reply that reopens this admission's grant.
            self.revoke()
        return 'model_unavailable' if model_refused else 'auth_revoked' if auth_refused else None
