"""#104 DTO/legacy retry/public documentation checks; no DB/network/model."""
from copy import deepcopy
import json
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest

from app.pat_distribution import register as distribution
from app.research import Store, ResearchError, mutation_fingerprint
from app.research_api import operation_failure
from app.version_authors import without_version_authors
from config_tests.research_result_fixtures import save_result
from config_tests.test_research_pats import FakeDB, management


@pytest.mark.parametrize('base',['','/deep/test'])
def test_llm_is_public_db_free_offline_and_uses_configured_origin_once(base):
    app=FastAPI();app.state.research_agent_api_enabled=False
    settings=SimpleNamespace(base_path=base,origin='https://platform.example')
    distribution(app,settings,lambda body:JSONResponse(body))
    # There is no DB/session attribute; any attempted checkout would fail.
    with TestClient(app,base_url='https://host-attacker.invalid') as client:
        response=client.get(base+'/api/research/llm.md',headers={'X-Forwarded-Host':'header-attacker.invalid'})
    assert response.status_code==200 and response.headers['Content-Type'].startswith('text/markdown')
    assert response.headers['Cache-Control']=='no-store' and response.headers['X-Content-Type-Options']=='nosniff'
    assert 'https://platform.example'+base+'/api/research/agent/v1' in response.text
    assert 'https://platform.example'+base+'/api/research/client/operations.json' in response.text
    assert 'attacker.invalid' not in response.text and '/deep/test/deep/test' not in response.text
    assert 'Authorization: Bearer <PAT>' in response.text and 'expectedVersion' in response.text and 'mutationId' in response.text
    assert 'Publishing and withdrawal are human actions' in response.text and 'commenters' in response.text
    assert r'JSON-encode once: wire `\n`, not `\\n`.' in response.text
    assert 'table-cell backgrounds or embeds' in response.text
    # #107: formatted posts are edited with patches; Markdown loss is refused unless allowed.
    for phrase in ('post-block-patch','formatting_would_be_lost','allowFormatLoss','latestVersion.content','1,000,000 bytes','8,000,000 bytes','[REDACTED]'):
        assert phrase in response.text,phrase


def test_documented_post_example_preserves_structure_with_one_json_encoding():
    from app.post_block_markdown import convert
    from app.post_blocks import text_projection
    source='## Findings\n\n**Evidence** and [source](https://example.com).\n\n- First\n- Second\n\n> Context\n\n| Item | Result |\n| --- | --- |\n| One | Yes |\n\n```python\nprint({"answer": 42})\n```\n'
    wire=json.dumps({'contentInput':{'format':'markdown','text':source}})
    decoded=json.loads(wire)['contentInput']['text']
    document,warnings=convert(decoded,'documented-example')
    assert warnings==[]
    assert [block['type'] for block in document['blocks']]==['heading','paragraph','list','quote','table','code']
    assert document['blocks'][0]['level']==2
    assert document['blocks'][-1]['text']=='print({"answer": 42})\n'
    assert 'Evidence' in text_projection(document)
    # Double-escaped newlines are literal backslashes, not Markdown structure.
    literal,_=convert(source.replace('\n',r'\n'),'double-encoded-example')
    assert len(literal['blocks'])==1


@pytest.mark.parametrize('old_scope',['read','write'])
def test_pre104_ack_loss_retry_matches_name_expiry_without_reissuing_or_backfilling(monkeypatch,old_scope):
    monkeypatch.setattr('app.research_api.require_schema',lambda connection:None)
    monkeypatch.setattr(FakeDB,'connection',lambda self:None,raising=False)
    db,client=management();key=str(uuid4())
    headers={'Origin':'https://platform.example','X-CSRF-Token':'csrf','Idempotency-Key':key}
    reply=client.post('/api/research/pats',json={'name':'Legacy token','expiryDays':30},headers=headers)
    assert reply.status_code==201,reply.text
    identity=reply.json()['credential']['id']
    row=next(iter(db.rows.values()));assert row.scope=='write'
    row.scope=old_scope
    issuance=next(iter(db.issuances.values()))
    assert issuance.fingerprint==mutation_fingerprint('pat:create',{'name':'Legacy token','expiryDays':30,'scope':'write'})
    issuance.fingerprint=mutation_fingerprint('pat:create',{'name':'Legacy token','expiryDays':30,'scope':old_scope})
    previous=issuance.fingerprint;db.active=10
    for ignored in ({},{'scope':'read'},{'scope':'write'},{'scope':None}):
        replay=client.post('/api/research/pats',json={'name':' Legacy token ','expiryDays':30,**ignored},headers=headers)
        assert replay.status_code==200,replay.text
        value=replay.json()
        assert value['credential']['id']==identity and value['token'] is None and not value['secretAvailable'] and value['replayed']
        assert set(value['credential'])=={'id','name','access','issuedAt','expiresAt','revokedAt','lastUsedAt'}
    assert row.scope==old_scope and issuance.fingerprint==previous and len(db.rows)==len(db.issuances)==1
    for changed in ({'name':'different','expiryDays':30},{'name':'Legacy token','expiryDays':31}):
        assert client.post('/api/research/pats',json=changed,headers=headers).status_code==409
    db.account.is_approved=False
    assert client.post('/api/research/pats',json={'name':'Legacy token','expiryDays':30},headers=headers).status_code==403


def legacy_result(kind='document'):
    value=save_result(kind)
    value['latestVersion']['author']={'kind':'pat','conversationId':None,'credentialId':str(uuid4()),'name':'Private credential'}
    return value


def test_projection_hides_only_version_metadata_and_keeps_public_byline_content_and_other_ids():
    original=legacy_result('post')
    original['author']={'login':'public-owner'}
    # Opaque content can contain author and version-like keys as user data.
    original['latestVersion']['content']={'author':'user content','number':1,'itemId':'user supplied'}
    direct={'id':str(uuid4()),'itemId':str(uuid4()),'number':1,'author':{'kind':'human','conversationId':None}}
    publication={'postId':str(uuid4()),'versionId':str(uuid4()),'author':{'login':'public-owner'}}
    body={'error':{'details':{'current':original,'versions':{'items':[direct]},'publication':publication}},
          'conversationId':str(uuid4())}
    before=deepcopy(body);value=without_version_authors(body)
    current=value['error']['details']['current']
    assert 'author' not in current['latestVersion'] and 'author' not in value['error']['details']['versions']['items'][0]
    assert current['author']==original['author'] and current['latestVersion']['content']==original['latestVersion']['content']
    assert value['error']['details']['publication']==publication and value['conversationId']==body['conversationId']
    assert body==before


def test_cached_save_projection_does_not_mutate_db_response_or_repeat_action():
    from app.research_results import success
    args={'title':'document','content':'# saved','mutationId':str(uuid4())}
    original=legacy_result();before=deepcopy(original);owner=uuid4()
    stored=SimpleNamespace(fingerprint=mutation_fingerprint('tool:research_document_save',args),status=200,response=original)
    class DB:
        def execute(self,*args):pass
        def get(self,model,identity):return stored
    def forbidden():raise AssertionError('Cached save was rerun')
    result,status=Store(DB(),owner).mutate(args['mutationId'],'tool:research_document_save',args,forbidden)
    assert status==200 and 'author' not in result['latestVersion'] and success('research_document_save',result,args)
    assert result['latestVersion']['id']==original['latestVersion']['id'] and original==before
    error,status=operation_failure(ResearchError('conflict','Reload',409,{'current':original}))
    assert status==409 and 'author' not in error['error']['details']['current']['latestVersion'] and original==before
