import copy
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from access_drop_capture import MAX_BYTES, MAX_LINE, capture, finalize, write_private
from access_endpoint_pin import pin
from test_access_endpoint_pin import metadata


def setup_values():
    pod, cep, agent = metadata()
    saved = pin(pod, cep, agent)
    endpoint = [{'id': saved['endpointId'], 'status': copy.deepcopy(cep['status'])}]
    return saved, pod, cep, agent, endpoint


def frames(mode='observe'):
    return ({'dn6Control': 'start', 'mode': mode, 'pid': 1234, 'parentPid': 1233, 'group': 1233, 'startTicks': 100},
            {'dn6Control': 'end', 'mode': mode, 'timeoutExit': 124})


def drop(inbound=False):
    return {'type': 'drop', 'source': 321, 'dstID': 0, 'reason': 'Policy denied by denylist',
            'srcLabel': 10, 'dstLabel': 1, 'File': 1, 'Line': 123,
            'summary': {'udp': 'unretained raw layer / fake-token-sensitive@example.test',
                        'l3': {'src': '127.0.0.3' if inbound else '127.0.0.2',
                               'dst': '127.0.0.2' if inbound else '127.0.0.3'},
                        'l4': {'src': '53' if inbound else '45678', 'dst': '45678' if inbound else '53'}}}


def stream(*values):
    return io.BytesIO(b''.join(json.dumps(v).encode()+b'\n' for v in values))


@pytest.mark.parametrize('inbound', [False, True])
def test_only_selected_dns_metadata_survives(inbound):
    values = setup_values()
    begin, end = frames()
    receipt = capture(stream(begin, drop(inbound), end), 'observe', values[0])
    result = finalize(receipt, dict(receipt['child'], childGone=True, groupGone=True), *values)
    assert result['matchingDnsDrops'] == result['policyDeniedDrops'] == 1
    assert result['dnsCause'] == 'unknown'
    encoded = json.dumps(receipt)
    for raw in ('fake-token', 'sensitive@example', 'raw layer', 'summary', '127.0.0.3'):
        assert raw not in encoded


@pytest.mark.parametrize('change', ['endpoint', 'ip', 'port', 'policy_verdict', 'mixed', 'tunnel', 'missing', 'boolean', 'long_reason'])
def test_foreign_or_unsupported_event_discards_prior_matches(change):
    value = drop()
    if change == 'endpoint': value['source'] = 322
    if change == 'ip': value['summary']['l3']['src'] = '127.0.0.4'
    if change == 'port': value['summary']['l4']['dst'] = '443'
    if change == 'policy_verdict': value['type'] = 'policy-verdict'
    if change == 'mixed': value['summary']['tcp'] = 'present'
    if change == 'tunnel': value['summary']['tunnel'] = {}
    if change == 'missing': del value['summary']['l3']
    if change == 'boolean': value['source'] = True
    if change == 'long_reason': value['reason'] = 'x'*129
    begin, end = frames()
    receipt = capture(stream(begin, drop(), value, end), 'observe', setup_values()[0])
    assert not receipt['complete'] and receipt['events'] == []


@pytest.mark.parametrize('raw', [b'CPU 00: Lost 1 events\n', b'{broken\n', b'raw-byte-payload\n', b'x'*(MAX_LINE+1), b'{}'])
def test_lost_malformed_payload_and_line_limits_are_unknown(raw):
    begin, _ = frames()
    source = stream(begin, drop()).getvalue()+raw
    receipt = capture(io.BytesIO(source), 'observe', setup_values()[0])
    assert not receipt['complete'] and receipt['events'] == []


def test_total_limit_and_record_limit():
    begin, end = frames()
    for source in (stream(begin, *[drop() for _ in range(256)], end), io.BytesIO(b'x'*MAX_BYTES)):
        receipt = capture(source, 'observe', setup_values()[0])
        assert not receipt['complete'] and receipt['events'] == []


def test_exact_child_proof_and_post_pin_required():
    values = setup_values()
    begin, end = frames()
    receipt = capture(stream(begin, drop(), end), 'observe', values[0])
    proof = dict(receipt['child'], childGone=True, groupGone=True)
    with pytest.raises(ValueError): finalize(receipt, proof, *values, transport_exit=1)
    with pytest.raises(ValueError): finalize(receipt, dict(proof, childGone=1), *values)
    for change in ('group_alive', 'wrong_child', 'replaced_pod'):
        variant, changed = copy.deepcopy(proof), copy.deepcopy(values)
        if change == 'group_alive': variant['groupGone'] = False
        if change == 'wrong_child': variant['pid'] += 1
        if change == 'replaced_pod': changed[1]['metadata']['uid'] = 'replacement'
        with pytest.raises(ValueError):
            finalize(receipt, variant, *changed)


def test_empty_observation_proves_no_dns_conclusion():
    values = setup_values()
    begin, end = frames()
    receipt = capture(stream(begin, end), 'observe', values[0])
    result = finalize(receipt, dict(receipt['child'], childGone=True, groupGone=True), *values)
    assert result['matchingDnsDrops'] == 0 and result['noDropConclusion'] == 'unknown'


def test_private_receipt_no_overwrite_or_symlink(tmp_path):
    target = tmp_path/'receipt.json'
    write_private(target, {'safe': True})
    assert target.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError): write_private(target, {'safe': False})
    link = tmp_path/'link'; link.symlink_to(target)
    with pytest.raises(FileExistsError): write_private(link, {})
    assert json.loads(target.read_text()) == {'safe': True}


def test_stdin_stall_is_bounded_without_raw_error(tmp_path):
    script = Path(__file__).with_name('access_drop_capture.py')
    process = subprocess.Popen([sys.executable, str(script), '--mode', 'probe', '--receipt', str(tmp_path/'stalled.json')],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    process.wait(timeout=15)  # Keep stdin open and silent; exercise the 12s alarm.
    out, err = process.stdout.read(), process.stderr.read()
    process.stdin.close()
    assert b'"captureComplete": false' in out and err == b''


@pytest.mark.parametrize('change', ['wrong_exit', 'missing_end', 'duplicate_start', 'wrong_group'])
def test_incomplete_or_unsafe_control_is_unknown(change):
    begin, end = frames()
    if change == 'wrong_exit': end['timeoutExit'] = 0
    if change == 'wrong_group': begin['group'] = begin['pid']
    values = [begin, drop(), end]
    if change == 'missing_end': values.pop()
    if change == 'duplicate_start': values.insert(1, begin)
    receipt = capture(stream(*values), 'observe', setup_values()[0])
    assert not receipt['complete'] and receipt['events'] == []


def test_remote_probe_reaps_child_and_exact_group():
    script = str(Path(__file__).with_name('access_drop_remote.sh'))
    process = subprocess.run(['sh', script, 'probe'], capture_output=True, timeout=5)
    assert process.returncode == 0
    receipt = capture(io.BytesIO(process.stdout), 'probe')
    assert receipt['complete'] and receipt['timeoutExit'] == 124
    child = receipt['child']
    proof = subprocess.run(['sh', script, 'check', *[str(child[k]) for k in ('pid', 'parentPid', 'group', 'startTicks')]],
                           capture_output=True, timeout=2)
    assert proof.returncode == 0
    assert finalize(receipt, json.loads(proof.stdout))['terminationProbePassed']


def test_fixture_remote_client_and_stream_sanitizer(tmp_path):
    fixture = tmp_path/'cilium-dbg'
    fixture.write_text('#!/bin/sh\nprintf "%s\\n" '+shlex.quote(json.dumps(drop()))+'\n'
                       'trap \'kill -TERM "$fixture_child" 2>/dev/null; wait "$fixture_child" 2>/dev/null; exit 0\' INT\n'
                       'sleep 30 &\nfixture_child=$!\nwait\n')
    fixture.chmod(0o700)
    script = str(Path(__file__).with_name('access_drop_remote.sh'))
    process = subprocess.Popen(['sh', script, 'observe', '321'], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=dict(os.environ, PATH=str(tmp_path)+':'+os.environ['PATH']))
    values = setup_values()
    receipt = capture(process.stdout, 'observe', values[0])
    assert process.wait(timeout=3) == 0
    child = receipt['child']
    proof = subprocess.run(['sh', script, 'check', *[str(child[k]) for k in ('pid', 'parentPid', 'group', 'startTicks')]],
                           capture_output=True, timeout=2)
    assert proof.returncode == 0
    assert finalize(receipt, json.loads(proof.stdout), *values)['matchingDnsDrops'] == 1
    assert 'fake-token' not in json.dumps(receipt)


def test_kill_fallback_does_not_bypass_exact_termination_proof(tmp_path):
    fixture = tmp_path/'cilium-dbg'
    fixture.write_text('#!'+sys.executable+'\nimport signal,time\n'
                       'signal.signal(signal.SIGINT, signal.SIG_IGN)\ntime.sleep(30)\n')
    fixture.chmod(0o700)
    script = str(Path(__file__).with_name('access_drop_remote.sh'))
    process = subprocess.run(['sh', script, 'observe', '321'], capture_output=True, timeout=9,
                             env=dict(os.environ, PATH=str(tmp_path)+':'+os.environ['PATH']))
    assert process.returncode == 0
    values = setup_values()
    receipt = capture(io.BytesIO(process.stdout), 'observe', values[0])
    assert receipt['complete'] and receipt['timeoutExit'] == 137
    child = receipt['child']
    proof = subprocess.run(['sh', script, 'check', *[str(child[k]) for k in ('pid', 'parentPid', 'group', 'startTicks')]],
                           capture_output=True, timeout=2)
    value = json.loads(proof.stdout)
    if proof.returncode == 0:
        assert finalize(receipt, value, *values)['noDropConclusion'] == 'unknown'
    else:
        with pytest.raises(ValueError): finalize(receipt, value, *values)
