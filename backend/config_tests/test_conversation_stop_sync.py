"""#114 ①: before a stop fences the runner, running conversations take the runner's actual result.
Unit level: fake sessions and runner; the database path is covered by the controller DB tests."""
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.conversations import RunnerHistoryInvalid, apply_runner_read, checked_runner_read, sync_running


def row(**changes):
    value = dict(id=uuid4(), request_id=uuid4(), state='running', error_code=None, version=3, updated_at=None,
                 tool_token_hash='h'*64, pending_text='question', native_record={'turns': [], 'platformRequests': []})
    value.update(changes)
    return SimpleNamespace(**value)


def answer(conversation, state, **changes):
    value = {'requestId': str(conversation.request_id), 'threadId': 'thread-1', 'turnId': 'turn-1', 'state': state,
             'record': {'turns': [{'id': 'turn-1', 'items': [{'type': 'userMessage'}, {'type': 'agentMessage'}]}]},
             'errorCode': None}
    value.update(changes)
    return value


class Sessions:
    def __init__(self, rows):
        self.rows = {item.id: item for item in rows}
        self.queue = []
    @contextmanager
    def begin(self):
        sessions = self
        class Db:
            def scalars(self, statement):
                ids = [item.id for item in sessions.rows.values() if item.state == 'running']
                sessions.queue = list(ids)
                return SimpleNamespace(all=lambda: ids)
            def scalar(self, statement):
                return sessions.rows[sessions.queue.pop(0)]
        yield Db()


class Runner:
    def __init__(self, answers): self.answers, self.reads = answers, []
    def read(self, identity):
        self.reads.append(identity)
        value = self.answers[identity]
        if isinstance(value, Exception):
            raise value
        return value


def test_finished_turns_take_the_runner_result_and_unknown_ones_stay_running():
    done, failed, busy, foreign, last = (row() for _ in range(5))
    settled = row(state='idle')
    runner = Runner({done.id: answer(done, 'idle'),
                     failed.id: answer(failed, 'failed', errorCode='codex_policy_refused'),
                     busy.id: answer(busy, 'running'),
                     foreign.id: {**answer(foreign, 'idle'), 'requestId': str(uuid4())},  # another request
                     last.id: answer(last, 'idle')})
    assert sync_running(Sessions([done, failed, busy, foreign, last, settled]), runner, uuid4()) == 3
    assert (done.state, done.error_code, done.version, done.tool_token_hash, done.pending_text) == ('idle', None, 4, None, None)
    assert done.native_record['nativeThreadId'] == 'thread-1' and done.native_record['turns'][0]['id'] == 'turn-1'
    assert (failed.state, failed.error_code) == ('failed', 'codex_policy_refused')
    assert busy.state == 'running' and busy.version == 3      # still in flight: never synthesized
    assert foreign.state == 'running' and foreign.version == 3  # this row only; the next one is still read
    assert last.state == 'idle'
    assert settled.id not in runner.reads                      # only running rows are read


@pytest.mark.parametrize('failure', [httpx.ConnectError('down'), httpx.ReadTimeout('slow'), OSError('kube')])
def test_a_runner_that_does_not_answer_ends_the_sync(failure):
    """Logout waits for this: after the first transport failure the rest would wait the same way."""
    gone, other = row(), row()
    runner = Runner({gone.id: failure, other.id: answer(other, 'idle')})
    assert sync_running(Sessions([gone, other]), runner, uuid4()) == 0
    assert runner.reads == [gone.id] and gone.state == other.state == 'running'


def test_no_read_starts_after_the_budget_and_every_call_runs_inside_it():
    from app import runner_http_budget
    rows = [row() for _ in range(3)]
    clock = SimpleNamespace(now=100.0)
    seen = []
    class Slow(Runner):
        def read(self, identity):
            seen.append(runner_http_budget.active())   # Runner.call bounds itself by the outer budget
            clock.now += 2.0                           # each read takes 2 of the 3 seconds
            return super().read(identity)
    runner = Slow({item.id: answer(item, 'idle') for item in rows})
    assert sync_running(Sessions(rows), runner, uuid4(), seconds=3, clock=lambda: clock.now) == 2
    assert runner.reads == [rows[0].id, rows[1].id] and rows[2].state == 'running'
    assert seen == [True, True] and not runner_http_budget.active()  # released after the sync


def test_unsafe_runner_error_codes_are_not_copied():
    conversation = row()
    apply_runner_read(conversation, checked_runner_read(conversation, answer(conversation, 'failed', errorCode='x'*80)))
    assert (conversation.state, conversation.error_code) == ('failed', 'codex_failed')


@pytest.mark.parametrize('change', [{'state': 'stopping'}, {'threadId': ''}, {'threadId': 5}])
def test_reads_outside_the_contract_keep_accepted_uncertainty(change):
    conversation = row()
    with pytest.raises(ValueError):
        checked_runner_read(conversation, {**answer(conversation, 'idle'), **change})
    pinned = row(native_record={'nativeThreadId': 'thread-0', 'turns': []})
    with pytest.raises(ValueError):
        checked_runner_read(pinned, answer(pinned, 'idle'))  # another native thread


def test_missing_history_is_reported_not_applied():
    conversation = row()
    with pytest.raises(RunnerHistoryInvalid):
        apply_runner_read(conversation, answer(conversation, 'idle', record=None))
    assert conversation.state == 'running'
    # The pre-stop sync swallows it: the controller closes what is left after the receipt.
    assert sync_running(Sessions([conversation]), Runner({conversation.id: answer(conversation, 'idle', record=[])}), uuid4()) == 0
    assert conversation.state == 'running'


def test_runner_calls_use_the_bounded_transport_only_inside_an_outer_budget(monkeypatch):
    import time
    from app import runner_http_budget
    from app.conversations import Runner as Transport
    bounded = []
    monkeypatch.setattr(Transport, '_call', lambda self, method, path, body, flag: bounded.append(flag) or {})
    client = Transport('http://10.42.0.9:8080', 't'*40, uuid4())
    client.call('GET', '/sessions/x')
    marker = runner_http_budget.begin(time.monotonic() + 3, time.monotonic)
    try:
        client.call('GET', '/sessions/x')
    finally:
        runner_http_budget.finish(marker)
    client.call('GET', '/sessions/x')
    assert bounded == [False, True, False]
