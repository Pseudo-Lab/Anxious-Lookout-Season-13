"""Real PostgreSQL post writing: owner boundaries, CAS and immutable history."""
import uuid
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import text
from app.migrate import migrate
from tests.test_research import identity, headers, create

ROOT = '/api/research/post-drafts'


@pytest.fixture(scope='module', autouse=True)
def posts_schema(schema):
    migrate('0007_post_blocks')


def content(**changes):
    from app.post_block_markdown import convert
    value = {'title': 'Post', 'content': '# Text', 'description': 'Summary', 'slug': '',
             'tags': ['one'], 'coverImageId': None, 'documentIds': [], **changes}
    if isinstance(value['content'], str): value['content'] = convert(value['content'], 'test-input')[0]
    return value


def proposal_content(**changes):
    markdown = changes.pop('content', '# Text')
    value = content(**changes)
    value.pop('content')
    value['contentInput'] = {'format': 'markdown', 'text': markdown}
    return value


def make_draft(client, auth, post=None, **changes):
    body = content(**changes)
    if post:
        body.update(postId=post['id'], basePostVersion=post['version'], baseVersionId=post['latestVersion']['id'])
    result = client.post(ROOT, json=body, headers=headers(auth))
    assert result.status_code == 201, result.text
    return result.json()


def commit(client, auth, draft, **changes):
    result = client.post(ROOT+'/'+draft['id']+'/commit', json={'expectedDraftRevision': draft['revision'], **changes}, headers=headers(auth))
    assert result.status_code == 200, result.text
    return result.json()['post']


def test_new_draft_autosave_commit_replay_and_history(client, admin):
    _, auth = identity(client, admin)
    draft = make_draft(client, auth, title='', content='', slug='')
    assert client.get('/api/research/posts').json()['items'] == []
    assert client.get(ROOT).json()['items'][0]['id'] == draft['id']
    assert 'content' not in client.get(ROOT).json()['items'][0]
    path = ROOT+'/'+draft['id']
    assert client.post(path+'/commit', json={'expectedDraftRevision': 1}, headers=headers(auth)).status_code == 422
    updated = client.put(path, json={'expectedDraftRevision': 1, **content(tags=['one', ' one '])}, headers=headers(auth))
    assert updated.status_code == 200, updated.text
    assert updated.json()['revision'] == 2 and updated.json()['tags'] == ['one']
    key = str(uuid.uuid4()); body = {'expectedDraftRevision': 2}
    result = client.post(path+'/commit', json=body, headers=headers(auth, key))
    assert result.status_code == 200, result.text
    assert client.post(path+'/commit', json=body, headers=headers(auth, key)).json() == result.json()
    post = result.json()['post']
    assert post['version'] == post['latestVersion']['number'] == 1
    assert client.get(ROOT).json()['items'] == []
    assert client.get(path).json()['state'] == 'committed'
    assert client.put(path, json={'expectedDraftRevision': 3, **content()}, headers=headers(auth)).status_code == 409
    second = make_draft(client, auth, post, content='Next text')
    latest = commit(client, auth, second, expectedVersion=1)
    assert latest['version'] == latest['latestVersion']['number'] == 2
    old = client.get('/api/research/posts/'+post['id']+'/versions/'+post['latestVersion']['id']).json()
    assert old['textProjection'] == 'Text'
    assert client.get('/api/research/posts?q=Post').json()['items'][0]['id'] == post['id']
    versions = client.get('/api/research/posts/'+post['id']+'/versions?limit=1').json()
    assert versions['items'][0]['number'] == 2 and 'content' not in versions['items'][0]
    assert client.get('/api/research/posts/'+post['id']+'/versions?cursor='+versions['nextCursor']).json()['items'][0]['number'] == 1


def test_owner_references_search_and_archive(client, admin):
    owner, auth = identity(client, admin)
    owner_cookie = client.cookies.get('anxious_session')
    document = create(client, auth, 'documents', title='100% source')
    draft = make_draft(client, auth, documentIds=[document['id']])
    post = commit(client, auth, draft)
    options = client.get('/api/research/post-document-options?q=%25').json()
    assert options['items'][0]['id'] == document['id']
    _, second = identity(client, admin, '222222')
    assert client.get('/api/research/posts/'+post['id']).status_code == 404
    assert client.get(ROOT+'/'+draft['id']).status_code == 404
    assert client.get('/api/research/post-document-options').json()['items'] == []
    assert client.post(ROOT, json=content(documentIds=[document['id']]), headers=headers(second)).status_code == 404
    assert client.get('/api/research/posts').json()['items'] == []
    # Restore the first owner's session and archive the referenced document.
    client.cookies.set('anxious_session', owner_cookie, path='/api')
    client.request('DELETE', '/api/research/documents/'+document['id'], json={'expectedVersion': 1}, headers=headers(auth))
    assert client.post(ROOT, json=content(documentIds=[document['id']]), headers=headers(auth)).status_code == 422
    assert client.get('/api/research/posts/'+post['id']).json()['latestVersion']['documentIds'] == [document['id']]
    active = make_draft(client, auth, post)
    archived = client.request('DELETE', '/api/research/posts/'+post['id'], json={'expectedVersion': 1}, headers=headers(auth))
    assert archived.status_code == 200, archived.text
    assert client.get('/api/research/posts').json()['items'] == []
    assert client.put(ROOT+'/'+active['id'], json={'expectedDraftRevision': 1, **content()}, headers=headers(auth)).status_code == 409
    assert client.request('DELETE', ROOT+'/'+active['id'], json={'expectedDraftRevision': 1}, headers=headers(auth)).json()['state'] == 'discarded'


def test_cas_active_draft_concurrency_and_slug_reservation(client, admin):
    _, auth = identity(client, admin)
    draft = make_draft(client, auth)
    path = ROOT+'/'+draft['id']
    def save(value):
        return client.put(path, json={'expectedDraftRevision': 1, **content(content=value)}, headers=headers(auth)).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(save, ['first', 'second'])) == [200, 409]
    current = client.get(path).json()
    def finish(_):
        return client.post(path+'/commit', json={'expectedDraftRevision': current['revision']}, headers=headers(auth)).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(finish, [1, 2])) == [200, 409]
    post = client.get('/api/research/posts').json()['items'][0]
    assert len(client.get('/api/research/posts/'+post['id']+'/versions').json()['items']) == 1
    active = make_draft(client, auth, post)
    assert client.post(ROOT, json={**content(), 'postId': post['id'], 'basePostVersion': 1,
        'baseVersionId': post['latestVersion']['id']}, headers=headers(auth)).json()['error']['details']['reason'] == 'active_draft_exists'
    assert client.put(ROOT+'/'+active['id'], json={'expectedDraftRevision': 1, **content(slug='rename')}, headers=headers(auth)).status_code == 422
    _, other = identity(client, admin, '222222')
    duplicate = make_draft(client, other)
    result = client.post(ROOT+'/'+duplicate['id']+'/commit', json={'expectedDraftRevision': 1}, headers=headers(other))
    assert result.status_code == 200, result.text
    assert result.json()['post']['latestVersion']['slug'] != post['latestVersion']['slug']
    assert client.get(ROOT+'/'+duplicate['id']).json()['state'] == 'committed'


def test_stale_base_explicit_resolution_and_transaction_rollback(client, admin, monkeypatch):
    from app import posts
    owner, auth = identity(client, admin)
    initial = make_draft(client, auth)
    post = commit(client, auth, initial)
    draft = make_draft(client, auth, post, content='local')
    # Simulate another committed writer before this draft is explicitly committed.
    from app.research import Store
    from sqlalchemy.orm import Session
    with Session(admin) as db, db.begin():
        store = Store(db, owner)
        store.graph_lock()
        posts.save_version(store, content(content='other'), posts.owned(store, post['id']))
    latest = client.get('/api/research/posts/'+post['id']).json()
    path = ROOT+'/'+draft['id']+'/commit'
    body = {'expectedDraftRevision': 1, 'expectedVersion': latest['version']}
    assert client.post(path, json=body, headers=headers(auth)).json()['error']['details']['reason'] == 'base_changed'
    body['resolvedBaseVersionId'] = latest['latestVersion']['id']
    original = posts.save_version
    def fail_after_write(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('synthetic transaction failure')
    monkeypatch.setattr(posts, 'save_version', fail_after_write)
    key = str(uuid.uuid4())
    assert client.post(path, json=body, headers=headers(auth, key)).status_code == 500
    assert client.get(ROOT+'/'+draft['id']).json()['state'] == 'active'
    assert client.get('/api/research/posts/'+post['id']).json()['version'] == 2
    monkeypatch.setattr(posts, 'save_version', original)
    result = client.post(path, json=body, headers=headers(auth, key))
    assert result.status_code == 200 and result.json()['post']['latestVersion']['number'] == 3


def test_bad_input_csrf_and_schema_grants(client, admin):
    _, auth = identity(client, admin)
    assert client.post(ROOT, json=content(), headers={'Idempotency-Key': str(uuid.uuid4())}).status_code == 403
    for changes in ({'tags': ['\x00']}, {'documentIds': ['nope']}, {'ownerId': str(uuid.uuid4())}):
        assert client.post(ROOT, json=content(**changes), headers=headers(auth)).status_code == 422
    assert client.get('/api/research/posts?limit=101').status_code == 422
    assert client.get('/api/research/posts?cursor=bad').status_code == 422
    with admin.begin() as db:
        db.execute(text('GRANT UPDATE ON research.post_versions TO anxious_api'))
    try:
        assert client.get('/api/research/health').status_code == 503
        assert client.post(ROOT, json=content(), headers=headers(auth)).status_code == 503
    finally:
        with admin.begin() as db:
            db.execute(text('REVOKE UPDATE ON research.post_versions FROM anxious_api'))
    assert client.get('/api/research/health').status_code == 200


@pytest.mark.parametrize('post_id', ['', 'not-a-uuid'])
def test_invalid_draft_post_id_is_validation_error_without_writes(client, admin, post_id):
    _, auth = identity(client, admin)
    key = str(uuid.uuid4())
    result = client.post(ROOT, json={**content(), 'postId': post_id,
        'basePostVersion': 1, 'baseVersionId': str(uuid.uuid4())}, headers=headers(auth, key))
    assert result.status_code == 422, result.text
    assert result.json()['error']['code'] == 'validation_error'
    with admin.connect() as db:
        assert db.execute(text('SELECT count(*) FROM research.post_drafts')).scalar_one() == 0
        assert db.execute(text('SELECT count(*) FROM research.idempotency WHERE key=:key'), {'key': key}).scalar_one() == 0
    # A rejected payload must not consume the key or prevent a valid new draft.
    assert client.post(ROOT, json=content(), headers=headers(auth, key)).status_code == 201


def test_old_marker_fails_closed_for_post_writes(client, admin):
    _, auth = identity(client, admin)
    with admin.begin() as db:
        marker = db.execute(text('SELECT version_num FROM research.alembic_version')).scalar_one()
        db.execute(text("UPDATE research.alembic_version SET version_num='0005_writing'"))
    try:
        assert client.post(ROOT, json=content(), headers=headers(auth)).status_code == 503
        assert client.get('/api/research/posts').status_code == 503
        # 0006 changes the proposal schema too; a false older marker is rejected globally.
        assert client.get('/api/research/documents').status_code == 503
    finally:
        with admin.begin() as db:
            db.execute(text('UPDATE research.alembic_version SET version_num=:marker'), {'marker': marker})
