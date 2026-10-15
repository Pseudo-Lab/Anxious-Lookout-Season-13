"""DN11 memory-only dual-channel sanitizer and offline context/proof finalizer."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import stat
import time

KEYS = ('EnableIPv4', 'EnableBPFTProxy', 'InstallIptRules', 'EnableEndpointRoutes')
ALIASES = ('enable-ipv4', 'enable-bpf-tproxy', 'install-iptables-rules', 'enable-endpoint-routes')
MAX_BYTES = 262144
DEADLINE = 12


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError()
        value[key] = item
    return value


def load(raw):
    return json.loads(raw, object_pairs_hook=unique_object,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))


def child_valid(child):
    if (not isinstance(child, dict) or set(child) != {'pid', 'parentPid', 'group', 'startTicks'}
            or any(type(v) is not int or not 1 <= v <= 2**63-1 for v in child.values())
            or min(child['pid'], child['parentPid'], child['group']) <= 1
            or child['parentPid'] != child['group'] or child['pid'] == child['group']):
        raise ValueError()
    return child


def agent_tag(pod):
    meta, spec, status = pod['metadata'], pod['spec'], pod['status']
    containers = status['containerStatuses']
    if (meta['namespace'] != 'kube-system' or meta.get('labels', {}).get('k8s-app') != 'cilium'
            or meta.get('deletionTimestamp') is not None
            or status['phase'] != 'Running' or spec.get('hostNetwork') is not True
            or spec.get('hostPID', False) is not False or spec.get('shareProcessNamespace', False) is not False
            or not containers or any(v['ready'] is not True or 'running' not in v['state'] for v in containers)
            or len([v for v in containers if v['name'] == 'cilium-agent']) != 1):
        raise ValueError()
    if any(not isinstance(v.get('imageID'), str) or not v['imageID']
           or type(v.get('restartCount')) is not int or v['restartCount'] < 0
           or not isinstance(v['state']['running'].get('startedAt'), str)
           or not v['state']['running']['startedAt'] for v in containers):
        raise ValueError()
    value = dict(namespace=meta['namespace'], name=meta['name'], uid=meta['uid'], spec=spec, containers=containers)
    if any(not isinstance(v, str) or not v for v in (meta['name'], meta['uid'], spec['nodeName'])):
        raise ValueError()
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def sanitize(stdout, stderr, mode, nonce, context_tag, exit_code, complete=True):
    receipt = {'mode': mode, 'contextTag': context_tag, 'complete': False,
               'options': {key: 'unknown' for key in KEYS}, 'helpSupported': False}
    try:
        if mode not in ('help', 'read') or not re.fullmatch('[0-9a-f]{32}', nonce):
            raise ValueError()
        # Only the first stderr line can be a control frame; CLI stderr is never a marker.
        first, separator, remaining = stderr.partition(b'\n')
        prefix = ('DN11:'+nonce+':').encode()
        if not separator or len(first) > 512 or not first.startswith(prefix):
            raise ValueError()
        receipt['child'] = child_valid(load(first[len(prefix):]))
        if (not complete or type(exit_code) is not int or exit_code != 0 or remaining
                or len(stdout)+len(stderr) >= MAX_BYTES or not stdout.endswith(b'\n')):
            raise ValueError()
        if mode == 'read':
            value = load(stdout)
            if (not isinstance(value, dict) or any(alias in value for alias in ALIASES)
                    or any(type(value.get(key)) is not bool for key in KEYS)):
                raise ValueError()
            receipt['options'] = {key: value[key] for key in KEYS}
        else:
            text = stdout.decode('utf-8', errors='strict')
            if (not re.search(r'^\s*cilium-dbg config(?:\s|$)', text, re.M)
                    or not re.search(r'^\s*(?:-r,\s*)?--read-only\s', text, re.M)
                    or not re.search(r'^\s*-o,\s*--output\s', text, re.M)):
                raise ValueError()
            receipt['helpSupported'] = True
        receipt['complete'] = True
    except Exception:
        receipt['options'] = {key: 'unknown' for key in KEYS}
        receipt['helpSupported'] = False
    return receipt


def write_private(path, value):
    with os.fdopen(os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600), 'w') as file:
        json.dump(value, file)


def capability_valid(value, context):
    return (isinstance(value, dict) and set(value) == {
        'helpSupported', 'contextMatched', 'versionVerifiedByHelp', 'contextTag'}
        and value['helpSupported'] is True and value['contextMatched'] is True
        and value['versionVerifiedByHelp'] is False and value['contextTag'] == context)


def read_channels(directory, deadline=DEADLINE):
    """FIFO contents stay in bounded memory; disk has only safe readiness/exit data."""
    start = time.monotonic()
    buffers = {'stdout': bytearray(), 'stderr': bytearray()}
    size = 0
    valid = True
    with selectors.DefaultSelector() as selector:
        fds = []
        try:
            for name in buffers:
                fd = os.open(directory/name, os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW)
                fds.append(fd)
                if not stat.S_ISFIFO(os.fstat(fd).st_mode):
                    raise ValueError()
                selector.register(fd, selectors.EVENT_READ, name)
            write_private(directory/'reader.ready', {'ready': True})
            while True:
                if time.monotonic()-start >= deadline:
                    return buffers, None, False
                # Exit is written after the producer closes both FIFO writers.
                done = (directory/'transport.exit').exists()
                drained = True
                for key, _ in sorted(selector.select(0.02), key=lambda v: v[0].data != 'stderr'):
                    chunk = os.read(key.fd, 8192)
                    if chunk:
                        drained = False
                        size += len(chunk)
                        if size >= MAX_BYTES:
                            valid = False
                        # Reserve a bounded marker prefix even if stdout floods first.
                        # Continue draining until transport closes or the deadline fires.
                        limit = 1024 if key.data == 'stderr' else MAX_BYTES-1024
                        if len(buffers[key.data])+len(chunk) > limit:
                            valid = False
                        buffers[key.data].extend(chunk[:max(0, limit-len(buffers[key.data]))])
                if done and drained:
                    raw = (directory/'transport.exit').read_bytes()
                    if not re.fullmatch(rb'(0|[1-9][0-9]{0,2})\n', raw):
                        raise ValueError()
                    return buffers, int(raw), valid
        finally:
            for fd in fds:
                os.close(fd)


def finalize(receipt, proof, before, after, transport_exit, proof_exit=0, capability=None):
    child = child_valid(receipt.get('child'))
    if (type(transport_exit) is not int or transport_exit != 0 or type(proof_exit) is not int or proof_exit != 0
            or receipt.get('complete') is not True
            or proof != dict(child, childGone=True, groupGone=True)
            or any(type(proof.get(k)) is not int for k in child)
            or proof.get('childGone') is not True or proof.get('groupGone') is not True
            or receipt['contextTag'] != agent_tag(before) or agent_tag(after) != agent_tag(before)):
        raise ValueError()
    if receipt['mode'] == 'help':
        if receipt.get('helpSupported') is not True:
            raise ValueError()
        return {'helpSupported': True, 'contextMatched': True, 'versionVerifiedByHelp': False,
                'contextTag': receipt['contextTag']}
    if (receipt['mode'] != 'read' or not capability_valid(capability, receipt['contextTag'])
            or set(receipt['options']) != set(KEYS) or any(type(v) is not bool for v in receipt['options'].values())):
        raise ValueError()
    options = receipt['options']
    branch = ('unknown' if not options['EnableIPv4'] else 'bpf_socket_assignment' if options['EnableBPFTProxy']
              else 'iptables_tproxy' if options['InstallIptRules'] else 'unknown')
    return {'configurationClassified': True, 'contextMatched': True, 'options': options,
            'nextReadCandidate': branch, 'trafficConclusion': 'unknown'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--channel-dir'); parser.add_argument('--mode', choices=('help', 'read'))
    parser.add_argument('--nonce'); parser.add_argument('--before', required=True)
    parser.add_argument('--agent-name')
    parser.add_argument('--anchor', required=True)
    parser.add_argument('--receipt', required=True); parser.add_argument('--after')
    parser.add_argument('--proof'); parser.add_argument('--transport-exit-code', type=int)
    parser.add_argument('--proof-exit-code', type=int)
    parser.add_argument('--capability')
    args = parser.parse_args()
    try:
        read = lambda path: load(Path(path).read_bytes())
        if agent_tag(read(args.before)) != agent_tag(read(args.anchor)):
            raise ValueError()
        if args.proof:
            result = finalize(read(args.receipt), read(args.proof), read(args.before), read(args.after),
                              args.transport_exit_code, args.proof_exit_code,
                              read(args.capability) if args.capability else None)
        else:
            if read(args.before)['metadata']['name'] != args.agent_name:
                raise ValueError()
            context = agent_tag(read(args.before))
            if args.mode == 'read' and not capability_valid(read(args.capability), context):
                raise ValueError()
            buffers, exit_code, complete = read_channels(Path(args.channel_dir))
            receipt = sanitize(bytes(buffers['stdout']), bytes(buffers['stderr']), args.mode, args.nonce,
                               context, exit_code, complete)
            write_private(args.receipt, receipt)
            result = {'sanitizedReceiptWritten': True, 'captureComplete': receipt['complete']}
        print(json.dumps(result))
    except Exception:
        parser.exit(1, 'DN11 config unknown/refused; raw channels are not printed\n')
