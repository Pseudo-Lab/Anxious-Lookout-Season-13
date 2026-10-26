"""#64 budget enforcement without a model: a fake app-server session drives harness.run()."""
import json
import queue

import pytest

import harness


class FakeSession:
    script = []

    def __init__(self, config, home, cwd):
        self.events, self.sent = queue.Queue(), []
        for event in self.script:
            self.events.put(event)

    def rpc(self, method, params, timeout=60):
        if method == 'thread/start':
            return {'thread': {'id': 'thread-1'}}
        if method == 'turn/start' and getattr(self, 'fail_turn', False):
            raise RuntimeError('lost reply')
        return {}

    def send(self, message):
        self.sent.append(message)
        if message.get('method') == 'turn/interrupt':
            self.events.put(usage(4_200_000, 1000))  # The interrupted turn's final usage still arrives.
            self.events.put({'method': 'turn/completed', 'params': {'turn': {'status': 'interrupted'}}})

    def close(self):
        pass


def usage(inputs, outputs):
    return {'method': 'thread/tokenUsage/updated', 'params': {'tokenUsage': {'total': {
        'inputTokens': inputs, 'cachedInputTokens': 0, 'outputTokens': outputs, 'reasoningOutputTokens': 0}}}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, 'PILOT', tmp_path)
    monkeypatch.setattr(harness, 'RESULTS', tmp_path/'results')
    monkeypatch.setattr(harness, 'BUDGET', tmp_path/'results'/'budget.json')
    monkeypatch.setattr(harness, 'Session', FakeSession)
    monkeypatch.setattr(harness, 'access', lambda: {'type': 'chatgptAuthTokens'})
    def workspace(config, task, run):
        path = tmp_path/run
        path.mkdir()
        return path, path
    monkeypatch.setattr(harness, 'prepare_workspace', workspace)
    monkeypatch.setattr(harness.pilot_tasks, 'grade', lambda task, root: {'passed': False, 'testsChanged': False, 'summary': ''})
    class Broker:
        commands, exit_code = [], 0
        def __init__(self, path, *args, **kwargs): self.path = path
        def activate(self, turn): return 'lease'
        def execute(self, request):
            Broker.commands.append(request['arguments']['command'])
            return {'exitCode': Broker.exit_code}
        def revoke(self): pass
        def close(self): pass
    Broker.commands, Broker.exit_code = [], 0
    import runner.shell_broker
    monkeypatch.setattr(runner.shell_broker, 'ShellBroker', Broker)
    return tmp_path


def budget(env):
    return json.loads((env/'results'/'budget.json').read_text())


def test_crossing_the_share_interrupts_and_counts_the_final_usage(env, capsys):
    FakeSession.script = [usage(4_100_000, 500)]
    harness.run('fix-failing-test', 'B', 900, 1)
    result = json.loads(capsys.readouterr().out)
    assert result['turnStatus'] == 'budget_stop' and result['tokens']['inputTokens'] == 4_200_000
    assert budget(env)['input'] == 4_200_000
    with pytest.raises(SystemExit):
        harness.run('slow-suite', 'B', 900, 1)  # The share is spent: no further paid turn.


def test_missing_usage_after_a_started_turn_blocks_later_runs(env, capsys):
    FakeSession.script = [{'method': 'harness/exited'}]
    harness.run('fix-failing-test', 'A', 900, 1)
    assert budget(env)['unknown'] and budget(env)['input'] == 0
    with pytest.raises(SystemExit, match='unconfirmed'):
        harness.run('slow-suite', 'A', 900, 1)


def test_a_lost_turn_start_reply_is_still_unknown(env, capsys, monkeypatch):
    FakeSession.script = []
    monkeypatch.setattr(FakeSession, 'fail_turn', True, raising=False)
    harness.run('fix-failing-test', 'A', 900, 1)
    assert json.loads(capsys.readouterr().out)['turnStatus'].startswith('harness_error')
    assert budget(env)['unknown']


def test_each_configuration_gets_half_of_the_full_run_cap():
    assert harness.SHARE == {'input': 4_000_000, 'output': 30_000}
    assert sum(harness.SHARE.values()) * 2 == sum(harness.LIMITS.values())


def test_usage_then_exit_without_completion_is_a_counted_lower_bound_and_blocks(env, capsys):
    FakeSession.script = [usage(10, 2), {'method': 'harness/exited'}]
    harness.run('fix-failing-test', 'A', 900, 1)
    state = budget(env)
    assert state['input'] == 10 and state['unknown'] and state['inflight'] is None
    with pytest.raises(SystemExit, match='unconfirmed'):
        harness.run('slow-suite', 'A', 900, 1)


def test_settlement_survives_a_grading_failure(env, monkeypatch):
    FakeSession.script = [usage(10, 2), {'method': 'turn/completed', 'params': {'turn': {'status': 'completed'}}}]
    def broken(task, root): raise OSError('disk')
    monkeypatch.setattr(harness.pilot_tasks, 'grade', broken)
    harness.run('fix-failing-test', 'B', 900, 1)
    state = budget(env)
    assert (state['input'], state['output'], state['unknown'], state['inflight']) == (10, 2, [], None)
    assert state['errors'] == ['fix-failing-test-B-01']  # A grading failure is an error slot and counts.
    assert state['slots']['fix-failing-test-B-01']['status'] == 'harness_error: grade OSError'


def test_three_grading_failures_stop_the_gate(env, capsys, monkeypatch):
    def broken(task, root): raise OSError('disk')
    monkeypatch.setattr(harness.pilot_tasks, 'grade', broken)
    for slot in (1, 2, 3):
        FakeSession.script = [usage(10, 2), {'method': 'turn/completed', 'params': {'turn': {'status': 'completed'}}}]
        harness.run('fix-failing-test', 'B', 900, slot)
    verdict, reason = harness.gate({'A': ledger(), 'B': budget(env)})
    assert verdict == 'stop' and 'harness error' in reason


def test_a_result_write_failure_is_recorded_and_exits_non_zero(env, monkeypatch):
    FakeSession.script = [usage(10, 2), {'method': 'turn/completed', 'params': {'turn': {'status': 'completed'}}}]
    real = harness.Path.write_text
    def full(self, *args, **kwargs):
        if self.suffix == '.json' and self.parent.name == 'results' and self.name != 'budget.json':
            raise OSError('no space')
        return real(self, *args, **kwargs)
    monkeypatch.setattr(harness.Path, 'write_text', full)
    with pytest.raises(OSError):
        harness.run('slow-suite', 'B', 900, 1)
    state = budget(env)
    assert state['input'] == 10 and state['slots']['slow-suite-B-01']['status'] == 'harness_error: result OSError'
    assert state['errors'] == ['slow-suite-B-01']


def test_a_claim_left_started_stops_the_gate():
    abandoned = ledger(slots={'fix-failing-test-A-01': {'run': 'r', 'status': 'started'}})
    verdict, reason = harness.gate({'A': abandoned, 'B': ledger()})
    assert verdict == 'stop' and 'claimed but never finished' in reason


def test_a_crash_after_the_paid_call_leaves_an_unsettled_marker(env, monkeypatch):
    # Simulate the process dying inside the turn: the durable marker written before turn/start remains.
    FakeSession.script = []
    def die(self, method, params, timeout=60):
        if method == 'turn/start': raise SystemExit('killed')
        return {'thread': {'id': 't'}} if method == 'thread/start' else {}
    monkeypatch.setattr(FakeSession, 'rpc', die)
    monkeypatch.setattr(harness, 'save', lambda state: harness.BUDGET.write_text(json.dumps(state)) if state.get('inflight') else None)
    with pytest.raises(SystemExit):
        harness.run('fix-failing-test', 'B', 900, 1)
    assert budget(env)['inflight']
    with pytest.raises(SystemExit, match='never settled'):
        harness.run('slow-suite', 'B', 900, 1)


def test_completion_without_any_usage_report_is_unconfirmed(env, capsys):
    FakeSession.script = [{'method': 'turn/completed', 'params': {'turn': {'status': 'completed'}}}]
    harness.run('fix-failing-test', 'B', 900, 1)
    assert budget(env)['unknown'] and json.loads(capsys.readouterr().out)['settled'] is False
    with pytest.raises(SystemExit, match='unconfirmed'):
        harness.run('slow-suite', 'B', 900, 1)


DONE = [{'method': 'thread/tokenUsage/updated', 'params': {'tokenUsage': {'total': {
    'inputTokens': 10, 'cachedInputTokens': 0, 'outputTokens': 2, 'reasoningOutputTokens': 0}}}},
    {'method': 'turn/completed', 'params': {'turn': {'status': 'completed'}}}]


def ledger(**fields):
    return {'input': 0, 'cachedInput': 0, 'output': 0, 'runs': [], 'unknown': [], 'inflight': None, **fields}


def test_a_slot_runs_once_and_is_never_rerun(env, capsys):
    FakeSession.script = DONE
    harness.run('fix-failing-test', 'B', 900, 3)
    assert budget(env)['slots']['fix-failing-test-B-03']['status'] == 'completed'
    with pytest.raises(SystemExit, match='already ran'):
        harness.run('fix-failing-test', 'B', 900, 3)


def test_a_preparation_failure_is_an_error_slot_without_a_turn(env, capsys, monkeypatch):
    def broken(config, task, run): raise PermissionError('residue')
    monkeypatch.setattr(harness, 'prepare_workspace', broken)
    started = []
    monkeypatch.setattr(harness, 'Session', lambda *args: started.append(args))
    harness.run('slow-suite', 'B', 900, 1)
    result = json.loads(capsys.readouterr().out)
    state = budget(env)
    assert result['turnStatus'] == 'harness_error: PermissionError' and result['summary'].startswith('not graded')
    assert started == [] and state['errors'] == ['slow-suite-B-01'] and state['input'] == 0 and not state['unknown']


def test_configuration_a_empties_the_workspace_through_the_executor_first(env, capsys):
    import runner.shell_broker
    FakeSession.script = DONE
    harness.run('fix-failing-test', 'A', 900, 1)
    assert runner.shell_broker.ShellBroker.commands == ['find . -mindepth 1 -delete']
    runner.shell_broker.ShellBroker.exit_code = 1
    harness.run('fix-failing-test', 'A', 900, 2)
    assert json.loads(capsys.readouterr().out.splitlines()[-1])['turnStatus'] == 'harness_error: RuntimeError'
    assert budget(env)['errors'] == ['fix-failing-test-A-02']


def test_gate_names_slots_in_alternating_order_and_skips_claimed_ones():
    assert harness.ORDER[:4] == [('fix-failing-test', 'A', 1), ('fix-failing-test', 'B', 1),
                                 ('slow-suite', 'A', 1), ('slow-suite', 'B', 1)]
    assert len(harness.ORDER) == 60 == len(set(harness.ORDER))
    claimed = {'slots': {'fix-failing-test-A-01': {}}}
    assert harness.gate({'A': ledger(**claimed), 'B': ledger()}) == ('next', ('fix-failing-test', 'B', 1))
    every = {harness.slot_key(*slot): {} for slot in harness.ORDER}
    assert harness.gate({'A': ledger(slots=every), 'B': ledger()}) == ('done', None)


@pytest.mark.parametrize('a, b, reason', [
    (ledger(unknown=['x']), ledger(), 'A: '),
    (ledger(), ledger(inflight='y'), 'B: '),
    (ledger(halted='z'), ledger(), 'A: '),
    (ledger(errors=['e1', 'e2']), ledger(errors=['e3']), '3 harness errors across A and B'),
    (ledger(input=4_000_000), ledger(), 'A: '),
    (ledger(output=29_999), ledger(output=29_999), ''),
])
def test_gate_stops_both_configurations(a, b, reason):
    verdict, value = harness.gate({'A': a, 'B': b})
    if reason:
        assert verdict == 'stop' and value.startswith(reason)
    else:
        assert verdict == 'next'  # 59,998 output is still under the 60,000 total and each 30,000 share.


def test_gate_stops_on_the_total_even_below_each_share(monkeypatch):
    monkeypatch.setattr(harness, 'LIMITS', {'input': 100, 'output': 100})
    monkeypatch.setattr(harness, 'SHARE', {'input': 60, 'output': 60})
    assert harness.gate({'A': ledger(input=50), 'B': ledger(input=50)}) == ('stop', 'total budget exhausted')


def test_settled_usage_is_on_disk_before_grading_even_if_grading_never_returns(env, monkeypatch):
    FakeSession.script = [usage(10, 2), {'method': 'turn/completed', 'params': {'turn': {'status': 'completed'}}}]
    def interrupted(task, root): raise KeyboardInterrupt()
    monkeypatch.setattr(harness.pilot_tasks, 'grade', interrupted)
    with pytest.raises(KeyboardInterrupt):
        harness.run('fix-failing-test', 'B', 900, 1)
    state = budget(env)
    assert (state['input'], state['output'], state['runs'], state['inflight']) == (10, 2, [state['runs'][0]], None)
    assert state['slots']['fix-failing-test-B-01']['status'] == 'started'  # The gate stops on it until reconciled.
    assert harness.gate({'A': ledger(), 'B': state})[0] == 'stop'
