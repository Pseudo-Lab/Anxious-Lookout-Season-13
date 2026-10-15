import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace

import pytest

import access_dns_contrast_prepare as prep
import access_dns_contrast_exec as launch
import access_dns_contrast_runtime as runtime
import access_dns_contrast_scope as scope
import access_runner_dns_prepare as direct
import access_web_prepare as web
from test_access_web_prepare import package
from test_access_runner_dns_prepare import runner_snapshot
from test_access_runner_dns_control_prepare import service_list, changes
from test_access_endpoint_pin import metadata

NONCE = 'a'*32


@pytest.fixture
def policy_data(runner_snapshot):
    snapshot, inputs, images = runner_snapshot
    baseline = direct.emit(*runner_snapshot)
    web.configure(images)
    production = web.emit(snapshot, inputs, 'policy')['items'][0]
    production['metadata'].update(uid='production-uid', resourceVersion='1')
    pair = prep.policy_pair(*runner_snapshot, production, baseline)
    return production, pair, baseline


def result():
    return {'helperAttempts': 4, 'libcQueries': 0, 'nativeStarted': False,
            'sourceCredentialRead': False, 'httpOrAuthRpc': False,
            'dnsContrastContext': {'phase': 'l4', 'jobUid': 'l4-job', 'podUid': 'l4-pod', 'nonce': NONCE,
                                   'policyUid': 'diagnostic-uid', 'policyRv': '2', 'policyRevision': 12, 'startTicks': 100},
            **{name: {key: {'status': 'reply', 'rcode': 0, 'answersPresent': True}
                      for key in ('absoluteUdp', 'absoluteTcp')} for name in ('codexBackend', 'callbackService')}}


def owned(obj):
    return {key: obj['metadata'][key] for key in ('namespace', 'name', 'uid')} | {'kind': obj['kind']}


def cleanup():
    return {'job': {'kind': 'Job', 'namespace': 'codex-trial', 'name': prep.JOBS['l4'], 'uid': 'l4-job'},
            'podUid': 'l4-pod', 'nonce': NONCE, 'jobAbsent': True, 'ownerPodCount': 0, 'controlPodCount': 0, 'runnerPodCount': 0}


def test_policy_clone_and_conditional_transition_only_change_one_dns_path(policy_data):
    production, pair, _ = policy_data
    assert changes(production['spec'], pair['l7']['spec']) == [('endpointSelector', 'matchLabels', 'app')]
    converted = copy.deepcopy(pair['l4'])
    converted['spec']['egress'][1]['toPorts'][0]['rules'] = pair['l7']['spec']['egress'][1]['toPorts'][0]['rules']
    assert converted == pair['l7']
    current = copy.deepcopy(pair['l4']); current['metadata'].update(uid='diagnostic-uid', resourceVersion='2')
    patch = prep.transition(current, pair, owned(current), result(), 0, cleanup())
    assert [v['path'] for v in patch] == ['/metadata/uid', '/metadata/resourceVersion', '/spec', prep.RULE_PATH]
    assert patch[-1]['op'] == 'add'
    assert current['spec']['enableDefaultDeny'] == {'ingress': True, 'egress': True}
    assert prep.deletion(current, owned(current))['preconditions'] == {'uid': 'diagnostic-uid'}


@pytest.mark.parametrize('change', ['uid', 'spec', 'timeout', 'rcode', 'bool_rcode', 'transport', 'job_present', 'owner_pod', 'control_pod', 'runner_pod', 'bool_count', 'other_phase', 'other_job', 'other_nonce'])
def test_failed_l4_or_cleanup_cannot_enter_l7(policy_data, change):
    _, pair, _ = policy_data
    current = copy.deepcopy(pair['l4']); current['metadata'].update(uid='diagnostic-uid', resourceVersion='2')
    saved = owned(current); observed = result(); cleared = cleanup(); exit_code = 0
    if change == 'uid': current['metadata']['uid'] = 'other'
    elif change == 'spec': current['spec']['enableDefaultDeny']['egress'] = False
    elif change == 'timeout': observed['codexBackend']['absoluteUdp']['status'] = 'timeout'
    elif change == 'rcode': observed['callbackService']['absoluteTcp']['rcode'] = 5
    elif change == 'bool_rcode': observed['callbackService']['absoluteTcp']['rcode'] = False
    elif change == 'transport': exit_code = 1
    elif change == 'job_present': cleared['jobAbsent'] = False
    elif change == 'bool_count': cleared['ownerPodCount'] = False
    elif change == 'other_phase': observed['dnsContrastContext']['phase'] = 'l7'
    elif change == 'other_job': observed['dnsContrastContext']['jobUid'] = 'other-job'
    elif change == 'other_nonce': observed['dnsContrastContext']['nonce'] = 'b'*32
    else: cleared[{'owner_pod': 'ownerPodCount', 'control_pod': 'controlPodCount', 'runner_pod': 'runnerPodCount'}[change]] = 1
    with pytest.raises(ValueError): prep.transition(current, pair, saved, observed, exit_code, cleared)


def test_production_objects_and_uid_replacements_are_never_cleanup_targets(policy_data):
    production, pair, _ = policy_data
    with pytest.raises(ValueError): prep.deletion(production, owned(production))
    diagnostic = copy.deepcopy(pair['l4']); diagnostic['metadata'].update(uid='new', resourceVersion='2')
    ref = owned(diagnostic); ref['uid'] = 'old'
    with pytest.raises(ValueError): prep.deletion(diagnostic, ref)


def test_source_or_policy_drift_cannot_be_cloned(runner_snapshot, policy_data):
    production, _, baseline = policy_data
    production['spec']['egress'][1]['toPorts'][0]['rules']['dns'].append({'matchPattern': '*'})
    with pytest.raises(ValueError): prep.policy_pair(*runner_snapshot, production, baseline)


def test_holder_baseline_delta_and_phase_delta_are_mechanically_limited(runner_snapshot, service_list, policy_data):
    baseline = policy_data[2]
    l4 = launch.emit(*runner_snapshot, baseline, service_list, 'l4', NONCE)
    restored = copy.deepcopy(l4)
    restored['metadata']['name'] = baseline['metadata']['name']
    restored['spec']['activeDeadlineSeconds'] = baseline['spec']['activeDeadlineSeconds']
    restored['spec']['template']['metadata']['labels']['app'] = baseline['spec']['template']['metadata']['labels']['app']
    current = restored['spec']['template']['spec']['containers'][0]
    original = baseline['spec']['template']['spec']['containers'][0]
    current['command'], current['env'] = original['command'], original['env']
    assert restored == baseline
    l7 = launch.emit(*runner_snapshot, baseline, service_list, 'l7', 'b'*32)
    l7['metadata']['name'] = l4['metadata']['name']
    l7['spec']['template']['spec']['containers'][0]['command'] = launch.holder_command(NONCE)
    assert l7 == l4


@pytest.mark.parametrize('phase', ['l4', 'l7'])
def test_phase_generated_service_name_labels_are_rejected(runner_snapshot, service_list, policy_data, phase):
    service_list['items'][0]['spec']['selector'] = {'batch.kubernetes.io/job-name': prep.JOBS[phase]}
    with pytest.raises(ValueError): launch.emit(*runner_snapshot, policy_data[2], service_list, phase, NONCE)


def holder_context(runner_snapshot, service_list, policy_data, phase='l4'):
    _, _, baseline = policy_data
    job = launch.emit(*runner_snapshot, baseline, service_list, phase, NONCE)
    job['metadata'].update(uid='job-uid', resourceVersion='3')
    pod, cep, agent = metadata()
    pod['metadata'].update(name=prep.JOBS[phase]+'-fixture', labels=copy.deepcopy(job['spec']['template']['metadata']['labels']),
        ownerReferences=[{'kind': 'Job', 'name': job['metadata']['name'], 'uid': 'job-uid', 'controller': True}])
    for key in ('job-name', 'batch.kubernetes.io/job-name'): pod['metadata']['labels'][key] = job['metadata']['name']
    for key in ('controller-uid', 'batch.kubernetes.io/controller-uid'): pod['metadata']['labels'][key] = 'job-uid'
    pod['spec'] = copy.deepcopy(job['spec']['template']['spec'])
    pod['spec']['nodeName'] = pod['spec']['nodeSelector']['kubernetes.io/hostname']
    pod['status']['containerStatuses'] = [{'name': 'diagnose', 'restartCount': 0, 'state': {'running': {}},
                                           'containerID': 'containerd://fixture', 'imageID': direct.RUNNER_IMAGE}]
    cep['metadata']['name'] = pod['metadata']['name']; cep['metadata']['ownerReferences'][0]['name'] = pod['metadata']['name']
    agent['spec'].update(nodeName=pod['spec']['nodeName'], hostNetwork=True)
    receipt = {'holding': True, 'networkRequested': False, 'podUid': pod['metadata']['uid'], 'podName': pod['metadata']['name'],
               'nonce': NONCE, 'startTicks': 100, 'startNs': 1_000_000_000,
               'cmdlineSha256': hashlib.sha256(('\0'.join(launch.holder_command(NONCE))+'\0').encode()).hexdigest()}
    return job, pod, cep, agent, receipt


def witness(production, global_policy):
    def labels(obj, kind):
        return ['k8s:io.cilium.k8s.policy.derived-from='+kind, 'k8s:io.cilium.k8s.policy.name='+obj['metadata']['name'],
                *(['k8s:io.cilium.k8s.policy.namespace=codex-trial'] if kind == 'CiliumNetworkPolicy' else []),
                'k8s:io.cilium.k8s.policy.uid='+obj['metadata']['uid']]
    def row(obj, kind, port, protocol, l7):
        return {'derived-from-rules': [labels(obj, kind)], 'rule': json.dumps({'tier': 200, 'port': port, 'protocol': protocol, 'l7-rules': l7})}
    policy = {'policy-enabled': 'both', 'policy-revision': 12, 'l4': {'egress': [
        row(global_policy, 'CiliumClusterwideNetworkPolicy', 0, 'ANY', [{'reserved:host': {'verdict': 1}}]),
        row(production, 'CiliumNetworkPolicy', 8080, 'TCP', [{'api': None}]),
        *[row(production, 'CiliumNetworkPolicy', 53, protocol, [{scope.DNS_SELECTOR: scope.DNS_RULES}]) for protocol in ('TCP', 'UDP')],
        row(production, 'CiliumNetworkPolicy', 443, 'TCP', [{'provider': None}])],
        'ingress': [row(production, 'CiliumNetworkPolicy', 8080, 'TCP', [{'api': None}])]}}
    return [{'status': {'policy': {'realized': policy}}}]


def realized_context(runner_snapshot, service_list, policy_data, phase):
    production, pair, _ = policy_data
    job, pod, cep, agent, receipt = holder_context(runner_snapshot, service_list, policy_data, phase)
    diagnostic = copy.deepcopy(pair[phase]); diagnostic['metadata'].update(uid='diagnostic-uid', resourceVersion='2')
    global_policy = {'kind': 'CiliumClusterwideNetworkPolicy', 'metadata': {'name': 'anxious-lookout-node-protection',
                     'uid': 'global-uid', 'resourceVersion': '1'}, 'spec': {'fixture': 'immutable'}}
    reference = witness(production, global_policy)
    rows = scope.rows(reference[0]['status']['policy']['realized'], production, diagnostic, global_policy, phase, reference=True)
    p = {'policy-enabled': 'both', 'policy-revision': 12, 'l4': {key: [
        dict(json.loads(row), rule=json.dumps(json.loads(row)['rule'])) for row in value] for key, value in rows.items()}}
    policy = {'spec': copy.deepcopy(p), 'realized': copy.deepcopy(p), 'proxy-statistics': []}
    if phase == 'l7': policy.update({'proxy-policy-revision': 12, 'proxy-statistics': [
        {'protocol': 'dns', 'location': 'egress', 'port': 53, 'allocated-proxy-port': 30000} for _ in range(2)]})
    response = [{'id': cep['status']['id'], 'status': dict(copy.deepcopy(cep['status']), policy=policy)}]
    return phase, production, diagnostic, global_policy, pair, pod, cep, agent, response, reference, copy.deepcopy(global_policy)


@pytest.mark.parametrize('phase', ['l4', 'l7'])
def test_exact_realized_dns_rows_and_owner_bind_exec(runner_snapshot, service_list, policy_data, phase):
    values = realized_context(runner_snapshot, service_list, policy_data, phase)
    observed = scope.realized(*values)
    assert observed['allocatedPort'] == (30000 if phase == 'l7' else None)
    job, pod, _, _, receipt = holder_context(runner_snapshot, service_list, policy_data, phase)
    command = launch.command(job, pod, receipt, observed, phase, NONCE, policy_data[2]['spec']['template']['spec']['containers'][0]['command'][3])
    assert command[:3] == ['python', '-B', '-c']
    assert job['spec']['activeDeadlineSeconds'] == 50
    assert job['spec']['template']['spec']['containers'][0]['env'] == launch.ENV


@pytest.mark.parametrize('change', ['uid', 'ready_only', 'other_policy', 'l4_bypass', 'wrong_dns', 'revision', 'allocated', 'global_drift'])
def test_ready_revision_alone_and_policy_bypass_never_grant_exec(runner_snapshot, service_list, policy_data, change):
    values = realized_context(runner_snapshot, service_list, policy_data, 'l7')
    policy = values[8][0]['status']['policy']
    if change == 'uid': values[2]['metadata']['uid'] = 'wrong'
    elif change == 'ready_only': policy['realized']['l4'] = {'egress': [], 'ingress': []}
    elif change == 'other_policy': policy['realized']['l4']['egress'][0]['derived-from-rules'].append(['other'])
    elif change == 'l4_bypass': policy['realized']['l4']['egress'].append(copy.deepcopy(policy['realized']['l4']['egress'][1]))
    elif change == 'wrong_dns':
        row = policy['realized']['l4']['egress'][2]; body = json.loads(row['rule']); body['l7-rules'] = [{scope.DNS_SELECTOR: None}]; row['rule'] = json.dumps(body)
    elif change == 'revision': policy['realized']['policy-revision'] += 1
    elif change == 'allocated': policy['proxy-statistics'][0]['allocated-proxy-port'] = True
    elif change == 'global_drift': values[3]['spec']['fixture'] = 'drift'
    with pytest.raises((ValueError, KeyError)): scope.realized(*values)


@pytest.mark.parametrize('change', ['owner', 'restart', 'command', 'env', 'fieldref', 'image', 'nonce', 'receipt_uid'])
def test_wrong_remote_selection_or_receipt_cannot_emit_exec(runner_snapshot, service_list, policy_data, change):
    job, pod, _, _, receipt = holder_context(runner_snapshot, service_list, policy_data)
    granted = scope.realized(*realized_context(runner_snapshot, service_list, policy_data, 'l4'))
    if change == 'owner': pod['metadata']['ownerReferences'][0]['uid'] = 'other'
    elif change == 'restart': pod['status']['containerStatuses'][0]['restartCount'] = 1
    elif change == 'command': pod['spec']['containers'][0]['command'][3] += '\n# changed'
    elif change == 'env': pod['spec']['containers'][0]['env'].append({'name': 'EXTRA', 'value': 'unsafe'})
    elif change == 'fieldref': pod['spec']['containers'][0]['env'][0]['valueFrom']['fieldRef']['fieldPath'] = 'metadata.name'
    elif change == 'image': pod['status']['containerStatuses'][0]['imageID'] = 'other'
    elif change == 'nonce': receipt['nonce'] = 'b'*32
    elif change == 'receipt_uid': receipt['podUid'] = 'replacement'
    with pytest.raises(ValueError): launch.command(job, pod, receipt, granted, 'l4', NONCE, policy_data[2]['spec']['template']['spec']['containers'][0]['command'][3])


@pytest.mark.parametrize('now,first,allowed', [(27_999_999_999, True, True), (28_000_000_000, True, False),
    (37_999_999_999, False, True), (38_000_000_000, False, False), (-1, True, False)])
def test_remote_cutoffs_refuse_late_arrival(now, first, allowed):
    if allowed: runtime.window(1, now+1, first)
    else:
        with pytest.raises(ValueError): runtime.window(1, now+1, first)


def runtime_context(monkeypatch, tmp_path):
    command = launch.holder_command(NONCE)
    digest = hashlib.sha256(('\0'.join(command)+'\0').encode()).hexdigest()
    expected = {'podUid': 'pod-uid', 'podName': 'pod-name', 'startTicks': 100, 'startNs': 1_000_000_000,
                'cmdlineSha256': digest, 'nonce': NONCE, 'holderCommand': command}
    monkeypatch.setenv(runtime.ENV_UID, expected['podUid']); monkeypatch.setenv(runtime.ENV_NAME, expected['podName'])
    monkeypatch.setattr(runtime.os, 'getuid', lambda: 10001); monkeypatch.setattr(runtime.os, 'getpid', lambda: 2)
    monkeypatch.setattr(runtime, 'own_pid1', lambda: (100, 1_000_000_000, digest))
    monkeypatch.setattr(runtime.time, 'clock_gettime_ns', lambda _: 2_000_000_000)
    original_open = os.open
    monkeypatch.setattr(runtime.os, 'open', lambda path, *args: original_open(str(tmp_path/Path(path).name), *args))
    return expected


@pytest.mark.parametrize('change', ['pod', 'missing', 'restart', 'nonce', 'late'])
def test_remote_owner_and_late_checks_happen_before_claim_and_queries(monkeypatch, tmp_path, change):
    expected = runtime_context(monkeypatch, tmp_path)
    if change == 'pod': monkeypatch.setenv(runtime.ENV_UID, 'other')
    elif change == 'missing': monkeypatch.delenv(runtime.ENV_NAME)
    elif change == 'restart': expected['startTicks'] += 1
    elif change == 'nonce': expected['nonce'] = 'b'*32
    elif change == 'late': monkeypatch.setattr(runtime.time, 'clock_gettime_ns', lambda _: 29_000_000_000)
    with pytest.raises(ValueError): runtime.run_once(expected, {})
    assert not list(tmp_path.iterdir())


def test_transport_failure_claim_is_consumed_and_cannot_be_replayed(monkeypatch, tmp_path):
    expected = runtime_context(monkeypatch, tmp_path)
    with pytest.raises(KeyError): runtime.run_once(expected, {})  # Failure after claim, before any query.
    with pytest.raises(FileExistsError): runtime.run_once(expected, {})
    assert [p.read_text() for p in tmp_path.iterdir()] == ['claimed\n']


@pytest.mark.parametrize('mode', ['four', 'late_first_send', 'late_later_send'])
def test_network_guards_preserve_four_attempts_and_abort_before_late_send(monkeypatch, tmp_path, mode):
    expected = runtime_context(monkeypatch, tmp_path)
    now = [2_000_000_000]; calls = []
    monkeypatch.setattr(runtime.time, 'clock_gettime_ns', lambda _: now[0])
    class Deadline(BaseException): pass
    class FakeSocket:
        def __init__(self, family, type): self.type = type
        def connect(self, *_):
            if mode == 'late_first_send': now[0] = 29_000_000_000
            if mode == 'late_later_send' and calls: now[0] = 39_000_000_000
        def send(self, *_): calls.append('send')
        def sendall(self, *_): calls.append('sendall')
    module = SimpleNamespace(socket=FakeSocket, SOCK_STREAM=socket.SOCK_STREAM)
    def query(name, resolver, tcp=False):
        s = module.socket(socket.AF_INET, socket.SOCK_STREAM if tcp else socket.SOCK_DGRAM)
        s.connect((resolver, 53))
        (s.sendall if tcp else s.send)(b'fixture')
        return {'status': 'reply', 'rcode': 0, 'answersPresent': True}
    def body(read, probe):
        assert callable(read)
        for name in ('chatgpt.com', 'api.m2-hosting.svc.cluster.local'):
            for tcp in (False, True): probe(name, '127.0.0.1', tcp=tcp)
        return result()
    namespace = {'RunnerDnsDeadline': Deadline, 'socket': module, 'query': query, 'runner_image_dns': body}
    if mode == 'four':
        assert runtime.run_once(expected, namespace) == result()
        assert calls == ['send', 'sendall', 'send', 'sendall']
    else:
        with pytest.raises(Deadline): runtime.run_once(expected, namespace)
        assert calls == ([] if mode == 'late_first_send' else ['send'])
    assert module.socket is FakeSocket


@pytest.mark.parametrize('mode', ['ack_failure', 'output_limit'])
def test_host_wrapper_transport_failure_never_replays_and_bounds_output(tmp_path, mode):
    tmp_path.chmod(0o700)
    program = tmp_path/'program.py'
    injected = tmp_path/'should-not-exist'
    program.write_text('literal $(touch '+str(injected)+') `touch '+str(injected)+'`\n')
    program.chmod(0o600)
    fake = tmp_path/'kubectl'
    called = tmp_path/'called'
    fake.write_text('#!/bin/bash\nprintf "%s\\0" "$@" >"'+str(called)+'"\n'+
        ('head -c 1000000 /dev/zero\n' if mode == 'output_limit' else 'printf "{\\"ack\\":false}\\n"\nexit 1\n'))
    fake.chmod(0o700)
    wrapper = Path(__file__).with_name('run_dns_contrast_exec.sh')
    prefix = tmp_path/'l4'
    argv = ['bash', str(wrapper), str(fake), prep.JOBS['l4']+'-fixture', str(program), str(prefix)]
    first = subprocess.run(argv, capture_output=True, timeout=15)
    assert first.returncode != 0 and json.loads(first.stdout)['attemptConsumed']
    recorded = called.read_bytes()
    assert b'codex-trial\0exec\0' in recorded and program.read_text().rstrip().encode() in recorded
    assert not injected.exists() and len(Path(str(prefix)+'.stdout').read_bytes()) <= 131072
    second = subprocess.run(argv, capture_output=True, timeout=2)
    assert second.returncode != 0 and called.read_bytes() == recorded
