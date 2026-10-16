"""Real HTTP/auth/PostgreSQL with fake Kubernetes/runner HTTP; no live provider."""
from dataclasses import replace
import json
from uuid import uuid4

import httpx
import pytest

from app.runner_kube import KubePods
from app.runner_controller import Identity
from app.user_runner_runtime import Runtime
from app.conversations import Runner
from runner.lifecycle import Phase
from config_tests.test_user_runner_http import research_schema, client, identity, headers, create_conversation
from config_tests.test_runner_registry import schema, admin, read
from config_tests.test_runner_kube import plan, Server, response


@pytest.fixture
def runtime(client, admin, plan, monkeypatch):
    owner, auth = identity(client, admin)
    assert client.post('/api/research/codex/runner', json={}, headers=headers(auth)).status_code == 202
    state = read(admin, owner)
    plan = replace(plan, identity=Identity.of(state))
    namespace_uid = uuid4()
    server = Server(plan)
    deletes = []
    def kube(request):
        if request.url.path == '/api/v1/namespaces/isolated':
            return response(200, {'kind': 'Namespace', 'metadata': {'uid': str(namespace_uid)}})
        if request.method == 'DELETE':
            assert json.loads(request.content)['preconditions']['uid'] == server.pod['metadata']['uid']
            deletes.append(server.pod['metadata']['uid'])
            server.pod = None
            return response(200, {'kind': 'Status'})
        result = server(request)
        if request.method == 'POST':
            server.pod['status'] = {'phase': 'Running', 'podIP': '10.42.0.10'}
        return result
    pods = KubePods('https://kube.invalid', 'isolated', 'k'*40, None, [plan], _transport=httpx.MockTransport(kube))
    runtime = Runtime(client.app.state.engine, pods, plan, 't'*40, namespace_uid, '10.42.0.0/16', 1)
    client.app.state.user_runner_runtime = runtime
    calls = []
    def transport(self, method, path, body=None):
        calls.append((method, path))
        assert self.owner == owner and self.state_id == str(state.state_id)
        assert self.url == 'http://10.42.0.10:8080' and self.token == 't'*40
        binding = {'ownerId': str(owner), 'stateId': str(state.state_id), 'podUid': server.pod['metadata']['uid']}
        if path == '/health':
            return {**binding, 'available': True, 'reason': None, 'verification': 'unverified', 'model': 'gpt-6.1-sol'}
        if method == 'POST':
            transport.request = body['requestId']
            return {**binding, 'state': 'running', 'requestId': body['requestId']}
        return {**binding, 'state': 'idle', 'requestId': transport.request, 'threadId': 'synthetic-thread',
                'record': {'turns': []}}
    monkeypatch.setattr(Runner, 'call', transport)
    return owner, auth, runtime, server, deletes, calls


def test_web_prepares_one_pod_dispatches_reads_and_logout_deletes_exact_uid(client, admin, runtime):
    owner, auth, rt, server, deletes, calls = runtime
    for _ in range(2):
        result = client.post('/api/research/codex/runner', json={}, headers=headers(auth))
        assert result.status_code in {200, 202}, result.text
        assert result.json()['available'] is True
    assert sum(method == 'POST' for method, _ in server.requests) == 1
    assert client.get('/api/research/codex/status').json()['available'] is True
    conversation = create_conversation(client, auth)
    path = '/api/research/sessions/'+conversation['id']
    reply = client.post(path+'/messages', json={'text': 'one turn', 'expectedVersion': 1}, headers=headers(auth))
    assert reply.status_code == 202, reply.text
    assert sum(method == 'POST' for method, _ in calls) == 1
    assert client.get(path).json()['state'] == 'idle'
    uid = server.pod['metadata']['uid']
    assert client.post('/api/auth/logout', headers=auth).status_code == 204
    assert deletes == [uid] and server.pod is None
    assert read(admin, owner).phase == Phase.FAILED
    assert str(read(admin, owner).pod_uid) == uid  # No unsupported release/termination proof.
    assert client.get('/api/auth/me').status_code == 401


@pytest.mark.parametrize('damage', ['uid', 'ip', 'labels', 'terminated'])
def test_runtime_refuses_stale_or_foreign_pod_before_native_dispatch(client, runtime, damage):
    owner, auth, rt, server, deletes, calls = runtime
    rt.ensure(owner)
    if damage == 'uid': server.pod['metadata']['uid'] = str(uuid4())
    elif damage == 'ip': server.pod['status']['podIP'] = '127.0.0.1'
    elif damage == 'labels': server.pod['metadata']['labels']['foreign'] = 'true'
    else: server.pod['metadata']['deletionTimestamp'] = '2026-10-15T00:00:00Z'
    assert client.get('/api/research/codex/status').json()['available'] is False
    conversation = create_conversation(client, auth)
    reply = client.post('/api/research/sessions/'+conversation['id']+'/messages',
        json={'text': 'must not dispatch', 'expectedVersion': 1}, headers=headers(auth))
    assert reply.status_code == 503, reply.text
    assert calls == [] and deletes == []


def test_stop_requires_csrf_and_fences_future_dispatch_without_deleting_state(client, admin, runtime):
    owner, auth, rt, server, deletes, calls = runtime
    rt.ensure(owner)
    assert client.delete('/api/research/codex/runner', headers={**auth, 'X-CSRF-Token': 'wrong'}).status_code == 403
    assert not deletes
    reply = client.delete('/api/research/codex/runner', headers=auth)
    assert reply.status_code == 200 and reply.json()['terminationRequested'] is True
    assert read(admin, owner).state_id == rt.plan.identity.state_id
    assert client.get('/api/research/codex/status').json()['available'] is False
    assert client.post('/api/research/codex/runner', json={}, headers=headers(auth)).status_code == 409
    assert sum(method == 'POST' for method, _ in server.requests) == 1


def test_delete_failure_retains_session_and_dispatch_fence_for_retry(client, admin, runtime, monkeypatch):
    owner, auth, rt, server, deletes, calls = runtime
    rt.ensure(owner)
    original = rt.pods._request
    def refused(method, *args, **kwargs):
        if method == 'DELETE':
            raise ValueError('synthetic uncertain delete')
        return original(method, *args, **kwargs)
    monkeypatch.setattr(rt.pods, '_request', refused)
    assert client.post('/api/auth/logout', headers=auth).status_code == 503
    assert client.get('/api/auth/me').status_code == 200
    assert read(admin, owner).phase == Phase.FAILED
    assert client.get('/api/research/codex/status').json()['available'] is False
    monkeypatch.setattr(rt.pods, '_request', original)
    assert client.post('/api/auth/logout', headers=auth).status_code == 204
    assert len(deletes) == 1


def test_wrong_native_uid_cannot_mark_status_available(client, runtime, monkeypatch):
    owner, auth, rt, server, deletes, calls = runtime
    rt.ensure(owner)
    original = Runner.call
    def wrong(self, *args, **kwargs):
        return {**original(self, *args, **kwargs), 'podUid': str(uuid4())}
    monkeypatch.setattr(Runner, 'call', wrong)
    assert client.get('/api/research/codex/status').json()['available'] is False
