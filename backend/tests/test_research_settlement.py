"""Review-owned PostgreSQL/API settlement gate; fake runner, no model/MCP.

Scheduling probes pause actual authenticated tool requests on opposite sides of
Store.mutate's advisory lock. Back does not execute this integration suite.
"""
import threading
import uuid

import pytest
from sqlalchemy import text

from app.research import Store, mutation_fingerprint
from tests.test_conversations import create_session
from tests.test_page_chat import HeldRunner, callback
from tests.test_research import headers, identity


class SettlementRunner(HeldRunner):
    def __init__(self, client, owner, engine):
        super().__init__(client, owner)
        self.owner, self.engine = str(owner), engine
        self.state_id, self.pod = str(uuid.uuid4()), str(uuid.uuid4())
        self.fence, self.acks, self.lose_ack, self.drained = None, [], False, True

    def pending(self, session, arguments):
        self.state = 'failed'
        self.record = {'turns': [{'id': 'held-turn', 'status': 'interrupted', 'items': [
            {'id': 'u', 'type': 'userMessage', 'content': [{'type': 'text', 'text': self.last['text']}]}]}]}
        self.fence = {'version': 1, 'ownerId': self.owner, 'stateId': self.state_id,
            'sessionId': session['id'], 'requestId': self.last['requestId'],
            'mutationId': arguments['mutationId'], 'tool': 'research_document_save',
            'fingerprint': mutation_fingerprint('tool:research_document_save', arguments),
            'fenceId': str(uuid.uuid4()), 'phase': 'execute_intent', 'outcome': None,
            'native': {'generation': 'generation', 'threadId': 'same-native-thread',
                       'turnId': 'held-turn', 'itemId': 'call'}}

    def read(self, identity):
        return {**super().read(identity), 'ownerId': self.owner, 'stateId': self.state_id,
                'podUid': self.pod, 'researchFence': self.fence, 'researchDrained': self.drained}

    def research_settlement(self, identity, body):
        assert body['podUid'] == self.pod and body['binding']['sessionId'] == str(identity)
        # A separate transaction sees terminal evidence before any ACK is sent.
        with self.engine.connect() as db:
            stored = db.execute(text('SELECT status, fingerprint FROM research.idempotency WHERE owner_id=:owner AND key=:key'),
                {'owner': self.owner, 'key': self.fence['mutationId']}).one()
        assert stored.fingerprint == self.fence['fingerprint']
        assert stored.status == (200 if body['outcome'] == 'applied' else 409)
        self.acks.append(body)
        if self.lose_ack:
            self.lose_ack = False
            raise OSError('simulated ACK transport loss')
        self.fence.update(phase='terminal', outcome=body['outcome'])
        return {'ownerId': self.owner, 'stateId': self.state_id, 'podUid': self.pod,
                'requestId': self.last['requestId'], 'fenceId': self.fence['fenceId'], 'outcome': body['outcome']}


def setup(client, admin):
    owner, auth = identity(client, admin)
    session = create_session(client, auth)
    runner = SettlementRunner(client, owner, admin)
    result = client.post('/api/research/sessions/' + session['id'] + '/messages',
        json={'text': 'Save a private document', 'expectedVersion': 1}, headers=headers(auth))
    assert result.status_code == 202, result.text
    args = {'mutationId': str(uuid.uuid4()), 'title': 'Settled document', 'content': 'Source citation'}
    return owner, auth, session, runner, args


def poll(client, session):
    reply = client.get('/api/research/sessions/' + session['id'])
    assert reply.status_code == 200, reply.text
    return reply.json()


def test_timeout_after_success_commit_auto_resolves_applied_without_replay(client, admin):
    owner, auth, session, runner, args = setup(client, admin)
    saved = callback(client, session, runner, 'research_document_save', args)
    assert saved.status_code == 200, saved.text
    runner.pending(session, args)  # The runner lost this response, not its DB commit.
    detail = poll(client, session)
    assert detail['state'] == 'failed' and detail['storage']['state'] == 'applied'
    assert len(runner.acks) == 1
    documents = client.get('/api/research/documents').json()['items']
    assert len(documents) == 1 and documents[0]['id'] == saved.json()['id']
    version = client.get('/api/research/documents/' + documents[0]['id']).json()['latestVersion']
    assert version['author'] == {'kind': 'codex', 'conversationId': session['id']}


def test_resolver_before_already_authenticated_late_execute_tombstones_snapshot_zero(client, admin, monkeypatch):
    _, _, session, runner, args = setup(client, admin)
    entered, release, replies = threading.Event(), threading.Event(), []
    original = Store.mutate
    def pause_before_lock(store, key, operation, payload, action, status=200):
        if key == args['mutationId'] and operation == 'tool:research_document_save':
            entered.set()  # Actual HTTP auth/scope checks have already passed.
            assert release.wait(10)
        return original(store, key, operation, payload, action, status)
    monkeypatch.setattr(Store, 'mutate', pause_before_lock)
    worker = threading.Thread(target=lambda: replies.append(callback(client, session, runner, 'research_document_save', args)))
    worker.start()
    try:
        assert entered.wait(5)
        runner.pending(session, args)
        assert poll(client, session)['storage']['state'] == 'not_applied'
    finally:
        release.set()
        worker.join(timeout=10)
    assert not worker.is_alive() and len(replies) == 1
    assert replies[0].status_code == 409 and replies[0].json()['error']['code'] == 'mutation_not_applied'
    assert client.get('/api/research/documents').json()['items'] == []
    assert len(runner.acks) == 1


def test_execute_holds_lock_then_commits_resolver_waits_and_eventually_applied(client, admin, monkeypatch):
    _, _, session, runner, args = setup(client, admin)
    entered, release, replies = threading.Event(), threading.Event(), []
    original = Store.create_item
    def pause_action(store, kind, body):
        if body.title == args['title']:
            entered.set()  # Store.mutate has already acquired the mutation lock.
            assert release.wait(10)
        return original(store, kind, body)
    monkeypatch.setattr(Store, 'create_item', pause_action)
    worker = threading.Thread(target=lambda: replies.append(callback(client, session, runner, 'research_document_save', args)))
    worker.start()
    try:
        assert entered.wait(5)
        runner.pending(session, args)
        assert poll(client, session)['storage']['state'] == 'checking'
        assert runner.acks == []
    finally:
        release.set()
        worker.join(timeout=10)
    assert not worker.is_alive() and replies[0].status_code == 200
    detail = poll(client, session)
    assert detail['state'] == 'failed' and detail['storage']['state'] == 'applied'
    assert len(runner.acks) == 1
    assert len(client.get('/api/research/documents').json()['items']) == 1


def test_uncertain_fence_waits_for_native_drain_and_lost_ack_retries_status_only(client, admin):
    _, auth, session, runner, args = setup(client, admin)
    runner.pending(session, args)
    runner.drained = False
    assert poll(client, session)['storage']['state'] == 'checking'
    assert runner.acks == []
    runner.drained, runner.lose_ack = True, True
    assert poll(client, session)['storage']['state'] == 'checking'
    assert len(runner.acks) == 1
    assert poll(client, session)['storage']['state'] == 'not_applied'
    assert len(runner.acks) == 2 and runner.dispatches == 1
    assert client.get('/api/research/documents').json()['items'] == []
    assert runner.acks[0] == runner.acks[1]
