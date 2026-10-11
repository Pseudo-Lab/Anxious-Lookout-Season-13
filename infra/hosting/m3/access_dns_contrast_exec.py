"""Prepare a metadata-only holder and an exact-owner, single-use remote DNS exec."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re

import access_runner_dns_prepare as direct
import access_runner_dns_control_prepare as control
from access_dns_contrast_prepare import JOBS, identity
from access_dns_contrast_runtime import ENV_UID, ENV_NAME

ENV = [{'name': name, 'valueFrom': {'fieldRef': {'apiVersion': 'v1', 'fieldPath': field}}}
       for name, field in ((ENV_UID, 'metadata.uid'), (ENV_NAME, 'metadata.name'))]


def nonce_ok(nonce):
    if not isinstance(nonce, str) or not re.fullmatch('[a-f0-9]{32}', nonce):
        raise ValueError()


def holder_command(nonce):
    nonce_ok(nonce)
    source = Path(__file__).with_name('access_dns_contrast_runtime.py').read_text()
    return ['python', '-B', '-c', source+'\nholder('+json.dumps(nonce)+')\n']


def emit(snapshot, inputs, images, baseline, services, phase, nonce):
    if phase not in JOBS:
        raise ValueError()
    original = direct.emit(snapshot, inputs, images)
    if control.canonical(original) != control.canonical(baseline):
        raise ValueError()
    job = copy.deepcopy(original)
    job['metadata']['name'] = JOBS[phase]
    job['spec']['activeDeadlineSeconds'] = 50
    labels = job['spec']['template']['metadata']['labels']
    if any(key in labels for key in control.JOB_UID_LABELS+control.JOB_NAME_LABELS):
        raise ValueError()
    labels['app'] = control.CONTROL_APP
    control.services_exclude_control(services, labels, JOBS[phase])
    container = job['spec']['template']['spec']['containers'][0]
    container['command'] = holder_command(nonce)
    container['env'] = copy.deepcopy(ENV)
    return job


def selected(job, pod, phase, nonce):
    ref = identity(job, 'Job', JOBS[phase])
    template = job['spec']['template']
    c = template['spec']['containers'][0]
    if (len(template['spec']['containers']) != 1 or c['image'] != direct.RUNNER_IMAGE
            or c['command'] != holder_command(nonce) or c['env'] != ENV
            or job['spec']['backoffLimit'] != 0 or job['spec']['activeDeadlineSeconds'] != 50):
        raise ValueError()
    meta, spec, status = pod['metadata'], pod['spec'], pod['status']
    owners = meta['ownerReferences']
    if (pod.get('kind') != 'Pod' or meta['namespace'] != 'codex-trial'
            or not meta['name'].startswith(JOBS[phase]+'-') or not meta.get('uid') or meta.get('deletionTimestamp')
            or len(owners) != 1 or owners[0].get('kind') != 'Job' or owners[0].get('name') != ref['name']
            or owners[0].get('uid') != ref['uid'] or owners[0].get('controller') is not True
            or status['phase'] != 'Running' or len(spec['containers']) != 1
            or any(spec.get(k, False) is not False for k in ('hostNetwork', 'hostPID', 'shareProcessNamespace'))
            or spec.get('automountServiceAccountToken') is not False or spec.get('volumes', [])):
        raise ValueError()
    actual = spec['containers'][0]
    if (actual.get('name') != 'diagnose' or actual.get('image') != direct.RUNNER_IMAGE
            or actual.get('imagePullPolicy') != 'Never' or spec.get('serviceAccountName') != 'trial'
            or actual.get('command') != c['command'] or actual.get('args', [])
            or actual.get('env') != ENV or actual.get('envFrom', []) or actual.get('volumeMounts', [])
            or actual.get('securityContext') != c['securityContext']
            or spec.get('securityContext') != template['spec']['securityContext']
            or spec.get('nodeSelector') != template['spec']['nodeSelector']
            or spec['nodeName'] != template['spec']['nodeSelector']['kubernetes.io/hostname']):
        raise ValueError()
    for key in ('dnsPolicy', 'dnsConfig'):
        if spec.get(key) != template['spec'].get(key):
            raise ValueError()
    if any(meta['labels'].get(k) != v for k, v in template['metadata']['labels'].items()):
        raise ValueError()
    if (any(meta['labels'].get(k) != ref['name'] for k in control.JOB_NAME_LABELS)
            or any(meta['labels'].get(k) != ref['uid'] for k in control.JOB_UID_LABELS)):
        raise ValueError()
    states = status['containerStatuses']
    if (len(states) != 1 or states[0]['name'] != 'diagnose' or type(states[0]['restartCount']) is not int
            or states[0]['restartCount'] != 0 or not isinstance(states[0]['state'].get('running'), dict)
            or not states[0].get('containerID') or states[0].get('imageID') != direct.RUNNER_IMAGE):
        raise ValueError()
    return ref


def command(job, pod, receipt, scope, phase, nonce, dns_program):
    selected(job, pod, phase, nonce)
    if (receipt.get('holding') is not True or receipt.get('networkRequested') is not False
            or receipt.get('podUid') != pod['metadata']['uid'] or receipt.get('podName') != pod['metadata']['name']
            or receipt.get('nonce') != nonce or type(receipt.get('startTicks')) is not int or receipt['startTicks'] <= 0
            or type(receipt.get('startNs')) is not int or receipt['startNs'] <= 0
            or receipt.get('cmdlineSha256') != hashlib.sha256(('\0'.join(holder_command(nonce))+'\0').encode()).hexdigest()
            or scope.get('phase') != phase or scope.get('realizedRowsMatch') is not True
            or scope['pin']['podUid'] != receipt['podUid'] or scope['pin']['podName'] != receipt['podName']):
        raise ValueError()
    expected = {key: receipt[key] for key in ('podUid', 'podName', 'startTicks', 'startNs', 'cmdlineSha256', 'nonce')}
    expected['holderCommand'] = holder_command(nonce)
    expected.update(phase=phase, jobUid=job['metadata']['uid'], policyUid=scope['policyUid'],
                    policyRv=scope['policyRv'], policyRevision=scope['policyRevision'])
    helper = Path(__file__).with_name('access_dns_diagnose.py').read_text().split("\nif __name__ == '__main__':")
    if len(helper) != 2 or dns_program != helper[0]+direct.RUNTIME_PROGRAM:
        raise ValueError()
    parts = dns_program.split("\nif __name__ == '__main__':")
    if len(parts) != 2:
        raise ValueError()
    source = Path(__file__).with_name('access_dns_contrast_runtime.py').read_text()
    source += '\nEXPECTED = '+repr(expected)+'\nDNS = {"__name__":"definitions"}\nexec('+repr(parts[0])+',DNS)\n'
    source += '''
try:
    result = run_once(EXPECTED, DNS)
    result['dnsContrastContext'] = {key: EXPECTED[key] for key in
        ('phase','jobUid','podUid','nonce','policyUid','policyRv','policyRevision','startTicks')}
    print(json.dumps(result), flush=True)
except (Exception, DNS['RunnerDnsDeadline']):
    print('{"dnsContrastExec":"unknown","networkRequested":"unknown"}', flush=True)
    raise SystemExit(1)
'''
    return ['python', '-B', '-c', source]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('holder', 'command', 'program'))
    parser.add_argument('--phase', required=True, choices=tuple(JOBS)); parser.add_argument('--nonce', required=True)
    for key in ('snapshot', 'inputs', 'images', 'baseline', 'services', 'job', 'pod', 'receipt', 'scope'):
        parser.add_argument('--'+key)
    args = parser.parse_args()
    try:
        read = lambda key: json.loads(Path(getattr(args, key)).read_text())
        if args.action == 'holder':
            result = emit(*(read(key) for key in ('snapshot', 'inputs', 'images', 'baseline', 'services')), args.phase, args.nonce)
        else:
            baseline = read('baseline')
            result = command(*(read(key) for key in ('job', 'pod', 'receipt', 'scope')), args.phase, args.nonce,
                             baseline['spec']['template']['spec']['containers'][0]['command'][3])
        if args.action == 'program':
            print(result[3], end='')
        else:
            print(json.dumps(result, indent=2))
    except Exception:
        parser.exit(1, 'DNS contrast holder/exec refused; private metadata is not printed\n')
