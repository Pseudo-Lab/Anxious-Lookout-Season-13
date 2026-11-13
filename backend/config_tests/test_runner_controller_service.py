"""#61 in-cluster controller on a disposable PostgreSQL with fake Kubernetes and runner HTTP."""
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text
from app.runner_termination_control import stop_for, queue_entry
from ops.runner_controller_service import Service
from runner.lifecycle import Phase, Refused
from config_tests.test_runner_registry import schema, admin, read
from config_tests.test_user_runner_http import research_schema, client, identity, headers, create_conversation
from config_tests.test_runner_kube import plan
from config_tests.test_user_runner_runtime import runtime
from config_tests.test_runner_termination_control import login

NODE_UID, BOOT = str(uuid4()), str(uuid4())


@pytest.fixture
def service(runtime, admin):
    owner, auth, rt, server, deletes, calls = runtime
    rt.ensure(owner)
    server.pod['spec']['nodeName'] = 'node'
    server.pod['status'] = {'phase': 'Running', 'podIP': '10.42.0.9',
                            'containerStatuses': [{'name': 'runner', 'state': {'running': {}}}]}
    rt.stop(owner)
    with admin.begin() as db:
        stop = stop_for(db, read(admin, owner))
    class Pool:
        @contextmanager
        def bound(self, _): yield rt
    receipts, drains = [], []
    health = {'ownerId': str(owner), 'stateId': str(rt.plan.identity.state_id), 'podUid': str(stop['pod_uid']),
              'verification': 'real', 'available': True, 'reason': None, 'model': 'fixture'}
    class Transport:
        def __init__(self, *args): pass
        def call(self, method, path, body):
            if (method, path) == ('GET', '/health'):
                return dict(health)
            assert method == 'POST' and path == '/lifecycle/drain'
            drains.append(body)
            result = {**body, 'version': 1, 'clean': True}
            receipts.append(result)
            return result
    node = SimpleNamespace(calls=0, boot=BOOT)
    def identity_(client, now):
        node.calls += 1
        return {'nodeName': 'node', 'nodeUid': NODE_UID, 'bootId': node.boot}
    node.identity = identity_
    instance = Service(admin, admin, Pool(), Pool(), node, runner=Transport)
    return SimpleNamespace(service=instance, owner=owner, rt=rt, server=server, deletes=deletes,
                           drains=drains, node=node, stop=stop, health=health)


def stops_completed(admin):
    with admin.begin() as db:
        return db.execute(text('SELECT proof FROM runner_termination.stops WHERE completed_at IS NOT NULL')).scalars().all()


def test_drain_delete_then_pod_absence_releases_once_and_reenters(service, admin):
    token = login(admin, service.owner)
    with admin.begin() as db:
        queue_entry(db, service.owner, uuid4(), token)
    assert service.service.step(service.owner, 300) == 'deleting'
    assert service.deletes == [str(service.stop['pod_uid'])] and len(service.drains) == 1
    assert read(admin, service.owner).phase == Phase.STOPPING and stops_completed(admin) == []
    assert service.service.step(service.owner, 300) == 'released'
    state = read(admin, service.owner)
    assert state.phase == Phase.PREPARING and state.generation == service.stop['generation'] + 1 and state.pod_uid is None
    proof, = stops_completed(admin)
    assert proof['evidence'] == 'pod-deleted' and (proof['nodeUid'], proof['bootId']) == (NODE_UID, BOOT)
    assert len(service.deletes) == 1


def test_deleting_pod_waits_for_kubelet(service, admin):
    service.server.pod['metadata']['deletionTimestamp'] = '2026-10-26T00:00:00Z'
    with admin.begin() as db:
        db.execute(text("""UPDATE runner_termination.stops SET receipt=CAST(:r AS jsonb) WHERE stop_id=:s"""),
                   {'r': '{"version":1}', 's': service.stop['stop_id']})
    assert service.service.step(service.owner, 300) == 'awaiting_pod_deletion'
    assert service.deletes == []


def test_reboot_between_delete_and_proof_reanchors_to_the_reporting_node(service, admin):
    service.service.step(service.owner, 300)
    service.node.boot = str(uuid4())  # The node rebooted after the DELETE.
    assert service.service.step(service.owner, 300) == 'released'
    proof, = stops_completed(admin)
    assert proof['bootId'] == service.node.boot


def test_node_failure_keeps_the_stopping_slot(service, admin):
    service.service.step(service.owner, 300)
    def refused(*args): raise Refused()
    service.node.identity = refused
    with pytest.raises(Refused):
        service.service.step(service.owner, 300)
    state = read(admin, service.owner)
    assert state.phase == Phase.STOPPING and state.pod_uid == service.stop['pod_uid'] and stops_completed(admin) == []


def test_lost_delete_response_is_retried_against_the_same_uid(service, admin, monkeypatch):
    original = service.rt.pods._request
    def lost(method, *args, **kwargs):
        result = original(method, *args, **kwargs)
        if method == 'DELETE': raise TimeoutError()
        return result
    monkeypatch.setattr(service.rt.pods, '_request', lost)
    with pytest.raises(TimeoutError):
        service.service.step(service.owner, 300)
    assert read(admin, service.owner).phase == Phase.STOPPING
    monkeypatch.setattr(service.rt.pods, '_request', original)
    assert service.service.step(service.owner, 300) == 'released'
    assert len(service.deletes) == 1 and len(service.drains) == 1


@pytest.mark.parametrize('dead', ['failed-phase', 'runner-exited'])
def test_dead_runner_fails_its_turn_visibly_and_releases_without_drain(service, admin, dead):
    with admin.begin() as db:
        db.execute(text('DELETE FROM runner_termination.stops WHERE owner_id=:owner'), {'owner': service.owner})
        db.execute(text("INSERT INTO research.conversations(owner_id,title,state) VALUES(:owner,'busy','running')"),
                   {'owner': service.owner})
    exited = {'terminated': {'exitCode': 0, 'reason': 'Completed'}}
    # #68: the native runner Pod has one container.
    service.server.pod['status'] = {'phase': 'Failed' if dead == 'failed-phase' else 'Running',
                                    'containerStatuses': [{'name': 'runner', 'state': exited}]}
    assert service.service.step(service.owner, 300) == 'deleting'
    assert service.drains == [] and len(service.deletes) == 1
    with admin.begin() as db:
        conversation = db.execute(text('SELECT state,error_code FROM research.conversations WHERE owner_id=:owner'),
                                  {'owner': service.owner}).one()
        receipt = stop_for(db, read(admin, service.owner))['receipt']
    assert tuple(conversation) == ('failed', 'codex_runner_lost')
    assert receipt['version'] == 2 and receipt['terminal']['containers']['runner'] == {'exitCode': 0, 'reason': 'Completed'}
    assert service.service.step(service.owner, 300) == 'released'
    assert read(admin, service.owner).phase == Phase.STOPPED


def test_pod_gone_before_any_receipt_is_terminal(service, admin):
    service.server.pod = None
    assert service.service.step(service.owner, 300) == 'released'
    with admin.begin() as db:
        receipt = db.execute(text('SELECT receipt FROM runner_termination.stops WHERE owner_id=:owner'),
                             {'owner': service.owner}).scalar_one()
    assert receipt['terminal'] == {'podAbsent': True} and service.drains == [] and service.deletes == []


def test_replaced_pod_uid_is_never_acted_on(service, admin):
    service.server.pod['metadata']['uid'] = str(uuid4())
    with pytest.raises(Refused):
        service.service.step(service.owner, 300)
    assert service.deletes == [] and service.drains == []


@pytest.mark.parametrize('native_idle', [True, False, 'wrong-uid'])
def test_idle_needs_runner_evidence_before_the_stop_intent(service, admin, native_idle):
    with admin.begin() as db:
        db.execute(text('DELETE FROM runner_termination.stops WHERE owner_id=:owner'), {'owner': service.owner})
        db.execute(text("UPDATE runner_runtime.registry SET last_activity=clock_timestamp()-interval '1 hour' WHERE owner_id=:owner"),
                   {'owner': service.owner})
    class Transport:
        def __init__(self, *args): pass
        def call(self, method, path, body):
            if path == '/health':
                return dict(service.health)
            if path == '/lifecycle/drain':
                return {**body, 'version': 1, 'clean': True}
            assert path == '/lifecycle/idle' and body == {'idleSeconds': 300}
            return {'ownerId': str(service.owner), 'stateId': str(service.rt.plan.identity.state_id),
                    'podUid': str(uuid4()) if native_idle == 'wrong-uid' else str(service.stop['pod_uid']),
                    'idle': native_idle is True}
    service.service.runner = Transport
    if native_idle is True:
        assert service.service.step(service.owner, 300) == 'deleting'
    else:
        with pytest.raises(Refused):
            service.service.step(service.owner, 300)
        with admin.begin() as db:
            assert stop_for(db, read(admin, service.owner)) is None


# #61 R1 / R2: Running runners that are blocked -------------------------------------------------
def blocked(service, *, health, drain):
    """Runner transport answering /health with `health` (merged into the fixture's) and the drain with
    `drain(body)`, which returns a receipt or raises."""
    seen = []
    class Transport:
        def __init__(self, *args): pass
        def call(self, method, path, body):
            seen.append((method, path))
            if (method, path) == ('GET', '/health'):
                return {**service.health, **health}
            assert (method, path) == ('POST', '/lifecycle/drain')
            service.drains.append(body)
            return drain(body)
    service.service.runner = Transport
    return seen


def refuse_drain(status):
    import httpx
    def drain(body):
        raise httpx.HTTPStatusError('refused', request=httpx.Request('POST', 'http://runner/lifecycle/drain'),
                                    response=httpx.Response(status, json={'error': 'codex_unavailable'}))
    return drain


def running_conversation(admin, owner):
    with admin.begin() as db:
        db.execute(text("INSERT INTO research.conversations(owner_id,title,state) VALUES(:owner,'busy','running')"),
                   {'owner': owner})


def conversation(admin, owner):
    with admin.begin() as db:
        return tuple(db.execute(text('SELECT state,error_code FROM research.conversations WHERE owner_id=:owner'),
                                {'owner': owner}).one())


def receipt_of(admin, owner):
    with admin.begin() as db:
        return db.execute(text('SELECT receipt FROM runner_termination.stops WHERE owner_id=:owner AND completed_at IS NULL'),
                          {'owner': owner}).scalar_one_or_none()


def test_unavailable_runner_drains_with_its_failure_and_releases(service, admin):
    running_conversation(admin, service.owner)
    blocked(service, health={'available': False, 'reason': 'auth_revoked', 'failure': 'auth_revoked', 'verification': 'unverified'},
            drain=lambda body: {**body, 'version': 3, 'unavailable': {'reason': 'auth_revoked', 'clean': False}})
    assert service.service.step(service.owner, 300) == 'deleting'
    receipt = receipt_of(admin, service.owner)
    assert receipt['version'] == 3 and receipt['unavailable'] == {'reason': 'auth_revoked', 'clean': False}
    assert conversation(admin, service.owner) == ('failed', 'codex_runner_lost')
    assert read(admin, service.owner).phase == Phase.STOPPING and len(service.deletes) == 1
    assert service.service.step(service.owner, 300) == 'released'
    assert read(admin, service.owner).phase == Phase.STOPPED


@pytest.mark.parametrize('health', [
    {'available': False, 'reason': 'policy_refused', 'failure': 'policy_refused'},  # another failure
    {'available': False, 'reason': 'auth_revoked'},                                  # no failure field
    {'available': True, 'reason': None, 'failure': 'auth_revoked'}])                 # says available
def test_unavailable_receipt_needs_the_same_failure_in_health(service, admin, health):
    running_conversation(admin, service.owner)
    blocked(service, health=health,
            drain=lambda body: {**body, 'version': 3, 'unavailable': {'reason': 'auth_revoked', 'clean': True}})
    assert service.service.step(service.owner, 300) == 'unavailable_reason_mismatch'
    assert receipt_of(admin, service.owner) is None and service.deletes == []
    assert conversation(admin, service.owner) == ('running', None)
    assert read(admin, service.owner).phase != Phase.STOPPING


@pytest.mark.parametrize('open_stop', [True, False])
def test_operator_reclaim_records_what_it_observed_then_the_loop_releases(service, admin, open_stop):
    if not open_stop:
        with admin.begin() as db:
            db.execute(text('DELETE FROM runner_termination.stops WHERE owner_id=:owner'), {'owner': service.owner})
    running_conversation(admin, service.owner)
    blocked(service, health={'available': False, 'reason': 'auth_revoked', 'verification': 'unverified'}, drain=refuse_drain(503))
    result = service.service.reclaim(service.owner, service.stop['pod_uid'])
    assert result['result'] == 'accepted' and result['stopOpenedByThisRun'] is (not open_stop)
    assert result['failedConversations'] == 1 and service.deletes == []
    receipt = receipt_of(admin, service.owner)
    assert receipt['version'] == 4 and receipt['operator']['drainStatus'] == 503
    assert receipt['operator']['health'] == {'available': False, 'reason': 'auth_revoked', 'verification': 'unverified'}
    assert receipt['operator'] == result['operator']
    assert conversation(admin, service.owner) == ('failed', 'codex_runner_lost')
    drains = len(service.drains)
    assert service.service.step(service.owner, 300) == 'deleting'
    assert len(service.drains) == drains and len(service.deletes) == 1  # The receipt exists: no second drain.
    assert service.service.step(service.owner, 300) == 'released'
    assert read(admin, service.owner).phase == Phase.STOPPED


@pytest.mark.parametrize('case,code', [
    ('available', 'runner_available'), ('stopping', 'runner_stopping'), ('wrong-uid', 'pod_uid_mismatch'),
    ('dead', 'runner_dead'), ('drained', 'drain_accepted'), ('drain-401', 'drain_status'), ('deleting', 'pod_deleting'),
    ('receipt', 'receipt_exists')])
def test_operator_reclaim_refuses_with_observed_values_and_writes_no_receipt(service, admin, case, code):
    from ops.runner_controller_service import Refusal
    running_conversation(admin, service.owner)
    health = {'available': False, 'reason': 'auth_revoked', 'verification': 'unverified'}
    drain = refuse_drain(409)
    if case == 'available': health = {'available': True, 'reason': None}
    if case == 'stopping': health = {'available': False, 'reason': 'unavailable'}
    if case == 'dead':
        service.server.pod['status'] = {'phase': 'Failed', 'containerStatuses': [
            {'name': 'runner', 'state': {'terminated': {'exitCode': 137, 'reason': 'OOMKilled'}}}]}
    if case == 'drained': drain = lambda body: {**body, 'version': 1, 'clean': True}
    if case == 'drain-401': drain = refuse_drain(401)
    if case == 'deleting': service.server.pod['metadata']['deletionTimestamp'] = '2026-11-01T00:00:00Z'
    if case == 'receipt':
        with admin.begin() as db:
            db.execute(text("UPDATE runner_termination.stops SET receipt=CAST(:r AS jsonb) WHERE stop_id=:s"),
                       {'r': '{"version":1}', 's': service.stop['stop_id']})
    blocked(service, health=health, drain=drain)
    with pytest.raises(Refusal) as caught:
        service.service.reclaim(service.owner, uuid4() if case == 'wrong-uid' else service.stop['pod_uid'])
    assert caught.value.code == code and caught.value.observed
    if case in {'available', 'stopping'}:
        assert caught.value.observed['health']['available'] is (case == 'available')
    if case in {'drained', 'drain-401'}:
        assert caught.value.observed['stopId'] == str(service.stop['stop_id'])
    assert service.deletes == [] and conversation(admin, service.owner) == ('running', None)
    receipt = receipt_of(admin, service.owner)
    assert receipt is None or receipt == {'version': 1}
