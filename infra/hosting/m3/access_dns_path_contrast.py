"""Functions for one configured DNS Service versus one verified CoreDNS backend."""
import ipaddress
import json
import signal


class ContrastDeadline(BaseException):
    pass  # Must escape the shared query helper's Exception handler.


def path_context(data, node, validate_context):
    service, pod, endpoint_slice = data['service'], data['pod'], data['slice']
    expected = validate_context(service, pod, node)
    meta = endpoint_slice['metadata']
    owners = [o for o in meta['ownerReferences'] if o.get('kind') == 'Service' and o.get('controller') is True]
    if (endpoint_slice.get('kind') != 'EndpointSlice' or meta['namespace'] != 'kube-system'
            or not meta.get('uid') or not meta.get('resourceVersion')
            or meta['labels'].get('kubernetes.io/service-name') != 'kube-dns'
            or len(owners) != 1 or owners[0]['uid'] != service['metadata']['uid'] or owners[0]['name'] != 'kube-dns'
            or endpoint_slice['addressType'] not in ('IPv4', 'IPv6')):
        raise ValueError()
    family = 4 if endpoint_slice['addressType'] == 'IPv4' else 6
    matches = [e for e in endpoint_slice['endpoints'] if e.get('targetRef', {}).get('uid') == pod['metadata']['uid']]
    if len(matches) != 1:
        raise ValueError()
    endpoint = matches[0]; ref = endpoint['targetRef']
    if (ref.get('kind') != 'Pod' or ref.get('namespace') != 'kube-system' or ref.get('name') != pod['metadata']['name']
            or endpoint.get('nodeName') != node or endpoint['conditions'].get('ready') is not True
            or endpoint['conditions'].get('terminating', False) is not False):
        raise ValueError()
    ports = {(p['port'], p['protocol']) for p in endpoint_slice['ports']}
    if not {(53, 'TCP'), (53, 'UDP')}.issubset(ports):
        raise ValueError()
    services = [v for v in expected['serviceIps'] if ipaddress.ip_address(v).version == family]
    pods = [v for v in expected['podIps'] if ipaddress.ip_address(v).version == family]
    if not isinstance(endpoint['addresses'], list) or any(not isinstance(v, str) or '%' in v for v in endpoint['addresses']):
        raise ValueError()
    addresses = [str(ipaddress.ip_address(v)) for v in endpoint['addresses']]
    if len(services) != 1 or len(pods) != 1 or addresses != pods or services == pods:
        raise ValueError()
    return {'serviceIps': services, 'podIps': pods}


def safe_reply(value):
    if set(value) != {'status', 'rcode', 'answersPresent'} or value['status'] not in ('reply', 'timeout', 'failed'):
        raise ValueError()
    if type(value['answersPresent']) is not bool:
        raise ValueError()
    if value['status'] == 'reply':
        if type(value['rcode']) is not int or not 0 <= value['rcode'] <= 15:
            raise ValueError()
    elif value['rcode'] is not None or value['answersPresent']:
        raise ValueError()
    return dict(value)


def contrast(text, expected, query_fn, compare_fn):
    comparison = compare_fn(text, expected)
    if comparison['resolverCount'] != 1 or not comparison['primaryMatchesDnsService']:
        raise ValueError()
    report = {'networkRequested': True, 'queryCount': 4, 'dnsCause': 'unknown'}
    for alias, key in (('configuredService', 'serviceIps'), ('knownCoreDnsPod', 'podIps')):
        addresses = expected[key]
        if len(addresses) != 1:
            raise ValueError()
        report[alias] = {transport: safe_reply(query_fn('chatgpt.com', addresses[0], tcp=tcp))
                         for transport, tcp in (('udp', False), ('tcp', True))}
    return report


def main(expected, read_fn, query_fn, compare_fn):
    def expired(*_):
        raise ContrastDeadline()
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(12)
    try:
        result = contrast(read_fn(), expected, query_fn, compare_fn)
        signal.alarm(0)
        print(json.dumps(result))
    except (Exception, ContrastDeadline):
        signal.alarm(0)
        print('{"dnsPathContrast":"unknown","networkRequested":"unknown"}')
        raise SystemExit(1)
