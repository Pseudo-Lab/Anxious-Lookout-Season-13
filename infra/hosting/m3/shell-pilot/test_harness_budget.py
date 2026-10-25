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
            self.events.put(usage(2_000_000, 1000))  # The interrupted turn's final usage still arrives.
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
        def __init__(self, path, *args, **kwargs): self.path = path
        def activate(self, turn): return 'lease'
        def close(self): pass
    import runner.shell_broker
    monkeypatch.setattr(runner.shell_broker, 'ShellBroker', Broker)
    return tmp_path


def budget(env):
    return json.loads((env/'results'/'budget.json').read_text())


def test_crossing_the_share_interrupts_and_counts_the_final_usage(env, capsys):
    FakeSession.script = [usage(1_300_000, 500)]
    harness.run('fix-failing-test', 'B', 900)
    result = json.loads(capsys.readouterr().out)
    assert result['turnStatus'] == 'budget_stop' and result['tokens']['inputTokens'] == 2_000_000
    assert budget(env)['input'] == 2_000_000
    with pytest.raises(SystemExit):
        harness.run('slow-suite', 'B', 900)  # The share is spent: no further paid turn.


def test_missing_usage_after_a_started_turn_blocks_later_runs(env, capsys):
    FakeSession.script = [{'method': 'harness/exited'}]
    harness.run('fix-failing-test', 'A', 900)
    assert budget(env)['unknown'] and budget(env)['input'] == 0
    with pytest.raises(SystemExit, match='unknown token usage'):
        harness.run('slow-suite', 'A', 900)


def test_a_lost_turn_start_reply_is_still_unknown(env, capsys, monkeypatch):
    FakeSession.script = []
    monkeypatch.setattr(FakeSession, 'fail_turn', True, raising=False)
    harness.run('fix-failing-test', 'A', 900)
    assert json.loads(capsys.readouterr().out)['turnStatus'].startswith('harness_error')
    assert budget(env)['unknown']


def test_each_configuration_gets_half_of_the_pilot_cap():
    assert harness.SHARE == {'input': 1_200_000, 'output': 50_000}
    assert sum(harness.SHARE.values()) * 2 == sum(harness.LIMITS.values())
