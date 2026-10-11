"""DN11B bounded static DNS TPROXY/FIB metadata; no live calls in the sanitizer."""
import argparse
import ipaddress
import json
from pathlib import Path
import re
import shlex

from access_config_capture import (MAX_BYTES, agent_tag, child_valid, load, read_channels, write_private)

CHAIN = 'CILIUM_PRE_mangle'
COMMENT = 'cilium: TPROXY to host cilium-dns-egress proxy'
OPTIONS = dict(EnableIPv4=True, EnableBPFTProxy=False, InstallIptRules=True, EnableEndpointRoutes=False)


def classification_valid(value):
    return (isinstance(value, dict) and set(value) == {'configurationClassified', 'contextMatched',
            'options', 'nextReadCandidate', 'trafficConclusion'}
            and value['configurationClassified'] is True and value['contextMatched'] is True
            and value['nextReadCandidate'] == 'iptables_tproxy' and value['trafficConclusion'] == 'unknown'
            and set(value['options']) == set(OPTIONS)
            and all(value['options'][k] is v for k, v in OPTIONS.items()))


def capability_valid(value, context):
    return (isinstance(value, dict) and set(value) == {'capabilitySupported', 'byteOrder', 'iptablesMode',
            'contextTag', 'trafficConclusion'} and value['capabilitySupported'] is True
            and value['byteOrder'] == 'little' and value['iptablesMode'] in ('legacy', 'nf_tables')
            and value['contextTag'] == context and value['trafficConclusion'] == 'unknown')


def capability(raw):
    lines = raw.decode('utf-8', errors='strict').splitlines()
    if len(lines) != 3 or lines[0] not in ('x86_64', 'aarch64'):
        raise ValueError()
    iptables = re.fullmatch(r'iptables v1\.8\.[0-9]+ \((legacy|nf_tables)\)', lines[1])
    ip = re.fullmatch(r'ip utility, iproute2-6\.([0-9]+)\.[0-9]+(?:, libbpf [0-9.]+)?', lines[2])
    if not iptables or not ip or int(ip[1]) < 1:
        raise ValueError()
    return {'capabilitySupported': True, 'byteOrder': 'little', 'iptablesMode': iptables[1]}


def mark(raw):
    if not isinstance(raw, str) or not re.fullmatch(r'0x[0-9a-fA-F]{1,8}(?:/0x[0-9a-fA-F]{1,8})?', raw):
        raise ValueError()
    value, separator, mask = raw.partition('/')
    return int(value, 16), int(mask, 16) if separator else 0xffffffff


def dns_rules(raw):
    text = raw.decode('utf-8', errors='strict')
    lines = text.splitlines()
    if not 1 <= len(lines) <= 256:
        raise ValueError()
    rows = {'tcp': [], 'udp': []}
    for index, line in enumerate(lines):
        if not line or len(line) > 4096 or any(ord(v) < 32 and v != '\t' for v in line):
            raise ValueError()
        tokens = shlex.split(line, comments=False)
        if index == 0:
            if tokens != ['-N', CHAIN]: raise ValueError()
            continue
        if (len(tokens) < 4 or tokens[:2] != ['-A', CHAIN] or ('-j' not in tokens and '-g' not in tokens)):
            raise ValueError()
        if 'cilium-dns-egress' not in line:
            # Non-DNS rows stay in transient memory; only their basic -S framing is inspected.
            continue
        fields, modules = {}, []
        rest = tokens[2:]
        if len(rest) % 2:
            raise ValueError()
        for flag, value in zip(rest[::2], rest[1::2]):
            if flag == '-m':
                modules.append(value)
            elif flag in ('-p', '--mark', '--comment', '-j', '--on-ip', '--on-port', '--tproxy-mark') and flag not in fields:
                fields[flag] = value
            else:
                raise ValueError()
        if (set(fields) != {'-p', '--mark', '--comment', '-j', '--on-ip', '--on-port', '--tproxy-mark'}
                or fields['--comment'] != COMMENT or fields['-p'] not in rows or fields['-j'] != 'TPROXY'
                or sorted(modules) not in (['comment', 'mark'], sorted(['comment', 'mark', fields['-p']]))):
            raise ValueError()
        port_raw = fields['--on-port']
        if not re.fullmatch('[1-9][0-9]{0,4}', port_raw) or int(port_raw) > 65535:
            raise ValueError()
        port = int(port_raw)
        address = ipaddress.IPv4Address(fields['--on-ip'])
        encoded_port = int.from_bytes(port.to_bytes(2, 'big'), 'little')
        packet_mark, packet_mask = mark(fields['--mark'])
        proxy_mark, proxy_mask = mark(fields['--tproxy-mark'])
        rows[fields['-p']].append((port, packet_mark == (encoded_port << 16 | 0x200) and packet_mask == 0xffffffff,
                                  str(address) == '127.0.0.1', proxy_mark == 0x200 and proxy_mask == 0xffffffff))
    single = all(len(v) == 1 for v in rows.values())
    same_port = single and rows['tcp'][0][0] == rows['udp'][0][0]
    result = {p+'RuleCount': len(v) for p, v in rows.items()}
    result.update(singleDnsRulePair=single, tcpUdpCommonPort=same_port)
    for p, values in rows.items():
        result[p+'MarkMatchesPort'] = values[0][1] if len(values) == 1 else 'unknown'
        result[p+'TargetsLocalhost'] = values[0][2] if len(values) == 1 else 'unknown'
        result[p+'SetsToProxyMark'] = values[0][3] if len(values) == 1 else 'unknown'
    result['status'] = ('absent' if all(not v for v in rows.values()) else 'supported_match'
                        if same_port and all(all(v[1:]) for values in rows.values() for v in values) else 'mismatch')
    return result


def proxy_route(raw):
    value = load(raw)
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise ValueError()
    row = value[0]
    # Known simple FIB route only; cache/multipath/encap/rules-with-selectors are not a default proxy route.
    if (not set(row).issubset({'type', 'dst', 'table', 'dev', 'protocol', 'scope', 'prefsrc', 'flags', 'metric'})
            or not {'dst', 'dev', 'table'}.issubset(row) or not isinstance(row['dst'], str)
            or not isinstance(row['dev'], str) or not row['dev'] or len(row['dev']) > 64):
        raise ValueError()
    if row['dst'] not in ('default', '0.0.0.0/0'):
        ipaddress.IPv4Network(row['dst'], strict=False)
    table = row.get('table')
    if table is not None and (type(table) not in (str, int) or isinstance(table, str) and not re.fullmatch('[0-9]{1,10}', table)):
        raise ValueError()
    for key in ('protocol', 'scope', 'prefsrc', 'type'):
        if key in row and not isinstance(row[key], str):
            raise ValueError()
    # iproute2 print_route omits the default RTN_UNICAST type without details.
    # Only these simple route types are supported; unknown types are not mismatches.
    route_type = row.get('type', 'unicast')
    if route_type not in ('local', 'unicast'):
        raise ValueError()
    if 'prefsrc' in row: ipaddress.IPv4Address(row['prefsrc'])
    if 'metric' in row and (type(row['metric']) is not int or not 0 <= row['metric'] <= 2**32-1):
        raise ValueError()
    if 'flags' in row and (not isinstance(row['flags'], list) or row['flags']):
        raise ValueError()
    matched = (route_type == 'local' and row['dst'] in ('default', '0.0.0.0/0')
               and table in (2004, '2004') and row['dev'] == 'lo')
    return {'status': 'supported_match' if matched else 'mismatch', 'markedFibLookupUsesLocalDefault': matched,
            'usesProxyTable': table in (2004, '2004'), 'usesLoopbackDevice': row['dev'] == 'lo',
            'priorityRuleVerified': False, 'flowConclusion': 'unknown'}


def sanitize(stdout, stderr, mode, nonce, context, transport_exit, complete=True, cap=None):
    receipt = {'mode': mode, 'contextTag': context, 'complete': False, 'observation': {'status': 'unknown'}}
    try:
        if mode not in ('capability', 'dns-rules', 'proxy-route') or not re.fullmatch('[0-9a-f]{32}', nonce):
            raise ValueError()
        first, separator, rest = stderr.partition(b'\n')
        prefix = ('DN11:'+nonce+':').encode()
        if not separator or len(first) > 512 or not first.startswith(prefix):
            raise ValueError()
        receipt['child'] = child_valid(load(first[len(prefix):]))
        if (not complete or type(transport_exit) is not int or transport_exit != 0 or rest
                or len(stdout)+len(stderr) >= MAX_BYTES or not stdout.endswith(b'\n')):
            raise ValueError()
        if mode != 'capability' and not capability_valid(cap, context):
            raise ValueError()
        receipt['observation'] = capability(stdout) if mode == 'capability' else dns_rules(stdout) if mode == 'dns-rules' else proxy_route(stdout)
        receipt['complete'] = True
    except Exception:
        receipt['observation'] = {'status': 'unknown'}
    return receipt


def observation_valid(mode, value):
    if not isinstance(value, dict): return False
    if mode == 'capability':
        return (set(value) == {'capabilitySupported', 'byteOrder', 'iptablesMode'}
                and value['capabilitySupported'] is True and value['byteOrder'] == 'little'
                and value['iptablesMode'] in ('legacy', 'nf_tables'))
    if mode == 'dns-rules':
        count_keys = ['tcpRuleCount', 'udpRuleCount']
        boolean_keys = ['singleDnsRulePair', 'tcpUdpCommonPort']
        flag_keys = [p+suffix for p in ('tcp', 'udp') for suffix in ('MarkMatchesPort', 'TargetsLocalhost', 'SetsToProxyMark')]
        return (set(value) == set(count_keys+boolean_keys+flag_keys+['status'])
                and all(type(value[k]) is int and 0 <= value[k] <= 256 for k in count_keys)
                and all(type(value[k]) is bool for k in boolean_keys)
                and all(type(value[k]) is bool or value[k] == 'unknown' for k in flag_keys)
                and value['status'] in ('supported_match', 'absent', 'mismatch'))
    if mode == 'proxy-route':
        flags = ['markedFibLookupUsesLocalDefault', 'usesProxyTable', 'usesLoopbackDevice']
        return (set(value) == set(flags+['status', 'priorityRuleVerified', 'flowConclusion'])
                and all(type(value[k]) is bool for k in flags) and value['priorityRuleVerified'] is False
                and value['flowConclusion'] == 'unknown' and value['status'] in ('supported_match', 'mismatch'))
    return False


def finalize(receipt, proof, before, after, exit_code, proof_exit, cap=None):
    child = child_valid(receipt.get('child'))
    context = agent_tag(before)
    if (type(exit_code) is not int or exit_code != 0 or type(proof_exit) is not int or proof_exit != 0
            or receipt.get('complete') is not True or receipt['contextTag'] != context or agent_tag(after) != context
            or proof != dict(child, childGone=True, groupGone=True)
            or any(type(proof.get(k)) is not int for k in child)
            or proof.get('childGone') is not True or proof.get('groupGone') is not True):
        raise ValueError()
    if receipt['mode'] != 'capability' and not capability_valid(cap, context):
        raise ValueError()
    if not observation_valid(receipt['mode'], receipt['observation']):
        raise ValueError()
    return dict(receipt['observation'], contextTag=context, trafficConclusion='unknown')


def combine(rules, route, cap, context):
    if not capability_valid(cap, context): raise ValueError()
    observations = []
    for mode, value in (('dns-rules', rules), ('proxy-route', route)):
        if value.get('contextTag') != context or value.get('trafficConclusion') != 'unknown': raise ValueError()
        selected = {k: v for k, v in value.items() if k not in ('contextTag', 'trafficConclusion')}
        if not observation_valid(mode, selected): raise ValueError()
        observations.append(selected)
    dns, fib = observations
    status = ('supported_match' if dns['status'] == fib['status'] == 'supported_match'
              else 'absent' if dns['status'] == 'absent' else 'mismatch')
    return {'staticConfigurationStatus': status, 'dnsRuleStatus': dns['status'], 'markedFibStatus': fib['status'],
            'contextMatched': True, 'endpointAllocationLink': 'unknown', 'listenerLink': 'unknown',
            'priority9Verified': False, 'allDestinationsVerified': False, 'trafficConclusion': 'unknown'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('before', 'anchor', 'classification'): parser.add_argument('--'+name, required=True)
    parser.add_argument('--receipt')
    parser.add_argument('--rules-final'); parser.add_argument('--route-final')
    for name in ('channel-dir', 'nonce', 'agent-name', 'after', 'proof', 'capability'): parser.add_argument('--'+name)
    parser.add_argument('--mode', choices=('capability', 'dns-rules', 'proxy-route'))
    parser.add_argument('--transport-exit-code', type=int); parser.add_argument('--proof-exit-code', type=int)
    args = parser.parse_args()
    try:
        read = lambda path: load(Path(path).read_bytes())
        context = agent_tag(read(args.before))
        if context != agent_tag(read(args.anchor)) or not classification_valid(read(args.classification)):
            raise ValueError()
        cap = read(args.capability) if args.capability else None
        if args.rules_final or args.route_final:
            result = combine(read(args.rules_final), read(args.route_final), cap, context)
        elif args.proof:
            result = finalize(read(args.receipt), read(args.proof), read(args.before), read(args.after),
                              args.transport_exit_code, args.proof_exit_code, cap)
        else:
            if (read(args.before)['metadata']['name'] != args.agent_name
                    or args.mode != 'capability' and not capability_valid(cap, context)):
                raise ValueError()
            channels, code, complete = read_channels(Path(args.channel_dir))
            receipt = sanitize(bytes(channels['stdout']), bytes(channels['stderr']), args.mode, args.nonce,
                               context, code, complete, cap)
            write_private(args.receipt, receipt)
            result = {'sanitizedReceiptWritten': True, 'captureComplete': receipt['complete']}
        print(json.dumps(result))
    except Exception:
        parser.exit(1, 'DN11B metadata unknown/refused; raw rules/routes are not printed\n')
