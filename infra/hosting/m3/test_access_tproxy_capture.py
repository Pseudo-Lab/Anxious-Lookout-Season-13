import copy
import json
import os
from pathlib import Path
import subprocess

import pytest

from access_config_capture import agent_tag, load
from access_tproxy_capture import (CHAIN, COMMENT, OPTIONS, capability, classification_valid,
                                  combine, dns_rules, finalize, proxy_route, sanitize)
from test_access_config_capture import agent, CHILD, MARKER, NONCE

CAP = b'x86_64\niptables v1.8.10 (nf_tables)\nip utility, iproute2-6.8.0, libbpf 1.4.0\n'


def cap():
    return dict(capability(CAP), contextTag=agent_tag(agent()), trafficConclusion='unknown')


def rules(port=35555):
    encoded = int.from_bytes(port.to_bytes(2, 'big'), 'little') << 16 | 0x200
    rows = [f'-N {CHAIN}']
    for protocol in ('tcp', 'udp'):
        rows.append(f'-A {CHAIN} -p {protocol} -m mark --mark {encoded:#x} -m comment --comment "{COMMENT}" '
                    f'-j TPROXY --on-port {port} --on-ip 127.0.0.1 --tproxy-mark 0x200/0xffffffff')
    rows.append(f'-A {CHAIN} -m comment --comment "PRIVATE_OTHER_RULE_SENTINEL" -j ACCEPT')
    return ('\n'.join(rows)+'\n').encode()


def route(**changes):
    return (json.dumps([dict(type='local', dst='default', dev='lo', table='2004',
                            protocol='cilium', scope='host', flags=[], **changes)])+'\n').encode()


def receipt(mode='dns-rules', raw=None, stderr=MARKER, code=0):
    raw = raw if raw is not None else CAP if mode == 'capability' else route() if mode == 'proxy-route' else rules()
    return sanitize(raw, stderr, mode, NONCE, agent_tag(agent()), code, cap=cap() if mode != 'capability' else None)


def test_current_rule_pair_internal_port_and_network_mark_match_without_raw_persistence():
    value = receipt()
    final = finalize(value, dict(CHILD, childGone=True, groupGone=True), agent(), agent(), 0, 0, cap())
    assert value['complete'] and final['status'] == 'supported_match' and final['tcpUdpCommonPort']
    assert final['tcpMarkMatchesPort'] and final['udpTargetsLocalhost']
    assert all(v not in json.dumps(value)+json.dumps(final) for v in ('35555', '8AE3', '127.0.0.1', 'PRIVATE'))
    # A fresh rule port is evaluated on its own, never from DN10 historical allocation.
    assert receipt(raw=rules(40001))['observation']['status'] == 'supported_match'


@pytest.mark.parametrize('variant', ['empty_pair', 'one_protocol', 'duplicate', 'port', 'mark', 'mask', 'target', 'rewrite'])
def test_supported_absence_or_mismatch_is_distinct_from_unknown(variant):
    raw = rules(); lines = raw.decode().splitlines()
    if variant == 'empty_pair': lines = lines[:1]
    elif variant == 'one_protocol': del lines[2]
    elif variant == 'duplicate': lines.append(lines[1])
    elif variant == 'port': lines[2] = lines[2].replace('--on-port 35555', '--on-port 40001')
    elif variant == 'mark': lines[1] = lines[1].replace('0xe38a0200', '0x8ae30200')
    elif variant == 'mask': lines[1] = lines[1].replace('0xe38a0200', '0xe38a0200/0xffff')
    elif variant == 'target': lines[1] = lines[1].replace('127.0.0.1', '127.0.0.2')
    elif variant == 'rewrite': lines[1] = lines[1].replace('--tproxy-mark 0x200/', '--tproxy-mark 0x300/')
    value = receipt(raw=('\n'.join(lines)+'\n').encode())
    assert value['complete'] and value['observation']['status'] == ('absent' if variant == 'empty_pair' else 'mismatch')


@pytest.mark.parametrize('variant', ['bad_header', 'no_header', 'unsupported_row', 'unbalanced_quote',
    'duplicate_field', 'missing_field', 'noncanonical_port', 'malformed_mark', 'foreign_dns_comment',
    'partial', 'no_newline', 'stderr', 'nonzero'])
def test_incomplete_or_unsupported_chain_never_means_absence(variant):
    raw, stderr, code = rules(), MARKER, 0
    if variant == 'bad_header': raw = raw.replace(b'-N CILIUM_PRE_mangle', b'-N OTHER_CHAIN')
    elif variant == 'no_header': raw = raw.split(b'\n', 1)[1]
    elif variant == 'unsupported_row': raw += b'malformed unsupported row\n'
    elif variant == 'unbalanced_quote': raw += b'-A CILIUM_PRE_mangle --comment "PRIVATE_SENTINEL -j ACCEPT\n'
    elif variant == 'duplicate_field': raw = raw.replace(b'-p tcp', b'-p tcp -p tcp')
    elif variant == 'missing_field': raw = raw.replace(b'--on-ip 127.0.0.1 ', b'')
    elif variant == 'noncanonical_port': raw = raw.replace(b'--on-port 35555', b'--on-port 035555')
    elif variant == 'malformed_mark': raw = raw.replace(b'--mark 0xe38a0200', b'--mark PRIVATE_MARK_SENTINEL')
    elif variant == 'foreign_dns_comment': raw = raw.replace(b'cilium-dns-egress proxy', b'cilium-dns-egress-other proxy')
    elif variant == 'partial': raw = raw[:100]
    elif variant == 'no_newline': raw = raw.rstrip()
    elif variant == 'stderr': stderr += b'PRIVATE_ERROR_SENTINEL\n'
    elif variant == 'nonzero': code = 1
    value = receipt(raw=raw, stderr=stderr, code=code)
    assert not value['complete'] and value['observation'] == {'status': 'unknown'}
    assert 'PRIVATE' not in json.dumps(value)


def test_single_fib_result_can_match_without_priority_or_packet_claims():
    value = receipt('proxy-route')
    assert value['complete'] and value['observation']['status'] == 'supported_match'
    assert value['observation']['priorityRuleVerified'] is False and value['observation']['flowConclusion'] == 'unknown'
    row = json.loads(route())[0]; row['table'] = 2005
    assert receipt('proxy-route', raw=(json.dumps([row])+'\n').encode())['observation']['status'] == 'mismatch'


@pytest.mark.parametrize('explicit_type', [True, False])
def test_supported_unicast_route_is_mismatch_including_default_type_omission(explicit_type):
    row = json.loads(route())[0]
    if explicit_type: row['type'] = 'unicast'
    else: del row['type']
    value = receipt('proxy-route', raw=(json.dumps([row])+'\n').encode())
    assert value['complete'] and value['observation']['status'] == 'mismatch'
    assert value['observation']['markedFibLookupUsesLocalDefault'] is False


@pytest.mark.parametrize('route_type', ['unsupported-route-type', '', 'PRIVATE_TYPE_SENTINEL', None, 1, False])
def test_unsupported_route_types_are_unknown_not_supported_mismatch(route_type):
    row = json.loads(route())[0]; row['type'] = route_type
    value = receipt('proxy-route', raw=(json.dumps([row])+'\n').encode())
    assert value['complete'] is False and value['observation'] == {'status': 'unknown'}
    assert 'PRIVATE' not in json.dumps(value)


@pytest.mark.parametrize('variant', ['missing_table', 'duplicate', 'multiple', 'partial', 'cache', 'bool_table', 'sentinel_device'])
def test_unsupported_fib_schema_is_unknown(variant):
    row = json.loads(route())[0]
    if variant == 'missing_table': del row['table']
    elif variant == 'cache': row['cache'] = ['local']
    elif variant == 'bool_table': row['table'] = True
    elif variant == 'sentinel_device': row['dev'] = 123
    raw = (json.dumps([row, row] if variant == 'multiple' else [row])+'\n').encode()
    if variant == 'duplicate': raw = raw.replace(b'"table": "2004"', b'"table":"PRIVATE_SENTINEL", "table":"2004"')
    if variant == 'partial': raw = raw[:-8]
    value = receipt('proxy-route', raw=raw)
    assert not value['complete'] and value['observation']['status'] == 'unknown'
    assert 'PRIVATE' not in json.dumps(value)


@pytest.mark.parametrize('raw', [b'aarch64_be\niptables v1.8.10 (nf_tables)\nip utility, iproute2-6.8.0\n',
    b'x86_64\niptables v1.8.10 (unknown)\nip utility, iproute2-6.8.0\n',
    b'x86_64\niptables v1.8.10 (legacy)\nip utility, iproute2-5.0.0\n'])
def test_capability_refuses_untraced_arch_or_tool_families(raw):
    assert not receipt('capability', raw=raw)['complete']


@pytest.mark.parametrize('variant', ['proof', 'transport', 'uid', 'cap_context'])
def test_each_observation_requires_its_own_proof_context_and_capability(variant):
    before, after, proof, capability_value, code = agent(), agent(), dict(CHILD, childGone=True, groupGone=True), cap(), 0
    if variant == 'proof': proof['startTicks'] += 1
    elif variant == 'transport': code = 124
    elif variant == 'uid': after['metadata']['uid'] += '-replacement'
    elif variant == 'cap_context': capability_value['contextTag'] = 'b'*64
    with pytest.raises(ValueError): finalize(receipt(), proof, before, after, code, 0, capability_value)


def test_combined_static_result_requires_both_accepted_observations():
    proof = dict(CHILD, childGone=True, groupGone=True)
    dns = finalize(receipt(), proof, agent(), agent(), 0, 0, cap())
    fib = finalize(receipt('proxy-route'), proof, agent(), agent(), 0, 0, cap())
    value = combine(dns, fib, cap(), agent_tag(agent()))
    assert value['staticConfigurationStatus'] == 'supported_match'
    assert value['endpointAllocationLink'] == value['listenerLink'] == value['trafficConclusion'] == 'unknown'
    assert value['priority9Verified'] is value['allDestinationsVerified'] is False
    with pytest.raises(ValueError): combine(dns, {}, cap(), agent_tag(agent()))
    foreign = dict(fib, contextTag='b'*64)
    with pytest.raises(ValueError): combine(dns, foreign, cap(), agent_tag(agent()))


def test_finalizer_rejects_unrecognized_receipt_fields_and_invalid_classification():
    value = receipt(); value['observation']['PRIVATE_SENTINEL'] = 'must not emit'
    with pytest.raises(ValueError): finalize(value, dict(CHILD, childGone=True, groupGone=True), agent(), agent(), 0, 0, cap())
    classification = dict(configurationClassified=True, contextMatched=True, options=OPTIONS,
                          nextReadCandidate='iptables_tproxy', trafficConclusion='unknown')
    assert classification_valid(classification)
    bad = copy.deepcopy(classification); bad['options']['EnableBPFTProxy'] = 0
    assert not classification_valid(bad)


def test_cli_gate_stops_before_reader_or_transport_on_missing_capability(tmp_path):
    before = tmp_path/'before.json'; before.write_text(json.dumps(agent()))
    classification = tmp_path/'classification.json'
    classification.write_text(json.dumps(dict(configurationClassified=True, contextMatched=True, options=OPTIONS,
                          nextReadCandidate='iptables_tproxy', trafficConclusion='unknown')))
    p = subprocess.run(['python', '-B', str(Path(__file__).with_name('access_tproxy_capture.py')),
        '--before', str(before), '--anchor', str(before), '--classification', str(classification),
        '--channel-dir', str(tmp_path), '--agent-name', agent()['metadata']['name'], '--mode', 'dns-rules',
        '--nonce', NONCE, '--receipt', str(tmp_path/'receipt.json')], capture_output=True, timeout=2)
    assert p.returncode == 1 and not (tmp_path/'reader.ready').exists() and not (tmp_path/'receipt.json').exists()


@pytest.mark.parametrize('variant', ['valid', 'stderr', 'foreign_capability'])
def test_new_host_wrapper_runs_real_reader_with_mock_transport_only(tmp_path, variant):
    inputs = tmp_path/'inputs'; inputs.mkdir(mode=0o700)
    output = tmp_path/'output'; output.mkdir(mode=0o700)
    for name in ('before', 'anchor'): (inputs/(name+'.json')).write_text(json.dumps(agent()))
    classification = dict(configurationClassified=True, contextMatched=True, options=OPTIONS,
                          nextReadCandidate='iptables_tproxy', trafficConclusion='unknown')
    (inputs/'classification.json').write_text(json.dumps(classification))
    capability_value = cap()
    if variant == 'foreign_capability': capability_value['contextTag'] = 'b'*64
    (inputs/'capability.json').write_text(json.dumps(capability_value))
    docker = tmp_path/'docker'
    docker.write_text('''#!/usr/bin/env python
import os, sys
args = sys.argv[1:]
assert '--log-driver=none' in args and args[args.index('--network')+1] == 'none'
mounts = {}
for i, value in enumerate(args):
    if value == '--mount':
        parts = dict(v.split('=',1) for v in args[i+1].split(',') if '=' in v)
        mounts[parts['dst']] = parts['src']
args = args[args.index('--entrypoint')+3:]
def mapped(arg):
    for target, source in mounts.items():
        if arg == target or arg.startswith(target+'/'): return source+arg[len(target):]
    return arg
assert '/checks/access_tproxy_capture.py' in args
os.execv(sys.executable, [sys.executable]+[mapped(arg) for arg in args])
''')
    docker.chmod(0o700)
    kubectl = tmp_path/'kubectl'
    kubectl.write_text('''#!/usr/bin/env python
import os, sys
from pathlib import Path
from test_access_tproxy_capture import rules
from test_access_config_capture import CHILD
import json
args = sys.argv[1:]
assert args[8:16] == ['timeout','--signal=TERM','--kill-after=1s','6s','sh','-s','--','dns-rules']
assert len(args) == 17
assert 'exec iptables -w 2 -t mangle -S CILIUM_PRE_mangle' in sys.stdin.read()
Path(os.environ['TEST_CALLED']).write_text('called')
sys.stderr.write('DN11:'+args[-1]+':'+json.dumps(CHILD)+'\\n')
if os.environ['TEST_VARIANT'] == 'stderr': sys.stderr.write('PRIVATE_ERROR_SENTINEL\\n')
sys.stdout.buffer.write(rules())
''')
    kubectl.chmod(0o700)
    env = dict(os.environ, DN11_DOCKER=str(docker), DN11_KUBECTL=str(kubectl), DN11_IMAGE='sha256:'+'a'*64,
        DN11_SOURCE_DIR=str(Path(__file__).parent), DN11_INPUT_DIR=str(inputs), DN11_OUTPUT_DIR=str(output),
        DN11_AGENT=agent()['metadata']['name'], DN11_MODE='dns-rules', TEST_VARIANT=variant,
        TEST_CALLED=str(tmp_path/'called'), PYTHONPATH=str(Path(__file__).parent)+':'+os.environ.get('PYTHONPATH', ''))
    p = subprocess.run(['bash', str(Path(__file__).with_name('capture_access_tproxy.sh'))], env=env,
                       capture_output=True, timeout=5)
    assert p.returncode == (0 if variant == 'valid' else 1)
    assert (tmp_path/'called').exists() is (variant != 'foreign_capability')
    assert not (output/'stdout').exists() and not (output/'stderr').exists()
    assert 'PRIVATE' not in p.stdout.decode()+p.stderr.decode()
    assert all('PRIVATE' not in file.read_text() for file in output.iterdir() if file.is_file())
    if variant != 'foreign_capability':
        value = load((output/'receipt.json').read_bytes())
        assert value['complete'] is (variant == 'valid')


@pytest.mark.parametrize('mode,expected', [('dns-rules', ['iptables', '-w', '2', '-t', 'mangle', '-S', CHAIN]),
    ('proxy-route', ['ip', '-j', '-4', 'route', 'get', 'fibmatch', '198.18.0.1', 'mark', '0x200']),
    ('capability', ['uname', '-m', 'iptables', '--version', 'ip', '-Version'])])
def test_remote_exact_argv_and_new_child_proof(tmp_path, mode, expected):
    for name in ('uname', 'ip', 'iptables'):
        fake = tmp_path/name
        fake.write_text('#!/bin/sh\nprintf "%s\\n" "'+name+'" "$@"\n'); fake.chmod(0o700)
    comm = tmp_path/'comm'; comm.write_text('cilium-agent\n')
    source = Path(__file__).with_name('access_tproxy_remote.sh').read_text().replace('/proc/1/comm', str(comm))
    script = tmp_path/'remote'; script.write_text(source)
    env = dict(os.environ, PATH=str(tmp_path)+':'+os.environ['PATH'])
    p = subprocess.run(['timeout', '--signal=TERM', '--kill-after=1s', '2s', 'sh', str(script), mode, NONCE],
                       env=env, capture_output=True, timeout=3)
    assert p.returncode == 0 and p.stdout.decode().splitlines() == expected
    child = load(p.stderr.partition(b':')[2].partition(b':')[2])
    proof = subprocess.run(['sh', str(Path(__file__).with_name('access_drop_remote.sh')), 'check',
        *[str(child[k]) for k in ('pid', 'parentPid', 'group', 'startTicks')]], capture_output=True, timeout=2)
    assert proof.returncode == 0 and load(proof.stdout) == dict(child, childGone=True, groupGone=True)
    for extra in ('--all', 'rule', 'flush'):
        p = subprocess.run(['sh', str(script), mode, NONCE, extra], env=env, capture_output=True, timeout=2)
        assert p.returncode == 2 and not p.stdout and not p.stderr
