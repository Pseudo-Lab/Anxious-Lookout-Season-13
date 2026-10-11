"""Compare exact fresh DNS realized rows to the stored production-policy witness."""
import argparse
import copy
import json
from pathlib import Path

from access_endpoint_pin import pin, verify
from access_dns_contrast_prepare import POLICY, identity
from access_runner_dns_control_prepare import canonical

DNS_SELECTOR = '&LabelSelector{MatchLabels:map[string]string{k8s:io.cilium.k8s.policy.cluster: default,k8s:io.kubernetes.pod.namespace: kube-system,k8s:k8s-app: kube-dns,},MatchExpressions:[]LabelSelectorRequirement{},}'
DNS_RULES = {'listenerPriority': 121, 'dns': [{'matchName': 'chatgpt.com'}, {'matchName': 'api.m2-hosting.svc.cluster.local'}]}


def endpoint_one(response):
    entries = response if isinstance(response, list) else [response]
    if len(entries) != 1:
        raise ValueError()
    return entries[0]


def rows(policy, production, diagnostic, global_policy, phase, *, reference=False):
    outputs = {}
    for direction in ('egress', 'ingress'):
        entries = policy['l4'][direction]
        if not isinstance(entries, list) or len(entries) != (5 if direction == 'egress' else 1):
            raise ValueError()
        converted = []
        seen = []
        for entry in entries:
            if not {'rule', 'derived-from-rules'} <= entry.keys() or entry.keys() - {'rule', 'derived-from-rules', 'rules-by-selector'}:
                raise ValueError()
            refs = entry['derived-from-rules']
            if not isinstance(refs, list) or len(refs) != 1:
                raise ValueError()
            target = production if reference else diagnostic
            cnp = ['k8s:io.cilium.k8s.policy.derived-from=CiliumNetworkPolicy',
                   'k8s:io.cilium.k8s.policy.name='+target['metadata']['name'],
                   'k8s:io.cilium.k8s.policy.namespace=codex-trial',
                   'k8s:io.cilium.k8s.policy.uid='+target['metadata']['uid']]
            ccnp = ['k8s:io.cilium.k8s.policy.derived-from=CiliumClusterwideNetworkPolicy',
                    'k8s:io.cilium.k8s.policy.name='+global_policy['metadata']['name'],
                    'k8s:io.cilium.k8s.policy.uid='+global_policy['metadata']['uid']]
            if sorted(refs[0]) not in (sorted(cnp), sorted(ccnp)):
                raise ValueError()
            value = copy.deepcopy(entry)
            rule = json.loads(value['rule'])
            if (set(rule) != {'tier', 'port', 'protocol', 'l7-rules'} or type(rule['port']) is not int
                    or type(rule['tier']) is not int or rule['tier'] != 200):
                raise ValueError()
            is_cnp = sorted(refs[0]) == sorted(cnp)
            seen.append((is_cnp, rule['port'], rule['protocol']))
            if rule['port'] == 53:
                if (not is_cnp or direction != 'egress' or rule['protocol'] not in ('TCP', 'UDP')
                        or canonical(rule['l7-rules']) != canonical([{DNS_SELECTOR: DNS_RULES if reference or phase == 'l7' else None}])):
                    raise ValueError()
                if reference and phase == 'l4':
                    rule['l7-rules'] = [{DNS_SELECTOR: None}]
            if reference and is_cnp:
                replacements = dict(zip(cnp, ['k8s:io.cilium.k8s.policy.derived-from=CiliumNetworkPolicy',
                    'k8s:io.cilium.k8s.policy.name='+diagnostic['metadata']['name'],
                    'k8s:io.cilium.k8s.policy.namespace=codex-trial',
                    'k8s:io.cilium.k8s.policy.uid='+diagnostic['metadata']['uid']]))
                def replace(item):
                    if isinstance(item, str): return replacements.get(item, item)
                    if isinstance(item, list): return [replace(v) for v in item]
                    if isinstance(item, dict): return {k: replace(v) for k, v in item.items()}
                    return item
                value = replace(value)
            value['derived-from-rules'] = [sorted(value['derived-from-rules'][0])]
            value['rule'] = rule
            converted.append(canonical(value))
        wanted = [(True, 8080, 'TCP')] if direction == 'ingress' else [
            (False, 0, 'ANY'), (True, 8080, 'TCP'), (True, 53, 'TCP'), (True, 53, 'UDP'), (True, 443, 'TCP')]
        if sorted(seen) != sorted(wanted):
            raise ValueError()
        outputs[direction] = sorted(converted)
    if policy['policy-enabled'] != 'both':
        raise ValueError()
    return outputs


def realized(phase, production, diagnostic, global_policy, pair, pod, cep, agent, response, reference, global_baseline):
    if phase not in ('l4', 'l7'):
        raise ValueError()
    identity(production, 'CiliumNetworkPolicy', 'access-web-boundary-v1')
    identity(diagnostic, 'CiliumNetworkPolicy', POLICY)
    if canonical(diagnostic['spec']) != canonical(pair[phase]['spec']):
        raise ValueError()
    original = copy.deepcopy(pair['l7']['spec'])
    original['endpointSelector']['matchLabels']['app'] = 'access-web-runner'
    if canonical(production['spec']) != canonical(original):
        raise ValueError()
    if (global_policy.get('kind') != 'CiliumClusterwideNetworkPolicy'
            or global_policy['metadata']['name'] != 'anxious-lookout-node-protection'
            or not global_policy['metadata'].get('uid') or global_policy['metadata'].get('deletionTimestamp')):
        raise ValueError()
    if (canonical(global_policy['spec']) != canonical(global_baseline['spec'])
            or global_policy['metadata']['uid'] != global_baseline['metadata']['uid']
            or global_policy['metadata']['resourceVersion'] != global_baseline['metadata']['resourceVersion']):
        raise ValueError()
    saved = pin(pod, cep, agent)
    verify(saved, pod, cep, agent, response)
    if (agent['spec'].get('hostNetwork') is not True or agent['spec'].get('hostPID', False) is not False
            or agent['spec'].get('shareProcessNamespace', False) is not False):
        raise ValueError()
    status = endpoint_one(response)['status']
    if status['state'] != 'ready':
        raise ValueError()
    policy = status['policy']
    reference_policy = endpoint_one(reference)['status']['policy']['realized']
    expected = rows(reference_policy, production, diagnostic, global_policy, phase, reference=True)
    revisions = [policy['spec']['policy-revision'], policy['realized']['policy-revision']]
    if any(type(v) is not int or v <= 0 for v in revisions) or len(set(revisions)) != 1:
        raise ValueError()
    for state in ('spec', 'realized'):
        if rows(policy[state], production, diagnostic, global_policy, phase) != expected:
            raise ValueError()
    stats = policy.get('proxy-statistics')
    if stats is None:
        stats = []
    if not isinstance(stats, list):
        raise ValueError()
    if phase == 'l4':
        if stats:
            raise ValueError()
        port = None
    else:
        if (type(policy.get('proxy-policy-revision')) is not int or policy['proxy-policy-revision'] != revisions[0]
                or len(stats) != 2 or any(v.get('protocol') != 'dns' or v.get('location') != 'egress'
                or type(v.get('port')) is not int or v['port'] != 53 for v in stats)):
            raise ValueError()
        ports = [v['allocated-proxy-port'] for v in stats]
        if any(type(v) is not int or not 1 <= v <= 65535 for v in ports) or len(set(ports)) != 1:
            raise ValueError()
        port = ports[0]
    return {'phase': phase, 'pin': saved, 'policyUid': diagnostic['metadata']['uid'],
            'policyRv': diagnostic['metadata']['resourceVersion'], 'policyRevision': revisions[0],
            'allocatedPort': port, 'realizedRowsMatch': True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', required=True, choices=('l4', 'l7'))
    for key in ('production', 'diagnostic', 'global-policy', 'pair', 'pod', 'cep', 'agent', 'endpoint', 'reference', 'global-baseline'):
        parser.add_argument('--'+key, required=True)
    args = parser.parse_args()
    try:
        read = lambda key: json.loads(Path(getattr(args, key.replace('-', '_'))).read_text())
        result = realized(args.phase, *(read(key) for key in ('production', 'diagnostic', 'global-policy', 'pair',
                          'pod', 'cep', 'agent', 'endpoint', 'reference', 'global-baseline')))
        print(json.dumps(result, indent=2))
    except Exception:
        parser.exit(1, 'DNS realized-path contrast refused; private metadata is not printed\n')
