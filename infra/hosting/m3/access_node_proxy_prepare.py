"""Offline node proxy trial guards. Never opens Kubernetes or executes commands."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re

import access_runner_dns_prepare as direct
import access_dns_contrast_exec as launch
import access_web_prepare as web
from access_dns_contrast_prepare import JOBS
from access_dns_contrast_scope import endpoint_one, rows
from access_endpoint_pin import pin, verify
from access_runner_dns_control_prepare import canonical, JOB_NAME_LABELS, JOB_UID_LABELS
from access_node_proxy_probe import instructions, PROBE_TOOL_IMAGE

CONFIG = 'access-dns-bpf-tproxy-trial-v1'
FLAG = 'enable-bpf-tproxy'
KEYS = (FLAG, 'enable-ipv4', 'enable-ipv6', 'install-iptables-rules', 'enable-endpoint-routes')
ENDPOINT_WITNESS_FORMAT = 'cilium-1.20.2-stable-policy-v2'
CILIUM_IMAGE = 'quay.io/cilium/cilium:v1.20.2@sha256:2939231d0d3e3ebddcd80fffa168b7ddcc78fdf0dc864d1c8c126ff523c54f01'
NODE_ENV = {'name': 'K8S_NODE_NAME', 'valueFrom': {'fieldRef': {'apiVersion': 'v1', 'fieldPath': 'spec.nodeName'}}}
NAMESPACE_ENV = {'name': 'CILIUM_K8S_NAMESPACE', 'valueFrom': {'fieldRef': {'apiVersion': 'v1', 'fieldPath': 'metadata.namespace'}}}


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def policy_witness(values):
    expected = {('CiliumNetworkPolicy', 'codex-trial', 'access-web-boundary-v1'),
                ('CiliumNetworkPolicy', 'm2-hosting', 'hosting-access-web-link-v1'),
                ('CiliumClusterwideNetworkPolicy', None, 'anxious-lookout-node-protection')}
    found = []
    for obj in values:
        m = obj['metadata']
        key = (obj['kind'], m.get('namespace'), m['name'])
        if key not in expected or not m.get('uid') or m.get('deletionTimestamp'):
            raise ValueError()
        found.append({'kind': obj['kind'], 'namespace': m.get('namespace'), 'name': m['name'],
                      'uid': m['uid'], 'spec': obj['spec']})
    if len(found) != 3 or len({(v['kind'], v['namespace'], v['name']) for v in found}) != 3:
        raise ValueError()
    return sorted(found, key=lambda v: v['name'])


def ident(obj, kind, namespace=None):
    m = obj['metadata']
    if (obj.get('kind') != kind or (namespace is not None and m.get('namespace') != namespace)
            or not m.get('name') or not m.get('uid') or not m.get('resourceVersion') or m.get('deletionTimestamp')):
        raise ValueError()
    return {key: m[key] for key in ('name', 'uid', 'resourceVersion')}


def items(value):
    if (not value['metadata'].get('resourceVersion') or value['metadata'].get('continue', '')
            or value['metadata'].get('remainingItemCount', 0) or not isinstance(value['items'], list)):
        raise ValueError()
    typed = {'NodeList': ('Node', 'v1'), 'PodList': ('Pod', 'v1'),
             'CiliumNodeConfigList': ('CiliumNodeConfig', 'cilium.io/v2')}.get(value.get('kind'))
    if not typed:
        return value['items']
    kind, version = typed
    if value.get('apiVersion') != version:
        raise ValueError()
    result = []
    for item in value['items']:
        # Typed API list already declares item types. Interpret omitted nested
        # TypeMeta in copies only; never accept conflicting explicit TypeMeta.
        if item.get('kind', kind) != kind or item.get('apiVersion', version) != version:
            raise ValueError()
        result.append(copy.deepcopy(item) | {'kind': kind, 'apiVersion': version})
    return result


def selector_matches(selector, labels):
    if selector is None:
        return False  # Cilium ignores nil selectors; {} selects all nodes.
    if set(selector) - {'matchLabels', 'matchExpressions'}:
        raise ValueError()
    if any(labels.get(k) != v for k, v in selector.get('matchLabels', {}).items()):
        return False
    for expression in selector.get('matchExpressions', []):
        k, op = expression['key'], expression['operator']
        vs = expression.get('values', [])
        if op == 'In': ok = k in labels and labels[k] in vs
        elif op == 'NotIn': ok = k not in labels or labels[k] not in vs
        elif op == 'Exists': ok = k in labels
        elif op == 'DoesNotExist': ok = k not in labels
        else: raise ValueError()
        if not ok: return False
    return True


def ready(pod):
    if (pod['status'].get('phase') != 'Running'
            or not any(c.get('type') == 'Ready' and c.get('status') == 'True'
                       for c in pod['status'].get('conditions', []))):
        raise ValueError()
    c = [v for v in pod['status']['containerStatuses'] if v['name'] == 'cilium-agent']
    if len(c) != 1 or c[0].get('ready') is not True or not c[0].get('state', {}).get('running'):
        raise ValueError()


def agent_signature(pod):
    c = [c for c in pod['status']['containerStatuses'] if c['name'] == 'cilium-agent']
    if len(c) != 1 or not c[0].get('containerID') or type(c[0].get('restartCount')) is not int:
        raise ValueError()
    return {k: c[0][k] for k in ('containerID', 'restartCount', 'imageID')} | {'startedAt': c[0]['state']['running']['startedAt']}


def application_witness(snapshot):
    api, runner, config = snapshot['api'], snapshot['runner'], snapshot['config']
    ident(api, 'Deployment', 'm2-hosting')
    ident(runner, 'Deployment', 'codex-trial')
    ident(config, 'ConfigMap', 'm2-hosting')
    generation = api['metadata']['generation']
    status = api['status']
    if (type(generation) is not int or generation <= 0 or type(status.get('observedGeneration')) is not int
            or status['observedGeneration'] < generation
            or any(type(status.get(k)) is not int or status[k] != 1
                   for k in ('replicas', 'readyReplicas', 'updatedReplicas', 'availableReplicas'))):
        raise ValueError()
    return digest({'api': {'uid': api['metadata']['uid'], 'spec': api['spec']},
                   'runner': {'uid': runner['metadata']['uid'], 'spec': runner['spec']},
                   'config': {'uid': config['metadata']['uid'], 'data': config['data']}})


def selected_agent(pods, node, daemonset):
    ds = ident(daemonset, 'DaemonSet', 'kube-system')
    candidates = [p for p in items(pods) if p['spec'].get('nodeName') == node['name']
                  and p['metadata'].get('labels', {}).get('k8s-app') == 'cilium']
    if len(candidates) != 1:
        raise ValueError()
    pod = candidates[0]
    ident(pod, 'Pod', 'kube-system')
    owners = pod['metadata'].get('ownerReferences', [])
    if (len(owners) != 1 or owners[0].get('kind') != 'DaemonSet' or owners[0].get('name') != ds['name']
            or owners[0].get('uid') != ds['uid'] or owners[0].get('controller') is not True
            or pod['spec'].get('hostNetwork') is not True):
        raise ValueError()
    compare_containers(pod, daemonset)
    return pod


def compare_containers(pod, daemonset):
    expected = daemonset['spec']['template']['spec']
    actual = pod['spec']
    if actual.get('serviceAccountName', 'default') != expected.get('serviceAccountName', 'default'):
        raise ValueError()
    token_volumes = [v for v in actual.get('volumes', []) if v['name'].startswith('kube-api-access-')]
    token_name = None
    if token_volumes:
        if (len(token_volumes) != 1 or expected.get('serviceAccountName') != 'cilium'
                or actual.get('serviceAccountName') != 'cilium'
                or expected.get('automountServiceAccountToken', True) is not True
                or actual.get('automountServiceAccountToken', True) is not True
                or any(v['name'].startswith('kube-api-access-') for v in expected.get('volumes', []))):
            raise ValueError()
        token_name = token_volumes[0]['name']
        if not re.fullmatch('kube-api-access-[a-z0-9]{5}', token_name):
            raise ValueError()
        projected = {'name': token_name, 'projected': {'defaultMode': 420, 'sources': [
            {'serviceAccountToken': {'expirationSeconds': 3607, 'path': 'token'}},
            {'configMap': {'name': 'kube-root-ca.crt', 'items': [{'key': 'ca.crt', 'path': 'ca.crt'}]}},
            {'downwardAPI': {'items': [{'path': 'namespace', 'fieldRef': {'apiVersion': 'v1', 'fieldPath': 'metadata.namespace'}}]}}]}}
        if token_volumes[0] != projected:
            raise ValueError()
    if [v for v in actual.get('volumes', []) if v['name'] != token_name] != expected.get('volumes', []):
        raise ValueError()
    # Compare copies only; raw DS/Pod stay unchanged. The only permitted delta
    # is the exact standard admission mount backed by the checked projection.
    for key in ('containers', 'initContainers'):
        wanted = expected.get(key, [])
        got = copy.deepcopy(actual.get(key, []))
        if len(wanted) != len(got):
            raise ValueError()
        for original, current in zip(wanted, got):
            if token_name:
                mount = {'name': token_name, 'readOnly': True, 'mountPath': '/var/run/secrets/kubernetes.io/serviceaccount'}
                if current.get('volumeMounts') != original.get('volumeMounts', [])+[mount]:
                    raise ValueError()
                current['volumeMounts'] = copy.deepcopy(original.get('volumeMounts', []))
                if 'volumeMounts' not in original: current.pop('volumeMounts')
            if current != original:
                raise ValueError()


def startup(ds):
    spec = ds['spec']['template']['spec']
    init = [c for c in spec.get('initContainers', []) if c['name'] == 'config']
    agent = [c for c in spec['containers'] if c['name'] == 'cilium-agent']
    if len(init) != 1 or len(agent) != 1:
        raise ValueError()
    i, a = init[0], agent[0]
    if i.get('command') != ['cilium-dbg', 'build-config'] or a.get('command') != ['cilium-agent']:
        raise ValueError()
    if i.get('image') != CILIUM_IMAGE or a.get('image') != CILIUM_IMAGE:
        raise ValueError()
    # One intentionally narrow startup profile; unknown flags/env/shell stop.
    allowed = ('--source=config-map:kube-system/cilium-config,cilium-node-config:kube-system',
               '--dest=/tmp/cilium/config-map')
    args = i.get('args', [])
    node_env = [e for e in i.get('env', []) if e.get('name') == 'K8S_NODE_NAME']
    if node_env != [NODE_ENV]:
        raise ValueError()
    defaults_profile = args == []
    if defaults_profile:
        # Fixed image/source: default dest+source order, empty override filters.
        if (i['image'] != CILIUM_IMAGE or a['image'] != CILIUM_IMAGE
                or [e for e in i.get('env', []) if e.get('name') == 'CILIUM_K8S_NAMESPACE'] != [NAMESPACE_ENV]):
            raise ValueError()
    elif sorted(args) != sorted(allowed):
        raise ValueError()
    for c in (i, a):
        if c.get('envFrom'):
            raise ValueError()
        envs = c.get('env', [])
        if len({e['name'] for e in envs}) != len(envs):
            raise ValueError()
        auxiliary = {'KUBERNETES_SERVICE_HOST': {'name': 'KUBERNETES_SERVICE_HOST', 'value': '127.0.0.1'},
                     'KUBERNETES_SERVICE_PORT': {'name': 'KUBERNETES_SERVICE_PORT', 'value': '6443'}}
        if c is a:
            auxiliary.update({
                'CILIUM_CLUSTERMESH_CONFIG': {'name': 'CILIUM_CLUSTERMESH_CONFIG', 'value': '/var/lib/cilium/clustermesh/'},
                'GOMEMLIMIT': {'name': 'GOMEMLIMIT', 'valueFrom': {'resourceFieldRef': {'resource': 'limits.memory', 'divisor': '1'}}},
                'KUBE_CLIENT_BACKOFF_BASE': {'name': 'KUBE_CLIENT_BACKOFF_BASE', 'value': '1'},
                'KUBE_CLIENT_BACKOFF_DURATION': {'name': 'KUBE_CLIENT_BACKOFF_DURATION', 'value': '120'}})
        for env in envs:
            name = env['name']
            if name in auxiliary:
                if not defaults_profile or env != auxiliary[name]: raise ValueError()
                continue
            if name not in ('K8S_NODE_NAME', 'CILIUM_K8S_NAMESPACE'):
                raise ValueError()
            if name == 'CILIUM_K8S_NAMESPACE' and env not in (NAMESPACE_ENV, {'name': name, 'value': 'kube-system'}):
                raise ValueError()
            if name == 'K8S_NODE_NAME' and env != NODE_ENV:
                raise ValueError()
    if a.get('args') != ['--config-dir=/tmp/cilium/config-map']:
        raise ValueError()
    mount_path = '/tmp' if defaults_profile else '/tmp/cilium/config-map'
    mounts = [[m for m in c.get('volumeMounts', []) if m.get('mountPath') == mount_path] for c in (i, a)]
    if any(len(m) != 1 for m in mounts) or mounts[0][0]['name'] != mounts[1][0]['name']:
        raise ValueError()
    if any(set(m[0]) != {'name', 'mountPath'} for m in mounts):
        raise ValueError()
    if any(m['mountPath'].startswith(mount_path+'/') for c in (i, a) for m in c.get('volumeMounts', [])):
        raise ValueError()
    volume = [v for v in spec['volumes'] if v['name'] == mounts[0][0]['name']]
    if len(volume) != 1 or set(volume[0]) != {'name', 'emptyDir'}:
        raise ValueError()


def settings(values, flag):
    if set(values) != set(KEYS) or values[FLAG] != flag:
        raise ValueError()
    if values['enable-ipv4'] != 'true' or values['install-iptables-rules'] != 'true' or values['enable-endpoint-routes'] != 'false':
        raise ValueError()
    if values['enable-ipv6'] not in ('true', 'false'):
        raise ValueError()


def select_initial(b):
    expected = b['expectedNode']
    selected = [n for n in items(b['nodes']) if n['metadata'].get('labels', {}).get('kubernetes.io/hostname') == expected['hostname']]
    if len(selected) != 1:
        raise ValueError()
    ref = ident(selected[0], 'Node')
    if ref['name'] != expected['name'] or ref['uid'] != expected['uid']:
        raise ValueError()
    startup(b['daemonset'])
    pod = selected_agent(b['agents'], ref, b['daemonset'])
    ready(pod)
    return ident(pod, 'Pod', 'kube-system')


def policy_snapshot(policy):
    value = copy.deepcopy(policy)
    revision, build = value['policy-revision'], value.get('build')
    # Fixed GetPolicyModel populates Build and PolicyRevision from the same
    # agent-local revision. Validate the alias before cross-agent comparison.
    if type(revision) is not int or revision <= 0 or type(build) is not int or build != revision:
        raise ValueError()
    del value['policy-revision']
    del value['build']
    l4 = value['l4']
    if not isinstance(l4, dict):
        raise ValueError()
    # Cilium L4Policy.GetModel appends filters from Go maps, without row order.
    # Sort only the outer ingress/egress rows. Preserve every field, inner
    # array order, rule JSON string and duplicate multiplicity exactly.
    for direction in ('ingress', 'egress'):
        entries = l4[direction]
        if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
            raise ValueError()
        l4[direction] = sorted(entries, key=canonical)
    return value


def endpoint_witnesses(values, agent_uid):
    if set(values) != {'api', 'dns'}:
        raise ValueError()
    out = {}
    for alias, v in values.items():
        if (v['agentUid'] != agent_uid or not v['podUid'] or not v['cepUid']
                or not v['attachment'] or v['state'] != 'ready'):
            raise ValueError()
        policy = v['policy']
        revisions = [policy[k]['policy-revision'] for k in ('spec', 'realized')]
        if any(type(r) is not int or r <= 0 for r in revisions) or len(set(revisions)) != 1:
            raise ValueError()
        realized = policy_snapshot(policy['realized'])
        wanted = policy_snapshot(policy['spec'])
        if canonical(realized) != canonical(wanted):
            raise ValueError()
        out[alias] = {k: v[k] for k in ('podUid', 'cepUid', 'attachment')}
        out[alias]['realizedHash'] = digest(realized)
    return out


def preflight(b):
    expected = b['expectedNode']
    ns = items(b['nodes'])
    selected = [n for n in ns if n['metadata'].get('labels', {}).get('kubernetes.io/hostname') == expected['hostname']]
    if len(selected) != 1:
        raise ValueError()
    node = selected[0]
    ref = ident(node, 'Node')
    if ref['name'] != expected['name'] or ref['uid'] != expected['uid']:
        raise ValueError()
    if not any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in node['status']['conditions']):
        raise ValueError()
    info = node['status']['nodeInfo']
    if not info.get('machineID') or not info.get('bootID') or not info.get('kernelVersion'):
        raise ValueError()
    if sum(n['status']['nodeInfo'].get('machineID') == info['machineID'] for n in ns) != 1:
        raise ValueError()
    crd = b['crd']
    ident(crd, 'CustomResourceDefinition')
    if (crd['metadata']['name'] != 'ciliumnodeconfigs.cilium.io' or crd['spec']['group'] != 'cilium.io'
            or crd['spec']['scope'] != 'Namespaced'
            or crd['spec']['names']['kind'] != 'CiliumNodeConfig'
            or not any(v['name'] == 'v2' and v['served'] is True for v in crd['spec']['versions'])
            or not any(c['type'] == 'Established' and c['status'] == 'True' for c in crd['status']['conditions'])):
        raise ValueError()
    version = [v for v in crd['spec']['versions'] if v['name'] == 'v2']
    if len(version) != 1:
        raise ValueError()
    properties = version[0]['schema']['openAPIV3Schema']['properties']['spec']['properties']
    if properties['defaults']['additionalProperties']['type'] != 'string' or properties['nodeSelector']['type'] != 'object':
        raise ValueError()
    for config in items(b['nodeConfigs']):
        if (config['metadata']['name'] == CONFIG or
                selector_matches(config['spec'].get('nodeSelector'), node['metadata']['labels'])):
            raise ValueError()
    ds = b['daemonset']
    startup(ds)
    pod = selected_agent(b['agents'], ref, ds)
    ready(pod)
    image_id = next(c['imageID'] for c in pod['status']['containerStatuses'] if c['name'] == 'cilium-agent')
    if '@sha256:' not in image_id:
        raise ValueError()
    cm = b['configMap']
    ident(cm, 'ConfigMap', 'kube-system')
    if cm['metadata']['name'] != 'cilium-config':
        raise ValueError()
    if any(cm.get('data', {}).get(k, 'false') not in ('false', '') for k in ('clean-cilium-state', 'clean-cilium-bpf-state')):
        raise ValueError()
    if b['version'] != '1.20.2' or b['independentApiServer'] not in ('https://127.0.0.1:6443', 'https://[::1]:6443'):
        raise ValueError()
    if b['independentApiGetPassed'] is not True:
        raise ValueError()
    settings(b['settings'], 'false')
    # The existing restored API/Runner/storage guard is mandatory, not a boolean assertion.
    baseline = direct.emit(b['snapshot'], b['inputs'], b['images'])
    if canonical(baseline) != canonical(b['baseline']):
        raise ValueError()
    web.configure(b['images'])
    expected_policy = web.emit(b['snapshot'], b['inputs'], 'policy')['items']
    for expected_policy_obj in expected_policy:
        current = [p for p in b['policies'] if p['metadata']['name'] == expected_policy_obj['metadata']['name']]
        if len(current) != 1 or canonical(current[0]['spec']) != canonical(expected_policy_obj['spec']):
            raise ValueError()
    if baseline['spec']['template']['spec']['nodeSelector'] != {'kubernetes.io/hostname': expected['hostname']}:
        raise ValueError()
    endpoints = endpoint_witnesses(b['endpoints'], pod['metadata']['uid'])
    application = application_witness(b['snapshot'])
    return {'node': ref | {'hostname': expected['hostname']}, 'nodeCount': len(ns),
            'binding': {'nodeUid': ref['uid'], 'kernel': info['kernelVersion'], 'bootId': info['bootID'],
                        'machineSha256': hashlib.sha256(info['machineID'].encode()).hexdigest()},
            'daemonsetUid': ds['metadata']['uid'], 'daemonsetSpecHash': digest(ds['spec']),
            'initialAgent': ident(pod, 'Pod', 'kube-system'), 'agentImageId': image_id, 'settings': b['settings'],
            'configMapHash': digest(b['configMap']), 'nodeConfigsHash': digest(items(b['nodeConfigs'])),
            'policiesHash': digest(policy_witness(b['policies'])), 'baselineHash': digest(b['baseline']),
            'endpointWitnesses': endpoints,
            'endpointWitnessFormat': ENDPOINT_WITNESS_FORMAT,
            'applicationWitness': application,
            'readySeconds': 120, 'normalAgentReplacements': 2, 'defaultRollback': True}


def emit(receipt, capability):
    lifecycle = capability.get('lifecycle', {})
    nonce = lifecycle.get('ownerNonce', '')
    if (lifecycle.get('mode') != 'probe' or lifecycle.get('imageId') != PROBE_TOOL_IMAGE
            or not isinstance(nonce, str) or not re.fullmatch('[a-f0-9]{32}', nonce)
            or lifecycle.get('containerName') != 'access-node-proxy-'+nonce
            or not isinstance(lifecycle.get('containerId'), str)
            or not re.fullmatch('[a-f0-9]{64}', lifecycle['containerId'])
            or type(lifecycle.get('childExit')) is not int or lifecycle['childExit'] != 0
            or any(lifecycle.get(k) is not True for k in ('probeCompleted', 'containerAbsentVerified', 'p2Eligible'))):
        raise ValueError()
    expected_hashes = [hashlib.sha256(instructions(f, p)).hexdigest() for f in (4, 6) for p in ('tcp', 'udp')]
    if (any(capability.get(k) != v for k, v in receipt['binding'].items())
            or capability.get('programType') != 'SCHED_CLS' or capability.get('programHashes') != expected_hashes
            or type(capability.get('loadsClosed')) is not int or capability['loadsClosed'] != 4
            or any(capability.get(k) is not False for k in ('attached', 'pinned', 'programExecuted'))
            or capability.get('helperContextSupported') is not True):
        raise ValueError()
    return {'apiVersion': 'cilium.io/v2', 'kind': 'CiliumNodeConfig',
            'metadata': {'namespace': 'kube-system', 'name': CONFIG},
            'spec': {'nodeSelector': {'matchLabels': {'kubernetes.io/hostname': receipt['node']['hostname']}},
                     'defaults': {FLAG: 'true'}}}


def current_agent(receipt, nodes, ds, pods):
    all_nodes = items(nodes)
    node = [n for n in all_nodes if n['metadata']['name'] == receipt['node']['name']]
    matched = [n for n in all_nodes if n['metadata'].get('labels', {}).get('kubernetes.io/hostname') == receipt['node']['hostname']]
    if (len(node) != 1 or len(matched) != 1 or node[0]['metadata']['uid'] != receipt['node']['uid']
            or node[0]['metadata']['labels'].get('kubernetes.io/hostname') != receipt['node']['hostname']
            or node[0]['status']['nodeInfo']['bootID'] != receipt['binding']['bootId']
            or ds['metadata']['uid'] != receipt['daemonsetUid'] or digest(ds['spec']) != receipt['daemonsetSpecHash']):
        raise ValueError()
    return selected_agent(pods, receipt['node'], ds)


def delete_options(obj, expected_uid):
    if obj['metadata']['uid'] != expected_uid:
        raise ValueError()
    return {'apiVersion': 'v1', 'kind': 'DeleteOptions', 'propagationPolicy': 'Foreground',
            'preconditions': {'uid': expected_uid, 'resourceVersion': obj['metadata']['resourceVersion']}}


def delete_agent(receipt, nodes, ds, pods, stage, replacement=None):
    pod = current_agent(receipt, nodes, ds, pods)
    ref = ident(pod, 'Pod', 'kube-system')
    if stage == 'apply':
        if ref['uid'] != receipt['initialAgent']['uid']:
            raise ValueError()
        ready(pod)
    elif stage == 'rollback':
        # Replacement identity is a fresh API observation, never an exec/Ready prerequisite.
        if not replacement or ref['uid'] != replacement['uid'] or ref['name'] != replacement['name'] or ref['uid'] == receipt['initialAgent']['uid']:
            raise ValueError()
    else:
        raise ValueError()
    return {'path': '/api/v1/namespaces/kube-system/pods/'+ref['name'],
            'body': delete_options(pod, ref['uid'])}


def delete_config(receipt, current, owned, emitted):
    ref = ident(current, 'CiliumNodeConfig', 'kube-system')
    if ref['name'] != CONFIG or ref['uid'] != owned['uid'] or canonical(current['spec']) != canonical(emitted['spec']):
        raise ValueError()
    if current['spec']['nodeSelector'] != {'matchLabels': {'kubernetes.io/hostname': receipt['node']['hostname']}}:
        raise ValueError()
    return {'path': '/apis/cilium.io/v2/namespaces/kube-system/ciliumnodeconfigs/'+CONFIG,
            'body': delete_options(current, ref['uid'])}


def health(receipt, b, flag, excluded):
    if receipt.get('endpointWitnessFormat') != ENDPOINT_WITNESS_FORMAT:
        raise ValueError()
    pod = current_agent(receipt, b['nodes'], b['daemonset'], b['agents'])
    if pod['metadata']['uid'] in excluded:
        raise ValueError()
    ready(pod)
    if next(c['imageID'] for c in pod['status']['containerStatuses'] if c['name'] == 'cilium-agent') != receipt['agentImageId']:
        raise ValueError()
    settings(b['settings'], flag)
    expected = receipt['settings'] | {FLAG: flag}
    if b['settings'] != expected or b['status']['cilium']['state'].lower() != 'ok' or b['status']['kubernetes']['state'].lower() != 'ok':
        raise ValueError()
    if (digest(b['configMap']) != receipt['configMapHash'] or digest(policy_witness(b['policies'])) != receipt['policiesHash']
            or digest(direct.emit(b['snapshot'], b['inputs'], b['images'])) != receipt['baselineHash']
            or application_witness(b['snapshot']) != receipt['applicationWitness']):
        raise ValueError()
    if endpoint_witnesses(b['endpoints'], pod['metadata']['uid']) != receipt['endpointWitnesses']:
        raise ValueError()
    if flag == 'false' and digest(items(b['nodeConfigs'])) != receipt['nodeConfigsHash']:
        raise ValueError()
    if flag == 'true':
        configs = items(b['nodeConfigs'])
        selected = [c for c in configs if c['metadata']['name'] == CONFIG]
        if len(selected) != 1:
            raise ValueError()
        config = selected[0]
        ident(config, 'CiliumNodeConfig', 'kube-system')
        expected_spec = {'nodeSelector': {'matchLabels': {'kubernetes.io/hostname': receipt['node']['hostname']}},
                         'defaults': {FLAG: 'true'}}
        if (config['metadata']['uid'] != b['ownedConfig']['uid'] or config['spec'] != expected_spec
                or digest([c for c in configs if c is not config]) != receipt['nodeConfigsHash']):
            raise ValueError()
    return ident(pod, 'Pod', 'kube-system') | {'signature': agent_signature(pod)}


def dns_job(b, receipt, nonce):
    job = direct.emit(b['snapshot'], b['inputs'], b['images'])
    if digest(job) != receipt['baselineHash']:
        raise ValueError()
    job['metadata']['name'] = JOBS['node']
    job['spec']['activeDeadlineSeconds'] = 50
    container = job['spec']['template']['spec']['containers'][0]
    container['command'] = launch.holder_command(nonce)
    container['env'] = copy.deepcopy(launch.ENV)
    return job


def service_membership(services, labels, pod_name, job_name=JOBS['node']):
    selected = []
    for service in items(services):
        m = service['metadata']
        if m.get('namespace') != 'codex-trial' or not m.get('uid'):
            raise ValueError()
        selector = service['spec'].get('selector', {})
        if any(k in selector for k in JOB_UID_LABELS + JOB_NAME_LABELS):
            raise ValueError()
        if selector and all(labels.get(k) == v for k, v in selector.items()):
            # One original production Service; no new selector/name/label changes.
            if m['name'] != 'access-web-runner-v1' or selector != {'app': 'access-web-runner'}:
                raise ValueError()
            if service['spec'].get('publishNotReadyAddresses', False) is not False:
                raise ValueError()
            selected.append({'name': m['name'], 'uid': m['uid']})
    if len(selected) != 1 or not pod_name.startswith(job_name+'-'):
        raise ValueError()
    return selected


def dns_scope(receipt, replacement, b, nonce):
    launch.selected(b['job'], b['pod'], 'node', nonce)
    if b['agent']['metadata']['uid'] != replacement['uid'] or b['agent']['metadata']['uid'] == receipt['initialAgent']['uid']:
        raise ValueError()
    ready(b['agent'])
    if agent_signature(b['agent']) != replacement['signature']:
        raise ValueError()
    saved = pin(b['pod'], b['cep'], b['agent'])
    verify(saved, b['pod'], b['cep'], b['agent'], b['endpoint'])
    if saved['node'] != receipt['node']['name']:
        raise ValueError()
    service_membership(b['services'], b['pod']['metadata']['labels'], saved['podName'])
    production, global_policy = b['production'], b['globalPolicy']
    if digest(policy_witness(b['policies'])) != receipt['policiesHash']:
        raise ValueError()
    if production not in b['policies'] or global_policy not in b['policies']:
        raise ValueError()
    ident(production, 'CiliumNetworkPolicy', 'codex-trial')
    if production['metadata']['name'] != 'access-web-boundary-v1':
        raise ValueError()
    value = endpoint_one(b['endpoint'])['status']
    policy = value['policy']
    revisions = [policy[k]['policy-revision'] for k in ('spec', 'realized')]
    expected = rows(endpoint_one(b['reference'])['status']['policy']['realized'], production, production, global_policy, 'l7', reference=True)
    if (value['state'] != 'ready' or any(type(v) is not int or v <= 0 for v in revisions)
            or len(set(revisions)) != 1 or policy.get('proxy-policy-revision') != revisions[0]):
        raise ValueError()
    for k in ('spec', 'realized'):
        if rows(policy[k], production, production, global_policy, 'l7') != expected:
            raise ValueError()
    stats = policy['proxy-statistics']
    if len(stats) != 2 or any(s.get('protocol') != 'dns' or s.get('location') != 'egress' or type(s.get('port')) is not int or s['port'] != 53 for s in stats):
        raise ValueError()
    ports = [s['allocated-proxy-port'] for s in stats]
    if any(type(p) is not int or not 1 <= p <= 65535 for p in ports) or len(set(ports)) != 1:
        raise ValueError()
    return {'phase': 'node', 'pin': saved, 'policyUid': production['metadata']['uid'],
            'policyRv': production['metadata']['resourceVersion'], 'policyRevision': revisions[0],
            'allocatedPort': ports[0], 'realizedRowsMatch': True}


def disposition(stage, elapsed, result):
    if type(elapsed) is not int or elapsed < 0 or result not in ('ok', 'failed', 'pending'):
        raise ValueError()
    if stage == 'apply-ready':
        if result == 'failed' or elapsed >= 120: return 'remove-owned-config'
        return 'fresh-health-gate' if result == 'ok' else 'wait-within-120s'
    if stage == 'apply-health': return 'dns-holder-gate' if result == 'ok' else 'remove-owned-config'
    if stage == 'dns': return 'exact-job-cleanup-then-remove-owned-config'
    if stage == 'rollback-config': return 'fresh-replacement-delete-once' if result == 'ok' else 'stop-handoff'
    if stage == 'rollback-ready':
        if result == 'failed' or elapsed >= 120: return 'stop-handoff'
        return 'restored-health-and-baseline-gate' if result == 'ok' else 'wait-within-120s'
    if stage == 'rollback-health': return 'restored' if result == 'ok' else 'stop-handoff'
    raise ValueError()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('select', 'preflight', 'emit', 'delete-agent', 'delete-config', 'health', 'dns-job', 'dns-scope', 'disposition'))
    parser.add_argument('--bundle', required=True)
    args = parser.parse_args()
    try:
        b = json.loads(Path(args.bundle).read_text())
        if args.action == 'select': result = select_initial(b)
        elif args.action == 'preflight': result = preflight(b)
        elif args.action == 'emit': result = emit(b['receipt'], b['capability'])
        elif args.action == 'delete-agent': result = delete_agent(b['receipt'], b['nodes'], b['daemonset'], b['agents'], b['stage'], b.get('replacement'))
        elif args.action == 'delete-config': result = delete_config(b['receipt'], b['current'], b['owned'], b['emitted'])
        elif args.action == 'health': result = health(b['receipt'], b, b['flag'], b['excluded'])
        elif args.action == 'dns-job': result = dns_job(b, b['receipt'], b['nonce'])
        elif args.action == 'disposition': result = disposition(b['stage'], b['elapsed'], b['result'])
        else: result = dns_scope(b['receipt'], b['replacement'], b, b['nonce'])
        print(json.dumps(result, indent=2))
    except Exception:
        parser.exit(1, 'Node proxy operation refused; private metadata is not printed\n')
