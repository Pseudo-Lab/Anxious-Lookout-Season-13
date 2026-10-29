"""#60 runner side: pull from codex-broker, renew before expiry, answer the app-server 401 refresh."""
import json
import queue
import threading
import time
from uuid import uuid4

import httpx
import pytest

from app.codex_policy import CodexFailure
from runner.app import Native
from runner.broker_client import BrokerClient, BrokerPull

OWNER, STATE = str(uuid4()), str(uuid4())
URL = 'http://codex-broker.codex-broker.svc:8080'


def body(**changes):
    return {'version': 2, 'ownerId': OWNER, 'stateId': STATE, 'accountId': 'synthetic-account',
            'accessToken': 'synthetic-access', 'expiresAt': int(time.time()) + 7200, 'snapshotId': str(uuid4()), **changes}


def client(tmp_path, handler):
    token = tmp_path/'token'
    token.write_text('projected-'+'t'*60+'\n')
    return BrokerClient(URL, token, OWNER, STATE, transport=httpx.MockTransport(handler))


def test_fetch_presents_the_projected_token_and_own_identity(tmp_path):
    seen = []
    def handler(request):
        seen.append((str(request.url), request.headers['authorization'], json.loads(request.content)))
        return httpx.Response(200, json=body())
    assert client(tmp_path, handler).fetch(refresh=True)['accessToken'] == 'synthetic-access'
    assert seen == [(URL+'/v1/access', 'Bearer projected-'+'t'*60, {'ownerId': OWNER, 'stateId': STATE, 'refresh': True})]


@pytest.mark.parametrize('response,reason', [
    (httpx.Response(503, json={'error': 'auth_expired'}), 'auth_expired'),
    (httpx.Response(503, json={'error': 'not_configured'}), 'unavailable'),
    (httpx.Response(403, json={'error': 'refused'}), 'unavailable'),
    (httpx.Response(200, json=body(ownerId=str(uuid4()))), 'policy_refused'),
    (httpx.Response(200, json={**body(), 'extra': 1}), 'policy_refused'),
    (httpx.Response(200, text='not json'), 'unavailable'),
])
def test_fetch_failures_are_fixed_reasons(tmp_path, response, reason):
    with pytest.raises(CodexFailure) as caught:
        client(tmp_path, lambda request: response).fetch()
    assert caught.value.reason == reason


@pytest.mark.parametrize('url', ['https://codex-broker.codex-broker.svc:8080', 'http://broker.example.com:8080',
                                 'http://user:pw@codex-broker.codex-broker.svc:8080', 'http://codex-broker.codex-broker.svc:8080/x'])
def test_only_an_in_cluster_service_url_is_accepted(tmp_path, url):
    with pytest.raises(CodexFailure):
        BrokerClient(url, tmp_path/'token', OWNER, STATE)


def test_pull_retries_until_applied_then_waits_for_the_renewal_window():
    calls, applied, waits = [], [], []
    def fetch():
        calls.append(1)
        if len(calls) == 1: raise CodexFailure('unavailable')
        return body(expiresAt=10_000)
    done = threading.Event()
    pull = BrokerPull.__new__(BrokerPull)
    pull.fetch, pull.apply, pull.renew_before, pull.delays, pull.clock = fetch, applied.append, 900, (0.5, 1), lambda: 1_000
    class Stop:
        def is_set(self): return len(waits) >= 2
        def wait(self, seconds): waits.append(seconds)
    pull.stopped = Stop()
    pull.run()
    assert len(calls) == 2 and len(applied) == 1
    assert waits == [0.5, 10_000 - 1_000 - 900]


class Auth:
    def __init__(self, envelope=None, failure=None):
        self.snapshot = {'accessToken': 'rejected-access', 'chatgptAccountId': 'synthetic-account', 'chatgptPlanType': 'pro'}
        self.envelope, self.failure, self.installed, self.blocked = envelope, failure, [], []
    def refresher(self):
        if self.failure: raise self.failure
        return self.envelope
    def install(self, envelope):
        self.installed.append(envelope)
        self.snapshot = {'accessToken': envelope['accessToken'], 'chatgptAccountId': envelope['accountId']}
    def check(self, budget=True): pass
    def state(self): return {'deadline': time.time() + 3600}
    def block(self, reason): self.blocked.append(reason)


def native(auth):
    value = Native.__new__(Native)
    value.external, value.auth, value.sent = True, auth, []
    value.messages, value.mapping, value.active_session, value.blocked_reason = queue.Queue(), {}, None, None
    value.send = value.sent.append
    value.messages.put({'id': 7, 'method': 'account/chatgptAuthTokens/refresh', 'params': {'reason': 'unauthorized'}})
    return value


def test_app_server_refresh_is_answered_from_the_broker():
    auth = Auth(body(accessToken='renewed-access'))
    runner = native(auth)
    runner.receive()
    assert runner.sent == [{'id': 7, 'result': {'accessToken': 'renewed-access', 'chatgptAccountId': 'synthetic-account',
                                                'chatgptPlanType': None}}]
    assert auth.installed and auth.blocked == [] and runner.blocked_reason is None


@pytest.mark.parametrize('auth', [Auth(failure=CodexFailure('unavailable')), Auth(body(accessToken='rejected-access'))])
def test_refresh_without_a_new_token_keeps_the_fixed_refusal(auth):
    runner = native(auth)
    with pytest.raises(CodexFailure) as caught:
        runner.receive()
    assert caught.value.reason == 'auth_revoked' and auth.blocked == ['auth_revoked']
    assert runner.sent[0]['error']['code'] == -32001 and 'rejected-access' not in json.dumps(runner.sent)
