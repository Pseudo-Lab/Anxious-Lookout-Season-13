"""Emit-only stopped-state/anonymous-network diagnostic Jobs. No application startup."""
import argparse
import json
from pathlib import Path

import access_web_prepare as package
DIAGNOSTIC_IMAGE = 'docker.io/library/anxious-s13-access-api-9157719@sha256:597a505fac727b554460406cc175d99cf944f1832369c475a69f9043e7d8e48f'


def emit(snapshot, inputs, image, kind, window=None, dns_inputs=None):
    package.base.OLD_API = package.BASELINE_API
    package.base.validate_api(snapshot)  # Restored API70 baseline only.
    owner, state = package.base.identifiers(inputs)
    node = package.base.storage(snapshot)
    if image != DIAGNOSTIC_IMAGE:
        raise ValueError()
    directory = Path(__file__).parent
    if kind not in {'state', 'network', 'dns', 'dns-ndots1', 'dns-observed', 'resolver-map'}:
        raise ValueError()
    if kind != 'resolver-map' and dns_inputs is not None:
        raise ValueError()
    if kind == 'resolver-map' and window is not None:
        raise ValueError()
    name = 'access-'+kind+'-diagnose-v1'
    script = directory/('access_resolver_map.py' if kind == 'resolver-map' else 'access_state_diagnose.py' if kind == 'state' else
                        'access_dns_diagnose.py' if kind.startswith('dns') else 'access_network_diagnose.py')
    volumes, mounts, env = [], [], []
    if kind in ('state', 'resolver-map'):
        # Existing stopped runner only; fresh current GET supplied in snapshot.
        runner = snapshot['runner']
        if (runner['metadata']['namespace'] != 'codex-trial' or runner['metadata']['name'] != 'access-web-runner-v1'
                or runner['spec']['replicas'] != 0 or snapshot['runnerPodCount'] != 0):
            raise ValueError()
    if kind == 'state':
        for suffix in ('native', 'control'):
            volumes.append({'name': suffix, 'persistentVolumeClaim': {'claimName': 'trial-'+suffix, 'readOnly': True}})
            mounts.append({'name': suffix, 'mountPath': '/'+suffix, 'readOnly': True})
        env = [{'name': 'RUNNER_ACCOUNT_ID', 'value': owner}, {'name': 'RUNNER_STATE_ID', 'value': state}]
        if window:
            import uuid
            if str(uuid.UUID(window)) != window:
                raise ValueError()
            env.append({'name': 'DIAG_WINDOW_ID', 'value': window})
    container = {'name': 'diagnose', 'image': image, 'command': ['python', '-B', '-c', script.read_text()],
                 'env': env, 'volumeMounts': mounts,
                 'resources': {'requests': {'cpu': '25m', 'memory': '64Mi'}, 'limits': {'cpu': '100m', 'memory': '128Mi'}},
                 'securityContext': {'readOnlyRootFilesystem': True, 'allowPrivilegeEscalation': False, 'capabilities': {'drop': ['ALL']}}}
    job = {'apiVersion': 'batch/v1', 'kind': 'Job', 'metadata': {'namespace': 'codex-trial', 'name': name},
           'spec': {'backoffLimit': 0, 'activeDeadlineSeconds': 30, 'template': {
               'metadata': {'labels': {'app': 'access-state-diagnose' if kind == 'state' else 'access-web-runner'}},
               'spec': {'restartPolicy': 'Never', 'serviceAccountName': 'trial', 'automountServiceAccountToken': False,
                        'nodeSelector': {'kubernetes.io/hostname': node}, 'terminationGracePeriodSeconds': 5,
                        'securityContext': {'runAsNonRoot': True, 'runAsUser': 10001, 'runAsGroup': 10001,
                                            'seccompProfile': {'type': 'RuntimeDefault'}},
                        'containers': [container], 'volumes': volumes}}}}
    if kind == 'dns-observed':
        # Bounded window for exact endpoint/proxy metadata, not a background monitor.
        container['command'][3] = 'import time\ntime.sleep(8)\n'+script.read_text()+'\ntime.sleep(20)\n'
        job['spec']['activeDeadlineSeconds'] = 60
    if kind == 'resolver-map':
        from access_resolver_map import expected_context
        context = expected_context(dns_inputs['service'], dns_inputs['pod'], node)
        container['command'][3] = 'EXPECTED_DNS_CONTEXT = '+json.dumps(context)+'\n'+script.read_text()
    if kind == 'dns-ndots1':
        job['spec']['template']['spec']['dnsConfig'] = {'options': [{'name': 'ndots', 'value': '1'}]}
    if kind != 'state':
        return job  # Selects already deployed runner network policy, mounts no PVC/Secrets.
    deny = {'apiVersion': 'cilium.io/v2', 'kind': 'CiliumNetworkPolicy',
            'metadata': {'namespace': 'codex-trial', 'name': 'access-state-diagnose-deny-v1'},
            'spec': {'endpointSelector': {'matchLabels': {'app': 'access-state-diagnose'}},
                     'ingressDeny': [{'fromEntities': ['all']}], 'egressDeny': [{'toEntities': ['all']}]}}
    return {'apiVersion': 'v1', 'kind': 'List', 'items': [deny, job]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=('state', 'network', 'dns', 'dns-ndots1', 'dns-observed', 'resolver-map'))
    parser.add_argument('--snapshot', required=True); parser.add_argument('--inputs', required=True)
    parser.add_argument('--window-inputs')
    parser.add_argument('--dns-inputs')
    args = parser.parse_args()
    try:
        snapshot, inputs = json.loads(Path(args.snapshot).read_text()), json.loads(Path(args.inputs).read_text())
        window = json.loads(Path(args.window_inputs).read_text())['windowId'] if args.window_inputs else None
        dns_inputs = json.loads(Path(args.dns_inputs).read_text()) if args.dns_inputs else None
        print(json.dumps(emit(snapshot, inputs, DIAGNOSTIC_IMAGE, args.kind, window, dns_inputs), indent=2))
    except Exception:
        parser.exit(1, 'Read-only diagnostic emission refused; private values are not printed\n')
