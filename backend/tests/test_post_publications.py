"""Real API publication immutability, reference previews, cache and public scope."""
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from app.migrate import migrate
from app.page_scope import shell_allowed
from tests.test_research import identity, headers, create
from tests.test_posts import make_draft, commit
from tests.test_post_proposals import turn
from tests.test_page_chat import callback


@pytest.fixture(scope='module', autouse=True)
def public_schema(schema):
    migrate('0007_post_blocks')


def preview(client, post):
    result = client.get('/api/research/posts/'+post['id']+'/publication-preview', params={'versionId': post['latestVersion']['id']})
    assert result.status_code == 200, result.text
    return result.json()


def publish(client, auth, post):
    shown = preview(client, post)
    body = {key: shown[key] for key in ('expectedVersion', 'versionId', 'previewToken')}
    result = client.post('/api/research/posts/'+post['id']+'/publications', json=body, headers=headers(auth))
    assert result.status_code == 201, result.text
    return result.json()


def test_public_snapshot_edit_republish_cache_and_withdraw(client, admin):
    _, auth = identity(client, admin)
    post = commit(client, auth, make_draft(client, auth))
    release = publish(client, auth, post)
    url = '/api/public/posts/'+post['latestVersion']['slug']
    first = client.get(url)
    assert first.json() == release
    assert first.headers['cache-control'] == 'public, max-age=0, must-revalidate'
    assert client.get(url, headers={'If-None-Match': first.headers['etag']}).status_code == 304
    current = client.get('/api/research/posts/'+post['id']).json()
    assert current['version'] == 2 and current['publication']['id'] == release['id']
    changed = commit(client, auth, make_draft(client, auth, current, content='Private revision'), expectedVersion=2)
    assert client.get(url).json()['textProjection'] == 'Text'
    assert preview(client, changed)['currentPublication'] == release
    assert publish(client, auth, changed)['textProjection'] == 'Private revision'
    assert client.get(url, headers={'If-None-Match': first.headers['etag']}).status_code == 200
    assert client.get('/api/public/posts?tag=one').json()['items'][0]['author']['name'].startswith('fixture-')
    assert 'content' not in client.get('/api/public/posts').json()['items'][0]
    assert client.get('/api/public/post-tags').json()['items'] == [{'tag': 'one', 'count': 1}]
    removed = client.request('DELETE', '/api/research/posts/'+post['id']+'/publication', json={'expectedVersion': 4}, headers=headers(auth))
    assert removed.status_code == 200, removed.text
    client.cookies.clear()
    assert client.get(url, headers={'If-None-Match': first.headers['etag']}).status_code == 404
    assert client.get('/api/public/posts').json()['items'] == []
    assert client.get('/api/public/post-tags').json()['items'] == []


def test_preview_staleness_and_private_reference_body_never_public(client, admin):
    _, auth = identity(client, admin)
    doc = create(client, auth, 'documents', content='DO NOT PUBLISH PRIVATE SOURCE BODY')
    post = commit(client, auth, make_draft(client, auth, documentIds=[doc['id']]))
    shown = preview(client, post)
    assert shown['documents'] == [{'id': doc['id'], 'title': doc['title'], 'publicUrl': None}]
    assert 'DO NOT PUBLISH' not in json.dumps(shown)
    changed = client.patch('/api/research/documents/'+doc['id'], json={'expectedVersion': 1, 'title': 'Changed reference', 'content': doc['content']}, headers=headers(auth))
    assert changed.status_code == 200
    result = client.post('/api/research/posts/'+post['id']+'/publications',
        json={key: shown[key] for key in ('expectedVersion', 'versionId', 'previewToken')}, headers=headers(auth))
    assert result.status_code == 409 and result.json()['error']['details']['reason'] == 'preview_changed'
    release = publish(client, auth, post)
    assert release['documents'][0]['title'] == 'Changed reference'
    assert 'DO NOT PUBLISH' not in json.dumps(release)
    client.request('DELETE', '/api/research/documents/'+doc['id'], json={'expectedVersion': 2}, headers=headers(auth))
    assert client.get('/api/public/posts/'+post['latestVersion']['slug']).json() == release
    assert preview(client, post)['publishable'] is False
    assert client.get('/api/public/posts?q=Post').json()['items'][0]['postId'] == post['id']


def test_concurrent_publish_archive_and_pagination(client, admin):
    _, auth = identity(client, admin)
    first = commit(client, auth, make_draft(client, auth, tags=['a','b']))
    shown = preview(client, first)
    body = {key: shown[key] for key in ('expectedVersion', 'versionId', 'previewToken')}
    def do(_):
        return client.post('/api/research/posts/'+first['id']+'/publications', json=body, headers=headers(auth)).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(do, [1, 2])) == [201, 409]
    second = commit(client, auth, make_draft(client, auth, slug='second', tags=['b','c']))
    publish(client, auth, second)
    listing = client.get('/api/public/posts?limit=1').json()
    assert len(listing['items']) == 1 and listing['nextCursor']
    assert len(client.get('/api/public/posts', params={'cursor': listing['nextCursor']}).json()['items']) == 1
    tags = client.get('/api/public/post-tags?limit=1').json()
    assert tags['items'] == [{'tag': 'a', 'count': 1}]
    assert client.get('/api/public/post-tags', params={'cursor': tags['nextCursor']}).json()['items'] == [{'tag':'b','count':2},{'tag':'c','count':1}]
    assert client.get('/api/public/post-tags?cursor=bad').status_code == 422
    assert client.request('DELETE', '/api/research/posts/'+first['id'], json={'expectedVersion': 2}, headers=headers(auth)).status_code == 200
    assert client.get('/api/public/posts/'+first['latestVersion']['slug']).status_code == 404
    assert len(client.get('/api/public/posts').json()['items']) == 1


def test_public_post_context_rejects_owner_tools_and_survives_current_withdraw(client, admin):
    owner, auth = identity(client, admin)
    post = commit(client, auth, make_draft(client, auth))
    release = publish(client, auth, post)
    session, runner = turn(client, auth, owner, {'kind': 'public_post', 'resourceId': post['id']})
    assert not shell_allowed(runner.last['scope'])
    assert callback(client, session, runner, 'research_page_context', {}).json() == release
    assert callback(client, session, runner, 'research_post_draft_get', {'postId': post['id']}).status_code == 403
    assert callback(client, session, runner, 'research_get', {'type': 'post', 'id': post['id']}).status_code == 403
    client.request('DELETE', '/api/research/posts/'+post['id']+'/publication', json={'expectedVersion': 2}, headers=headers(auth))
    assert callback(client, session, runner, 'research_page_context', {}).json() == release


def test_legacy_seed_import_is_exact_and_migration_replay_is_idempotent(admin, monkeypatch):
    database = 'restore_seed_'+uuid.uuid4().hex
    url = make_url(os.environ['ADMIN_DATABASE_URL']).set(database=database)
    candidate = None
    with admin.connect().execution_options(isolation_level='AUTOCOMMIT') as db:
        db.execute(text('CREATE DATABASE '+database))
    try:
        monkeypatch.setenv('ADMIN_DATABASE_URL', url.render_as_string(hide_password=False))
        migrate('0006_posts'); migrate('0006_posts')
        candidate = create_engine(url)
        with candidate.connect() as db:
            assert db.execute(text('SELECT count(*) FROM research.posts WHERE owner_id IS NULL AND legacy_import')).scalar_one() == 3
            rows = db.execute(text('SELECT snapshot FROM research.post_publications')).scalars().all()
            assert len(rows) == 3
            for snapshot in rows:
                raw = (Path('/source/db/legacy-posts')/(snapshot['slug']+'.mdx')).read_text()
                _, header, body = raw.split('---', 2)
                assert snapshot['content'] == body.lstrip('\n')
                assert snapshot['author']['login'] is None
                assert snapshot['slug'] in {'welcome-to-observatory','understanding-transformer-architecture','docker-basics-for-developers'}
    finally:
        if candidate: candidate.dispose()
        with admin.connect().execution_options(isolation_level='AUTOCOMMIT') as db:
            db.execute(text('DROP DATABASE '+database+' WITH (FORCE)'))


def test_populated_0005_upgrade_preserves_documents_drafts_and_public_snapshot(admin, monkeypatch):
    from sqlalchemy.orm import Session
    from app.research import Store, DocumentInput
    from app.writing import DraftCreate, create_draft
    from app.publications import preview as document_preview, publish as document_publish, Publish
    database = 'restore_upgrade_'+uuid.uuid4().hex
    url = make_url(os.environ['ADMIN_DATABASE_URL']).set(database=database)
    candidate = None
    with admin.connect().execution_options(isolation_level='AUTOCOMMIT') as db:
        db.execute(text('CREATE DATABASE '+database))
    try:
        monkeypatch.setenv('ADMIN_DATABASE_URL', url.render_as_string(hide_password=False))
        migrate('0005_writing')
        candidate = create_engine(url)
        with Session(candidate) as db, db.begin():
            owner = db.execute(text("INSERT INTO auth.accounts(github_id,login,is_approved,role) VALUES('555555','upgrade',true,'editor') RETURNING id")).scalar_one()
            store = Store(db, owner)
            doc = store.create_item('document', DocumentInput(title='Existing document', content='Original 0005 words'))
            draft = create_draft(store, DraftCreate(title='Uncommitted', content='Draft to preserve', documentId=doc['id'], baseDocumentVersion=1, baseVersionId=doc['latestVersion']['id']))
            shown = document_preview(store, doc['id'])
            released = document_publish(store, doc['id'], Publish(expectedVersion=1, versionId=shown['versionId'], previewToken=shown['previewToken']))
        migrate('0006_posts')
        with candidate.connect() as db:
            assert db.execute(text('SELECT content FROM research.versions WHERE item_id=:id'), {'id':doc['id']}).scalar_one() == 'Original 0005 words'
            assert db.execute(text('SELECT content,state FROM research.document_drafts WHERE id=:id'), {'id':draft['id']}).one() == ('Draft to preserve','active')
            assert db.execute(text('SELECT snapshot FROM research.publications WHERE id=:id'), {'id':released['id']}).scalar_one() == released
            assert db.execute(text('SELECT count(*) FROM research.post_publications')).scalar_one() == 3
    finally:
        if candidate: candidate.dispose()
        with admin.connect().execution_options(isolation_level='AUTOCOMMIT') as db:
            db.execute(text('DROP DATABASE '+database+' WITH (FORCE)'))
