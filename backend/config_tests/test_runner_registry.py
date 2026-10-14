"""Real disposable PostgreSQL transactions, no HTTP/controller/Pod/provider proof."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from app.migrate import migrate as auth_migrate
from app.runner_migrate import migrate
from app.runner_registry import Registry
from app.runner_schema import validate
from runner.lifecycle import Phase, Fence, Completion, Refused


@pytest.fixture(scope='session', autouse=True)
def schema():
    auth_migrate(); migrate()


@pytest.fixture
def admin(schema):
    engine = create_engine(os.environ['ADMIN_DATABASE_URL'], hide_parameters=True)
    with engine.begin() as db:
        db.execute(text('TRUNCATE auth.permission_audit,auth.sessions,auth.oauth_transactions,auth.accounts CASCADE'))
    yield engine
    engine.dispose()


@pytest.fixture
def owner(admin):
    identity = uuid4()
    with admin.begin() as db:
        db.execute(text("INSERT INTO auth.accounts(id,github_id,login,role,is_approved) VALUES(:id,'12345','synthetic','editor',true)"), {'id': identity})
    return identity


@pytest.fixture
def t0():
    return datetime.now(timezone.utc)


def enter(engine, owner, key, now):
    with engine.begin() as db:
        return Registry(db).enter(owner, key, now)


def read(engine, owner):
    with engine.begin() as db: return Registry(db).read(owner)


def ready(engine, owner, now):
    state, _ = enter(engine, owner, uuid4(), now)
    with engine.begin() as db:
        return Registry(db).observe(Fence.of(state), pod_uid=uuid4(), ready=True,
               active_requests=0, ambiguous=False, verified=True, now=now)


def test_concurrent_first_entries_have_one_state_generation_and_outbox(admin, owner, t0):
    keys = [uuid4() for _ in range(12)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        states = list(pool.map(lambda key: enter(admin, owner, key, t0)[0], keys))
    assert len({state.state_id for state in states}) == 1 and {state.generation for state in states} == {1}
    with admin.begin() as db:
        assert db.execute(text('SELECT count(*) FROM runner_runtime.registry')).scalar_one() == 1
        assert db.execute(text('SELECT count(*) FROM runner_runtime.outbox')).scalar_one() == 1
        assert db.execute(text('SELECT count(*) FROM runner_runtime.entry_receipts')).scalar_one() == 12
    with admin.begin() as db: validate(db)


def test_same_key_and_pending_coalesce_do_not_change_activity(admin, owner, t0):
    key = uuid4(); first, replayed = enter(admin, owner, key, t0)
    assert not replayed
    second, replayed = enter(admin, owner, key, t0 + timedelta(minutes=1))
    assert replayed and second == first
    coalesced, replayed = enter(admin, owner, uuid4(), t0 + timedelta(minutes=2))
    assert not replayed and coalesced == first
    assert read(admin, owner) == first


def test_two_owners_same_intent_key_keep_distinct_state_and_receipt(admin, owner, t0):
    another = uuid4()
    with admin.begin() as db:
        db.execute(text("INSERT INTO auth.accounts(id,github_id,login,role,is_approved) VALUES(:id,'54321','other','admin',true)"), {'id': another})
    key = uuid4()
    a, _ = enter(admin, owner, key, t0); b, _ = enter(admin, another, key, t0)
    assert a.state_id != b.state_id and a.owner != b.owner
    assert enter(admin, owner, key, t0)[1] and enter(admin, another, key, t0)[1]


@pytest.mark.parametrize('role,approved', [('commenter',True),('editor',False),('admin',False)])
def test_current_membership_checked_before_existing_replay(admin, owner, t0, role, approved):
    key = uuid4(); before, _ = enter(admin, owner, key, t0)
    with admin.begin() as db:
        db.execute(text('UPDATE auth.accounts SET role=:role,is_approved=:approved WHERE id=:owner'),
                   {'owner': owner, 'role': role, 'approved': approved})
    with pytest.raises(Refused): enter(admin, owner, key, t0 + timedelta(minutes=1))
    assert read(admin, owner) == before


def test_entry_failure_rolls_back_state_receipt_and_outbox(admin, owner, t0):
    key = uuid4()
    with pytest.raises(RuntimeError):
        with admin.begin() as db:
            Registry(db).enter(owner, key, t0)
            raise RuntimeError('synthetic process/transaction failure')
    assert read(admin, owner) is None
    with admin.begin() as db:
        assert db.execute(text('SELECT count(*) FROM runner_runtime.entry_receipts')).scalar_one() == 0
        assert db.execute(text('SELECT count(*) FROM runner_runtime.outbox')).scalar_one() == 0
    enter(admin, owner, key, t0)
    admin.dispose()
    assert enter(admin, owner, key, t0)[1]  # New DB connection, committed receipt persists.


def test_admission_completion_are_atomic_and_repeated_actual_time_does_not_extend_idle(admin, owner, t0):
    state = ready(admin, owner, t0); request = uuid4()
    with admin.begin() as db:
        state = Registry(db).admit(Fence.of(state), request, t0 + timedelta(minutes=1))
    assert state.active_requests == 1
    completed = Completion(request, t0 + timedelta(minutes=2))
    with admin.begin() as db:
        state = Registry(db).complete(Fence.of(state), completion=completed, verified=True, now=t0 + timedelta(minutes=40))
    assert state.last_activity == completed.completed_at and state.active_requests == 1
    with admin.begin() as db:
        same = Registry(db).complete(Fence.of(state), completion=completed, verified=True, now=t0 + timedelta(minutes=50))
    assert same == state and same.completions == ()
    with pytest.raises(Refused):
        with admin.begin() as db:
            Registry(db).complete(Fence.of(state), completion=Completion(request, t0 + timedelta(minutes=49)),
                                  verified=True, now=t0 + timedelta(minutes=50))
    assert read(admin, owner) == state
    with admin.begin() as db:
        state = Registry(db).observe(Fence.of(state), pod_uid=state.pod_uid, ready=True,
            active_requests=0, ambiguous=False, verified=True, now=t0 + timedelta(minutes=50))
    assert state.active_requests == 0 and state.last_activity == completed.completed_at


def test_observation_cannot_erase_uncompleted_admitted_request_or_idle_it(admin, owner, t0):
    state = ready(admin, owner, t0); request = uuid4()
    with admin.begin() as db: state = Registry(db).admit(Fence.of(state), request, t0)
    with admin.begin() as db:
        state = Registry(db).observe(Fence.of(state), pod_uid=state.pod_uid, ready=True,
            active_requests=0, ambiguous=False, verified=True, now=t0 + timedelta(minutes=60))
    assert state.active_requests == 1 and state.ambiguous and state.last_activity == t0
    with pytest.raises(Refused):
        with admin.begin() as db: Registry(db).idle(Fence.of(state), now=t0 + timedelta(minutes=60), verified=True)
    assert read(admin, owner) == state


def test_completion_of_unadmitted_request_wrong_fence_or_future_time_refused(admin, owner, t0):
    state = ready(admin, owner, t0)
    with pytest.raises(Refused):
        with admin.begin() as db:
            Registry(db).complete(Fence.of(state), completion=Completion(uuid4(), t0), verified=True, now=t0)
    request = uuid4()
    with admin.begin() as db: updated = Registry(db).admit(Fence.of(state), request, t0)
    for fence, completion in ((Fence.of(state), Completion(request, t0)),
                              (Fence.of(updated), Completion(request, t0 + timedelta(minutes=1)))):
        with pytest.raises(Refused):
            with admin.begin() as db:
                Registry(db).complete(fence, completion=completion, verified=True, now=t0)
    assert read(admin, owner) == updated


def test_failed_admit_and_completion_transactions_keep_receipt_state_consistent(admin, owner, t0):
    state = ready(admin, owner, t0); request = uuid4()
    with pytest.raises(RuntimeError):
        with admin.begin() as db:
            Registry(db).admit(Fence.of(state), request, t0)
            raise RuntimeError('synthetic failure')
    assert read(admin, owner) == state
    with admin.begin() as db: admitted = Registry(db).admit(Fence.of(state), request, t0)
    with pytest.raises(RuntimeError):
        with admin.begin() as db:
            Registry(db).complete(Fence.of(admitted), completion=Completion(request, t0 + timedelta(minutes=1)),
                                  verified=True, now=t0 + timedelta(minutes=1))
            raise RuntimeError('synthetic failure')
    assert read(admin, owner) == admitted
    with admin.begin() as db:
        assert db.execute(text('SELECT completed_at FROM runner_runtime.admissions WHERE owner_id=:owner AND request_id=:request'),
                          {'owner': owner, 'request': request}).scalar_one() is None


def test_idle_stop_outbox_and_reentry_preserve_state_and_all_receipts(admin, owner, t0):
    state = ready(admin, owner, t0)
    with pytest.raises(Refused):
        with admin.begin() as db: Registry(db).idle(Fence.of(state), now=t0 + timedelta(minutes=29), verified=True)
    with admin.begin() as db:
        stopping = Registry(db).idle(Fence.of(state), now=t0 + timedelta(minutes=30), verified=True)
    with pytest.raises(Refused):
        with admin.begin() as db: Registry(db).stopped(Fence.of(stopping), terminated_pod_uid=stopping.pod_uid, verified=False)
    with admin.begin() as db:
        stopped = Registry(db).stopped(Fence.of(stopping), terminated_pod_uid=stopping.pod_uid, verified=True)
    assert stopped.phase == Phase.STOPPED and stopped.state_id == state.state_id and stopped.pod_uid is None
    again, _ = enter(admin, owner, uuid4(), t0 + timedelta(minutes=31))
    assert again.state_id == state.state_id and again.generation == 2
    with admin.begin() as db:
        assert db.execute(text("SELECT count(*) FROM runner_runtime.outbox WHERE kind='stop-pod-only' AND pod_uid=:pod"),
                          {'pod': state.pod_uid}).scalar_one() == 1
        assert db.execute(text('SELECT count(*) FROM runner_runtime.entry_receipts')).scalar_one() == 2


def test_completed_history_is_retained_and_targeted_replay_does_not_change_activity(admin, owner, t0):
    state = ready(admin, owner, t0); requests = [uuid4() for _ in range(15000)]
    with admin.begin() as db:
        db.execute(text('''INSERT INTO runner_runtime.admissions
            (owner_id,request_id,state_id,generation,pod_uid,admitted_at,completed_at)
            VALUES(:owner,:request,:state,:generation,:pod,:time,:time)'''),
            [{'owner': owner, 'request': request, 'state': state.state_id, 'generation': state.generation,
              'pod': state.pod_uid, 'time': t0} for request in requests])
    with admin.begin() as db:
        result = Registry(db).complete(Fence.of(state), completion=Completion(requests[-1], t0),
                                      verified=True, now=t0 + timedelta(hours=1))
    assert result == state and result.completions == ()
    with admin.begin() as db:
        assert db.execute(text('SELECT count(*) FROM runner_runtime.admissions')).scalar_one() == 15000
    with pytest.raises(Refused):
        with admin.begin() as db: Registry(db).admit(Fence.of(state), requests[0], t0)


def test_api_role_cannot_delete_receipts_or_rebind_owner_state(admin, owner, t0):
    state, _ = enter(admin, owner, uuid4(), t0)
    api = create_engine(os.environ['DATABASE_URL'], hide_parameters=True)
    try:
        for statement in ('DELETE FROM runner_runtime.entry_receipts',
                          'DELETE FROM runner_runtime.registry',
                          'UPDATE runner_runtime.registry SET state_id=gen_random_uuid()',
                          'UPDATE runner_runtime.registry SET owner_id=gen_random_uuid()',
                          'UPDATE runner_runtime.admissions SET request_id=gen_random_uuid()'):
            with pytest.raises(DBAPIError):
                with api.begin() as db: db.execute(text(statement))
        with api.begin() as db:
            result, replayed = Registry(db).enter(owner, uuid4(), t0)
        assert result == state and not replayed
    finally: api.dispose()


def test_migration_repeat_preserves_data_and_unknown_drift_is_not_repaired(admin, owner, t0):
    state, _ = enter(admin, owner, uuid4(), t0)
    migrate(); assert read(admin, owner) == state
    with admin.begin() as db: db.execute(text('ALTER TABLE runner_runtime.registry ADD COLUMN unexpected text'))
    try:
        with pytest.raises(RuntimeError): migrate()
        with admin.begin() as db:
            assert db.execute(text("SELECT count(*) FROM information_schema.columns WHERE table_schema='runner_runtime' AND table_name='registry' AND column_name='unexpected'")).scalar_one() == 1
    finally:
        with admin.begin() as db: db.execute(text('ALTER TABLE runner_runtime.registry DROP COLUMN unexpected'))
    migrate()


def test_refusal_caught_inside_outer_transaction_cannot_commit_partial_admission(admin, owner, t0, monkeypatch):
    state = ready(admin, owner, t0); request = uuid4()
    save = Registry._save
    def failure(self, *args, **kwargs):
        save(self, *args, **kwargs)
        raise Refused('synthetic after-save failure')
    monkeypatch.setattr(Registry, '_save', failure)
    with admin.begin() as db:
        with pytest.raises(Refused): Registry(db).admit(Fence.of(state), request, t0)
        assert Registry(db).read(owner) == state
    assert read(admin, owner) == state
    with admin.begin() as db:
        assert db.execute(text('SELECT count(*) FROM runner_runtime.admissions')).scalar_one() == 0


def test_capacity_wait_and_current_fences_do_not_extend_activity(admin, owner, t0):
    state, _ = enter(admin, owner, uuid4(), t0)
    with admin.begin() as db: waiting = Registry(db).capacity(Fence.of(state), admitted=False)
    assert waiting.phase == Phase.WAITING and waiting.pod_uid is None and waiting.last_activity == t0
    with pytest.raises(Refused):
        with admin.begin() as db: Registry(db).capacity(Fence.of(state), admitted=True)
    with admin.begin() as db: retrying = Registry(db).capacity(Fence.of(waiting), admitted=True)
    assert retrying.phase == Phase.PREPARING and retrying.generation == state.generation and retrying.last_activity == t0
    with admin.begin() as db:
        assert db.execute(text("SELECT count(*) FROM runner_runtime.outbox WHERE kind='ensure'")).scalar_one() == 2


def test_fresh_explicit_migration_keeps_existing_account_and_auth_grants(admin, owner, t0):
    with admin.begin() as db: db.execute(text('DROP SCHEMA runner_runtime CASCADE'))
    migrate()
    with admin.begin() as db:
        validate(db)
        assert db.execute(text('SELECT role,is_approved FROM auth.accounts WHERE id=:owner'), {'owner': owner}).one() == ('editor', True)
        assert db.execute(text("SELECT has_column_privilege('anxious_api','auth.accounts','role','UPDATE')")).scalar_one() is False
    enter(admin, owner, uuid4(), t0)


def test_existing_research_contract_remains_valid_after_additive_runtime_migration(admin, owner, t0):
    from app.research_schema_contract import validate_research
    auth_migrate('0004_publication')
    migrate()
    with admin.begin() as db:
        assert validate_research(db) == '0004_publication'
        validate(db)
        assert db.execute(text('SELECT role,is_approved FROM auth.accounts WHERE id=:owner'), {'owner': owner}).one() == ('editor', True)
