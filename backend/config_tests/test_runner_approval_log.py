"""#68 P-A: the runner's approval records (design P-A r3 §1). Pure: no DB, no Codex binary, no network.

These tests prove what the runner's handler writes about each approval request and that the records never change a
decision. That pinned Codex 0.160.1 sends these requests for every command, and in which order relative to the
sandboxed process, is VM evidence (exec observer), not shown here.
"""
import ast
import hashlib
import json
import logging
import queue
import threading
import time
import uuid
from pathlib import Path

import pytest

from runner import app as runner_app
from runner import native_policy as policy
from runner.app import Native, approval_hash, approval_refused, approval_rpc_hash

WORK = '/state/workspace'
APP = Path(__file__).resolve().parents[1] / 'runner' / 'app.py'
KEYS = {
    'dispatched': {'v', 'seq', 'stage', 'kind', 'rpc', 'item', 'thread', 'turn', 'inTurn', 'pending', 'arrived',
                   'arrivedM', 'm', 't'},
    'decided': {'v', 'seq', 'stage', 'decision', 'reason', 'refused', 'm', 't'},
    'sent': {'v', 'seq', 'stage', 'ok', 'm0', 'm1', 't'},
}
SUMMARY_KEYS = {'v', 'thread', 'turn', 'dispatched', 'decided', 'sent', 'arrivedOnly', 'seqFirst', 'seqLast',
                'outside', 't'}
REASONS = {'accepted', 'not_eligible', 'completed_turn', 'authorize_refused', 'no_turn', 'error'}


def command(**changes):
    value = {'kind': 'command', 'threadId': 'thread', 'turnId': 'turn', 'itemId': 'item-1', 'startedAtMs': 1,
             'approvalId': None, 'environmentId': None, 'reason': None, 'networkApprovalContext': None,
             'command': 'python -m pytest -q', 'cwd': WORK, 'commandActions': [],
             'proposedExecpolicyAmendment': ['python', '-m', 'pytest', '-q'],
             'proposedNetworkPolicyAmendments': None, 'availableDecisions': ['accept', 'decline']}
    value.update(changes)
    return {'id': 'request-' + str(value['itemId']), 'method': 'item/commandExecution/requestApproval',
            'params': value}


def file_change(**changes):
    value = {'threadId': 'thread', 'turnId': 'turn', 'itemId': 'patch-1', 'startedAtMs': 1, 'reason': None,
             'grantRoot': None}
    value.update(changes)
    return {'id': 'request-' + value['itemId'], 'method': 'item/fileChange/requestApproval', 'params': value}


@pytest.fixture
def native():
    """The same minimal Native as test_native_policy.py (no __init__): the records must work without new setup."""
    value = Native.__new__(Native)
    value.messages, value.completed, value.next_id = queue.Queue(), {}, 7
    value.auth, value.blocked_reason, value.external = None, None, False
    value.work = Path(WORK)
    value.sent, value.authorized = [], []
    value.send = value.sent.append
    value.allow = True

    def authorize(turn, arguments):
        value.authorized.append((turn['turnId'], arguments))
        if isinstance(value.allow, Exception):
            raise value.allow
        return value.allow
    value.authorize_shell = authorize
    return value


@pytest.fixture
def turn():
    return {'thread': 'thread', 'turnId': 'turn', 'pendingTools': [], 'approvalItems': set(), 'session': 's',
            'request': 'r', 'token': 't' * 40, 'scope': {'page': {'kind': 'document_workspace'}}}


@pytest.fixture
def records(caplog):
    caplog.set_level(logging.INFO, logger='uvicorn.error')

    def read(prefix='runner_approval'):
        out = []
        for record in caplog.records:
            text = record.getMessage()
            head, _, body = text.partition(' ')
            if head == prefix:
                out.append(json.loads(body))
        return out
    return read


def deliver(native, *messages):
    """Through the real stdout reader (where the arrival is stamped, review round 1), then the queue. The reader ends
    with its fatal marker; every rpc below returns at its own response before reaching it."""
    native.process = type('Process', (), {'stdout': [json.dumps(m) + '\n' for m in messages]})()
    native.reader()


def decisions(native):
    return [(reply['id'], reply['result']['decision']) for reply in native.sent
            if isinstance(reply.get('result'), dict) and 'decision' in reply['result']]


def by_stage(lines, stage):
    return [line for line in lines if line['stage'] == stage]


# --- L1 schema -----------------------------------------------------------------------------------------------------

def test_l1_each_stage_has_exactly_its_keys(native, turn, records):
    native.reply_approval(command(), turn)
    lines = records()
    assert [line['stage'] for line in lines] == ['dispatched', 'decided', 'sent']
    for line in lines:
        assert set(line) == KEYS[line['stage']] and line['v'] == 1 and line['seq'] == 1
    dispatched, decided, sent = lines
    assert dispatched['kind'] == 'command' and dispatched['inTurn'] is True and dispatched['pending'] is False
    assert decided['decision'] == 'accept' and decided['reason'] == 'accepted' and decided['refused'] == []
    assert sent['ok'] is True and all(isinstance(sent[k], int) for k in ('m0', 'm1'))
    native.approval_summary('thread', 'turn')
    (summary,) = records('runner_approval_summary')
    assert set(summary) == SUMMARY_KEYS and summary['v'] == 1


@pytest.mark.parametrize('names', [[name] for name in policy.COMMAND_REFUSED] + [list(policy.COMMAND_REFUSED)])
def test_l1_refused_names_only_the_present_refused_fields(native, turn, records, names):
    native.reply_approval(command(**{name: {'x': 1} for name in names}), turn)
    (decided,) = by_stage(records(), 'decided')
    assert decided['refused'] == sorted(names) and set(decided['refused']) <= set(policy.COMMAND_REFUSED)
    assert decided['decision'] == 'decline' and decided['reason'] == 'not_eligible'


def test_l1_refused_is_empty_for_file_changes_and_odd_params(native, turn, records):
    native.reply_approval(file_change(), turn)
    odd = {'id': 'odd', 'method': 'item/commandExecution/requestApproval', 'params': ['not', 'a', 'dict']}
    native.reply_approval(odd, turn)
    assert [line['refused'] for line in by_stage(records(), 'decided')] == [[], []]
    assert approval_refused({'method': 'item/fileChange/requestApproval', 'params': {'approvalId': 'x'}}) == []


# --- L2 nothing but the fixed fields -------------------------------------------------------------------------------

def test_l2_no_free_text_reaches_any_record(native, turn, records, caplog):
    marks = {name: 'mark-' + uuid.uuid4().hex for name in (
        'command', 'cwd', 'reason', 'grant', 'network', 'additional', 'approvalId', 'amendment', 'actions', 'token',
        'scope', 'policy')}
    turn['token'] = marks['token']
    turn['scope'] = {'page': {'kind': 'document_workspace', 'title': marks['scope']}}
    native.reply_approval(command(command='echo ' + marks['command'], cwd=WORK + '/' + marks['cwd'],
                                  reason=marks['reason'], commandActions=[{'command': marks['actions']}],
                                  proposedExecpolicyAmendment=[marks['policy']]), turn)
    native.reply_approval(command(itemId='item-2', networkApprovalContext={'host': marks['network']},
                                  additionalPermissions={'p': marks['additional']}, approvalId=marks['approvalId'],
                                  proposedNetworkPolicyAmendments=[marks['amendment']]), turn)
    native.reply_approval(file_change(grantRoot='/' + marks['grant'], reason=marks['reason']), turn)
    native.approval_summary('thread', 'turn')
    assert len(records()) == 9
    assert not [mark for mark in marks.values() if mark in caplog.text]


# --- L3 order and seq ----------------------------------------------------------------------------------------------

def test_l3_seq_is_gap_free_and_stages_in_order(native, turn, records):
    for index in range(5):
        native.reply_approval(command(itemId='item-%d' % index), turn)
    native.reply_approval(file_change(), turn)
    native.reply_approval(command(itemId='late'), None)
    lines = records()
    assert [line['seq'] for line in by_stage(lines, 'dispatched')] == list(range(1, 8))
    for seq in range(1, 8):
        assert [line['stage'] for line in lines if line['seq'] == seq] == ['dispatched', 'decided', 'sent']


# --- L4 decisions are unchanged; the reason names the branch ---------------------------------------------------------

def test_l4_reasons_match_the_existing_decision_cases(native, turn, records):
    native.reply_approval(command(), turn)                                     # accepted
    native.reply_approval(command(cwd='/state'), turn)                         # outside the allow list
    native.reply_approval(command(itemId='item-1'), turn)                      # duplicate item
    native.allow = False
    native.reply_approval(command(itemId='refused'), turn)                     # permission check says no
    native.allow = RuntimeError('callback down')
    native.reply_approval(command(itemId='raised'), turn)                      # permission check raised
    native.allow = True
    native.reply_approval(command(itemId='outside'), None)                     # no turn
    native.completed[('thread', 'turn')] = {'status': 'interrupted'}
    native.reply_approval(command(itemId='finished'), turn)                    # late request, finished turn
    expected = [('request-item-1', 'accept', 'accepted'), ('request-item-1', 'decline', 'not_eligible'),
                ('request-item-1', 'decline', 'not_eligible'), ('request-refused', 'decline', 'authorize_refused'),
                ('request-raised', 'decline', 'error'), ('request-outside', 'decline', 'no_turn'),
                ('request-finished', 'decline', 'completed_turn')]
    assert decisions(native) == [(identity, decision) for identity, decision, _ in expected]
    assert [line['reason'] for line in by_stage(records(), 'decided')] == [reason for _, _, reason in expected]
    assert {line['reason'] for line in by_stage(records(), 'decided')} <= REASONS


def test_l4_file_change_rules_and_reasons(native, turn, records, monkeypatch):
    # #68 X2 (stated diff): an accepted file change needs its item/started changes inside the workspace
    monkeypatch.setattr(policy, 'lstat', lambda path: (_ for _ in ()).throw(FileNotFoundError(path)) if path != WORK
                        else Path('/').lstat())
    native.file_change_started({'threadId': 'thread', 'turnId': 'turn', 'item': {
        'type': 'fileChange', 'id': 'patch-1', 'status': 'inProgress',
        'changes': [{'path': WORK + '/notes.md', 'kind': {'type': 'add'}, 'diff': '+x\n'}]}}, turn)
    native.reply_approval(file_change(), turn)
    native.reply_approval(file_change(itemId='patch-2', grantRoot='/state'), turn)
    assert decisions(native) == [('request-patch-1', 'accept'), ('request-patch-2', 'decline')]
    assert [(l['decision'], l['reason']) for l in by_stage(records(), 'decided')] == [
        ('accept', 'accepted'), ('decline', 'not_eligible')]
    assert by_stage(records(), 'dispatched')[0]['kind'] == 'fileChange'


# --- L5 a failed send ------------------------------------------------------------------------------------------------

def test_l5_send_failure_is_logged_and_still_raises(native, turn, records):
    def broken(_):
        raise BrokenPipeError('stdin closed')
    native.send = broken
    with pytest.raises(BrokenPipeError):
        native.reply_approval(command(), turn)
    (sent,) = by_stage(records(), 'sent')
    assert sent['ok'] is False and sent['m0'] <= sent['m1']


# --- L6 the summary --------------------------------------------------------------------------------------------------

def test_l6_summary_counts_and_resets(native, turn, records):
    native.reply_approval(command(), turn)
    native.reply_approval(command(itemId='item-2'), turn)
    native.reply_approval(command(itemId='x'), None)
    native.approval_summary('thread', 'turn')
    native.approval_summary('thread', 'turn')
    first, second = records('runner_approval_summary')
    assert (first['dispatched'], first['decided'], first['sent'], first['outside']) == (3, 3, 3, 1)
    assert (first['seqFirst'], first['seqLast'], first['arrivedOnly']) == (1, 3, 0)
    assert first['thread'] == approval_hash('thread') and first['turn'] == approval_hash('turn')
    assert (second['dispatched'], second['seqFirst'], second['seqLast']) == (0, None, None)


def _turn_method():
    tree = ast.parse(APP.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and any(
                isinstance(n, ast.Constant) and n.value == 'runner_turn_timing %s' for n in ast.walk(node)):
            return node
    raise AssertionError('the turn method with runner_turn_timing was not found')


def test_l6_summary_is_written_in_the_turns_final_block():
    """Normal and exception paths: the final block that writes runner_turn_timing calls turn_released (the summary
    under the protocol mutex, then the release) exactly once, and no other release of the mutex is left there."""
    method = _turn_method()
    blocks = [handler for node in ast.walk(method) if isinstance(node, ast.Try) for handler in [node.finalbody]
              if handler]
    holders = [block for block in blocks if any(
        isinstance(n, ast.Constant) and n.value == 'runner_turn_timing %s' for stmt in block for n in ast.walk(stmt))]
    assert holders, 'runner_turn_timing is not in a finally block'
    calls = [n for stmt in holders[-1] for n in ast.walk(stmt)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'turn_released']
    assert len(calls) == 1
    releases = [n for stmt in holders[-1] for n in ast.walk(stmt) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute) and n.func.attr == 'release'
                and isinstance(n.func.value, ast.Attribute) and n.func.value.attr == 'lock']
    assert releases == []


# --- L7 the hash helpers -------------------------------------------------------------------------------------------

def test_l7_hashes():
    assert approval_hash('call_5d645') == hashlib.sha256(b'call_5d645').hexdigest()[:12]
    assert approval_hash(None) is None and approval_hash(7) is None
    assert approval_rpc_hash(7) == hashlib.sha256(b'7').hexdigest()[:12]
    assert approval_rpc_hash('r-1') == hashlib.sha256(b'"r-1"').hexdigest()[:12]
    assert approval_rpc_hash(None) is None and approval_rpc_hash(True) is None and approval_rpc_hash(1.5) is None


@pytest.mark.parametrize('identity', [True, False, 1.0, [1], {'a': 1}, None])
def test_l7_rpc_hash_rejects_other_types(identity):
    assert approval_rpc_hash(identity) is None


def test_l7_rpc_hash_keeps_integer_and_string_ids_apart(native, turn, records):
    """review: 1 and "1" are different ids; the arrival map uses the same JSON text rule as the rpc hash."""
    assert approval_rpc_hash(1) != approval_rpc_hash('1')
    messages = []
    for identity in (1, '1'):
        message = command(itemId='item-%r' % (identity,))
        message['id'] = identity
        messages.append(message)
    deliver(native, *messages, {'id': 7, 'result': {}})
    native.rpc('thread/read', {'threadId': 'thread'}, turn)
    first, second = by_stage(records(), 'dispatched')
    assert (first['rpc'], second['rpc']) == (approval_rpc_hash(1), approval_rpc_hash('1'))
    assert first['arrivedM'] is not None and second['arrivedM'] is not None  # each found its own arrival
    native.approval_summary('thread', 'turn')
    assert records('runner_approval_summary')[0]['arrivedOnly'] == 0


# --- L8 a record failure never changes a decision --------------------------------------------------------------------

@pytest.mark.parametrize('breakage', ['logger', 'hash', 'state', 'utc'])
def test_l8_record_failures_leave_the_decision_unchanged(native, turn, monkeypatch, breakage):
    def boom(*args, **kwargs):
        raise RuntimeError('record failure')
    if breakage == 'logger':
        monkeypatch.setattr(logging.getLogger('uvicorn.error'), 'info', boom)
    elif breakage == 'hash':
        monkeypatch.setattr(runner_app, 'approval_hash', boom)
    elif breakage == 'state':
        monkeypatch.setattr(Native, 'approval_state', boom)
    else:
        monkeypatch.setattr(runner_app, 'approval_utc', boom)
    native.reply_approval(command(), turn)
    native.allow = False
    native.reply_approval(command(itemId='item-2'), turn)
    native.approval_summary('thread', 'turn')
    assert decisions(native) == [('request-item-1', 'accept'), ('request-item-2', 'decline')]
    assert native.authorized == [('turn', {'command': 'python -m pytest -q', 'cwd': WORK})] * 2


# --- L9 pending requests --------------------------------------------------------------------------------------------

def test_l9_pending_request_keeps_its_arrival_and_its_decision(native, turn, records):
    turn['turnId'] = None
    deliver(native, command(), {'id': 7, 'result': {'turn': {'id': 'turn'}}})
    started = native.rpc('turn/start', {'threadId': 'thread', 'input': []}, turn)
    time.sleep(0.002)
    turn['turnId'] = started['turn']['id']
    for message in turn.pop('pendingTools'):
        native.reply_server(message, turn)
    (dispatched,) = by_stage(records(), 'dispatched')
    assert dispatched['pending'] is True and dispatched['arrived'] is not None
    assert dispatched['arrivedM'] < dispatched['m'] and dispatched['m'] - dispatched['arrivedM'] >= 2_000_000
    assert decisions(native) == [('request-item-1', 'accept')]


def test_l9_direct_request_is_not_pending(native, turn, records):
    deliver(native, command(), {'id': 7, 'result': {}})
    native.rpc('thread/read', {'threadId': 'thread'}, turn)
    (dispatched,) = by_stage(records(), 'dispatched')
    assert dispatched['pending'] is False and dispatched['arrivedM'] <= dispatched['m']


# --- L10 an arrival never dispatched -------------------------------------------------------------------------------

def test_l10_arrived_but_not_dispatched_shows_in_the_summary(native, turn, records):
    native.approval_arrived(command(itemId='lost'))
    deliver(native, command(), {'id': 7, 'result': {}})  # read by the reader and dispatched
    native.rpc('thread/read', {'threadId': 'thread'}, turn)
    native.approval_summary('thread', 'turn')
    (summary,) = records('runner_approval_summary')
    # Only the request that was never dispatched counts; the dispatched one left the arrival map.
    assert summary['arrivedOnly'] == 1 and summary['dispatched'] == 1


def test_l10_arrival_is_the_reader_time_not_the_dequeue_time(native, turn, records):
    """review round 1 M5: a request read from stdout before a revoke but dequeued after it keeps its earlier arrival;
    a request read but never dispatched (left in the queue) is counted in arrivedOnly."""
    deliver(native, command(), command(itemId='queued'), {'id': 7, 'result': {}})
    revoke = time.monotonic_ns()             # e.g. the unit's session revoke, while both wait in the queue
    time.sleep(0.003)
    first = native.messages.get()            # the first request is handled late (a slow earlier handler)
    native.reply_server(first, turn)
    native.approval_summary('thread', 'turn')
    (dispatched,) = by_stage(records(), 'dispatched')
    assert dispatched['arrivedM'] < revoke < dispatched['m']
    (summary,) = records('runner_approval_summary')
    assert summary['dispatched'] == 1 and summary['arrivedOnly'] == 1  # 'queued' was read but never dispatched


def test_l10_the_receive_loop_never_stamps_again(native, turn, records):
    """The dequeue path only marks pending requests; it never replaces the reader's arrival stamp."""
    source = APP.read_text()
    receive = source[source.index('    def receive(self'):source.index('    def reply_tool(self')]
    assert 'self.approval_arrived(message)' not in receive
    assert 'self.approval_arrived(message, pending=True)' in receive
    reader = source[source.index('    def reader(self'):source.index('    def send(self')]
    assert reader.index('self.approval_arrived(message)') < reader.index('self.messages.put(message)')


# --- L11 the send boundaries --------------------------------------------------------------------------------------

def test_l11_m0_and_m1_wrap_the_write(native, turn, records):
    stamps = []

    def stamped(body):
        stamps.append(time.monotonic_ns())
        native.sent.append(body)
    native.send = stamped
    native.reply_approval(command(), turn)
    (sent,) = by_stage(records(), 'sent')
    assert sent['m0'] <= stamps[0] <= sent['m1']


def test_l11_m0_is_taken_after_the_decision():
    """Static: the send boundary wraps exactly the self.send call of reply_approval (m0 before, m1 in finally)."""
    source = APP.read_text()
    body = source[source.index('    def reply_approval'):source.index('    # --- #68 P-A approval records')]
    assert body.index('self.approval_decided(') < body.index('time.monotonic_ns()') < body.index('self.send(answer)')
    assert body.index('self.send(answer)') < body.index('self.approval_sent(seq, sent, before, time.monotonic_ns())')


# --- L12 the monotonic order of one request -------------------------------------------------------------------------

def test_l12_monotonic_values_are_ordered(native, turn, records):
    deliver(native, command(), {'id': 7, 'result': {}})
    native.rpc('thread/read', {'threadId': 'thread'}, turn)
    dispatched, decided, sent = records()
    assert dispatched['arrivedM'] <= dispatched['m'] <= decided['m'] <= sent['m0'] <= sent['m1']


# --- review round 2 (M6): the summary is taken under the protocol mutex --------------------------------------------

def test_m6_each_turn_summary_holds_only_its_own_records(native, turn, records, monkeypatch):
    """Two real workers on the real mutex. Worker A (no approval) ends its turn through turn_released; worker B waits
    for the mutex, records one approval, then ends its turn. A's summary is made to wait (up to 0.5 s) for B's
    record: with the summary taken before the release, B cannot record in time and A reports 0; if the release
    came first, B's record would fall into A's summary (review's A=1, B=0)."""
    native.lock = threading.Lock()
    b_recorded, a_holds = threading.Event(), threading.Event()
    real_summary = Native.approval_summary
    calls = []

    def summary(self, thread, turn_id):
        if turn_id == 'turn-a':
            b_recorded.wait(0.5)  # gives B its chance to record before A's snapshot
        calls.append(turn_id)
        return real_summary(self, thread, turn_id)
    monkeypatch.setattr(Native, 'approval_summary', summary)

    def worker_a():
        native.lock.acquire()
        a_holds.set()
        native.turn_released({'thread': 'thread', 'turn_id': 'turn-a'})

    def worker_b():
        a_holds.wait(5)
        native.lock.acquire()
        native.reply_approval(command(), turn)
        b_recorded.set()
        native.turn_released({'thread': 'thread', 'turn_id': 'turn-b'})
    threads = [threading.Thread(target=worker_a), threading.Thread(target=worker_b)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    summaries = {s['turn']: s for s in records('runner_approval_summary')}
    assert calls == ['turn-a', 'turn-b']
    assert summaries[approval_hash('turn-a')]['dispatched'] == 0
    assert summaries[approval_hash('turn-b')]['dispatched'] == 1
    assert not native.lock.locked()


def test_m6_turn_released_always_releases(native, monkeypatch):
    native.lock = threading.Lock()
    native.lock.acquire()
    monkeypatch.setattr(Native, 'approval_summary', lambda self, thread, turn_id: (_ for _ in ()).throw(RuntimeError))
    with pytest.raises(RuntimeError):
        native.turn_released({'thread': 't', 'turn_id': 'u'})
    assert not native.lock.locked()
