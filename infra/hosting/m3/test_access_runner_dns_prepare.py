import copy
import json

import pytest

from access_runner_dns_prepare import SOURCE_SHA, RUNNER_IMAGE, emit
from access_diagnose_prepare import DIAGNOSTIC_IMAGE
from test_access_web_prepare import package


@pytest.fixture
def runner_snapshot(package):
    import access_web_prepare as web
    snapshot, inputs, _ = package
    snapshot['runner'] = {'metadata': {'namespace': 'codex-trial', 'name': 'access-web-runner-v1'},
        'spec': {'replicas': 0, 'template': {'metadata': {'labels': {'app': 'access-web-runner'}},
             'spec': {'containers': [{'name': 'runner', 'image': RUNNER_IMAGE}],
             'nodeSelector': {'kubernetes.io/hostname': web.base.storage(snapshot)},
             'dnsPolicy': 'ClusterFirst', 'dnsConfig': {'options': [{'name': 'ndots', 'value': '5'}]},
             'serviceAccountName': 'trial'}}}}
    snapshot['runnerPodCount'] = 0
    images = {'sourceSha': SOURCE_SHA, 'api': DIAGNOSTIC_IMAGE, 'runner': RUNNER_IMAGE}
    return snapshot, inputs, images


def test_actual_runner_reference_keeps_current_dns_node_selector_and_no_auth_mounts(runner_snapshot):
    snapshot, inputs, images = runner_snapshot
    job = emit(snapshot, inputs, images)
    ps = job['spec']['template']['spec']; c = ps['containers'][0]
    assert c['image'] == RUNNER_IMAGE and c['command'][:3] == ['python', '-B', '-c']
    assert c['imagePullPolicy'] == 'Never'
    assert ps['dnsPolicy'] == 'ClusterFirst' and ps['dnsConfig'] == snapshot['runner']['spec']['template']['spec']['dnsConfig']
    assert ps['nodeSelector'] == snapshot['runner']['spec']['template']['spec']['nodeSelector']
    assert ps['securityContext']['runAsUser'] == ps['securityContext']['runAsGroup'] == 10001
    assert ps['volumes'] == c['volumeMounts'] == c['env'] == []
    assert not ps['automountServiceAccountToken'] and c['securityContext']['readOnlyRootFilesystem']
    assert job['spec']['activeDeadlineSeconds'] == 30 and job['spec']['backoffLimit'] == 0
    assert job['spec']['template']['metadata']['labels'] == {'app': 'access-web-runner'}


@pytest.mark.parametrize('change', ['wrong_image', 'wrong_source', 'running', 'pod_exists', 'node', 'host_network', 'service_account'])
def test_no_other_image_node_or_running_runner_can_be_substituted(runner_snapshot, change):
    values = copy.deepcopy(runner_snapshot); snapshot, inputs, images = values
    if change == 'wrong_image': images['runner'] = DIAGNOSTIC_IMAGE
    elif change == 'wrong_source': images['sourceSha'] = 'a'*40
    elif change == 'running': snapshot['runner']['spec']['replicas'] = 1
    elif change == 'pod_exists': snapshot['runnerPodCount'] = 1
    elif change == 'node': snapshot['runner']['spec']['template']['spec']['nodeSelector'] = {'kubernetes.io/hostname': 'other'}
    elif change == 'host_network': snapshot['runner']['spec']['template']['spec']['hostNetwork'] = True
    elif change == 'service_account': snapshot['runner']['spec']['template']['spec']['serviceAccountName'] = 'other'
    with pytest.raises(ValueError): emit(*values)


def test_exact_four_attempts_no_libc_and_no_address_output(runner_snapshot):
    job = emit(*runner_snapshot)
    namespace = {'__name__': 'fixture'}
    exec(job['spec']['template']['spec']['containers'][0]['command'][3], namespace)
    calls = []
    def probe(name, server, tcp=False):
        calls.append((name, server, tcp))
        return {'status': 'timeout', 'rcode': None, 'answersPresent': False}
    result = namespace['runner_image_dns'](lambda: 'nameserver 10.43.0.10\nsearch svc.cluster.local\noptions ndots:5\n', probe)
    assert calls == [(name, '10.43.0.10', tcp) for name in ('chatgpt.com', 'api.m2-hosting.svc.cluster.local') for tcp in (False, True)]
    assert result['helperAttempts'] == 4 and result['libcQueries'] == 0 and result['entrypointOverridden']
    assert not result['sourceCredentialRead'] and not result['nativeStarted'] and not result['httpOrAuthRpc']
    assert '10.43.0.10' not in json.dumps(result) and 'svc.cluster.local' not in json.dumps(result)


def test_program_deadline_escapes_shared_query_exception_handler(runner_snapshot):
    namespace = {'__name__': 'fixture'}
    exec(emit(*runner_snapshot)['spec']['template']['spec']['containers'][0]['command'][3], namespace)
    def stalled(*_, **__):
        try: raise namespace['RunnerDnsDeadline']()
        except Exception: return {}
    with pytest.raises(namespace['RunnerDnsDeadline']):
        namespace['runner_image_dns'](lambda: 'nameserver 10.43.0.10\n', stalled)
