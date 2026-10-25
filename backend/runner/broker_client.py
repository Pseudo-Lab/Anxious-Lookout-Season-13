"""Pull operator access from codex-broker with this Pod's projected token (#60).

Returns envelope v2 for ServiceAccessAuth.install, which keeps every owner, state,
account and expiry check. Failures are fixed CodexFailure reasons, never broker text.
"""
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit

import httpx

from app.codex_policy import CodexFailure
from .binding import canonical_uuid

FIELDS = {'version', 'ownerId', 'stateId', 'accountId', 'accessToken', 'expiresAt', 'snapshotId'}


class BrokerClient:
    def __init__(self, url, token_file, owner, state_id, *, transport=None):
        parsed = urlsplit(url)
        if (parsed.scheme != 'http' or not parsed.hostname or not parsed.hostname.endswith('.svc')
                or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
            raise CodexFailure('policy_refused')  # In-cluster Service only; Cilium confines the hop.
        self.url = url.rstrip('/') + '/v1/access'
        self.token_file = Path(token_file)
        self.owner, self.state_id = canonical_uuid(owner), canonical_uuid(state_id)
        self.transport = transport

    def fetch(self, *, refresh=False):
        try:
            token = self.token_file.read_text().strip()  # Kubelet rotates it; read every call.
            with httpx.Client(timeout=15, trust_env=False, follow_redirects=False, transport=self.transport) as client:
                response = client.post(self.url, headers={'Authorization': 'Bearer '+token},
                                       json={'ownerId': self.owner, 'stateId': self.state_id, 'refresh': refresh})
        except (OSError, httpx.HTTPError):
            raise CodexFailure('unavailable') from None
        if response.status_code == 503 and response.headers.get('content-type', '').startswith('application/json'):
            if response.json().get('error') == 'auth_expired':
                raise CodexFailure('auth_expired')
        if response.status_code != 200 or len(response.content) > 65536:
            raise CodexFailure('unavailable')
        try:
            envelope = response.json()
        except ValueError:
            raise CodexFailure('unavailable') from None
        if (type(envelope) is not dict or set(envelope) != FIELDS
                or envelope['ownerId'] != self.owner or envelope['stateId'] != self.state_id):
            raise CodexFailure('policy_refused')
        return envelope


class BrokerPull:
    """Keep this runner supplied: first pull at start, then renew before the access expiry.

    The app-server's own 401 path asks the broker through ServiceAccessAuth.refresher.
    Apply failures (a turn holding the native lock, broker not logged in) retry with
    backoff; nothing here replays a request or touches the ledger directly.
    """
    def __init__(self, fetch, apply, *, renew_before=900, delays=(1, 2, 4, 8, 15, 30), clock=time.time):
        self.fetch, self.apply, self.renew_before, self.delays, self.clock = fetch, apply, renew_before, delays, clock
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        failures = 0
        while not self.stopped.is_set():
            try:
                envelope = self.fetch()
                self.apply(envelope)
                failures = 0
                wait = max(60, envelope['expiresAt'] - self.clock() - self.renew_before)
            except Exception:
                wait = self.delays[min(failures, len(self.delays)-1)]
                failures += 1
            self.stopped.wait(wait)

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=20)
