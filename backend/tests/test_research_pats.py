"""Review-owned Docker/PostgreSQL PAT gate. No provider, VM or live credentials."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import os
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text

from app.main import create_app
from app import research_pats as pats
from tests.test_research import identity, headers, create
from tests.test_policy_publication import publish, connect


@pytest.fixture
def pat_client(admin, monkeypatch):
    monkeypatch.setenv('AUTH_ORIGIN','https://pat.example')
    monkeypatch.setenv('RESEARCH_AGENT_API_ENABLE','true')
    app=create_app()
    with TestClient(app,base_url='https://pat.example',raise_server_exceptions=False) as client:
        yield client


def issue(client, auth, *, scope='write', name='External researcher', key=None):
    result=client.post('/api/research/pats',json={'name':name,'scope':scope},headers=headers(auth,key))
    assert result.status_code==201,result.text
    return result.json()


def call(client, token, operation='research_list', body=None):
    return client.post('/api/research/agent/v1/'+operation,json=body if body is not None else {'type':'document'},
                       headers={'Authorization':'Bearer '+token})


def test_issue_once_replay_revoke_and_wrong_hash_do_not_expose_or_touch_victim(pat_client, admin):
    owner,auth=identity(pat_client,admin)
    key=str(uuid4()); first=issue(pat_client,auth,key=key)
    assert first['secretAvailable'] and not first['replayed'] and pats.TOKEN.fullmatch(first['token'])
    listed=pat_client.get('/api/research/pats').json()['items'][0]
    assert listed['id']==first['credential']['id'] and listed['lastUsedAt'] is None
    assert 'token' not in listed and 'hash' not in json.dumps(listed)
    replay=pat_client.post('/api/research/pats',json={'name':'External researcher','scope':'write'},headers=headers(auth,key))
    assert replay.status_code==200 and replay.json()['token'] is None and replay.json()['replayed']
    wrong=first['token'][:-1]+('a' if first['token'][-1]!='a' else 'b')
    assert call(pat_client,wrong).status_code==401
    with admin.connect() as db:
        row=db.execute(text('SELECT token_hash,rate_count,last_used_at FROM research.credentials WHERE id=:id'),{'id':listed['id']}).one()
        assert row.token_hash==pats.token_hash(first['token']) and row.rate_count==0 and row.last_used_at is None
        assert first['token'] not in str(db.execute(text('SELECT * FROM research.pat_issuances')).all())
    assert call(pat_client,first['token']).status_code==200
    assert pat_client.get('/api/research/pats').json()['items'][0]['lastUsedAt'] is not None
    for _ in range(2):
        assert pat_client.delete('/api/research/pats/'+listed['id'],headers=headers(auth)).status_code==200
    assert call(pat_client,first['token']).status_code==401


def test_owner_cap_serializes_two_new_keys_and_replay_still_works_at_ten(pat_client,admin):
    owner,auth=identity(pat_client,admin)
    key=str(uuid4()); issue(pat_client,auth,key=key)
    for _ in range(8):issue(pat_client,auth)
    cookie=pat_client.cookies.get('anxious_session')
    def submit(_):
        return pat_client.post('/api/research/pats',json={'name':'concurrent','scope':'read'},
            headers={**headers(auth),'Cookie':'anxious_session='+cookie}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(submit,range(2)))==[201,409]
    replay=pat_client.post('/api/research/pats',json={'name':'External researcher','scope':'write'},headers=headers(auth,key))
    assert replay.status_code==200 and replay.json()['token'] is None
    with admin.connect() as db:
        assert db.execute(text('SELECT count(*) FROM research.credentials WHERE owner_id=:id'),{'id':owner}).scalar_one()==10


def test_commenter_read_scope_current_role_and_browser_logout_independence(pat_client,admin):
    owner,auth=identity(pat_client,admin,role='commenter')
    created=issue(pat_client,auth)
    assert set(created['credential'])=={'id','name','issuedAt','expiresAt','revokedAt','lastUsedAt'}
    assert call(pat_client,created['token']).status_code==403
    assert pat_client.get('/api/public/documents',headers={'Authorization':'Bearer '+created['token']}).status_code==200
    with admin.begin() as db:db.execute(text("UPDATE auth.accounts SET role='editor' WHERE id=:id"),{'id':owner})
    assert call(pat_client,created['token']).status_code==200
    read=issue(pat_client,auth,scope='read')
    assert call(pat_client,read['token'],'research_document_save',{'title':'now allowed','content':'saved','mutationId':str(uuid4())}).status_code==200
    assert pat_client.post('/api/auth/logout',headers=auth).status_code==204
    assert call(pat_client,created['token']).status_code==200
    with admin.begin() as db:db.execute(text("UPDATE auth.accounts SET role='commenter',is_approved=false WHERE id=:id"),{'id':owner})
    assert call(pat_client,created['token']).status_code==403


def test_pat_saves_first_author_exact_replay_audit_and_public_nested_projection(pat_client,admin):
    owner,auth=identity(pat_client,admin)
    first=issue(pat_client,auth,name='Private PAT name')
    second=issue(pat_client,auth,name='Later PAT name')
    args={'title':'Source','content':'body','sourceUrl':'https://example.com/source',
          'collectedAt':'2026-11-17T00:00:00Z','contentKind':'excerpt','mutationId':str(uuid4())}
    result=call(pat_client,first['token'],'research_material_save',args)
    assert result.status_code==200,result.text
    source=result.json();assert 'author' not in source['latestVersion']
    author={'kind':'pat','conversationId':None,'credentialId':first['credential']['id'],'name':'Private PAT name'}
    assert call(pat_client,second['token'],'research_material_save',args).json()==source
    doc=create(pat_client,auth,'documents');connect(pat_client,auth,doc,source);publish(pat_client,auth,doc)
    public=pat_client.get('/api/public/documents/'+doc['id']).json()
    assert first['credential']['id'] not in json.dumps(public) and 'Private PAT name' not in json.dumps(public)
    bad=call(pat_client,first['token'],'research_document_save',{'title':'x','content':'x','mutationId':str(uuid4()),'author':author})
    assert bad.status_code==422
    with admin.connect() as db:
        assert db.execute(text('SELECT count(*) FROM research.conversations')).scalar_one()==0
        assert db.execute(text("SELECT count(*) FROM research.versions WHERE author_kind='pat'")).scalar_one()==1
        audits=db.execute(text('SELECT status,summary FROM research.pat_audit ORDER BY created_at')).all()
        assert len(audits)==3 and audits[-1].status=='failed'
        wire=json.dumps([row.summary for row in audits])
        assert first['token'] not in wire and 'Private PAT name' not in wire and 'sourceUrl' not in wire
    with admin.begin() as db:db.execute(text("UPDATE auth.accounts SET role='commenter' WHERE id=:id"),{'id':owner})
    assert call(pat_client,second['token'],'research_material_save',args).status_code==403
    pat_client.delete('/api/research/pats/'+first['credential']['id'],headers=headers(auth))
    # Historical version keeps the first immutable author even after revoke.
    with admin.connect() as db:
        assert db.execute(text('SELECT author_name FROM research.versions WHERE id=:id'),{'id':source['latestVersion']['id']}).scalar_one()=='Private PAT name'


def test_failure_rollbacks_do_not_refund_pat_quota(pat_client,admin):
    owner,auth=identity(pat_client,admin);created=issue(pat_client,auth)
    for _ in range(60):
        reply=call(pat_client,created['token'],'research_document_save',{'title':'bad','mutationId':str(uuid4())})
        assert reply.status_code==422,reply.text
    limited=call(pat_client,created['token'])
    assert limited.status_code==429 and limited.headers['Retry-After']=='60'
    with admin.connect() as db:
        assert db.execute(text('SELECT rate_count FROM research.credentials')).scalar_one()==60
        assert db.execute(text('SELECT count(*) FROM research.items')).scalar_one()==0
        assert db.execute(text('SELECT count(*) FROM research.pat_audit')).scalar_one()==60


def test_final_lock_wait_rechecks_expiry_before_mutation(pat_client,admin,monkeypatch):
    _,auth=identity(pat_client,admin);created=issue(pat_client,auth)
    original=pats.observe
    def expire_after_admission(sessions,token):
        principal=original(sessions,token)
        with admin.begin() as db:
            # Keep valid issued<expires constraint while making the final now expired.
            db.execute(text("UPDATE research.credentials SET issued_at=now()-interval '2 days', expires_at=now()-interval '1 day' WHERE id=:id"),{'id':principal.credential})
        return principal
    monkeypatch.setattr(pats,'observe',expire_after_admission)
    assert call(pat_client,created['token'],'research_document_save',{'title':'no','content':'no','mutationId':str(uuid4())}).status_code==401
    with admin.connect() as db:assert db.execute(text('SELECT count(*) FROM research.items')).scalar_one()==0


def test_populated_008_upgrade_and_restore_revoke_pats_preserve_provenance(admin,monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url
    from app.migrate import migrate
    from app.research_schema_contract import validate_research
    from app.restore_research import restore_candidate
    from app.research import Store, DocumentInput
    from app.version_authors import VersionAuthor
    from tests.recovery_probe import signature
    database='restore_pat_'+uuid4().hex
    url=make_url(os.environ['ADMIN_DATABASE_URL']).set(database=database)
    with admin.connect().execution_options(isolation_level='AUTOCOMMIT') as control:
        control.execute(text('CREATE DATABASE '+database))
    candidate=create_engine(url,hide_parameters=True)
    try:
        monkeypatch.setenv('ADMIN_DATABASE_URL',url.render_as_string(hide_password=False))
        migrate('0008_version_authors')
        with candidate.begin() as db:
            owner=db.execute(text("INSERT INTO auth.accounts(github_id,login,is_approved,role) VALUES('991991','synthetic-pat',true,'editor') RETURNING id")).scalar_one()
            item=db.execute(text("INSERT INTO research.items(owner_id,kind) VALUES(:owner,'document') RETURNING id"),{'owner':owner}).scalar_one()
            version=db.execute(text("INSERT INTO research.versions(item_id,owner_id,kind,number,title,content,author_kind,author_conversation_id) VALUES(:item,:owner,'document',1,'Retained','Original','codex',:conversation) RETURNING id"),{'item':item,'owner':owner,'conversation':uuid4()}).scalar_one()
            db.execute(text("INSERT INTO research.document_drafts(owner_id,document_id,base_document_version,base_version_id,title,content) VALUES(:owner,:item,1,:version,'Human draft','Retained draft')"),{'owner':owner,'item':item,'version':version})
            db.execute(text("INSERT INTO research.document_proposals(owner_id,document_id,base_document_version,base_version_id,title,content) VALUES(:owner,:item,1,:version,'Old proposal','Retained proposal')"),{'owner':owner,'item':item,'version':version})
            release=uuid4()
            db.execute(text("INSERT INTO research.publications(id,document_id,owner_id,version_id,snapshot) VALUES(:release,:item,:owner,:version,CAST(:snapshot AS jsonb))"),{'release':release,'item':item,'owner':owner,'version':version,'snapshot':json.dumps({'content':'Original public snapshot','author':{'login':'synthetic-pat'},'materials':[]})})
            db.execute(text('INSERT INTO research.publication_heads(document_id,owner_id,publication_id) VALUES(:item,:owner,:release)'),{'item':item,'owner':owner,'release':release})
            before=signature(db)
            version_rows = {table: [dict(row._mapping) for row in db.execute(text(
                'SELECT * FROM research.'+table+' ORDER BY id'))] for table in ('versions', 'post_versions')}
        migrate('0009_research_pats');migrate('0009_research_pats')
        with candidate.begin() as db:
            assert validate_research(db)=='0009_research_pats'
            after=signature(db)
            for table in before:
                if table not in {'research.versions', 'research.post_versions'}:
                    assert before[table]==after[table], table
            # Both tables gain author_name, including migration-seeded legacy
            # posts. Compare every original field and require only the new NULL.
            for table, original in version_rows.items():
                rows = [dict(row._mapping) for row in db.execute(text('SELECT * FROM research.'+table+' ORDER BY id'))]
                assert all(row['author_name'] is None for row in rows)
                assert [{key: value for key, value in row.items() if key != 'author_name'} for row in rows] == original
            old=db.execute(text('SELECT title,content,author_kind,author_conversation_id,author_name FROM research.versions WHERE id=:id'),{'id':version}).one()
            assert old.title=='Retained' and old.content=='Original' and old.author_kind=='codex' and old.author_name is None
            credential=uuid4();token=pats.issue_value(credential)
            db.execute(text("INSERT INTO research.credentials(id,owner_id,name,scope,purpose,token_hash,expires_at) VALUES(:id,:owner,'Retained PAT','write',:purpose,:hash,now()+interval '1 day')"),{'id':credential,'owner':owner,'purpose':pats.PURPOSE,'hash':pats.token_hash(token)})
        from sqlalchemy.orm import Session
        with Session(candidate) as db,db.begin():
            saved=Store(db,owner,author=VersionAuthor(credential_id=credential,name='Retained PAT')).create_item('document',DocumentInput(title='PAT version',content='Retained PAT body'))
        with candidate.connect() as db:before_restore=signature(db)
        assert restore_candidate(url.render_as_string(hide_password=False),os.environ['API_DATABASE_PASSWORD'],database)=='0009_research_pats'
        with candidate.connect() as db:
            assert validate_research(db)=='0009_research_pats' and signature(db)==before_restore
            assert db.execute(text('SELECT revoked_at IS NOT NULL FROM research.credentials WHERE id=:id'),{'id':credential}).scalar_one()
            assert db.execute(text('SELECT author_name FROM research.versions WHERE id=:id'),{'id':saved['latestVersion']['id']}).scalar_one()=='Retained PAT'
        with Session(candidate) as db:
            from app.pat_models import Credential
            from app.models import Account
            from app.research import ResearchError
            with pytest.raises(ResearchError):pats.resolve(db.get(Credential,credential),token,db.get(Account,owner))
    finally:
        candidate.dispose()
        with admin.connect().execution_options(isolation_level='AUTOCOMMIT') as control:
            control.execute(text('DROP DATABASE '+database+' WITH (FORCE)'))


def test_removed_approval_valid_cookie_can_list_revoke_but_not_issue_and_foreign_hidden(pat_client,admin):
    owner,auth=identity(pat_client,admin);created=issue(pat_client,auth)
    with admin.begin() as db:db.execute(text('UPDATE auth.accounts SET is_approved=false WHERE id=:id'),{'id':owner})
    listed=pat_client.get('/api/research/pats')
    assert listed.status_code==200 and 'effectiveScope' not in listed.json()['items'][0]
    assert pat_client.post('/api/research/pats',json={'name':'no','scope':'read'},headers=headers(auth)).status_code==403
    assert pat_client.delete('/api/research/pats/'+created['credential']['id'],headers=headers(auth)).status_code==200
    _,other_auth=identity(pat_client,admin,number='222222')
    assert pat_client.delete('/api/research/pats/'+created['credential']['id'],headers=headers(other_auth)).status_code==404
    assert all(item['id']!=created['credential']['id'] for item in pat_client.get('/api/research/pats').json()['items'])


def test_audit_insert_failure_rolls_back_mutation_receipt_but_not_admission(pat_client,admin):
    from sqlalchemy import event
    from sqlalchemy.exc import SQLAlchemyError
    from app.pat_models import PatAudit
    _,auth=identity(pat_client,admin);created=issue(pat_client,auth)
    args={'title':'Atomic','content':'Must not partially commit','mutationId':str(uuid4())}
    def refuse_audit(*args):raise SQLAlchemyError('synthetic audit failure without user inputs')
    event.listen(PatAudit,'before_insert',refuse_audit)
    try:
        response=call(pat_client,created['token'],'research_document_save',args)
        assert response.status_code==503,response.text
    finally:event.remove(PatAudit,'before_insert',refuse_audit)
    with admin.connect() as db:
        assert db.execute(text('SELECT count(*) FROM research.items')).scalar_one()==0
        assert db.execute(text('SELECT count(*) FROM research.idempotency')).scalar_one()==0
        assert db.execute(text('SELECT count(*) FROM research.pat_audit')).scalar_one()==0
        assert db.execute(text('SELECT rate_count FROM research.credentials')).scalar_one()==1
    assert call(pat_client,created['token'],'research_document_save',args).status_code==200
    with admin.connect() as db:
        assert db.execute(text('SELECT count(*) FROM research.versions')).scalar_one()==1
        assert db.execute(text('SELECT count(*) FROM research.pat_audit')).scalar_one()==1


def test_pat_pages_equal_issued_time_tie_break_and_approval_loss(pat_client,admin):
    owner,auth=identity(pat_client,admin)
    created=[issue(pat_client,auth,name='Page '+str(index)) for index in range(4)]
    ids=sorted([item['credential']['id'] for item in created],reverse=True)
    with admin.begin() as db:
        db.execute(text("UPDATE research.credentials SET issued_at='2026-11-17T00:00:00Z',expires_at='2030-01-01T00:00:00Z' WHERE owner_id=:id"),{'id':owner})
        db.execute(text('UPDATE auth.accounts SET is_approved=false WHERE id=:id'),{'id':owner})
    collected=[];cursor=None
    for _ in range(3):
        params={'limit':2,**({'cursor':cursor} if cursor else {})}
        response=pat_client.get('/api/research/pats',params=params)
        assert response.status_code==200,response.text
        page=response.json();collected.extend(row['id'] for row in page['items'])
        assert all('effectiveScope' not in row and 'scope' not in row for row in page['items'])
        cursor=page['nextCursor']
        if cursor is None:break
    assert collected==ids and len(set(collected))==4 and cursor is None
