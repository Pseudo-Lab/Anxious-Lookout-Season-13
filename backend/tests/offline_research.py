"""One test-image-only r2 storage scenario; no native CLI/service/TLS claims.

The cookie trigger admits a normal conversation and uses a held status double
with the production agent router/transactions. Mock turn grants never leave this
process. Production runtime has neither this module nor the trigger route.
"""
import threading
from uuid import UUID, uuid5

from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.research import fail, Input
from app.research_models import Conversation
from .offline_services import require_offline


class Held:
    def __init__(self):
        self.last = None
        self.state = 'running'
        self.record = {'turns': []}

    def health(self):
        return {'model': 'gpt-6.1-sol', 'available': True, 'reason': None, 'verification': 'fixture'}

    def submit(self, identity, body):
        self.session_id, self.last = identity, body

    def read(self, identity):
        return {'requestId': self.last['requestId'], 'state': self.state,
                'turnId': 'held-turn' if self.record['turns'] else None,
                'record': self.record}

    def finish(self):
        self.record = {'turns': [{'id': 'held-turn', 'items': [
            {'id': 'offline-r2-user', 'type': 'userMessage', 'content': [{'type': 'text', 'text': self.last['text']}]},
            {'id': 'offline-r2-answer', 'type': 'agentMessage', 'text': 'Offline r2 storage fixture completed.'}]}]}
        self.state = 'idle'


def checked(response, codes):
    if response.status_code not in codes:
        fail('not_ready', 'Offline r2 fixture did not confirm success', 503)
    return response.json()


def result(session, calls, document):
    saved = next((entry for entry in calls['items'] if entry.get('operation') == 'research_document_save'
                  and entry.get('status') == 'succeeded' and entry.get('saved')), None)
    if saved is None or document['latestVersion']['author'] != {'kind': 'codex', 'conversationId': session['id']}:
        fail('not_ready', 'Offline r2 fixture evidence mismatch', 503)
    return {'sessionId': session['id'], 'requestId': saved['requestId'],
            'resourceId': document['id'], 'versionId': saved['saved']['versionId'],
            'receiptId': saved['id'], 'callbackRejected': True}


def register(app):
    require_offline()
    if app.state.user_runners_enabled or app.state.research_agent_api_enabled:
        raise RuntimeError('Offline held fixture requires real research activation disabled')
    prefix = app.state.settings.base_path
    lock = threading.Lock()

    @app.post(prefix + '/api/research/__mock/offline/research-save')
    def trigger(body: dict, request: Request):
        require_offline()
        Input.model_validate(body)  # Strict empty body; no owner/content/grant input.
        keys = request.headers.getlist('idempotency-key')
        try:
            if len(keys) != 1 or str(UUID(keys[0])) != keys[0]:
                raise ValueError()
            key = UUID(keys[0])
        except (ValueError, TypeError):
            fail('validation_error', 'Canonical Idempotency-Key required', 422)
        # Release authentication checkout/locks before nested HTTP/agent work.
        with app.state.research_authorized(request, True) as store:
            owner = store.owner
            if store.db.scalar(select(Conversation.id).where(Conversation.owner_id == owner,
                                                            Conversation.state == 'running')) is not None:
                fail('conflict', 'Offline owner already has an active turn', 409)
        if not lock.acquire(blocking=False):
            fail('conflict', 'Offline fixture is busy', 409)
        old = app.state.research_runners.get(owner)
        held = Held()
        client = TestClient(app, base_url=app.state.settings.origin, raise_server_exceptions=False)
        auth = {'Cookie': request.headers.get('cookie', ''), 'Origin': request.headers.get('origin', ''),
                'X-CSRF-Token': request.headers.get('x-csrf-token', '')}
        def post(path, body, step):
            return client.post(prefix + path, json=body,
                               headers={**auth, 'Idempotency-Key': str(uuid5(key, step))})
        def get(path):
            return checked(client.get(prefix + path, headers=auth), {200})
        try:
            app.state.research_runners[owner] = held
            session = checked(post('/api/research/sessions', {'title': 'Offline r2 API fixture'}, 'session'), {201})
            path = '/api/research/sessions/' + session['id']
            calls = get(path + '/research-calls')
            if calls['items']:
                prior = next((entry.get('saved') for entry in calls['items'] if entry.get('saved')), None)
                if prior is None:
                    fail('not_ready', 'Offline fixture has incomplete prior evidence', 503)
                # Explicit trigger retry only returns prior evidence; no new admission/save.
                return JSONResponse(result(session, calls, get('/api/research/documents/' + prior['id'])),
                                    headers={'Cache-Control': 'no-store'})
            checked(post(path + '/messages', {'text': 'Offline r2 API storage probe', 'expectedVersion': 1}, 'message'), {202})
            if held.last is None:
                fail('not_ready', 'Offline admission was not held', 503)
            from .research_api_fixture import call
            arguments = {'title': 'Offline r2 saved document', 'content': 'Synthetic source retained by the r2 agent API.',
                         'mutationId': str(uuid5(key, 'save'))}
            document = checked(call(app, session['id'], held, 'research_document_save', arguments), {200})
            # Prove the retired callback refuses the very same admitted owner.
            refused = client.post(prefix + '/api/internal/research/tools', headers={
                'Authorization': 'Bearer ' + held.last['toolToken']}, json={
                'sessionId': session['id'], 'requestId': held.last['requestId'],
                'name': 'research_document_save', 'arguments': arguments})
            if refused.status_code != 403 or refused.json().get('error', {}).get('code') != 'tool_not_allowed':
                fail('not_ready', 'Retired owner callback was not refused', 503)
            held.finish()
            terminal = get(path)
            if terminal['state'] != 'idle':
                fail('not_ready', 'Offline fixture did not settle', 503)
            calls = get(path + '/research-calls')
            value = result(session, calls, document)
            return JSONResponse(value, headers={'Cache-Control': 'no-store'})
        finally:
            if old is None:
                app.state.research_runners.pop(owner, None)
            else:
                app.state.research_runners[owner] = old
            client.close()
            lock.release()
