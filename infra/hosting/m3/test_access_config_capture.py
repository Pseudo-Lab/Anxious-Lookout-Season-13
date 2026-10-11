import copy
import json
import os
from pathlib import Path
import subprocess
import threading
import time

import pytest

from access_config_capture import (KEYS, MAX_BYTES, agent_tag, finalize, load, read_channels,
                                   sanitize, write_private)
from test_access_endpoint_pin import metadata

NONCE = 'a'*32
CHILD = {'pid': 123, 'parentPid': 122, 'group': 122, 'startTicks': 100}
MARKER = ('DN11:'+NONCE+':'+json.dumps(CHILD)+'\n').encode()
HELP = b'Usage:\n  cilium-dbg config [<option>=(enable|disable) ...]\nFlags:\n  -r, --read-only  Display read only configurations\n  -o, --output string  json or yaml\n'


def agent():
    pod = metadata()[2]
    pod['metadata']['name'] = 'cilium-fixture'
    pod['spec']['hostNetwork'] = True
    # Match a Kubernetes Pod response, including the selected container state.
    pod['status']['phase'] = 'Running'
    pod['status']['containerStatuses'] = [{'name': 'cilium-agent', 'ready': True,
        'imageID': 'fixture-image', 'restartCount': 0, 'state': {'running': {'startedAt': 'fixture-time'}}}]
    return pod


def raw(options=None):
    return (json.dumps(dict({key: True for key in KEYS}, **(options or {}),
                           UnrelatedConfig='PRIVATE_SENTINEL_DO_NOT_EMIT'))+'\n').encode()


def receipt(mode='read', stdout=None, stderr=MARKER, code=0, complete=True):
    return sanitize(stdout if stdout is not None else HELP if mode == 'help' else raw(),
                    stderr, mode, NONCE, agent_tag(agent()), code, complete)


def capability():
    return finalize(receipt('help'), dict(CHILD, childGone=True, groupGone=True), agent(), agent(), 0)


@pytest.mark.parametrize('tproxy,iptables,ipv4,branch', [(True, True, True, 'bpf_socket_assignment'),
    (False, True, True, 'iptables_tproxy'), (False, False, True, 'unknown'), (True, True, False, 'unknown')])
def test_serializer_boolean_fields_and_branches(tproxy, iptables, ipv4, branch):
    value = receipt(stdout=raw({'EnableBPFTProxy': tproxy, 'InstallIptRules': iptables, 'EnableIPv4': ipv4}))
    final = finalize(value, dict(CHILD, childGone=True, groupGone=True), agent(), agent(), 0,
                     capability=capability())
    assert final['nextReadCandidate'] == branch and final['trafficConclusion'] == 'unknown'
    assert 'PRIVATE_SENTINEL' not in json.dumps(value)+json.dumps(final)
    assert all(type(v) is bool for v in final['options'].values())


@pytest.mark.parametrize('variant', ['missing', 'null', 'integer', 'string', 'alias', 'duplicate',
    'nested_duplicate', 'partial', 'non_object', 'nan', 'no_newline', 'extra_json', 'stderr',
    'second_marker', 'wrong_nonce', 'nonzero', 'timeout', 'oversize'])
def test_unsupported_or_incomplete_data_is_unknown_without_sentinel_leak(variant):
    stdout, stderr, code, complete = raw(), MARKER, 0, True
    value = json.loads(stdout)
    if variant == 'missing': del value[KEYS[0]]
    elif variant in ('null', 'integer', 'string'): value[KEYS[0]] = {'null': None, 'integer': 0, 'string': 'false'}[variant]
    elif variant == 'alias': value['enable-ipv4'] = False
    if variant in ('missing', 'null', 'integer', 'string', 'alias'): stdout = (json.dumps(value)+'\n').encode()
    elif variant == 'duplicate': stdout = stdout.replace(b'"EnableIPv4": true', b'"EnableIPv4": false, "EnableIPv4": true')
    elif variant == 'nested_duplicate': stdout = stdout[:-2]+b', "Other": {"duplicate":1,"duplicate":2}}\n'
    elif variant == 'partial': stdout = stdout[:-5]
    elif variant == 'non_object': stdout = b'[]\n'
    elif variant == 'nan': stdout = stdout.replace(b'true', b'NaN', 1)
    elif variant == 'no_newline': stdout = stdout.rstrip()
    elif variant == 'extra_json': stdout += b'{}\n'
    elif variant == 'stderr': stderr += b'PRIVATE_STDERR_SENTINEL\n'
    elif variant == 'second_marker': stderr += MARKER
    elif variant == 'wrong_nonce': stderr = stderr.replace(NONCE.encode(), b'b'*32)
    elif variant == 'nonzero': code = 1
    elif variant == 'timeout': complete = False
    elif variant == 'oversize': stdout += b'PRIVATE_SENTINEL'+b'x'*MAX_BYTES
    result = receipt(stdout=stdout, stderr=stderr, code=code, complete=complete)
    assert result['complete'] is False and set(result['options'].values()) == {'unknown'}
    assert 'PRIVATE' not in json.dumps(result)


@pytest.mark.parametrize('variant', ['transport', 'proof_exit', 'ticks', 'live_group', 'uid', 'spec',
    'image', 'restart', 'deleted', 'not_ready', 'other_capability'])
def test_result_requires_exact_proof_and_agent_continuity(variant):
    before, after = agent(), agent()
    proof, exit_code, proof_exit, cap = dict(CHILD, childGone=True, groupGone=True), 0, 0, capability()
    if variant == 'transport': exit_code = 124
    elif variant == 'proof_exit': proof_exit = 1
    elif variant == 'ticks': proof['startTicks'] += 1
    elif variant == 'live_group': proof['groupGone'] = False
    elif variant == 'uid': after['metadata']['uid'] += '-replacement'
    elif variant == 'spec': after['spec']['hostPID'] = True
    elif variant == 'image': after['status']['containerStatuses'][0]['imageID'] += '-new'
    elif variant == 'restart': after['status']['containerStatuses'][0]['restartCount'] += 1
    elif variant == 'deleted': after['metadata']['deletionTimestamp'] = 'now'
    elif variant == 'not_ready': after['status']['containerStatuses'][0]['ready'] = False
    elif variant == 'other_capability': cap['contextTag'] = 'b'*64
    with pytest.raises((ValueError, KeyError)):
        finalize(receipt(), proof, before, after, exit_code, proof_exit, cap)


def test_help_is_capability_only_and_has_no_version_inference():
    value = capability()
    assert value['versionVerifiedByHelp'] is False
    assert not receipt('help', stdout=b'PRIVATE_HELP_SENTINEL\n')['complete']


def fifo_directory(tmp_path):
    for name in ('stdout', 'stderr'): os.mkfifo(tmp_path/name, 0o600)


def test_dual_fifo_transport_keeps_both_raw_channels_off_disk(tmp_path):
    fifo_directory(tmp_path)
    def producer():
        while not (tmp_path/'reader.ready').exists(): time.sleep(0.002)
        with open(tmp_path/'stdout', 'wb') as out, open(tmp_path/'stderr', 'wb') as err:
            out.write(raw()); err.write(MARKER+b'PRIVATE_STDERR_SENTINEL\n')
        (tmp_path/'transport.exit.tmp').write_text('0\n')
        (tmp_path/'transport.exit.tmp').rename(tmp_path/'transport.exit')
    thread = threading.Thread(target=producer); thread.start()
    channels, code, complete = read_channels(tmp_path, deadline=1)
    thread.join(timeout=1)
    assert complete and code == 0 and not receipt(stdout=bytes(channels['stdout']), stderr=bytes(channels['stderr']))['complete']
    assert all('PRIVATE' not in p.read_text() for p in tmp_path.iterdir() if p.is_file())


def test_fifo_deadline_and_regular_file_refusal(tmp_path):
    fifo_directory(tmp_path)
    channels, code, complete = read_channels(tmp_path, deadline=0.05)
    assert code is None and not complete and all(not v for v in channels.values())
    (tmp_path/'stdout').unlink(); (tmp_path/'stdout').write_text('PRIVATE_SENTINEL')
    with pytest.raises(ValueError): read_channels(tmp_path)


def test_receipt_cannot_overwrite_or_follow_symlink(tmp_path):
    output = tmp_path/'receipt'
    write_private(output, receipt())
    assert output.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError): write_private(output, {})
    link = tmp_path/'link'; link.symlink_to(output)
    with pytest.raises(FileExistsError): write_private(link, {})


@pytest.mark.parametrize('stderr_noise', [False, True])
def test_host_wrapper_exact_transport_and_nonlogging_paths(tmp_path, stderr_noise):
    inputs = tmp_path/'inputs'; inputs.mkdir(mode=0o700)
    output = tmp_path/'output'; output.mkdir(mode=0o700)
    for name in ('before', 'anchor'): (inputs/(name+'.json')).write_text(json.dumps(agent()))
    (inputs/'capability.json').write_text(json.dumps(capability()))
    docker = tmp_path/'docker'
    docker.write_text('''#!/usr/bin/env python
import json, sys
from pathlib import Path
from access_config_capture import agent_tag, read_channels, sanitize, write_private
args = sys.argv[1:]
assert args[:3] == ['run', '--rm', '--pull=never']
assert '--log-driver=none' in args and args[args.index('--network')+1] == 'none'
mounts = {}
for i, value in enumerate(args):
    if value == '--mount':
        parts = dict(v.split('=',1) for v in args[i+1].split(',') if '=' in v)
        mounts[parts['dst']] = Path(parts['src'])
mode, nonce = args[args.index('--mode')+1], args[args.index('--nonce')+1]
root = mounts['/channels']
pod = json.loads((mounts['/inputs']/'before.json').read_text())
assert pod['metadata']['name'] == args[args.index('--agent-name')+1]
assert args[args.index('--capability')+1] == '/inputs/capability.json'
assert json.loads((mounts['/inputs']/'capability.json').read_text())['contextTag'] == agent_tag(pod)
channels, code, complete = read_channels(root)
receipt = sanitize(bytes(channels['stdout']), bytes(channels['stderr']), mode, nonce, agent_tag(pod), code, complete)
write_private(root/'receipt.json', receipt)
print(json.dumps({'sanitizedReceiptWritten':True,'captureComplete':receipt['complete']}))
''')
    docker.chmod(0o700)
    kubectl = tmp_path/'kubectl'
    kubectl.write_text('''#!/usr/bin/env python
import json, os, sys
args = sys.argv[1:]
assert args[:9] == ['-n','kube-system','exec','-i',os.environ['DN11_AGENT'],'-c','cilium-agent','--','timeout']
assert args[9:16] == ['--signal=TERM','--kill-after=1s','6s','sh','-s','--','read']
assert len(args) == 17
assert 'exec cilium-dbg config --read-only -o json' in sys.stdin.read()
nonce = args[-1]
sys.stderr.write('DN11:'+nonce+':'+json.dumps({'pid':123,'parentPid':122,'group':122,'startTicks':100})+'\\n')
if os.environ['TEST_NOISE'] == 'yes': sys.stderr.write('PRIVATE_STDERR_SENTINEL\\n')
print(json.dumps({'EnableIPv4':True,'EnableBPFTProxy':True,'InstallIptRules':True,'EnableEndpointRoutes':False,'Other':'PRIVATE_STDOUT_SENTINEL'}))
''')
    kubectl.chmod(0o700)
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).parent), DN11_DOCKER=str(docker),
        DN11_KUBECTL=str(kubectl), DN11_IMAGE='sha256:'+'a'*64, DN11_SOURCE_DIR=str(Path(__file__).parent),
        DN11_INPUT_DIR=str(inputs), DN11_OUTPUT_DIR=str(output), DN11_AGENT=agent()['metadata']['name'],
        DN11_MODE='read', TEST_NOISE='yes' if stderr_noise else 'no')
    wrapper = Path(__file__).with_name('capture_access_config.sh')
    process = subprocess.run(['bash', str(wrapper)], env=env, capture_output=True, timeout=5)
    assert process.returncode == (1 if stderr_noise else 0)
    safe = load((output/'receipt.json').read_bytes())
    assert safe['complete'] is (not stderr_noise)
    assert not (output/'stdout').exists() and not (output/'stderr').exists()
    assert 'PRIVATE' not in process.stdout.decode()+process.stderr.decode()
    assert all('PRIVATE' not in p.read_text() for p in output.iterdir() if p.is_file())


def test_fifo_oversize_keeps_child_marker_but_refuses_all_options(tmp_path):
    fifo_directory(tmp_path)
    def producer():
        while not (tmp_path/'reader.ready').exists(): time.sleep(0.002)
        with open(tmp_path/'stderr', 'wb') as err, open(tmp_path/'stdout', 'wb') as out:
            err.write(MARKER); err.flush()
            out.write(b'x'*MAX_BYTES)
        (tmp_path/'transport.exit.tmp').write_text('0\n')
        (tmp_path/'transport.exit.tmp').rename(tmp_path/'transport.exit')
    thread = threading.Thread(target=producer, daemon=True); thread.start()
    channels, code, complete = read_channels(tmp_path, deadline=1)
    thread.join(timeout=1)
    value = receipt(stdout=bytes(channels['stdout']), stderr=bytes(channels['stderr']), code=code, complete=complete)
    assert not thread.is_alive() and not complete and value['child'] == CHILD and not value['complete']
    assert sum(len(v) for v in channels.values()) <= MAX_BYTES


@pytest.mark.parametrize('variant', ['missing', 'other_context', 'not_supported', 'numeric_boolean'])
def test_read_capability_gate_precedes_reader_ready_and_transport(tmp_path, variant):
    before = tmp_path/'before.json'; before.write_text(json.dumps(agent()))
    cap = capability()
    if variant == 'other_context': cap['contextTag'] = 'b'*64
    elif variant == 'not_supported': cap['helpSupported'] = False
    elif variant == 'numeric_boolean': cap['helpSupported'] = 1
    saved = tmp_path/'capability.json'
    if variant != 'missing': saved.write_text(json.dumps(cap))
    args = ['python', '-B', str(Path(__file__).with_name('access_config_capture.py')),
            '--before', str(before), '--anchor', str(before), '--agent-name', agent()['metadata']['name'],
            '--mode', 'read', '--nonce', NONCE, '--channel-dir', str(tmp_path),
            '--receipt', str(tmp_path/'receipt.json'), '--capability', str(saved)]
    process = subprocess.run(args, capture_output=True, timeout=2)
    assert process.returncode == 1 and not (tmp_path/'reader.ready').exists()
    assert not (tmp_path/'receipt.json').exists() and process.stdout == b''


@pytest.mark.parametrize('mode,args', [('help', ['config', '--help']), ('read', ['config', '--read-only', '-o', 'json'])])
def test_remote_exact_argv_and_owned_process_proof(tmp_path, mode, args):
    fake = tmp_path/'cilium-dbg'
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    fake.chmod(0o700)
    comm = tmp_path/'comm'; comm.write_text('cilium-agent\n')
    remote = Path(__file__).with_name('access_config_remote.sh').read_text().replace('/proc/1/comm', str(comm))
    script = tmp_path/'remote'; script.write_text(remote)
    environment = dict(os.environ, PATH=str(tmp_path)+':'+os.environ['PATH'])
    p = subprocess.run(['timeout', '--signal=TERM', '--kill-after=1s', '2s', 'sh', str(script), mode, NONCE],
                       env=environment, capture_output=True, timeout=3)
    assert p.returncode == 0 and p.stdout.decode().splitlines() == args
    child = load(p.stderr.partition(b':')[2].partition(b':')[2])
    checker = str(Path(__file__).with_name('access_drop_remote.sh'))
    proof = subprocess.run(['sh', checker, 'check', *[str(child[k]) for k in ('pid', 'parentPid', 'group', 'startTicks')]], capture_output=True, timeout=2)
    assert proof.returncode == 0 and load(proof.stdout) == dict(child, childGone=True, groupGone=True)
    for extra in ('EnableIPv4=false', '--num-pages=1', '--all'):
        p = subprocess.run(['sh', str(script), mode, NONCE, extra], env=environment, capture_output=True, timeout=2)
        assert p.returncode == 2 and p.stdout == p.stderr == b''
