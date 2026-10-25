"""Controller worker using real disposable DB and fake Kubernetes, no mounts."""
from pathlib import Path
from uuid import UUID, uuid4

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


def test_host_worker_leaves_bound_owners_to_the_controller_which_creates_pods_with_capacity(admin, owner, t0, tmp_path, monkeypatch):
    # #61: the host worker only prepares storage and publishes; runner-controller creates the Pod.
    monkeypatch.setenv('CODEX_USER_RUNNERS_ENABLE', 'true')
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
    assert worker.pending() == []  # Published bindings belong to runner-controller.
    assert worker.process(owner) == 'published' and servers[owner].requests == []
    from contextlib import contextmanager
    from types import SimpleNamespace
    from ops.runner_controller_service import Service
    class Pool:
        @contextmanager
        def bound(self, candidate):
            with admin.begin() as db:
                plan = Bindings(db).resolve(Registry(db).read(candidate), 'isolated', ns)
            pods = factory('https://kube.invalid', 'isolated', 'k'*40, None, [plan])
            try: yield SimpleNamespace(namespace=lambda: None, pods=pods)
            finally: pods.close()
    service = Service(admin, admin, Pool(), Pool(), node=None)
    assert set(service.waiting()) == {owner, other}
    for flag in (None, 'false'):
        with monkeypatch.context() as patch:
            if flag is None:
                patch.delenv('CODEX_USER_RUNNERS_ENABLE', raising=False)
            else:
                patch.setenv('CODEX_USER_RUNNERS_ENABLE', flag)
            with pytest.raises(Refused): service.ensure(owner)
            assert servers[owner].requests == [] and read(admin, owner).pod_uid is None
    service.ensure(owner)
    assert read(admin, owner).pod_uid is not None
    service.ensure(other)
    assert read(admin, other).phase == Phase.WAITING and servers[other].pod is None
    assert sum(method == 'POST' for method,_ in servers[owner].requests) == 1
    with admin.begin() as db:
        db.execute(text('UPDATE auth.accounts SET is_approved=false WHERE id=:owner'), {'owner':other})
    assert other not in service.waiting()
    with pytest.raises(Refused): service.ensure(other)


@pytest.mark.parametrize('selector', ['CODEX_USER_RUNNER_ALLOWLIST', 'CODEX_USER_RUNNER_ACCOUNT_ID'])
def test_pending_filters_excluded_prefix_before_limit(admin, t0, tmp_path, monkeypatch, selector):
    monkeypatch.setenv('CODEX_USER_RUNNERS_ENABLE', 'true')
    monkeypatch.delenv('CODEX_USER_RUNNER_ACCOUNT_ID', raising=False)
    monkeypatch.delenv('CODEX_USER_RUNNER_ALLOWLIST', raising=False)
    owners = [UUID(int=index) for index in range(1, 12)]
    for index, candidate in enumerate(owners):
        with admin.begin() as db:
            db.execute(text("INSERT INTO auth.accounts(id,github_id,login,role,is_approved) VALUES(:id,:github,:login,'editor',true)"),
                       {'id':candidate, 'github':str(80000+index), 'login':'batch-'+str(index)})
        enter(admin, candidate, uuid4(), t0)
    monkeypatch.setenv(selector, str(owners[-1]))
    root = tmp_path/'outputs'; root.mkdir(mode=0o700)
    worker = Worker(admin, {}, root)
    for _ in range(3):
        assert worker.pending(limit=10) == [owners[-1]]


@pytest.mark.parametrize('flag', [None, 'false'])
def test_disabled_worker_never_prepares_unbound_intent(admin, owner, t0, tmp_path, monkeypatch, flag):
    if flag is None:
        monkeypatch.delenv('CODEX_USER_RUNNERS_ENABLE', raising=False)
    else:
        monkeypatch.setenv('CODEX_USER_RUNNERS_ENABLE', flag)
    state, _ = enter(admin, owner, uuid4(), t0)
    root = tmp_path/'outputs'; root.mkdir(mode=0o700)
    def forbidden(*args, **kwargs): pytest.fail('disabled worker reached external resource creation')
    worker = Worker(admin, {}, root, preparer=forbidden, allocator=forbidden, port_factory=forbidden)
    assert worker.pending() == []
    with pytest.raises(Refused): worker.process(owner)
    assert read(admin, owner) == state
    assert list(root.iterdir()) == []
