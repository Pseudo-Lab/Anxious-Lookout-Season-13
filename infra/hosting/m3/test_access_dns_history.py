import copy
import json

import pytest

from access_dns_history import MAX_BYTES, plan, summarize, verify_filters


def scope():
    pin = {'namespace': 'codex-trial', 'podName': 'access-dns-observed-diagnose-v1-test',
           'podUid': 'pod-uid', 'node': 'test-node', 'podIp': '127.0.0.2',
           'agentUid': 'agent-uid', 'agentName': 'test-agent'}
    pod = {'kind': 'Pod', 'metadata': {'namespace': pin['namespace'], 'name': pin['podName'], 'uid': pin['podUid'],
           'ownerReferences': [{'kind': 'Job', 'controller': True, 'name': 'test-job', 'uid': 'job-uid'}]},
           'spec': {'nodeName': pin['node']}, 'status': {'podIP': pin['podIp']}}
    job = {'kind': 'Job', 'metadata': {'namespace': pin['namespace'], 'name': 'test-job', 'uid': 'job-uid'},
           'spec': {'activeDeadlineSeconds': 60, 'backoffLimit': 0},
           'status': {'succeeded': 1, 'startTime': '2026-10-11T11:28:21Z', 'completionTime': '2026-10-11T11:29:11Z'}}
    agent = {'kind': 'Pod', 'metadata': {'namespace': 'kube-system', 'name': pin['agentName'], 'uid': pin['agentUid'],
             'labels': {'k8s-app': 'cilium'}}, 'spec': {'nodeName': pin['node']}, 'status': {'phase': 'Running'}}
    return pin, pod, job, agent


def response(inbound=False):
    pin, *_ = scope()
    target = {'namespace': pin['namespace'], 'pod_name': pin['podName']}
    other = {'namespace': 'kube-system', 'pod_name': 'test-dns'}
    return {'flow': {'time': '2026-10-11T11:28:30Z', 'node_name': pin['node'], 'event_type': {'type': 1},
            'source': other if inbound else target, 'destination': target if inbound else other,
            'IP': {'source': '127.0.0.3' if inbound else pin['podIp'], 'destination': pin['podIp'] if inbound else '127.0.0.3'},
            'l4': {'UDP': {'source_port': 53 if inbound else 54321, 'destination_port': 54321 if inbound else 53}},
            'verdict': 'DROPPED', 'drop_reason_desc': 'POLICY_DENIED',
            'egress_denied_by': [{'name': 'anxious-lookout-node-protection', 'kind': 'CiliumClusterwideNetworkPolicy'}]}}


def test_filters_bind_both_directions_and_mask_excludes_payload():
    value = plan(*scope())
    a, b = value['filters']
    assert a['source_ip'] == b['destination_ip'] == ['127.0.0.2']
    assert a['source_pod'] == b['destination_pod']
    assert a['destination_port'] == b['source_port'] == ['53']
    assert a['event_type'] == b['event_type'] == [{'type': 1}, {'type': 5}]
    assert '--last' not in value['argv'] and '--follow' not in value['argv']
    assert 'l7' not in value['argv'][value['argv'].index('--field-mask')+1]


@pytest.mark.parametrize('change', ['none', 'loose_or', 'wrong_port', 'missing', 'deny'])
def test_printed_raw_filters_cannot_widen_scope(change):
    value = plan(*scope())
    filters = copy.deepcopy(value['filters'])
    if change == 'loose_or': filters.append({'source_pod': ['codex-trial/']})
    if change == 'wrong_port': filters[0]['destination_port'] = ['443']
    if change == 'missing': filters.pop()
    raw = 'allowlist:\n' + ''.join("- '"+json.dumps(f)+"'\n" for f in filters)
    raw += 'denylist: []\n' if change != 'deny' else "denylist:\n- '{}'\n"
    if change == 'none':
        assert verify_filters(value, raw.encode()) == {'exactRawFiltersMatch': True}
    else:
        with pytest.raises(ValueError):
            verify_filters(value, raw.encode())


@pytest.mark.parametrize('inbound', [False, True])
def test_exact_records_only_emit_safe_counts(inbound):
    result = summarize(plan(*scope()), json.dumps(response(inbound)).encode(), 0)
    assert result['namedNodePolicyDeniedRecords'] == 1
    assert result['dnsCause'] == 'unknown'
    assert '127.0.0' not in json.dumps(result)


def test_same_named_namespaced_policy_does_not_attribute_global_deny():
    value = response()
    value['flow']['egress_denied_by'][0].update(kind='CiliumNetworkPolicy', namespace='codex-trial')
    result = summarize(plan(*scope()), json.dumps(value).encode(), 0)
    assert result['policyDeniedRecords'] == 1
    assert result['namedNodePolicyDeniedRecords'] == 0


@pytest.mark.parametrize('change', ['prefix', 'ip', 'node', 'time', 'port', 'trace', 'payload', 'lost', 'empty', 'truncated', 'exit'])
def test_foreign_or_incomplete_response_is_unknown(change):
    value = response()
    if change == 'prefix': value['flow']['source']['pod_name'] += '-foreign'
    if change == 'ip': value['flow']['IP']['source'] = '127.0.0.4'
    if change == 'node': value['flow']['node_name'] = 'foreign'
    if change == 'time': value['flow']['time'] = '2026-10-11T12:00:00Z'
    if change == 'port': value['flow']['l4']['UDP']['destination_port'] = 443
    if change == 'trace': value['flow']['event_type']['type'] = 4
    if change == 'payload': value['flow']['l7'] = {'dns': {'query': 'foreign'}}
    if change == 'lost': value = {'lost_events': {'num_events_lost': 1}}
    raw = json.dumps(value).encode()
    if change == 'empty': raw = b''
    if change == 'truncated': raw = b'x' * MAX_BYTES
    with pytest.raises((ValueError, KeyError)):
        summarize(plan(*scope()), raw, 1 if change == 'exit' else 0)


@pytest.mark.parametrize('change', ['owner', 'agent', 'duration', 'unfinished'])
def test_scope_rejects_wrong_owner_agent_and_unbounded_time(change):
    pin, pod, job, agent = copy.deepcopy(scope())
    if change == 'owner': pod['metadata']['ownerReferences'][0]['uid'] = 'other'
    if change == 'agent': agent['metadata']['uid'] = 'other'
    if change == 'duration': job['status']['completionTime'] = '2026-10-11T12:00:00Z'
    if change == 'unfinished': job['status']['succeeded'] = 0
    with pytest.raises(ValueError):
        plan(pin, pod, job, agent)
