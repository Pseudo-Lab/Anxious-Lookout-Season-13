"""Offline fixed collector/holder/nonce-policy/numeric exec preparation."""
import argparse
import copy
import datetime
import hashlib
import ipaddress
import json
from pathlib import Path
import signal
import time

import access_dns_contrast_exec as own
import access_dns_contrast_prepare as contrast
import access_dns_contrast_scope as native
import access_endpoint_pin as endpoint
import access_node_proxy_prepare as node
import access_numeric_lease as dns
import access_runner_dns_prepare as direct
from access_runner_dns_control_prepare import canonical

NAME = 'access-runner-numeric-tls-v1'
LABEL = 'issue7-network-check'


def runtime_source():
    directory = Path(__file__).parent
    return (directory/'access_dns_contrast_runtime.py').read_text()+'\n'+(directory/'access_numeric_runtime.py').read_text()


def holder_command(nonce):
    own.nonce_ok(nonce)
    return ['python', '-B', '-c', runtime_source()+'\nholder('+json.dumps(nonce)+')\n']


def holder(b, nonce):
    node.health(b['receipt'], b, 'false', [])
    job = direct.emit(b['snapshot'], b['inputs'], b['images'])
    if node.digest(job) != b['receipt']['baselineHash']: raise ValueError()
    job['metadata']['name'] = NAME
    job['spec']['activeDeadlineSeconds'] = 30
    labels = job['spec']['template']['metadata']['labels']
    if LABEL in labels: raise ValueError()
    labels[LABEL] = nonce
    node.service_membership(b['services'], labels, NAME+'-pending', NAME)
    if any(p['metadata'].get('labels', {}).get('app') == 'access-web-runner'
           and p['metadata']['labels'].get(LABEL) == nonce for p in node.items(b['pods'])): raise ValueError()
    c = job['spec']['template']['spec']['containers'][0]
    c['env'] = copy.deepcopy(own.ENV); c['command'] = holder_command(nonce)
    return job


def selected(job, pod, nonce):
    if job['metadata']['name'] != NAME or job['spec']['activeDeadlineSeconds'] != 30:
        raise ValueError()
    c = job['spec']['template']['spec']['containers'][0]
    if c['command'] != holder_command(nonce) or job['spec']['template']['metadata']['labels'] != {'app': 'access-web-runner', LABEL: nonce}:
        raise ValueError()
    own.selected_exact(job, pod, NAME, nonce, holder_command(nonce), 30)
    return node.ident(pod, 'Pod', 'codex-trial')


def singleton(pods, pod, nonce):
    matches = [p for p in node.items(pods) if p['metadata'].get('labels', {}).get('app') == 'access-web-runner'
               and p['metadata']['labels'].get(LABEL) == nonce]
    if len(matches) != 1 or matches[0]['metadata']['uid'] != pod['metadata']['uid']: raise ValueError()


def service_target(service, slices, pod, replica_set, deployment, port):
    ref = node.ident(service, 'Service', service['metadata']['namespace'])
    namespace, expected_name = ('m2-hosting', 'api') if port == 8080 else ('kube-system', 'kube-dns')
    if service['metadata']['namespace'] != namespace or ref['name'] != expected_name: raise ValueError()
    ip = str(ipaddress.IPv4Address(service['spec']['clusterIP']))
    numeric_ip = ipaddress.ip_address(ip)
    if numeric_ip.is_loopback or numeric_ip.is_link_local or numeric_ip.is_unspecified or numeric_ip.is_multicast or numeric_ip.is_reserved: raise ValueError()
    selector = service['spec']['selector']
    if not selector or any(pod['metadata']['labels'].get(k) != v for k, v in selector.items()): raise ValueError()
    node.ident(replica_set, 'ReplicaSet', namespace); node.ident(deployment, 'Deployment', namespace)
    if any(pod['metadata']['labels'].get(k) != v for k, v in deployment['spec']['template']['metadata']['labels'].items()): raise ValueError()
    if pod['metadata']['namespace'] != namespace or pod['status']['phase'] != 'Running' or pod['metadata'].get('deletionTimestamp'): raise ValueError()
    if any(type(obj['metadata'].get(k)) is not str or not obj['metadata'][k]
           for obj in (service, pod, replica_set, deployment) for k in ('uid', 'resourceVersion')): raise ValueError()
    if not any(c['type'] == 'Ready' and c['status'] == 'True' for c in pod['status']['conditions']): raise ValueError()
    owners = pod['metadata']['ownerReferences']
    if len(owners) != 1 or owners[0].get('controller') is not True or owners[0]['uid'] != replica_set['metadata']['uid']:
        raise ValueError()
    parent = replica_set['metadata']['ownerReferences']
    if len(parent) != 1 or parent[0].get('controller') is not True or parent[0]['uid'] != deployment['metadata']['uid']:
        raise ValueError()
    if owners[0]['kind'] != 'ReplicaSet' or parent[0]['kind'] != 'Deployment': raise ValueError()
    ports = [p for p in service['spec']['ports'] if p['port'] == port and p.get('protocol') == 'TCP']
    if len(ports) != 1 or service['spec'].get('publishNotReadyAddresses', False): raise ValueError()
    service_port = ports[0]
    target = service_port['targetPort']
    target_container = None
    if type(target) is int:
        if target != port: raise ValueError()
    elif type(target) is str and target and port == 8080:
        # Resolve only the bound Pod's regular api container, never another
        # Pod/container or an unrelated numeric entry in the EndpointSlice.
        declarations = [(c['name'], p) for c in pod['spec']['containers']
                        for p in c.get('ports', []) if p.get('name') == target]
        if (len(declarations) != 1 or declarations[0][0] != 'api'
                or declarations[0][1].get('protocol', 'TCP') != 'TCP'
                or type(declarations[0][1].get('containerPort')) is not int
                or declarations[0][1]['containerPort'] != port): raise ValueError()
        target_container = declarations[0][0]
    else: raise ValueError()
    count = 0
    for s in node.items(slices):
        if s['metadata']['namespace'] != namespace or s['metadata']['labels']['kubernetes.io/service-name'] != expected_name:
            raise ValueError()
        if not any(o['uid'] == ref['uid'] and o['kind'] == 'Service' for o in s['metadata']['ownerReferences']): raise ValueError()
        resolved = [p for p in s['ports'] if p.get('name') == service_port.get('name') and p.get('protocol') == 'TCP']
        if (s['addressType'] != 'IPv4' or len(resolved) != 1
                or type(resolved[0].get('port')) is not int or resolved[0]['port'] != port): raise ValueError()
        for e in s['endpoints']:
            if e['conditions'].get('ready') is not True or e['conditions'].get('terminating', False): raise ValueError()
            t = e['targetRef']
            if t['kind'] != 'Pod' or t['namespace'] != namespace or t['uid'] != pod['metadata']['uid'] or t.get('name', pod['metadata']['name']) != pod['metadata']['name'] or e['addresses'] != [pod['status']['podIP']]:
                raise ValueError()
            count += 1
    if count != 1: raise ValueError()
    return {'uid': ref['uid'], 'rv': ref['resourceVersion'], 'ip': ip, 'podUid': pod['metadata']['uid'],
            'servicePortName': service_port.get('name'), 'targetPort': target,
            'resolvedPort': port, 'targetContainer': target_container}


def collector_binding(b, nonce):
    own.nonce_ok(nonce)
    node.health(b['receipt'], b, 'false', [])
    callback = service_target(b['callbackService'], b['callbackSlices'], b['apiPod'], b['apiReplicaSet'], b['snapshot']['api'], 8080)
    resolver = service_target(b['dnsService'], b['dnsSlices'], b['dnsPod'], b['dnsReplicaSet'], b['dnsDeployment'], 53)
    if b['apiPod']['spec']['nodeName'] != b['receipt']['node']['name'] or b['dnsPod']['spec']['nodeName'] != b['receipt']['node']['name']:
        raise ValueError()
    container = [s for s in b['apiPod']['status']['containerStatuses'] if s['name'] == 'api']
    if (len(container) != 1 or container[0].get('ready') is not True
            or type(container[0].get('restartCount')) is not int or container[0]['restartCount'] < 0
            or not container[0].get('state', {}).get('running') or not container[0].get('containerID') or not container[0].get('imageID')): raise ValueError()
    return {'nonce': nonce, 'nodeUid': b['receipt']['node']['uid'], 'bootId': b['receipt']['binding']['bootId'],
            'apiPodName': b['apiPod']['metadata']['name'], 'apiPodUid': callback['podUid'],
            'apiContainerSignature': {k: container[0][k] for k in ('containerID', 'restartCount', 'imageID', 'state')},
            'callback': callback, 'resolverUid': resolver['uid'], 'resolverRv': resolver['rv'], 'resolverIp': resolver['ip']}


def collector_program(binding):
    source = Path(dns.__file__).read_text()
    return source+'\nimport signal\ndef expired(*a): raise SystemExit(1)\nsignal.signal(signal.SIGALRM,expired)\nsignal.alarm(3)\nprint(json.dumps(collect('+repr(binding)+')),flush=True)\n'


def original_scope(b, nonce):
    selected(b['job'], b['pod'], nonce); singleton(b['pods'], b['pod'], nonce)
    if b['production'] not in b['policies'] or b['globalPolicy'] not in b['policies']: raise ValueError()
    node.service_membership(b['services'], b['pod']['metadata']['labels'], b['pod']['metadata']['name'], NAME)
    saved = endpoint.pin(b['pod'], b['cep'], b['agent'])
    endpoint.verify(saved, b['pod'], b['cep'], b['agent'], b['endpoint'])
    if (saved['agentUid'] != b['receipt']['initialAgent']['uid'] or saved['node'] != b['receipt']['node']['name']
            or node.agent_signature(b['agent']) != b['agentSignature']
            or node.digest(node.policy_witness(b['policies'])) != b['receipt']['policiesHash']): raise ValueError()
    p = native.endpoint_one(b['endpoint'])['status']['policy']
    if native.endpoint_one(b['endpoint'])['status']['state'] != 'ready' or p['spec']['policy-revision'] != p['realized']['policy-revision']:
        raise ValueError()
    if canonical(node.policy_snapshot(p['spec'])) != canonical(node.policy_snapshot(p['realized'])): raise ValueError()
    reference = native.endpoint_one(b['reference'])['status']['policy']['realized']
    if (native.rows(p['realized'], b['production'], b['production'], b['globalPolicy'], 'l7')
            != native.rows(reference, b['production'], b['production'], b['globalPolicy'], 'l7', reference=True)
            or type(p.get('proxy-policy-revision')) is not int or p['proxy-policy-revision'] != p['realized']['policy-revision']): raise ValueError()
    return saved


def policy(lease, nonce, now_ns):
    if lease['binding']['nonce'] != nonce or lease['expiryNs']-now_ns < 15_000_000_000: raise ValueError()
    return policy_spec(lease, nonce)


def policy_spec(lease, nonce):
    own.nonce_ok(nonce)
    ip = dns.public_ipv4(lease['providerIp'])
    return {'apiVersion': 'cilium.io/v2', 'kind': 'CiliumNetworkPolicy',
        'metadata': {'name': NAME, 'namespace': 'codex-trial'},
        'spec': {'endpointSelector': {'matchLabels': {'app': 'access-web-runner', LABEL: nonce}},
                 'enableDefaultDeny': {'ingress': False, 'egress': False},
                 'egress': [{'toCIDR': [ip+'/32'], 'toPorts': [{'ports': [{'port': '443', 'protocol': 'TCP'}]}]}]}}


def scoped(b, nonce, lease, now_ns):
    selected(b['job'], b['pod'], nonce); singleton(b['pods'], b['pod'], nonce)
    node.service_membership(b['services'], b['pod']['metadata']['labels'], b['pod']['metadata']['name'], NAME)
    if b['agent']['metadata']['uid'] != b['receipt']['initialAgent']['uid']: raise ValueError()
    if lease['binding'] != collector_binding(b['collectorBaseline'], nonce): raise ValueError()
    temporary = b['temporary']
    node.ident(temporary, 'CiliumNetworkPolicy', 'codex-trial')
    if lease['binding']['nonce'] != nonce or lease['expiryNs']-now_ns < 10_000_000_000: raise ValueError()
    if temporary['metadata']['name'] != NAME or temporary['metadata']['uid'] != b['ownedPolicy']['uid'] or temporary['spec'] != policy_spec(lease, nonce)['spec']: raise ValueError()
    if node.digest(node.policy_witness(b['policies'])) != b['receipt']['policiesHash']: raise ValueError()
    # Generic pin permits only this additional fixed diagnostic name.
    saved = endpoint.pin(b['pod'], b['cep'], b['agent'])
    endpoint.verify(saved, b['pod'], b['cep'], b['agent'], b['endpoint'])
    if saved['node'] != b['receipt']['node']['name'] or node.agent_signature(b['agent']) != b['agentSignature']:
        raise ValueError()
    p = native.endpoint_one(b['endpoint'])['status']['policy']
    if native.endpoint_one(b['endpoint'])['status']['state'] != 'ready': raise ValueError()
    if p['spec']['policy-revision'] != p['realized']['policy-revision']: raise ValueError()
    if canonical(node.policy_snapshot(p['spec'])) != canonical(node.policy_snapshot(p['realized'])): raise ValueError()
    original = b['production']; global_policy = b['globalPolicy']
    if original not in b['policies'] or global_policy not in b['policies']: raise ValueError()
    desired = copy.deepcopy(native.endpoint_one(b['reference'])['status']['policy']['realized'])
    native.rows(desired, original, original, global_policy, 'l7', reference=True)
    got = copy.deepcopy(p['realized'])
    candidates = [e for e in got['l4']['egress'] if json.loads(e['rule'])['port'] == 443]
    if len(candidates) != 1: raise ValueError()
    e = candidates[0]; rule = json.loads(e['rule'])
    refs = ['k8s:io.cilium.k8s.policy.derived-from=CiliumNetworkPolicy', 'k8s:io.cilium.k8s.policy.name='+NAME,
            'k8s:io.cilium.k8s.policy.namespace=codex-trial', 'k8s:io.cilium.k8s.policy.uid='+temporary['metadata']['uid']]
    if sum(sorted(r) == sorted(refs) for r in e['derived-from-rules']) != 1: raise ValueError()
    e['derived-from-rules'] = [r for r in e['derived-from-rules'] if sorted(r) != sorted(refs)]
    selector = '&LabelSelector{MatchLabels:map[string]string{cidr:'+lease['providerIp']+'/32: ,},MatchExpressions:[]LabelSelectorRequirement{},}'
    addition = {selector: None}
    if rule['l7-rules'].count(addition) != 1: raise ValueError()
    rule['l7-rules'].remove(addition); e['rule'] = json.dumps(rule)
    if native.rows(got, original, original, global_policy, 'l7') != native.rows(desired, original, original, global_policy, 'l7', reference=True):
        raise ValueError()
    # DNS redirect rows/ACK remain present although this worker emits no DNS.
    if type(p.get('proxy-policy-revision')) is not int or p['proxy-policy-revision'] != p['realized']['policy-revision']: raise ValueError()
    stats = p['proxy-statistics']
    if len(stats) != 2 or any(s['protocol'] != 'dns' or s['location'] != 'egress' or s['port'] != 53 for s in stats): raise ValueError()
    ports = [s['allocated-proxy-port'] for s in stats]
    if any(type(port) is not int or not 1 <= port <= 65535 for port in ports) or len(set(ports)) != 1: raise ValueError()
    return {'pin': saved, 'policyUid': temporary['metadata']['uid'], 'policyRv': temporary['metadata']['resourceVersion'],
            'policyRevision': p['realized']['policy-revision'], 'nonce': nonce, 'lease': lease}


def program(b, scope, lease):
    nonce = scope['nonce']; selected(b['job'], b['pod'], nonce)
    receipt = b['holderReceipt']
    command = holder_command(nonce)
    if (receipt['podUid'] != b['pod']['metadata']['uid'] or receipt['podName'] != b['pod']['metadata']['name']
            or receipt['nonce'] != nonce or receipt['holding'] is not True or receipt['networkRequested'] is not False
            or receipt['cmdlineSha256'] != hashlib.sha256(('\0'.join(command)+'\0').encode()).hexdigest()
            or scope['pin']['podUid'] != receipt['podUid'] or scope['lease'] != lease
            or receipt['timeNamespace'] != lease['timeNamespace'] or receipt['bootId'] != lease['bootId']): raise ValueError()
    start = datetime.datetime.fromisoformat(b['job']['status']['startTime'].replace('Z', '+00:00')).timestamp()
    deadline = lease['endNs']+int(start*1_000_000_000)-lease['realtimeNs']+30_000_000_000
    expected = {k: receipt[k] for k in ('podUid', 'podName', 'startTicks', 'startNs', 'cmdlineSha256', 'nonce')}
    expected.update(holderCommand=command, lease=lease, callbackIp=lease['binding']['callback']['ip'],
                    jobUid=b['job']['metadata']['uid'], jobDeadlineNs=deadline,
                    context={'leaseHash': node.digest(lease), 'policyUid': scope['policyUid'],
                             'policyRv': scope['policyRv'], 'agentUid': scope['pin']['agentUid'],
                             'policyRevision': scope['policyRevision']})
    return runtime_source()+'\nEXPECTED='+repr(expected)+'''\ntry:
    print(json.dumps(numeric_once(EXPECTED)),flush=True)
except (Exception,NumericDeadline):
    print('{"numericTransport":"unknown","attemptConsumed":true}',flush=True)
    raise SystemExit(1)
'''


def deletion(current, owned):
    if current['kind'] not in ('Job', 'CiliumNetworkPolicy') or current['metadata']['name'] != NAME:
        raise ValueError()
    node.ident(current, current['kind'], 'codex-trial')
    if current['metadata']['uid'] != owned['uid'] or node.digest(current['spec']) != owned['specHash']:
        raise ValueError()
    return node.delete_options(current, owned['uid'])


def checked_result(b):
    selected(b['job'], b['pod'], b['nonce']); singleton(b['pods'], b['pod'], b['nonce'])
    scope, lease, r = b['scope'], b['lease'], b['result']
    endpoint.verify(scope['pin'], b['pod'], b['cep'], b['agent'], b['endpoint'])
    expected = {'leaseHash': node.digest(lease), 'policyUid': scope['policyUid'], 'policyRv': scope['policyRv'],
                'agentUid': scope['pin']['agentUid'], 'policyRevision': scope['policyRevision']}
    if (b['transportExit'] != 0 or type(b['transportExit']) is not int or r.get('context') != expected
            or r.get('nonce') != b['nonce'] or r.get('podUid') != b['pod']['metadata']['uid'] or r.get('jobUid') != b['job']['metadata']['uid']
            or node.agent_signature(b['agent']) != b['agentSignature']
            or node.digest(node.policy_witness(b['policies'])) != b['receipt']['policiesHash']
            or b['temporary']['metadata']['uid'] != scope['policyUid'] or b['temporary']['metadata']['resourceVersion'] != scope['policyRv']
            or b['temporary']['spec'] != policy_spec(lease, b['nonce'])['spec']
            or any(r.get(k) is not False for k in ('authRpc', 'nativeStarted', 'sourceCredentialRead'))
            or type(r.get('dnsQueries')) is not int or r['dnsQueries'] != 0 or type(r.get('httpBytes')) is not int or r['httpBytes'] != 0): raise ValueError()
    if any(type(r.get(k)) is not int for k in ('enteredNs', 'finishedNs', 'providerAttempts')): raise ValueError()
    if not lease['endNs'] <= r['enteredNs'] <= r['finishedNs'] < lease['expiryNs'] or r.get('deadlineExceeded', False): raise ValueError()
    start = b['holderReceipt']['startNs']
    job_start = datetime.datetime.fromisoformat(b['job']['status']['startTime'].replace('Z', '+00:00')).timestamp()
    job_end = lease['endNs']+int(job_start*1_000_000_000)-lease['realtimeNs']+30_000_000_000
    if type(start) is not int or r['enteredNs'] >= start+18_000_000_000 or r['finishedNs'] >= min(start+28_000_000_000, job_end): raise ValueError()
    if (r['callback'] == 'failed' and (r['providerAttempts'] != 0 or r['provider'] != 'not_attempted')
            or r['callback'] == 'tcp_connected' and (r['providerAttempts'] != 1 or r['provider'] not in ('tls_verified', 'failed', 'certificate_failed'))
            or r['callback'] not in ('failed', 'tcp_connected')): raise ValueError()
    return {'callbackTcp': r['callback'], 'providerTls': r['provider'], 'webOrNativeSuccess': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('holder', 'binding', 'precollect', 'collector', 'lease', 'policy', 'scope', 'program', 'result', 'delete'))
    parser.add_argument('--bundle', required=True); a = parser.parse_args()
    try:
        b = json.loads(Path(a.bundle).read_text())
        clock = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
        if a.action in ('lease', 'policy', 'scope', 'program'):
            captured = b['capture']
            if b['bindingAfter'] != b['binding'] or b['binding'] != collector_binding(b['collectorBaseline'], b['nonce']): raise ValueError()
            if (captured['bootId'] != b['binding']['bootId']
                    or captured['bootId'] != Path('/proc/sys/kernel/random/boot_id').read_text().strip()
                    or captured['timeNamespace'] != str(Path('/proc/self/ns/time').readlink())): raise ValueError()
            verified_lease = dns.lease(captured, b['binding'], clock, 10 if a.action in ('scope', 'program') else 15)
            if a.action != 'lease' and b['lease'] != verified_lease: raise ValueError()
        if a.action == 'holder': result = holder(b, b['nonce'])
        elif a.action == 'binding': result = collector_binding(b, b['nonce'])
        elif a.action == 'precollect': result = original_scope(b, b['nonce'])
        elif a.action == 'collector':
            if b['binding'] != collector_binding(b['collectorBaseline'], b['nonce']): raise ValueError()
            original_scope(b['precollection'], b['nonce'])
            result = collector_program(b['binding'])
        elif a.action == 'lease':
            if b['bindingAfter'] != b['binding']: raise ValueError()
            result = dns.lease(b['capture'], b['binding'], clock)
        elif a.action == 'policy': result = policy(b['lease'], b['nonce'], clock)
        elif a.action == 'scope': result = scoped(b, b['nonce'], b['lease'], clock)
        elif a.action == 'program':
            verified_scope = scoped(b, b['nonce'], b['lease'], clock)
            if verified_scope != b['scope']: raise ValueError()
            result = program(b, b['scope'], b['lease'])
        elif a.action == 'result': result = checked_result(b)
        else: result = deletion(b['current'], b['owned'])
        print(result if isinstance(result, str) else json.dumps(result, indent=2))
    except Exception:
        parser.exit(1, 'Numeric transport preparation refused; private metadata is not printed\n')
