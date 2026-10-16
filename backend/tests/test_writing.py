"""Real PostgreSQL/API drafts, pending proposals, CAS and retained history."""
import uuid
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import text
from app.migrate import migrate
from tests.test_research import identity, headers, create
from tests.test_conversations import create_session
from tests.test_page_chat import HeldRunner, callback
from tests.test_policy_publication import publish

ROOT = '/api/research/document-drafts'


@pytest.fixture(scope='module', autouse=True)
def writing_schema(schema):
    migrate('0005_writing')


def make_draft(client, auth, document=None, **changes):
    body = {'title': '', 'content': ''}
    if document:
        body.update(documentId=document['id'], baseDocumentVersion=document['version'],
                    baseVersionId=document['latestVersion']['id'], title=document['title'], content=document['content'])
    body.update(changes)
    reply = client.post(ROOT, json=body, headers=headers(auth))
    assert reply.status_code == 201, reply.text
    return reply.json()


def versions(client, doc):
    return client.get('/api/research/documents/'+doc['id']+'/versions').json()['items']


def propose(client, admin, document, auth, owner):
    runner = HeldRunner(client, owner)
    session = create_session(client, auth)
    reply = client.post('/api/research/sessions/'+session['id']+'/messages',
        json={'text': 'propose a revision', 'expectedVersion': 1}, headers=headers(auth))
    assert reply.status_code == 202, reply.text
    args = {'id': document['id'], 'expectedVersion': document['version'], 'title': 'Codex proposal',
            'content': '# Suggested content', 'mutationId': str(uuid.uuid4())}
    reply = callback(client, session, runner, 'research_document_update', args)
    assert reply.status_code == 200, reply.text
    assert callback(client, session, runner, 'research_document_update', args).json() == reply.json()
    return reply.json()


def test_new_blank_draft_autosave_commit_and_replay(client, admin):
    owner, auth = identity(client, admin)
    draft = make_draft(client, auth)
    path = ROOT+'/'+draft['id']
    assert client.get('/api/research/documents').json()['items'] == []
    assert client.get(ROOT).json()['items'][0]['id'] == draft['id']
    assert 'content' not in client.get(ROOT).json()['items'][0]
    assert client.post(path+'/commit', json={'expectedDraftRevision': 1}, headers=headers(auth)).status_code == 422
    changed = {'expectedDraftRevision': 1, 'title': 'Saved', 'content': 'Words'}
    key = str(uuid.uuid4())
    saved = client.put(path, json=changed, headers=headers(auth, key))
    assert saved.json()['revision'] == 2
    assert client.put(path, json=changed, headers=headers(auth, key)).json() == saved.json()
    assert client.put(path, json={**changed, 'content': 'other'}, headers=headers(auth, key)).status_code == 409
    assert client.put(path, json=changed, headers=headers(auth)).status_code == 409
    key = str(uuid.uuid4()); body = {'expectedDraftRevision': 2}
    committed = client.post(path+'/commit', json=body, headers=headers(auth, key))
    assert committed.status_code == 200, committed.text
    result = committed.json()
    assert result['draft']['state'] == 'committed' and result['document']['latestVersion']['number'] == 1
    assert client.post(path+'/commit', json=body, headers=headers(auth, key)).json() == result
    assert len(versions(client, result['document'])) == 1
    assert client.get(ROOT).json()['items'] == []
    assert client.get(path).json()['content'] == 'Words'
    assert client.put(path, json={**changed, 'expectedDraftRevision': 3}, headers=headers(auth)).status_code == 409


def test_document_head_conflict_preserves_draft_and_explicit_resolution(client, admin):
    _, auth = identity(client, admin)
    doc = create(client, auth, 'documents')
    draft = make_draft(client, auth, doc, content='My local text')
    path = ROOT+'/'+draft['id']
    assert len(versions(client, doc)) == 1
    assert client.post(ROOT, json={'documentId': doc['id'], 'baseDocumentVersion': 1,
        'baseVersionId': doc['latestVersion']['id'], 'title': '', 'content': ''}, headers=headers(auth)).status_code == 409
    latest = client.patch('/api/research/documents/'+doc['id'], json={'expectedVersion': 1,
        'title': 'Other tab', 'content': 'Other text'}, headers=headers(auth)).json()
    body = {'expectedDraftRevision': 1, 'expectedVersion': 2}
    assert client.post(path+'/commit', json=body, headers=headers(auth)).status_code == 409
    assert client.get(path).json()['state'] == 'active'
    assert len(versions(client, doc)) == 2
    body['resolvedBaseVersionId'] = latest['latestVersion']['id']
    result = client.post(path+'/commit', json=body, headers=headers(auth))
    assert result.status_code == 200, result.text
    assert result.json()['document']['content'] == 'My local text'
    assert len(versions(client, doc)) == 3


def test_discard_tombstone_and_archived_document_recovery(client, admin):
    _, auth = identity(client, admin)
    doc = create(client, auth, 'documents')
    draft = make_draft(client, auth, doc)
    path = ROOT+'/'+draft['id']
    client.request('DELETE', '/api/research/documents/'+doc['id'], json={'expectedVersion': 1}, headers=headers(auth))
    assert client.put(path, json={'expectedDraftRevision': 1, 'title': '', 'content': ''}, headers=headers(auth)).status_code == 409
    assert client.post(path+'/commit', json={'expectedDraftRevision': 1, 'expectedVersion': 2}, headers=headers(auth)).status_code == 409
    assert client.get(path).status_code == 200
    result = client.request('DELETE', path, json={'expectedDraftRevision': 1}, headers=headers(auth))
    assert result.json()['state'] == 'discarded'
    assert client.put(path, json={'expectedDraftRevision': 2, 'title': '', 'content': ''}, headers=headers(auth)).status_code == 409


def test_other_owner_and_bad_input_do_not_expose_drafts(client, admin):
    _, auth = identity(client, admin)
    first = make_draft(client, auth)
    _, second = identity(client, admin, '222222')
    path = ROOT+'/'+first['id']
    assert client.get(path).status_code == 404
    assert client.put(path, json={'expectedDraftRevision': 1, 'title': '', 'content': ''}, headers=headers(second)).status_code == 404
    assert client.get(ROOT).json()['items'] == []
    for body in ({'title': '', 'content': '', 'ownerId': 'fake'}, {'title': '', 'content': '\x00'},
                 {'title': '', 'content': '', 'baseVersionId': str(uuid.uuid4())}):
        assert client.post(ROOT, json=body, headers=headers(second)).status_code == 422
    assert client.post(ROOT, json={'title': '', 'content': ''}, headers={'Idempotency-Key': str(uuid.uuid4())}).status_code == 403


def test_codex_proposal_does_not_change_head_and_atomic_accept_preserves_originals(client, admin):
    owner, auth = identity(client, admin)
    doc = create(client, auth, 'documents')
    draft = make_draft(client, auth, doc, content='My unsaved draft')
    proposed = propose(client, admin, doc, auth, owner)
    path = '/api/research/documents/'+doc['id']
    assert proposed['status'] == 'pending' and proposed['origin'] == 'codex'
    assert client.get(path).json()['content'] == doc['content']
    assert len(versions(client, doc)) == 1
    assert len(client.get(path+'/proposals').json()['items']) == 1
    target = path+'/proposals/'+proposed['id']
    body = {'expectedVersion': 1, 'expectedProposalVersion': 1, 'resolvedBaseVersionId': doc['latestVersion']['id'],
            'title': 'Merged', 'content': 'User-reviewed merge'}
    assert client.post(target+'/accept', json=body, headers=headers(auth)).status_code == 409
    body.update(draftId=draft['id'], expectedDraftRevision=1)
    key = str(uuid.uuid4())
    result = client.post(target+'/accept', json=body, headers=headers(auth, key))
    assert result.status_code == 200, result.text
    assert result.json()['proposal']['status'] == 'accepted'
    assert result.json()['document']['content'] == 'User-reviewed merge'
    assert result.json()['draft']['state'] == 'committed'
    assert client.get(ROOT+'/'+draft['id']).json()['content'] == 'My unsaved draft'
    assert client.get(target).json()['content'] == '# Suggested content'
    assert client.post(target+'/accept', json=body, headers=headers(auth, key)).json() == result.json()
    assert client.post(target+'/accept', json={**body, 'expectedProposalVersion': 2}, headers=headers(auth)).status_code == 409
    assert len(versions(client, doc)) == 2


def test_stale_accept_rolls_back_and_discard_preserves_publication(client, admin):
    owner, auth = identity(client, admin)
    doc = create(client, auth, 'documents')
    publish(client, auth, doc)
    root = '/api/research/documents/'+doc['id']
    doc = client.get(root).json()
    published = client.get('/api/public/documents/'+doc['id']).json()
    draft = make_draft(client, auth, doc)
    proposal = propose(client, admin, doc, auth, owner)
    path = root+'/proposals/'+proposal['id']
    client.put(ROOT+'/'+draft['id'], json={'expectedDraftRevision': 1, 'title': 'Later', 'content': 'autosave'}, headers=headers(auth))
    body = {'expectedVersion': doc['version'], 'expectedProposalVersion': 1, 'resolvedBaseVersionId': doc['latestVersion']['id'],
            'title': 'Accept', 'content': 'Text', 'draftId': draft['id'], 'expectedDraftRevision': 1}
    assert client.post(path+'/accept', json=body, headers=headers(auth)).status_code == 409
    assert client.get(path).json()['status'] == 'pending'
    assert client.get(ROOT+'/'+draft['id']).json()['state'] == 'active'
    assert len(versions(client, doc)) == 1
    key = str(uuid.uuid4())
    result = client.post(path+'/discard', json={'expectedProposalVersion': 1}, headers=headers(auth, key))
    assert result.json()['status'] == 'discarded'
    assert client.post(path+'/discard', json={'expectedProposalVersion': 1}, headers=headers(auth, key)).json() == result.json()
    assert client.get('/api/public/documents/'+doc['id']).json() == published


def test_simultaneous_autosaves_have_one_winner(client, admin):
    _, auth = identity(client, admin)
    value = make_draft(client, auth)
    def save(content):
        return client.put(ROOT+'/'+value['id'], json={'expectedDraftRevision': 1, 'title': '', 'content': content}, headers=headers(auth)).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(save, ['first', 'second'])) == [200, 409]
    assert client.get(ROOT+'/'+value['id']).json()['revision'] == 2


def test_concurrent_accepts_append_only_one_version(client, admin):
    owner, auth = identity(client, admin)
    doc = create(client, auth, 'documents')
    proposal = propose(client, admin, doc, auth, owner)
    path = '/api/research/documents/'+doc['id']+'/proposals/'+proposal['id']+'/accept'
    body = {'expectedVersion': 1, 'expectedProposalVersion': 1, 'resolvedBaseVersionId': doc['latestVersion']['id'],
            'title': proposal['title'], 'content': proposal['content']}
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda _: client.post(path, json=body, headers=headers(auth)).status_code, range(2)))
    assert sorted(replies) == [200, 409]
    assert len(versions(client, doc)) == 2


def test_foreign_proposals_and_revoked_login_never_replay_success(client, admin):
    owner, auth = identity(client, admin)
    doc = create(client, auth, 'documents')
    proposal = propose(client, admin, doc, auth, owner)
    path = '/api/research/documents/'+doc['id']+'/proposals/'+proposal['id']
    key = str(uuid.uuid4()); body = {'expectedProposalVersion': 1}
    assert client.post(path+'/discard', json=body, headers=headers(auth, key)).status_code == 200
    with admin.begin() as db:
        db.execute(text('DELETE FROM auth.sessions WHERE account_id=:owner'), {'owner': owner})
    assert client.post(path+'/discard', json=body, headers=headers(auth, key)).status_code == 401
    _, other = identity(client, admin, '222222')
    assert client.get(path).status_code == 404
    assert client.get('/api/research/documents/'+doc['id']+'/proposals').status_code == 404
    assert client.post(path+'/discard', json=body, headers=headers(other)).status_code == 404


@pytest.mark.parametrize('damage,repair', [
    ('REVOKE UPDATE ON research.document_drafts FROM anxious_api', 'GRANT UPDATE ON research.document_drafts TO anxious_api'),
    ('GRANT DELETE ON research.document_proposals TO anxious_api', 'REVOKE DELETE ON research.document_proposals FROM anxious_api'),
    ('ALTER TABLE research.document_drafts RENAME TO hidden_drafts', 'ALTER TABLE research.hidden_drafts RENAME TO document_drafts'),
])
def test_writing_readiness_refuses_schema_and_privilege_drift(client, admin, damage, repair):
    _, auth = identity(client, admin)
    with admin.begin() as db: db.execute(text(damage))
    try:
        assert client.get('/api/research/health').status_code == 503
        assert client.post(ROOT, json={'title': '', 'content': ''}, headers=headers(auth)).status_code == 503
        with pytest.raises(RuntimeError): migrate('0005_writing')
    finally:
        with admin.begin() as db: db.execute(text(repair))
    assert client.get('/api/research/health').status_code == 200


def test_conflict_reason_contract(client, admin):
    owner, auth = identity(client, admin)
    doc = create(client, auth, 'documents')
    value = make_draft(client, auth, doc)
    path = ROOT+'/'+value['id']
    def reason(reply, expected):
        assert reply.status_code == 409, reply.text
        assert reply.json()['error']['details']['reason'] == expected
    reason(client.post(ROOT, json={'title': '', 'content': '', 'documentId': doc['id'],
        'baseDocumentVersion': 1, 'baseVersionId': doc['latestVersion']['id']}, headers=headers(auth)), 'active_draft_exists')
    reason(client.put(path, json={'expectedDraftRevision': 2, 'title': '', 'content': ''}, headers=headers(auth)), 'draft_revision')
    reason(client.post(path+'/commit', json={'expectedDraftRevision': 1, 'expectedVersion': 2}, headers=headers(auth)), 'document_version')
    reason(client.post(path+'/commit', json={'expectedDraftRevision': 1, 'expectedVersion': 1,
        'resolvedBaseVersionId': str(uuid.uuid4())}, headers=headers(auth)), 'base_changed')
    proposal = propose(client, admin, doc, auth, owner)
    ppath = '/api/research/documents/'+doc['id']+'/proposals/'+proposal['id']
    client.post(ppath+'/discard', json={'expectedProposalVersion': 1}, headers=headers(auth))
    reason(client.post(ppath+'/discard', json={'expectedProposalVersion': 2}, headers=headers(auth)), 'proposal_closed')
    client.request('DELETE', '/api/research/documents/'+doc['id'], json={'expectedVersion': 1}, headers=headers(auth))
    reason(client.put(path, json={'expectedDraftRevision': 1, 'title': '', 'content': ''}, headers=headers(auth)), 'archived')
    client.request('DELETE', path, json={'expectedDraftRevision': 1}, headers=headers(auth))
    reason(client.put(path, json={'expectedDraftRevision': 2, 'title': '', 'content': ''}, headers=headers(auth)), 'draft_closed')


@pytest.mark.parametrize('operation', ['commit', 'accept'])
def test_partial_snapshot_failure_rolls_back_all_states_and_retry(client, admin, monkeypatch, operation):
    from app.research import Store
    from sqlalchemy.exc import OperationalError
    owner, auth = identity(client, admin)
    doc = create(client, auth, 'documents')
    draft = make_draft(client, auth, doc)
    if operation == 'accept':
        proposal = propose(client, admin, doc, auth, owner)
        path = '/api/research/documents/'+doc['id']+'/proposals/'+proposal['id']+'/accept'
        body = {'expectedVersion': 1, 'expectedProposalVersion': 1, 'resolvedBaseVersionId': doc['latestVersion']['id'],
            'title': 'Accepted', 'content': 'Reviewed', 'draftId': draft['id'], 'expectedDraftRevision': 1}
    else:
        path = ROOT+'/'+draft['id']+'/commit'
        body = {'expectedVersion': 1, 'expectedDraftRevision': 1}
    original = Store.add_snapshot
    def broken(self, row, content):
        original(self, row, content)
        raise OperationalError('private query', None, Exception('private value'))
    monkeypatch.setattr(Store, 'add_snapshot', broken)
    key = str(uuid.uuid4())
    result = client.post(path, json=body, headers=headers(auth, key))
    assert result.status_code == 503 and 'private' not in result.text
    assert client.get(ROOT+'/'+draft['id']).json()['state'] == 'active'
    assert client.get('/api/research/documents/'+doc['id']).json()['version'] == 1
    assert len(versions(client, doc)) == 1
    if operation == 'accept':
        assert client.get(path.removesuffix('/accept')).json()['status'] == 'pending'
    monkeypatch.setattr(Store, 'add_snapshot', original)
    assert client.post(path, json=body, headers=headers(auth, key)).status_code == 200
    assert len(versions(client, doc)) == 2


def test_old_schema_refuses_writing_and_never_falls_back_to_direct_tool_update(client, admin):
    owner, auth = identity(client, admin)
    doc = create(client, auth, 'documents')
    runner = HeldRunner(client, owner)
    session = create_session(client, auth)
    assert client.post('/api/research/sessions/'+session['id']+'/messages',
        json={'text': 'propose', 'expectedVersion': 1}, headers=headers(auth)).status_code == 202
    with admin.begin() as db:
        db.execute(text("UPDATE research.alembic_version SET version_num='0004_publication'"))
    try:
        assert client.post(ROOT, json={'title': '', 'content': ''}, headers=headers(auth)).status_code == 503
        reply = callback(client, session, runner, 'research_document_update', {'id': doc['id'],
            'expectedVersion': 1, 'title': 'Not direct', 'content': 'Not direct', 'mutationId': str(uuid.uuid4())})
        assert reply.status_code == 503
        assert client.get('/api/research/documents/'+doc['id']).json()['content'] == doc['content']
        assert len(versions(client, doc)) == 1
    finally:
        with admin.begin() as db:
            db.execute(text("UPDATE research.alembic_version SET version_num='0005_writing'"))
