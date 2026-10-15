"""Guarded single callback-link CNP correction; never touches runner/base policies."""
import argparse
import json
from pathlib import Path

import access_web_prepare as package


def patch(current, inputs, images):
    package.configure(images)
    selected = dict(inputs, providerHosts=['chatgpt.com'], procedureRef='ACCESS-WEB-TEST.md')
    old = package.base.policies(selected)['items'][1]['spec']
    new = dict(old, enableDefaultDeny={'ingress': False, 'egress': False}, egress=old['egress'][:1])
    if (current.get('kind') != 'CiliumNetworkPolicy' or current['metadata']['namespace'] != 'm2-hosting'
            or current['metadata']['name'] != 'hosting-access-web-link-v1'
            or not current['metadata'].get('uid') or not current['metadata'].get('resourceVersion')
            or current['spec'] != old):
        raise ValueError()
    return [{'op': 'test', 'path': '/metadata/uid', 'value': current['metadata']['uid']},
            {'op': 'test', 'path': '/metadata/resourceVersion', 'value': current['metadata']['resourceVersion']},
            {'op': 'test', 'path': '/spec', 'value': old}, {'op': 'replace', 'path': '/spec', 'value': new}]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--current', required=True); parser.add_argument('--inputs', required=True); parser.add_argument('--images', required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(patch(json.loads(Path(args.current).read_text()), json.loads(Path(args.inputs).read_text()),
                               json.loads(Path(args.images).read_text())), indent=2))
    except Exception:
        parser.exit(1, 'Callback-link patch refused; private values are not printed\n')
