"""Read only the diagnostic Pod's resolver file and emit address-free comparisons."""
import ipaddress
import json
import os
import stat


def expected_context(service, pod, node):
    sm, pm = service['metadata'], pod['metadata']
    selectors = service['spec']['selector']
    if (service.get('kind') != 'Service' or sm['namespace'] != 'kube-system' or sm['name'] != 'kube-dns'
            or not sm.get('uid') or not sm.get('resourceVersion')
            or service['spec'].get('type', 'ClusterIP') != 'ClusterIP'
            or selectors.get('k8s-app') != 'kube-dns'
            or pod.get('kind') != 'Pod' or pm['namespace'] != 'kube-system'
            or not pm.get('uid') or not pm.get('resourceVersion')
            or pod['spec']['nodeName'] != node or pod['spec'].get('hostNetwork', False)
            or pod['status']['phase'] != 'Running'
            or not any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in pod['status']['conditions'])
            or not all(pm['labels'].get(k) == v for k, v in selectors.items())):
        raise ValueError()
    ports = {(p['port'], p['protocol']) for p in service['spec']['ports']}
    if not {(53, 'UDP'), (53, 'TCP')}.issubset(ports):
        raise ValueError()
    services = service['spec'].get('clusterIPs') or [service['spec']['clusterIP']]
    pods = [p['ip'] for p in pod['status'].get('podIPs', [])] or [pod['status']['podIP']]
    if not 1 <= len(services) <= 2 or not 1 <= len(pods) <= 2:
        raise ValueError()
    for addresses in (services, pods):
        for value in addresses:
            if not isinstance(value, str) or '%' in value:
                raise ValueError()
            address = ipaddress.ip_address(value)
            if address.is_unspecified or address.is_multicast or address.is_loopback or address.is_link_local:
                raise ValueError()
    if service['spec']['clusterIP'] not in services or pod['status']['podIP'] not in pods:
        raise ValueError()
    return {'serviceIps': [str(ipaddress.ip_address(v)) for v in services],
            'podIps': [str(ipaddress.ip_address(v)) for v in pods]}


def summary(text, expected):
    if len(text.encode()) > 65536 or len(text.splitlines()) > 256:
        raise ValueError()
    resolvers = []
    for line in text.splitlines():
        words = line.split('#', 1)[0].split(';', 1)[0].split()
        if words and words[0] == 'nameserver':
            if len(words) != 2:
                raise ValueError()
            resolvers.append(ipaddress.ip_address(words[1]))
    if not 1 <= len(resolvers) <= 8:
        raise ValueError()
    primary = resolvers[0]
    services = [ipaddress.ip_address(v) for v in expected['serviceIps']]
    pods = [ipaddress.ip_address(v) for v in expected['podIps']]
    return {'schemaVersion': 1, 'resolverCount': len(resolvers),
            'primaryMatchesDnsService': primary in services, 'primaryMatchesKnownCoreDnsPod': primary in pods,
            'allResolversMatchDnsService': all(v in services for v in resolvers),
            'primaryFamilyMatchesDnsService': any(v.version == primary.version for v in services),
            'primaryScope': 'loopback' if primary.is_loopback else 'link_local' if primary.is_link_local else 'other',
            'networkRequested': False, 'dnsCause': 'unknown'}


def read_resolver():
    fd = os.open('/etc/resolv.conf', os.O_RDONLY|os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError()
        raw = os.read(fd, 65537)
        if len(raw) > 65536:
            raise ValueError()
        return raw.decode('utf-8')
    finally:
        os.close(fd)


if __name__ == '__main__':
    try:
        print(json.dumps(summary(read_resolver(), EXPECTED_DNS_CONTEXT)))
    except Exception:
        print('{"resolverMap":"unknown","networkRequested":false}')
        raise SystemExit(1)
