import copy

import pytest

from access_endpoint_pin import pin, verify


def metadata():
    pod = {'kind': 'Pod', 'metadata': {'namespace': 'codex-trial', 'name': 'access-dns-observed-diagnose-v1-synthetic',
           'uid': 'selected-pod', 'labels': {'app': 'access-web-runner'}}, 'spec': {'nodeName': 'selected-node'}, 'status': {'phase': 'Running', 'podIP': '127.0.0.2'}}
    cep = {'kind': 'CiliumEndpoint', 'metadata': {'namespace': 'codex-trial', 'name': pod['metadata']['name'], 'uid': 'selected-cep',
           'ownerReferences': [{'kind': 'Pod', 'uid': 'selected-pod', 'name': pod['metadata']['name']}]},
           'status': {'id': 321, 'state': 'ready', 'external-identifiers': {'cni-attachment-id': 'synthetic-cni:eth0'},
                      'networking': {'addressing': [{'ipv4': '127.0.0.2'}]}}}
    agent = {'kind': 'Pod', 'metadata': {'namespace': 'kube-system', 'name': 'selected-agent', 'uid': 'selected-agent-uid',
             'labels': {'k8s-app': 'cilium'}}, 'spec': {'nodeName': 'selected-node'}, 'status': {'phase': 'Running'}}
    return pod, cep, agent


def test_local_endpoint_id_and_post_query_identity_match():
    pod, cep, agent = metadata()
    value = pin(pod, cep, agent)
    response = [{'id': 321, 'status': {'state': 'ready', 'external-identifiers': cep['status']['external-identifiers'],
                                     'networking': cep['status']['networking'], 'policy': {'realized': {}}}}]
    assert verify(value, pod, cep, agent, response)['selectedEndpointMatches']
    response[0]['id'] = 322
    with pytest.raises(ValueError):
        verify(value, pod, cep, agent, response)


@pytest.mark.parametrize('change', ['owner', 'node', 'id', 'phase', 'pod_uid', 'external'])
def test_stale_or_foreign_metadata_is_refused(change):
    pod, cep, agent = metadata()
    if change == 'owner': cep['metadata']['ownerReferences'][0]['uid'] = 'other'
    if change == 'node': agent['spec']['nodeName'] = 'other'
    if change == 'id': cep['status']['id'] = True
    if change == 'phase': pod['status']['phase'] = 'Succeeded'
    if change == 'pod_uid': pod['metadata']['uid'] = 'replacement'
    if change == 'external': cep['status']['external-identifiers']['cni-attachment-id'] = ''
    with pytest.raises(ValueError):
        pin(pod, cep, agent)


@pytest.mark.parametrize('change', ['pod', 'cep', 'agent', 'endpoint', 'deleting', 'response_cni', 'response_ip', 'multiple'])
def test_post_query_replacement_and_reused_id_are_refused(change):
    pod, cep, agent = metadata()
    saved = pin(pod, cep, agent)
    response = [{'id': 321, 'status': copy.deepcopy(cep['status'])}]
    if change == 'pod':
        pod['metadata']['uid'] = 'replacement'
        cep['metadata']['ownerReferences'][0]['uid'] = 'replacement'
    if change == 'cep': cep['metadata']['uid'] = 'replacement'
    if change == 'agent': agent['metadata']['uid'] = 'replacement'
    if change == 'endpoint': cep['status']['id'] = 322
    if change == 'deleting': pod['metadata']['deletionTimestamp'] = '2026-10-11T00:00:00Z'
    if change == 'response_cni': response[0]['status']['external-identifiers']['cni-attachment-id'] = 'other:eth0'
    if change == 'response_ip': response[0]['status']['networking']['addressing'] = [{'ipv4': '127.0.0.3'}]
    if change == 'multiple': response.append(copy.deepcopy(response[0]))
    with pytest.raises(ValueError):
        verify(saved, pod, cep, agent, response)
