import copy
import hashlib
import struct
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import access_node_proxy_prepare as prep
import access_node_proxy_probe as probe
import access_dns_contrast_exec as launch
import access_runner_dns_prepare as direct
import access_web_prepare as web
from test_access_endpoint_pin import metadata
from test_access_runner_dns_prepare import runner_snapshot
from test_access_web_prepare import package
from test_access_dns_contrast import witness, NONCE


def meta(kind, name, namespace=None, uid=None):
    return {'kind': kind, 'metadata': {'name': name, 'uid': uid or name+'-uid', 'resourceVersion': '1',
                                     **({'namespace': namespace} if namespace else {})}}


def listing(values):
    return {'metadata': {'resourceVersion': '1'}, 'items': values}


@pytest.fixture
def bundle(runner_snapshot):
    snapshot, inputs, images = runner_snapshot
    snapshot['api']['metadata']['generation'] = 1
    snapshot['api']['status'] = {'observedGeneration': 1, 'replicas': 1, 'readyReplicas': 1, 'updatedReplicas': 1, 'availableReplicas': 1}
    snapshot['runner']['kind'] = 'Deployment'
    snapshot['runner']['metadata'].update(uid='runner-deployment', resourceVersion='1')
    snapshot['config']['metadata'].update(uid='hosting-config', resourceVersion='1')
    baseline = direct.emit(*runner_snapshot)
    hostname = baseline['spec']['template']['spec']['nodeSelector']['kubernetes.io/hostname']
    node = meta('Node', hostname)
    node['metadata']['labels'] = {'kubernetes.io/hostname': hostname}
    node['status'] = {'conditions': [{'type': 'Ready', 'status': 'True'}],
                      'nodeInfo': {'machineID': 'machine', 'bootID': 'boot', 'kernelVersion': '6.6-test'}}
    ds = meta('DaemonSet', 'cilium', 'kube-system')
    mounts = [{'name': 'config', 'mountPath': '/tmp/cilium/config-map'}]
    ds['spec'] = {'template': {'spec': {'hostNetwork': True, 'volumes': [{'name': 'config', 'emptyDir': {}}],
          'containers': [{'name': 'cilium-agent', 'command': ['cilium-agent'], 'image': prep.CILIUM_IMAGE,
                          'args': ['--config-dir=/tmp/cilium/config-map'], 'volumeMounts': mounts}],
          'initContainers': [{'name': 'config', 'command': ['cilium-dbg', 'build-config'], 'image': prep.CILIUM_IMAGE,
                              'env': [{'name': 'K8S_NODE_NAME', 'valueFrom': {'fieldRef': {'apiVersion': 'v1', 'fieldPath': 'spec.nodeName'}}}],
                              'args': ['--source=config-map:kube-system/cilium-config,cilium-node-config:kube-system',
                                       '--dest=/tmp/cilium/config-map'], 'volumeMounts': mounts}]}}}
    pod = meta('Pod', 'cilium-original', 'kube-system')
    pod['metadata'].update(labels={'k8s-app': 'cilium'}, ownerReferences=[{
        'kind': 'DaemonSet', 'name': 'cilium', 'uid': ds['metadata']['uid'], 'controller': True}])
    pod['spec'] = copy.deepcopy(ds['spec']['template']['spec']) | {'nodeName': hostname}
    pod['status'] = {'phase': 'Running', 'conditions': [{'type': 'Ready', 'status': 'True'}],
                     'containerStatuses': [{'name': 'cilium-agent', 'ready': True, 'state': {'running': {'startedAt': 'now'}},
                                            'containerID': 'containerd://agent', 'restartCount': 0,
                                            'imageID': 'cilium@sha256:'+'a'*64}]}
    crd = meta('CustomResourceDefinition', 'ciliumnodeconfigs.cilium.io')
    crd['spec'] = {'group': 'cilium.io', 'scope': 'Namespaced', 'names': {'kind': 'CiliumNodeConfig'},
                   'versions': [{'name': 'v2', 'served': True,
                                 'schema': {'openAPIV3Schema': {'properties': {'spec': {'properties': {
                                     'defaults': {'additionalProperties': {'type': 'string'}},
                                     'nodeSelector': {'type': 'object'}}}}}}}]}
    crd['status'] = {'conditions': [{'type': 'Established', 'status': 'True'}]}
    policies = [meta(kind, name, ns) | {'spec': {'immutable': name}} for kind, name, ns in (
        ('CiliumNetworkPolicy', 'access-web-boundary-v1', 'codex-trial'),
        ('CiliumNetworkPolicy', 'hosting-access-web-link-v1', 'm2-hosting'),
        ('CiliumClusterwideNetworkPolicy', 'anxious-lookout-node-protection', None))]
    web.configure(images)
    for expected in web.emit(snapshot, inputs, 'policy')['items']:
        next(p for p in policies if p['metadata']['name'] == expected['metadata']['name'])['spec'] = expected['spec']
    cm = meta('ConfigMap', 'cilium-config', 'kube-system') | {'data': {prep.FLAG: 'false'}}
    p = {'policy-revision': 1, 'policy-enabled': 'both', 'l4': {'egress': [], 'ingress': []}}
    endpoints = {alias: {'podUid': alias+'-pod', 'cepUid': alias+'-cep', 'attachment': alias+'-attachment',
                          'agentUid': pod['metadata']['uid'], 'state': 'ready',
                          'policy': {'spec': copy.deepcopy(p), 'realized': copy.deepcopy(p)}} for alias in ('api', 'dns')}
    return {'expectedNode': {'name': hostname, 'hostname': hostname, 'uid': node['metadata']['uid']},
            'nodes': listing([node]), 'daemonset': ds, 'agents': listing([pod]), 'crd': crd,
            'nodeConfigs': listing([]), 'configMap': cm, 'policies': policies, 'endpoints': endpoints,
            'settings': dict(zip(prep.KEYS, ('false', 'true', 'false', 'true', 'false'))),
            'version': '1.20.2', 'independentApiServer': 'https://127.0.0.1:6443', 'independentApiGetPassed': True,
            'snapshot': snapshot, 'inputs': inputs, 'images': images, 'baseline': baseline}


def capability(receipt):
    calls = []
    result = probe.run(receipt['binding'], 'machine', 'boot', '6.6-test', calls.append)
    assert len(calls) == 4
    result['lifecycle'] = {'mode': 'probe', 'imageId': probe.PROBE_TOOL_IMAGE, 'ownerNonce': NONCE,
        'containerName': 'access-node-proxy-'+NONCE, 'containerId': 'b'*64, 'childExit': 0,
        'probeCompleted': True, 'containerAbsentVerified': True, 'p2Eligible': True}
    return result


def replacement(b):
    pod = b['agents']['items'][0]
    pod['metadata'].update(uid='replacement', name='cilium-replacement')
    for endpoint in b['endpoints'].values(): endpoint['agentUid'] = 'replacement'
    b['status'] = {'cilium': {'state': 'Ok'}, 'kubernetes': {'state': 'Ok'}}
    return prep.ident(pod, 'Pod', 'kube-system')


def installed_config(b, receipt):
    config = prep.emit(receipt, capability(receipt))
    config['metadata'].update(uid='owned-config', resourceVersion='2')
    b['ownedConfig'] = {'uid': 'owned-config'}
    b['nodeConfigs'] = listing([config])


def test_single_node_diff_default_rollback_and_fresh_conditional_deletes(bundle):
    assert prep.select_initial(bundle)['uid'] == bundle['agents']['items'][0]['metadata']['uid']
    receipt = prep.preflight(bundle)
    emitted = prep.emit(receipt, capability(receipt))
    assert emitted['spec'] == {'nodeSelector': {'matchLabels': {'kubernetes.io/hostname': receipt['node']['hostname']}},
                               'defaults': {prep.FLAG: 'true'}}
    initial = prep.delete_agent(receipt, bundle['nodes'], bundle['daemonset'], bundle['agents'], 'apply')
    assert initial['body']['preconditions']['uid'] == receipt['initialAgent']['uid']
    new = replacement(bundle)
    # New agent never became Ready, and cannot exec. Recovery still uses host GETs.
    bundle['agents']['items'][0]['status'] = {'phase': 'Pending'}
    rollback = prep.delete_agent(receipt, bundle['nodes'], bundle['daemonset'], bundle['agents'], 'rollback', new)
    assert rollback['body']['preconditions']['uid'] == 'replacement'
    current = copy.deepcopy(emitted); current['metadata'].update(uid='owned', resourceVersion='2')
    assert prep.delete_config(receipt, current, {'uid': 'owned'}, emitted)['body']['preconditions'] == {'uid': 'owned', 'resourceVersion': '2'}
    assert receipt['normalAgentReplacements'] == 2 and receipt['defaultRollback'] is True


@pytest.mark.parametrize('change', ['wrong_node', 'wrong_uid', 'multi_node', 'not_ready', 'boot_missing', 'machine_duplicate',
    'crd_missing', 'crd_not_served', 'crd_not_established', 'conflict', 'same_name', 'empty_selector',
    'already_true', 'unsupported_version', 'cluster_ip_api', 'api_failed', 'source_order', 'deny_flag',
    'unknown_env', 'shell', 'wrong_mount', 'wrong_owner', 'multi_agent', 'wrong_image', 'api_drift', 'api_unready', 'api_unobserved', 'endpoint_unrealized'])
def test_preflight_fails_before_mutation(bundle, change):
    node = bundle['nodes']['items'][0]; ds = bundle['daemonset']; pod = bundle['agents']['items'][0]
    init = ds['spec']['template']['spec']['initContainers'][0]
    if change == 'wrong_node': bundle['expectedNode']['hostname'] = 'other'
    elif change == 'wrong_uid': bundle['expectedNode']['uid'] = 'other'
    elif change == 'multi_node': bundle['nodes']['items'].append(copy.deepcopy(node))
    elif change == 'not_ready': node['status']['conditions'][0]['status'] = 'False'
    elif change == 'boot_missing': node['status']['nodeInfo']['bootID'] = ''
    elif change == 'machine_duplicate':
        other = copy.deepcopy(node); other['metadata']['labels']['kubernetes.io/hostname'] = 'other'; bundle['nodes']['items'].append(other)
    elif change == 'crd_missing': bundle['crd']['metadata']['name'] = 'other'
    elif change == 'crd_not_served': bundle['crd']['spec']['versions'][0]['served'] = False
    elif change == 'crd_not_established': bundle['crd']['status']['conditions'][0]['status'] = 'False'
    elif change in ('conflict', 'empty_selector', 'same_name'):
        c = meta('CiliumNodeConfig', prep.CONFIG if change == 'same_name' else 'conflict', 'kube-system')
        c['spec'] = {'nodeSelector': {} if change == 'empty_selector' else {'matchLabels': node['metadata']['labels']}}
        bundle['nodeConfigs']['items'].append(c)
    elif change == 'already_true': bundle['settings'][prep.FLAG] = 'true'
    elif change == 'unsupported_version': bundle['version'] = '1.19.0'
    elif change == 'cluster_ip_api': bundle['independentApiServer'] = 'https://10.43.0.1:443'
    elif change == 'api_failed': bundle['independentApiGetPassed'] = False
    elif change == 'source_order': init['args'][0] = '--source=cilium-node-config:kube-system,config-map:kube-system/cilium-config'
    elif change == 'deny_flag': init['args'].append('--deny-config-keys=enable-bpf-tproxy')
    elif change == 'unknown_env': init['env'] = [{'name': 'CILIUM_ENABLE_BPF_TPROXY', 'value': 'false'}]
    elif change == 'shell': init['command'] = ['sh', '-c']
    elif change == 'wrong_mount': init['volumeMounts'][0]['mountPath'] = '/other'
    elif change == 'wrong_owner': pod['metadata']['ownerReferences'][0]['uid'] = 'other'
    elif change == 'multi_agent': bundle['agents']['items'].append(copy.deepcopy(pod))
    elif change == 'wrong_image': pod['spec']['containers'][0]['image'] = 'other'
    elif change == 'api_drift': bundle['snapshot']['api']['spec']['replicas'] = 0
    elif change == 'api_unready': bundle['snapshot']['api']['status']['readyReplicas'] = 0
    elif change == 'api_unobserved': bundle['snapshot']['api']['status']['observedGeneration'] = 0
    else: bundle['endpoints']['api']['policy']['realized']['policy-revision'] = 2
    with pytest.raises((ValueError, KeyError)): prep.preflight(bundle)


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'literal', 'wrong_field', 'wrong_version', 'extra_value'])
def test_config_init_requires_exact_once_node_fieldref(bundle, change):
    init = bundle['daemonset']['spec']['template']['spec']['initContainers'][0]
    env = init['env']
    if change == 'missing': init['env'] = []
    elif change == 'duplicate': env.append(copy.deepcopy(env[0]))
    elif change == 'literal': env[0] = {'name': 'K8S_NODE_NAME', 'value': bundle['expectedNode']['name']}
    elif change == 'wrong_field': env[0]['valueFrom']['fieldRef']['fieldPath'] = 'metadata.name'
    elif change == 'wrong_version': env[0]['valueFrom']['fieldRef']['apiVersion'] = 'v2'
    else: env[0]['value'] = ''
    bundle['agents']['items'][0]['spec']['initContainers'] = copy.deepcopy(bundle['daemonset']['spec']['template']['spec']['initContainers'])
    with pytest.raises(ValueError): prep.startup(bundle['daemonset'])
    with pytest.raises(ValueError): prep.select_initial(bundle)
    with pytest.raises(ValueError): prep.preflight(bundle)


@pytest.mark.parametrize('change', ['node_uid', 'boot', 'capability_unknown', 'attach', 'hash', 'load_count'])
def test_capability_unknown_cannot_emit_node_config(bundle, change):
    r = prep.preflight(bundle); c = capability(r)
    if change == 'node_uid': c['nodeUid'] = 'other'
    elif change == 'boot': c['bootId'] = 'other'
    elif change == 'capability_unknown': c['helperContextSupported'] = 'unknown'
    elif change == 'attach': c['attached'] = True
    elif change == 'hash': c['programHashes'][0] = 'other'
    else: c['loadsClosed'] = True
    with pytest.raises(ValueError): prep.emit(r, c)


@pytest.mark.parametrize('stage', ['apply', 'rollback'])
def test_replaced_uid_and_ds_drift_never_delete_unowned_agent(bundle, stage):
    r = prep.preflight(bundle); new = replacement(bundle)
    saved = r['initialAgent'] if stage == 'apply' else new
    bundle['agents']['items'][0]['metadata']['uid'] = 'unseen'
    with pytest.raises(ValueError): prep.delete_agent(r, bundle['nodes'], bundle['daemonset'], bundle['agents'], stage, saved)
    bundle['agents']['items'][0]['metadata']['uid'] = saved['uid']
    bundle['daemonset']['metadata']['uid'] = 'new-controller'
    with pytest.raises(ValueError): prep.delete_agent(r, bundle['nodes'], bundle['daemonset'], bundle['agents'], stage, saved)


@pytest.mark.parametrize('change', ['false', 'ready_failed', 'status_failed', 'image_changed', 'policy_changed', 'endpoint_changed', 'baseline_changed'])
def test_ineffective_or_unhealthy_apply_never_grants_dns(bundle, change):
    r = prep.preflight(bundle); replacement(bundle); installed_config(bundle, r); bundle['settings'][prep.FLAG] = 'true'
    if change == 'false': bundle['settings'][prep.FLAG] = 'false'
    elif change == 'ready_failed': bundle['agents']['items'][0]['status']['conditions'][0]['status'] = 'False'
    elif change == 'status_failed': bundle['status']['cilium']['state'] = 'Failure'
    elif change == 'image_changed': bundle['agents']['items'][0]['status']['containerStatuses'][0]['imageID'] = 'other'
    elif change == 'policy_changed': bundle['policies'][0]['spec'] = {'changed': True}
    elif change == 'endpoint_changed': bundle['endpoints']['api']['attachment'] = 'other'
    else: bundle['snapshot']['runnerPodCount'] = 1
    with pytest.raises(ValueError): prep.health(r, bundle, 'true', [r['initialAgent']['uid']])


def test_health_settings_and_endpoints_required_on_default_restore(bundle):
    r = prep.preflight(bundle); replacement(bundle)
    assert prep.health(r, bundle, 'false', [r['initialAgent']['uid']])['uid'] == 'replacement'
    bundle['nodeConfigs']['items'].append({'remaining': True})
    with pytest.raises(ValueError): prep.health(r, bundle, 'false', [r['initialAgent']['uid']])


@pytest.mark.parametrize('stage,elapsed,result,next_action', [
    ('apply-ready', 119, 'pending', 'wait-within-120s'), ('apply-ready', 120, 'pending', 'remove-owned-config'),
    ('apply-health', 1, 'failed', 'remove-owned-config'), ('dns', 1, 'ok', 'exact-job-cleanup-then-remove-owned-config'),
    ('dns', 1, 'failed', 'exact-job-cleanup-then-remove-owned-config'), ('rollback-ready', 120, 'pending', 'stop-handoff'),
    ('rollback-health', 1, 'failed', 'stop-handoff'), ('rollback-config', 1, 'failed', 'stop-handoff')])
def test_failure_paths_are_bounded_and_success_still_rolls_back(stage, elapsed, result, next_action):
    assert prep.disposition(stage, elapsed, result) == next_action


@pytest.mark.parametrize('change', ['uid', 'spec'])
def test_owned_config_replacement_or_drift_is_not_deleted(bundle, change):
    r = prep.preflight(bundle); emitted = prep.emit(r, capability(r)); obj = copy.deepcopy(emitted)
    obj['metadata'].update(uid='owned', resourceVersion='2')
    if change == 'uid': obj['metadata']['uid'] = 'other'
    else: obj['spec']['defaults'][prep.FLAG] = 'false'
    with pytest.raises(ValueError): prep.delete_config(r, obj, {'uid': 'owned'}, emitted)


@pytest.mark.parametrize('family,protocol', [(4, 'tcp'), (4, 'udp'), (6, 'tcp'), (6, 'udp')])
def test_probe_correct_context_helper_chain_and_no_attach_syscalls(family, protocol, monkeypatch):
    code = probe.instructions(family, protocol)
    decoded = list(struct.iter_unpack('<BBhi', code))
    assert [imm for op, _, _, imm in decoded if op == 0x85] == [99 if protocol == 'tcp' else 85, 124, 86]
    assert (12 if family == 4 else 36) in [imm for op, _, _, imm in decoded if op == 0xb7]
    calls = []; closed = []
    def syscall(number, command, attr, length):
        calls.append((command.value, attr._obj.prog_type, length.value))
        return 17
    monkeypatch.setattr(probe.ctypes, 'CDLL', lambda *a, **kw: SimpleNamespace(syscall=MockCall(syscall)))
    monkeypatch.setattr(probe.os, 'close', closed.append)
    monkeypatch.setattr(probe.platform, 'machine', lambda: 'aarch64')
    probe.load_only(code)
    assert calls == [(5, 3, 72)] and closed == [17]


class MockCall:
    def __init__(self, fn): self.fn = fn
    def __call__(self, *a): return self.fn(*a)


def test_probe_failure_stops_remaining_loads_and_host_mismatch_prevents_any_load(bundle):
    r = prep.preflight(bundle); calls = []
    def fail(code): calls.append(code); raise ValueError()
    with pytest.raises(ValueError): probe.run(r['binding'], 'machine', 'boot', '6.6-test', fail)
    assert len(calls) == 1
    with pytest.raises(ValueError): probe.run(r['binding'], 'other', 'boot', '6.6-test', calls.append)
    assert len(calls) == 1


def test_failed_bpf_syscall_never_calls_close_on_invalid_fd(monkeypatch):
    closed = []
    monkeypatch.setattr(probe.ctypes, 'CDLL', lambda *a, **kw: SimpleNamespace(syscall=MockCall(lambda *args: -1)))
    monkeypatch.setattr(probe.os, 'close', closed.append)
    monkeypatch.setattr(probe.platform, 'machine', lambda: 'aarch64')
    with pytest.raises(ValueError): probe.load_only(probe.instructions(4, 'tcp'))
    assert closed == []


def test_original_service_membership_and_new_holder_keep_auth_unmounted(bundle):
    r = prep.preflight(bundle); job = prep.dns_job(bundle, r, NONCE)
    ps = job['spec']['template']['spec']; container = ps['containers'][0]
    assert job['metadata']['name'] == 'access-runner-dns-node-v1'
    assert job['spec']['template']['metadata']['labels']['app'] == 'access-web-runner'
    assert container['image'] == direct.RUNNER_IMAGE and ps['volumes'] == container['volumeMounts'] == []
    assert container['env'] == launch.ENV and ps['automountServiceAccountToken'] is False
    service = meta('Service', 'access-web-runner-v1', 'codex-trial') | {'spec': {'selector': {'app': 'access-web-runner'}}}
    assert len(prep.service_membership(listing([service]), {'app': 'access-web-runner'}, job['metadata']['name']+'-x')) == 1
    service['spec']['publishNotReadyAddresses'] = True
    with pytest.raises(ValueError): prep.service_membership(listing([service]), {'app': 'access-web-runner'}, job['metadata']['name']+'-x')


def dns_context(bundle):
    r = prep.preflight(bundle)
    replacement(bundle); installed_config(bundle, r); bundle['settings'][prep.FLAG] = 'true'
    healthy = prep.health(r, bundle, 'true', [r['initialAgent']['uid']])
    job = prep.dns_job(bundle, r, NONCE); job['metadata'].update(uid='job', resourceVersion='2')
    pod, cep, _ = metadata()
    pod['metadata'].update(name=job['metadata']['name']+'-test', labels=copy.deepcopy(job['spec']['template']['metadata']['labels']),
        ownerReferences=[{'kind': 'Job', 'name': job['metadata']['name'], 'uid': 'job', 'controller': True}])
    for k in launch.control.JOB_NAME_LABELS: pod['metadata']['labels'][k] = job['metadata']['name']
    for k in launch.control.JOB_UID_LABELS: pod['metadata']['labels'][k] = 'job'
    pod['spec'] = copy.deepcopy(job['spec']['template']['spec']) | {'nodeName': r['node']['name']}
    pod['status']['containerStatuses'] = [{'name': 'diagnose', 'restartCount': 0, 'state': {'running': {}},
                                         'containerID': 'containerd://dns', 'imageID': direct.RUNNER_IMAGE}]
    cep['metadata']['name'] = pod['metadata']['name']; cep['metadata']['ownerReferences'][0]['name'] = pod['metadata']['name']
    production, _, global_policy = bundle['policies']
    reference = witness(production, global_policy)
    policy = reference[0]['status']['policy']['realized']
    ep = [{'id': cep['status']['id'], 'status': copy.deepcopy(cep['status']) | {
        'policy': {'spec': copy.deepcopy(policy), 'realized': copy.deepcopy(policy), 'proxy-policy-revision': 12,
                   'proxy-statistics': [{'protocol': 'dns', 'location': 'egress', 'port': 53, 'allocated-proxy-port': 30000} for _ in range(2)]}}}]
    service = meta('Service', 'access-web-runner-v1', 'codex-trial') | {'spec': {'selector': {'app': 'access-web-runner'}}}
    data = dict(job=job, pod=pod, cep=cep, agent=bundle['agents']['items'][0], policies=bundle['policies'],
                services=listing([service]), production=production, globalPolicy=global_policy, reference=reference, endpoint=ep)
    return r, healthy, data


def test_original_runner_l7_fresh_agent_gate_and_single_use_program(bundle):
    r, healthy, b = dns_context(bundle)
    granted = prep.dns_scope(r, healthy, b, NONCE)
    holder_receipt = {'holding': True, 'networkRequested': False, 'podUid': b['pod']['metadata']['uid'],
        'podName': b['pod']['metadata']['name'], 'nonce': NONCE, 'startTicks': 100, 'startNs': 1,
        'cmdlineSha256': hashlib.sha256(('\0'.join(launch.holder_command(NONCE))+'\0').encode()).hexdigest()}
    argv = launch.command(b['job'], b['pod'], holder_receipt, granted, 'node', NONCE,
                          bundle['baseline']['spec']['template']['spec']['containers'][0]['command'][3])
    assert argv[:3] == ['python', '-B', '-c'] and 'run_once' in argv[3]


@pytest.mark.parametrize('change', ['old_agent', 'agent_restart', 'unacked', 'l4', 'service', 'other_policy'])
def test_false_or_bypass_fresh_dns_gate_never_grants_network(bundle, change):
    r, healthy, b = dns_context(bundle)
    if change == 'old_agent': b['agent']['metadata']['uid'] = r['initialAgent']['uid']
    elif change == 'agent_restart': b['agent']['status']['containerStatuses'][0]['restartCount'] += 1
    elif change == 'unacked': b['endpoint'][0]['status']['policy']['proxy-policy-revision'] = 11
    elif change == 'l4':
        body = b['endpoint'][0]['status']['policy']['realized']['l4']['egress'][2]
        import json
        rule = json.loads(body['rule']); rule['l7-rules'] = []; body['rule'] = json.dumps(rule)
    elif change == 'service': b['services']['items'][0]['metadata']['name'] = 'other'
    else: b['policies'][0]['spec']['enableDefaultDeny']['egress'] = False
    with pytest.raises(ValueError): prep.dns_scope(r, healthy, b, NONCE)


@pytest.mark.parametrize('change', ['missing', 'replaced', 'spec', 'second_node'])
def test_fresh_apply_config_and_selector_scope_cannot_drift(bundle, change):
    r = prep.preflight(bundle); replacement(bundle); installed_config(bundle, r); bundle['settings'][prep.FLAG] = 'true'
    if change == 'missing': bundle['nodeConfigs']['items'] = []
    elif change == 'replaced': bundle['nodeConfigs']['items'][0]['metadata']['uid'] = 'other'
    elif change == 'spec': bundle['nodeConfigs']['items'][0]['spec']['defaults'][prep.FLAG] = 'false'
    else:
        node = copy.deepcopy(bundle['nodes']['items'][0]); node['metadata']['name'] = 'other'; bundle['nodes']['items'].append(node)
    with pytest.raises(ValueError): prep.health(r, bundle, 'true', [r['initialAgent']['uid']])


@pytest.mark.parametrize('mode,count', [('objects', 11), ('agent', 7)])
def test_readonly_wrapper_exact_commands_and_exclusive_files(tmp_path, mode, count):
    tool = tmp_path/'kubectl'; capture = tmp_path/'capture'
    tool.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >>'+str(capture)+'\nprintf "{}\\n"\n')
    tool.chmod(0o700)
    evidence = tmp_path/'evidence'; evidence.mkdir(mode=0o700)
    wrapper = Path(__file__).with_name('node_proxy_readonly.sh')
    argv = ['bash', str(wrapper), str(tool), 'cilium-selected', str(evidence), mode]
    assert subprocess.run(argv, capture_output=True).returncode == 0
    commands = capture.read_text().splitlines()
    assert len(commands) == count
    assert not any(' create ' in c or ' delete ' in c or ' monitor ' in c or 'feature' in c for c in commands)
    assert all('--request-timeout=8s' in c for c in commands)
    assert subprocess.run(argv, capture_output=True).returncode != 0
    assert capture.read_text().splitlines() == commands


def test_readonly_wrapper_failure_stops_remaining_commands(tmp_path):
    tool = tmp_path/'kubectl'; capture = tmp_path/'capture'
    tool.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >>'+str(capture)+'\nexit 1\n'); tool.chmod(0o700)
    evidence = tmp_path/'evidence'; evidence.mkdir(mode=0o700)
    wrapper = Path(__file__).with_name('node_proxy_readonly.sh')
    assert subprocess.run(['bash', str(wrapper), str(tool), 'cilium-selected', str(evidence), 'objects'], capture_output=True).returncode == 1
    assert len(capture.read_text().splitlines()) == 1
