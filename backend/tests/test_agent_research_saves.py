"""Review-owned Docker/PostgreSQL gate; runner is held, no real provider/MCP here."""
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.migrate import migrate
from app.research import Store
from app.research_results import success
from tests.test_research import create, identity, headers, material
from tests.test_conversations import create_session
from tests.test_page_chat import HeldRunner, callback
from tests.test_posts import make_draft, commit, proposal_content
from tests.test_policy_publication import publish, connect


@pytest.fixture(scope='module', autouse=True)
def authors_schema(schema):
    migrate('0008_version_authors')


def turn(client, owner, auth, page=None):
    runner = HeldRunner(client, owner)
    session = create_session(client, auth)
    reply = client.post('/api/research/sessions/' + session['id'] + '/messages',
        json={'text': 'Save private research', 'expectedVersion': 1,
              'page': page or {'kind': 'general'}}, headers=headers(auth))
    assert reply.status_code == 202, reply.text
    return session, runner


def test_document_direct_save_authors_conflicts_retry_and_human_draft(client, admin):
    owner, auth = identity(client, admin)
    doc = create(client, auth, 'documents')
    draft_body = {'documentId': doc['id'], 'baseDocumentVersion': 1,
                  'baseVersionId': doc['latestVersion']['id'], 'title': 'Human', 'content': 'Dirty human draft'}
    draft = client.post('/api/research/document-drafts', json=draft_body, headers=headers(auth)).json()
    session, runner = turn(client, owner, auth)
    args = {'id': doc['id'], 'expectedVersion': 1, 'title': 'Codex', 'content': 'Saved source', 'mutationId': str(uuid.uuid4())}
    result = callback(client, session, runner, 'research_document_update', args)
    assert result.status_code == 200, result.text
    saved = result.json()
    assert success('research_document_update', saved, args)
    assert saved['version'] == saved['latestVersion']['number'] == 2
    assert saved['latestVersion']['author'] == {'kind': 'codex', 'conversationId': session['id']}
    assert saved['saved']['versionId'] == saved['latestVersion']['id']
    assert callback(client, session, runner, 'research_document_update', args).json() == saved
    conflict = callback(client, session, runner, 'research_document_update', {**args, 'mutationId': str(uuid.uuid4())})
    assert conflict.status_code == 409
    assert conflict.json()['error']['details']['current']['latestVersion']['id'] == saved['latestVersion']['id']
    assert client.get('/api/research/document-drafts/' + draft['id']).json() == draft
    versions = client.get('/api/research/documents/' + doc['id'] + '/versions').json()['items']
    assert [version['author']['kind'] for version in versions] == ['codex', 'human']
    human = client.patch('/api/research/documents/' + doc['id'],
                       json={'title': 'Restored', 'content': doc['content'], 'expectedVersion': 2}, headers=headers(auth))
    assert human.status_code == 200, human.text
    assert human.json()['latestVersion']['author'] == {'kind': 'human', 'conversationId': None}
    assert client.get('/api/research/document-proposals').json()['items'] == []


def test_new_post_save_preserves_draft_and_rejects_old_propose_publish_and_archive(client, admin):
    owner, auth = identity(client, admin)
    draft = make_draft(client, auth, content='Human original')
    session, runner = turn(client, owner, auth)
    args = {**proposal_content(content='Codex source'), 'mutationId': str(uuid.uuid4()),
            'draftId': draft['id'], 'expectedDraftRevision': 1}
    result = callback(client, session, runner, 'research_post_save', args)
    assert result.status_code == 200, result.text
    post = result.json()
    assert success('research_post_save', post, args)
    assert post['latestVersion']['textProjection'] == 'Codex source'
    assert post['latestVersion']['author'] == {'kind': 'codex', 'conversationId': session['id']}
    assert post['saved']['destination']['path'] == '/write/' and post['publication'] is None
    assert client.get('/api/research/post-drafts/' + draft['id']).json() == draft
    assert callback(client, session, runner, 'research_post_save', args).json() == post
    assert client.get('/api/research/post-proposals').json()['items'] == []
    assert callback(client, session, runner, 'research_post_propose', args).status_code == 403
    for name in ('research_post_publish', 'research_post_withdraw'):
        assert callback(client, session, runner, name, {}).status_code == 403
    assert callback(client, session, runner, 'research_archive', {
        'type': 'post', 'id': post['id'], 'expectedVersion': 1, 'mutationId': str(uuid.uuid4())}).status_code == 422


def test_agent_versions_do_not_modify_public_snapshots_or_expose_conversation_in_public_materials(client, admin):
    owner, auth = identity(client, admin)
    session, runner = turn(client, owner, auth)
    args = {**material(), 'mutationId': str(uuid.uuid4())}
    source = callback(client, session, runner, 'research_material_save', args).json()
    assert success('research_material_save', source, args)
    doc = create(client, auth, 'documents')
    connect(client, auth, doc, source)
    publication = publish(client, auth, doc)
    before = client.get('/api/public/documents/' + doc['id']).json()
    assert all('author' not in item for item in before['materials'])
    changed = callback(client, session, runner, 'research_document_update', {
        'id': doc['id'], 'expectedVersion': publication['expectedVersion'] if 'expectedVersion' in publication else 2,
        'title': 'New private title', 'content': 'Private body', 'mutationId': str(uuid.uuid4())})
    assert changed.status_code == 200, changed.text
    assert client.get('/api/public/documents/' + doc['id']).json() == before


def test_existing_pending_proposal_can_be_accepted_after_transition(client, admin):
    from app.document_proposals import create as legacy_proposal
    from app.research_tools import SaveDocument
    owner, auth = identity(client, admin)
    # Seed a retained pre-transition proposal through the legacy internal helper.
    # No HTTP/tool route exposes proposal creation after #97.
    with Session(admin) as db, db.begin():
        pending = legacy_proposal(Store(db, owner),
            SaveDocument(title='Retained proposal', content='Old source', mutationId=str(uuid.uuid4())))
    assert client.get('/api/research/document-proposals/' + pending['id']).json()['status'] == 'pending'
    accepted = client.post('/api/research/document-proposals/' + pending['id'] + '/accept',
        json={'title': 'Accepted old proposal', 'content': 'Old source', 'expectedProposalVersion': 1}, headers=headers(auth))
    assert accepted.status_code == 200, accepted.text
    with admin.connect() as db:
        assert db.execute(text('SELECT count(*) FROM research.document_proposals')).scalar_one() == 1


def test_foreign_and_public_context_cannot_write(client, admin):
    first, auth = identity(client, admin)
    doc = create(client, auth, 'documents')
    publish(client, auth, doc)
    other, other_auth = identity(client, admin, '222222')
    session, runner = turn(client, other, other_auth)
    args = {'id': doc['id'], 'expectedVersion': 2, 'title': 'Foreign', 'content': 'Denied', 'mutationId': str(uuid.uuid4())}
    assert callback(client, session, runner, 'research_document_update', args).status_code == 404
    public, public_runner = turn(client, other, other_auth, {'kind': 'public_document', 'resourceId': doc['id']})
    assert callback(client, public, public_runner, 'research_document_update', args).status_code == 403
    assert callback(client, public, public_runner, 'research_page_context', {}).status_code == 200


def test_every_mutation_real_response_satisfies_shared_terminal_contract(client, admin):
    owner, auth = identity(client, admin)
    session, runner = turn(client, owner, auth)
    def checked(name, args):
        body = {**args, 'mutationId': str(uuid.uuid4())}
        reply = callback(client, session, runner, name, body)
        assert reply.status_code == 200, reply.text
        result = reply.json()
        assert success(name, result, body), (name, 'callback contract mismatch')
        assert success(name, result), (name, 'stored result contract mismatch')
        return result
    document = checked('research_document_save', {'title': 'Document', 'content': 'Sources'})
    source = checked('research_material_save', material())
    checked('research_document_update', {'id': document['id'], 'expectedVersion': 1,
        'title': 'Revised document', 'content': 'Updated source'})
    checked('research_material_update', {**material(), 'id': source['id'], 'expectedVersion': 1})
    relation = checked('research_relation_save', {'source': {'type': 'document', 'id': document['id']},
        'target': {'type': 'material', 'id': source['id']}, 'kind': 'cites', 'description': '', 'directed': True})
    revised = checked('research_relation_update', {'id': relation['id'], 'expectedVersion': 1,
        'kind': 'supports', 'description': 'Verified source'})
    checked('research_post_save', {**proposal_content(content='Cited source'), 'documentIds': [document['id']]})
    checked('research_archive', {'type': 'relation', 'id': relation['id'], 'expectedVersion': revised['version']})
