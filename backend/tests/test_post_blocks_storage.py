"""Real DB boundary/atomic migration tests; no production connections."""
import json
import os
import uuid
from contextlib import contextmanager
import pytest
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.engine import make_url
from app.migrate import migrate
from app.research import ResearchError
from tests.test_research import identity, headers
from tests.test_posts import content, make_draft, commit, ROOT
from tests.test_post_proposals import turn
from tests.test_page_chat import callback

@pytest.fixture(scope='module',autouse=True)
def block_schema(schema): migrate('0007_post_blocks')

def test_json_pointer_and_markdown_old_boundary_rejected(client,admin):
    _,auth=identity(client,admin)
    bad=content();bad['content']['version']=True
    result=client.post(ROOT,json=bad,headers=headers(auth))
    assert result.status_code==422 and result.json()['error']['details']['path']=='/content/version'
    bad['content']='old markdown'
    assert client.post(ROOT,json=bad,headers=headers(auth)).status_code==422
    assert client.get(ROOT).json()['items']==[]

def test_proposal_comparison_recomputed_and_d1(client,admin):
    owner,auth=identity(client,admin)
    draft=make_draft(client,auth,content='same')
    post=commit(client,auth,draft)
    session,runner=turn(client,auth,owner)
    value=content();value.pop('content')
    value.update(contentInput={'format':'markdown','text':'same'},postId=post['id'],expectedVersion=1,mutationId=str(uuid.uuid4()))
    response=callback(client,session,runner,'research_post_propose',value)
    assert response.status_code==200,response.text
    proposal=response.json()
    assert client.get('/api/research/posts/'+post['id']).json()['version']==1
    altered=content(content='same')['content'];altered['blocks'][0]['align']='right'
    compared=client.post('/api/research/post-proposals/'+proposal['id']+'/comparison',json={'content':altered},headers=headers(auth))
    assert compared.status_code==200,compared.text
    assert compared.json()['textProjection']==compared.json()['baseTextProjection']=='same'
    assert any('align' in item['changedProperties'] for item in compared.json()['formatChanges'])
    assert compared.json()['baseVersionId']==post['latestVersion']['id']
    _,other=identity(client,admin,'222222')
    assert client.post('/api/research/post-proposals/'+proposal['id']+'/comparison',json={'content':altered},headers=headers(other)).status_code==404

@contextmanager
def isolated_database(monkeypatch):
    admin=make_url(os.environ['ADMIN_DATABASE_URL']);name='restore_blocks_'+uuid.uuid4().hex
    control=create_engine(admin,isolation_level='AUTOCOMMIT')
    with control.connect() as c: c.execute(text('CREATE DATABASE '+name))
    url=admin.set(database=name)
    monkeypatch.setenv('ADMIN_DATABASE_URL',url.render_as_string(hide_password=False))
    engine=create_engine(url)
    try: yield engine
    finally:
        engine.dispose()
        with control.connect() as c: c.execute(text('DROP DATABASE '+name+' WITH (FORCE)'))
        control.dispose()

def test_migration_atomic_private_preservation_replay_and_signature(monkeypatch):
    from research_alembic.post_blocks_migration import migrate_content
    from app.post_block_markdown import convert
    from app.post_blocks import digest
    from tests.recovery_probe import signature
    with isolated_database(monkeypatch) as engine:
        migrate('0006_posts')
        with engine.begin() as c:
            before=c.execute(text('SELECT id,payload FROM research.post_versions ORDER BY id')).mappings().all()
            first=before[-1]
            damaged={**first['payload'],'content':'<script>unsupported</script>'}
            c.execute(text('UPDATE research.post_versions SET payload=CAST(:v AS jsonb) WHERE id=:id'),{'id':first['id'],'v':json.dumps(damaged)})
        with pytest.raises(ResearchError): migrate('0007_post_blocks')
        with engine.begin() as c:
            assert c.execute(text('SELECT version_num FROM research.alembic_version')).scalar_one()=='0006_posts'
            assert not inspect(c).has_table('post_markdown_backups',schema='research')
            assert c.execute(text("SELECT count(*) FROM research.post_publications WHERE jsonb_typeof(snapshot->'content')='string'")).scalar_one()==3
            c.execute(text('UPDATE research.post_versions SET payload=CAST(:v AS jsonb) WHERE id=:id'),{'id':first['id'],'v':json.dumps(first['payload'])})
        with engine.begin() as c:
            broken={**first['payload'],'documentIds':[str(uuid.uuid4())]}
            c.execute(text('UPDATE research.post_versions SET payload=CAST(:v AS jsonb) WHERE id=:id'),{'id':first['id'],'v':json.dumps(broken)})
        with pytest.raises(RuntimeError): migrate('0007_post_blocks')
        with engine.begin() as c:
            assert not inspect(c).has_table('post_markdown_backups',schema='research')
            c.execute(text('UPDATE research.post_versions SET payload=CAST(:v AS jsonb) WHERE id=:id'),{'id':first['id'],'v':json.dumps(first['payload'])})
        migrate('0007_post_blocks')
        with engine.begin() as c:
            assert c.execute(text('SELECT count(*) FROM research.post_markdown_backups')).scalar_one()==6
            assert not c.execute(text("SELECT has_table_privilege('anxious_api','research.post_markdown_backups','SELECT')")).scalar_one()
            for old in before:
                row=c.execute(text('SELECT payload FROM research.post_versions WHERE id=:id'),{'id':old['id']}).scalar_one()
                document,_=convert(old['payload']['content'],str(old['id']))
                assert row['content']==document
                assert all(row[k]==v for k,v in old['payload'].items() if k!='content')
                saved=c.execute(text("SELECT markdown,canonical_sha256 FROM research.post_markdown_backups WHERE storage_table='post_versions' AND row_id=:id"),{'id':old['id']}).one()
                assert saved.markdown==old['payload']['content'] and saved.canonical_sha256==digest(document)
            original=signature(c);migrate_content(c);assert signature(c)==original
            assert 'research.post_markdown_backups' in original
            c.execute(text("UPDATE research.post_markdown_backups SET markdown='tamper' WHERE row_id=:id"),{'id':before[0]['id']})
            with pytest.raises(RuntimeError): migrate_content(c)
        # Alembic revision replay never rewrites converted rows.
        migrate('0007_post_blocks')

def test_pre_migration_commit_replay_adapts_cached_shape_without_second_write(client,admin):
    _,auth=identity(client,admin)
    draft=make_draft(client,auth)
    key=str(uuid.uuid4());body={'expectedDraftRevision':1};path=ROOT+'/'+draft['id']+'/commit'
    result=client.post(path,json=body,headers=headers(auth,key))
    assert result.status_code==200
    cached=result.json()
    cached['draft']['content']='# Text'
    cached['post']['latestVersion']['content']='# Text'
    with admin.begin() as c:
        c.execute(text('UPDATE research.idempotency SET response=CAST(:v AS jsonb) WHERE key=CAST(:key AS uuid)'),{'v':json.dumps(cached),'key':key})
    replay=client.post(path,json=body,headers=headers(auth,key))
    assert replay.status_code==200,replay.text
    assert replay.json()['post']['latestVersion']['content']['format']=='post-blocks'
    assert replay.json()['post']['latestVersion']['number']==1

def test_codex_boundary_errors_have_safe_json_pointer(client,admin):
    owner,auth=identity(client,admin);session,runner=turn(client,auth,owner)
    value=content();value.pop('content');value['mutationId']=str(uuid.uuid4())
    value['contentInput']={'format':'markdown','text':'\ud800'}
    # HTTP JSON encoder can represent the escaped surrogate; the callback helper
    # itself uses httpx's strict UTF-8 serializer, so exercise the shared tool model.
    from app.post_proposals import Propose
    with pytest.raises(ResearchError) as caught: Propose.model_validate(value)
    assert caught.value.details['path']=='/contentInput/text'
    assert caught.value.status==422
