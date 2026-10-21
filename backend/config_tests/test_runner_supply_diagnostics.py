"""Read-only preflight and bounded failure logging; disposable DB, fake Kube."""
import json
from types import SimpleNamespace
from uuid import uuid4
import pytest
from sqlalchemy import text
from ops.runner_supply_diagnostics import ReadOnlyEngine, diagnose_observer, emit_failure, clear_failure
from config_tests.test_runner_host_supply import supply, schema, admin, read, research_schema, client, identity, headers, binding_schema, plan, runtime
from config_tests.test_runner_stop_observer import observer


def test_supply_preflight_has_no_side_effects(supply,admin):
    worker,owner,rt,server,source,health,seen = supply
    before,content = read(admin,owner),source.read_bytes()
    assert worker.diagnose(owner) == 'ready_for_supply'
    assert seen == ['read'] and list(worker.attempt_root.iterdir()) == []
    assert before == read(admin,owner) and content == source.read_bytes()
    worker.process(owner)
    records = {p:p.read_bytes() for p in worker.attempt_root.iterdir()}
    seen.clear()
    assert worker.diagnose(owner) == 'attempt_accepted'
    assert seen == ['read'] and records == {p:p.read_bytes() for p in worker.attempt_root.iterdir()}
    health.update(available=True,reason=None); seen.clear()
    assert worker.diagnose(owner) == 'already_available' and seen == []


@pytest.mark.parametrize('damage,expected',[
    ('busy','running_conversation'),('revoked','owner_ineligible'),('disabled','excluded'),
    ('uid','pod_uid_mismatch'),('health','health_identity_mismatch'),('account','source_account_mismatch'),
    ('source','source_unreadable'),('stop','stop_pending'),('namespace','namespace_unavailable'),
    ('exception','health_unavailable'),('missing','pod_unavailable')])
def test_supply_fixed_reasons(supply,admin,monkeypatch,damage,expected):
    worker,owner,rt,server,source,health,seen = supply
    if damage == 'busy':
        with admin.begin() as db: db.execute(text("INSERT INTO research.conversations(owner_id,title,state) VALUES(:owner,'busy','running')"),{'owner':owner})
    if damage == 'revoked':
        with admin.begin() as db: db.execute(text('UPDATE auth.accounts SET is_approved=false WHERE id=:owner'),{'owner':owner})
    if damage == 'disabled': monkeypatch.setenv('CODEX_USER_RUNNERS_ENABLE','false')
    if damage == 'uid': server.pod['metadata']['uid'] = str(uuid4())
    if damage == 'health': health['podUid'] = str(uuid4())
    if damage == 'account': worker.account = 'different'
    if damage == 'source': source.chmod(0o644)
    if damage == 'stop': rt.stop(owner)
    if damage == 'namespace': rt.namespace_uid = uuid4()
    if damage == 'missing': server.pod = None
    if damage == 'exception':
        def fail(*args): raise ValueError('SECRET-CREDENTIAL-SENTINEL')
        worker.runner = fail
    before = read(admin,owner)
    assert worker.diagnose(owner) == expected
    assert before == read(admin,owner)
    assert not any(isinstance(item,tuple) for item in seen)
    assert list(worker.attempt_root.iterdir()) == []


def test_unknown_attempt_never_authorizes_retry(supply):
    worker,owner,rt,server,source,health,seen = supply
    def timeout(*args,**kwargs): raise TimeoutError('SECRET')
    worker.run = timeout
    with pytest.raises(TimeoutError): worker.process(owner)
    records = {p:p.read_bytes() for p in worker.attempt_root.iterdir()}
    assert worker.diagnose(owner) == 'attempt_unknown'
    assert records == {p:p.read_bytes() for p in worker.attempt_root.iterdir()}


def test_database_readonly_is_enforced(admin):
    with pytest.raises(Exception):
        with ReadOnlyEngine(admin).begin() as db:
            assert db.execute(text('SHOW transaction_read_only')).scalar_one() == 'on'
            db.execute(text("UPDATE auth.accounts SET is_approved=false"))


@pytest.mark.parametrize('damage,expected',[(None,'ready_for_observation'),('busy','running_conversation'),('uid','pod_uid_mismatch'),('node','node_mismatch'),('missing','node_mismatch')])
def test_stop_diagnosis_preserves_intent_and_never_drains(observer,admin,damage,expected):
    from app.runner_termination_control import stop_for
    instance,owner,rt,server,deletes,receipts,stop = observer
    if damage == 'busy':
        with admin.begin() as db: db.execute(text("INSERT INTO research.conversations(owner_id,title,state) VALUES(:owner,'busy','running')"),{'owner':owner})
    if damage == 'uid': server.pod['metadata']['uid'] = str(uuid4())
    if damage == 'node': server.pod['spec']['nodeName'] = 'other-node'
    if damage == 'missing': server.pod = None
    before = read(admin,owner)
    assert diagnose_observer(instance,owner) == expected
    assert read(admin,owner) == before and deletes == [] and receipts == [] and instance.host.calls == 0
    with admin.begin() as db: assert dict(stop_for(db,before)) == dict(stop)


def test_failure_log_dedup_and_recovery(tmp_path):
    tmp_path.chmod(0o700); owner = uuid4()
    assert emit_failure(tmp_path,owner,'supply_refused','running_conversation',now=1000)
    assert not emit_failure(tmp_path,owner,'supply_refused','running_conversation',now=1899)
    assert emit_failure(tmp_path,owner,'supply_refused','running_conversation',now=1900)
    assert emit_failure(tmp_path,owner,'supply_refused','stop_pending',now=1901)
    clear_failure(tmp_path,owner)
    assert emit_failure(tmp_path,owner,'supply_refused','stop_pending',now=1902)
    path = next(tmp_path.iterdir()); path.write_text('corrupt')
    assert emit_failure(tmp_path,owner,'supply_refused','stop_pending',now=1903)


def test_cli_invalid_inputs_never_expose_exceptions(monkeypatch,capsys,tmp_path):
    from ops.runner_host_supply import diagnostic_command
    monkeypatch.setenv('OBSERVER_DATABASE_URL','invalid-secret-sentinel')
    args = SimpleNamespace(owner=uuid4(),inputs=str(tmp_path/'missing'),observer_inputs=str(tmp_path/'observer'))
    assert diagnostic_command(args) == 1
    output = capsys.readouterr()
    assert output.err == '' and 'sentinel' not in output.out
    assert [json.loads(line)['result'] for line in output.out.splitlines()] == ['input_invalid','input_invalid']


def test_batch_suppression_never_skips_work_or_success_reset(admin,tmp_path,monkeypatch,capsys):
    import os
    import sys
    import app.runner_pool
    import ops.runner_host_supply as module
    from runner.auth import write_private
    owner = uuid4(); calls = []
    tmp_path.chmod(0o700)
    config = tmp_path/'inputs.json'; cursor = tmp_path/'cursor.json'
    write_private(config,dict(pool={},account='synthetic',sourceUid=0,kubeconfig='unused',kubectl='unused',attemptRoot='unused'))
    worker = SimpleNamespace(diagnose=lambda owner:'running_conversation')
    def refused(owner):
        calls.append(owner)
        raise ValueError('SECRET-SENTINEL')
    worker.process = refused
    monkeypatch.setenv('OBSERVER_DATABASE_URL',os.environ['ADMIN_DATABASE_URL'])
    monkeypatch.setenv('CODEX_USER_RUNNERS_ENABLE','true')
    monkeypatch.delenv('CODEX_USER_RUNNER_ALLOWLIST',raising=False)
    monkeypatch.delenv('CODEX_USER_RUNNER_ACCOUNT_ID',raising=False)
    monkeypatch.setattr(app.runner_pool,'Pool',lambda *args:None)
    monkeypatch.setattr(module,'Supply',lambda *args,**kwargs:worker)
    monkeypatch.setattr(sys,'argv',['supply','--inputs',str(config),'--owner',str(owner),'--cursor-file',str(cursor)])
    for index in range(2):
        with pytest.raises(SystemExit) as caught: module.main()
        assert caught.value.code == 1
        output = capsys.readouterr()
        assert 'SECRET' not in output.out+output.err
        assert bool(output.out) is (index == 0)
        assert json.loads(cursor.read_bytes()) == {'after':str(owner)}
    assert calls == [owner,owner]
    worker.process = lambda owner:'already_available'
    module.main(); capsys.readouterr()
    worker.process = refused
    with pytest.raises(SystemExit): module.main()
    assert json.loads(capsys.readouterr().out)['reason'] == 'running_conversation'


def test_cli_diagnosis_uses_readonly_pool_and_does_not_process(supply,tmp_path,monkeypatch,capsys):
    import os
    import app.runner_pool
    import ops.runner_host_supply as module
    from runner.auth import write_private
    worker,owner,rt,server,source,health,seen = supply
    config = tmp_path/'inputs.json'
    write_private(config,dict(pool={},account='synthetic',sourceUid=0,kubeconfig='unused',kubectl='unused',attemptRoot='unused'))
    def pool(engine,config):
        assert isinstance(engine,ReadOnlyEngine)
        with engine.begin() as db:
            assert db.execute(text('SHOW transaction_read_only')).scalar_one() == 'on'
        return worker.pool
    monkeypatch.setenv('OBSERVER_DATABASE_URL',os.environ['ADMIN_DATABASE_URL'])
    monkeypatch.setattr(app.runner_pool,'Pool',pool)
    monkeypatch.setattr(module,'Supply',lambda *args,**kwargs:worker)
    args = SimpleNamespace(owner=owner,inputs=str(config),observer_inputs=None)
    assert module.diagnostic_command(args) == 0
    assert json.loads(capsys.readouterr().out) == {'owner':str(owner),'stage':'host-supply-diagnose','result':'ready_for_supply'}
    assert seen == ['read'] and list(worker.attempt_root.iterdir()) == []
