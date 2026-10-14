"""Read-only operations inventory and test-only HTTP phase setup, real Docker PG."""
import os
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from app.runner_controller import Controller
from app.runner_registry import Registry
from ops.runner_recovery import inventory
from runner.lifecycle import Fence, Refused
from tests.runner_phase_fixture import fixture_engine, set_phase
from config_tests.test_runner_registry import schema, admin, owner, t0, enter, read, preservation_snapshot


def test_inventory_uncertain_slot_no_content_and_no_mutation(admin, owner, t0):
    state = enter(admin, owner, uuid4(), t0)[0]
    Controller(admin, None, lambda: t0, max_pods=1).reserve(Fence.of(state))
    before = preservation_snapshot(admin)
    result = inventory(admin, owner=owner)
    assert result['releaseToolAvailable'] is False and result['nextAfter'] is None
    row, = result['records']
    assert row['capacityHeld'] and row['nextEvidence'] == 'lookup_only'
    assert row['generation'] == 1 and row['podUid'] is None and row['issuedEnsureReceipts'] == 1
    assert set(row) == {'ownerId','stateId','generation','revision','phase','podUid','lastActivity',
                        'activeRequests','ambiguous','uncompletedAdmissions','issuedEnsureReceipts','capacityHeld','nextEvidence'}
    assert preservation_snapshot(admin) == before


def test_inventory_bound_pending_and_stopped_keep_distinct_evidence(admin, owner, t0):
    state = enter(admin, owner, uuid4(), t0)[0]
    with admin.begin() as db:
        registry = Registry(db)
        state = registry.observe(Fence.of(state), pod_uid=uuid4(), ready=True,
            active_requests=0, ambiguous=False, verified=True, now=t0)
    row = inventory(admin, owner=owner)['records'][0]
    assert row['capacityHeld'] and row['nextEvidence'] == 'native_evidence_required'
    with admin.begin() as db:
        registry = Registry(db)
        stopping = registry.idle(Fence.of(state), now=t0 + timedelta(minutes=30), verified=True)
        registry.stopped(Fence.of(stopping), terminated_pod_uid=state.pod_uid, verified=True)
    row = inventory(admin, owner=owner)['records'][0]
    assert not row['capacityHeld'] and row['nextEvidence'] == 'already_stopped'


def test_inventory_api_role_pagination_and_empty_account(admin, owner, t0):
    enter(admin, owner, uuid4(), t0)
    engine = create_engine(os.environ['DATABASE_URL'], hide_parameters=True)
    try:
        result = inventory(engine, limit=1)
        assert result['nextAfter'] == str(owner)
        assert inventory(engine, after=owner)['records'] == []
        assert inventory(engine, owner=uuid4())['records'] == []
    finally:
        engine.dispose()


@pytest.mark.parametrize('phase,expected', [('preparing','provisioning'),('waiting','capacity_wait'),
    ('unreachable','runner_unreachable'),('ready','ready'),('stopped','idle_stopped'),('failed','runner_failed')])
def test_fixture_phase_preserves_identity_activity_and_receipts(admin, owner, t0, phase, expected):
    state = enter(admin, owner, uuid4(), t0)[0]
    set_phase(admin, owner, phase)
    result = read(admin, owner)
    assert result.phase.value == expected and result.state_id == state.state_id and result.last_activity == t0
    with admin.begin() as db:
        assert db.execute(text('SELECT count(*) FROM runner_runtime.entry_receipts')).scalar_one() == 1
        assert db.execute(text('SELECT count(*) FROM runner_runtime.outbox')).scalar_one() == 1


def test_fixture_refuses_issued_permission_without_reset(admin, owner, t0):
    state = enter(admin, owner, uuid4(), t0)[0]
    Controller(admin, None, lambda: t0, max_pods=1).reserve(Fence.of(state))
    before = preservation_snapshot(admin)
    with pytest.raises(Refused): set_phase(admin, owner, 'preparing')
    assert preservation_snapshot(admin) == before


def test_fixture_refuses_uncompleted_admission(admin, owner, t0):
    state = enter(admin, owner, uuid4(), t0)[0]
    with admin.begin() as db:
        registry = Registry(db)
        state = registry.observe(Fence.of(state), pod_uid=uuid4(), ready=True,
            active_requests=0, ambiguous=False, verified=True, now=t0)
        registry.admit(Fence.of(state), uuid4(), t0)
    before = preservation_snapshot(admin)
    with pytest.raises(Refused): set_phase(admin, owner, 'preparing')
    assert preservation_snapshot(admin) == before


@pytest.mark.parametrize('variable,value', [('APP_ENV','production'),('OAUTH_MODE','github'),
    ('RUNNER_HTTP_FIXTURE','false'),('ADMIN_DATABASE_URL','postgresql://postgres:private@live/production')])
def test_fixture_entrypoint_refuses_operational_environment(monkeypatch, variable, value):
    monkeypatch.setenv('APP_ENV', 'test'); monkeypatch.setenv('OAUTH_MODE', 'mock')
    monkeypatch.setenv('RUNNER_HTTP_FIXTURE', 'true')
    monkeypatch.setenv(variable, value)
    with pytest.raises(Refused): fixture_engine()
