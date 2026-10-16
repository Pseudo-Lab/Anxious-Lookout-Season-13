"""Real disposable DB and source parsing, fake Kubernetes exec transport."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text
from ops.runner_host_supply import Supply, SOURCE
from ops.access_snapshot import selected_file
from runner.auth import write_private
from runner.lifecycle import Refused
from config_tests.test_runner_registry import schema, admin, read
from config_tests.test_user_runner_http import research_schema, client, identity, headers
from config_tests.test_runner_termination_control import binding_schema
from config_tests.test_runner_kube import plan
from config_tests.test_user_runner_runtime import runtime
from config_tests.test_service_access import jwt, ACCOUNT


@pytest.fixture
def supply(runtime,admin,tmp_path,monkeypatch):
    owner,auth,rt,server,deletes,calls = runtime
    rt.ensure(owner)
    monkeypatch.setenv('CODEX_USER_RUNNERS_ENABLE','true')
    monkeypatch.delenv('CODEX_USER_RUNNER_ACCOUNT_ID',raising=False)
    monkeypatch.delenv('CODEX_USER_RUNNER_ALLOWLIST',raising=False)
    class Pool:
        @contextmanager
        def bound(self,_): yield rt
    source = tmp_path/'auth.json'
    write_private(source,{'tokens':{'access_token':jwt(),'account_id':ACCOUNT,
                                  'refresh_token':'NEVER_EXPORT_REFRESH','id_token':'NEVER_EXPORT_ID'}})
    health = {'ownerId':str(owner),'stateId':str(rt.plan.identity.state_id),
              'podUid':str(read(admin,owner).pod_uid),'available':False,'reason':'not_configured'}
    class Transport:
        def __init__(self,*args): pass
        def health(self): return dict(health)
    seen = []
    def source_reader(path,**kwargs):
        assert path == SOURCE
        seen.append('read')
        return selected_file(source,**kwargs)
    def run(argv,**kwargs):
        seen.append((argv,kwargs))
        return SimpleNamespace(returncode=0,stdout=b'{"accepted":true}\n')
    attempts = tmp_path/'attempts'; attempts.mkdir(mode=0o700)
    worker = Supply(admin,Pool(),account=ACCOUNT,source_uid=os.geteuid(),kubeconfig='/private/kubeconfig',attempt_root=attempts,
                    kubectl='/usr/bin/true',source_reader=source_reader,run=run,runner=Transport)
    return worker,owner,rt,server,source,health,seen


def test_supply_is_access_only_uid_bound_and_retry_snapshot_stable(supply,admin):
    worker,owner,rt,server,source,health,seen = supply
    before,state = source.read_bytes(),read(admin,owner)
    assert worker.process(owner) == 'supplied'
    with pytest.raises(FileExistsError): worker.process(owner)
    exported = []
    for argv,kwargs in (item for item in seen if isinstance(item,tuple)):
        assert argv[-2:] == ['--expected-pod-uid',str(state.pod_uid)]
        assert '-i' in argv and '-t' not in argv
        payload = json.loads(kwargs['input']); exported.append(payload)
        assert payload['ownerId'] == str(owner) and payload['stateId'] == str(state.state_id)
        assert b'NEVER_EXPORT' not in kwargs['input']
        assert 'accessToken' in payload and 'refresh_token' not in payload
    assert len(exported) == 1
    records = list(worker.attempt_root.glob('*.json'))
    assert len(records) == 1 and json.loads(records[0].read_bytes())['result'] == 'accepted'
    assert exported[0]['accessToken'] not in records[0].read_text()
    assert source.read_bytes() == before and read(admin,owner) == state
    seen.clear(); health.update(available=True,reason=None)
    assert worker.process(owner) == 'already_available' and seen == []


@pytest.mark.parametrize('damage',['stop','revoked','disabled','busy','uid','health-uid','account','source-owner','source-mode','expired'])
def test_supply_fails_closed_without_exec(supply,admin,monkeypatch,damage):
    worker,owner,rt,server,source,health,seen = supply
    if damage == 'stop': rt.stop(owner)
    if damage == 'revoked':
        with admin.begin() as db: db.execute(text('UPDATE auth.accounts SET is_approved=false WHERE id=:owner'),{'owner':owner})
    if damage == 'disabled': monkeypatch.setenv('CODEX_USER_RUNNERS_ENABLE','false')
    if damage == 'busy':
        with admin.begin() as db: db.execute(text("INSERT INTO research.conversations(owner_id,title,state) VALUES(:owner,'busy','running')"),{'owner':owner})
    if damage == 'uid': server.pod['metadata']['uid'] = str(uuid4())
    if damage == 'health-uid': health['podUid'] = str(uuid4())
    if damage == 'account': worker.account = 'wrong-account'
    if damage == 'source-owner': worker.source_uid = 10001
    if damage == 'source-mode': source.chmod(0o644)
    if damage == 'expired':
        value = json.loads(source.read_bytes()); value['tokens']['access_token'] = jwt(expiry=1)
        source.write_text(json.dumps(value))
    with pytest.raises(Exception): worker.process(owner)
    assert not any(isinstance(item,tuple) for item in seen)


def test_supply_unknown_exec_outcome_never_retries_in_invocation(supply):
    worker,owner,rt,server,source,health,seen = supply
    def timeout(*args,**kwargs):
        seen.append('exec')
        raise TimeoutError()
    worker.run = timeout
    with pytest.raises(TimeoutError): worker.process(owner)
    assert seen == ['read','exec']
    with pytest.raises(FileExistsError): worker.process(owner)
    assert seen == ['read','exec','read']
    value = json.loads(source.read_bytes()); value['tokens']['access_token'] = jwt()
    source.write_text(json.dumps(value))
    with pytest.raises(TimeoutError): worker.process(owner)
    assert seen[-2:] == ['read','exec']


def test_receiver_refuses_replaced_pod_before_reading_stdin(monkeypatch,capsys):
    from runner.access_supply import main
    import sys
    monkeypatch.setenv('RUNNER_POD_UID',str(uuid4()))
    monkeypatch.setattr(sys,'argv',['access_supply','/control/access.sock','--expected-pod-uid',str(uuid4())])
    class Unreadable:
        @property
        def buffer(self): pytest.fail('stale target consumed token stdin')
    monkeypatch.setattr(sys,'stdin',Unreadable())
    assert main() == 1
    assert json.loads(capsys.readouterr().out)['receipt'] == 'unknown'


def test_source_rejects_fifo_without_waiting(tmp_path):
    from ops.access_snapshot import SourceFailure
    path = tmp_path/'auth.json'
    os.mkfifo(path,mode=0o600)
    with pytest.raises(SourceFailure): selected_file(path)


def test_observer_db_role_can_supply_without_changing_state(supply,admin):
    from contextlib import contextmanager
    worker,owner,rt,server,source,health,seen = supply
    with admin.begin() as db:
        if not db.execute(text("SELECT 1 FROM pg_roles WHERE rolname='anxious_runner_observer'")).first():
            db.connection.driver_connection.execute(Path('/source/db/016_runner_observer_grants.sql').read_text())
    class ObserverEngine:
        @contextmanager
        def begin(self):
            with admin.begin() as db:
                db.execute(text('SET LOCAL ROLE anxious_runner_observer'))
                yield db
    worker.engine = ObserverEngine()
    before = read(admin,owner)
    assert worker.process(owner) == 'supplied'
    assert read(admin,owner) == before


@pytest.mark.parametrize('damage', ['deleted','replaced','terminating','spec','reference','namespace','lookup-error','registry'])
def test_post_receipt_target_drift_preserves_unknown_permit_without_redelivery(supply,admin,monkeypatch,damage):
    from copy import deepcopy
    from dataclasses import replace
    worker,owner,rt,server,source,health,seen = supply
    original_pod, original_objects = deepcopy(server.pod), deepcopy(server.objects)
    original_namespace, original_state = rt.namespace_uid, rt.state
    original_run = worker.run
    before = read(admin,owner)
    def accepted_then_changed(*args,**kwargs):
        result = original_run(*args,**kwargs)
        if damage == 'deleted': server.pod = None
        if damage == 'replaced': server.pod['metadata']['uid'] = str(uuid4())
        if damage == 'terminating': server.pod['metadata']['deletionTimestamp'] = '2026-10-16T09:00:00Z'
        if damage == 'spec': server.pod['spec']['containers'][0]['image'] = 'unexpected-image'
        if damage == 'reference': server.objects[rt.plan.native.name]['metadata']['uid'] = str(uuid4())
        if damage == 'namespace': rt.namespace_uid = uuid4()
        if damage == 'lookup-error': server.mode = 'timeout'
        if damage == 'registry':
            monkeypatch.setattr(rt,'state',lambda db,owner:replace(original_state(db,owner),revision=before.revision+1))
        return result
    worker.run = accepted_then_changed
    with pytest.raises(Refused): worker.process(owner)
    records = list(worker.attempt_root.glob('*.json'))
    assert len(records) == 1
    assert json.loads(records[0].read_bytes())['result'] == 'unknown'
    assert read(admin,owner) == before
    assert sum(isinstance(item,tuple) for item in seen) == 1
    # Repairing the target does not authorize automatic reuse of an uncertain permit.
    server.pod,server.objects,server.mode = original_pod,original_objects,'normal'
    rt.namespace_uid = original_namespace
    monkeypatch.setattr(rt,'state',original_state)
    with pytest.raises(FileExistsError): worker.process(owner)
    assert sum(isinstance(item,tuple) for item in seen) == 1
    assert json.loads(records[0].read_bytes())['result'] == 'unknown'
