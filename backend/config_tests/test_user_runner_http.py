"""Actual FastAPI/session/CSRF/API-role/PostgreSQL, no Pod/Native/provider."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import create_app, session_digest
from app.settings import Settings
from app.migrate import migrate as auth_migrate
from app.runner_registry import Registry
from app.codex_policy import RUNNER_PREFLIGHT_ERRORS
from runner.lifecycle import Phase, Fence
from config_tests.test_runner_registry import schema, admin, read, preservation_snapshot


@pytest.fixture(scope='module', autouse=True)
def research_schema(schema):
    auth_migrate('0004_publication')


@pytest.fixture
def client(admin, monkeypatch):
    monkeypatch.setenv('CODEX_USER_RUNNERS_ENABLE', 'true')
    monkeypatch.setenv('RESEARCH_ACCESS_POLICY', 'editors')
    monkeypatch.setenv('APP_ENV', 'test')
    settings = Settings(database_url=os.environ['DATABASE_URL'], origin='http://127.0.0.1:28080',
                        oauth_mode='disabled', client_id='', client_secret='', transaction_key='',
                        authorize_url='', token_url='', user_url='', secure_cookie=False)
    with TestClient(create_app(settings), base_url=settings.origin, raise_server_exceptions=False) as result:
        yield result


def identity(client, admin, *, role='editor', approved=True, number='12345'):
    owner, token, csrf = uuid4(), uuid4().hex, uuid4().hex
    with admin.begin() as db:
        db.execute(text("INSERT INTO auth.accounts(id,github_id,login,role,is_approved) VALUES(:owner,:number,'synthetic',:role,:approved)"),
                   {'owner': owner, 'number': number, 'role': role, 'approved': approved})
        db.execute(text("INSERT INTO auth.sessions(token_hash,account_id,csrf_token,expires_at) VALUES(:token,:owner,:csrf,now()+interval '1 hour')"),
                   {'token': session_digest(token, client.app.state.settings.origin), 'owner': owner, 'csrf': csrf})
    client.cookies.set('anxious_session', token, path='/api')
    return owner, {'Origin': client.app.state.settings.origin, 'X-CSRF-Token': csrf}


def headers(auth, key=None):
    return {**auth, 'Idempotency-Key': str(key or uuid4())}


def create_conversation(client, auth):
    response = client.post('/api/research/sessions', json={'title': 'Retained history'}, headers=headers(auth))
    assert response.status_code == 201, response.text
    return response.json()


class StaticRunner:
    def __init__(self, error=None):
        self.health_calls = self.submits = self.reads = 0
        self.error = error

    def health(self):
        self.health_calls += 1
        return {'available': True, 'reason': None, 'model': 'gpt-6.1-sol', 'verification': 'fixture',
                'accessExpiresAt': '2026-10-15T00:00:00Z', 'remainingAdmissions': 2}

    def submit(self, identity, body):
        self.submits += 1
        request = httpx.Request('POST', 'http://fixture.invalid/turn')
        reply = httpx.Response(503, json={'error': self.error or 'unknown'}, request=request)
        raise httpx.HTTPStatusError('synthetic ambiguous result', request=request, response=reply)

    def read(self, identity):
        self.reads += 1
        raise httpx.ReadError('synthetic uncertain connection')


def test_preparation_me_status_replay_and_get_are_real_http_and_read_only(client, admin):
    owner, auth = identity(client, admin)
    assert client.get('/api/auth/me').json()['user']['codexRunner'] is True
    before = preservation_snapshot(admin)
    status = client.get('/api/research/codex/status')
    assert status.status_code == 200 and status.json() == {'available': False, 'reason': 'not_prepared',
                                                         'verification': 'unverified', 'model': 'gpt-6.1-sol'}
    assert preservation_snapshot(admin) == before
    key = uuid4()
    first = client.post('/api/research/codex/runner', json={}, headers=headers(auth, key))
    assert first.status_code == 202 and first.json()['reason'] == 'provisioning' and first.json()['retryAfterSeconds'] == 2
    assert first.headers['cache-control'] == 'no-store'
    state = read(admin, owner)
    snapshot = preservation_snapshot(admin)
    for _ in range(3):
        reply = client.post('/api/research/codex/runner', json={}, headers=headers(auth, key))
        assert reply.status_code == 202 and reply.json() == first.json()
        assert client.get('/api/research/codex/status').json() == first.json()
    assert preservation_snapshot(admin) == snapshot and read(admin, owner) == state
    assert all(field not in first.json() for field in ('ownerId', 'stateId', 'podUid', 'accessExpiresAt', 'remainingAdmissions'))


def test_concurrent_tabs_coalesce_into_single_owner_runtime_and_outbox(client, admin):
    owner, auth = identity(client, admin)
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(lambda _: client.post('/api/research/codex/runner', json={}, headers=headers(auth)), range(12)))
    assert all(response.status_code == 202 for response in responses)
    state = read(admin, owner)
    assert state.generation == 1
    with admin.begin() as db:
        assert db.execute(text('SELECT count(*) FROM runner_runtime.registry')).scalar_one() == 1
        assert db.execute(text('SELECT count(*) FROM runner_runtime.outbox')).scalar_one() == 1
        assert db.execute(text('SELECT count(*) FROM runner_runtime.entry_receipts')).scalar_one() == 12
        assert db.execute(text('SELECT count(*) FROM runner_runtime.admissions')).scalar_one() == 0


@pytest.mark.parametrize('role,approved', [('commenter',True),('editor',False),('admin',False)])
def test_ineligible_status_200_and_prepare_403_never_creates_state(client, admin, role, approved):
    owner, auth = identity(client, admin, role=role, approved=approved)
    assert client.get('/api/auth/me').json()['user']['codexRunner'] is False
    status = client.get('/api/research/codex/status')
    assert status.status_code == 200 and status.json()['reason'] == 'not_enabled_for_account'
    assert client.post('/api/research/codex/runner', json={}, headers=headers(auth)).status_code == 403
    assert read(admin, owner) is None
    assert client.get('/api/research/sessions').status_code == 403


def test_unauthenticated_revoked_session_and_membership_rechecked_before_replay(client, admin):
    assert client.get('/api/research/codex/status').status_code == 401
    assert client.post('/api/research/codex/runner', json={}, headers=headers({})).status_code == 401
    owner, auth = identity(client, admin); key = uuid4()
    assert client.post('/api/research/codex/runner', json={}, headers=headers(auth, key)).status_code == 202
    snapshot = preservation_snapshot(admin)
    with admin.begin() as db: db.execute(text('UPDATE auth.accounts SET is_approved=false WHERE id=:owner'), {'owner': owner})
    assert client.post('/api/research/codex/runner', json={}, headers=headers(auth, key)).status_code == 403
    assert preservation_snapshot(admin) == snapshot
    with admin.begin() as db: db.execute(text('DELETE FROM auth.sessions WHERE account_id=:owner'), {'owner': owner})
    assert client.post('/api/research/codex/runner', json={}, headers=headers(auth, key)).status_code == 401
    assert client.get('/api/research/codex/status').status_code == 401
    assert preservation_snapshot(admin) == snapshot


@pytest.mark.parametrize('damage', [{'Origin': 'https://wrong.invalid'}, {'X-CSRF-Token': 'wrong'}, {'X-CSRF-Token': ''}])
def test_origin_csrf_validated_before_receipt_replay_without_activity(client, admin, damage):
    owner, auth = identity(client, admin); key = uuid4()
    assert client.post('/api/research/codex/runner', json={}, headers=headers(auth, key)).status_code == 202
    before = preservation_snapshot(admin)
    assert client.post('/api/research/codex/runner', json={}, headers={**headers(auth, key), **damage}).status_code == 403
    assert preservation_snapshot(admin) == before


@pytest.mark.parametrize('body', [{'ownerId': str(uuid4())}, {'url': 'https://wrong.invalid'}, {'model': 'other'},
                                 {'stateId': str(uuid4())}, {'accessToken': 'synthetic'}])
def test_browser_cannot_choose_runtime_or_credentials(client, admin, body):
    owner, auth = identity(client, admin)
    assert client.post('/api/research/codex/runner', json=body, headers=headers(auth)).status_code == 422
    assert read(admin, owner) is None


@pytest.mark.parametrize('key', [None, '', 'invalid'])
def test_invalid_idempotency_key_creates_no_intent(client, admin, key):
    owner, auth = identity(client, admin)
    incoming = dict(auth)
    if key is not None: incoming['Idempotency-Key'] = key
    assert client.post('/api/research/codex/runner', json={}, headers=incoming).status_code == 422
    assert read(admin, owner) is None


def test_equivalent_uuid_header_normalizes_to_one_receipt_without_extending_activity(client, admin):
    owner, auth = identity(client, admin); key = uuid4()
    first = client.post('/api/research/codex/runner', json={}, headers={**auth, 'Idempotency-Key': str(key).upper()})
    assert first.status_code == 202
    before = preservation_snapshot(admin)
    again = client.post('/api/research/codex/runner', json={}, headers=headers(auth, key))
    assert again.status_code == 202 and again.json() == first.json()
    assert preservation_snapshot(admin) == before


def test_two_accounts_same_key_and_account_switch_keep_private_state(client, admin):
    owner_a, auth_a = identity(client, admin); key = uuid4()
    assert client.post('/api/research/codex/runner', json={}, headers=headers(auth_a, key)).status_code == 202
    a = read(admin, owner_a)
    owner_b, auth_b = identity(client, admin, number='54321')
    assert client.get('/api/research/codex/status').json()['reason'] == 'not_prepared'
    assert client.post('/api/research/codex/runner', json={}, headers=headers(auth_b, key)).status_code == 202
    b = read(admin, owner_b)
    assert a.owner != b.owner and a.state_id != b.state_id
    assert read(admin, owner_a) == a


def test_registry_ready_still_refuses_without_transport_and_legacy_static_bypass(client, admin):
    owner, auth = identity(client, admin)
    runner = StaticRunner(); client.app.state.research_runners[owner] = runner
    client.app.state.codex_verification = 'fixture'
    conversation = create_conversation(client, auth)
    key = uuid4()
    assert client.post('/api/research/codex/runner', json={}, headers=headers(auth, key)).status_code == 202
    current = read(admin, owner)
    with admin.begin() as db:
        Registry(db).observe(Fence.of(current), pod_uid=uuid4(), ready=True, active_requests=0,
                             ambiguous=False, verified=True, now=datetime.now(timezone.utc))
    assert client.get('/api/research/codex/status').json()['reason'] == 'runner_unreachable'
    assert client.post('/api/research/codex/runner', json={}, headers=headers(auth, key)).status_code == 200
    before = preservation_snapshot(admin)
    reply = client.post('/api/research/sessions/' + conversation['id'] + '/messages',
                        json={'text':'Keep draft','expectedVersion':1}, headers=headers(auth))
    assert reply.status_code == 503 and reply.json()['error']['code'] == 'codex_runner_unreachable'
    assert preservation_snapshot(admin) == before
    assert runner.health_calls == runner.submits == runner.reads == 0


@pytest.mark.parametrize('reason,phase', [('not_prepared',None),('provisioning',Phase.PREPARING),
    ('capacity_wait',Phase.WAITING),('runner_unreachable',Phase.UNREACHABLE),('runner_failed',Phase.FAILED),('idle_stopped',Phase.STOPPED)])
def test_six_preflight_codes_make_no_message_receipt_tool_or_dispatch(client, admin, reason, phase):
    owner, auth = identity(client, admin); conversation = create_conversation(client, auth)
    if phase is not None:
        client.post('/api/research/codex/runner', json={}, headers=headers(auth))
        state = read(admin, owner)
        with admin.begin() as db:
            registry = Registry(db)
            if phase == Phase.WAITING: registry.capacity(Fence.of(state), admitted=False)
            elif phase == Phase.UNREACHABLE:
                registry.observe(Fence.of(state), pod_uid=uuid4(), ready=False, active_requests=0,
                                 ambiguous=False, verified=True, now=datetime.now(timezone.utc))
            elif phase in {Phase.FAILED, Phase.STOPPED}:
                # Isolated controlled state fixture; not actual controller evidence.
                db.execute(text('UPDATE runner_runtime.registry SET phase=:phase WHERE owner_id=:owner'),
                           {'phase': phase.value, 'owner': owner})
    before = preservation_snapshot(admin); key = uuid4()
    path = '/api/research/sessions/' + conversation['id'] + '/messages'
    for _ in range(2):
        reply = client.post(path, json={'text':'Retain this draft','expectedVersion':1}, headers=headers(auth, key))
        assert reply.status_code == 503 and reply.json()['error']['code'] == RUNNER_PREFLIGHT_ERRORS[reason]
    assert preservation_snapshot(admin) == before
    with admin.begin() as db:
        assert db.execute(text('SELECT count(*) FROM research.idempotency WHERE key=:key'), {'key': key}).scalar_one() == 0
        row = db.execute(text('SELECT state,version,request_id,tool_token_hash,pending_text FROM research.conversations WHERE id=:id'), {'id': conversation['id']}).one()
        assert row == ('idle',1,None,None,None)


def test_feature_off_keeps_legacy_status_trial_fields_and_owned_history(client, admin):
    owner, auth = identity(client, admin); conversation = create_conversation(client, auth)
    runner = StaticRunner(); client.app.state.research_runners[owner] = runner
    client.app.state.codex_verification = 'fixture'
    client.app.state.user_runners_enabled = False
    assert client.get('/api/auth/me').json()['user']['codexRunner'] is False
    status = client.get('/api/research/codex/status')
    assert status.status_code == 200 and status.json()['available'] is True and status.json()['remainingAdmissions'] == 2
    assert status.json()['accessExpiresAt'] == '2026-10-15T00:00:00Z'
    assert client.post('/api/research/codex/runner', json={}, headers=headers(auth)).status_code == 403
    assert read(admin, owner) is None
    assert client.get('/api/research/sessions/' + conversation['id']).status_code == 200
    assert client.get('/api/research/sessions').json()['items'][0]['id'] == conversation['id']


@pytest.mark.parametrize('code', list(RUNNER_PREFLIGHT_ERRORS.values()) + ['unknown_503'])
def test_accepted_submit_503_stays_uncertain_through_on_off_and_tool_expiry(client, admin, code):
    owner, auth = identity(client, admin); conversation = create_conversation(client, auth)
    runner = StaticRunner(error=code); client.app.state.research_runners[owner] = runner
    client.app.state.codex_verification = 'fixture'; client.app.state.user_runners_enabled = False
    path = '/api/research/sessions/' + conversation['id']; key = uuid4()
    message = {'text':'Accepted original','expectedVersion':1}
    first = client.post(path + '/messages', json=message, headers=headers(auth, key))
    assert first.status_code == 202 and runner.submits == 1
    with admin.begin() as db:
        db.execute(text("UPDATE research.conversations SET tool_expires_at=now()-interval '1 hour' WHERE id=:id"), {'id': conversation['id']})
    before = client.get(path).json()
    assert before['state'] == 'running'
    client.app.state.user_runners_enabled = True
    reads = runner.reads
    assert client.get(path).json() == before and runner.reads == reads
    assert client.post(path + '/messages', json=message, headers=headers(auth, key)).json() == first.json()
    client.app.state.user_runners_enabled = False
    after = client.get(path).json()
    assert after == before and after['state'] == 'running' and runner.submits == 1
    assert client.post(path + '/messages', json=message, headers=headers(auth, key)).json() == first.json()
    assert runner.submits == 1


def test_status_phase_changes_hints_and_stopped_reprepare_retain_binding(client, admin):
    owner, auth = identity(client, admin)
    client.post('/api/research/codex/runner', json={}, headers=headers(auth))
    state = read(admin, owner)
    with admin.begin() as db: state = Registry(db).capacity(Fence.of(state), admitted=False)
    assert client.get('/api/research/codex/status').json()['retryAfterSeconds'] == 15
    with admin.begin() as db:
        state = Registry(db).capacity(Fence.of(state), admitted=True)
        state = Registry(db).observe(Fence.of(state), pod_uid=uuid4(), ready=True, active_requests=0,
            ambiguous=False, verified=True, now=datetime.now(timezone.utc))
        state = Registry(db).idle(Fence.of(state), now=state.last_activity + timedelta(minutes=30), verified=True)
    assert client.get('/api/research/codex/status').json()['reason'] == 'runner_unreachable'
    with admin.begin() as db:
        Registry(db).stopped(Fence.of(state), terminated_pod_uid=state.pod_uid, verified=True)
        # Synthetic clock alignment for the re-entry HTTP request, not a timeout inference.
        db.execute(text("UPDATE runner_runtime.registry SET last_activity=now()-interval '31 minutes' WHERE owner_id=:owner"), {'owner': owner})
    status = client.get('/api/research/codex/status').json()
    assert status['reason'] == 'idle_stopped' and 'retryAfterSeconds' not in status
    reply = client.post('/api/research/codex/runner', json={}, headers=headers(auth))
    assert reply.status_code == 202 and reply.json()['reason'] == 'provisioning'
    again = read(admin, owner)
    assert again.state_id == state.state_id and again.generation == state.generation + 1


def test_enabled_readiness_schema_drift_refuses_and_off_does_not_adopt_or_repair(client, admin):
    owner, auth = identity(client, admin)
    with admin.begin() as db: db.execute(text('ALTER TABLE runner_runtime.registry ENABLE ROW LEVEL SECURITY'))
    try:
        assert client.get('/readyz').status_code == 503
        assert client.get('/api/research/codex/status').status_code == 503
        assert client.post('/api/research/codex/runner', json={}, headers=headers(auth)).status_code == 503
        assert read(admin, owner) is None
        client.app.state.user_runners_enabled = False
        assert client.get('/readyz').status_code == 200
        assert client.get('/api/research/codex/status').status_code == 200
        with admin.begin() as db:
            assert db.execute(text("SELECT relrowsecurity FROM pg_class WHERE oid='runner_runtime.registry'::regclass")).scalar_one() is True
    finally:
        with admin.begin() as db: db.execute(text('ALTER TABLE runner_runtime.registry DISABLE ROW LEVEL SECURITY'))


def test_enable_flag_defaults_false_and_rejects_invalid_configuration(client, monkeypatch):
    from app.user_runners import feature_enabled
    monkeypatch.delenv('CODEX_USER_RUNNERS_ENABLE', raising=False)
    assert feature_enabled() is False
    monkeypatch.setenv('CODEX_USER_RUNNERS_ENABLE','yes')
    with pytest.raises(ValueError): create_app(client.app.state.settings)
