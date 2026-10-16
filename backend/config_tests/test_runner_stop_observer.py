"""Real stop/entry DB and fake Kube/native/host evidence orchestration."""
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text
from app.runner_termination_control import stop_for, queue_entry
from ops.runner_stop_observer import Observer
from runner.lifecycle import Phase, Refused
from config_tests.test_runner_registry import schema, admin, read
from config_tests.test_user_runner_http import research_schema, client, identity, headers, create_conversation
from config_tests.test_runner_kube import plan
from config_tests.test_user_runner_runtime import runtime
from config_tests.test_runner_termination_control import login


@pytest.fixture
def observer(runtime,admin,tmp_path,monkeypatch):
    owner, auth, rt, server, deletes, calls = runtime
    rt.ensure(owner)
    server.pod['spec']['nodeName'] = 'node'
    rt.stop(owner)
    with admin.begin() as db: stop = stop_for(db,read(admin,owner))
    class Pool:
        @contextmanager
        def bound(self, _): yield rt
    receipts = []
    class Transport:
        def __init__(self,*args): pass
        def call(self,method,path,body):
            assert method == 'POST' and path == '/lifecycle/drain'
            result = {**body,'version':1,'clean':True}
            receipts.append(result)
            return result
    host = SimpleNamespace(node_name='node',node_uid=uuid4(),boot_id=uuid4(),node_matches=lambda *args:None)
    host.calls = 0
    def verify(uid, *, get_node, get_pod):
        assert get_pod() is None
        host.calls += 1
        return {'podUid':str(uid),'nodeName':'node','nodeUid':str(host.node_uid),'bootId':str(host.boot_id),
                'verifiedAt':datetime.now(timezone.utc).isoformat()}
    host.verify = verify
    instance = Observer(admin,Pool(),host,tmp_path,runner=Transport)
    instance.node = lambda runtime:{}
    instance.retained_receipt = lambda *args:receipts[-1]
    return instance,owner,rt,server,deletes,receipts,stop


def test_observer_drains_deletes_then_proves_and_reenters_once(observer,admin):
    instance,owner,rt,server,deletes,receipts,stop = observer
    token = login(admin,owner)
    with admin.begin() as db: queue_entry(db,owner,uuid4(),token)
    first = instance.process(owner)
    assert first.phase == Phase.STOPPING and first.pod_uid == stop['pod_uid']
    assert deletes == [str(stop['pod_uid'])] and len(receipts) == 1
    assert instance.host.calls == 0
    second = instance.process(owner)
    assert second.phase == Phase.PREPARING and second.generation == stop['generation']+1
    assert second.pod_uid is None and instance.host.calls == 1
    assert len(deletes) == 1
    with admin.begin() as db:
        assert db.execute(text('SELECT count(*) FROM runner_termination.stops WHERE completed_at IS NOT NULL')).scalar_one() == 1


def test_absent_before_anchor_never_becomes_node_proof(observer,admin):
    instance,owner,rt,server,deletes,receipts,stop = observer
    server.pod = None
    with pytest.raises(Refused): instance.process(owner)
    assert read(admin,owner).pod_uid == stop['pod_uid'] and instance.host.calls == 0


def test_host_failure_preserves_stopping_slot_and_entry(observer,admin):
    instance,owner,rt,server,deletes,receipts,stop = observer
    instance.process(owner)
    def refused(*args,**kwargs): raise Refused()
    instance.host.verify = refused
    with pytest.raises(Refused): instance.process(owner)
    assert read(admin,owner).phase == Phase.STOPPING and read(admin,owner).pod_uid == stop['pod_uid']
    with admin.begin() as db: assert stop_for(db,read(admin,owner))['completed_at'] is None


def test_lost_delete_response_is_retryable_without_releasing_slot(observer,admin,monkeypatch):
    instance,owner,rt,server,deletes,receipts,stop = observer
    original = rt.pods._request
    def lost(method,*args,**kwargs):
        result = original(method,*args,**kwargs)
        if method == 'DELETE': raise TimeoutError()
        return result
    monkeypatch.setattr(rt.pods,'_request',lost)
    with pytest.raises(TimeoutError): instance.process(owner)
    assert read(admin,owner).phase == Phase.STOPPING and instance.host.calls == 0
    monkeypatch.setattr(rt.pods,'_request',original)
    result = instance.process(owner)
    assert result.phase == Phase.STOPPED and len(deletes) == 1
