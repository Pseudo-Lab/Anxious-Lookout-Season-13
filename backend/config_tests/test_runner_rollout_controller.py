"""#101 controller wiring without a database: reconcile order, the stale-plan checks before creation,
the rollout cluster's evidence checks and the operator `rollout` command."""
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from ops import runner_controller_service as module
from ops.runner_controller_service import CheckedPods, RolloutCluster, Service, Stuck, log_line, rollout_command, main
from ops.runner_replacement import Target, View
from runner.lifecycle import Phase, Refused

OLD = 'registry.invalid/runner@sha256:'+'a'*64
NEW = 'registry.invalid/runner@sha256:'+'b'*64


class Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class Engine:
    """begin()/connect() yield a connection whose every query answers the binding image."""
    def __init__(self, image):
        self.image = image

    @contextmanager
    def begin(self):
        yield SimpleNamespace(execute=lambda *args, **kwargs: Result(self.image))

    connect = begin


class FakeRollout:
    def __init__(self, calls, *, fail=False, halt_on=None):
        self.calls, self.fail, self.halt_on = calls, fail, halt_on
        self.settings = SimpleNamespace(fingerprint={'image': NEW, 'runnerFlags': []})

    def run(self):
        self.calls.append('rollout')
        if self.fail:
            raise Stuck('rollout_db')
        return [(uuid4(), 'rollout', 'converged'), (uuid4(), 'rollout', 'waiting_turn')]

    def before_create(self, owner):
        self.calls.append(('before_create', owner))

    def ensure_result(self, owner, ok, image_is_target):
        self.calls.append(('ensure_result', ok, image_is_target))
        return 'ensure_failed' if self.halt_on == ok else None


def service_with(monkeypatch, rollout, ensure):
    calls = rollout.calls if rollout is not None else []
    owner, waiting = uuid4(), uuid4()
    service = Service.__new__(Service)
    service.rollout = rollout
    service.controller_engine = Engine(NEW)
    service.candidates = lambda idle: [owner]
    service.step = lambda owner_, idle: calls.append('step') or 'deleting'
    service.waiting = lambda: [waiting]
    service.ensure = lambda owner_: calls.append('ensure') or ensure(owner_)
    monkeypatch.setattr(module, 'load_limits', lambda: SimpleNamespace(idle_seconds=300))
    monkeypatch.setattr(module, 'allowed', lambda owner_: True)
    return service, owner, waiting


def test_rollout_runs_between_the_stop_steps_and_creation(monkeypatch):
    calls = []
    service, owner, waiting = service_with(monkeypatch, FakeRollout(calls), lambda owner: None)
    result = service.reconcile()
    assert calls[:3] == ['step', 'rollout', 'ensure'] and calls[3] == ('ensure_result', True, True)
    assert [line[2] for line in result if line[1] == 'rollout'] == ['waiting_turn']  # converged is not logged
    assert (waiting, 'ensured', None) in result


def test_a_failing_rollout_pass_never_blocks_stops_or_creation(monkeypatch):
    calls = []
    service, owner, waiting = service_with(monkeypatch, FakeRollout(calls, fail=True), lambda owner: None)
    result = service.reconcile()
    assert (None, 'rollout', 'stuck:rollout_db') in result and (waiting, 'ensured', None) in result
    assert log_line(None, 'rollout', 'stuck:rollout_db') == {'owner': None, 'stage': 'controller', 'result': 'rollout', 'reason': 'stuck:rollout_db'}


def test_failed_creation_is_counted_and_a_moved_binding_is_not(monkeypatch):
    calls = []
    def ensure(owner):
        raise OSError('boom')
    service, owner, waiting = service_with(monkeypatch, FakeRollout(calls, halt_on=False), ensure)
    result = service.reconcile()
    assert ('ensure_result', False, True) in calls and (waiting, 'rollout_halted', 'ensure_failed') in result
    calls.clear()
    def moved(owner):
        raise Stuck('binding_moved')
    service.ensure = lambda owner_: moved(owner_)
    service.reconcile()
    assert ('ensure_result', True, True) in calls


def test_without_a_target_reconcile_is_unchanged(monkeypatch):
    service, owner, waiting = service_with(monkeypatch, None, lambda owner: None)
    assert service.reconcile() == [(owner, 'deleting', None), (waiting, 'ensured', None)]


@pytest.mark.parametrize('bound_image,expected', [(NEW, 'created'), (OLD, 'refused')])
def test_create_rereads_the_binding_inside_the_locked_ensure(bound_image, expected):
    created = []
    pods = SimpleNamespace(lookup=lambda identity: None, create_once=lambda identity: created.append(identity) or 'pod')
    checked = CheckedPods(pods, Engine(NEW), SimpleNamespace(image=bound_image))
    identity = SimpleNamespace(owner=uuid4())
    if expected == 'created':
        assert checked.create_once(identity) == 'pod' and created == [identity]
    else:
        with pytest.raises(Refused):
            checked.create_once(identity)
        assert created == []


def test_ensure_refuses_a_plan_resolved_before_a_move_before_any_reservation(monkeypatch):
    owner = uuid4()
    reserved = []
    runtime = SimpleNamespace(namespace=lambda: None, plan=SimpleNamespace(image=OLD), pods=object())
    @contextmanager
    def bound(owner_):
        yield runtime
    service = Service.__new__(Service)
    service.rollout = None
    service.controller_pool = SimpleNamespace(bound=bound)
    service.controller_engine = Engine(NEW)  # The binding moved after Pool.bound resolved OLD.
    service.now = lambda: None
    monkeypatch.setattr(module, 'allowed', lambda owner_: True)
    monkeypatch.setattr(module, 'Registry', lambda db: SimpleNamespace(_current=lambda owner_: SimpleNamespace()))
    monkeypatch.setattr(module, 'Controller', lambda *args, **kwargs: reserved.append(args) or SimpleNamespace(ensure=lambda fence: None))
    with pytest.raises(Stuck) as stuck:
        service.ensure(owner)
    assert stuck.value.reason == 'binding_moved' and reserved == []


def cluster_with(answer):
    owner, state, pod = uuid4(), uuid4(), uuid4()
    calls = []
    class Transport:
        def __init__(self, address, token, owner_, state_):
            assert (owner_, state_) == (owner, str(state))
        def call(self, method, path, body):
            calls.append((method, path, body))
            return answer(owner, state, pod)
    runtime = SimpleNamespace(namespace=lambda: None, token='t'*40)
    @contextmanager
    def bound(owner_):
        yield runtime
    service = SimpleNamespace(observer_pool=SimpleNamespace(bound=bound), runner=Transport,
                              address=lambda runtime_, obj: 'http://10.42.0.9:8080')
    view = View(owner, state, Phase.UNREACHABLE, pod, OLD)
    return RolloutCluster(service), view, {'metadata': {'uid': str(pod)}}, calls


def test_cluster_idle_and_health_require_this_pods_evidence():
    cluster, view, obj, calls = cluster_with(lambda o, s, p: {'ownerId': str(o), 'stateId': str(s), 'podUid': str(p), 'idle': True})
    assert cluster.idle(view, obj, 60) is True and calls == [('POST', '/lifecycle/idle', {'idleSeconds': 60})]
    cluster, view, obj, calls = cluster_with(lambda o, s, p: {'ownerId': str(o), 'stateId': str(s), 'podUid': str(uuid4()), 'idle': True})
    with pytest.raises(Refused):
        cluster.idle(view, obj, 60)
    cluster, view, obj, calls = cluster_with(lambda o, s, p: {'podUid': str(uuid4()), 'available': True, 'reason': None})
    with pytest.raises(Refused):
        cluster.health(view, obj)
    cluster, view, obj, calls = cluster_with(lambda o, s, p: {'ownerId': str(o), 'state': 'interrupting'})
    session, request = uuid4(), uuid4()
    cluster.interrupt(view, obj, session, request)
    assert calls == [('POST', '/sessions/'+str(session)+'/interrupt', {'requestId': str(request)})]


class CommandStore:
    def __init__(self, state):
        self.target = None if state is None else Target(uuid4(), {'image': NEW, 'runnerFlags': []}, state)

    def current(self):
        return self.target

    def set_state(self, target, state, reason=None, owner=None):
        self.target = Target(target.target_id, target.fingerprint, state, reason, owner)
        return self.target

    def counts(self, target_id):
        return {'moved': 2, 'blocked:runner_failed': 1}


@pytest.mark.parametrize('state,action,rc,after', [
    ('canary', 'status', 0, 'canary'), ('canary', 'halt', 0, 'halted'), ('rolling', 'halt', 0, 'halted'),
    ('halted', 'resume', 0, 'rolling'), ('canary', 'resume', 0, 'rolling'), ('complete', 'halt', 3, 'complete'),
    ('rolling', 'resume', 3, 'rolling'), (None, 'status', 3, None)])
def test_rollout_command(state, action, rc, after):
    store = CommandStore(state)
    service = SimpleNamespace(rollout=SimpleNamespace(store=store))
    code, report = rollout_command(service, action)
    assert code == rc and (store.target is None or store.target.state == after)
    if rc == 0:
        assert report['state'] == after and report['owners'] == {'moved': 2, 'blocked:runner_failed': 1}
        assert report['reason'] == ('operator' if action == 'halt' else None)


def test_rollout_command_line_prints_and_returns_its_code(capsys):
    store = CommandStore('rolling')
    assert main(['rollout', 'halt'], factory=lambda: (SimpleNamespace(rollout=SimpleNamespace(store=store)),)) == 0
    assert '"state": "halted"' in capsys.readouterr().out
    def broken():
        raise RuntimeError('secret')
    assert main(['rollout', 'status'], factory=broken) == 1
    assert 'secret' not in capsys.readouterr().out
