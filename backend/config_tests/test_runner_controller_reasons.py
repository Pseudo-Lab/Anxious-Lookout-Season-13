"""#114 controller reason codes and idle classification, without a database or Kubernetes."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from ops import runner_controller_service as module
from ops.runner_controller_service import Service, Stuck, call, log_line, reason_of, stage
from runner.lifecycle import Phase, Refused

SECRET = 'postgresql://user:SECRET@db/x Bearer SECRETTOKEN'


def status_error(status):
    return httpx.HTTPStatusError(SECRET, request=httpx.Request('POST', 'http://runner/x'),
                                 response=httpx.Response(status, json={'error': SECRET}))


class Raising:
    def __init__(self, failure): self.failure = failure
    def call(self, method, path, body): raise self.failure


@pytest.mark.parametrize('name,failure,reason', [
    ('drain', status_error(409), 'drain_conflict'),
    ('drain', status_error(503), 'drain_status_503'),
    ('health', status_error(409), 'health_status_409'),
    ('idle', status_error(401), 'idle_status_401'),
    ('drain', httpx.ConnectError(SECRET), 'drain_unreachable'),
    ('drain', httpx.ReadTimeout(SECRET), 'drain_unreachable'),
    ('health', ValueError('Runner ownership mismatch '+SECRET), 'health_invalid')])
def test_runner_call_failures_become_fixed_reasons(name, failure, reason):
    with pytest.raises(Stuck) as caught:
        call(Raising(failure), name, 'POST', '/x', {})
    assert caught.value.reason == reason and 'SECRET' not in str(caught.value)
    assert isinstance(caught.value, Refused)  # existing callers that expect Refused keep working


def test_stage_names_the_failure_and_keeps_an_inner_reason():
    with pytest.raises(Stuck) as caught:
        with stage('receipt_rejected'):
            raise Refused(SECRET)
    assert caught.value.reason == 'receipt_rejected' and 'SECRET' not in str(caught.value)
    with pytest.raises(Stuck) as caught:
        with stage('receipt_rejected'):
            raise Stuck('drain_conflict')
    assert caught.value.reason == 'drain_conflict'
    assert reason_of(RuntimeError(SECRET)) == 'error:RuntimeError'


def test_reconcile_reports_result_and_reason_without_exception_text(monkeypatch):
    owners = [uuid4() for _ in range(6)]
    outcomes = {owners[0]: Stuck('drain_conflict'), owners[1]: RuntimeError(SECRET), owners[2]: 'runner_not_idle',
                owners[3]: 'stale_running_conversations', owners[4]: 'deleting'}
    service = Service.__new__(Service)
    service.candidates = lambda idle: owners[:5]
    service.waiting = lambda: [owners[5]]
    def step(owner, idle):
        value = outcomes[owner]
        if isinstance(value, Exception):
            raise value
        return value
    def ensure(owner): raise OSError(SECRET)
    service.step, service.ensure = step, ensure
    monkeypatch.setattr(module, 'load_limits', lambda: SimpleNamespace(idle_seconds=300))
    monkeypatch.setattr(module, 'allowed', lambda owner: True)
    result = service.reconcile()
    assert result == [(owners[0], 'reconciliation_required', 'drain_conflict'),
                      (owners[1], 'reconciliation_required', 'error:RuntimeError'),
                      (owners[2], 'running', 'runner_not_idle'),
                      (owners[3], 'running', 'stale_running_conversations'),
                      (owners[4], 'deleting', None),
                      (owners[5], 'reconciliation_required', 'ensure:error:OSError')]
    lines = [json.dumps(log_line(*item)) for item in result]
    assert all('SECRET' not in line for line in lines)
    assert json.loads(lines[4]) == {'owner': str(owners[4]), 'stage': 'controller', 'result': 'deleting'}
    assert json.loads(lines[0])['reason'] == 'drain_conflict'


# idle(): busy, stale and stuck runners --------------------------------------------------------------
@pytest.fixture
def idle(monkeypatch):
    owner, state_id, pod_uid = uuid4(), uuid4(), uuid4()
    clock = SimpleNamespace(now=datetime(2026, 11, 19, 12, tzinfo=timezone.utc))
    world = SimpleNamespace(running=0, answer=None, stops=[], calls=[])
    class Engine:
        @contextmanager
        def begin(self): yield object()
    class Transport:
        def __init__(self, address, token, owner_, state): pass
        def call(self, method, path, body):
            world.calls.append((method, path, body))
            return {'ownerId': str(owner), 'stateId': str(state_id), 'podUid': str(pod_uid), 'idle': world.answer}
    service = Service(Engine(), Engine(), None, None, None, runner=Transport, now=lambda: clock.now)
    monkeypatch.setattr(Service, 'address', lambda self, runtime, obj: 'http://10.42.0.9:8080')
    monkeypatch.setattr(module, 'running_conversations', lambda db, owner_: world.running)
    monkeypatch.setattr(module, 'request_idle_stop', lambda db, owner_, **kw: world.stops.append((owner_, kw)))
    state = SimpleNamespace(phase=Phase.UNREACHABLE, active_requests=0, ambiguous=True, state_id=state_id,
                            pod_uid=pod_uid, last_activity=clock.now - timedelta(hours=1))
    run = lambda: service.idle(SimpleNamespace(token='t'*40), owner, state, {}, 300)
    return SimpleNamespace(run=run, world=world, clock=clock, owner=owner, service=service)


def test_busy_runner_with_a_running_conversation_is_never_an_error(idle):
    idle.world.answer, idle.world.running = False, 1
    for _ in range(3):
        assert idle.run() == 'runner_not_idle'
        idle.clock.now += timedelta(hours=1)
    assert idle.world.stops == []


def test_runner_refusing_idle_with_nothing_running_is_stuck_after_twice_the_idle_period(idle):
    idle.world.answer = False
    assert idle.run() == 'runner_not_idle'          # a turn may have ended moments ago
    idle.clock.now += timedelta(seconds=599)
    assert idle.run() == 'runner_not_idle'
    idle.clock.now += timedelta(seconds=1)
    with pytest.raises(Stuck) as caught:
        idle.run()
    assert caught.value.reason == 'idle_refused' and idle.world.stops == []


def test_a_running_conversation_or_an_idle_answer_resets_the_stuck_clock(idle):
    idle.world.answer = False
    idle.run()
    idle.clock.now += timedelta(seconds=599)
    idle.world.running = 1
    assert idle.run() == 'runner_not_idle'          # busy again: the earlier refusal is forgotten
    idle.world.running = 0
    idle.clock.now += timedelta(seconds=1)
    assert idle.run() == 'runner_not_idle'
    assert idle.owner in idle.service.not_idle


def test_idle_runner_with_a_stale_running_conversation_waits_for_the_api(idle):
    idle.world.answer, idle.world.running = True, 1
    assert idle.run() == 'stale_running_conversations' and idle.world.stops == []


def test_idle_runner_with_nothing_running_records_the_stop(idle):
    idle.world.answer = True
    assert idle.run() is None
    assert idle.world.stops == [(idle.owner, {'idle_seconds': 300, 'native_idle': True})]
    assert idle.world.calls == [('POST', '/lifecycle/idle', {'idleSeconds': 300})]


def test_idle_evidence_for_another_pod_is_stuck_not_a_stop(idle):
    idle.world.answer = 'yes'
    with pytest.raises(Stuck) as caught:
        idle.run()
    assert caught.value.reason == 'idle_evidence_mismatch' and idle.world.stops == []


def test_registry_conditions_skip_the_runner_call(idle):
    idle.world.answer = True
    state = SimpleNamespace(phase=Phase.READY, active_requests=1, ambiguous=False, state_id=uuid4(), pod_uid=uuid4(),
                            last_activity=idle.clock.now - timedelta(hours=1))
    assert idle.service.idle(SimpleNamespace(token='t'*40), idle.owner, state, {}, 300) == 'running'
    assert idle.world.calls == []
