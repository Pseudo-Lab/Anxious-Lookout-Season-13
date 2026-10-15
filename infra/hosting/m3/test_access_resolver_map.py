import copy
import json
import socket
import subprocess
import sys

import pytest

import access_resolver_map as resolver
from access_diagnose_prepare import emit, DIAGNOSTIC_IMAGE
from test_access_web_prepare import package


def dns_metadata(node='selected-node'):
    service = {'kind': 'Service', 'metadata': {'namespace': 'kube-system', 'name': 'kube-dns', 'uid': 'service-uid', 'resourceVersion': '1'},
               'spec': {'type': 'ClusterIP', 'selector': {'k8s-app': 'kube-dns'}, 'clusterIP': '10.43.0.10',
                        'clusterIPs': ['10.43.0.10'], 'ports': [{'port': 53, 'protocol': p} for p in ('UDP', 'TCP')]}}
    pod = {'kind': 'Pod', 'metadata': {'namespace': 'kube-system', 'name': 'known-dns', 'uid': 'dns-uid',
           'resourceVersion': '2', 'labels': {'k8s-app': 'kube-dns'}}, 'spec': {'nodeName': node},
           'status': {'phase': 'Running', 'podIP': '10.42.0.10', 'conditions': [{'type': 'Ready', 'status': 'True'}]}}
    return {'service': service, 'pod': pod}


@pytest.mark.parametrize('primary,service_match,pod_match,scope', [
    ('10.43.0.10', True, False, 'other'), ('10.42.0.10', False, True, 'other'),
    ('169.254.20.10', False, False, 'link_local'), ('127.0.0.53', False, False, 'loopback')])
def test_runtime_resolver_comparison_is_address_free(primary, service_match, pod_match, scope, monkeypatch):
    def forbidden(*_, **__): raise AssertionError('No socket may be opened')
    monkeypatch.setattr(socket, 'socket', forbidden)
    data = dns_metadata()
    expected = resolver.expected_context(data['service'], data['pod'], 'selected-node')
    result = resolver.summary('nameserver '+primary+'\nsearch PRIVATE_SEARCH_SENTINEL\n', expected)
    assert result['primaryMatchesDnsService'] is service_match
    assert result['primaryMatchesKnownCoreDnsPod'] is pod_match and result['primaryScope'] == scope
    assert not result['networkRequested'] and result['dnsCause'] == 'unknown'
    for value in (primary, '10.43.0.10', '10.42.0.10', 'PRIVATE_SEARCH_SENTINEL'):
        assert value not in json.dumps(result)


@pytest.mark.parametrize('text', ['', 'nameserver invalid\n', 'nameserver 10.43.0.10 extra\n',
                                  'nameserver 10.43.0.10\n'*9, 'x'*65537])
def test_missing_malformed_or_unbounded_resolver_is_refused(text):
    with pytest.raises(ValueError): resolver.summary(text, {'serviceIps': ['10.43.0.10'], 'podIps': ['10.42.0.10']})


@pytest.mark.parametrize('change', ['namespace', 'service_name', 'selector', 'port', 'ready', 'node', 'host_network', 'headless'])
def test_unknown_or_foreign_dns_metadata_is_refused(change):
    data = dns_metadata(); service, pod = data['service'], data['pod']
    if change == 'namespace': service['metadata']['namespace'] = 'foreign'
    if change == 'service_name': service['metadata']['name'] = 'other'
    if change == 'selector': service['spec']['selector']['extra'] = 'foreign'
    if change == 'port': service['spec']['ports'][0]['port'] = 5353
    if change == 'ready': pod['status']['conditions'][0]['status'] = 'False'
    if change == 'node': pod['spec']['nodeName'] = 'foreign'
    if change == 'host_network': pod['spec']['hostNetwork'] = True
    if change == 'headless': service['spec']['clusterIP'] = 'None'; service['spec']['clusterIPs'] = ['None']
    with pytest.raises(ValueError): resolver.expected_context(service, pod, 'selected-node')


def test_new_job_reads_runtime_file_without_network_or_state(package):
    import access_web_prepare as web
    snapshot, inputs, _ = package
    snapshot['runner'] = {'metadata': {'namespace': 'codex-trial', 'name': 'access-web-runner-v1'}, 'spec': {'replicas': 0}}
    snapshot['runnerPodCount'] = 0
    data = dns_metadata(web.base.storage(snapshot))
    job = emit(snapshot, inputs, DIAGNOSTIC_IMAGE, 'resolver-map', dns_inputs=data)
    pod = job['spec']['template']['spec']; container = pod['containers'][0]
    assert container['env'] == container['volumeMounts'] == pod['volumes'] == []
    assert not pod['automountServiceAccountToken'] and job['spec']['activeDeadlineSeconds'] == 30
    assert 'dnsConfig' not in pod and 'dnsPolicy' not in pod
    process = subprocess.run([sys.executable, '-B', '-c', container['command'][3]], capture_output=True, timeout=3)
    assert process.returncode == 0
    result = json.loads(process.stdout)
    assert result['networkRequested'] is False and result['dnsCause'] == 'unknown'
    assert b'10.43.0.10' not in process.stdout and process.stderr == b''
    snapshot['runner']['spec']['replicas'] = 1
    with pytest.raises(ValueError): emit(snapshot, inputs, DIAGNOSTIC_IMAGE, 'resolver-map', dns_inputs=data)


def test_dual_stack_and_secondary_mismatch_are_not_hidden():
    expected = {'serviceIps': ['10.43.0.10', 'fd00::10'], 'podIps': ['10.42.0.10']}
    result = resolver.summary('nameserver fd00::10\nnameserver 169.254.20.10\n', expected)
    assert result['primaryMatchesDnsService'] and result['primaryFamilyMatchesDnsService']
    assert result['resolverCount'] == 2 and result['allResolversMatchDnsService'] is False
