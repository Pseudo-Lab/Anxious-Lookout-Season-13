"""Bounded stdin-only DNS event sanitizer; no raw stream persistence or live calls."""
import argparse
import datetime as dt
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import sys
import time

from access_endpoint_pin import verify

MAX_BYTES, MAX_LINE, MAX_RECORDS = 262144, 16384, 256


def integer(value, minimum=0, maximum=2**64-1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError()
    return value


def tag(pin):
    return hashlib.sha256(json.dumps(pin, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def selected_drop(value, pin):
    if value.get('type') != 'drop':
        raise ValueError()
    source, dest = integer(value['source'], maximum=65535), integer(value['dstID'], maximum=65535)
    if pin['endpointId'] not in (source, dest):
        raise ValueError()
    summary = value['summary']
    # Never keep raw layer strings, DNS payload, overlays or arbitrary JSON keys.
    if 'tunnel' in summary or ('tcp' in summary) == ('udp' in summary):
        raise ValueError()
    src_ip, dst_ip = str(ipaddress.ip_address(summary['l3']['src'])), str(ipaddress.ip_address(summary['l3']['dst']))
    ports = summary['l4']
    for name in ('src', 'dst'):
        raw = ports[name]
        if not isinstance(raw, str) or not re.fullmatch(r'[0-9]{1,5}', raw):
            raise ValueError()
    src_port, dst_port = integer(int(ports['src']), 1, 65535), integer(int(ports['dst']), 1, 65535)
    if src_ip == pin['podIp'] and dst_port == 53:
        direction = 'outbound'
    elif dst_ip == pin['podIp'] and src_port == 53:
        direction = 'inbound'
    else:
        raise ValueError()
    reason = value['reason']
    if not isinstance(reason, str) or not 1 <= len(reason) <= 128:
        raise ValueError()
    match = re.fullmatch(r'(Policy denied(?: by denylist)?)(?:, -?[0-9]{1,3})?', reason)
    category = ('explicit_deny' if match and match[1].endswith('denylist') else
                'policy_denied' if match else 'other_drop')
    return {'receivedAt': utc(), 'direction': direction, 'protocol': 'tcp' if 'tcp' in summary else 'udp',
            'reasonCategory': category, 'reasonDigest': hashlib.sha256(reason.encode()).hexdigest(),
            'sourceIdentity': integer(value['srcLabel'], maximum=2**32-1),
            'destinationIdentity': integer(value['dstLabel'], maximum=2**32-1),
            'file': integer(value['File'], maximum=255), 'line': integer(value['Line'], maximum=65535)}


def selected_dns(value, pin, identity):
    if (value.get('type') != 'logRecord' or value.get('l7Proto') != 'dns'
            or value.get('observationPoint') != 'Egress' or value.get('http') is not None
            or value.get('l7') is not None or value.get('flowType') not in ('Request', 'Response')
            or value.get('verdict') not in ('Forwarded', 'Denied', 'Error', 'Redirected')):
        raise ValueError()
    src, dst = integer(value['srcEpID'], maximum=65535), integer(value['dstEpID'], maximum=65535)
    if (src == pin['endpointId']) == (dst == pin['endpointId']):
        raise ValueError()
    side = 'src' if src == pin['endpointId'] else 'dst'
    if integer(value[side+'Identity'], 1, 2**32-1) != identity:
        raise ValueError()
    labels = value[side+'EpLabels']
    if not isinstance(labels, list) or not {'k8s:app=access-web-runner',
            'k8s:io.kubernetes.pod.namespace=codex-trial'}.issubset(labels):
        raise ValueError()
    dns = value['dns']
    if dns.get('ObservationSource') != 'proxy':
        raise ValueError()
    query = dns['Query']
    names = {'chatgpt.com': 'provider', 'api.m2-hosting.svc.cluster.local': 'callback'}
    if query.endswith('.'):
        query = query[:-1]
    if query not in names:
        raise ValueError()
    questions = dns['QTypes']
    if not isinstance(questions, list) or not 1 <= len(questions) <= 2 or any(type(q) is not int or q not in (1, 28) for q in questions):
        raise ValueError()
    rcode = integer(dns.get('RCode', 0), maximum=4095)  # v1.20.2 omits the integer zero.
    answers = dns.get('IPs', [])
    if not isinstance(answers, list) or len(answers) > 64:
        raise ValueError()
    for address in answers:
        if not isinstance(address, str):
            raise ValueError()
        ipaddress.ip_address(address)
    return {'receivedAt': utc(), 'target': names[query], 'flowType': value['flowType'],
            'verdict': value['verdict'], 'qTypes': questions,
            'rcode': rcode if value['flowType'] == 'Response' else None,
            'hasAnswers': bool(answers) if value['flowType'] == 'Response' else None}


def capture(stream, mode, pin=None, identity=None):
    begin = time.monotonic()
    receipt = {'mode': mode, 'startedAt': utc(), 'complete': False, 'events': []}
    if mode not in ('probe', 'observe', 'observe-dns'):
        raise ValueError()
    if mode != 'probe':
        integer(pin['endpointId'], 1, 65535)
        ipaddress.ip_address(pin['podIp'])
        receipt['pinTag'] = tag(pin)
    if mode == 'observe-dns':
        receipt['securityIdentity'] = integer(identity, 1, 2**32-1)
    size = count = 0
    try:
        while True:
            line = stream.readline(MAX_LINE+1)
            if not line:
                break
            size += len(line); count += 1
            if len(line) > MAX_LINE or size >= MAX_BYTES or count > MAX_RECORDS or not line.endswith(b'\n'):
                raise ValueError()
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError()
            if 'dn6Control' in value:
                if value.get('mode') != mode:
                    raise ValueError()
                if value['dn6Control'] == 'start':
                    if 'child' in receipt or 'timeoutExit' in receipt:
                        raise ValueError()
                    child = {name: integer(value[name], 1, 2**63-1) for name in ('pid', 'parentPid', 'group', 'startTicks')}
                    receipt['child'] = child  # Needed for exact post-check even on invalid data.
                    if min(child['pid'], child['parentPid'], child['group']) <= 1 or child['parentPid'] != child['group'] or child['pid'] == child['group']:
                        raise ValueError()
                    receipt['subscriptionStartedAt'] = utc()
                elif value['dn6Control'] == 'end':
                    if 'child' not in receipt or 'timeoutExit' in receipt:
                        raise ValueError()
                    code = integer(value['timeoutExit'])
                    if code not in (124, 137):
                        raise ValueError()
                    receipt['timeoutExit'] = code
                    receipt['subscriptionEndedAt'] = utc()
                else:
                    raise ValueError()
            else:
                if mode == 'probe' or 'child' not in receipt or 'timeoutExit' in receipt:
                    raise ValueError()
                receipt['events'].append(selected_dns(value, pin, identity) if mode == 'observe-dns' else selected_drop(value, pin))
        if 'child' not in receipt or 'timeoutExit' not in receipt or time.monotonic()-begin > 12:
            raise ValueError()
        receipt['complete'] = True
    except Exception:
        receipt['events'] = []
    receipt['endedAt'] = utc()
    return receipt


def finalize(receipt, proof, pin=None, pod=None, cep=None, agent=None, endpoint=None, transport_exit=0):
    expected = dict(receipt.get('child', {}), childGone=True, groupGone=True)
    if (transport_exit != 0 or receipt.get('complete') is not True or not receipt.get('child')
            or proof.get('childGone') is not True or proof.get('groupGone') is not True or proof != expected):
        raise ValueError()
    if receipt['mode'] == 'probe':
        if receipt['timeoutExit'] != 124:
            raise ValueError()
        return {'terminationProbePassed': True, 'killFallbackVerified': False}
    if receipt.get('pinTag') != tag(pin):
        raise ValueError()
    verify(pin, pod, cep, agent, endpoint)
    events = receipt['events']
    if receipt['mode'] == 'observe-dns':
        if receipt['securityIdentity'] != integer(cep['status']['identity']['id'], 1, 2**32-1):
            raise ValueError()
        return {'captureComplete': True, 'dnsRequests': sum(v['flowType'] == 'Request' for v in events),
                'dnsResponses': sum(v['flowType'] == 'Response' for v in events),
                'dnsProxyDenied': sum(v['verdict'] == 'Denied' for v in events),
                'dnsProxyErrors': sum(v['verdict'] == 'Error' for v in events), 'dnsCause': 'unknown',
                'noEventConclusion': 'unknown'}
    return {'captureComplete': True, 'matchingDnsDrops': len(events),
            'policyDeniedDrops': sum(v['reasonCategory'] in ('policy_denied', 'explicit_deny') for v in events),
            'dnsCause': 'unknown', 'noDropConclusion': 'unknown'}


def write_private(path, value):
    with os.fdopen(os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600), 'w') as target:
        json.dump(value, target)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('probe', 'observe', 'observe-dns')); parser.add_argument('--receipt', required=True)
    parser.add_argument('--proof'); parser.add_argument('--pin'); parser.add_argument('--pod')
    parser.add_argument('--cep'); parser.add_argument('--agent'); parser.add_argument('--endpoint')
    parser.add_argument('--transport-exit-code', type=int)
    args = parser.parse_args()
    try:
        read = lambda path: json.loads(Path(path).read_text())
        if args.proof:
            if args.transport_exit_code != 0:
                raise ValueError()
            result = finalize(read(args.receipt), read(args.proof), *[read(p) if p else None for p in
                              (args.pin, args.pod, args.cep, args.agent, args.endpoint)],
                              transport_exit=args.transport_exit_code)
        else:
            def expired(*_):
                raise TimeoutError()
            signal.signal(signal.SIGALRM, expired)
            signal.alarm(12)
            identity = None
            if args.mode == 'observe-dns':
                ep = read(args.endpoint)
                if not isinstance(ep, list) or len(ep) != 1:
                    raise ValueError()
                identity = ep[0]['status']['identity']['id']
            receipt = capture(sys.stdin.buffer, args.mode, read(args.pin) if args.pin else None, identity)
            signal.alarm(0)
            write_private(args.receipt, receipt)
            result = {'sanitizedReceiptWritten': True, 'captureComplete': receipt['complete']}
        print(json.dumps(result))
    except Exception:
        parser.exit(1, 'Bounded drop capture unknown/refused; raw data and identities are not printed\n')
