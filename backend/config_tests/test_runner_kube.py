"""Actual HTTP adapter with MockTransport; no cluster, TLS handshake or provider."""
import base64
from copy import deepcopy
from dataclasses import replace
import json
from uuid import uuid4

import httpx
import pytest

from app.runner_controller import Identity
from app.runner_kube import KubePods, Plan, Reference, LABEL
from runner.lifecycle import Refused


@pytest.fixture
def plan():
    return Plan(Identity(uuid4(), uuid4(), 1), 'registry.invalid/runner@sha256:'+'a'*64,
                Reference('native', uuid4(), 'native-pv'), Reference('control', uuid4(), 'control-pv'),
                Reference('transport', uuid4()), 'https://api.invalid/tools', 'runner')


def response(status, value=None, headers=None):
    return httpx.Response(status, stream=httpx.ByteStream(json.dumps(value).encode()), headers=headers)


class Server:
    def __init__(self, plan):
        self.plan = plan
        self.requests = []
        self.pod = None
        self.mode = None
        labels = {LABEL+'owner': str(plan.identity.owner), LABEL+'state': str(plan.identity.state_id)}
        self.objects = {}
        for ref in (plan.native, plan.control):
            self.objects[ref.name] = {'apiVersion': 'v1', 'kind': 'PersistentVolumeClaim',
                'metadata': {'name': ref.name, 'namespace': 'isolated', 'uid': str(ref.uid), 'labels': labels.copy()},
                'spec': {'volumeName': ref.volume}, 'status': {'phase': 'Bound'}}
        self.objects[plan.transport.name] = {'apiVersion': 'v1', 'kind': 'Secret', 'type': 'Opaque', 'immutable': True,
            'metadata': {'name': plan.transport.name, 'namespace': 'isolated', 'uid': str(plan.transport.uid), 'labels': labels.copy()},
            'data': {'token': base64.b64encode(b't'*40).decode()}}

    def __call__(self, request):
        self.requests.append((request.method, request.url.path))
        assert request.url.host == 'kube.invalid'
        assert request.url.path.startswith('/api/v1/namespaces/isolated/')
        assert request.headers['authorization'] == 'Bearer '+'k'*40
        assert all(0 < value <= 2 for value in request.extensions['timeout'].values())
        path = request.url.path
        if request.method == 'POST':
            assert path.endswith('/pods')
            if self.mode == 'conflict': return response(409, {'message': 'do not log token'})
            assert self.pod is None
            self.pod = json.loads(request.content)
            self.pod['metadata']['uid'] = str(uuid4())
            if self.mode == 'lost': raise httpx.ReadTimeout('synthetic private response')
            return response(201, self.pod)
        if '/pods/' in path:
            if self.mode == 'redirect': return response(307, {}, {'location': 'https://foreign.invalid/private'})
            if self.mode == 'forbidden': return response(403, {'data': 'private response'})
            if self.mode == 'timeout': raise httpx.ReadTimeout('synthetic private response')
            return response(200, self.pod) if self.pod else response(404)
        return response(200, self.objects[path.rsplit('/', 1)[1]])


@pytest.fixture
def server(plan):
    return Server(plan)


@pytest.fixture
def port(plan, server):
    result = KubePods('https://kube.invalid', 'isolated', 'k'*40, None, [plan], _transport=httpx.MockTransport(server))
    yield result
    result.close()


def test_exact_create_and_lookup_no_credentials_in_pod_spec(port, server, plan):
    assert port.lookup(plan.identity) is None
    pod = port.create_once(plan.identity)
    assert pod.identity == plan.identity and port.lookup(plan.identity) == pod
    assert [method for method, _ in server.requests].count('POST') == 1
    encoded = json.dumps(server.pod)
    assert 'k'*40 not in encoded and 't'*40 not in encoded
    assert server.pod['spec']['automountServiceAccountToken'] is False
    assert len(server.pod['spec']['containers']) == 1


@pytest.mark.parametrize('mode', ['redirect', 'forbidden', 'timeout'])
def test_lookup_errors_never_follow_redirect_retry_or_post(port, server, plan, mode):
    server.mode = mode
    with pytest.raises(Refused) as caught: port.lookup(plan.identity)
    assert len(server.requests) == 1 and server.requests[0][0] == 'GET'
    assert 'private' not in str(caught.value)


@pytest.mark.parametrize('mode', ['lost', 'conflict'])
def test_post_failure_is_one_attempt_without_automatic_lookup_or_retry(port, server, plan, mode):
    server.mode = mode
    with pytest.raises(Refused): port.create_once(plan.identity)
    assert [method for method, _ in server.requests].count('POST') == 1
    assert server.requests[-1][0] == 'POST'
    if mode == 'lost':
        assert port.lookup(plan.identity).uid
        assert [method for method, _ in server.requests].count('POST') == 1


@pytest.mark.parametrize('damage', ['owner', 'generation', 'namespace', 'uid', 'deleting', 'owner-reference',
                                    'sidecar', 'ephemeral', 'volume', 'mount', 'env', 'image', 'privileged',
                                    'serviceaccount', 'host', 'command', 'tolerations', 'network-label', 'network-annotation'])
def test_foreign_or_modified_pod_refused(port, server, plan, damage):
    port.create_once(plan.identity)
    obj = server.pod
    if damage == 'owner': obj['metadata']['labels'][LABEL+'owner'] = str(uuid4())
    elif damage == 'generation': obj['metadata']['labels'][LABEL+'generation'] = '2'
    elif damage == 'namespace': obj['metadata']['namespace'] = 'foreign'
    elif damage == 'uid': obj['metadata']['uid'] = 'invalid'
    elif damage == 'deleting': obj['metadata']['deletionTimestamp'] = '2026-10-14T00:00:00Z'
    elif damage == 'owner-reference': obj['metadata']['ownerReferences'] = [{'uid': str(uuid4())}]
    elif damage == 'sidecar': obj['spec']['containers'].append(deepcopy(obj['spec']['containers'][0]))
    elif damage == 'ephemeral': obj['spec']['ephemeralContainers'] = [{'name': 'debug'}]
    elif damage == 'volume': obj['spec']['volumes'][0] = {'name': 'native', 'hostPath': {'path': '/'}}
    elif damage == 'mount': obj['spec']['containers'][0]['volumeMounts'][0]['subPath'] = 'other'
    elif damage == 'env': obj['spec']['containers'][0]['envFrom'] = [{'secretRef': {'name': 'foreign'}}]
    elif damage == 'image': obj['spec']['containers'][0]['image'] = 'foreign:latest'
    elif damage == 'privileged': obj['spec']['containers'][0]['securityContext']['privileged'] = True
    elif damage == 'serviceaccount': obj['spec']['serviceAccountName'] = 'admin'
    elif damage == 'host': obj['spec']['hostNetwork'] = True
    elif damage == 'command': obj['spec']['containers'][0]['command'] = ['sh']
    elif damage == 'tolerations': obj['spec']['tolerations'] = [{'operator': 'Exists'}]
    elif damage == 'network-label': obj['metadata']['labels']['app'] = 'trusted-api'
    elif damage == 'network-annotation': obj['metadata']['annotations'] = {'k8s.v1.cni.cncf.io/networks': 'foreign'}
    with pytest.raises(Refused) as caught: port.lookup(plan.identity)
    assert str(caught.value) == 'Kubernetes lookup requires reconciliation'
    assert [method for method, _ in server.requests].count('POST') == 1


@pytest.mark.parametrize('damage', ['pvc-owner', 'pvc-uid', 'pv', 'unbound', 'secret-uid', 'mutable-secret', 'token-keys', 'token'])
def test_reference_drift_prevents_post(port, server, plan, damage):
    native, secret = server.objects['native'], server.objects['transport']
    if damage == 'pvc-owner': native['metadata']['labels'][LABEL+'owner'] = str(uuid4())
    elif damage == 'pvc-uid': native['metadata']['uid'] = str(uuid4())
    elif damage == 'pv': native['spec']['volumeName'] = 'foreign-pv'
    elif damage == 'unbound': native['status']['phase'] = 'Pending'
    elif damage == 'secret-uid': secret['metadata']['uid'] = str(uuid4())
    elif damage == 'mutable-secret': secret['immutable'] = False
    elif damage == 'token-keys': secret['data']['foreign'] = 'extra'
    elif damage == 'token': secret['data']['token'] = base64.b64encode(b'short').decode()
    with pytest.raises(Refused): port.create_once(plan.identity)
    assert all(method == 'GET' for method, _ in server.requests)


def test_narrow_server_defaults_are_accepted(port, server, plan):
    original = port.create_once(plan.identity)
    spec = server.pod['spec']
    spec.update(nodeName='node-one', schedulerName='default-scheduler', serviceAccount='runner', priority=0,
                preemptionPolicy='PreemptLowerPriority', tolerations=[{'key': 'node.kubernetes.io/not-ready',
                'operator': 'Exists', 'effect': 'NoExecute', 'tolerationSeconds': 300}])
    spec['containers'][0].update(terminationMessagePath='/dev/termination-log', terminationMessagePolicy='File', resources={})
    assert port.lookup(plan.identity) == original


def test_cross_owner_storage_or_token_reuse_refused(plan, server):
    foreign = replace(plan, identity=Identity(uuid4(), uuid4(), 1))
    with pytest.raises(Refused):
        KubePods('https://kube.invalid', 'isolated', 'k'*40, None, [plan, foreign], _transport=httpx.MockTransport(server))


@pytest.mark.parametrize('endpoint,namespace', [('http://kube.invalid', 'isolated'), ('https://kube.invalid/path', 'isolated'),
                                               ('https://user:pass@kube.invalid', 'isolated'), ('https://kube.invalid', '../foreign')])
def test_invalid_destination_refused_before_io(plan, server, endpoint, namespace):
    with pytest.raises(Refused): KubePods(endpoint, namespace, 'k'*40, None, [plan], _transport=httpx.MockTransport(server))
    assert server.requests == []


def test_oversized_body_refused_without_logging(plan):
    def serve(request):
        return httpx.Response(200, stream=httpx.ByteStream(b'x'*(2*1024*1024+1)))
    port = KubePods('https://kube.invalid', 'isolated', 'k'*40, None, [plan], _transport=httpx.MockTransport(serve))
    try:
        with pytest.raises(Refused): port.lookup(plan.identity)
    finally:
        port.close()


def test_elapsed_response_budget_refuses_slow_stream(plan):
    import time
    class Slow(httpx.SyncByteStream):
        def __iter__(self):
            time.sleep(0.02)
            yield b'{}'
    port = KubePods('https://kube.invalid', 'isolated', 'k'*40, None, [plan], deadline=0.001,
                    _transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Slow())))
    try:
        with pytest.raises(Refused): port.lookup(plan.identity)
    finally:
        port.close()


def test_new_generation_cannot_switch_retained_storage(plan, server):
    changed = replace(plan, identity=replace(plan.identity, generation=2), native=Reference('new-native', uuid4(), 'new-pv'))
    with pytest.raises(Refused):
        KubePods('https://kube.invalid', 'isolated', 'k'*40, None, [plan, changed], _transport=httpx.MockTransport(server))
    assert server.requests == []
