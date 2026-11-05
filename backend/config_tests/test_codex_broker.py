"""#60 codex-broker: TokenReview + Pod identity, credential freshness, HTTP front. Fake API, synthetic tokens."""
import json
import os
import re
import stat
import time
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.codex_policy import CodexFailure
from broker.app import create_app, envelope, snapshot_id
from broker.credentials import Credentials
from broker.identity import Kube, Verifier
from config_tests.test_service_access import jwt, ACCOUNT
from runner.lifecycle import Refused

OWNER, STATE, POD_UID = uuid4(), uuid4(), uuid4()
NAME = 'runner-' + OWNER.hex
PRESENTED = 'projected.' + 'p'*80


def review(**changes):
    status = {'authenticated': True, 'audiences': ['codex-broker'],
              'user': {'username': 'system:serviceaccount:codex-runner:runner-workload',
                       'extra': {'authentication.kubernetes.io/pod-name': [NAME],
                                 'authentication.kubernetes.io/pod-uid': [str(POD_UID)]}}}
    status.update(changes)
    return {'kind': 'TokenReview', 'status': status}


def pod(**changes):
    value = {'kind': 'Pod', 'metadata': {'name': NAME, 'namespace': 'codex-runner', 'uid': str(POD_UID),
             'labels': {'anxious.io/managed': 'user-runner', 'anxious.io/owner': str(OWNER),
                        'anxious.io/state': str(STATE), 'anxious.io/generation': '2'}},
             'spec': {'serviceAccountName': 'runner-workload'}, 'status': {'phase': 'Running'}}
    for path, item in changes.items():
        target = value
        *parents, leaf = path.split('|')
        for key in parents:
            target = target[key]
        if item is None: target.pop(leaf, None)
        else: target[leaf] = item
    return value


def kube(tmp_path, token_review, pod_value, seen=None):
    (tmp_path/'sa-token').write_text('broker-own-token')
    def handler(request):
        if seen is not None: seen.append((request.method, request.url.path, request.headers['authorization'],
                                          json.loads(request.content) if request.content else None))
        if request.url.path.endswith('/tokenreviews'):
            return httpx.Response(201, json=token_review)
        if request.url.path == '/api/v1/namespaces/codex-runner/pods/'+NAME and pod_value is not None:
            return httpx.Response(200, json=pod_value)
        return httpx.Response(404, json={})
    return Kube('https://kube.invalid', tmp_path/'sa-token', None, transport=httpx.MockTransport(handler))


def test_verifier_binds_the_projected_token_to_the_running_labelled_pod(tmp_path):
    seen = []
    identity = Verifier(kube(tmp_path, review(), pod(), seen)).verify(PRESENTED)
    assert (identity.owner, identity.state_id, identity.generation, identity.pod_uid) == (OWNER, STATE, 2, POD_UID)
    method, path, authorization, body = seen[0]
    assert (method, path, authorization) == ('POST', '/apis/authentication.k8s.io/v1/tokenreviews', 'Bearer broker-own-token')
    assert body['spec'] == {'token': PRESENTED, 'audiences': ['codex-broker']}
    assert seen[1][:2] == ('GET', '/api/v1/namespaces/codex-runner/pods/'+NAME)


@pytest.mark.parametrize('token_review,pod_value', [
    (review(authenticated=False), pod()),
    (review(audiences=['kubernetes']), pod()),
    (review(user={'username': 'system:serviceaccount:codex-runner:runner-pool-controller', 'extra': {}}), pod()),
    (review(user={'username': 'system:serviceaccount:codex-runner:runner-workload', 'extra': {}}), pod()),
    (review(), None),
    (review(), pod(**{'metadata|uid': str(uuid4())})),
    (review(), pod(**{'metadata|deletionTimestamp': '2026-10-26T00:00:00Z'})),
    (review(), pod(**{'status|phase': 'Pending'})),
    (review(), pod(**{'spec|serviceAccountName': 'default'})),
    (review(), pod(**{'metadata|labels|anxious.io/managed': None})),
    (review(), pod(**{'metadata|labels|anxious.io/generation': '01'})),
    (review(), pod(**{'metadata|labels|anxious.io/owner': str(uuid4())})),
])
def test_verifier_refuses_other_pods_and_forged_tokens(tmp_path, token_review, pod_value):
    with pytest.raises(Exception):
        Verifier(kube(tmp_path, token_review, pod_value)).verify(PRESENTED)


def write_auth(home, token, account=ACCOUNT):
    path = home/'auth.json'
    if path.exists(): path.unlink()  # New inode, like an atomic rewrite.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump({'auth_mode': 'chatgpt', 'tokens': {'access_token': token, 'refresh_token': 'synthetic-refresh',
                                                       'id_token': token, 'account_id': account}}, stream)


class FakeServer:
    def __init__(self, renew):
        self.renew, self.calls, self.closed = renew, [], False
    def __call__(self, binary, home):
        self.home = home
        return self
    def rpc(self, method, params, timeout=30):
        self.calls.append((method, params))
        self.renew(self.home)
        return {'id': 1, 'error': {'message': 'workspace routing discovery failed'}}
    def alive(self): return not self.closed
    def close(self): self.closed = True


@pytest.fixture
def home(tmp_path):
    path = tmp_path/'codex'
    path.mkdir(mode=0o700)
    return path


def test_fresh_access_needs_no_app_server(home):
    token = jwt(int(time.time()) + 7200)
    write_auth(home, token)
    server = FakeServer(lambda home: pytest.fail('refresh not needed'))
    assert Credentials(home, ACCOUNT, server=server).access()[:2] == (token, ACCOUNT)


def test_near_expiry_forces_refresh_and_reads_the_rotated_file(home):
    write_auth(home, jwt(int(time.time()) + 600))
    renewed = jwt(int(time.time()) + 7200)
    server = FakeServer(lambda home: write_auth(home, renewed))
    assert Credentials(home, ACCOUNT, server=server).access()[0] == renewed
    assert server.calls == [('account/read', {'refreshToken': True})]


def test_forced_refresh_is_rate_limited_and_never_returns_the_rejected_token(home):
    first = jwt(int(time.time()) + 7200)
    write_auth(home, first)
    same = FakeServer(lambda home: None)  # The provider kept the old token.
    credentials = Credentials(home, ACCOUNT, server=same)
    with pytest.raises(CodexFailure) as caught:
        credentials.access(force=True)
    assert caught.value.reason == 'auth_expired'
    assert credentials.access(force=True)[0] == first and len(same.calls) == 1  # Within 60s: no second refresh.


def events(home):
    path = home.parent/'broker-events.jsonl'
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_refresh_events_are_fixed_fields_only_and_tell_scheduled_from_forced(home):
    # #72 E4 gates on a scheduled refresh; a forced one or a login must not count as one.
    write_auth(home, jwt(int(time.time()) + 7200))
    Credentials(home, ACCOUNT, server=FakeServer(lambda home: pytest.fail('fresh'))).access()
    assert events(home) == []  # No refresh, no event.
    write_auth(home, jwt(int(time.time()) + 600))
    renewed = jwt(int(time.time()) + 7200)
    credentials = Credentials(home, ACCOUNT, server=FakeServer(lambda home: write_auth(home, renewed)))
    credentials.access()
    write_auth(home, jwt(int(time.time()) + 7200))
    with pytest.raises(CodexFailure):
        Credentials(home, ACCOUNT, server=FakeServer(lambda home: None)).access(force=True)  # Provider kept the token.
    (first, second), now = events(home), int(time.time())
    assert first['kind'] == 'scheduled' and first['ok'] is True and second['kind'] == 'forced' and second['ok'] is False
    assert all(set(e) == {'at', 'event', 'kind', 'ok', 'before', 'after'} and e['event'] == 'refresh'
               and now - 5 <= e['at'] <= now for e in (first, second))
    assert all(re.fullmatch(r'\d+:\d+', e[k]) for e in (first, second) for k in ('before', 'after'))
    text = (home.parent/'broker-events.jsonl').read_text()
    assert renewed not in text and ACCOUNT not in text
    assert stat.S_IMODE((home.parent/'broker-events.jsonl').stat().st_mode) == 0o600


def identity(home):
    info = (home/'auth.json').lstat()
    return '%d:%d' % (info.st_ino, info.st_mtime_ns)


def test_events_chain_auth_json_identity_and_a_relogin_breaks_the_chain(home):
    # #72 review: a scheduled success from an earlier login must not survive a new device login.
    write_auth(home, jwt(int(time.time()) + 600))
    login = identity(home)
    credentials = Credentials(home, ACCOUNT, server=FakeServer(lambda home: write_auth(home, jwt(int(time.time()) + 7200))))
    credentials.access()
    (refresh,) = events(home)
    assert refresh['before'] == login and refresh['after'] == identity(home) != login  # Continuous with the login.
    time.sleep(0.01)
    write_auth(home, jwt(int(time.time()) + 600))  # A new device login replaces auth.json; it writes no event.
    assert events(home) == [refresh] and identity(home) != refresh['after']  # The chain no longer reaches the file.
    credentials.access()
    later = events(home)[-1]
    assert later['before'] != refresh['after'] and later['after'] == identity(home)  # The break is visible.


def test_failed_scheduled_refresh_is_recorded_as_not_ok(home):
    write_auth(home, jwt(int(time.time()) + 600))
    with pytest.raises(CodexFailure):
        Credentials(home, ACCOUNT, server=FakeServer(lambda home: None)).access()
    assert [(e['kind'], e['ok']) for e in events(home)] == [('scheduled', False)]


@pytest.mark.parametrize('case,reason', [('missing', 'not_configured'), ('account', 'policy_refused'),
                                         ('expired', 'auth_expired')])
def test_credentials_fail_closed(home, case, reason):
    if case == 'account': write_auth(home, jwt(account='another-account'), account='another-account')
    if case == 'expired': write_auth(home, jwt(int(time.time()) - 10))
    server = FakeServer(lambda home: None)
    with pytest.raises(CodexFailure) as caught:
        Credentials(home, ACCOUNT, server=server).access()
    assert caught.value.reason == reason


def test_envelope_is_v2_and_snapshot_stable_per_pod_and_token(home):
    token = jwt(int(time.time()) + 7200)
    write_auth(home, token)
    identity = SimpleNamespace(owner=OWNER, state_id=STATE, generation=2, pod_uid=POD_UID)
    value = envelope(identity, Credentials(home, ACCOUNT, server=FakeServer(lambda home: None)))
    assert set(value) == {'version', 'ownerId', 'stateId', 'snapshotId', 'accountId', 'accessToken', 'expiresAt'}
    assert (value['version'], value['ownerId'], value['stateId'], value['accountId']) == (2, str(OWNER), str(STATE), ACCOUNT)
    assert value['snapshotId'] == str(snapshot_id(identity, token))
    assert snapshot_id(SimpleNamespace(**{**vars(identity), 'pod_uid': uuid4()}), token) != snapshot_id(identity, token)


class StubVerifier:
    def __init__(self, result): self.result, self.seen = result, []
    def verify(self, token):
        self.seen.append(token)
        if isinstance(self.result, Exception): raise self.result
        return self.result


class StubCredentials:
    def __init__(self, failure=None): self.failure, self.forced = failure, []
    def access(self, force=False):
        self.forced.append(force)
        if self.failure: raise self.failure
        return 'synthetic-access-token-value', ACCOUNT, int(time.time()) + 7200
    def close(self): pass


IDENTITY = SimpleNamespace(owner=OWNER, state_id=STATE, generation=2, pod_uid=POD_UID)
BODY = {'ownerId': str(OWNER), 'stateId': str(STATE)}


def call(verifier, credentials, body=BODY, bearer=PRESENTED):
    with TestClient(create_app(verifier, credentials)) as client:
        headers = {'Authorization': 'Bearer '+bearer} if bearer else {}
        return client.post('/v1/access', json=body, headers=headers)


def test_http_issues_envelope_only_for_the_verified_owner():
    credentials = StubCredentials()
    response = call(StubVerifier(IDENTITY), credentials, {**BODY, 'refresh': True})
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    assert response.json()['accessToken'] == 'synthetic-access-token-value' and credentials.forced == [True]


@pytest.mark.parametrize('verifier,body,bearer,status,code', [
    (StubVerifier(IDENTITY), BODY, None, 401, 'unauthenticated'),
    (StubVerifier(IDENTITY), {'ownerId': 'nope', 'stateId': str(STATE)}, PRESENTED, 400, 'invalid_request'),
    (StubVerifier(IDENTITY), {**BODY, 'extra': 1}, PRESENTED, 400, 'invalid_request'),
    (StubVerifier(IDENTITY), {**BODY, 'refresh': 'yes'}, PRESENTED, 400, 'invalid_request'),
    (StubVerifier(Refused('forged')), BODY, PRESENTED, 403, 'refused'),
    (StubVerifier(IDENTITY), {'ownerId': str(uuid4()), 'stateId': str(STATE)}, PRESENTED, 403, 'refused'),
])
def test_http_refusals_are_fixed_and_issue_nothing(verifier, body, bearer, status, code):
    credentials = StubCredentials()
    response = call(verifier, credentials, body, bearer)
    assert (response.status_code, response.json()) == (status, {'error': code})
    assert credentials.forced == []


@pytest.mark.parametrize('failure,code', [(CodexFailure('not_configured'), 'not_configured'),
                                          (CodexFailure('auth_expired'), 'auth_expired'),
                                          (CodexFailure('policy_refused'), 'unavailable'),
                                          (RuntimeError('SECRET-SENTINEL'), 'unavailable')])
def test_http_credential_failures_map_to_fixed_codes(failure, code):
    response = call(StubVerifier(IDENTITY), StubCredentials(failure))
    assert (response.status_code, response.json()) == (503, {'error': code})
    assert 'SECRET' not in response.text


@pytest.mark.parametrize('value,passed', [('http://127.0.0.1:18080/oauth/token', True), ('http://[::1]:9/t', True),
                                          ('https://127.0.0.1:18080/oauth/token', False), ('http://auth.example.com/token', False),
                                          ('http://127.0.0.1/oauth/token', False), (None, False)])
def test_only_a_loopback_refresh_override_reaches_the_app_server(monkeypatch, tmp_path, value, passed):
    # Development checks point the refresh at a mock in the broker Pod; nothing else may redirect it.
    from broker import credentials
    if value is None: monkeypatch.delenv('CODEX_REFRESH_TOKEN_URL_OVERRIDE', raising=False)
    else: monkeypatch.setenv('CODEX_REFRESH_TOKEN_URL_OVERRIDE', value)
    seen = {}
    class Process:
        stdin = SimpleNamespace(write=lambda line: None, flush=lambda: None)
        stdout = iter(())
    def popen(command, env, **kwargs):
        seen.update(env)
        return Process()
    monkeypatch.setattr(credentials.AppServer, 'rpc', lambda self, method, params, timeout=30: {})
    credentials.AppServer('/usr/local/bin/codex', tmp_path, popen=popen)
    assert seen.get('CODEX_REFRESH_TOKEN_URL_OVERRIDE') == (value if passed else None)
    assert set(seen) <= {'PATH', 'LANG', 'HOME', 'CODEX_HOME', 'CODEX_REFRESH_TOKEN_URL_OVERRIDE'}


def test_failed_app_server_start_is_closed_and_the_next_access_retries(monkeypatch, home):
    # A failed initialize must not leave a managed writer behind; the next call starts a fresh one.
    from broker import credentials
    write_auth(home, jwt(int(time.time()) + 600))
    processes = []
    class Process:
        def __init__(self):
            self.stdin = SimpleNamespace(write=lambda line: None, flush=lambda: None, close=lambda: self.events.append('stdin'))
            self.stdout, self.events = iter(()), []
        def wait(self, timeout=None): self.events.append('wait')
        def kill(self): self.events.append('kill')
        def poll(self): return 0 if 'wait' in self.events else None
    def popen(command, env, **kwargs):
        processes.append(Process())
        return processes[-1]
    def failing_rpc(self, method, params, timeout=30):
        raise CodexFailure('unavailable')
    monkeypatch.setattr(credentials.AppServer, 'rpc', failing_rpc)
    factory = lambda binary, home: credentials.AppServer(binary, home, popen=popen)
    instance = Credentials(home, ACCOUNT, server=factory)
    for _ in range(2):
        with pytest.raises(CodexFailure):
            instance.access()
        assert instance.server is None
    assert len(processes) == 2 and all(p.events[:2] == ['stdin', 'wait'] for p in processes)


def test_non_object_json_is_a_fixed_400_without_reflection():
    with TestClient(create_app(StubVerifier(IDENTITY), StubCredentials())) as client:
        response = client.post('/v1/access', json=['SECRET-SENTINEL'], headers={'Authorization': 'Bearer '+PRESENTED})
    assert (response.status_code, response.json()) == (400, {'error': 'invalid_request'})
    assert response.headers['cache-control'] == 'no-store' and 'SECRET' not in response.text
