"""Narrow legacy zero-admission recovery; no live host/provider evidence."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text
from app.runner_registry import Registry
from ops.runner_legacy_reconcile import LegacyRecovery, empty_native_audit, journal_evidence
from ops.runner_stop_observer import Observer
from runner.lifecycle import Fence, Phase, Refused
from config_tests.test_runner_registry import schema, admin, t0, read
from config_tests.test_runner_termination_control import binding_schema, research_schema
from config_tests.test_service_access import setup as service_setup, ACCOUNT


@pytest.fixture
def legacy(admin,tmp_path,t0):
    auth,home,envelope = service_setup(tmp_path)
    auth.install(envelope); auth.attach(home); auth.close()
    native,control = home.parent,auth.control
    (control/'operator-account').write_text(ACCOUNT); (control/'operator-account').chmod(0o600)
    (native/'broker.json').write_text('{}'); (native/'broker.json').chmod(0o600)
    (native/'runner.lock').touch(mode=0o600)
    for directory in (native,control):
        for path in [*directory.rglob('*'),directory]: os.chown(path,10001,10001)
    from uuid import UUID
    owner,state_id,pod = UUID(auth.owner),UUID(auth.state_id),uuid4()
    with admin.begin() as db:
        db.execute(text("INSERT INTO auth.accounts(id,github_id,login,role,is_approved) VALUES(:id,'12345','legacy','editor',true)"),{'id':owner})
        db.execute(text("""INSERT INTO runner_runtime.registry
            (owner_id,state_id,phase,generation,revision,pod_uid,active_requests,ambiguous,last_activity)
            VALUES(:owner,:state,'runner_failed',1,8,:pod,0,true,:now)"""),
            {'owner':owner,'state':state_id,'pod':pod,'now':t0})
    state = read(admin,owner)
    runtime = SimpleNamespace(namespace=lambda:None,state=lambda db,owner:Registry(db)._current(owner))
    class Pool:
        @contextmanager
        def bound(self,_): yield runtime
    host = SimpleNamespace(node_name='node',node_uid=uuid4(),boot_id=uuid4())
    host.verify = lambda uid,**kw:{'podUid':str(uid),'nodeName':'node','nodeUid':str(host.node_uid),
                                   'bootId':str(host.boot_id),'verifiedAt':datetime.now(timezone.utc).isoformat()}
    observer = Observer(admin,Pool(),host,tmp_path)
    observer.node = lambda _:{}
    observer.pod = lambda _:None
    recovery = LegacyRecovery(observer)
    recovery.roots = lambda *args:{'native':native,'control':control}
    journal = tmp_path/'journal.jsonl'
    journal.write_text(json.dumps({'_SYSTEMD_UNIT':'k3s.service','_BOOT_ID':host.boot_id.hex,
                                  '_HOSTNAME':'simple','MESSAGE':'pod sync uid '+str(pod)})+'\n')
    journal.chmod(0o600)
    return recovery,Fence.of(state),journal,native,control


def test_report_apply_repeat_and_next_generation_preserve_ledger_and_broker(legacy,admin):
    recovery,fence,journal,native,control = legacy
    before = {str(path):path.read_bytes() for root in (native,control) for path in root.rglob('*') if path.is_file()}
    report = recovery.run(fence,journal,'simple',ACCOUNT)
    assert read(admin,fence.owner).phase == Phase.FAILED and read(admin,fence.owner).ambiguous
    applied = recovery.run(fence,journal,'simple',ACCOUNT,approved_report=report)
    assert applied['reconciled']
    state = read(admin,fence.owner)
    assert state.phase == Phase.STOPPED and state.pod_uid is None and not state.ambiguous
    assert state.generation == fence.generation and state.state_id == fence.state_id
    assert recovery.run(fence,journal,'simple',ACCOUNT,approved_report=report)['alreadyReconciled']
    with admin.begin() as db:
        newer,_ = Registry(db).enter(fence.owner,uuid4(),lambda:datetime.now(timezone.utc))
    assert newer.generation == fence.generation+1
    assert recovery.run(fence,journal,'simple',ACCOUNT,approved_report=report)['alreadyReconciled']
    assert read(admin,fence.owner) == newer
    after = {str(path):path.read_bytes() for root in (native,control) for path in root.rglob('*') if path.is_file()}
    assert before == after


@pytest.mark.parametrize('damage',['broker','journal-uid','journal-boot','journal-host','pending','receipt-change','proof','admission','stale','auth-file','marker-version'])
def test_legacy_uncertainty_or_drift_preserves_failed_state(legacy,admin,damage):
    recovery,fence,journal,native,control = legacy
    report = recovery.run(fence,journal,'simple',ACCOUNT)
    if damage == 'broker': (native/'broker.json').write_text('{"uncertain":{"state":"running"}}')
    if damage == 'auth-file': (native/'codex/auth.json').write_text('{}')
    if damage == 'marker-version':
        marker = json.loads((native/'state-binding.json').read_text())
        marker['version'] = True
        (native/'state-binding.json').write_text(json.dumps(marker))
    if damage == 'stale':
        with admin.begin() as db:
            db.execute(text('UPDATE runner_runtime.registry SET revision=revision+1 WHERE owner_id=:owner'), {'owner':fence.owner})
    if damage == 'admission':
        with admin.begin() as db:
            db.execute(text('''INSERT INTO runner_runtime.admissions
                (owner_id,request_id,state_id,generation,pod_uid,admitted_at,completed_at)
                VALUES(:owner,:request,:state,:generation,:pod,now(),now())'''),
                {'owner':fence.owner,'request':uuid4(),'state':fence.state_id,'generation':fence.generation,'pod':fence.pod_uid})
    if damage.startswith('journal-'):
        value = json.loads(journal.read_text())
        value[{'journal-uid':'MESSAGE','journal-boot':'_BOOT_ID','journal-host':'_HOSTNAME'}[damage]] = 'wrong'
        journal.write_text(json.dumps(value))
    if damage == 'pending':
        with admin.begin() as db:
            db.execute(text("INSERT INTO research.conversations(owner_id,title,state) VALUES(:owner,'pending','running')"),{'owner':fence.owner})
    if damage == 'receipt-change': report['revision'] += 1
    if damage == 'proof':
        def fail(*args,**kwargs): raise Refused()
        recovery.observer.host.verify = fail
    with pytest.raises((Refused,ValueError)):
        recovery.run(fence,journal,'simple',ACCOUNT,approved_report=report)
    assert read(admin,fence.owner).phase == Phase.FAILED and read(admin,fence.owner).pod_uid == fence.pod_uid
    with admin.begin() as db:
        assert db.execute(text('SELECT count(*) FROM runner_termination.stops')).scalar_one() == 0


def test_nonzero_service_admissions_refuse_empty_broker_recovery(tmp_path):
    auth,home,envelope = service_setup(tmp_path)
    auth.install(envelope); auth.attach(home); auth.consume(str(uuid4())); auth.close()
    (auth.control/'operator-account').write_text(ACCOUNT)
    (home.parent/'broker.json').write_text('{}')
    from uuid import UUID
    with pytest.raises((Refused,ValueError)):
        empty_native_audit(home.parent,auth.control,UUID(auth.owner),UUID(auth.state_id),ACCOUNT,read=lambda path:path.read_bytes())
