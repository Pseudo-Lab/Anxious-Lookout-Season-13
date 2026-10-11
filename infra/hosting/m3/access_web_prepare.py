"""Emit-only access web package, reusing existing PV/API guarded patch renderer.

Images come from a reviewed immutable production release manifest, never fixtures.
No source credentials are read by this renderer.
"""
import argparse
import json
import re
from pathlib import Path

import owner_prepare as base
BASELINE_API = base.API
BASE_EMIT = base.emit


def configure(images):
    if set(images) != {'sourceSha', 'api', 'runner'} or not re.fullmatch(r'[a-f0-9]{40}', images['sourceSha']):
        raise ValueError()
    for key in ('api', 'runner'):
        if not re.fullmatch(r'docker.io/library/anxious-s13-access-(api|runner)-[a-f0-9]+@sha256:[a-f0-9]{64}', images[key]):
            raise ValueError()
        if '-'+key+'-' not in images[key]:
            raise ValueError()
    base.OLD_API, base.API, base.RUNNER = BASELINE_API, images['api'], images['runner']
    base.NAME = 'access-web-runner-v1'
    base.LABEL = 'access-web-runner'
    base.API_INPUT = 'hosting-access-web-input-v1'
    base.TRANSPORT = 'access-web-transport-v1'
    base.CONFIG = 'hosting-access-web-v1'


def emit(snapshot, inputs, part):
    # Operator chooses existing owner/state. Only the unchanged Codex backend is permitted.
    selected = dict(inputs, providerHosts=['chatgpt.com'], procedureRef='ACCESS-WEB-TEST.md')
    value = BASE_EMIT(snapshot, selected, part)
    if part == 'runner':
        container = value['spec']['template']['spec']['containers'][0]
        env = {v['name']: v['value'] for v in container['env'] if 'value' in v}
        env.pop('CODEX_PERSONAL_CONTROL_DIR')
        env.update(CODEX_AUTH_MODE='access-only', CODEX_ACCESS_CONTROL_DIR='/control/access-web',
                   RUNNER_STATE_DIR='/native/access-web')
        container['env'] = [{'name': k, 'value': v} for k, v in env.items()] + [
            {'name': 'RUNNER_POD_UID', 'valueFrom': {'fieldRef': {'fieldPath': 'metadata.uid'}}}]
        probe = container['readinessProbe']['exec']['command'][2]
        # Keep reachable health/history when auth expires; expose reason instead of empty Service.
        container['readinessProbe']['exec']['command'][2] = probe.replace("v['available'] is True and ", '')
    if part == 'init':
        container = value['spec']['template']['spec']['containers'][0]
        container['command'][2] = ("import os; from pathlib import Path; from runner.binding import initialize_binding; "
            "initialize_binding('/native/access-web',os.environ['RUNNER_ACCOUNT_ID'],os.environ['RUNNER_STATE_ID']); "
            "p=Path('/control/access-web'); assert not p.exists(); p.mkdir(mode=0o700)")
        container['volumeMounts'].append({'name': 'control', 'mountPath': '/control'})
        value['spec']['template']['spec']['volumes'].append(
            {'name': 'control', 'persistentVolumeClaim': {'claimName': 'trial-control'}})
        value['metadata']['name'] = 'access-web-state-init-v1'
    if part == 'init-policy':
        value['metadata']['name'] = 'access-web-state-init-deny-v1'
    if part == 'policy':
        value['items'][0]['metadata']['name'] = 'access-web-boundary-v1'
        value['items'][1]['metadata']['name'] = 'hosting-access-web-link-v1'
        # This is an additive callback link, not the hosting API's complete policy.
        # Do not flip the existing API into deny mode or proxy/restrict its DB/OAuth DNS.
        bridge = value['items'][1]['spec']
        bridge['enableDefaultDeny'] = {'ingress': False, 'egress': False}
        bridge['egress'] = bridge['egress'][:1]  # runner8080 only; baseline owns API DNS/DB/GitHub
    return value


def patch(snapshot, inputs, current, action):
    if action not in {'runner-start', 'runner-stop'}:
        return base.patch(snapshot, inputs, current, action)
    # Reuse the guarded UID/RV/full-defaulted-spec lifecycle checks with access emission.
    previous = base.emit
    try:
        base.emit = emit
        return base.patch(snapshot, inputs, current, action)
    finally:
        base.emit = previous


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('part', choices=('upgrade', 'rollback-upgrade', 'enable', 'disable',
                         'runner-start', 'runner-stop', 'api-input', 'config', 'init-policy', 'init', 'policy', 'runner', 'service'))
    parser.add_argument('--images', required=True)
    parser.add_argument('--snapshot', required=True)
    parser.add_argument('--inputs', required=True)
    parser.add_argument('--current')
    args = parser.parse_args()
    try:
        configure(json.loads(Path(args.images).read_text()))
        snapshot, inputs = json.loads(Path(args.snapshot).read_text()), json.loads(Path(args.inputs).read_text())
        if set(inputs) != {'ownerId', 'stateId'}:
            raise ValueError()
        if args.part in {'upgrade', 'rollback-upgrade', 'enable', 'disable', 'runner-start', 'runner-stop'}:
            # API parts are unchanged base guarded mutations; bind new image and names.
            result = patch(snapshot, inputs, json.loads(Path(args.current).read_text()), args.part)
        else:
            result = emit(snapshot, inputs, args.part)
        print(json.dumps(result, indent=2))
    except Exception:
        parser.exit(1, 'Access web package refused; private values are not printed\n')


if __name__ == '__main__':
    main()
