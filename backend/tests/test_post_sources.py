import json
import re
import uuid
import pytest
from sqlalchemy import text
from app.migrate import migrate
from tests.test_research import identity, headers, create, material
from tests.test_posts import make_draft, commit, content, ROOT
from tests.test_post_publications import preview, publish
from tests.test_post_proposals import turn
from tests.test_page_chat import callback


@pytest.fixture(scope='module', autouse=True)
def source_schema(schema):
    migrate('0006_posts')


def relate(client, auth, document, source):
    response = client.post('/api/research/relations', json={
        'source': {'type':'document', 'id':document['id']},
        'target': {'type':'material', 'id':source['id']}, 'kind':'source', 'directed':True}, headers=headers(auth))
    assert response.status_code == 201, response.text
    return response.json()


def test_slug_omission_collision_retry_and_existing_address(client, admin, monkeypatch):
    from app import posts
    _, auth = identity(client, admin)
    draft = make_draft(client, auth, slug='../ignored')
    assert draft['slug'] == ''
    first = commit(client, auth, draft)
    slug = first['latestVersion']['slug']
    assert re.fullmatch(r'post-[a-f0-9]{32}', slug)
    body = content(); body.pop('slug')
    created = client.post(ROOT, json=body, headers=headers(auth))
    assert created.status_code == 201, created.text
    second = created.json()
    identities = iter([uuid.UUID(slug[5:]), uuid.uuid4()])
    original = posts.uuid
    class Candidates:
        uuid4 = staticmethod(lambda: next(identities))
    monkeypatch.setattr(posts, 'uuid', Candidates)
    saved = commit(client, auth, second)
    assert saved['latestVersion']['slug'] != slug
    monkeypatch.setattr(posts, 'uuid', original)
    existing = make_draft(client, auth, first)
    assert existing['slug'] == slug
    assert commit(client, auth, existing, expectedVersion=1)['latestVersion']['slug'] == slug


def test_sources_auto_explicit_remove_and_public_snapshot(client, admin):
    _, auth = identity(client, admin)
    doc = create(client, auth, 'documents', title='Document')
    source = create(client, auth, title='Source', content='SECRET SOURCE BODY')
    manual = create(client, auth, title='Direct', sourceUrl='https://example.com/direct')
    relation = relate(client, auth, doc, source)
    resolved = client.post('/api/research/post-material-options/resolve', json={'documentIds':[doc['id']]}, headers=auth)
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()['items'][0]['documentIds'] == [doc['id']]
    assert client.post('/api/research/post-material-options/resolve', json={'documentIds':[doc['id']]}).status_code == 403
    draft = make_draft(client, auth, documentIds=[doc['id']])
    assert draft['materialIds'] == [source['id']]
    assert draft['materialSources'] == [{'materialId':source['id'], 'documentIds':[doc['id']]}]
    selected = make_draft(client, auth, documentIds=[doc['id']], materialIds=[manual['id'],source['id']])
    assert selected['materialSources'][0]['documentIds'] == []
    assert make_draft(client, auth, documentIds=[doc['id']], materialIds=[])['materialIds'] == []
    post = commit(client, auth, selected)
    shown = preview(client, post)
    assert [m['id'] for m in shown['materials']] == [manual['id'],source['id']]
    assert all(set(m) == {'id','title','sourceUrl'} for m in shown['materials'])
    released = publish(client, auth, post)
    assert 'SECRET SOURCE BODY' not in json.dumps(released)
    update = client.patch('/api/research/materials/'+source['id'], json={**material(title='Changed source'), 'expectedVersion':1}, headers=headers(auth))
    assert update.status_code == 200
    assert client.get('/api/public/posts/'+released['slug']).json() == released
    assert client.request('DELETE', '/api/research/relations/'+relation['id'], json={'expectedVersion':1}, headers=headers(auth)).status_code == 200
    stored = client.get('/api/research/posts/'+post['id']).json()['latestVersion']
    assert stored['materialIds'] == [manual['id'],source['id']]
    assert stored['materialSources'][1]['documentIds'] == [doc['id']]
    assert len(client.get('/api/research/post-material-options?q=Direct').json()['items']) == 1


def test_material_only_proposal_accept_stale_preview_archive_and_foreign(client, admin):
    owner, auth = identity(client, admin)
    source = create(client, auth)
    session, runner = turn(client, auth, owner)
    args = content(materialIds=[source['id']]); args.pop('slug')
    proposed = callback(client, session, runner, 'research_post_propose', {**args,'mutationId':str(uuid.uuid4())})
    assert proposed.status_code == 200, proposed.text
    assert proposed.json()['slug'] == '' and proposed.json()['materialSources'][0]['documentIds'] == []
    assert client.get('/api/research/posts').json()['items'] == []
    accepted = client.post('/api/research/post-proposals/'+proposed.json()['id']+'/accept', json={**args,'expectedProposalVersion':1}, headers=headers(auth))
    assert accepted.status_code == 200, accepted.text
    post = accepted.json()['post']; shown = preview(client, post)
    assert client.patch('/api/research/materials/'+source['id'], json={**material(title='New title'), 'expectedVersion':1}, headers=headers(auth)).status_code == 200
    stale = client.post('/api/research/posts/'+post['id']+'/publications', json={k:shown[k] for k in ('versionId','expectedVersion','previewToken')}, headers=headers(auth))
    assert stale.status_code == 409
    assert client.request('DELETE', '/api/research/materials/'+source['id'], json={'expectedVersion':2}, headers=headers(auth)).status_code == 200
    shown = preview(client, post); assert shown['publishable'] is False
    assert client.post('/api/research/posts/'+post['id']+'/publications', json={k:shown[k] for k in ('versionId','expectedVersion','previewToken')}, headers=headers(auth)).status_code == 422
    assert client.post(ROOT, json=content(materialIds=[source['id']]), headers=headers(auth)).status_code == 422
    _, other = identity(client, admin, '222222')
    assert client.post(ROOT, json=content(materialIds=[source['id']]), headers=headers(other)).status_code == 404


def test_legacy_payload_and_snapshot_are_read_without_rewriting(client, admin):
    _, auth = identity(client, admin)
    post = commit(client, auth, make_draft(client, auth))
    release = publish(client, auth, post)
    with admin.begin() as db:
        db.execute(text("UPDATE research.post_versions SET payload=payload-'materialIds'-'materialSources' WHERE post_id=:id"), {'id':post['id']})
        db.execute(text("UPDATE research.post_publications SET snapshot=snapshot-'materials' WHERE post_id=:id"), {'id':post['id']})
    assert client.get('/api/research/posts/'+post['id']).json()['latestVersion']['materialIds'] == []
    assert client.get('/api/public/posts/'+release['slug']).json()['materials'] == []
    with admin.connect() as db:
        assert not db.execute(text("SELECT snapshot ? 'materials' FROM research.post_publications WHERE post_id=:id"), {'id':post['id']}).scalar_one()


def test_auto_sources_dedupe_order_archive_and_limits(client, admin):
    _, auth = identity(client, admin)
    docs = [create(client, auth, 'documents', title='Doc '+str(i)) for i in range(2)]
    sources = [create(client, auth, title='Source '+str(i)) for i in range(3)]
    first = relate(client, auth, docs[0], sources[0])
    second = relate(client, auth, docs[0], sources[1])
    relate(client, auth, docs[1], sources[0])
    relate(client, auth, docs[1], sources[2])
    assert client.request('DELETE', '/api/research/materials/'+sources[2]['id'], json={'expectedVersion':1}, headers=headers(auth)).status_code == 200
    doc_ids = [doc['id'] for doc in docs]
    response = client.post('/api/research/post-material-options/resolve', json={'documentIds':doc_ids}, headers=auth)
    assert response.status_code == 200, response.text
    expected = [source['id'] for _, source in sorted([(first['id'],sources[0]),(second['id'],sources[1])])]
    assert [item['id'] for item in response.json()['items']] == expected
    assert response.json()['excludedArchivedIds'] == [sources[2]['id']]
    draft = make_draft(client, auth, documentIds=doc_ids)
    assert draft['materialIds'] == expected
    origin = next(item for item in draft['materialSources'] if item['materialId'] == sources[0]['id'])
    assert origin['documentIds'] == doc_ids
    for values in ([sources[0]['id']]*2, [str(uuid.uuid4()) for _ in range(51)]):
        assert client.post(ROOT, json=content(materialIds=values), headers=headers(auth)).status_code == 422
    assert client.get('/api/research/post-material-options?limit=101').status_code == 422
    _, other = identity(client, admin, '222222')
    assert client.post('/api/research/post-material-options/resolve', json={'documentIds':doc_ids}, headers=other).status_code == 404
