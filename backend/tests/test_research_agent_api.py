"""Review-owned PostgreSQL/router checks with synthetic live-status port only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import uuid

import pytest
from sqlalchemy import select, text

from app.research_models import Conversation
from tests.test_agent_research_saves import turn
from tests.test_research import identity
from tests.test_page_chat import callback
from tests.research_api_fixture import api_client


def prepared(client, admin):
    owner, auth = identity(client, admin)
    session, runner = turn(client, owner, auth)
    assert callback(client, session, runner, 'research_list', {'type': 'document'}).status_code == 200
    token = runner._research_db_fixture_tokens[(session['id'], runner.last['requestId'])]
    return owner, auth, session, runner, token, api_client(client.app)


def test_public_router_requires_separate_bearer_and_internal_callback_cannot_use_it(client, admin):
    owner, auth, session, runner, token, api = prepared(client, admin)
    path = '/api/research/agent/v1/research_list'
    assert api.post(path, json={'type': 'document'}).status_code == 401
    assert api.post(path, json={'type': 'document'}, headers={
        'Authorization': 'Bearer ' + runner.last['toolToken']}).status_code == 401
    assert api.post('/api/research/agent/v1/research_post_publish', json={}, headers={
        'Authorization': 'Bearer ' + token}).status_code == 403
    internal = client.post('/api/internal/research/tools', json={'sessionId': session['id'],
        'requestId': runner.last['requestId'], 'name': 'research_get', 'arguments': {}},
        headers={'Authorization': 'Bearer ' + token})
    assert internal.status_code in {401, 403, 404}
    assert client.post(path, json={'type': 'document'}, headers={
        'Authorization': 'Bearer ' + token}).status_code == 404  # Default-off real app.


@pytest.mark.parametrize('damage', ['expired', 'hash', 'request', 'login', 'role'])
def test_fresh_db_auth_blocks_expiry_revoke_new_request_and_role_changes(client, admin, damage):
    owner, auth, session, runner, token, api = prepared(client, admin)
    with client.app.state.sessions.begin() as db:
        row = db.scalar(select(Conversation).where(Conversation.id == uuid.UUID(session['id'])).with_for_update())
        if damage in {'expired', 'hash'}:
            record = json.loads(json.dumps(row.native_record))
            grant = record['platformRequests'][-1]['researchGrant']
            if damage == 'expired': grant['expiresAt'] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
            else: grant['hash'] = None
            row.native_record = record
        if damage == 'request': row.request_id = uuid.uuid4()
        if damage == 'login': db.execute(text('DELETE FROM auth.sessions WHERE token_hash=:hash'), {'hash': row.login_hash})
    if damage == 'role':
        with admin.begin() as db:
            db.execute(text("UPDATE auth.accounts SET role='commenter' WHERE id=:owner"), {'owner': owner})
    reply = api.post('/api/research/agent/v1/research_list', json={'type': 'document'},
                     headers={'Authorization': 'Bearer ' + token})
    assert reply.status_code in {401, 403}
    if damage == 'expired': assert reply.json()['error']['code'] == 'research_token_expired'
    assert token not in reply.text


def test_invalid_success_rolls_back_write_and_idempotency_but_records_failure(client, admin, monkeypatch):
    _, _, session, runner, _, _ = prepared(client, admin)
    monkeypatch.setattr('app.research_receipts.success', lambda *args: False)
    key = str(uuid.uuid4())
    reply = callback(client, session, runner, 'research_document_save',
                     {'title': 'Must rollback', 'content': 'no committed version', 'mutationId': key})
    assert reply.status_code == 503
    with admin.connect() as db:
        assert db.execute(text("SELECT count(*) FROM research.versions WHERE title='Must rollback'")).scalar_one() == 0
        assert db.execute(text('SELECT count(*) FROM research.idempotency WHERE key=:key'), {'key': key}).scalar_one() == 0
        record = db.execute(text('SELECT native_record FROM research.conversations WHERE id=:id'), {'id': session['id']}).scalar_one()
    assert record['researchCalls'][-1]['status'] == 'failed'
    assert 'saved' not in record['researchCalls'][-1]


def test_receipts_commit_with_mutation_and_refresh_cannot_forge_or_erase_them(client, admin):
    _, _, session, runner, token, _ = prepared(client, admin)
    saved = callback(client, session, runner, 'research_document_save', {'title': 'Real save',
        'content': 'Source body', 'mutationId': str(uuid.uuid4())}).json()
    root = '/api/research/sessions/' + session['id']
    before = client.get(root + '/research-calls').json()['items']
    assert before[-1]['saved'] == saved['saved'] and token not in json.dumps(before)
    runner.record['researchCalls'] = [{'id': 'forged', 'status': 'succeeded'}]
    assert client.get(root).status_code == 200
    assert client.get(root + '/research-calls').json()['items'] == before
    assert len(saved['saved']['versionId']) == 36


def test_five_same_conversation_calls_release_connections_without_nested_checkout(client, admin):
    _, _, session, runner, _, _ = prepared(client, admin)
    def read(_): return callback(client, session, runner, 'research_list', {'type': 'document'}).status_code
    with ThreadPoolExecutor(max_workers=5) as pool:
        assert list(pool.map(read, range(5))) == [200] * 5
    assert client.app.state.engine.pool.checkedout() == 0
