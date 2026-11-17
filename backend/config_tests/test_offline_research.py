"""Test-only trigger policy and fixed evidence; no DB, socket or model."""
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest

from app.research import ResearchError
from tests.offline_research import Held, checked, register, result


def test_held_status_contains_no_grant_or_native_cli_claim():
    held=Held();identity=uuid4();request=str(uuid4())
    held.submit(identity,{'requestId':request,'text':'synthetic input'})
    assert held.read(identity)['state']=='running'
    held.finish()
    record=held.read(identity)
    assert record['state']=='idle' and record['turnId']=='held-turn'
    assert [item['type'] for item in record['record']['turns'][0]['items']]==['userMessage','agentMessage']
    assert held.health()['verification']=='fixture'


def test_trigger_requires_explicit_offline_factory_and_disabled_real_flags(monkeypatch):
    app=SimpleNamespace(state=SimpleNamespace(user_runners_enabled=False,research_agent_api_enabled=False))
    monkeypatch.setenv('APP_ENV','production');monkeypatch.setenv('OFFLINE_RUNNER_FIXTURE','true')
    with pytest.raises(RuntimeError):register(app)
    monkeypatch.setenv('APP_ENV','test');app.state.user_runners_enabled=True
    with pytest.raises(RuntimeError):register(app)
    app.state.user_runners_enabled=False;app.state.research_agent_api_enabled=True
    with pytest.raises(RuntimeError):register(app)
    monkeypatch.setenv('OPENAI_API_KEY','synthetic-never-used')
    with pytest.raises(RuntimeError):register(app)


def test_evidence_returns_fixed_ids_only_and_rejects_fabricated_saved_author():
    session,request,resource,version,receipt=[str(uuid4()) for _ in range(5)]
    calls={'items':[{'operation':'research_document_save','status':'succeeded','requestId':request,
                    'id':receipt,'saved':{'versionId':version}}]}
    document={'id':resource,'latestVersion':{'author':{'kind':'codex','conversationId':session}}}
    assert result({'id':session},calls,document)=={'sessionId':session,'requestId':request,'resourceId':resource,
             'versionId':version,'receiptId':receipt,'callbackRejected':True}
    document['latestVersion']['author']={'kind':'human','conversationId':None}
    with pytest.raises(ResearchError):result({'id':session},calls,document)
    with pytest.raises(ResearchError):checked(SimpleNamespace(status_code=503),{200})


def test_missing_cookie_is_rejected_before_fixture_runner_mapping_or_mutation(monkeypatch):
    monkeypatch.setenv('APP_ENV','test');monkeypatch.setenv('OFFLINE_RUNNER_FIXTURE','true')
    app=FastAPI();app.state.user_runners_enabled=app.state.research_agent_api_enabled=False
    app.state.settings=SimpleNamespace(base_path='')
    @contextmanager
    def denied(request,mutate):
        assert mutate and not request.cookies
        raise ResearchError('unauthenticated','Authentication required',401)
        yield
    app.state.research_authorized=denied
    @app.exception_handler(ResearchError)
    async def error(request,exc):return JSONResponse({'error':{'code':exc.code}},status_code=exc.status)
    register(app)
    with TestClient(app) as client:
        response=client.post('/api/research/__mock/offline/research-save',json={},
                             headers={'Idempotency-Key':str(uuid4())})
        assert response.status_code==401 and not hasattr(app.state,'research_runners')
        assert client.post('/__mock/offline/research-save',json={}).status_code==404
