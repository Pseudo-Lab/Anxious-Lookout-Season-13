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
    result=client.post('/api/internal/research/tools',content=json.dumps({
        'sessionId':session['id'],'requestId':runner.last['requestId'],
        'name':'research_post_propose','arguments':value}),headers={
        'Authorization':'Bearer '+runner.last['toolToken'],'Content-Type':'application/json'})
    assert result.status_code==422,result.text
    assert result.json()['error']['details']['path']=='/contentInput/text'


def legacy_cache(admin, owner, key, operation, body, cached):
    import hashlib
    def old(value):
        if isinstance(value,list): return [old(v) for v in value]
        if not isinstance(value,dict): return value
        return {k: '# Text' if k=='content' and isinstance(v,dict) else old(v) for k,v in value.items()}
    fingerprint=hashlib.sha256(json.dumps([operation,body],sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    with admin.begin() as c:
        c.execute(text('UPDATE research.idempotency SET fingerprint=:fp,response=CAST(:v AS jsonb) WHERE owner_id=:owner AND key=CAST(:key AS uuid)'),
                  {'fp':fingerprint,'v':json.dumps(old(cached)),'owner':owner,'key':key})


def post_storage_snapshot(admin):
    with admin.connect() as c:
        return {table:c.execute(text(f'SELECT to_jsonb(t) FROM research.{table} t ORDER BY to_jsonb(t)::text')).scalars().all()
                for table in ('posts','post_drafts','post_versions','post_proposals','post_publications','idempotency')}


@pytest.mark.parametrize('operation', ['create','update','accept'])
def test_legacy_changed_wire_retry_owner_fingerprint_and_no_writes(client,admin,operation):
    from tests.test_posts import proposal_content
    owner,auth=identity(client,admin)
    method='POST';path=ROOT;body=content();status=201
    if operation=='update':
        draft=make_draft(client,auth)
        method='PUT';path=ROOT+'/'+draft['id'];body['expectedDraftRevision']=1;status=200
    elif operation=='accept':
        session,runner=turn(client,auth,owner)
        proposed=callback(client,session,runner,'research_post_propose',{
            **proposal_content(),'mutationId':str(uuid.uuid4())})
        assert proposed.status_code==200,proposed.text
        path='/api/research/post-proposals/'+proposed.json()['id']+'/accept'
        body['expectedProposalVersion']=1;status=200
    key=str(uuid.uuid4())
    result=client.request(method,path,json=body,headers=headers(auth,key))
    assert result.status_code==status,result.text
    legacy={**body,'content':'# Text'}
    legacy_cache(admin,owner,key,method+' '+path,legacy,result.json())
    before=post_storage_snapshot(admin)
    replay=client.request(method,path,json=legacy,headers=headers(auth,key))
    assert replay.status_code==status,replay.text
    value=replay.json()['post']['latestVersion'] if operation=='accept' else replay.json()
    assert value['content']['format']=='post-blocks' and value['textProjection']=='Text'
    changed=client.request(method,path,json={**legacy,'content':'changed'},headers=headers(auth,key))
    assert changed.status_code==409 and changed.json()['error']['code']=='idempotency_conflict'
    assert client.request(method,path,json=legacy,headers=headers(auth)).status_code==422
    assert client.request(method,path,json=legacy,headers={**headers(auth,key),'X-CSRF-Token':'wrong'}).status_code==403
    assert client.request(method,path,json=legacy,headers={**headers(auth,key),'Origin':'https://wrong.example'}).status_code==403
    _,other=identity(client,admin,'222222')
    assert client.request(method,path,json=legacy,headers=headers(other,key)).status_code==422
    client.cookies.clear()
    assert client.request(method,path,json=legacy,headers=headers(auth,key)).status_code==401
    assert post_storage_snapshot(admin)==before


def test_legacy_propose_tool_retry_before_new_shape_validation(client,admin):
    from tests.test_posts import proposal_content
    owner,auth=identity(client,admin);session,runner=turn(client,auth,owner)
    key=str(uuid.uuid4());body={**proposal_content(),'mutationId':key}
    result=callback(client,session,runner,'research_post_propose',body)
    assert result.status_code==200,result.text
    legacy={k:v for k,v in body.items() if k!='contentInput'};legacy['content']='# Text'
    legacy_cache(admin,owner,key,'tool:research_post_propose',legacy,result.json())
    other,other_auth=identity(client,admin,'222222');other_session,other_runner=turn(client,other_auth,other)
    before=post_storage_snapshot(admin)
    replay=callback(client,session,runner,'research_post_propose',legacy)
    assert replay.status_code==200,replay.text
    assert replay.json()['content']['format']=='post-blocks'
    assert replay.json()['id']==result.json()['id']
    assert callback(client,session,runner,'research_post_propose',{**legacy,'content':'changed'}).status_code==409
    assert callback(client,session,runner,'research_post_propose',{**legacy,'mutationId':str(uuid.uuid4())}).status_code==422
    assert callback(client,other_session,other_runner,'research_post_propose',legacy).status_code==422
    assert client.post('/api/internal/research/tools',json={'sessionId':session['id'],'requestId':runner.last['requestId'],
        'name':'research_post_propose','arguments':legacy}).status_code in (401,403)
    assert post_storage_snapshot(admin)==before


@pytest.mark.parametrize('operation',['create','update','accept','commit'])
def test_v11_client_revision_gate_prevents_old_bundle_writes(client,admin,operation):
    from tests.test_posts import proposal_content
    owner,auth=identity(client,admin)
    method='POST';path=ROOT;body=content();status=201
    if operation in ('update','commit'):
        draft=make_draft(client,auth)
        path=ROOT+'/'+draft['id'];status=200
        if operation=='update':
            method='PUT';body['expectedDraftRevision']=1
        else:
            path+='/commit';body={'expectedDraftRevision':1}
    elif operation=='accept':
        session,runner=turn(client,auth,owner)
        proposed=callback(client,session,runner,'research_post_propose',{
            **proposal_content(),'mutationId':str(uuid.uuid4())})
        assert proposed.status_code==200,proposed.text
        path='/api/research/post-proposals/'+proposed.json()['id']+'/accept'
        body['expectedProposalVersion']=1;status=200
    key=str(uuid.uuid4());old_headers={**auth,'Idempotency-Key':key}
    before=post_storage_snapshot(admin)
    for revision in (None,'1','1.0','1.2'):
        request_headers={**old_headers}
        if revision is not None: request_headers['X-Post-Contract-Revision']=revision
        result=client.request(method,path,json=body,headers=request_headers)
        assert result.status_code==409,result.text
        assert result.json()['error']['code']=='post_contract_upgrade_required'
        assert result.json()['error']['details']['requiredRevision']=='1.1'
        assert post_storage_snapshot(admin)==before
    accepted=client.request(method,path,json=body,headers=headers(auth,key))
    assert accepted.status_code==status,accepted.text
    before=post_storage_snapshot(admin)
    replay=client.request(method,path,json=body,headers=old_headers)
    assert replay.status_code==status and replay.json()==accepted.json()
    assert post_storage_snapshot(admin)==before


def test_v11_content_survives_draft_version_and_public_snapshot(client,admin):
    from pathlib import Path
    fixture=json.loads((Path(__file__).resolve().parents[1]/'contracts/post-blocks-v1/golden.json').read_text())
    font=next(c for c in fixture['valid'] if c['name']=='font-size-color-order-merge')
    table=next(c for c in fixture['valid'] if c['name']=='table-widths-ratio')
    raw={**font['input'],'blocks':font['input']['blocks']+table['input']['blocks']}
    expected={**font['canonical'],'blocks':font['canonical']['blocks']+table['canonical']['blocks']}
    _,auth=identity(client,admin)
    draft=make_draft(client,auth,content=raw)
    assert draft['content']==expected
    post=commit(client,auth,draft)
    assert post['latestVersion']['content']==expected
    from tests.test_post_publications import publish
    published=publish(client,auth,post)
    assert published['content']==expected
    public=client.get('/api/public/posts/'+post['latestVersion']['slug'])
    assert public.status_code==200 and public.json()['content']==expected
