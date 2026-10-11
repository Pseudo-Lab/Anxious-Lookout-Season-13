"""Bind a numeric local Cilium endpoint to fresh exact Pod/CEP/agent metadata.

Private pin output is operator-only; no endpoint list, credentials or live exec.
"""
import argparse
import json
import os
from pathlib import Path


def pin(pod, cep, agent):
    meta, cm, am = pod['metadata'], cep['metadata'], agent['metadata']
    namespace, name, uid = meta['namespace'], meta['name'], meta['uid']
    node = pod['spec']['nodeName']
    if namespace == 'codex-trial':
        if not name.startswith('access-dns-observed-diagnose-v1-') or meta['labels'].get('app') != 'access-web-runner':
            raise ValueError()
    elif namespace == 'kube-system':
        if meta['labels'].get('k8s-app') != 'kube-dns':
            raise ValueError()
    else:
        raise ValueError()
    if (pod.get('kind') != 'Pod' or cep.get('kind') != 'CiliumEndpoint' or agent.get('kind') != 'Pod'
            or pod['status']['phase'] != 'Running' or agent['status']['phase'] != 'Running'
            or am['namespace'] != 'kube-system' or am['labels'].get('k8s-app') != 'cilium'
            or agent['spec']['nodeName'] != node or cm['namespace'] != namespace or cm['name'] != name
            or any(v.get('deletionTimestamp') for v in (meta, cm, am))
            or not uid or not cm.get('uid') or not am.get('uid') or cep['status']['state'] != 'ready'):
        raise ValueError()
    owners = [v for v in cm['ownerReferences'] if v.get('kind') == 'Pod']
    if len(owners) != 1 or owners[0]['uid'] != uid or owners[0]['name'] != name:
        raise ValueError()
    endpoint = cep['status']['id']  # Local endpoint ID, NOT status.identity.id.
    if type(endpoint) is not int or not 1 <= endpoint <= 65535:
        raise ValueError()
    external = cep['status']['external-identifiers']
    attachment = external.get('cni-attachment-id')
    pod_ip = pod['status'].get('podIP')
    addressing = cep['status']['networking']['addressing']
    if (not isinstance(attachment, str) or not 1 <= len(attachment) <= 256 or not pod_ip
            or not any(pod_ip in (value.get('ipv4'), value.get('ipv6')) for value in addressing)):
        raise ValueError()
    if 'k8s-pod-name' in external and external['k8s-pod-name'] != namespace+'/'+name:
        raise ValueError()
    return {'namespace': namespace, 'podName': name, 'podUid': uid, 'cepUid': cm['uid'],
            'node': node, 'agentName': am['name'], 'agentUid': am['uid'], 'endpointId': endpoint,
            'cniAttachmentId': attachment, 'podIp': pod_ip}


def verify(saved, pod, cep, agent, response):
    if pin(pod, cep, agent) != saved:
        raise ValueError()
    entries = response if isinstance(response, list) else [response]
    if len(entries) != 1:
        raise ValueError()
    value = entries[0]
    if type(value.get('id')) is not int or value['id'] != saved['endpointId']:
        raise ValueError()
    status = value.get('status', {})
    external = status.get('external-identifiers', {})
    if external.get('cni-attachment-id') != saved['cniAttachmentId']:
        raise ValueError()
    if 'k8s-pod-name' in external and external['k8s-pod-name'] != saved['namespace']+'/'+saved['podName']:
        raise ValueError()
    if not any(saved['podIp'] in (v.get('ipv4'), v.get('ipv6')) for v in status.get('networking', {}).get('addressing', [])):
        raise ValueError()
    return {'selectedEndpointMatches': True, 'endpointState': 'ready' if status.get('state') == 'ready' else 'not_ready',
            'realizedPolicyPresent': isinstance(status.get('policy', {}).get('realized'), dict)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pod', required=True); parser.add_argument('--cep', required=True); parser.add_argument('--agent', required=True)
    parser.add_argument('--pin', required=True); parser.add_argument('--response')
    args = parser.parse_args()
    try:
        pod, cep, agent = [json.loads(Path(path).read_text()) for path in (args.pod, args.cep, args.agent)]
        if args.response:
            print(json.dumps(verify(json.loads(Path(args.pin).read_text()), pod, cep, agent,
                                    json.loads(Path(args.response).read_text()))))
        else:
            value = pin(pod, cep, agent)
            with os.fdopen(os.open(args.pin, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'w') as target:
                json.dump(value, target)
            print('{"endpointPinCreated":true}')
    except Exception:
        parser.exit(1, 'Exact local endpoint pin refused; identities are not printed\n')
