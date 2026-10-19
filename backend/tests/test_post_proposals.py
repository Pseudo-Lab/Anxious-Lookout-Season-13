"""Real tool callback: D1 creates no document/post snapshot before acceptance."""
import uuid
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import text
from app.migrate import migrate
from tests.test_research import identity, headers, create
from tests.test_conversations import create_session
from tests.test_page_chat import HeldRunner, callback
from tests.test_posts import proposal_content, content, make_draft, commit, ROOT


@pytest.fixture(scope='module', autouse=True)
def proposal_schema(schema):
    migrate('0007_post_blocks')


def turn(client, auth, owner, page=None):
    runner = HeldRunner(client, owner)
    session = create_session(client, auth)
    result = client.post('/api/research/sessions/'+session['id']+'/messages',
        json={'text': 'Propose writing changes', 'expectedVersion': 1, 'page': page or {'kind': 'post_workspace'}}, headers=headers(auth))
    assert result.status_code == 202, result.text
    return session, runner


def test_new_post_proposal_reads_sources_and_draft_but_creates_no_version(client, admin):
    owner, auth = identity(client, admin)
    doc = create(client, auth, 'documents')
    draft = make_draft(client, auth, content='Human original', documentIds=[doc['id']])
    session, runner = turn(client, auth, owner)
    result = callback(client, session, runner, 'research_post_draft_get', {'draftId': draft['id']})
    assert result.json()['draft']['textProjection'] == 'Human original'
    assert callback(client, session, runner, 'research_get', {'type': 'document', 'id': doc['id']}).json()['content'] == doc['content']
    args = {**proposal_content(documentIds=[doc['id']]), 'mutationId': str(uuid.uuid4()), 'draftId': draft['id'], 'expectedDraftRevision': 1}
    proposal = callback(client, session, runner, 'research_post_propose', args)
    assert proposal.status_code == 200, proposal.text
    assert callback(client, session, runner, 'research_post_propose', args).json() == proposal.json()
    proposal = proposal.json()
    assert proposal['postId'] is None and proposal['status'] == 'pending'
    assert client.get('/api/research/posts').json()['items'] == []
    assert client.get(ROOT+'/'+draft['id']).json()['textProjection'] == 'Human original'
    path = '/api/research/post-proposals/'+proposal['id']+'/accept'
    body = {**content(), 'expectedProposalVersion': 1}
    assert client.post(path, json=body, headers=headers(auth)).status_code == 409
    body.update(draftId=draft['id'], expectedDraftRevision=1)
    key = str(uuid.uuid4())
    result = client.post(path, json=body, headers=headers(auth, key))
    assert result.status_code == 200, result.text
    assert client.post(path, json=body, headers=headers(auth, key)).json() == result.json()
    assert result.json()['post']['latestVersion']['number'] == 1
    assert result.json()['proposal']['acceptedPostId'] == result.json()['post']['id']
    assert result.json()['draft']['textProjection'] == 'Human original'
    assert callback(client, session, runner, 'research_post_propose', {**args, 'mutationId': str(uuid.uuid4())}).status_code == 409
    assert client.post(path, json=body, headers=headers(auth)).status_code == 409


def test_existing_post_proposal_atomic_cas_and_concurrent_accept(client, admin):
    owner, auth = identity(client, admin)
    post = commit(client, auth, make_draft(client, auth))
    draft = make_draft(client, auth, post, content='Human changes')
    session, runner = turn(client, auth, owner, {'kind': 'post', 'resourceId': post['id']})
    args = {**proposal_content(content='Codex changes'), 'postId': post['id'], 'expectedVersion': 1, 'mutationId': str(uuid.uuid4())}
    proposal = callback(client, session, runner, 'research_post_propose', args).json()
    assert proposal['status'] == 'pending'
    assert callback(client, session, runner, 'research_get', {'type': 'post', 'id': post['id']}).json()['latestVersion']['textProjection'] == 'Text'
    assert client.put(ROOT+'/'+draft['id'], json={**content(content='New human changes'), 'expectedDraftRevision': 1}, headers=headers(auth)).status_code == 200
    body = {**content(content='Reviewed merge'), 'expectedVersion': 1, 'resolvedBaseVersionId': post['latestVersion']['id'],
        'expectedProposalVersion': 1, 'draftId': draft['id'], 'expectedDraftRevision': 1}
    path = '/api/research/post-proposals/'+proposal['id']+'/accept'
    assert client.post(path, json=body, headers=headers(auth)).status_code == 409
    assert client.get('/api/research/posts/'+post['id']).json()['version'] == 1
    body['expectedDraftRevision'] = 2
    def accept(_): return client.post(path, json=body, headers=headers(auth)).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(accept, [1, 2])) == [200, 409]
    assert len(client.get('/api/research/posts/'+post['id']+'/versions').json()['items']) == 2
    assert client.get('/api/research/post-proposals/'+proposal['id']).json()['textProjection'] == 'Codex changes'
    assert client.get(ROOT+'/'+draft['id']).json()['textProjection'] == 'New human changes'
    assert callback(client, session, runner, 'research_archive', {'type': 'post', 'id': post['id'], 'expectedVersion': 2, 'mutationId': str(uuid.uuid4())}).status_code == 422


def test_new_document_save_requires_human_accept_and_owner(client, admin):
    owner, auth = identity(client, admin)
    session, runner = turn(client, auth, owner)
    args = {'title': 'Suggested document', 'content': 'Suggested original', 'mutationId': str(uuid.uuid4())}
    result = callback(client, session, runner, 'research_document_save', args)
    assert result.status_code == 200, result.text
    proposal = result.json()
    assert proposal['documentId'] is None and proposal['baseVersionId'] is None
    assert client.get('/api/research/documents').json()['items'] == []
    key = str(uuid.uuid4()); path = '/api/research/document-proposals/'+proposal['id']+'/accept'
    body = {'expectedProposalVersion': 1, 'title': 'Reviewed', 'content': 'Human accepted'}
    accepted = client.post(path, json=body, headers=headers(auth, key))
    assert accepted.status_code == 200, accepted.text
    assert client.post(path, json=body, headers=headers(auth, key)).json() == accepted.json()
    assert accepted.json()['proposal']['content'] == 'Suggested original'
    assert accepted.json()['document']['content'] == 'Human accepted'
    assert accepted.json()['proposal']['acceptedDocumentId'] == accepted.json()['document']['id']
    assert len(client.get('/api/research/documents').json()['items']) == 1
    _, other = identity(client, admin, '222222')
    assert client.get('/api/research/document-proposals/'+proposal['id']).status_code == 404
    assert client.post(path, json=body, headers=headers(other, key)).status_code == 404
    assert client.get('/api/research/document-proposals').json()['items'] == []


def test_foreign_draft_and_discard_do_not_mutate_head(client, admin):
    owner, auth = identity(client, admin)
    draft = make_draft(client, auth)
    owner_cookie = client.cookies.get('anxious_session')
    session, runner = turn(client, auth, owner)
    args = {**proposal_content(), 'mutationId': str(uuid.uuid4())}
    proposal = callback(client, session, runner, 'research_post_propose', args).json()
    other, other_auth = identity(client, admin, '222222')
    other_session, other_runner = turn(client, other_auth, other)
    refused = callback(client, other_session, other_runner, 'research_post_propose',
        {**args, 'mutationId': str(uuid.uuid4()), 'draftId': draft['id'], 'expectedDraftRevision': 1})
    assert refused.status_code == 404
    assert client.get('/api/research/post-proposals/'+proposal['id']).status_code == 404
    client.cookies.set('anxious_session', owner_cookie, path='/api')
    path = '/api/research/post-proposals/'+proposal['id']+'/discard'
    body = {'expectedProposalVersion': 1}
    assert client.post(path, json=body, headers=headers(auth)).json()['status'] == 'discarded'
    assert client.post(path, json=body, headers=headers(auth)).status_code == 409
    assert client.get('/api/research/posts').json()['items'] == []
    assert client.get(ROOT+'/'+draft['id']).json()['state'] == 'active'


def test_draft_slug_in_progress_and_workspace_hint_validate_owner(client, admin):
    owner, auth = identity(client, admin)
    draft = make_draft(client, auth, slug='docker-basics-')
    assert draft['slug'] == ''  # Supplied new-post slug is ignored.
    session, runner = turn(client, auth, owner, {'kind': 'post_workspace', 'draftId': draft['id']})
    assert runner.last['scope']['context']['draft']['id'] == draft['id']
    assert runner.last['scope']['page']['draftId'] == draft['id']
    other, auth2 = identity(client, admin, '222222')
    other_session = create_session(client, auth2)
    HeldRunner(client, other)
    reply = client.post('/api/research/sessions/'+other_session['id']+'/messages',
        json={'text': 'read draft', 'expectedVersion': 1, 'page': {'kind': 'post_workspace', 'draftId': draft['id']}}, headers=headers(auth2))
    assert reply.status_code == 404
    reply = client.post('/api/research/sessions/'+other_session['id']+'/messages',
        json={'text': 'read draft', 'expectedVersion': 1, 'page': {'kind': 'general', 'draftId': draft['id']}}, headers=headers(auth2))
    assert reply.status_code == 422
