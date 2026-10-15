"""One actual immutable Runner-image DNS check, reusing the reviewed DNS helper."""
import argparse
import copy
import json
from pathlib import Path

import access_diagnose_prepare as existing

SOURCE_SHA = '91577194a5a9343778b68fcaa258d5941f92aa5a'
RUNNER_IMAGE = 'docker.io/library/anxious-s13-access-runner-9157719@sha256:2948da59599ba7842406d3ce49fccf716f47010fb713a43721c95eeb3f08d2e7'
RUNTIME_PROGRAM = '''
import platform
import signal

class RunnerDnsDeadline(BaseException):
    pass

def runner_image_dns(read_resolver, query_fn):
    libc_family, libc_version = platform.libc_ver()
    servers, searches, ndots = configuration(read_resolver())
    if not 1 <= len(servers) <= 8:
        raise ValueError()
    report = {'runnerImageRuntime': {'python': platform.python_version(), 'machine': platform.machine(),
              'libcFamily': libc_family, 'libcVersion': libc_version},
              'entrypointOverridden': True, 'sourceCredentialRead': False, 'nativeStarted': False,
              'httpOrAuthRpc': False, 'configuredResolvers': len(servers), 'searchSuffixCount': searches,
              'ndots': ndots, 'helperAttempts': 4, 'libcQueries': 0, 'dnsCause': 'unknown'}
    for alias, name in zip(('codexBackend', 'callbackService'), TARGETS):
        report[alias] = {'absoluteUdp': query_fn(name, servers[0]), 'absoluteTcp': query_fn(name, servers[0], tcp=True)}
    return report

if __name__ == '__main__':
    def expired(*_): raise RunnerDnsDeadline()
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(10)
    try:
        result = runner_image_dns(lambda: Path('/etc/resolv.conf').read_text(), query)
        signal.alarm(0)
        print(json.dumps(result), flush=True)
    except (Exception, RunnerDnsDeadline):
        signal.alarm(0)
        print('{"runnerImageDnsCheck":"unknown","networkRequested":"unknown"}', flush=True)
        raise SystemExit(1)
'''


def emit(snapshot, inputs, images):
    if images != {'sourceSha': SOURCE_SHA, 'api': existing.DIAGNOSTIC_IMAGE, 'runner': RUNNER_IMAGE}:
        raise ValueError()
    runner = snapshot['runner']
    spec = runner['spec']['template']['spec']
    labels = runner['spec']['template']['metadata']['labels']
    if (runner['metadata']['namespace'] != 'codex-trial' or runner['metadata']['name'] != 'access-web-runner-v1'
            or type(runner['spec']['replicas']) is not int or runner['spec']['replicas'] != 0
            or type(snapshot['runnerPodCount']) is not int or snapshot['runnerPodCount'] != 0
            or len(spec['containers']) != 1 or spec['containers'][0]['name'] != 'runner'
            or spec['containers'][0]['image'] != RUNNER_IMAGE or spec.get('hostNetwork', False) is not False
            or spec.get('serviceAccountName', 'trial') != 'trial' or labels.get('app') != 'access-web-runner'):
        raise ValueError()
    job = existing.emit(snapshot, inputs, existing.DIAGNOSTIC_IMAGE, 'dns')
    ps = job['spec']['template']['spec']
    if spec.get('nodeSelector') != ps['nodeSelector']: raise ValueError()
    for key in ('dnsPolicy', 'dnsConfig'):
        if key in spec:
            if spec[key] is None: raise ValueError()
            ps[key] = copy.deepcopy(spec[key])
    source = Path(__file__).with_name('access_dns_diagnose.py').read_text().split("\nif __name__ == '__main__':")
    if len(source) != 2: raise ValueError()
    container = ps['containers'][0]
    container['image'] = RUNNER_IMAGE
    container['imagePullPolicy'] = 'Never'
    container['command'] = ['python', '-B', '-c', source[0]+RUNTIME_PROGRAM]
    job['metadata']['name'] = 'access-runner-image-dns-v1'
    job['spec']['template']['metadata']['labels'] = copy.deepcopy(labels)
    return job


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('snapshot', 'inputs', 'images'): parser.add_argument('--'+key, required=True)
    args = parser.parse_args()
    try:
        read = lambda key: json.loads(Path(getattr(args, key)).read_text())
        print(json.dumps(emit(read('snapshot'), read('inputs'), read('images')), indent=2))
    except Exception:
        parser.exit(1, 'Actual Runner-image DNS check refused; private metadata is not printed\n')
