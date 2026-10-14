"""Real PostgreSQL coordination with a synthetic Pod API; no actual Kubernetes."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Lock
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.runner_controller import Controller, Identity, Pod
from app.runner_registry import Registry
from runner.lifecycle import Fence, Phase, Refused
from config_tests.test_runner_registry import schema, admin, owner, t0, enter, read, preservation_snapshot


class Pods:
    def __init__(self):
        self.values = {}
        self.creates = []
        self.lock = Lock()
        self.failure = None
        self.foreign = None

    def lookup(self, identity):
        return self.foreign or self.values.get(identity.name)

    def create_once(self, identity):
        with self.lock:
            self.creates.append(identity)
            if self.failure == 'before':
                raise TimeoutError('synthetic unknown delivery')
            assert identity.name not in self.values
            pod = Pod(identity, uuid4())
            self.values[identity.name] = pod
            if self.failure == 'after':
                raise TimeoutError('synthetic lost response')
            return pod


@pytest.fixture
def pods():
    return Pods()


def candidate(admin, owner, t0):
    return enter(admin, owner, uuid4(), t0)[0]


def controller(admin, pods, t0, max_pods=1):
    return Controller(admin, pods, lambda: t0 + timedelta(seconds=1), max_pods=max_pods)


def issued(admin, state):
    with admin.begin() as db:
        return db.execute(text("SELECT count(*) FROM runner_runtime.outbox WHERE owner_id=:owner AND kind='ensure' AND settled"), {'owner': state.owner}).scalar_one()


def test_single_create_binds_identity_but_never_ready_or_activity(admin, owner, t0, pods):
    state = candidate(admin, owner, t0)
    result = controller(admin, pods, t0).ensure(Fence.of(state))
    assert len(pods.creates) == 1 and issued(admin, state) == 1
    assert result.phase == Phase.UNREACHABLE and result.ambiguous and result.active_requests == 0
    assert result.last_activity == t0 and result.state_id == state.state_id
    assert result.pod_uid == pods.values[Identity.of(state).name].uid
    assert read(admin, owner) == result
    with pytest.raises(Refused):
        with admin.begin() as db:
            Registry(db).idle(Fence.of(result), now=t0 + timedelta(hours=1), verified=True)
    assert read(admin, owner) == result


@pytest.mark.parametrize('max_pods', [0, 1])
def test_capacity_wait_does_not_extend_activity_and_can_resume(admin, owner, t0, pods, max_pods):
    state = candidate(admin, owner, t0)
    if max_pods:
        other = new_owner(admin)
        controller(admin, pods, t0).ensure(Fence.of(candidate(admin, other, t0)))
    worker = controller(admin, pods, t0, max_pods)
    assert worker.ensure(Fence.of(state)) is None
    waiting = read(admin, owner)
    assert waiting.phase == Phase.WAITING and waiting.last_activity == t0 and issued(admin, state) == 0
    assert worker.ensure(Fence.of(waiting)) is None
    assert read(admin, owner) == waiting
    result = controller(admin, pods, t0, max_pods + 1).ensure(Fence.of(waiting))
    assert result.pod_uid and result.last_activity == t0


def new_owner(admin):
    owner = uuid4()
    with admin.begin() as db:
        db.execute(text("INSERT INTO auth.accounts(id,github_id,login,role,is_approved) VALUES(:owner,:github,'fixture','editor',true)"),
                   {'owner': owner, 'github': str(owner.int % (2 ** 63))})
    return owner


def test_concurrent_owners_reserve_only_one_global_slot(admin, owner, t0, pods):
    states = [candidate(admin, owner, t0), candidate(admin, new_owner(admin), t0)]
    worker = controller(admin, pods, t0)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda state: worker.ensure(Fence.of(state)), states))
    assert sum(result is not None for result in results) == len(pods.creates) == 1
    assert sorted(read(admin, state.owner).phase.value for state in states) == ['capacity_wait', 'runner_unreachable']


def test_two_workers_same_owner_never_create_twice(admin, owner, t0, pods):
    state = candidate(admin, owner, t0)
    worker = controller(admin, pods, t0)
    def run():
        try:
            return worker.ensure(Fence.of(state))
        except Refused:
            return None  # Stale fence or not-yet-visible reserved create.
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda _: run(), range(12)))
    assert len(pods.creates) == 1 and issued(admin, state) == 1
    assert read(admin, owner).pod_uid == pods.values[Identity.of(state).name].uid


@pytest.mark.parametrize('failure', ['before', 'after'])
def test_unknown_create_delivery_never_reposts_and_holds_capacity(admin, owner, t0, pods, failure):
    state = candidate(admin, owner, t0)
    pods.failure = failure
    worker = controller(admin, pods, t0)
    with pytest.raises(TimeoutError): worker.ensure(Fence.of(state))
    assert issued(admin, state) == 1 and read(admin, owner) == state
    other = candidate(admin, new_owner(admin), t0)
    assert worker.ensure(Fence.of(other)) is None
    pods.failure = None
    # Simulate an independent controller process using only committed DB state.
    resumed = controller(admin, pods, t0)
    if failure == 'before':
        for _ in range(2):
            with pytest.raises(Refused): resumed.ensure(Fence.of(read(admin, owner)))
        assert read(admin, owner) == state
    else:
        assert resumed.ensure(Fence.of(state)).pod_uid == pods.values[Identity.of(state).name].uid
    assert len(pods.creates) == 1


def test_crash_between_reservation_and_external_call_is_lookup_only(admin, owner, t0, pods):
    state = candidate(admin, owner, t0)
    ticket = controller(admin, pods, t0).reserve(Fence.of(state))
    assert ticket.create and issued(admin, state) == 1
    with pytest.raises(Refused): controller(admin, pods, t0).ensure(Fence.of(state))
    assert pods.creates == [] and read(admin, owner) == state


def test_database_rollback_after_successful_create_adopts_same_pod(admin, owner, t0, pods):
    state = candidate(admin, owner, t0)
    # Clock reversal refuses the DB observation after the external create.
    worker = Controller(admin, pods, lambda: t0 - timedelta(seconds=1), max_pods=1)
    with pytest.raises(Refused): worker.ensure(Fence.of(state))
    assert read(admin, owner) == state and issued(admin, state) == 1
    result = controller(admin, pods, t0).ensure(Fence.of(state))
    assert len(pods.creates) == 1 and result.pod_uid == pods.values[Identity.of(state).name].uid


@pytest.mark.parametrize('field', ['owner', 'state_id', 'generation'])
def test_foreign_or_old_pod_is_not_adopted_or_replaced(admin, owner, t0, pods, field):
    state = candidate(admin, owner, t0)
    identity = Identity.of(state)
    values = dict(owner=identity.owner, state_id=identity.state_id, generation=identity.generation)
    values[field] = 2 if field == 'generation' else uuid4()
    pods.foreign = Pod(Identity(**values), uuid4())
    with pytest.raises(Refused): controller(admin, pods, t0).ensure(Fence.of(state))
    assert read(admin, owner) == state and issued(admin, state) == 1 and not pods.creates


def test_lost_bound_pod_never_recreated_without_termination_evidence(admin, owner, t0, pods):
    state = candidate(admin, owner, t0)
    worker = controller(admin, pods, t0)
    bound = worker.ensure(Fence.of(state))
    pods.values.clear()  # API absence is not child termination.
    snapshot = preservation_snapshot(admin)
    with pytest.raises(Refused): worker.ensure(Fence.of(bound))
    assert len(pods.creates) == 1 and preservation_snapshot(admin) == snapshot


def test_revocation_between_reservation_and_create_never_creates(admin, owner, t0, pods, monkeypatch):
    state = candidate(admin, owner, t0)
    worker = controller(admin, pods, t0)
    original = worker.reserve
    def reserve(fence):
        ticket = original(fence)
        with admin.begin() as db:
            db.execute(text('UPDATE auth.accounts SET is_approved=false WHERE id=:owner'), {'owner': owner})
        return ticket
    monkeypatch.setattr(worker, 'reserve', reserve)
    with pytest.raises(Refused): worker.ensure(Fence.of(state))
    assert pods.creates == [] and issued(admin, state) == 1


@pytest.mark.parametrize('value', [True, -1, '1', None])
def test_invalid_capacity_refused(admin, pods, t0, value):
    with pytest.raises(Refused): controller(admin, pods, t0, value)


def test_next_generation_requires_registry_termination_barrier_and_retains_history(admin, owner, t0, pods):
    state = candidate(admin, owner, t0)
    worker = controller(admin, pods, t0)
    bound = worker.ensure(Fence.of(state))
    # Synthetic native quiescence/termination evidence is supplied directly here;
    # this is sequencing coverage, NOT proof of a real process termination.
    with admin.begin() as db:
        registry = Registry(db)
        ready = registry.observe(Fence.of(bound), pod_uid=bound.pod_uid, ready=True,
            active_requests=0, ambiguous=False, verified=True, now=t0)
        stopping = registry.idle(Fence.of(ready), now=t0 + timedelta(minutes=30), verified=True)
    with pytest.raises(Refused):
        enter(admin, owner, uuid4(), t0 + timedelta(minutes=31))
    assert len(pods.creates) == 1
    pods.values.clear()  # Stand-in for externally verified stop and API deletion.
    with admin.begin() as db:
        Registry(db).stopped(Fence.of(stopping), terminated_pod_uid=bound.pod_uid, verified=True)
    new = candidate(admin, owner, t0 + timedelta(minutes=31))
    result = controller(admin, pods, t0 + timedelta(minutes=31)).ensure(Fence.of(new))
    assert result.state_id == bound.state_id and result.generation == bound.generation + 1
    assert result.pod_uid != bound.pod_uid and len(pods.creates) == 2
    with admin.begin() as db:
        assert db.execute(text('SELECT count(*) FROM runner_runtime.entry_receipts')).scalar_one() == 2
        assert db.execute(text("SELECT count(*) FROM runner_runtime.outbox WHERE kind='ensure' AND settled")).scalar_one() == 2


def test_api_database_role_can_coordinate_without_admin_grants(admin, owner, t0, pods):
    import os
    from sqlalchemy import create_engine
    state = candidate(admin, owner, t0)
    engine = create_engine(os.environ['DATABASE_URL'], hide_parameters=True)
    try:
        result = controller(engine, pods, t0).ensure(Fence.of(state))
        assert result.pod_uid and result.ambiguous
    finally:
        engine.dispose()


@pytest.mark.parametrize('after_reservation', [False, True])
def test_schema_drift_refuses_before_external_operations(admin, owner, t0, pods, monkeypatch, after_reservation):
    state = candidate(admin, owner, t0)
    worker = controller(admin, pods, t0)
    before = preservation_snapshot(admin)
    def drift():
        with admin.begin() as db:
            db.execute(text('ALTER TABLE runner_runtime.outbox ENABLE ROW LEVEL SECURITY'))
    if after_reservation:
        original = worker.reserve
        def reserve(fence):
            result = original(fence)
            drift()
            return result
        monkeypatch.setattr(worker, 'reserve', reserve)
    else:
        drift()
    try:
        with pytest.raises(RuntimeError): worker.ensure(Fence.of(state))
        assert pods.creates == [] and read(admin, owner) == state
    finally:
        with admin.begin() as db:
            db.execute(text('ALTER TABLE runner_runtime.outbox DISABLE ROW LEVEL SECURITY'))
    if after_reservation:
        assert issued(admin, state) == 1  # Reserved permission is never reset.
    else:
        assert preservation_snapshot(admin) == before
