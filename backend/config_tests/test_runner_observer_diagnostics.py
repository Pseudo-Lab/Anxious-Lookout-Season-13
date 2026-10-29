"""Read-only stop preflight; disposable DB, fake Kube. Host supply preflight removed (#60)."""
import json
from types import SimpleNamespace
from uuid import uuid4
import pytest
from sqlalchemy import text
from ops.runner_observer_diagnostics import ReadOnlyEngine, diagnose_observer, diagnostic_command
from config_tests.test_runner_registry import schema, admin, read
from config_tests.test_user_runner_http import research_schema, client, identity, headers
from config_tests.test_runner_kube import plan
from config_tests.test_user_runner_runtime import runtime
from config_tests.test_runner_termination_control import binding_schema, login
from config_tests.test_runner_stop_observer import observer
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


def test_cli_invalid_inputs_never_expose_exceptions(monkeypatch,capsys,tmp_path):
    monkeypatch.setenv('OBSERVER_DATABASE_URL','invalid-secret-sentinel')
    args = SimpleNamespace(owner=uuid4(),inputs=str(tmp_path/'missing'))
    assert diagnostic_command(args) == 1
    output = capsys.readouterr()
    assert output.err == '' and 'sentinel' not in output.out
    assert json.loads(output.out) == {'owner':str(args.owner),'stage':'termination-diagnose','result':'input_invalid'}
