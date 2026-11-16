"""Pure transaction/control protocol tests with fake DB/runner, not SQL isolation."""
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app import posts
from app.page_scope import POLICIES
from app.research import ResearchError, Store, mutation_fingerprint
from app.research_settlement import (BINDING, MUTATIONS, NOT_APPLIED, decide, reconcile)
from app.research_tools import dispatch


class Database:
    def __init__(self, record=None, locked=True):
        self.record, self.locked, self.added = record, locked, []

    def scalar(self, statement, params):
        assert 'pg_try_advisory_xact_lock' in str(statement)
        return self.locked

    def get(self, *_):
        return self.record

    def add(self, record):
        self.added.append(record)

    def flush(self):
        pass

    def execute(self, statement, *_):
        assert 'pg_advisory_xact_lock' in str(statement)


@pytest.fixture
def context():
    owner, state, session, request = (str(uuid4()) for _ in range(4))
    descriptor = {'version': 1, 'ownerId': owner, 'stateId': state, 'sessionId': session,
        'requestId': request, 'mutationId': str(uuid4()), 'tool': 'research_document_save',
        'fingerprint': 'a' * 64, 'fenceId': str(uuid4()), 'phase': 'execute_intent', 'outcome': None,
        'native': {'generation': 'native-generation', 'threadId': 'thread', 'turnId': 'turn', 'itemId': 'call'}}
    row = SimpleNamespace(id=session, request_id=request, native_record={'platformRequests': [
        {'requestId': request, 'turnId': 'turn', 'scope': {'page': {'kind': 'general'}}}]})
    result = {'ownerId': owner, 'stateId': state, 'podUid': str(uuid4()), 'requestId': request,
        'state': 'failed', 'threadId': 'thread', 'turnId': 'turn', 'researchFence': descriptor, 'researchDrained': True}
    events, db, failure = [], Database(), {'commit': False}
    @contextmanager
    def begin():
        events.append('begin')
        yield db
        if failure['commit']:
            events.append('commit_failed')
            raise RuntimeError('commit failed')
        events.append('committed')
    def ack(identity, body):
        assert events[-1] == 'committed'
        assert identity == session and body['binding'] == {key: descriptor[key] for key in BINDING}
        assert body['podUid'] == result['podUid']
        events.append('ACK')
        return {'requestId': request, 'fenceId': descriptor['fenceId'], 'outcome': body['outcome']}
    return SimpleNamespace(owner=owner, descriptor=descriptor, row=row, result=result, db=db,
        sessions=SimpleNamespace(begin=begin), runner=SimpleNamespace(research_settlement=ack),
        events=events, failure=failure)


def test_no_success_record_installs_terminal_tombstone_before_ack(context):
    ctx = context
    assert reconcile(ctx.sessions, ctx.owner, ctx.row, ctx.runner, ctx.result) == 'not_applied'
    assert ctx.events == ['begin', 'committed', 'ACK']
    stored = ctx.db.added[0]
    assert stored.status == 409 and stored.response == NOT_APPLIED
    assert stored.fingerprint == ctx.descriptor['fingerprint']


def test_committed_success_resolves_applied_without_reexecuting_content(context):
    ctx = context
    ctx.db.record = SimpleNamespace(status=200, response={'saved': {'id': str(uuid4())}},
                                     fingerprint=ctx.descriptor['fingerprint'])
    assert reconcile(ctx.sessions, ctx.owner, ctx.row, ctx.runner, ctx.result) == 'applied'
    assert ctx.events == ['begin', 'committed', 'ACK'] and ctx.db.added == []


def test_terminal_commit_failure_sends_no_ack(context):
    ctx = context
    ctx.failure['commit'] = True
    assert reconcile(ctx.sessions, ctx.owner, ctx.row, ctx.runner, ctx.result) == 'checking'
    assert ctx.events == ['begin', 'commit_failed']


def test_mutation_lock_busy_keeps_checking_and_sends_no_ack(context):
    ctx = context
    ctx.db.locked = False
    assert reconcile(ctx.sessions, ctx.owner, ctx.row, ctx.runner, ctx.result) == 'checking'
    assert ctx.events == ['begin', 'committed'] and ctx.db.added == []


@pytest.mark.parametrize('field', ['ownerId', 'stateId', 'sessionId', 'requestId', 'fingerprint', 'tool', 'native'])
def test_unverified_or_foreign_descriptor_never_reaches_db_or_ack(context, field):
    ctx = context
    ctx.descriptor[field] = {} if field == 'native' else 'other'
    assert reconcile(ctx.sessions, ctx.owner, ctx.row, ctx.runner, ctx.result) == 'unknown'
    assert ctx.events == []


def test_missing_original_metadata_and_public_scope_never_settle_owner_mutation(context):
    ctx = context
    ctx.row.native_record['platformRequests'][0]['scope']['page']['kind'] = 'public_document'
    assert reconcile(ctx.sessions, ctx.owner, ctx.row, ctx.runner, ctx.result) == 'unknown'
    ctx.row.native_record['platformRequests'] = []
    assert reconcile(ctx.sessions, ctx.owner, ctx.row, ctx.runner, ctx.result) == 'unknown'
    assert ctx.events == []


def test_native_drain_is_required_only_for_uncertain_path(context):
    ctx = context
    ctx.result.update(state='running', researchDrained=False)
    assert reconcile(ctx.sessions, ctx.owner, ctx.row, ctx.runner, ctx.result) == 'checking'
    assert ctx.events == []
    ctx.descriptor.update(phase='terminal', outcome='applied')
    assert reconcile(ctx.sessions, ctx.owner, ctx.row, ctx.runner, ctx.result) == 'applied'
    assert ctx.events == []  # Normal terminal callback, no polling ACK.


def test_fingerprint_conflict_keeps_unknown_without_ack(context):
    ctx = context
    ctx.db.record = SimpleNamespace(status=200, response={'saved': {}}, fingerprint='b' * 64)
    assert reconcile(ctx.sessions, ctx.owner, ctx.row, ctx.runner, ctx.result) == 'unknown'
    assert ctx.events == ['begin', 'committed'] and ctx.db.added == []


def test_duplicate_terminal_decision_does_not_insert_or_reexecute(context):
    ctx = context
    ctx.db.record = SimpleNamespace(status=409, response=NOT_APPLIED, fingerprint=ctx.descriptor['fingerprint'])
    assert decide(ctx.db, ctx.owner, ctx.descriptor['mutationId'], ctx.descriptor['fingerprint']) == 'not_applied'
    assert ctx.db.added == []


def test_lost_ack_retries_only_the_ack_on_later_poll(context):
    ctx = context
    def lost(*_):
        ctx.events.append('ACK_lost')
        raise OSError('connection lost')
    assert reconcile(ctx.sessions, ctx.owner, ctx.row, SimpleNamespace(research_settlement=lost), ctx.result) == 'checking'
    ctx.db.record = ctx.db.added[0]
    assert reconcile(ctx.sessions, ctx.owner, ctx.row, ctx.runner, ctx.result) == 'not_applied'
    assert len(ctx.db.added) == 1
    assert ctx.events == ['begin', 'committed', 'ACK_lost', 'begin', 'committed', 'ACK']


@pytest.mark.parametrize('name', sorted(MUTATIONS))
def test_common_store_terminal_guard_blocks_every_late_mutation_action(context, name):
    ctx = context
    arguments = {'mutationId': ctx.descriptor['mutationId'], 'title': 'later request'}
    ctx.db.record = SimpleNamespace(status=409, response=NOT_APPLIED,
        fingerprint=mutation_fingerprint('tool:' + name, arguments))
    with pytest.raises(ResearchError) as failure:
        Store(ctx.db, ctx.owner).mutate(arguments['mutationId'], 'tool:' + name, arguments,
            lambda: pytest.fail('Tombstone permitted a content action'))
    assert failure.value.code == 'mutation_not_applied' and failure.value.status == 409


@pytest.mark.parametrize('name', ['research_document_save', 'research_post_save'])
def test_dispatch_adapters_cannot_skip_the_common_terminal_guard(context, monkeypatch, name):
    ctx = context
    arguments = {'mutationId': ctx.descriptor['mutationId'], 'title': 'later request', 'content': 'body'}
    monkeypatch.setattr(posts, 'ready', lambda _: None)
    monkeypatch.setattr(posts, 'validate_retry_encoding', lambda *_: None)
    ctx.db.record = SimpleNamespace(status=409, response=NOT_APPLIED,
        fingerprint=mutation_fingerprint('tool:' + name, arguments))
    with pytest.raises(ResearchError) as failure:
        dispatch(Store(ctx.db, ctx.owner), name, arguments)
    assert failure.value.code == 'mutation_not_applied'


def test_runner_and_api_mutation_lists_cannot_drift():
    from runner.research_fence import MUTATIONS as runner_mutations
    assert MUTATIONS == runner_mutations
    assert all(name in POLICIES['general'][1] for name in MUTATIONS)
