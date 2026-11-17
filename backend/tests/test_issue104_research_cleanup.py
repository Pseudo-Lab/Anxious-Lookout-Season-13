"""Review-owned #104 DB/API gate; no live tokens, migration, VM or model."""
from copy import deepcopy
import hashlib
import json
from uuid import uuid4

from sqlalchemy import text

from app.research import mutation_fingerprint
from tests.test_research import identity, headers, create, material
from tests.test_research_pats import pat_client, issue, call
from tests.test_agent_research_saves import turn
from tests.test_page_chat import callback
from tests.test_posts import proposal_content
from tests.test_policy_publication import connect


def test_existing_read_row_can_save_without_backfill_and_keeps_owner_role_and_publish_boundaries(pat_client,admin):
    owner,auth=identity(pat_client,admin)
    issued=pat_client.post('/api/research/pats',json={'name':'Unified token'},headers=headers(auth))
    assert issued.status_code==201,issued.text
    value=issued.json();token=value['token'];credential=value['credential']['id']
    assert set(value['credential'])=={'id','name','issuedAt','expiresAt','revokedAt','lastUsedAt'}
    with admin.begin() as db:
        assert db.execute(text('SELECT scope FROM research.credentials WHERE id=:id'),{'id':credential}).scalar_one()=='write'
        # Stand in for a row issued before #104. Production performs no backfill.
        db.execute(text("UPDATE research.credentials SET scope='read' WHERE id=:id"),{'id':credential})
        before=dict(db.execute(text('SELECT id,owner_id,name,scope,purpose,token_hash,issued_at,expires_at,revoked_at FROM research.credentials WHERE id=:id'),{'id':credential}).one()._mapping)
    saved=call(pat_client,token,'research_document_save',{'title':'Legacy read can save','content':'Body','mutationId':str(uuid4())})
    assert saved.status_code==200,saved.text
    document=saved.json();assert 'author' not in document['latestVersion']
    with admin.connect() as db:
        assert dict(db.execute(text('SELECT id,owner_id,name,scope,purpose,token_hash,issued_at,expires_at,revoked_at FROM research.credentials WHERE id=:id'),{'id':credential}).one()._mapping)==before
        row=db.execute(text('SELECT author_kind,author_credential_id,author_name FROM research.versions WHERE id=:id'),{'id':document['latestVersion']['id']}).one()
        assert (row.author_kind,str(row.author_credential_id),row.author_name)==('pat',credential,'Unified token')
    assert call(pat_client,token,'research_post_publish',{}).status_code==403
    assert call(pat_client,token,'research_post_withdraw',{}).status_code==403
    other,other_auth=identity(pat_client,admin,'222222')
    foreign=create(pat_client,other_auth,'documents')
    assert call(pat_client,token,'research_get',{'type':'document','id':foreign['id']}).status_code==404
    with admin.begin() as db:db.execute(text("UPDATE auth.accounts SET role='commenter' WHERE id=:id"),{'id':owner})
    assert call(pat_client,token,'research_get',{'type':'document','id':document['id']}).status_code==403
    assert pat_client.get('/api/public/documents',headers={'Authorization':'Bearer '+token}).status_code==200
    with admin.begin() as db:db.execute(text("UPDATE auth.accounts SET role='editor',is_approved=false WHERE id=:id"),{'id':owner})
    assert call(pat_client,token).status_code==403
    with admin.begin() as db:db.execute(text('UPDATE research.credentials SET revoked_at=now() WHERE id=:id'),{'id':credential})
    assert call(pat_client,token).status_code==401


def test_pre104_issue_fingerprint_ack_loss_keeps_same_row_and_no_secret(pat_client,admin):
    owner,auth=identity(pat_client,admin)
    key=str(uuid4());created=issue(pat_client,auth,name='Legacy issue',key=key)
    old=mutation_fingerprint('pat:create',{'name':'Legacy issue','scope':'read','expiryDays':30})
    with admin.begin() as db:
        db.execute(text('UPDATE research.pat_issuances SET fingerprint=:hash WHERE owner_id=:owner AND key=:key'),{'hash':old,'owner':owner,'key':key})
        db.execute(text("UPDATE research.credentials SET scope='read' WHERE id=:id"),{'id':created['credential']['id']})
    for legacy in ({},{'scope':'read'},{'scope':'write'},{'scope':None}):
        response=pat_client.post('/api/research/pats',json={'name':' Legacy issue ','expiryDays':30,**legacy},headers=headers(auth,key))
        assert response.status_code==200,response.text
        result=response.json()
        assert result['credential']['id']==created['credential']['id'] and result['token'] is None
        assert result['replayed'] and not result['secretAvailable']
    for changed in ({'name':'changed','expiryDays':30},{'name':'Legacy issue','expiryDays':31}):
        assert pat_client.post('/api/research/pats',json=changed,headers=headers(auth,key)).status_code==409
    with admin.connect() as db:
        assert db.execute(text('SELECT count(*) FROM research.credentials WHERE owner_id=:id'),{'id':owner}).scalar_one()==1
        assert db.execute(text('SELECT fingerprint FROM research.pat_issuances WHERE owner_id=:owner AND key=:key'),{'owner':owner,'key':key}).scalar_one()==old
        assert db.execute(text('SELECT scope FROM research.credentials WHERE id=:id'),{'id':created['credential']['id']}).scalar_one()=='read'
    with admin.begin() as db:db.execute(text('UPDATE auth.accounts SET is_approved=false WHERE id=:id'),{'id':owner})
    assert pat_client.post('/api/research/pats',json={'name':'Legacy issue'},headers=headers(auth,key)).status_code==403


def test_old_cached_codex_result_is_hidden_without_rewriting_version_receipt_or_cache(client,admin):
    owner,auth=identity(client,admin);doc=create(client,auth,'documents')
    session,runner=turn(client,owner,auth)
    args={'id':doc['id'],'expectedVersion':1,'title':'Codex version','content':'Saved','mutationId':str(uuid4())}
    saved=callback(client,session,runner,'research_document_update',args)
    assert saved.status_code==200,saved.text
    result=saved.json();legacy=deepcopy(result)
    legacy['latestVersion']['author']={'kind':'codex','conversationId':session['id']}
    with admin.begin() as db:
        db.execute(text('UPDATE research.idempotency SET response=CAST(:response AS jsonb) WHERE owner_id=:owner AND key=:key'),
                   {'response':json.dumps(legacy),'owner':owner,'key':args['mutationId']})
    replay=callback(client,session,runner,'research_document_update',args)
    assert replay.status_code==200 and replay.json()==result
    conflict=callback(client,session,runner,'research_document_update',{**args,'mutationId':str(uuid4())})
    assert conflict.status_code==409 and 'author' not in conflict.json()['error']['details']['current']['latestVersion']
    versions=client.get('/api/research/documents/'+doc['id']+'/versions').json()['items']
    assert len(versions)==2 and all('author' not in version for version in versions)
    with admin.connect() as db:
        rows=db.execute(text('SELECT author_kind,author_conversation_id FROM research.versions WHERE item_id=:id ORDER BY number'),{'id':doc['id']}).all()
        assert rows[0].author_kind=='human' and rows[0].author_conversation_id is None
        assert rows[1].author_kind=='codex' and str(rows[1].author_conversation_id)==session['id']
        assert db.execute(text('SELECT response FROM research.idempotency WHERE owner_id=:owner AND key=:key'),{'owner':owner,'key':args['mutationId']}).scalar_one()==legacy
    receipts=client.get('/api/research/sessions/'+session['id']+'/research-calls').json()['items']
    assert receipts[0]['saved']['versionId']==receipts[1]['saved']['versionId']==result['latestVersion']['id']


def test_material_post_version_dtos_omit_provenance_but_public_owner_stays(pat_client,admin):
    owner,auth=identity(pat_client,admin);created=issue(pat_client,auth)
    source=call(pat_client,created['token'],'research_material_save',{**material(),'mutationId':str(uuid4())})
    assert source.status_code==200 and 'author' not in source.json()['latestVersion']
    post=call(pat_client,created['token'],'research_post_save',{**proposal_content(),'mutationId':str(uuid4())})
    assert post.status_code==200,post.text
    post=post.json();assert post['author']['login']=='fixture-111111' and 'author' not in post['latestVersion']
    versions=pat_client.get('/api/research/posts/'+post['id']+'/versions').json()['items']
    assert versions and all('author' not in version for version in versions)
    with admin.connect() as db:
        assert db.execute(text('SELECT author_kind FROM research.post_versions WHERE id=:id'),{'id':post['latestVersion']['id']}).scalar_one()=='pat'


def test_publication_preview_token_keeps_legacy_internal_fingerprint_without_exposing_author(client,admin):
    owner,auth=identity(client,admin);doc=create(client,auth,'documents');source=create(client,auth,'materials')
    connect(client,auth,doc,source)
    response=client.get('/api/research/documents/'+doc['id']+'/publication-preview')
    assert response.status_code==200,response.text
    preview=response.json();assert all('author' not in item for item in preview['materials'])
    # Frozen #97 token input used the internal provenance of referenced versions.
    internal=[{**item,'author':{'kind':'human','conversationId':None}} for item in preview['materials']]
    old_state={'owner':str(owner),'document':doc['id'],'version':preview['versionId'],'materials':internal}
    old_token=hashlib.sha256(json.dumps(old_state,sort_keys=True,ensure_ascii=True).encode()).hexdigest()
    assert preview['previewToken']==old_token
    published=client.post('/api/research/documents/'+doc['id']+'/publications',json={
        'versionId':preview['versionId'],'expectedVersion':preview['expectedVersion'],'previewToken':old_token},headers=headers(auth))
    assert published.status_code==201,published.text
    public=client.get('/api/public/documents/'+doc['id']).json()
    assert public['author']['login']=='fixture-111111' and all('author' not in row for row in public['materials'])
