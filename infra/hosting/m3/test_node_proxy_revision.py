import copy
import json
import os
from pathlib import Path
import subprocess

import pytest

import access_node_proxy_prepare as prep
import access_node_proxy_probe as probe
from test_access_node_proxy import bundle, capability
from test_access_runner_dns_prepare import runner_snapshot
from test_access_web_prepare import package


def actual_profile(b):
    spec = b['daemonset']['spec']['template']['spec']
    spec['serviceAccountName'] = 'cilium'
    spec['volumes'] = [{'name': 'tmp', 'emptyDir': {}}]
    init, agent = spec['initContainers'][0], spec['containers'][0]
    init.pop('args')
    for c in (init, agent):
        c['image'] = prep.CILIUM_IMAGE
        c['volumeMounts'] = [{'name': 'tmp', 'mountPath': '/tmp'}]
        c['env'] = [copy.deepcopy(prep.NODE_ENV), copy.deepcopy(prep.NAMESPACE_ENV),
                    {'name': 'KUBERNETES_SERVICE_HOST', 'value': '127.0.0.1'},
                    {'name': 'KUBERNETES_SERVICE_PORT', 'value': '6443'}]
    agent['env'] += [{'name': 'CILIUM_CLUSTERMESH_CONFIG', 'value': '/var/lib/cilium/clustermesh/'},
                     {'name': 'GOMEMLIMIT', 'valueFrom': {'resourceFieldRef': {'resource': 'limits.memory', 'divisor': '1'}}},
                     {'name': 'KUBE_CLIENT_BACKOFF_BASE', 'value': '1'}, {'name': 'KUBE_CLIENT_BACKOFF_DURATION', 'value': '120'}]
    pod = b['agents']['items'][0]
    pod['spec'] = copy.deepcopy(spec) | {'nodeName': b['expectedNode']['name']}
    name = 'kube-api-access-abc12'
    pod['spec']['volumes'].append({'name': name, 'projected': {'defaultMode': 420, 'sources': [
        {'serviceAccountToken': {'expirationSeconds': 3607, 'path': 'token'}},
        {'configMap': {'name': 'kube-root-ca.crt', 'items': [{'key': 'ca.crt', 'path': 'ca.crt'}]}},
        {'downwardAPI': {'items': [{'path': 'namespace', 'fieldRef': {'apiVersion': 'v1', 'fieldPath': 'metadata.namespace'}}]}}]}})
    for c in pod['spec']['containers']+pod['spec']['initContainers']:
        c['volumeMounts'].append({'name': name, 'readOnly': True, 'mountPath': '/var/run/secrets/kubernetes.io/serviceaccount'})
    # Raw typed API lists omit nested TypeMeta. Preserve this server shape.
    for field, kind in (('nodes', 'NodeList'), ('agents', 'PodList')):
        b[field].update(kind=kind, apiVersion='v1')
        for obj in b[field]['items']: obj.pop('kind')
    return b


def test_source_backed_default_profile_and_standard_admission_without_raw_rewrite(bundle):
    b = actual_profile(bundle)
    before = copy.deepcopy(b)
    assert prep.select_initial(b)['uid'] == b['agents']['items'][0]['metadata']['uid']
    assert prep.preflight(b)['defaultRollback'] is True
    assert b == before


@pytest.mark.parametrize('change', ['missing_namespace', 'wrong_namespace', 'unknown_image', 'override_source',
    'filter', 'node_override', 'default_dest_override', 'unknown_agent_env', 'extra_mount', 'writable_token',
    'wrong_audience', 'host_volume', 'different_container', 'different_service_account', 'conflicting_type', 'clean_state'])
def test_actual_profile_allows_only_proven_defaults_and_admission(bundle, change):
    b = actual_profile(bundle); spec = b['daemonset']['spec']['template']['spec']
    init, agent = spec['initContainers'][0], spec['containers'][0]
    pod = b['agents']['items'][0]
    if change == 'missing_namespace': init['env'] = [e for e in init['env'] if e['name'] != 'CILIUM_K8S_NAMESPACE']
    elif change == 'wrong_namespace': init['env'][1] = {'name': 'CILIUM_K8S_NAMESPACE', 'value': 'other'}
    elif change == 'unknown_image': init['image'] = 'unknown'
    elif change == 'override_source': init['env'].append({'name': 'CILIUM_SOURCE', 'value': 'node:'})
    elif change == 'filter': init['env'].append({'name': 'CILIUM_ALLOW_CONFIG_KEYS', 'value': 'other'})
    elif change == 'node_override': init['env'].append({'name': 'CILIUM_NODE_NAME', 'value': 'other'})
    elif change == 'default_dest_override': init['env'].append({'name': 'CILIUM_DEST', 'value': '/other'})
    elif change == 'unknown_agent_env': agent['env'].append({'name': 'CILIUM_ENABLE_BPF_TPROXY', 'value': 'false'})
    elif change == 'extra_mount': pod['spec']['containers'][0]['volumeMounts'].append({'name': 'foreign', 'mountPath': '/foreign'})
    elif change == 'writable_token': pod['spec']['containers'][0]['volumeMounts'][-1]['readOnly'] = False
    elif change == 'wrong_audience': pod['spec']['volumes'][-1]['projected']['sources'][0]['serviceAccountToken']['audience'] = 'foreign'
    elif change == 'host_volume': pod['spec']['volumes'][0] = {'name': 'tmp', 'hostPath': {'path': '/tmp'}}
    elif change == 'different_container': pod['spec']['containers'][0]['args'].append('--enable-bpf-tproxy=false')
    elif change == 'different_service_account': pod['spec']['serviceAccountName'] = 'foreign'
    elif change == 'conflicting_type': b['nodes']['items'][0]['kind'] = 'Pod'
    elif change == 'clean_state': b['configMap']['data']['clean-cilium-state'] = 'true'
    with pytest.raises(ValueError): prep.preflight(b)


@pytest.mark.parametrize('change', ['missing', 'selfcheck', 'absent_unknown', 'incomplete', 'timeout', 'foreign_id', 'foreign_name', 'foreign_image', 'not_eligible'])
def test_bare_or_uncertain_kernel_receipt_cannot_advance_to_p2(bundle, change):
    r = prep.preflight(bundle); c = capability(r); life = c['lifecycle']
    if change == 'missing': c.pop('lifecycle')
    elif change == 'selfcheck': life['mode'] = 'selfcheck'
    elif change == 'absent_unknown': life['containerAbsentVerified'] = False
    elif change == 'incomplete': life['probeCompleted'] = False
    elif change == 'timeout': life['childExit'] = 124
    elif change == 'foreign_id': life['containerId'] = 'bad'
    elif change == 'foreign_name': life['containerName'] = 'foreign'
    elif change == 'foreign_image': life['imageId'] = 'foreign'
    else: life['p2Eligible'] = False
    with pytest.raises(ValueError): prep.emit(r, c)


DOCKER_MOCK = r'''#!/usr/local/bin/python
import json,os,sys
from pathlib import Path
root=Path(__file__).parent
args=sys.argv[1:]
with (root/'calls').open('a') as f:f.write(json.dumps(args)+'\n')
scenario=os.environ['MOCK_SCENARIO']
path=root/'state'
s=json.loads(path.read_text()) if path.exists() else {'exists':False}
def save():path.write_text(json.dumps(s))
def option(k):return args[args.index(k)+1]
if args[0]=='create':
 s.update(exists=True,id='d'*64,name=option('--name'),nonce=option('--label').split('=',1)[1],running=False,exit=0)
 if scenario=='create_late':s['exists']=False;save();sys.exit(124)
 save()
 if scenario=='create_lost':sys.exit(1)
 Path(option('--cidfile')).write_text(s['id'])
 print(s['id']);sys.exit(0)
if args[0]=='inspect':
 if not s['exists'] or scenario=='daemon_failed' and s.get('started'):sys.exit(1)
 owner='foreign' if scenario=='foreign' else s['nonce']
 print('|'.join([s['id'],'/'+s['name'],owner,os.environ['MOCK_IMAGE'],str(s['running']).lower(),str(s['exit'])]));sys.exit(0)
if args[0]=='start':
 s['started']=True;s['running']=scenario not in ('normal',);s['exit']=0;save()
 print('{}')
 sys.exit(0 if scenario=='normal' else 137 if scenario=='transport' else 124)
if args[0]=='kill':
 if scenario=='kill_failed':sys.exit(1)
 s['running']=False;s['exit']=137;save();sys.exit(0)
if args[0]=='rm':
 if scenario=='remove_failed':sys.exit(1)
 s['exists']=False;save();sys.exit(0)
if args[:2]==['container','ls']:
 if scenario=='daemon_failed' and s.get('started') or scenario=='absence_failed' and not s['exists'] and s.get('started'):sys.exit(1)
 if s['exists']:print(s['id'])
 sys.exit(0)
sys.exit(1)
'''


@pytest.mark.parametrize('scenario,known,eligible', [('normal', True, True), ('timeout', True, False),
    ('transport', True, False), ('create_lost', True, False), ('create_late', False, False),
    ('foreign', False, False), ('kill_failed', False, False), ('remove_failed', False, False),
    ('daemon_failed', False, False), ('absence_failed', False, False)])
def test_owned_docker_lifecycle_failure_cleanup_and_handoff(tmp_path, scenario, known, eligible):
    tool = tmp_path/'docker'; tool.write_text(DOCKER_MOCK); tool.chmod(0o700)
    private = tmp_path/'evidence'; private.mkdir(mode=0o700)
    (private/'binding.json').write_text('{}'); (private/'binding.json').chmod(0o600)
    source = Path(__file__).parent
    argv = ['bash', str(source/'run_node_proxy_probe.sh'), str(tool), str(source), str(private), 'probe']
    env = dict(os.environ, MOCK_SCENARIO=scenario, MOCK_IMAGE=probe.PROBE_TOOL_IMAGE)
    run = subprocess.run(argv, capture_output=True, env=env, timeout=10)
    lifecycle = json.loads((private/'probe.lifecycle.json').read_text())
    assert lifecycle['containerAbsentVerified'] is known
    assert lifecycle['p2Eligible'] is eligible
    calls = [json.loads(line) for line in (tmp_path/'calls').read_text().splitlines()]
    mutations = [a for a in calls if a[0] in ('kill', 'rm')]
    assert all(a[-1] == 'd'*64 for a in mutations)
    if scenario == 'foreign': assert mutations == []
    if known: assert not json.loads((tmp_path/'state').read_text())['exists']
    else: assert run.returncode != 0
    count = len(calls)
    assert subprocess.run(argv, capture_output=True, env=env).returncode != 0
    assert len((tmp_path/'calls').read_text().splitlines()) == count
