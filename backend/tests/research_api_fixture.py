"""Review DB-only research HTTP fixture. No Kube/native/provider/grant activation.

Uses the production router/transactions and the original five-connection engine.
The bound runner status/identity port is an explicit double: these tests cannot
prove real binding, service authentication, sandbox isolation or model behavior.
"""
import threading
import uuid

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.research import ResearchError
from app.research_models import Conversation
from app.models import LoginSession
from app.page_scope import request_metadata, shell_allowed
from app import research_credentials as credentials
from app.research_api import register

LOCK = threading.RLock()


def api_client(app):
    with LOCK:
        existing = getattr(app.state, 'research_db_fixture_client', None)
        if existing is not None:
            return existing
        fixture = FastAPI()
        fixture.state.engine = app.state.engine
        fixture.state.user_runners_enabled = fixture.state.research_agent_api_enabled = True
        fixture.state.research_policy = app.state.research_policy
        def response(value, status=200):
            return JSONResponse(credentials.redact(value), status_code=status)
        @fixture.exception_handler(ResearchError)
        async def domain(request, error):
            return response({'error': {'code': error.code, 'message': error.message,
                **({'details': error.details} if error.details is not None else {})}}, error.status)
        @fixture.exception_handler(ValidationError)
        async def validation(request, error):
            return response({'error': {'code': 'validation_error', 'message': 'Invalid fields'}}, 422)
        @fixture.exception_handler(SQLAlchemyError)
        async def unavailable(request, error):
            return response({'error': {'code': 'not_ready', 'message': 'Database unavailable'}}, 503)
        class Status:
            def __init__(self, owner): self.owner = owner
            def research_authorize(self, conversation, request_id):
                held = app.state.research_runners[self.owner]
                valid = (held.last is not None and held.last['requestId'] == str(request_id)
                         and str(getattr(held, 'session_id', conversation)) == str(conversation)
                         and getattr(held, 'state', 'running') == 'running')
                return {'authorized': valid, 'ownerId': str(self.owner), 'stateId': str(uuid.UUID(int=10)),
                    'podUid': str(uuid.UUID(int=20)), 'generation': 1, 'sessionId': str(conversation),
                    'requestId': str(request_id), 'nativeTurnId': 'held-turn'}
            def research_binding(self, db, result):
                # Explicitly no registry/Kube proof in this storage fixture.
                assert result['ownerId'] == str(self.owner)
        register(fixture, app.state.sessions, app.state.settings, response, Status)
        existing = TestClient(fixture, base_url=app.state.settings.origin, raise_server_exceptions=False)
        app.state.research_db_fixture_client = existing
        return existing


def call(app, conversation, runner, name, arguments):
    with LOCK:
        with app.state.sessions.begin() as db:
            row = db.scalar(select(Conversation).where(Conversation.id == uuid.UUID(str(conversation))).with_for_update())
            metadata = request_metadata(row, runner.last['requestId'])
            if not shell_allowed(metadata['scope']):
                return None
            tokens = getattr(runner, '_research_db_fixture_tokens', None)
            if tokens is None:
                runner._research_db_fixture_tokens = tokens = {}
            key = (str(conversation), runner.last['requestId'])
            if key not in tokens:
                login = db.scalar(select(LoginSession).where(LoginSession.token_hash == row.login_hash))
                token, grant = credentials.issue(row.id, row.request_id, metadata['scope'], login.expires_at, owner=row.owner_id)
                row.native_record = {**row.native_record, 'platformRequests': [
                    {**entry, 'researchGrant': grant} if entry['requestId'] == key[1] else entry
                    for entry in row.native_record['platformRequests']]}
                tokens[key] = token
            token = tokens[key]
        fixture = api_client(app)
    return fixture.post('/api/research/agent/v1/' + name, json=arguments,
                        headers={'Authorization': 'Bearer ' + token})
