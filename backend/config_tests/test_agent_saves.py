"""Pure unit boundaries; DB/HTTP transactions and actual sandbox/API are review's gates."""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.agent_saves import SavePost, save_post
from app.page_scope import tool_allowed
from app.research import ResearchError, Store
from app.research_tools import dispatch, definitions
from app.version_authors import VersionAuthor, saved, value


def resource(kind='document'):
    return {'id': str(uuid4()), 'title': 'Notes', 'version': 2,
            'latestVersion': {'id': str(uuid4()), 'number': 2, 'title': 'Notes'}}


def test_authorship_is_server_context_and_not_a_tool_argument():
    conversation = uuid4()
    assert VersionAuthor().columns() == {
        'author_kind': 'human', 'author_conversation_id': None, 'author_credential_id': None, 'author_name': None}
    assert VersionAuthor(conversation).columns()['author_conversation_id'] == conversation
    row = SimpleNamespace(author_kind='codex', author_conversation_id=conversation)
    assert value(row) == {'kind': 'codex', 'conversationId': str(conversation)}
    with pytest.raises(ValidationError):
        from app.research_tools import SaveDocument
        SaveDocument(title='A', content='B', mutationId=str(uuid4()), author={'kind': 'human'})


@pytest.mark.parametrize('kind,path', [('material', '/research/material/'),
                                    ('document', '/research/document/'), ('post', '/write/')])
def test_save_summary_links_the_exact_snapshot(kind, path):
    row = resource(kind)
    result = saved(row, kind)
    assert result['saved'] == {'resourceType': kind, 'id': row['id'], 'title': 'Notes',
        'versionId': row['latestVersion']['id'], 'versionNumber': 2,
        'destination': {'path': path, 'resourceId': row['id'], 'versionId': row['latestVersion']['id']}}


@pytest.mark.parametrize('operation,method', [('research_document_save', 'create_item'),
                                           ('research_document_update', 'change_item')])
def test_document_dispatch_uses_snapshot_store_and_never_proposal_handlers(operation, method):
    calls = []
    row = resource()
    class SnapshotStore:
        def mutate(self, key, op, arguments, action):
            calls.append((key, op))
            return action(), False
        def create_item(self, kind, body):
            calls.append(('create_item', kind, body.title)); return row
        def change_item(self, identity, kind, body):
            calls.append(('change_item', kind, body.title)); return row
    args = {'title': 'Direct', 'content': 'Citation', 'mutationId': str(uuid4())}
    if method == 'change_item': args.update(id=row['id'], expectedVersion=1)
    result = dispatch(SnapshotStore(), operation, args)
    assert result['saved']['versionId'] == row['latestVersion']['id']
    assert calls[-1] == (method, 'document', 'Direct')


def test_retired_post_proposal_and_public_writes_are_rejected():
    names = {entry['name'] for entry in definitions()}
    assert 'research_post_save' in names and 'research_post_propose' not in names
    with pytest.raises(ResearchError) as error:
        dispatch(None, 'research_post_propose', {})
    assert error.value.status == 422
    public = {'page': {'kind': 'public_document'}, 'context': {'materials': []}}
    assert not tool_allowed(public, 'research_post_save', {})
    assert not tool_allowed(public, 'research_document_save', {})
    assert not tool_allowed({'page': {'kind': 'general'}}, 'publish', {})


def post_body(**changes):
    return SavePost(title='Article', description='', slug='', tags=[], coverImageId=None,
                    documentIds=[], contentInput={'format': 'markdown', 'text': 'Source'},
                    mutationId=str(uuid4()), **changes)


def test_post_revision_conflict_returns_current_before_writing(monkeypatch):
    from app import posts
    post = SimpleNamespace(id=uuid4(), version=3)
    current = resource('post')
    monkeypatch.setattr(posts, 'ready', lambda store: None)
    monkeypatch.setattr(posts, 'owned', lambda *args, **kwargs: post)
    monkeypatch.setattr(posts, 'post_value', lambda *args: current)
    monkeypatch.setattr(posts, 'save_version', lambda *args: pytest.fail('Conflict must not write'))
    store = SimpleNamespace(graph_lock=lambda: None)
    with pytest.raises(ResearchError) as error:
        save_post(store, post_body(postId=str(post.id), expectedVersion=2))
    assert error.value.status == 409
    assert error.value.details == {'reason': 'resource_version', 'current': current}


def test_post_save_checks_draft_revision_and_preserves_the_human_draft(monkeypatch):
    from app import posts
    post = SimpleNamespace(id=uuid4(), version=1)
    draft = SimpleNamespace(id=uuid4(), post_id=post.id, revision=4, state='active', payload={'text': 'Human'})
    before = vars(draft).copy()
    current = resource('post')
    monkeypatch.setattr(posts, 'ready', lambda store: None)
    monkeypatch.setattr(posts, 'owned', lambda *args, **kwargs: post)
    monkeypatch.setattr(posts, 'draft', lambda *args: draft)
    monkeypatch.setattr(posts, 'draft_value', lambda row: {'revision': row.revision, 'state': row.state})
    writes = []
    monkeypatch.setattr(posts, 'save_version', lambda store, payload, row: writes.append(payload) or row)
    monkeypatch.setattr(posts, 'post_value', lambda *args: current)
    store = SimpleNamespace(owner=uuid4(), graph_lock=lambda: None)
    with pytest.raises(ResearchError) as error:
        save_post(store, post_body(postId=str(post.id), expectedVersion=1,
                                  draftId=str(draft.id), expectedDraftRevision=3))
    assert error.value.details == {'reason': 'draft_revision', 'currentDraft': {'revision': 4, 'state': 'active'}}
    assert not writes
    result = save_post(store, post_body(postId=str(post.id), expectedVersion=1,
                                       draftId=str(draft.id), expectedDraftRevision=4))
    assert result['saved']['resourceType'] == 'post' and len(writes) == 1
    assert vars(draft) == before


def test_document_conflict_returns_current_and_does_not_append_a_snapshot():
    row = SimpleNamespace(version=3)
    current = resource()
    store = Store(None, uuid4())
    store.graph_lock = lambda: None
    store.owned_item = lambda *args, **kwargs: row
    store.serialize_item = lambda row: current
    store.add_snapshot = lambda *args: pytest.fail('Conflict must not append')
    with pytest.raises(ResearchError) as error:
        store.change_item(uuid4(), 'document', SimpleNamespace(expectedVersion=2))
    assert error.value.status == 409 and error.value.details['current'] == current
