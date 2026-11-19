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
    done, failed, busy, gone, foreign = (row() for _ in range(5))
    settled = row(state='idle')
    runner = Runner({done.id: answer(done, 'idle'),
                     failed.id: answer(failed, 'failed', errorCode='codex_policy_refused'),
                     busy.id: answer(busy, 'running'),
                     gone.id: httpx.ConnectError('down'),
                     foreign.id: {**answer(foreign, 'idle'), 'requestId': str(uuid4())}})  # another request
    assert sync_running(Sessions([done, failed, busy, gone, foreign, settled]), runner, uuid4()) == 2
    assert (done.state, done.error_code, done.version, done.tool_token_hash, done.pending_text) == ('idle', None, 4, None, None)
    assert done.native_record['nativeThreadId'] == 'thread-1' and done.native_record['turns'][0]['id'] == 'turn-1'
    assert (failed.state, failed.error_code) == ('failed', 'codex_policy_refused')
    assert busy.state == 'running' and busy.version == 3      # still in flight: never synthesized
    assert gone.state == 'running' and gone.native_record == {'turns': [], 'platformRequests': []}
    assert foreign.state == 'running' and foreign.version == 3
    assert settled.id not in runner.reads                      # only running rows are read


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
