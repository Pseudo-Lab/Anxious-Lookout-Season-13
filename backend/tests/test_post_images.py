import io
import uuid
from pathlib import Path
import pytest
from PIL import Image, PngImagePlugin
from sqlalchemy import text
from app.migrate import migrate
from app import post_images
from tests.test_research import identity, headers
from tests.test_posts import make_draft, commit, content, proposal_content
from tests.test_post_publications import publish, preview


@pytest.fixture(scope='module', autouse=True)
def image_schema(schema):
    migrate('0007_post_blocks')


@pytest.fixture(autouse=True)
def image_disk(tmp_path, monkeypatch):
    monkeypatch.setenv('POST_IMAGE_ROOT', str(tmp_path / 'data'))


def picture(format='PNG'):
    stream = io.BytesIO()
    info = PngImagePlugin.PngInfo(); info.add_text('private', 'secret metadata')
    Image.new('RGB', (12, 9), 'red').save(stream, format=format, pnginfo=info)
    return stream.getvalue()


def upload(client, auth, data=None, mime='image/png', key=None):
    return client.post('/api/research/post-images', content=data if data is not None else picture(),
        headers={**headers(auth, key), 'Content-Type': mime})


def delete(client, auth, image, key=None):
    return client.request('DELETE', '/api/research/post-images/'+image['id'], json={}, headers=headers(auth, key))


def test_upload_private_metadata_replay_public_withdraw(client, admin):
    _, auth = identity(client, admin)
    key = str(uuid.uuid4())
    first = upload(client, auth, key=key)
    assert first.status_code == 201, first.text
    image = first.json()
    assert upload(client, auth, key=key).json() == image
    assert upload(client, auth, picture('JPEG'), 'image/jpeg', key).status_code == 409
    response = client.get(image['url'])
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert 'private' not in Image.open(io.BytesIO(response.content)).info
    assert len(client.get('/api/research/post-images').json()['items']) == 1
    post = commit(client, auth, make_draft(client, auth, content='![image]('+image['url']+')', coverImageId=image['id']))
    assert preview(client, post)['images'] == [image]
    release = publish(client, auth, post)
    assert release['coverImageUrl'] == image['url']
    cookies = dict(client.cookies); client.cookies.clear()
    public = client.get(image['url'])
    assert public.status_code == 200 and public.headers['cache-control'].startswith('public,')
    assert client.get(image['url'], headers={'If-None-Match': public.headers['etag']}).status_code == 304
    client.cookies.update(cookies)
    assert client.request('DELETE', '/api/research/posts/'+post['id']+'/publication', json={'expectedVersion': 2}, headers=headers(auth)).status_code == 200
    assert delete(client, auth, image).status_code == 409  # retained history
    client.cookies.clear()
    hidden = client.get(image['url'], headers={'If-None-Match': public.headers['etag']})
    assert hidden.status_code == 404 and hidden.headers['cache-control'] == 'no-store'


def test_owner_delete_references_and_foreign(client, admin):
    _, auth = identity(client, admin)
    image = upload(client, auth).json()
    cookies = dict(client.cookies)
    _, other = identity(client, admin, '222222')
    assert client.get(image['url']).status_code == 404
    assert delete(client, other, image).status_code == 404
    result = client.post('/api/research/post-drafts', json=content(coverImageId=image['id']), headers=headers(other))
    assert result.status_code == 404
    client.cookies.clear(); assert client.get(image['url']).status_code == 404
    client.cookies.update(cookies)
    draft = make_draft(client, auth, content='![ref][x]\n\n[x]: '+image['url'])
    assert delete(client, auth, image).status_code == 409
    client.request('DELETE', '/api/research/post-drafts/'+draft['id'], json={'expectedDraftRevision': 1}, headers=headers(auth))
    assert delete(client, auth, image).status_code == 409  # discarded draft retained
    unused = upload(client, auth).json()
    key = str(uuid.uuid4())
    assert delete(client, auth, unused, key).status_code == 200
    assert delete(client, auth, unused, key).status_code == 200
    assert client.get(unused['url']).status_code == 404
    assert not (post_images.root()/unused['id']).exists()


def test_invalid_media_quota_missing_and_corrupt_bytes(client, admin, monkeypatch):
    _, auth = identity(client, admin)
    for data, mime in [(picture(), 'image/jpeg'), (b'bad', 'image/png'), (picture()[:30], 'image/png')]:
        assert upload(client, auth, data, mime).status_code == 422
    assert upload(client, auth, b'<svg/>', 'image/svg+xml').status_code == 415
    assert upload(client, auth, b'x'*(post_images.LIMIT+1)).status_code == 413
    assert client.post('/api/research/post-images', content=picture()).status_code == 403
    monkeypatch.setattr(post_images, 'QUOTA', 10)
    assert upload(client, auth).status_code == 413
    monkeypatch.setattr(post_images, 'QUOTA', 100*1024*1024)
    image = upload(client, auth).json()
    (post_images.root()/image['id']).write_bytes(b'corrupt')
    assert client.get(image['url']).status_code == 503
    assert client.post('/api/research/post-drafts', json=content(coverImageId=image['id']), headers=headers(auth)).status_code == 503
    monkeypatch.setenv('POST_IMAGE_ROOT', '/missing-volume/data')
    assert upload(client, auth).status_code == 503


def test_block_references_are_structural_only():
    identity = uuid.uuid4()
    block = {'id':str(uuid.uuid4()),'type':'image','imageId':str(identity),'alt':'alt','caption':[]}
    assert post_images.references(content(content={'format':'post-blocks','version':1,'blocks':[block]})) == [identity]
    for body in [str(identity), '/api/media/post-images/'+str(identity), '`'+str(identity)+'`']:
        assert post_images.references(content(content=body)) == []
    from app.research import ResearchError
    with pytest.raises(ResearchError):
        post_images.references({'content':'![x](/api/media/post-images/'+str(identity)+')','coverImageId':None})


def test_animation_pixel_limit_and_tombstone_retry(client, admin, monkeypatch):
    _, auth = identity(client, admin)
    stream = io.BytesIO()
    Image.new('RGB', (10,10), 'red').save(stream, format='PNG', save_all=True, append_images=[Image.new('RGB', (10,10), 'blue')])
    assert upload(client, auth, stream.getvalue()).status_code == 422
    stream = io.BytesIO(); Image.new('RGB', (8193,1)).save(stream, format='PNG')
    assert upload(client, auth, stream.getvalue()).status_code == 422
    image = upload(client, auth).json()
    original = Path.unlink
    def broken(path, *args, **kwargs): raise OSError('fixture')
    monkeypatch.setattr(Path, 'unlink', broken)
    key = str(uuid.uuid4())
    assert delete(client, auth, image, key).status_code == 200
    with admin.connect() as db:
        assert db.execute(text('SELECT deleted,purged FROM research.post_images')).one() == (True,False)
    monkeypatch.setattr(Path, 'unlink', original)
    assert delete(client, auth, image, key).status_code == 200
    with admin.connect() as db:
        assert db.execute(text('SELECT deleted,purged FROM research.post_images')).one() == (True,True)


def test_concurrent_quota_and_delete_reference_serialization(client, admin, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    _, auth = identity(client, admin)
    size = len(post_images.normalize(picture(), 'image/png')[0])
    monkeypatch.setattr(post_images, 'QUOTA', size)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: upload(client, auth), range(2)))
    assert sorted(r.status_code for r in results) == [201, 413]
    image = next(r.json() for r in results if r.status_code == 201)
    def race(action):
        if action == 'delete': return delete(client, auth, image)
        return client.post('/api/research/post-drafts', json=content(coverImageId=image['id']), headers=headers(auth))
    with ThreadPoolExecutor(max_workers=2) as pool:
        removed, referenced = list(pool.map(race, ['delete','reference']))
    assert (removed.status_code, referenced.status_code) in {(200,404),(409,201)}
    if referenced.status_code == 201:
        assert client.get(image['url']).status_code == 200


def test_proposal_retention_and_symlink_fail_closed(client, admin):
    from sqlalchemy.orm import Session
    from app.research import Store
    from app.post_proposals import Propose, create as propose
    owner, auth = identity(client, admin)
    image = upload(client, auth).json()
    with Session(admin) as db, db.begin():
        proposed = propose(Store(db, owner), Propose(**proposal_content(content='![x]('+image['url']+')'), mutationId=str(uuid.uuid4())))
    result = client.post('/api/research/post-proposals/'+proposed['id']+'/discard', json={'expectedProposalVersion':1}, headers=headers(auth))
    assert result.status_code == 200, result.text
    assert delete(client, auth, image).status_code == 409
    path = post_images.root()/image['id']
    path.unlink(); path.symlink_to('/etc/passwd')
    assert client.get(image['url']).status_code == 503


def test_restored_volume_modes_are_private(tmp_path, monkeypatch):
    directory = tmp_path/'restored'
    directory.mkdir(mode=0o770)
    directory.chmod(0o2770)  # CSI fsGroup-expanded permissions on a restored PVC
    monkeypatch.setenv('POST_IMAGE_ROOT', str(directory))
    assert post_images.root() == directory
    assert directory.stat().st_mode & 0o077 == 0


def test_nested_alt_does_not_publish_hidden_image(client, admin):
    _, auth = identity(client, admin)
    hidden = upload(client, auth).json()
    visible = [upload(client, auth).json() for _ in range(3)]
    body = {'format':'post-blocks','version':1,'blocks':[
        {'id':str(uuid.uuid4()),'type':'image','imageId':image['id'],
         'alt':f"![hidden]({hidden['url']})", 'caption':[]}
        for image in visible]}
    post = commit(client, auth, make_draft(client, auth, content=body))
    publish(client, auth, post)
    client.cookies.clear()
    # Alt descendants render as text, so they must never acquire public access.
    assert client.get(hidden['url']).status_code == 404
    assert all(client.get(image['url']).status_code == 200 for image in visible)


def test_usage_lists_all_retained_kinds_without_duplicates(client,admin):
    from sqlalchemy.orm import Session
    from app.research import Store
    from app.post_proposals import Propose, create as propose
    owner,auth=identity(client,admin)
    image=upload(client,auth).json()
    unused=upload(client,auth).json()
    def usages():
        response=client.get('/api/research/post-images')
        assert response.status_code==200 and response.headers['cache-control']=='no-store'
        return {item['id']:item['usage'] for item in response.json()['items']}
    assert usages()=={image['id']:[],unused['id']:[]}
    draft=make_draft(client,auth,content='![body]('+image['url']+')',coverImageId=image['id'])
    duplicate=make_draft(client,auth,coverImageId=image['id'])
    assert usages()[image['id']]==['draft']
    assert client.request('DELETE','/api/research/post-drafts/'+duplicate['id'],
        json={'expectedDraftRevision':1},headers=headers(auth)).status_code==200
    with Session(admin) as db,db.begin():
        proposal=propose(Store(db,owner),Propose(**proposal_content(coverImageId=image['id']),mutationId=str(uuid.uuid4())))
    assert usages()[image['id']]==['draft','proposal']
    assert client.post('/api/research/post-proposals/'+proposal['id']+'/discard',
        json={'expectedProposalVersion':1},headers=headers(auth)).status_code==200
    post=commit(client,auth,draft)
    assert usages()[image['id']]==['draft','proposal','version']
    publish(client,auth,post)
    all_kinds=['draft','proposal','version','publication']
    assert usages()[image['id']]==all_kinds
    assert client.request('DELETE','/api/research/posts/'+post['id']+'/publication',
        json={'expectedVersion':2},headers=headers(auth)).status_code==200
    assert client.request('DELETE','/api/research/posts/'+post['id'],
        json={'expectedVersion':3},headers=headers(auth)).status_code==200
    assert usages()[image['id']]==all_kinds
    make_draft(client,auth,content='`'+unused['url']+'`')
    assert usages()[unused['id']]==[]  # Image-looking text grants no retention.
    assert delete(client,auth,image).status_code==409
    assert delete(client,auth,unused).status_code==200
    assert usages()=={image['id']:all_kinds}


def test_usage_pagination_and_owner_isolation(client,admin):
    _,auth=identity(client,admin)
    used=upload(client,auth).json();unused=upload(client,auth).json()
    make_draft(client,auth,coverImageId=used['id'])
    first=client.get('/api/research/post-images',params={'limit':1}).json()
    assert len(first['items'])==1 and first['nextCursor']
    second=client.get('/api/research/post-images',params={'limit':1,'cursor':first['nextCursor']}).json()
    assert len(second['items'])==1 and second['nextCursor'] is None
    listed={row['id']:row['usage'] for page in (first,second) for row in page['items']}
    assert listed=={used['id']:['draft'],unused['id']:[]}
    _,other=identity(client,admin,'222222')
    assert client.get('/api/research/post-images').json()['items']==[]
    theirs=upload(client,other).json()
    response=client.get('/api/research/post-images').json()
    assert [(row['id'],row['usage']) for row in response['items']]==[(theirs['id'],[])]
    client.cookies.clear()
    assert client.get('/api/research/post-images').status_code==401
