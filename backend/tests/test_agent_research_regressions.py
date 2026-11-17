import uuid
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import text
from app.migrate import migrate
from tests.test_research import identity,headers,create
from tests.test_posts import make_draft,commit,proposal_content
from tests.test_agent_research_saves import turn
from tests.test_page_chat import callback
from tests.test_post_publications import publish

@pytest.fixture(scope='module',autouse=True)
def authors(schema):
    migrate('0008_version_authors')

def test_post_update_public_snapshot_and_human_resolution(client,admin):
    owner,auth=identity(client,admin)
    post=commit(client,auth,make_draft(client,auth,content='Initial public body'))
    publish(client,auth,post)
    post=client.get('/api/research/posts/'+post['id']).json()
    public_path='/api/public/posts/'+post['latestVersion']['slug']
    public_response=client.get(public_path)
    assert public_response.status_code==200,public_response.text
    before_public=public_response.json()
    active=make_draft(client,auth,post,content='Human dirty body')
    session,runner=turn(client,owner,auth)
    args={**proposal_content(content='Agent private body'),'postId':post['id'],'expectedVersion':post['version'],
          'draftId':active['id'],'expectedDraftRevision':active['revision'],'mutationId':str(uuid.uuid4())}
    stale=callback(client,session,runner,'research_post_save',{**args,'expectedDraftRevision':active['revision']+1})
    assert stale.status_code==409,stale.text
    assert stale.json()['error']['details']['currentDraft']==active
    result=callback(client,session,runner,'research_post_save',args)
    assert result.status_code==200,result.text
    saved=result.json()
    assert saved['latestVersion']['author']=={'kind':'codex','conversationId':session['id']}
    assert client.get('/api/research/post-drafts/'+active['id']).json()==active
    assert client.get(public_path).json()==before_public
    assert callback(client,session,runner,'research_post_save',args).json()==saved
    conflict=callback(client,session,runner,'research_post_save',{**args,'mutationId':str(uuid.uuid4())})
    assert conflict.status_code==409
    assert conflict.json()['error']['details']['current']['id']==post['id']
    route='/api/research/post-drafts/'+active['id']+'/commit'
    human={'expectedDraftRevision':active['revision'],'expectedVersion':saved['version']}
    refused=client.post(route,json=human,headers=headers(auth))
    assert refused.status_code==409,refused.text
    resolved=client.post(route,json={**human,'resolvedBaseVersionId':saved['latestVersion']['id']},headers=headers(auth))
    assert resolved.status_code==200,resolved.text
    assert resolved.json()['post']['latestVersion']['author']=={'kind':'human','conversationId':None}
    assert client.get(public_path).json()==before_public

def test_concurrent_agent_writes_have_one_winner_and_exactly_one_snapshot(client,admin):
    owner,auth=identity(client,admin)
    doc=create(client,auth,'documents')
    session,runner=turn(client,owner,auth)
    args={'id':doc['id'],'expectedVersion':1,'title':'Concurrent','content':'Saved once'}
    def write(_):return callback(client,session,runner,'research_document_update',{**args,'mutationId':str(uuid.uuid4())}).status_code
    with ThreadPoolExecutor(max_workers=2)as pool:assert sorted(pool.map(write,range(2)))==[200,409]
    versions=client.get('/api/research/documents/'+doc['id']+'/versions').json()['items']
    assert len(versions)==2
    assert versions[0]['author']['kind']=='codex'

def test_author_and_publish_fields_rejected_and_foreign_draft_does_not_leak(client,admin):
    owner,auth=identity(client,admin)
    owner_cookie=client.cookies.get('anxious_session')
    session,runner=turn(client,owner,auth)
    other,other_auth=identity(client,admin,'222222')
    foreign=make_draft(client,other_auth,content='FOREIGN PRIVATE DRAFT')
    client.cookies.set('anxious_session',owner_cookie,path='/api')
    args={**proposal_content(content='Agent source'),'mutationId':str(uuid.uuid4())}
    for extra in [{'author':{'kind':'human'}},{'publish':True}]:
        response=callback(client,session,runner,'research_post_save',{**args,**extra})
        assert response.status_code==422,response.text
    response=callback(client,session,runner,'research_post_save',{**args,'draftId':foreign['id'],'expectedDraftRevision':foreign['revision']})
    assert response.status_code==404,response.text
    assert 'FOREIGN PRIVATE DRAFT' not in response.text and 'currentDraft' not in response.text
    with admin.connect()as db:assert db.execute(text('SELECT count(*) FROM research.posts')).scalar_one()==0

def test_populated_0007_upgrade_frozen_constraints_grants_and_restore(admin,monkeypatch):
    import os,json
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import DBAPIError
    from app.research_schema_contract import validate_research
    from app.restore_research import restore_candidate
    from tests.recovery_probe import signature
    database='restore_review97_'+uuid.uuid4().hex
    url=make_url(os.environ['ADMIN_DATABASE_URL']).set(database=database)
    with admin.connect().execution_options(isolation_level='AUTOCOMMIT')as c:c.execute(text('CREATE DATABASE '+database))
    engine=create_engine(url,hide_parameters=True)
    try:
        monkeypatch.setenv('ADMIN_DATABASE_URL',url.render_as_string(hide_password=False))
        migrate('0007_post_blocks')
        with engine.begin()as c:
            owner=c.execute(text("INSERT INTO auth.accounts(github_id,login,is_approved,role) VALUES('777777','old-owner',true,'editor') RETURNING id")).scalar_one()
            item=c.execute(text("INSERT INTO research.items(owner_id,kind) VALUES(:owner,'document') RETURNING id"),{'owner':owner}).scalar_one()
            c.execute(text("INSERT INTO research.versions(item_id,owner_id,kind,number,title,content) VALUES(:item,:owner,'document',1,'Retained title','Retained body')"),{'item':item,'owner':owner})
            post=c.execute(text("INSERT INTO research.posts(owner_id,slug) VALUES(:owner,'review-retained') RETURNING id"),{'owner':owner}).scalar_one()
            from tests.test_posts import content
            from app.posts import payload,PostText
            body=payload(PostText(**content(content='Retained post body')))
            c.execute(text('INSERT INTO research.post_versions(post_id,number,payload) VALUES(:post,1,CAST(:payload AS jsonb))'),{'post':post,'payload':json.dumps(body)})
            before={table:[dict(row._mapping)for row in c.execute(text('SELECT * FROM research.'+table))]for table in ['versions','post_versions']}
            assert validate_research(c)=='0007_post_blocks'
        migrate('0008_version_authors');migrate('0008_version_authors')
        with engine.connect()as c:
            assert validate_research(c)=='0008_version_authors'
            for table in before:
                rows=[dict(row._mapping)for row in c.execute(text('SELECT * FROM research.'+table))]
                assert [{k:v for k,v in row.items()if not k.startswith('author_')}for row in rows]==before[table]
                assert all(row['author_kind']=='human' and row['author_conversation_id']is None and row['author_credential_id']is None for row in rows)
                for privilege in ['UPDATE','DELETE']:
                    assert not c.execute(text("SELECT has_table_privilege('anxious_api',:table,:priv)"),{'table':'research.'+table,'priv':privilege}).scalar_one()
        for table in ['versions','post_versions']:
            for sql in [f"ALTER TABLE research.{table} ALTER COLUMN author_kind DROP DEFAULT",f"ALTER TABLE research.{table} DROP CONSTRAINT {table}_author_check",f"GRANT UPDATE(author_kind) ON research.{table} TO anxious_api"]:
                with engine.connect()as c:
                    txn=c.begin()
                    try:
                        c.execute(text(sql))
                        with pytest.raises(RuntimeError):validate_research(c)
                    finally:txn.rollback()
            with engine.connect()as c:
                txn=c.begin()
                try:
                    with pytest.raises(DBAPIError):c.execute(text(f"UPDATE research.{table} SET author_kind='codex',author_conversation_id=NULL"))
                finally:txn.rollback()
        with engine.begin()as c:
            before_restore=signature(c)
            c.execute(text('REVOKE INSERT ON research.versions FROM anxious_api'))
        assert restore_candidate(url.render_as_string(hide_password=False),os.environ['API_DATABASE_PASSWORD'],database)=='0008_version_authors'
        with engine.connect()as c:
            assert validate_research(c)=='0008_version_authors'
            assert signature(c)==before_restore
    finally:
        engine.dispose()
        with admin.connect().execution_options(isolation_level='AUTOCOMMIT')as c:c.execute(text('DROP DATABASE '+database+' WITH (FORCE)'))
