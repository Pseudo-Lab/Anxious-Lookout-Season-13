import copy
import json
from pathlib import Path
import subprocess

import pytest

from access_endpoint_pin import pin
from access_proxy_listener_scope import scope, finalize
from access_diagnose_prepare import emit, DIAGNOSTIC_IMAGE
from test_access_endpoint_pin import metadata
from test_access_web_prepare import package


def context():
    pod, cep, agent = metadata()
    name = 'access-dns-proxy-listener-diagnose-v1-fixture'
    pod['metadata']['name'] = cep['metadata']['name'] = name
    cep['metadata']['ownerReferences'][0]['name'] = name
    agent['spec']['hostNetwork'] = True
    policy = {'desired': {'policy-revision': 12}, 'realized': {'policy-revision': 12}, 'proxy-policy-revision': 12,
              'proxy-statistics': [{'protocol': 'dns', 'location': 'egress', 'port': 53, 'allocated-proxy-port': 35555} for _ in range(2)]}
    saved = pin(pod, cep, agent)
    endpoint = [{'id': saved['endpointId'], 'status': dict(copy.deepcopy(cep['status']), policy=policy)}]
    return saved, pod, cep, agent, endpoint


def result():
    return dict(agentProcessMatched=True, dnsCause='unknown', **{v: {'selectedBoundSockets': 1, 'ownedByAgent': 1, 'wildcardBinds': 1}
                for v in ('tcp4', 'udp4', 'tcp6', 'udp6')})


def process_proof():
    child = {'pid': 123, 'parentPid': 122, 'group': 122, 'startTicks': 100}
    return child, dict(child, childGone=True, groupGone=True)


def test_only_single_fresh_allocated_port_is_bound():
    values = context(); saved = scope(*values)
    assert saved['allocatedPort'] == 35555 and saved['policyRevision'] == 12
    final = finalize(saved, *values[1:], result(), 0, *process_proof())
    assert final['selectedPortContextMatches'] and final['trafficConclusion'] == 'unknown'
    assert '35555' not in json.dumps(final)


@pytest.mark.parametrize('change', ['port', 'boolean', 'revision', 'host_network', 'shared_pid', 'host_pid', 'pod_uid'])
def test_unsupported_or_drifted_scope_is_refused(change):
    values = context()
    if change == 'port': values[4][0]['status']['policy']['proxy-statistics'][1]['allocated-proxy-port'] += 1
    if change == 'boolean': values[4][0]['status']['policy']['proxy-statistics'][0]['allocated-proxy-port'] = True
    if change == 'revision': values[4][0]['status']['policy']['proxy-policy-revision'] += 1
    if change == 'host_network': values[3]['spec']['hostNetwork'] = False
    if change == 'shared_pid': values[3]['spec']['shareProcessNamespace'] = True
    if change == 'host_pid': values[3]['spec']['hostPID'] = True
    if change == 'pod_uid': values[1]['metadata']['uid'] = 'replacement'
    with pytest.raises(ValueError): scope(*values)


def test_post_allocated_port_and_unsupported_result_are_refused():
    values = context(); saved = scope(*values)
    changed = copy.deepcopy(values)
    for stat in changed[4][0]['status']['policy']['proxy-statistics']: stat['allocated-proxy-port'] += 1
    with pytest.raises(ValueError): finalize(saved, *changed[1:], result(), 0, *process_proof())
    malformed = result(); malformed['tcp4']['ownedByAgent'] = 2
    with pytest.raises(ValueError): finalize(saved, *values[1:], malformed, 0, *process_proof())
    with pytest.raises(ValueError): finalize(saved, *values[1:], result(), 1, *process_proof())
    child, proof = process_proof()
    with pytest.raises(ValueError): finalize(saved, *values[1:], result(), 0, child, dict(proof, startTicks=101))


def fixture_proc(tmp_path, *, wrong_agent=False, missing=False, overflow=False):
    (tmp_path/'1/fd').mkdir(parents=True); (tmp_path/'net').mkdir()
    (tmp_path/'1/comm').write_text('other-process\n' if wrong_agent else 'cilium-agent\n')
    (tmp_path/'1/fd/3').symlink_to('socket:[111]'); (tmp_path/'1/fd/4').symlink_to('/PRIVATE_FILE_CONTENT_DO_NOT_READ')
    for name in ('tcp', 'udp', 'tcp6', 'udp6'):
        header = 'sl local_address rem_address st tx_queue tr retrnsmt uid timeout inode\n'
        address = '0'*32 if name.endswith('6') else '0'*8
        state = '0A' if name.startswith('tcp') else '07'
        selected = f'0: {address}:8AE3 {address}:0000 {state} 0:0 0:0 0 0 0 111\n'
        foreign = f'1: {address}:1234 {address}:5678 01 0:0 0:0 0 0 0 222\n'
        (tmp_path/'net'/name).write_text(header+selected+(foreign*4097 if overflow else foreign))
    if missing: (tmp_path/'net/udp6').unlink()
    source = Path(__file__).with_name('access_proxy_listener.sh').read_text().replace('/proc/', str(tmp_path)+'/')
    source = source.replace(str(tmp_path)+'/$$/stat', '/proc/$$/stat')
    script = tmp_path/'inspect.sh'; script.write_text(source)
    return script


def test_fixed_paths_return_only_selected_socket_counts(tmp_path):
    script = fixture_proc(tmp_path)
    process = subprocess.run(['timeout', '--signal=TERM', '--kill-after=1s', '2s', 'sh', str(script), '35555'], capture_output=True, timeout=3)
    assert process.returncode == 0
    value = json.loads(process.stdout)
    assert value['tcp4'] == value['udp6'] == {'selectedBoundSockets': 1, 'ownedByAgent': 1, 'wildcardBinds': 1}
    assert all(v not in process.stdout.decode() for v in ('8AE3', '35555', '111', 'PRIVATE_FILE', 'socket:', 'local_address'))
    child = json.loads(process.stderr)['listenerChild']
    checker = str(Path(__file__).with_name('access_drop_remote.sh'))
    proof = subprocess.run(['sh', checker, 'check', *[str(child[k]) for k in ('pid', 'parentPid', 'group', 'startTicks')]], capture_output=True, timeout=2)
    assert proof.returncode == 0 and json.loads(proof.stdout)['groupGone'] is True


@pytest.mark.parametrize('variant', ['agent', 'missing', 'overflow'])
def test_namespace_or_read_limits_are_unknown_not_absence(tmp_path, variant):
    script = fixture_proc(tmp_path, wrong_agent=variant == 'agent', missing=variant == 'missing', overflow=variant == 'overflow')
    process = subprocess.run(['timeout', '--signal=TERM', '--kill-after=1s', '2s', 'sh', str(script), '35555'], capture_output=True, timeout=3)
    assert process.returncode == 1 and json.loads(process.stdout) == {'listenerInspection': 'unknown'}


def test_holder_does_not_repeat_queries_or_subscribe(package, monkeypatch, capsys):
    import socket
    import time
    snapshot, inputs, _ = package
    snapshot['runner'] = {'metadata': {'namespace': 'codex-trial', 'name': 'access-web-runner-v1'}, 'spec': {'replicas': 0}}
    snapshot['runnerPodCount'] = 0
    job = emit(snapshot, inputs, DIAGNOSTIC_IMAGE, 'dns-proxy-listener')
    spec = job['spec']['template']['spec']; c = spec['containers'][0]
    assert job['spec']['activeDeadlineSeconds'] == 50 and c['env'] == c['volumeMounts'] == spec['volumes'] == []
    assert 'time.sleep(40)' in c['command'][3] and not spec['automountServiceAccountToken']
    assert all(v not in c['command'][3] for v in ('socket', 'query(', 'libc', 'monitor', '/proc', '/etc/resolv'))
    durations = []
    monkeypatch.setattr(time, 'sleep', durations.append)
    def no_socket(*_, **__): raise AssertionError('holder must not open sockets')
    monkeypatch.setattr(socket, 'socket', no_socket)
    exec(c['command'][3], {'__name__': '__main__'})
    rows = [json.loads(v) for v in capsys.readouterr().out.splitlines()]
    assert durations == [40] and all(v['networkRequested'] is False for v in rows)


def test_bound_socket_without_agent_ownership_is_not_called_owned(tmp_path):
    script = fixture_proc(tmp_path)
    (tmp_path/'1/fd/3').unlink()
    (tmp_path/'1/fd/3').symlink_to('socket:[999]')
    process = subprocess.run(['timeout', '--signal=TERM', '--kill-after=1s', '2s', 'sh', str(script), '35555'], capture_output=True, timeout=3)
    assert process.returncode == 0
    value = json.loads(process.stdout)
    assert value['tcp4']['selectedBoundSockets'] == 1 and value['tcp4']['ownedByAgent'] == 0


def test_noncanonical_port_is_refused_before_proc_inspection(tmp_path):
    script = fixture_proc(tmp_path)
    process = subprocess.run(['sh', str(script), '035555'], capture_output=True, timeout=3)
    assert process.returncode == 1 and json.loads(process.stdout) == {'listenerInspection': 'unknown'}
