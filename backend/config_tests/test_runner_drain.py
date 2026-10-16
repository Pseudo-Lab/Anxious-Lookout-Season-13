"""Private drain protocol: no host termination or provider proof implied."""
import threading
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from runner.app import create_app
from runner.auth import read_private
from runner.binding import initialize_binding


@pytest.fixture
def setup(tmp_path, monkeypatch):
    owner, state, pod = (str(uuid4()) for _ in range(3))
    root = tmp_path/'native'
    initialize_binding(root, owner, state)
    token = tmp_path/'token'; token.write_text('t'*40)
    for key, value in {'APP_ENV':'test', 'RUNNER_ACCOUNT_ID':owner, 'RUNNER_STATE_ID':state,
                       'RUNNER_POD_UID':pod, 'RUNNER_TOKEN_FILE':str(token), 'RUNNER_STATE_DIR':str(root),
                       'CODEX_AUTH_MODE':'none', 'RESEARCH_TOOL_CALLBACK_URL':'http://callback.invalid/tools'}.items():
        monkeypatch.setenv(key, value)
    class Native:
        fixture, real_verified = False, False
        def __init__(self, *args, **kwargs):
            self.state_lock, self.lock = threading.RLock(), threading.Lock()
            self.active_session, self.mapping = None, {}
            self.clean, self.closes = True, 0
        def close(self): self.closes += 1; return self.clean
        def availability(self): return None
        def accept(self, *args): pytest.fail('turn admitted after drain')
    monkeypatch.setattr('runner.app.Native', Native)
    body = dict(stopId=str(uuid4()),ownerId=owner,stateId=state,podUid=pod,generation=1)
    headers = {'Authorization':'Bearer '+'t'*40,'X-Runner-State-Id':state}
    with TestClient(create_app()) as client:
        yield client, body, headers, root


def test_drain_persists_exact_receipt_and_coalesces_same_request(setup):
    client, body, headers, root = setup
    response = client.post('/lifecycle/drain', json=body, headers=headers)
    assert response.status_code == 200 and response.json() == {**body, 'version':1, 'clean':True}
    assert read_private(root/('termination-'+body['podUid']+'.json')) == response.json()
    assert client.post('/lifecycle/drain', json=body, headers=headers).json() == response.json()
    assert client.app.state.native.closes == 1
    assert client.post('/lifecycle/drain', json={**body,'stopId':str(uuid4())}, headers=headers).status_code == 409
    turn = dict(requestId=str(uuid4()),text='no dispatch',tools=[],toolToken='u'*40)
    assert client.post('/sessions/'+str(uuid4())+'/turn',json=turn,headers=headers).status_code == 503
    assert client.get('/health',headers=headers).json()['available'] is False


@pytest.mark.parametrize('field', ['ownerId','stateId','podUid'])
def test_wrong_identity_never_drains(setup, field):
    client, body, headers, root = setup
    assert client.post('/lifecycle/drain',json={**body,field:str(uuid4())},headers=headers).status_code == 409
    assert client.app.state.native.closes == 0
    assert not list(root.glob('termination-*'))


@pytest.mark.parametrize('busy', ['active','locked','running','failed','unknown'])
def test_uncertain_work_never_creates_receipt(setup, busy):
    client, body, headers, root = setup
    native = client.app.state.native
    if busy == 'active': native.active_session = str(uuid4())
    elif busy == 'locked': native.lock.acquire()
    else: native.mapping = {'session': {'state':busy}}
    assert client.post('/lifecycle/drain',json=body,headers=headers).status_code == 409
    assert not client.app.state.stopping and native.closes == 0
    assert not list(root.glob('termination-*'))


def test_unclean_shutdown_never_claims_clean_receipt(setup):
    client, body, headers, root = setup
    client.app.state.native.clean = False
    assert client.post('/lifecycle/drain',json=body,headers=headers).json()['clean'] is False
    assert read_private(root/('termination-'+body['podUid']+'.json'))['clean'] is False


def test_unauthenticated_drain_never_stops(setup):
    client, body, headers, root = setup
    assert client.post('/lifecycle/drain',json=body).status_code == 401
    assert client.app.state.native.closes == 0


def test_existing_receipt_is_never_overwritten(setup):
    client, body, headers, root = setup
    file = root/('termination-'+body['podUid']+'.json')
    file.write_text('preserve evidence')
    assert client.post('/lifecycle/drain',json=body,headers=headers).status_code == 409
    assert file.read_text() == 'preserve evidence' and client.app.state.native.closes == 0


@pytest.mark.parametrize('failure', [None, 'forced', 'already-dead', 'worker-alive', 'binding'])
def test_native_close_requires_graceful_exit_and_quiescence(failure):
    from types import SimpleNamespace
    from subprocess import TimeoutExpired
    from app.codex_policy import CodexFailure
    from runner.app import Native
    native = Native.__new__(Native)
    native.closed, native.active_session, native.auth = False, None, None
    class Process:
        def __init__(self):
            self.code = 0 if failure == 'already-dead' else None
            self.forced = False
            self.stdin = self.stdout = self.stderr = SimpleNamespace(close=lambda:None)
        def poll(self): return self.code
        def wait(self, timeout):
            if failure == 'forced' and not self.forced: raise TimeoutExpired('fixture',timeout)
            self.code = 0
        def terminate(self): self.forced = True
    native.process = Process()
    native.worker = SimpleNamespace(join=lambda timeout:None, is_alive=lambda:failure == 'worker-alive')
    def binding():
        if failure == 'binding': raise CodexFailure('policy_refused')
    native.check_binding = binding
    assert native.close() is (failure is None)
    assert native.close() is (failure is None)


def test_idle_evidence_uses_native_clock_and_refuses_pending_work(setup):
    import time
    client, body, headers, root = setup
    native = client.app.state.native
    native.idle_since = time.monotonic()
    query = {'idleSeconds':300}
    assert client.post('/lifecycle/idle',json=query,headers=headers).json()['idle'] is False
    native.idle_since -= 301
    for _ in range(2):
        assert client.post('/lifecycle/idle',json=query,headers=headers).json() == {
            'ownerId':body['ownerId'],'stateId':body['stateId'],'podUid':body['podUid'],'idle':True}
    for state in ('running','failed','unknown'):
        native.mapping = {'session':{'state':state}}
        assert client.post('/lifecycle/idle',json=query,headers=headers).json()['idle'] is False
    native.mapping = {}
    native.active_session = 'busy'
    assert client.post('/lifecycle/idle',json=query,headers=headers).json()['idle'] is False
    assert client.post('/lifecycle/idle',json=query).status_code == 401
    assert client.post('/lifecycle/idle',json={'idleSeconds':True},headers=headers).status_code == 422
