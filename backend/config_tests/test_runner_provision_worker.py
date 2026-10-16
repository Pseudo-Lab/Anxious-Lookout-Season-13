"""Controller worker using real disposable DB and fake Kubernetes, no mounts."""
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text

from app.runner_bindings import Bindings, decode
from app.runner_registry import Registry
from app.runner_kube import KubePods
from runner.lifecycle import Phase, Refused
from ops.runner_provision_worker import Worker
from config_tests.test_runner_registry import schema, admin, owner, t0, enter, read
from config_tests.test_runner_bindings import binding_schema, plan_value
from config_tests.test_runner_kube import Server, response


def test_worker_consumes_published_plan_with_capacity_and_no_repreparation(admin, owner, t0, tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_USER_RUNNER_MAX_PODS', '1')
    monkeypatch.delenv('CODEX_USER_RUNNER_ACCOUNT_ID', raising=False)
    monkeypatch.delenv('CODEX_USER_RUNNER_ALLOWLIST', raising=False)
    first, _ = enter(admin, owner, uuid4(), t0)
    other = uuid4()
    with admin.begin() as db:
        db.execute(text("INSERT INTO auth.accounts(id,github_id,login,role,is_approved) VALUES(:id,'98765','other','editor',true)"), {'id':other})
    second, _ = enter(admin, other, uuid4(), t0)
    ns = uuid4(); servers = {}
    with admin.begin() as db:
        for state in (first,second):
            value = plan_value(state)
            Bindings(db).publish(value, 'isolated', ns, {'native':uuid4(),'control':uuid4()})
            servers[state.owner] = Server(decode(value))
    root = tmp_path/'outputs'; root.mkdir(mode=0o700)
    token = tmp_path/'kube'; token.write_text('k'*40); token.chmod(0o600)
    inputs = {'api':'https://kube.invalid','namespace':'isolated','namespaceUid':str(ns),
              'caFile':None,'kubeTokenFile':str(token)}
    def factory(*args, **kwargs):
        plan = args[4][0]
        def handler(request):
            if request.url.path == '/api/v1/namespaces/isolated':
                return response(200, {'metadata':{'uid':str(ns)}})
            return servers[plan.identity.owner](request)
        return KubePods(*args, **kwargs, _transport=httpx.MockTransport(handler))
    def no_preparation(*args, **kwargs): pytest.fail('existing binding must not be prepared again')
    worker = Worker(admin, inputs, root, preparer=no_preparation, port_factory=factory)
    assert set(worker.pending()) == {owner,other}
    worker.process(owner)
    assert read(admin, owner).pod_uid is not None
    worker.process(other)
    assert read(admin, other).phase == Phase.WAITING
    assert servers[other].pod is None
    assert sum(method == 'POST' for method,_ in servers[owner].requests) == 1
    with admin.begin() as db:
        db.execute(text('UPDATE auth.accounts SET is_approved=false WHERE id=:owner'), {'owner':other})
    assert worker.pending() == []
    with pytest.raises(Refused): worker.process(other)
