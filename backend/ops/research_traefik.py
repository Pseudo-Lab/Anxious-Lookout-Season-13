"""D7 emit-only single Traefik egress rule and build-time CLI transport pin.

Snapshots are operator-supplied inputs, not authorization. No cluster/network
calls, policy apply or discovery fallback are performed by this module.
"""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re

POLICY = 'runner-research-traefik-v1'
DEFAULT_HOST = 'traefik.kube-system.svc.cluster.local'


def require(value):
    if not value:
        raise ValueError('Traefik snapshot does not match reviewed inputs')


def name(value):
    require(isinstance(value, str) and re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', value))
    return value


def transport_host(namespace, service):
    return name(service) + '.' + name(namespace) + '.svc.cluster.local'


def compile_cli(source, namespace, service):
    marker = "CONNECT_HOST = '" + DEFAULT_HOST + "'"
    require(source.count(marker) == 1)
    return source.replace(marker, 'CONNECT_HOST = ' + repr(transport_host(namespace, service)), 1)


def render(values, service, pods, slices):
    require(set(values) == {'namespace', 'service', 'selector', 'targetPort'})
    namespace, service_name = name(values['namespace']), name(values['service'])
    selector, target = values['selector'], values['targetPort']
    require(type(target) is int and 1 <= target <= 65535)
    require(isinstance(selector, dict) and selector and all(
        isinstance(k, str) and isinstance(v, str) and k and v and not k.startswith('k8s:')
        and k != 'io.kubernetes.pod.namespace'
        for k, v in selector.items()))
    meta = service['metadata']
    require(service['kind'] == 'Service' and meta['namespace'] == namespace and meta['name'] == service_name
            and meta.get('uid') and meta.get('resourceVersion') and not meta.get('deletionTimestamp'))
    spec = service['spec']
    require(spec.get('selector') == selector and spec.get('type') != 'ExternalName' and not spec.get('externalName'))
    cluster_ip = ipaddress.ip_address(spec['clusterIP'])
    require(cluster_ip.is_private and not cluster_ip.is_loopback and not cluster_ip.is_link_local)
    ports = [port for port in spec['ports'] if port.get('port') == 443]
    require(len(ports) == 1 and ports[0].get('protocol', 'TCP') == 'TCP')
    service_port = ports[0]
    name(service_port['name'])
    port_ref = service_port.get('targetPort', 443)
    require(type(port_ref) is int or isinstance(port_ref, str))
    selected = [pod for pod in pods['items'] if pod['metadata'].get('namespace') == namespace
                and all(pod['metadata'].get('labels', {}).get(k) == v for k, v in selector.items())]
    require(selected)
    identities = {}
    for pod in selected:
        metadata = pod['metadata']
        require(metadata.get('uid') and metadata.get('resourceVersion') and not metadata.get('deletionTimestamp')
                and not pod['spec'].get('hostNetwork', False) and pod['status'].get('phase') == 'Running')
        matches = [(container, port) for container in pod['spec']['containers'] for port in container.get('ports', [])
                   if ((port.get('name') == port_ref) if isinstance(port_ref, str) else port.get('containerPort') == port_ref)
                   and port.get('protocol', 'TCP') == 'TCP']
        require(len(matches) == 1 and matches[0][1]['containerPort'] == target)
        container = matches[0][0]
        require(any(re.fullmatch(r'--entrypoints\.websecure\.address=:' + str(target) + r'(?:/tcp)?', arg, re.I)
                    for arg in container.get('args', [])))
        require(metadata['uid'] not in identities)
        identities[metadata['uid']] = pod['status']['podIP']
    endpoints = []
    for slice_ in slices['items']:
        metadata = slice_['metadata']
        if metadata.get('namespace') != namespace or metadata.get('labels', {}).get('kubernetes.io/service-name') != service_name:
            continue
        require(metadata.get('uid') and metadata.get('resourceVersion') and not metadata.get('deletionTimestamp'))
        resolved = [port for port in slice_['ports'] if port.get('name') == service_port['name']]
        require(len(resolved) == 1 and resolved[0].get('protocol', 'TCP') == 'TCP' and resolved[0]['port'] == target)
        for endpoint in slice_['endpoints']:
            require(endpoint.get('conditions', {}).get('ready') is True
                    and not endpoint.get('conditions', {}).get('terminating', False))
            ref = endpoint.get('targetRef', {})
            require(ref.get('kind') == 'Pod' and ref.get('namespace', namespace) == namespace
                    and ref.get('uid') in identities and endpoint['addresses'] == [identities[ref['uid']]])
            endpoints.append(ref['uid'])
    require(set(endpoints) == set(identities) and len(endpoints) == len(identities))
    labels = {'k8s:io.kubernetes.pod.namespace': namespace, **{'k8s:' + k: v for k, v in selector.items()}}
    policy = {'apiVersion': 'cilium.io/v2', 'kind': 'CiliumNetworkPolicy',
              'metadata': {'namespace': 'codex-runner', 'name': POLICY},
              'spec': {'endpointSelector': {'matchLabels': {'anxious.io/managed': 'user-runner'}},
                       'egress': [{'toEndpoints': [{'matchLabels': labels}],
                                   'toPorts': [{'ports': [{'port': str(target), 'protocol': 'TCP'}]}]}]}}
    proof = {'serviceUid': meta['uid'], 'serviceResourceVersion': meta['resourceVersion'],
             'transportHost': transport_host(namespace, service_name), 'servicePort': 443,
             'targetPort': target, 'podUids': sorted(identities),
             'inputSHA256': hashlib.sha256(json.dumps([values, service, pods, slices], sort_keys=True).encode()).hexdigest()}
    return policy, proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compile-cli')
    parser.add_argument('--namespace')
    parser.add_argument('--service')
    parser.add_argument('--values')
    parser.add_argument('--service-snapshot')
    parser.add_argument('--pods-snapshot')
    parser.add_argument('--slices-snapshot')
    parser.add_argument('--out')
    args = parser.parse_args()
    if args.compile_cli:
        require(args.namespace and args.service and not args.out and not args.values)
        path = Path(args.compile_cli)
        path.write_text(compile_cli(path.read_text(), args.namespace, args.service))
        return
    require(args.out and all((args.values, args.service_snapshot, args.pods_snapshot, args.slices_snapshot)))
    inputs = [json.loads(Path(path).read_text()) for path in
              (args.values, args.service_snapshot, args.pods_snapshot, args.slices_snapshot)]
    policy, proof = render(*inputs)
    os.umask(0o077)
    out = Path(args.out); out.mkdir(mode=0o700)
    (out/'egress.json').write_text(json.dumps(policy))
    (out/'proof.json').write_text(json.dumps(proof))


if __name__ == '__main__':
    main()
