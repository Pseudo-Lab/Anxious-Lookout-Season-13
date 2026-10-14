"""Explicit experimental operator access; no trial window or source renewal.

Only metadata and indexed lifetime fingerprints/receipts persist. Credential
plaintext stays in memory. Legacy service JSON requires offline migration;
legacy trial state is never adopted by this mode.
"""
import fcntl
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import uuid

from app.codex_policy import CodexFailure, REASONS
from .access import AccessAuth, claims
from .auth import read_private
from .binding import canonical_uuid
from .projection import ProjectionJournal, BASE, MASK, rolling
from .service_store import ServiceStore, LEDGER, LEGACY, refuse, private_file

MODE = 'operator-access-snapshot'
FIELDS = {'version', 'mode', 'owner', 'stateId', 'accountId', 'trialId', 'requests',
          'snapshotId', 'snapshotFingerprint', 'deadline', 'blocked', 'modelBlocked', 'projectionKey', 'nativeAttached'}


def fingerprint(value):
    return hashlib.sha256(value.encode()).hexdigest()


def is_fingerprint(value):
    return isinstance(value, str) and len(value) == 64 and all(char in '0123456789abcdef' for char in value)


class ServiceJournal(ProjectionJournal):
    """Same literal/projection HMACs; indexed storage instead of a 512-row array."""
    def __init__(self, auth, metadata):
        self.auth, self.owner, self.trial = auth, auth.owner, metadata['trialId']
        self.key = bytes.fromhex(metadata['projectionKey'])

    def row_mac(self, length, filter_hash, digest):
        body = json.dumps([self.owner, self.trial, length, filter_hash, digest], separators=(',', ':')).encode()
        return hmac.new(self.key, b'service-literal-v1\0' + body, hashlib.sha256).hexdigest()

    def remember(self, db, literals):
        for value in literals:
            refuse(isinstance(value, str) and 1 <= len(value) <= 65536)
            length, filter_hash, digest = len(value), rolling(value), self.digest(value)
            db.execute('INSERT OR IGNORE INTO literals VALUES(?,?,?,?)',
                       (length, str(filter_hash), digest, self.row_mac(length, filter_hash, digest)))

    def text(self, value):
        hits = []
        with self.auth.store.transaction() as db:
            metadata = self.auth._state(db)
            refuse(metadata['projectionKey'] == self.key.hex() and metadata['trialId'] == self.trial)
            # Distinct lengths are bounded by the credential format, not history
            # size. Query candidates per substring without loading all receipts.
            for (length,) in db.execute('SELECT DISTINCT length FROM literals'):
                if length > len(value):
                    continue
                factor = pow(BASE, length - 1, 1 << 64)
                current = rolling(value[:length])
                for offset in range(len(value) - length + 1):
                    candidates = db.execute('SELECT digest,mac FROM literals WHERE length=? AND filter=?',
                                            (length, str(current))).fetchall()
                    if candidates:
                        digest = self.digest(value[offset:offset + length])
                        for expected, mac in candidates:
                            refuse(hmac.compare_digest(self.row_mac(length, current, expected), mac))
                            if hmac.compare_digest(digest, expected):
                                hits.append((offset, offset + length))
                    if offset + length < len(value):
                        current = ((current - ord(value[offset]) * factor) * BASE + ord(value[offset + length])) & MASK
        if not hits:
            return value
        merged = []
        for start, end in sorted(hits):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        parts, previous = [], 0
        for start, end in merged:
            parts.extend((value[previous:start], '[redacted]'))
            previous = end
        return ''.join(parts) + value[previous:]


class ServiceAccessAuth(AccessAuth):
    service = True

    def __init__(self, control, owner, state_id, expected_account):
        super().__init__(control, owner, state_id)
        refuse(isinstance(expected_account, str) and 1 <= len(expected_account) <= 200)
        self.expected_account = expected_account
        self.store = ServiceStore(self.control)
        self.mutex = threading.RLock()

    def _validate(self, value):
        refuse(isinstance(value, dict) and set(value) == FIELDS and type(value['version']) is int
               and value['version'] == 3 and value['mode'] == MODE
               and value['owner'] == self.owner and value['stateId'] == self.state_id
               and value['accountId'] == self.expected_account)
        canonical_uuid(value['trialId'])  # Stable projection context, not a budget.
        refuse(type(value['requests']) is int and value['requests'] >= 0)
        refuse(value['blocked'] is None or (isinstance(value['blocked'], str) and value['blocked'] in REASONS))
        refuse(type(value['modelBlocked']) is bool and type(value['nativeAttached']) is bool
               and is_fingerprint(value['projectionKey']))
        canonical_uuid(value['snapshotId'])
        refuse(type(value['deadline']) is int and is_fingerprint(value['snapshotFingerprint']))
        return value

    def _state(self, db):
        rows = db.execute('SELECT payload FROM metadata').fetchmany(2)
        refuse(len(rows) == 1 and len(rows[0][0]) <= 4096)
        try:
            value = self._validate(json.loads(rows[0][0]))
        except (ValueError, TypeError, KeyError):
            raise CodexFailure('policy_refused') from None
        refuse(db.execute('SELECT fingerprint FROM snapshots WHERE id=?', (value['snapshotId'],)).fetchone()
               == (value['snapshotFingerprint'],))
        return value

    def _save(self, db, value):
        self._validate(value)
        payload = json.dumps(value, separators=(',', ':'))
        refuse(len(payload) <= 4096)
        db.execute('INSERT INTO metadata VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload', (payload,))

    def state(self):
        # requests is a fixed-size count for AccessAuth.attach compatibility;
        # individual receipts remain indexed and are never discarded.
        with self.mutex, self.store.transaction() as db:
            return self._state(db)

    def has_request(self, request):
        request = canonical_uuid(request)
        with self.mutex, self.store.transaction() as db:
            self._state(db)
            return db.execute('SELECT 1 FROM requests WHERE id=?', (request,)).fetchone() is not None

    def audit(self, db):
        """Explicit offline preservation check, never a per-request history scan."""
        refuse(db.execute('PRAGMA integrity_check').fetchall() == [('ok',)])
        value = self._state(db)
        refuse(db.execute('SELECT count(*) FROM requests').fetchone()[0] == value['requests'])
        for (identity,) in db.execute('SELECT id FROM requests'):
            canonical_uuid(identity)
        for identity, digest in db.execute('SELECT id,fingerprint FROM snapshots'):
            canonical_uuid(identity); refuse(is_fingerprint(digest))
        for (digest,) in db.execute('SELECT fingerprint FROM rejected'):
            refuse(is_fingerprint(digest) and db.execute(
                'SELECT 1 FROM snapshots WHERE fingerprint=? LIMIT 1', (digest,)).fetchone())
        journal = ServiceJournal(self, value)
        for length, filter_hash, digest, mac in db.execute('SELECT * FROM literals'):
            refuse(type(length) is int and 1 <= length <= 65536
                   and isinstance(filter_hash, str) and filter_hash.isdigit()
                   and 0 <= int(filter_hash) <= MASK and str(int(filter_hash)) == filter_hash
                   and is_fingerprint(digest) and is_fingerprint(mac))
            refuse(hmac.compare_digest(journal.row_mac(length, int(filter_hash), digest), mac))

    def install(self, envelope):
        expected = {'version', 'ownerId', 'stateId', 'accountId', 'accessToken', 'expiresAt', 'snapshotId'}
        refuse(isinstance(envelope, dict) and set(envelope) == expected
               and type(envelope['version']) is int and envelope['version'] == 2
               and envelope['ownerId'] == self.owner and envelope['stateId'] == self.state_id)
        identity = canonical_uuid(envelope['snapshotId'])
        value, account = claims(envelope['accessToken'])
        refuse(account == self.expected_account == envelope['accountId']
               and type(envelope['expiresAt']) is int and envelope['expiresAt'] == value['exp'])
        digest = fingerprint(envelope['accessToken'])
        literals = {envelope['accessToken'], account}
        if isinstance(value.get('email'), str) and value['email']:
            literals.add(value['email'])
        with self.mutex:
            with self.store.transaction(create=True) as db:
                if db.execute('SELECT 1 FROM metadata').fetchone():
                    ledger = self._state(db)
                    if ledger['modelBlocked']:
                        raise CodexFailure('model_unavailable')
                    if ledger['blocked'] in {'policy_refused', 'provider_rate_limited', 'model_unavailable'}:
                        raise CodexFailure(ledger['blocked'])
                    if db.execute('SELECT 1 FROM rejected WHERE fingerprint=?', (digest,)).fetchone():
                        raise CodexFailure('auth_revoked')
                    prior = db.execute('SELECT fingerprint FROM snapshots WHERE id=?', (identity,)).fetchone()
                    if prior:
                        refuse(identity == ledger['snapshotId'] and prior == (digest,))
                    # A known retired credential cannot return under a new UUID.
                    # This cannot order credentials we have never observed.
                    if digest != ledger['snapshotFingerprint']:
                        refuse(not db.execute('SELECT 1 FROM snapshots WHERE fingerprint=? LIMIT 1', (digest,)).fetchone())
                else:
                    ledger = {'version': 3, 'mode': MODE, 'owner': self.owner, 'stateId': self.state_id,
                              'accountId': account, 'trialId': str(uuid.uuid4()), 'requests': 0,
                              'snapshotId': identity, 'snapshotFingerprint': digest,
                              'deadline': value['exp'] - 60, 'blocked': None, 'modelBlocked': False,
                              'projectionKey': secrets.token_hex(32), 'nativeAttached': False}
                db.execute('INSERT OR IGNORE INTO snapshots VALUES(?,?)', (identity, digest))
                ledger.update(snapshotId=identity, snapshotFingerprint=digest, deadline=value['exp'] - 60, blocked=None)
                self._save(db, ledger)
                journal = ServiceJournal(self, ledger)
                journal.remember(db, literals)  # Atomic redaction history + snapshot.
            # Publish plaintext only after the durable commit has succeeded.
            self.snapshot = {'accessToken': envelope['accessToken'], 'chatgptAccountId': account}
            plan = value.get('https://api.openai.com/auth', {}).get('chatgpt_plan_type')
            if isinstance(plan, str):
                self.snapshot['chatgptPlanType'] = plan
            self.secrets = literals  # Historical plaintext need not stay in memory.
            self.journal = journal

    def attach(self, codex_home):
        with self.mutex:
            refuse(not self.control.is_relative_to(codex_home.parent.resolve())
                   and not codex_home.parent.resolve().is_relative_to(self.control))
            refuse(read_private(codex_home.parent / 'account-id', json_format=False).strip() == self.owner)
            refuse(not (codex_home / 'auth.json').exists() and not (codex_home / 'auth.json').is_symlink())
            if not self.lease:
                descriptor = private_file(self.control / 'lease.lock', create=True)
                self.lease = os.fdopen(descriptor, 'r+')
                try:
                    fcntl.flock(self.lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    self.lease.close(); self.lease = None
                    raise CodexFailure('policy_refused') from None
            with self.store.transaction() as db:
                value = self._state(db)
                self._check(value)
                if not value['nativeAttached']:
                    refuse(not (codex_home.parent / 'broker.json').exists()
                           and not (codex_home.parent / 'broker.json').is_symlink())
                    value['nativeAttached'] = True
                    self._save(db, value)
            self.journal = ServiceJournal(self, value)

    def _check(self, value):
        if not self.snapshot:
            raise CodexFailure('not_configured')
        refuse(fingerprint(self.snapshot['accessToken']) == value['snapshotFingerprint'])
        if time.time() >= value['deadline']:
            raise CodexFailure('auth_expired')
        if value['modelBlocked']:
            raise CodexFailure('model_unavailable')
        if value['blocked']:
            raise CodexFailure(value['blocked'])

    def check(self, budget=True):
        with self.mutex:
            if not self.snapshot:
                raise CodexFailure('not_configured')
            with self.store.transaction() as db:
                self._check(self._state(db))

    def consume(self, request):
        request = canonical_uuid(request)
        with self.mutex, self.store.transaction() as db:
            value = self._state(db)
            self._check(value)
            if db.execute('SELECT 1 FROM requests WHERE id=?', (request,)).fetchone():
                raise CodexFailure('unavailable')
            db.execute('INSERT INTO requests VALUES(?)', (request,))
            value['requests'] += 1
            self._save(db, value)

    def block(self, reason):
        refuse(isinstance(reason, str) and reason in REASONS)
        with self.mutex, self.store.transaction() as db:
            value = self._state(db)
            if self.snapshot:
                refuse(fingerprint(self.snapshot['accessToken']) == value['snapshotFingerprint'])
            value['blocked'] = reason
            if reason in {'auth_expired', 'auth_revoked'}:
                db.execute('INSERT OR IGNORE INTO rejected VALUES(?)', (value['snapshotFingerprint'],))
            self._save(db, value)

    def block_model(self):
        with self.mutex, self.store.transaction() as db:
            value = self._state(db)
            value['modelBlocked'] = True
            self._save(db, value)

    def remember_secrets(self):
        if self.journal:
            with self.mutex, self.store.transaction() as db:
                value = self._state(db)
                refuse(value['projectionKey'] == self.journal.key.hex())
                self.journal.remember(db, self.secrets)

    def status(self):
        return {}  # No trial remainingAdmissions/accessExpiresAt fields.
