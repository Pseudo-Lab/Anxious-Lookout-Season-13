"""Operator-supplied access snapshots; no renewable cache or provider refresh.

Credentials exist only in memory. The private control volume retains account/window
binding, reservations and keyed projection fingerprints, never token plaintext.
"""
import base64
import fcntl
import json
import os
import socket
import stat
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.codex_policy import CodexFailure
from .auth import private_directory, read_private, write_private
from .binding import canonical_uuid
from .projection import ProjectionJournal

MAX_ENVELOPE = 100_000


def claims(token):
    try:
        if not isinstance(token, str) or not 20 < len(token) <= 65536:
            raise ValueError()
        header, body, signature = token.split('.')
        if not signature:
            raise ValueError()
        def decode(value):
            return json.loads(base64.b64decode(value + '='*(-len(value) % 4), altchars=b'-_', validate=True))
        if decode(header).get('alg') not in {'RS256', 'ES256'}:
            raise ValueError()
        value = decode(body)
        if type(value.get('exp')) is not int or value['exp'] <= time.time()+120:
            raise CodexFailure('auth_expired')
        if 'nbf' in value and (type(value['nbf']) is not int or value['nbf'] > time.time()):
            raise ValueError()
        account = value.get('https://api.openai.com/auth', {}).get('chatgpt_account_id')
        if not isinstance(account, str) or not 1 <= len(account) <= 200:
            raise ValueError()
        return value, account
    except CodexFailure:
        raise
    except Exception:
        raise CodexFailure('policy_refused') from None


class AccessAuth:
    external = True

    def __init__(self, control, owner, state_id):
        self.control = private_directory(Path(control))
        self.owner, self.state_id = canonical_uuid(owner), canonical_uuid(state_id)
        self.snapshot = None
        self.secrets, self.journal, self.lease = set(), None, None

    def state(self):
        value = read_private(self.control/'access-ledger.json')
        if (value.get('owner') != self.owner or value.get('stateId') != self.state_id
                or not isinstance(value.get('requests'), list) or not isinstance(value.get('windows'), list)):
            raise CodexFailure('policy_refused')
        return value

    def install(self, envelope):
        expected = {'version', 'ownerId', 'stateId', 'accountId', 'accessToken', 'expiresAt',
                    'windowId', 'newWindow', 'maxDispatches'}
        if (not isinstance(envelope, dict) or set(envelope) != expected or type(envelope['version']) is not int
                or envelope['version'] != 1 or envelope['ownerId'] != self.owner or envelope['stateId'] != self.state_id
                or type(envelope['newWindow']) is not bool or type(envelope['maxDispatches']) is not int
                or envelope['maxDispatches'] != 3):
            raise CodexFailure('policy_refused')
        window = canonical_uuid(envelope['windowId'])
        value, account = claims(envelope['accessToken'])
        if account != envelope['accountId'] or type(envelope['expiresAt']) is not int or envelope['expiresAt'] != value['exp']:
            raise CodexFailure('policy_refused')
        file = self.control/'access-ledger.json'
        if file.exists():
            ledger = self.state()
            if ledger['accountId'] != account:
                raise CodexFailure('policy_refused')
            if envelope['newWindow']:
                if window in ledger['windows']:
                    raise CodexFailure('policy_refused')  # No replay can reset a consumed window.
                ledger['windows'].append(window)
                ledger['windowStartedAt'] = int(time.time())
            elif window != ledger['windowId']:
                raise CodexFailure('policy_refused')
        else:
            if not envelope['newWindow']:
                raise CodexFailure('policy_refused')
            ledger = {'version': 1, 'owner': self.owner, 'stateId': self.state_id,
                      'accountId': account, 'trialId': str(uuid.uuid4()), 'windows': [window], 'requests': [],
                      'windowStartedAt': int(time.time())}
        deadline = min(value['exp']-60, ledger['windowStartedAt']+900)
        if deadline <= time.time():
            raise CodexFailure('auth_expired')
        ledger.update(windowId=window, blocked=None, deadline=deadline)
        if ledger.get('modelBlocked'):
            raise CodexFailure('model_unavailable')
        write_private(file, ledger)
        # Publish only after binding/format checks. Startup/re-auth failure latches a refusal.
        self.snapshot = {'accessToken': envelope['accessToken'], 'chatgptAccountId': account}
        if isinstance(value.get('https://api.openai.com/auth', {}).get('chatgpt_plan_type'), str):
            self.snapshot['chatgptPlanType'] = value['https://api.openai.com/auth']['chatgpt_plan_type']
        self.secrets.update((envelope['accessToken'], account))
        if isinstance(value.get('email'), str) and value['email']:
            self.secrets.add(value['email'])
        self.remember_secrets()

    def attach(self, codex_home):
        if self.control.is_relative_to(codex_home.parent.resolve()) or codex_home.parent.resolve().is_relative_to(self.control):
            raise CodexFailure('policy_refused')
        if read_private(codex_home.parent/'account-id', json_format=False).strip() != self.owner:
            raise CodexFailure('policy_refused')
        if (codex_home/'auth.json').exists() or (codex_home/'auth.json').is_symlink():
            raise CodexFailure('policy_refused')
        if not self.lease:
            self.lease = (self.control/'lease.lock').open('a+')
            fcntl.flock(self.lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ledger = self.state()
        path = self.control/'access-projection.json'
        if path.exists():
            self.journal = ProjectionJournal(read_private(path), self.owner, ledger['trialId'])
        elif ledger['requests'] or (codex_home.parent/'broker.json').exists():
            raise CodexFailure('policy_refused')
        else:
            self.journal = ProjectionJournal.create(self.owner, ledger['trialId'])
            write_private(path, self.journal.payload())
        self.remember_secrets()
        self.check()

    def check(self, budget=True):
        if not self.snapshot:
            raise CodexFailure('not_configured')
        value = self.state()
        if time.time() >= value['deadline']:
            raise CodexFailure('auth_expired')
        if value.get('blocked'):
            raise CodexFailure(value['blocked'])
        if value.get('modelBlocked'):
            raise CodexFailure('model_unavailable')
        if budget and sum(item['windowId'] == value['windowId'] for item in value['requests']) >= 3:
            raise CodexFailure('budget_exhausted')

    def consume(self, request):
        self.check()
        value = self.state()
        if any(item['id'] == request for item in value['requests']):
            raise CodexFailure('unavailable')  # Ambiguous prior reservation never auto-replays.
        value['requests'].append({'id': request, 'windowId': value['windowId']})
        write_private(self.control/'access-ledger.json', value)

    def block(self, reason):
        value = self.state()
        value['blocked'] = reason
        write_private(self.control/'access-ledger.json', value)

    def block_model(self):
        value = self.state()
        value['modelBlocked'] = True
        write_private(self.control/'access-ledger.json', value)

    def status(self):
        if not (self.control/'access-ledger.json').exists():
            return {'accessExpiresAt': None, 'remainingAdmissions': None}
        value = self.state()
        return {'accessExpiresAt': datetime.fromtimestamp(value['deadline'], timezone.utc).isoformat(),
                'remainingAdmissions': max(0, 3-sum(item['windowId'] == value['windowId'] for item in value['requests']))}

    def remember_secrets(self):
        if self.journal and self.journal.observe(self.secrets):
            write_private(self.control/'access-projection.json', self.journal.payload())

    def refresh_projection_secrets(self):
        self.remember_secrets()  # Never read a native/host credential file.

    def sanitize(self, value):
        self.remember_secrets()
        return self.journal.sanitize(value)

    def projection_mac(self, session, thread, record):
        return self.journal.projection_mac(session, thread, record)

    def permitted_tool(self, arguments):
        self.check(budget=False)  # Already reserved third turn may still finish its tool.
        if self.sanitize(arguments) != arguments:
            raise CodexFailure('policy_refused')

    def close(self, clean=False):
        self.snapshot = None
        self.secrets.clear()
        if self.lease:
            self.lease.close()
            self.lease = None


class SupplySocket:
    """Local-only, same-UID operator ingress; not exposed by runner HTTP."""
    def __init__(self, control, apply):
        self.path, self.apply = Path(control)/'access.sock', apply
        self.stopped = threading.Event()
        if self.path.exists():
            value = self.path.lstat()
            if not stat.S_ISSOCK(value.st_mode) or value.st_uid != os.geteuid():
                raise CodexFailure('policy_refused')
            self.path.unlink()
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.bind(str(self.path))
        self.path.chmod(0o600)
        self.socket.listen(1)
        self.socket.settimeout(.5)
        self.worker = threading.Thread(target=self.run, daemon=True)
        self.worker.start()

    def run(self):
        while not self.stopped.is_set():
            try:
                client, _ = self.socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with client:
                try:
                    import struct
                    _, uid, _ = struct.unpack('3i', client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                    if uid != os.geteuid():
                        raise ValueError()
                    client.settimeout(5)
                    with client.makefile('rb') as incoming:
                        payload = incoming.readline(MAX_ENVELOPE+1)
                        if len(payload) > MAX_ENVELOPE or not payload.endswith(b'\n'):
                            raise ValueError()
                        if incoming.read(1):
                            raise ValueError()
                        envelope = json.loads(payload)
                    if self.stopped.is_set():
                        raise CodexFailure('unavailable')
                    self.apply(envelope)
                    result = {'accepted': True}
                except Exception as failure:
                    result = {'accepted': False, 'error': str(failure) if isinstance(failure, CodexFailure) else 'codex_policy_refused'}
                try:
                    client.sendall(json.dumps(result).encode()+b'\n')
                except OSError:
                    pass

    def close(self):
        self.stopped.set()
        self.socket.close()
        self.worker.join(timeout=45)
        self.path.unlink(missing_ok=True)
