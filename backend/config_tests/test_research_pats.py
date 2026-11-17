"""PAT policy/client/management doubles. DB concurrency is review-owned."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from app import research_pats as pats
from app import research_credentials as turn
from app.pat_management import Create, register
from app.pat_models import Credential, Issuance
from app.research import ResearchError
from app.research_rate import Guard, client_key
from app.version_authors import VersionAuthor, value
from app.research_results import success
from app.research_receipts import MUTATIONS
from config_tests.research_result_fixtures import save_result
from runner import research_cli

spec = importlib.util.spec_from_file_location('external_pat_client', Path(__file__).parents[1]/'client/anxious-research-pat/scripts/pat-client.py')
client_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client_module)


def principal():
    owner, identity = uuid4(), uuid4()
    token = pats.issue_value(identity)
    row = SimpleNamespace(id=identity, owner_id=owner, name='External researcher', scope='write', purpose=pats.PURPOSE,
        token_hash=pats.token_hash(token), expires_at=pats.now()+timedelta(days=1), revoked_at=None)
    account = SimpleNamespace(id=owner, is_approved=True, role='editor')
    return row, token, account


def test_pat_is_hash_only_and_both_principal_parsers_reject_other_purpose():
    row, token, account = principal()
    assert len(token) == 86 and pats.locate(token) == row.id
    assert token not in repr(vars(row))
    assert pats.resolve(row, token, account).credential == row.id
    with pytest.raises(ResearchError): turn.locate(token)
    with pytest.raises(ResearchError): pats.locate('rturn1.'+str(uuid4())+'.'+str(uuid4())+'.'+'a'*43)
    with pytest.raises(ResearchError): pats.locate('provider-secret')


@pytest.mark.parametrize('damage', ['hash','purpose','expiry','revoked','owner','uuid','malformed'])
def test_fresh_credential_validation_rejects_invalid_principals(damage):
    row, token, account = principal()
    if damage == 'hash': token = token[:-1]+('A' if token[-1]!='A' else 'B')
    if damage == 'purpose': row.purpose = turn.PURPOSE
    if damage == 'expiry': row.expires_at = pats.now()-timedelta(seconds=1)
    if damage == 'revoked': row.revoked_at = pats.now()
    if damage == 'owner': account.id = uuid4()
    if damage == 'uuid': row.id = uuid4()
    if damage == 'malformed': token = 'rpat1.'+'x'*36+'.'+'a'*43
    with pytest.raises(ResearchError) as error: pats.resolve(row, token, account)
    assert error.value.status == 401


@pytest.mark.parametrize('role,approved,scope,effective', [
    ('commenter',True,'write','public'),('commenter',True,'read','public'),
    ('editor',True,'write','write'),('admin',True,'read','write'),('editor',True,'read','write'),
    ('editor',False,'write','none')])
def test_fresh_role_limits_selected_scope_and_cached_results_cannot_bypass(role,approved,scope,effective):
    row, token, account = principal(); account.role, account.is_approved, row.scope = role,approved,scope
    checked = pats.resolve(row, token, account)
    assert checked.scope == effective
    if effective in {'public','none'}:
        with pytest.raises(ResearchError): pats.allowed(checked,'research_get',MUTATIONS)
    else:
        pats.allowed(checked,'research_get',MUTATIONS)
        pats.allowed(checked,'research_document_save',MUTATIONS)
    with pytest.raises(ResearchError): pats.allowed(checked,'research_post_publish',MUTATIONS)


@pytest.mark.parametrize('body', [{'name':'x','scope':'write','expiryDays':True}, {'name':' ','scope':'read'},
 {'name':'x','scope':'all'}, {'name':'x','scope':'write','expiryDays':0}, {'name':'x','scope':'read','expiryDays':91},
 {'name':'x\nsecret','scope':'read'}, {'name':'x','scope':'read','ownerId':str(uuid4())}])
def test_issue_strictly_rejects_invalid_fields(body):
    with pytest.raises((ValidationError,ResearchError)): Create.model_validate(body)


def test_retired_pat_text_is_redacted_and_rejected_including_nested_keys_and_internal_cli():
    _,token,_ = principal(); body={token:[{'text':'retired '+token}]}
    assert token not in json.dumps(turn.redact(body))
    assert token not in json.dumps(research_cli.safe(body))
    assert token not in json.dumps(client_module.safe(body))
    with pytest.raises(ResearchError): turn.reject_token_input(body)
    with pytest.raises((ValidationError,ResearchError)): Create(name=token,scope='read')


def test_pat_author_is_kept_internally_but_excluded_from_results():
    identity = uuid4(); author = VersionAuthor(credential_id=identity,name='External researcher')
    row = SimpleNamespace(**author.columns())
    expected = {'kind':'pat','conversationId':None,'credentialId':str(identity),'name':'External researcher'}
    assert value(row) == expected
    result=save_result()
    assert 'author' not in result['latestVersion']
    assert success('research_document_save',result)
    for damage in [{'kind':'pat','conversationId':None}, {**expected,'credentialId':'foreign'},
                   {**expected,'conversationId':str(uuid4())}, {**expected,'name':''}]:
        changed=deepcopy(result); changed['latestVersion']['author']=damage
        assert not success('research_document_save',changed)
    assert value(SimpleNamespace()) == {'kind':'human','conversationId':None}


def test_anonymous_guard_does_not_reset_buckets_by_eviction_or_before_window():
    clock=[0]; guard=Guard(lambda:clock[0])
    assert all(guard.admit('one') for _ in range(120))
    assert not guard.admit('one')
    assert all(guard.admit(str(n)) for n in range(480))
    assert not guard.admit('new') and guard.total == 600
    clock[0]=59.99; assert not guard.admit('new')
    clock[0]=60; assert guard.admit('one') and guard.total == 1


@pytest.mark.parametrize('peer,xff,expected', [
 ('203.0.113.1',['victim'],'203.0.113.1'),('10.0.0.1',['203.0.113.9, 10.0.0.2'],'203.0.113.9'),
 ('10.0.0.1',['victim'],'10.0.0.1'),('10.0.0.1',['1.1.1.1','2.2.2.2'],'10.0.0.1'),
 ('10.0.0.1',['1.1.1.1, 198.51.100.1'],'198.51.100.1')])
def test_only_trusted_peer_can_supply_bounded_unambiguous_client_chain(peer,xff,expected):
    import ipaddress
    from starlette.requests import Request
    request=Request({'type':'http','client':(peer,1),'headers':[(b'x-forwarded-for',v.encode()) for v in xff]})
    assert client_key(request,(ipaddress.ip_network('10.0.0.0/24'),)) == expected


def private_file(tmp_path):
    root=tmp_path/'private';root.mkdir(mode=0o700)
    path=root/'token';path.write_text(pats.issue_value(uuid4()));path.chmod(0o600)
    return path


@pytest.mark.parametrize('damage', ['symlink','hardlink','mode','parent-mode','directory','fifo','foreign','oversize'])
def test_external_client_rejects_unsafe_credential_files(tmp_path,monkeypatch,damage):
    import os
    path=private_file(tmp_path)
    if damage=='symlink':
        other=path.with_name('alias');other.symlink_to(path);path=other
    if damage=='hardlink': os.link(path,path.with_name('alias'))
    if damage=='mode': path.chmod(0o644)
    if damage=='parent-mode': path.parent.chmod(0o755)
    if damage=='directory': path.unlink();path.mkdir(mode=0o600)
    if damage=='fifo': path.unlink();os.mkfifo(path,0o600)
    if damage=='foreign': monkeypatch.setattr(client_module.os,'geteuid',lambda:123456)
    if damage=='oversize': path.write_text('x'*1025)
    with pytest.raises((ValueError,OSError)): client_module.read_token(str(path))


def test_external_client_reads_file_without_secret_in_argv_and_ignores_environment(tmp_path,monkeypatch):
    path=private_file(tmp_path)
    assert client_module.read_token(str(path))==path.read_text()
    monkeypatch.setenv('SSL_CERT_FILE','/must-not-read');monkeypatch.setenv('SSLKEYLOGFILE','/must-not-write')
    monkeypatch.setenv('HTTPS_PROXY','http://must-not-use')
    context=client_module.tls_context()
    import ssl
    assert context.check_hostname and context.verify_mode==ssl.CERT_REQUIRED and context.keylog_filename is None


@pytest.mark.parametrize('origin', ['http://example.com','https://example.com:8443','https://x@host',
 'https://example.com/path','https://example.com?token=x','https://example.com\\evil','https://EXAMPLE.com'])
def test_external_client_refuses_noncanonical_or_non_https_origin(origin):
    with pytest.raises(ValueError): client_module.origin_host(origin)


def test_external_client_never_redirects_or_retries_and_redacts_even_error_json(monkeypatch):
    seen=[]; token=pats.issue_value(uuid4())
    class Connection:
        def __init__(self,*args,**kwargs): seen.append(('connect',args))
        def request(self,*args,**kwargs): seen.append(('request',args))
        def getresponse(self): return SimpleNamespace(status=302,read=lambda size:json.dumps({'token':token}).encode())
        def close(self): seen.append(('close',))
    monkeypatch.setattr(client_module.http.client,'HTTPSConnection',Connection)
    status,result=client_module.exchange('https://platform.example','/api/public/documents')
    assert status==302 and token not in json.dumps(result)
    assert [e[0] for e in seen]==['connect','request','close']


class FakeDB:
    def __init__(self):
        self.account=SimpleNamespace(id=uuid4(),is_approved=True,role='commenter')
        self.login=SimpleNamespace(account_id=self.account.id,csrf_token='csrf',expires_at=pats.now()+timedelta(hours=1))
        self.rows={};self.issuances={};self.active=0;self.open=0
    @contextmanager
    def begin(self):
        self.open+=1
        try: yield self
        finally:self.open-=1
    def scalar(self,query):
        entity=query.column_descriptions[0].get('entity')
        return self.account if entity is not Credential and entity is not None else self.active
    def get(self,model,key): return (self.issuances if model is Issuance else self.rows).get(key)
    def add(self,row):
        if isinstance(row,Credential):self.rows[row.id]=row
        else:self.issuances[(row.owner_id,row.key)]=row
    def flush(self):pass


def management():
    db=FakeDB();app=FastAPI();app.state.research_agent_api_enabled=True;app.state.research_policy='editors'
    @app.exception_handler(ResearchError)
    async def error(request,exc):return JSONResponse({'error':{'code':exc.code}},status_code=exc.status)
    register(app,db,SimpleNamespace(base_path='',origin='https://platform.example'),
        lambda session,request:db.login if request.cookies.get('anxious_session')=='valid' else None,
        lambda value,status=200:JSONResponse(turn.redact(value),status_code=status))
    client=TestClient(app,base_url='https://platform.example');client.cookies.set('anxious_session','valid')
    return db,client


def test_management_one_time_secret_ack_loss_replay_precedes_cap_and_fresh_role(monkeypatch):
    monkeypatch.setattr('app.research_api.require_schema',lambda connection:None)
    monkeypatch.setattr(FakeDB,'connection',lambda self:None,raising=False)
    db,client=management();headers={'Origin':'https://platform.example','X-CSRF-Token':'csrf','Idempotency-Key':str(uuid4())}
    first=client.post('/api/research/pats',json={'name':'External researcher','scope':'write'},headers=headers)
    assert first.status_code==201,first.text
    token=first.json()['token'];assert pats.TOKEN.fullmatch(token) and db.open==0
    assert set(first.json()['credential']) == {'id','name','issuedAt','expiresAt','revokedAt','lastUsedAt'}
    assert next(iter(db.rows.values())).scope == 'write'
    assert token not in repr([vars(row) for row in db.rows.values()])
    db.active=10
    replay=client.post('/api/research/pats',json={'name':'External researcher','scope':'write'},headers=headers)
    assert replay.status_code==200 and replay.json()['token'] is None and replay.json()['replayed']
    assert replay.json()['credential']['id']==first.json()['credential']['id']
    assert client.post('/api/research/pats',json={'name':'changed','scope':'read'},headers=headers).status_code==409
    db.account.is_approved=False
    assert client.post('/api/research/pats',json={'name':'External researcher','scope':'write'},headers=headers).status_code==403
    client.cookies.clear()
    assert client.post('/api/research/pats',json={'name':'x','scope':'write'},headers={'Authorization':'Bearer '+token}).status_code==401


def test_pat_syntax_holdback_masks_every_chunk_boundary_without_turn_usage_marker():
    from runner.stream import Holdback
    from runner.stream_prefix import HoldPolicy
    from runner.projection import ProjectionJournal
    token=pats.issue_value(uuid4())
    journal=ProjectionJournal.create('synthetic-owner','synthetic-trial')
    assert token not in journal.sanitize({'text':token})['text']
    for boundary in range(1,len(token)):
        hold=Holdback(); emitted=''
        for chunk,final in [('prefix '+token[:boundary],False),(token[boundary:]+' suffix',True)]:
            hold.feed(chunk)
            planned,consumed,joined=hold.plan(hold.raw,journal.spans(hold.raw),HoldPolicy.none(),final,hold.joined)
            emitted+=planned
            hold.commit(consumed,joined,len(hold.raw))
        assert token not in emitted and '[redacted]' in emitted and emitted.startswith('prefix ')


def test_service_journal_masks_pat_without_a_research_turn_marker(tmp_path):
    from config_tests.test_service_access import setup
    auth,home,envelope=setup(tmp_path)
    auth.install(envelope)
    try:
        token=pats.issue_value(uuid4())
        assert not getattr(auth,'research_api_redaction',False)
        assert token not in auth.sanitize({'pasted':token})['pasted']
        assert auth.journal.spans(token)==[(0,len(token))]
    finally:auth.close()


def test_client_argument_errors_never_echo_accidentally_pasted_credentials(capsys):
    token=pats.issue_value(uuid4())
    assert client_module.main(['--origin','https://platform.example','--token',token,'research_list'])==1
    captured=capsys.readouterr()
    assert token not in captured.out+captured.err and not captured.err


def test_fixed_public_distribution_serves_real_client_skill_and_operation_schema():
    from app.pat_distribution import register as distribution
    app=FastAPI();distribution(app,SimpleNamespace(base_path='/base'),lambda body:JSONResponse(body))
    with TestClient(app) as client:
        code=client.get('/base/api/research/client/pat-client.py')
        skill=client.get('/base/api/research/client/SKILL.md')
        schema=client.get('/base/api/research/client/operations.json')
        assert code.status_code==200 and 'def read_token' in code.text and code.headers['Cache-Control']=='no-store'
        assert skill.status_code==200 and skill.text.startswith('---\nname: anxious-research-pat')
        assert schema.status_code==200 and 'research_document_save' in schema.json() and 'research_page_context' not in schema.json()
        assert client.get('/base/api/research/client/private-file').status_code==404


def test_pat_list_uses_issued_time_cursor_on_both_pages_after_approval_loss(monkeypatch):
    from sqlalchemy.dialects import postgresql
    monkeypatch.setattr('app.research_api.require_schema',lambda connection:None)
    monkeypatch.setattr(FakeDB,'connection',lambda self:None,raising=False)
    db,client=management()
    at=pats.now()
    ids=sorted([uuid4(),uuid4()],reverse=True)
    rows=[Credential(id=identity,owner_id=db.account.id,name='Page '+str(i),scope='read',purpose=pats.PURPOSE,
         token_hash='a'*64,created_at=at,expires_at=at+timedelta(days=30),rate_count=0) for i,identity in enumerate(ids)]
    queries=[];available=[rows,rows[1:]]
    def scalars(query):
        queries.append(query)
        return SimpleNamespace(all=lambda:available.pop(0))
    monkeypatch.setattr(db,'scalars',scalars,raising=False)
    first=client.get('/api/research/pats?limit=1')
    assert first.status_code==200,first.text
    page=first.json();assert page['items'][0]['id']==str(ids[0]) and page['nextCursor']
    db.account.is_approved=False
    second=client.get('/api/research/pats',params={'limit':1,'cursor':page['nextCursor']})
    assert second.status_code==200,second.text
    assert second.json()['items'][0]['id']==str(ids[1])
    assert set(second.json()['items'][0])=={'id','name','issuedAt','expiresAt','revokedAt','lastUsedAt'}
    assert second.json()['nextCursor'] is None
    for query in queries:
        sql=str(query.compile(dialect=postgresql.dialect()))
        assert 'ORDER BY research.credentials.issued_at DESC, research.credentials.id DESC' in sql
        assert 'updated_at' not in sql
    # Equal issuedAt uses the strictly descending UUID predicate on page2.
    compiled=queries[1].compile(dialect=postgresql.dialect())
    assert 'research.credentials.id <' in str(compiled)
    assert ids[0] in compiled.params.values() and at in compiled.params.values()


@pytest.mark.parametrize('cursor',['not-base64','x'*513,'W10','WyJub3QtdGltZSIsIngiXQ'])
def test_pat_cursor_invalid_inputs_are_fixed_422(cursor):
    from sqlalchemy import select
    from app.research import page_query
    with pytest.raises(ResearchError) as caught:
        page_query(select(Credential),Credential,1,cursor,time_field='created_at')
    assert caught.value.code=='validation_error' and caught.value.status==422
