"""Real API publication immutability, reference previews, cache and public scope."""
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
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


def moment(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


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
    assert release['updatedAt'] == release['publishedAt']
    republished = publish(client, auth, changed)
    assert republished['textProjection'] == 'Private revision'
    # #80: a republish keeps the first publishedAt and stamps its own time as updatedAt.
    assert republished['publishedAt'] == release['publishedAt']
    assert moment(republished['updatedAt']) > moment(release['updatedAt'])
    assert client.get(url).json()['updatedAt'] == republished['updatedAt']
    listed = client.get('/api/public/posts').json()['items'][0]
    assert (listed['publishedAt'], listed['updatedAt']) == (release['publishedAt'], republished['updatedAt'])
    owned = client.get('/api/research/posts/'+post['id']).json()['publication']
    assert (owned['publishedAt'], owned['updatedAt']) == (release['publishedAt'], republished['updatedAt'])
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


def test_publish_after_withdraw_starts_a_new_published_at(client, admin):
    _, auth = identity(client, admin)
    post = commit(client, auth, make_draft(client, auth))
    first = publish(client, auth, post)
    removed = client.request('DELETE', '/api/research/posts/'+post['id']+'/publication', json={'expectedVersion': 2}, headers=headers(auth))
    assert removed.status_code == 200, removed.text
    assert preview(client, post)['currentPublication'] is None
    again = publish(client, auth, post)
    assert again['id'] != first['id'] and again['publishedAt'] == again['updatedAt']
    assert moment(again['publishedAt']) > moment(first['publishedAt'])


def test_snapshot_without_updated_at_reads_published_at(client, admin):
    _, auth = identity(client, admin)
    post = commit(client, auth, make_draft(client, auth))
    release = publish(client, auth, post)
    with admin.begin() as db:
        db.execute(text("UPDATE research.post_publications SET snapshot=snapshot-'updatedAt' WHERE id=:id"), {'id': release['id']})
    shown = client.get('/api/public/posts/'+post['latestVersion']['slug']).json()
    assert shown['updatedAt'] == shown['publishedAt'] == release['publishedAt']
    later = commit(client, auth, make_draft(client, auth, client.get('/api/research/posts/'+post['id']).json(), content='Later'), expectedVersion=2)
    assert publish(client, auth, later)['publishedAt'] == release['publishedAt']


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
    # #119 F4: a private reference is left out of every PublicPost (title included); the owner still sees it.
    assert release['documents'] == [] and 'Changed reference' not in json.dumps(release)
    owned = client.get('/api/research/posts/'+post['id']).json()['publication']['documents']
    assert owned == [{'id': doc['id'], 'title': 'Changed reference', 'public': False, 'publicUrl': None}]
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
                raw = (Path(__file__).resolve().parents[1]/'db/legacy-posts'/(snapshot['slug']+'.mdx')).read_text()
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
        # Populate the actual 0005 schema with raw SQL. The current ORM includes
        # 0008 author columns and must not be used to invent pre-upgrade rows.
        with candidate.begin() as db:
            owner = db.execute(text("INSERT INTO auth.accounts(github_id,login,is_approved,role) VALUES('555555','upgrade',true,'editor') RETURNING id")).scalar_one()
            document_id = db.execute(text("INSERT INTO research.items(owner_id,kind,version) VALUES(:owner,'document',2) RETURNING id"), {'owner':owner}).scalar_one()
            version_id = db.execute(text("INSERT INTO research.versions(item_id,owner_id,kind,number,title,content) VALUES(:item,:owner,'document',1,'Existing document','Original 0005 words') RETURNING id"), {'item':document_id,'owner':owner}).scalar_one()
            draft_id = db.execute(text("INSERT INTO research.document_drafts(owner_id,document_id,base_document_version,base_version_id,title,content) VALUES(:owner,:item,1,:version,'Uncommitted','Draft to preserve') RETURNING id"), {'owner':owner,'item':document_id,'version':version_id}).scalar_one()
            release_id = uuid.uuid4()
            released = {'id':str(release_id),'documentId':str(document_id),'versionId':str(version_id),
                        'title':'Existing document','content':'Original 0005 words','publishedAt':'2026-11-16T00:00:00Z',
                        'materials':[],'author':{'login':'upgrade'}}
            db.execute(text('INSERT INTO research.publications(id,document_id,owner_id,version_id,snapshot) VALUES(:id,:doc,:owner,:version,CAST(:snapshot AS jsonb))'),
                       {'id':release_id,'doc':document_id,'owner':owner,'version':version_id,'snapshot':json.dumps(released)})
            db.execute(text('INSERT INTO research.publication_heads(document_id,owner_id,publication_id) VALUES(:doc,:owner,:id)'),
                       {'doc':document_id,'owner':owner,'id':release_id})
            doc, draft = {'id':str(document_id)}, {'id':str(draft_id)}
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


def test_issue119_reader_documents_follow_the_live_document_publication(client, admin):
    """#119 F4: private references are omitted; publishing the document later links it; withdrawing hides it again."""
    from tests.test_policy_publication import publish as publish_document
    _, auth = identity(client, admin)
    doc = create(client, auth, 'documents', content='reference body')
    post = commit(client, auth, make_draft(client, auth, documentIds=[doc['id']]))
    release = publish(client, auth, post)
    url = '/api/public/posts/'+release['slug']
    assert release['documents'] == [] and client.get(url).json()['documents'] == []
    publish_document(client, auth, doc)
    linked = client.get(url).json()['documents']
    assert [value['id'] for value in linked] == [doc['id']] and linked[0]['publicUrl'].endswith('/public/document/?id='+doc['id'])
    assert client.get('/api/public/posts', params={'q': release['title']}).json()['items'][0]['documents'] == linked
    owned = client.get('/api/research/posts/'+post['id']).json()['publication']['documents']
    assert owned == [{**linked[0], 'public': True}]
    version = client.get('/api/research/documents/'+doc['id']).json()['version']
    withdrawn = client.request('DELETE', '/api/research/documents/'+doc['id']+'/publication', json={'expectedVersion': version}, headers=headers(auth))
    assert withdrawn.status_code == 200, withdrawn.text
    assert client.get(url).json()['documents'] == []
    assert client.get('/api/research/posts/'+post['id']).json()['publication']['documents'][0]['public'] is False


def test_issue119_related_neighbours_by_first_publication_and_same_tag(client, admin):
    """#119 F2: previous/next by first publishedAt (a republish keeps its place); sameTag shares a tag."""
    _, auth = identity(client, admin)
    tag = 'related-'+uuid.uuid4().hex[:8]
    first = commit(client, auth, make_draft(client, auth, title='First', tags=[tag]))
    second = commit(client, auth, make_draft(client, auth, title='Second', tags=['elsewhere']))
    third = commit(client, auth, make_draft(client, auth, title='Third', tags=[tag, 'other']))
    slugs = [publish(client, auth, post)['slug'] for post in (first, second, third)]
    middle = client.get('/api/public/posts/'+slugs[1]+'/related')
    assert middle.status_code == 200 and middle.headers['cache-control'] == 'public, max-age=0, must-revalidate'
    assert middle.json()['previous']['slug'] == slugs[0] and middle.json()['next']['slug'] == slugs[2]
    assert set(middle.json()['previous']) == {'slug', 'title', 'publishedAt', 'tags', 'author'}
    assert middle.json()['sameTag'] == [] or all(slugs[1] != value['slug'] for value in middle.json()['sameTag'])
    last = client.get('/api/public/posts/'+slugs[2]+'/related').json()
    assert last['next'] is None and last['previous']['slug'] == slugs[1]
    assert [value['slug'] for value in last['sameTag'] if tag in value['tags']] == [slugs[0]]
    later = commit(client, auth, make_draft(client, auth, client.get('/api/research/posts/'+first['id']).json(), title='First again', tags=[tag]), expectedVersion=2)
    publish(client, auth, later)
    assert client.get('/api/public/posts/'+slugs[1]+'/related').json()['previous']['slug'] == slugs[0]
    assert client.get('/api/public/posts/missing-slug/related').status_code == 404


def test_issue119_post_authors_and_filters_on_postgresql(client, admin):
    """#119 A7 on a real database (review blocker: post-authors 503 from ORDER BY ... COLLATE)."""
    _, auth = identity(client, admin, number='119001')
    marker = 'authors-'+uuid.uuid4().hex[:8]
    release = publish(client, auth, commit(client, auth, make_draft(client, auth, title='Title '+marker, tags=[marker])))
    login = release['author']['login']
    reply = client.get('/api/public/post-authors')
    assert reply.status_code == 200, reply.text
    assert reply.headers['cache-control'] == 'public, max-age=0, must-revalidate' and reply.headers['etag']
    items = reply.json()['items']
    assert {'login': login, 'count': 1} in items and [item['login'] for item in items] == sorted(item['login'] for item in items)
    assert client.get('/api/public/post-authors', headers={'If-None-Match': reply.headers['etag']}).status_code == 304
    found = client.get('/api/public/posts', params={'q': marker.upper(), 'author': login}).json()
    assert found['total'] == 1 and found['items'][0]['slug'] == release['slug']
    assert client.get('/api/public/posts', params={'tag': marker}).json()['total'] == 1
    assert client.get('/api/public/posts', params={'q': '100%'}).json()['total'] == 0
    assert client.get('/api/public/post-tags', params={'q': marker.upper()}).json()['items'] == [{'tag': marker, 'count': 1}]


def test_issue119_related_for_seeded_posts_with_null_author_login(client, admin):
    """review major (bd3cbbb): seeded releases have author.login null; PostRef keeps both keys, login may be null.

    review r2: the admin fixture truncates accounts (CASCADE removes the ownerless seed rows), so this test does not
    rely on the seeds. It gives one of its own releases the seed author shape (asserted by the 0006 seed test above:
    snapshot author.login null) and reads it back through every path that reports authors."""
    _, auth = identity(client, admin, number='119002')
    tag = 'seedlike-'+uuid.uuid4().hex[:8]
    seeded = publish(client, auth, commit(client, auth, make_draft(client, auth, title='Seed-like', tags=[tag])))
    later = publish(client, auth, commit(client, auth, make_draft(client, auth, title='Later', tags=[tag])))
    with admin.begin() as db:
        db.execute(text('''UPDATE research.post_publications SET snapshot=jsonb_set(snapshot,'{author}',
            '{"login": null, "name": "초조한 전망대"}'::jsonb) WHERE id=:id'''), {'id': seeded['id']})
    assert client.get('/api/public/posts/'+seeded['slug']).json()['author'] == {'login': None, 'name': '초조한 전망대'}
    value = client.get('/api/public/posts/'+later['slug']+'/related').json()
    seed_refs = [ref for ref in [value['previous'], *value['sameTag']] if ref and ref['slug'] == seeded['slug']]
    assert len(seed_refs) == 2 and all(ref['author'] == {'login': None, 'name': '초조한 전망대'} for ref in seed_refs)
    back = client.get('/api/public/posts/'+seeded['slug']+'/related').json()
    assert back['next']['slug'] == later['slug'] and set(back['next']['author']) == {'login', 'name'}
    authors = client.get('/api/public/post-authors').json()['items']
    assert all(item['login'] is not None for item in authors)
    assert {'login': later['author']['login'], 'count': 1} in authors  # the seed-like release is not counted for anyone
