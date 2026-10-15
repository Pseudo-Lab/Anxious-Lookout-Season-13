"""Offline scope and validate one historical DN3 DNS verdict query. No live calls."""
import argparse
import datetime as dt
import ipaddress
import json
import os
import re
from pathlib import Path

SOCKET = 'unix:///var/run/cilium/hubble.sock'
MASK = ','.join(('time', 'node_name', 'IP.source', 'IP.destination',
                 'source.namespace', 'source.pod_name', 'source.identity',
                 'destination.namespace', 'destination.pod_name', 'destination.identity',
                 'l4.TCP.source_port', 'l4.TCP.destination_port',
                 'l4.UDP.source_port', 'l4.UDP.destination_port',
                 'event_type', 'verdict', 'drop_reason_desc',
                 'egress_denied_by.name', 'egress_denied_by.kind', 'egress_denied_by.namespace',
                 'ingress_denied_by.name', 'ingress_denied_by.kind', 'ingress_denied_by.namespace'))
MAX_BYTES = 262144
MAX_RECORDS = 256


def timestamp(value):
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError()
    return parsed


def plan(pin, pod, job, agent):
    meta, jm, am = pod['metadata'], job['metadata'], agent['metadata']
    owners = [o for o in meta['ownerReferences'] if o.get('kind') == 'Job' and o.get('controller') is True]
    if (pod.get('kind') != 'Pod' or job.get('kind') != 'Job' or agent.get('kind') != 'Pod'
            or meta['namespace'] != 'codex-trial' or jm['namespace'] != 'codex-trial'
            or not meta['name'].startswith('access-dns-observed-diagnose-v1-')
            or len(owners) != 1 or owners[0]['uid'] != jm['uid'] or owners[0]['name'] != jm['name']
            or meta['uid'] != pin['podUid'] or meta['name'] != pin['podName']
            or meta['namespace'] != pin['namespace'] or pod['spec']['nodeName'] != pin['node']
            or pod['status']['podIP'] != pin['podIp']
            or am['uid'] != pin['agentUid'] or am['name'] != pin['agentName']
            or am['namespace'] != 'kube-system' or agent['spec']['nodeName'] != pin['node']
            or am['labels'].get('k8s-app') != 'cilium' or agent['status']['phase'] != 'Running'
            or job['spec']['activeDeadlineSeconds'] != 60 or job['spec']['backoffLimit'] != 0
            or job['status'].get('succeeded') != 1):
        raise ValueError()
    ipaddress.ip_address(pin['podIp'])
    start, end = job['status']['startTime'], job['status']['completionTime']
    if not 0 < (timestamp(end)-timestamp(start)).total_seconds() <= 60:
        raise ValueError()
    target = pin['namespace']+'/'+pin['podName']
    events = [{'type': 1}, {'type': 5}]  # v1.20.2 drop / policy-verdict; no trace/L7.
    filters = [
        {'source_pod': [target], 'source_ip': [pin['podIp']], 'destination_port': ['53'],
         'protocol': ['tcp', 'udp'], 'event_type': events},
        {'destination_pod': [target], 'destination_ip': [pin['podIp']], 'source_port': ['53'],
         'protocol': ['tcp', 'udp'], 'event_type': events},
    ]
    args = ['hubble', 'observe', '--server', SOCKET, '--timeout', '5s', '--since', start,
            '--until', end, '--output', 'json', '--field-mask', MASK]
    for value in filters:
        args += ['--allowlist', json.dumps(value, separators=(',', ':'))]
    return {'target': pin, 'jobUid': jm['uid'], 'since': start, 'until': end,
            'filters': filters, 'argv': args, 'maxBytes': MAX_BYTES, 'maxRecords': MAX_RECORDS}


def field(value, snake, camel):
    return value.get(snake, value.get(camel))


def verify_filters(scope, raw):
    if len(raw) >= MAX_BYTES:
        raise ValueError()
    entries, section = [], None
    for line in raw.decode().splitlines():
        if line == 'allowlist:':
            if section is not None:
                raise ValueError()
            section = 'allowlist'
        elif line == 'denylist: []':
            if section != 'allowlist':
                raise ValueError()
            section = 'denylist'
        else:
            match = re.fullmatch(r"\s*- '(.*)'", line)
            if section != 'allowlist' or not match:
                raise ValueError()
            entries.append(json.loads(match[1]))
    if entries != scope['filters']:
        raise ValueError()
    return {'exactRawFiltersMatch': True}


def summarize(scope, raw, exit_code):
    if exit_code != 0 or len(raw) >= MAX_BYTES:
        raise ValueError()
    lines = raw.splitlines()
    if not lines or len(lines) > MAX_RECORDS:
        raise ValueError()
    selected = scope['target']
    drops = denied = node_denied = 0
    for line in lines:
        record = json.loads(line)
        if 'lost_events' in record or 'lostEvents' in record:
            raise ValueError()
        flow = record['flow']
        node = field(flow, 'node_name', 'nodeName')
        if node not in (selected['node'], 'default/'+selected['node']):
            raise ValueError()
        moment = timestamp(flow['time'])
        if not timestamp(scope['since']) <= moment <= timestamp(scope['until']):
            raise ValueError()
        event = field(flow, 'event_type', 'eventType')['type']
        if 'l7' in flow or type(event) is not int or event not in (1, 5):
            raise ValueError()
        l4 = flow['l4']
        if len(l4) != 1 or next(iter(l4)) not in ('TCP', 'UDP'):
            raise ValueError()
        ports = next(iter(l4.values()))
        address = flow['IP']
        source, dest = flow['source'], flow['destination']
        outbound = (source.get('namespace') == selected['namespace']
                    and field(source, 'pod_name', 'podName') == selected['podName']
                    and address.get('source') == selected['podIp']
                    and field(ports, 'destination_port', 'destinationPort') == 53)
        inbound = (dest.get('namespace') == selected['namespace']
                   and field(dest, 'pod_name', 'podName') == selected['podName']
                   and address.get('destination') == selected['podIp']
                   and field(ports, 'source_port', 'sourcePort') == 53)
        if not outbound and not inbound:
            raise ValueError()
        verdict = flow['verdict']
        if verdict not in ('DROPPED', 'FORWARDED', 'AUDIT', 'REDIRECTED', 'ERROR'):
            raise ValueError()
        reason = field(flow, 'drop_reason_desc', 'dropReasonDesc')
        is_denied = verdict == 'DROPPED' and reason in ('POLICY_DENIED', 'POLICY_DENY')
        policies = (field(flow, 'egress_denied_by', 'egressDeniedBy') or []) + (field(flow, 'ingress_denied_by', 'ingressDeniedBy') or [])
        drops += verdict == 'DROPPED'
        denied += is_denied
        node_denied += is_denied and any(p.get('name') == 'anxious-lookout-node-protection'
                                        and p.get('kind') == 'CiliumClusterwideNetworkPolicy'
                                        and not p.get('namespace') for p in policies)
    return {'matchingRecords': len(lines), 'droppedRecords': drops, 'policyDeniedRecords': denied,
            'namedNodePolicyDeniedRecords': node_denied, 'dnsCause': 'unknown'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True)
    parser.add_argument('--pin'); parser.add_argument('--pod'); parser.add_argument('--job'); parser.add_argument('--agent')
    parser.add_argument('--response'); parser.add_argument('--exit-code', type=int)
    parser.add_argument('--raw-filters')
    args = parser.parse_args()
    try:
        if args.raw_filters:
            with open(args.raw_filters, 'rb') as source:
                raw = source.read(MAX_BYTES)
            result = verify_filters(json.loads(Path(args.plan).read_text()), raw)
        elif args.response:
            with open(args.response, 'rb') as source:
                raw = source.read(MAX_BYTES)
            result = summarize(json.loads(Path(args.plan).read_text()), raw, args.exit_code)
        else:
            value = plan(*[json.loads(Path(p).read_text()) for p in (args.pin, args.pod, args.job, args.agent)])
            with os.fdopen(os.open(args.plan, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600), 'w') as target:
                json.dump(value, target)
            result = {'historicalScopeCreated': True}
        print(json.dumps(result))
    except Exception:
        parser.exit(1, 'Historical DNS scope unknown/refused; private identities are not printed\n')
