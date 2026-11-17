"""D7 rendering and HTTP/TLS doubles only: no sockets, cluster or model calls."""
from copy import deepcopy
from pathlib import Path
import ssl
from types import SimpleNamespace

import pytest

from ops import research_traefik as policy
from ops import research_https_preflight
from runner import research_cli


def snapshots():
    values = {'namespace': 'kube-system', 'service': 'traefik',
              'selector': {'app.kubernetes.io/name': 'traefik'}, 'targetPort': 8443}
    service = {'kind': 'Service', 'metadata': {'namespace': 'kube-system', 'name': 'traefik',
        'uid': 'service-uid', 'resourceVersion': '1'}, 'spec': {'type': 'LoadBalancer', 'clusterIP': '10.43.0.10',
        'selector': values['selector'].copy(), 'ports': [{'name': 'websecure', 'protocol': 'TCP', 'port': 443, 'targetPort': 'websecure'}]}}
    pods = {'items': [{'metadata': {'namespace': 'kube-system', 'name': 'traefik-one', 'uid': 'pod-uid',
        'resourceVersion': '2', 'labels': values['selector'].copy()},
        'spec': {'containers': [{'name': 'traefik', 'args': ['--entryPoints.websecure.address=:8443/tcp'],
            'ports': [{'name': 'websecure', 'containerPort': 8443, 'protocol': 'TCP'}]}]},
        'status': {'phase': 'Running', 'podIP': '10.42.0.20'}}]}
    slices = {'items': [{'metadata': {'namespace': 'kube-system', 'name': 'traefik-slice',
        'uid': 'slice-uid', 'resourceVersion': '3', 'labels': {'kubernetes.io/service-name': 'traefik'}},
        'ports': [{'name': 'websecure', 'port': 8443, 'protocol': 'TCP'}],
        'endpoints': [{'addresses': ['10.42.0.20'], 'conditions': {'ready': True},
            'targetRef': {'kind': 'Pod', 'namespace': 'kube-system', 'name': 'traefik-one', 'uid': 'pod-uid'}}]}]}
    return values, service, pods, slices


def test_one_egress_rule_resolves_named_service_port_to_the_verified_backend_only():
    inputs = snapshots(); before = deepcopy(inputs)
    result, proof = policy.render(*inputs)
    assert result['spec'] == {'endpointSelector': {'matchLabels': {'anxious.io/managed': 'user-runner'}},
        'egress': [{'toEndpoints': [{'matchLabels': {'k8s:io.kubernetes.pod.namespace': 'kube-system',
            'k8s:app.kubernetes.io/name': 'traefik'}}],
            'toPorts': [{'ports': [{'port': '8443', 'protocol': 'TCP'}]}]}]}
    assert proof['servicePort'] == 443 and proof['targetPort'] == 8443 and proof['podUids'] == ['pod-uid']
    assert proof['transportHost'] == 'traefik.kube-system.svc.cluster.local'
    assert inputs == before


@pytest.mark.parametrize('override', [':9443/tcp', ':8443/udp', '127.0.0.1:8443/tcp', ':8443/UDP'])
@pytest.mark.parametrize('split', [False, True])
def test_conflicting_listener_declarations_cannot_certify_an_earlier_match(override, split):
    inputs = snapshots()
    args = inputs[2]['items'][0]['spec']['containers'][0]['args']
    args.extend(['--entryPoints.websecure.address', override] if split else ['--entryPoints.websecure.address=' + override])
    with pytest.raises(ValueError): policy.render(*inputs)


def test_equivalent_tcp_listener_duplicates_are_normalized():
    inputs = snapshots()
    inputs[2]['items'][0]['spec']['containers'][0]['args'].extend(['--entryPoints.websecure.address', ':8443'])
    assert policy.render(*inputs)[1]['targetPort'] == 8443


@pytest.mark.parametrize('damage', ['frontend-port', 'wrong-target', 'bool-target', 'empty-selector',
    'reserved-label', 'service-namespace', 'external-name', 'host-network', 'pod-label', 'pod-terminating',
    'pod-pending', 'container-port', 'entrypoint', 'slice-port', 'endpoint-uid', 'endpoint-address',
    'endpoint-not-ready', 'endpoint-terminating', 'no-slice', 'duplicate-pod'])
def test_forged_stale_or_broader_endpoint_snapshot_is_refused(damage):
    values, service, pods, slices = snapshots()
    pod, slice_ = pods['items'][0], slices['items'][0]
    if damage == 'frontend-port': service['spec']['ports'][0]['port'] = 8080
    if damage == 'wrong-target': values['targetPort'] = 443
    if damage == 'bool-target': values['targetPort'] = True
    if damage == 'empty-selector': values['selector'] = {}
    if damage == 'reserved-label': values['selector']['io.kubernetes.pod.namespace'] = 'm2-hosting'
    if damage == 'service-namespace': service['metadata']['namespace'] = 'm2-hosting'
    if damage == 'external-name': service['spec']['type'] = 'ExternalName'
    if damage == 'host-network': pod['spec']['hostNetwork'] = True
    if damage == 'pod-label': pod['metadata']['labels'] = {'app.kubernetes.io/name': 'other'}
    if damage == 'pod-terminating': pod['metadata']['deletionTimestamp'] = 'now'
    if damage == 'pod-pending': pod['status']['phase'] = 'Pending'
    if damage == 'container-port': pod['spec']['containers'][0]['ports'][0]['containerPort'] = 443
    if damage == 'entrypoint': pod['spec']['containers'][0]['args'] = ['--entryPoints.admin.address=:8443/tcp']
    if damage == 'slice-port': slice_['ports'][0]['port'] = 443
    if damage == 'endpoint-uid': slice_['endpoints'][0]['targetRef']['uid'] = 'replacement'
    if damage == 'endpoint-address': slice_['endpoints'][0]['addresses'] = ['10.0.0.107']
    if damage == 'endpoint-not-ready': slice_['endpoints'][0]['conditions']['ready'] = False
    if damage == 'endpoint-terminating': slice_['endpoints'][0]['conditions']['terminating'] = True
    if damage == 'no-slice': slices['items'] = []
    if damage == 'duplicate-pod': pods['items'].append(deepcopy(pod))
    with pytest.raises(ValueError): policy.render(values, service, pods, slices)


@pytest.mark.parametrize('namespace,service', [('ingress', 'edge'), ('kube-system', 'traefik')])
def test_image_build_pin_is_compiled_from_validated_service_identity(namespace, service):
    source = Path(research_cli.__file__).read_text()
    result = policy.compile_cli(source, namespace, service)
    expected = policy.transport_host(namespace, service)
    assert ('CONNECT_HOST = ' + repr(expected)) in result
    assert result.replace(repr(expected), repr(policy.DEFAULT_HOST), 1) == source


@pytest.mark.parametrize('namespace,service', [('kube-system', 'api:8080'), ('../host', 'traefik'),
    ('kube-system\n', 'traefik'), ('', 'traefik'), ('kube-system', 'x' * 64)])
def test_build_inputs_cannot_inject_an_address_port_or_code(namespace, service):
    with pytest.raises(ValueError): policy.compile_cli(Path(research_cli.__file__).read_text(), namespace, service)


@pytest.mark.parametrize('module', [research_cli, research_https_preflight])
@pytest.mark.parametrize('origin', ['research.example.invalid', '192.0.2.10', '2001:db8::10'])
def test_tcp_pin_preserves_public_tls_identity_and_http_host_for_dns_and_ip_origins(module, origin, monkeypatch):
    events, sent = [], []
    class Socket:
        def setsockopt(self, *args): pass
        def sendall(self, value): sent.append(bytes(value))
        def close(self): pass
    sock = Socket()
    def connect(address, **kwargs):
        events.append(('tcp', address, kwargs)); return sock
    def wrap(value, *, server_hostname):
        assert value is sock
        events.append(('tls', server_hostname)); return sock
    monkeypatch.setattr(module.socket, 'create_connection', connect)
    monkeypatch.setenv('HTTPS_PROXY', 'http://must-not-use')
    context = SimpleNamespace(check_hostname=True, verify_mode=ssl.CERT_REQUIRED, wrap_socket=wrap)
    connection = module.pinned_connection(origin, timeout=5, context=context)
    connection.request('POST', '/api/research/agent/v1/research_list', body=b'{}')
    connection.close()
    assert events == [('tcp', ('traefik.kube-system.svc.cluster.local',443), {'timeout':5}), ('tls', origin)]
    authority = '[' + origin + ']' if ':' in origin else origin
    assert ('Host: ' + authority + '\r\n').encode() in b''.join(sent)


@pytest.mark.parametrize('module', [research_cli, research_https_preflight])
def test_service_connection_failure_has_no_public_or_internal_fallback(module, monkeypatch):
    seen = []
    def refuse(address, **kwargs):
        seen.append(address); raise OSError('Synthetic failure')
    monkeypatch.setattr(module.socket, 'create_connection', refuse)
    context = SimpleNamespace(check_hostname=True, verify_mode=ssl.CERT_REQUIRED)
    connection = module.pinned_connection('192.0.2.10', timeout=5, context=context)
    with pytest.raises(OSError): connection.connect()
    connection.close()
    assert seen == [('traefik.kube-system.svc.cluster.local',443)]
