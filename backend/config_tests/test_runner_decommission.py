"""#66 decommission against real PostgreSQL (review's DB suite); fake Kube/native/host as the observer tests."""
import json
from uuid import uuid4

import pytest
from sqlalchemy import text

from ops import runner_decommission as tool
from runner.lifecycle import Phase
from config_tests.test_runner_registry import schema, admin, read
from config_tests.test_user_runner_http import research_schema, client, identity, headers, create_conversation
from config_tests.test_runner_kube import plan
from config_tests.test_user_runner_runtime import runtime
from config_tests.test_runner_termination_control import login
from config_tests.test_runner_stop_observer import observer
from config_tests.test_runner_bindings import binding_schema


def publish(admin, owner, rt):
    state = read(admin, owner)
    p = rt.plan
    value = {'owner': str(owner), 'state': str(state.state_id), 'generation': 1, 'image': p.image,
             'native': {'name': p.native.name, 'uid': str(p.native.uid), 'volume': p.native.volume},
             'control': {'name': p.control.name, 'uid': str(p.control.uid), 'volume': p.control.volume},
             'transport': {'name': p.transport.name, 'uid': str(p.transport.uid)},
             'callback': p.callback, 'serviceAccount': p.service_account}
    with admin.begin() as db:
        db.execute(text('''INSERT INTO runner_bindings.bindings(owner_id,state_id,namespace,namespace_uid,plan)
            VALUES(:owner,:state,'isolated',:ns,CAST(:plan AS jsonb))'''),
            {'owner': owner, 'state': state.state_id, 'ns': rt.namespace_uid, 'plan': json.dumps(value)})
        for kind, name, uid in [('pvc', p.native.name, p.native.uid), ('pvc', p.control.name, p.control.uid),
                                ('pv', p.native.volume, uuid4()), ('pv', p.control.volume, uuid4()),
                                ('secret', p.transport.name, p.transport.uid)]:
            db.execute(text('INSERT INTO runner_bindings.resources VALUES(:owner,:kind,:name,:uid)'),
                       {'owner': owner, 'kind': kind, 'name': name, 'uid': uid})


def fence(admin, owner):
    state = read(admin, owner)
    return {'owner': owner, 'state': state.state_id, 'generation': state.generation, 'revision': state.revision}


@pytest.fixture
def stopped(observer, admin):
    instance, owner, rt, server, deletes, receipts, stop = observer
    publish(admin, owner, rt)
    instance.process(owner)
    assert instance.process(owner).phase == Phase.STOPPED
    return owner, rt, stop


def remaining(admin, owner):
    with admin.begin() as db:
        return {table: db.execute(text('SELECT count(*) FROM '+table+' WHERE owner_id=:owner'), {'owner': owner}).scalar_one()
                for table, _ in tool.TABLES}


def test_report_then_apply_removes_runner_rows_only_and_repeats_safely(stopped, admin):
    owner, rt, stop = stopped
    f = fence(admin, owner)
    with admin.connect() as db:
        value = tool.export(db, f)
        db.rollback()
    assert value['phase'] == 'idle_stopped' and value['lastStop']['podUid'] == str(stop['pod_uid'])
    assert value['rows']['runner_bindings.resources']['count'] == 5 and value['rows']['runner_runtime.registry']['count'] == 1
    assert value['host']['storage'].endswith('/'+f['state'].hex) and value['host']['output'].endswith('/'+owner.hex)
    assert 'session_hash' not in json.dumps(value)
    with admin.begin() as db:
        assert tool.apply(db, f, value) == 'deleted'
    assert not any(remaining(admin, owner).values())
    with admin.begin() as db:
        assert db.execute(text('SELECT count(*) FROM auth.accounts WHERE id=:owner'), {'owner': owner}).scalar_one() == 1
        assert tool.apply(db, f, value) == 'already_applied'


def test_open_stop_is_refused_with_observed_phase(observer, admin):
    instance, owner, rt, server, deletes, receipts, stop = observer
    publish(admin, owner, rt)
    with admin.begin() as db, pytest.raises(tool.Refusal) as caught:
        tool.export(db, fence(admin, owner))
    assert caught.value.code == 'not_idle_stopped' and caught.value.observed['phase'] != 'idle_stopped'
    assert remaining(admin, owner)['runner_runtime.registry'] == 1


def test_unconsumed_entry_is_pending_work(stopped, admin):
    owner, rt, stop = stopped
    token = login(admin, owner)
    state = read(admin, owner)
    with admin.begin() as db:  # A later entry the release did not consume (not reachable through queue_entry).
        db.execute(text('''INSERT INTO runner_termination.entries(owner_id,key,state_id,generation,session_hash)
            VALUES(:owner,:key,:state,:generation,:session)'''),
            {'owner': owner, 'key': uuid4(), 'state': state.state_id, 'generation': state.generation, 'session': token})
    with admin.begin() as db, pytest.raises(tool.Refusal) as caught:
        tool.export(db, fence(admin, owner))
    assert caught.value.code == 'pending_work' and caught.value.observed['unconsumedEntries'] == 1


def test_changed_rows_or_fence_refuse_apply_and_delete_nothing(stopped, admin):
    owner, rt, stop = stopped
    f = fence(admin, owner)
    with admin.connect() as db:
        value = tool.export(db, f)
        db.rollback()
    tampered = json.loads(json.dumps(value))
    tampered['rows']['runner_runtime.outbox']['sha256'] = '0'*64
    with admin.begin() as db, pytest.raises(tool.Refusal) as caught:
        tool.apply(db, f, tampered)
    assert caught.value.code == 'export_changed' and caught.value.observed['fields'] == ['rows']
    with admin.begin() as db, pytest.raises(tool.Refusal) as caught:
        tool.apply(db, {**f, 'revision': f['revision']+1}, value)
    assert caught.value.code == 'fence_changed'
    assert remaining(admin, owner)['runner_runtime.registry'] == 1


def test_missing_binding_resource_is_refused(stopped, admin):
    owner, rt, stop = stopped
    with admin.begin() as db:
        db.execute(text("DELETE FROM runner_bindings.resources WHERE owner_id=:owner AND kind='secret'"), {'owner': owner})
    with admin.begin() as db, pytest.raises(tool.Refusal) as caught:
        tool.export(db, fence(admin, owner))
    assert caught.value.code == 'resources_mismatch' and len(caught.value.observed['resources']) == 4
