"""Fixed app-selector control; refuse any other delta from the recorded render."""
import argparse
import copy
import json
from pathlib import Path

import access_runner_dns_prepare as direct

CONTROL_APP = 'access-runner-dns-control'
CONTROL_JOB = 'access-runner-selector-dns-v1'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def services_exclude_control(services, labels):
    # Supply a fresh, unfiltered `get services -n codex-trial -o json` result.
    if (services.get('kind') not in ('List', 'ServiceList') or services.get('apiVersion') != 'v1'
            or not isinstance(services.get('metadata', {}).get('resourceVersion'), str)
            or not services['metadata']['resourceVersion'] or not isinstance(services.get('items'), list)):
        raise ValueError()
    names = set()
    for service in services['items']:
        metadata = service['metadata']
        name = metadata['name']
        if (service.get('kind') != 'Service' or service.get('apiVersion') != 'v1'
                or metadata.get('namespace') != 'codex-trial' or not isinstance(name, str)
                or not name or name in names or not isinstance(metadata.get('uid'), str)
                or not metadata['uid']):
            raise ValueError()
        names.add(name)
        selector = service['spec'].get('selector', {})
        if (not isinstance(selector, dict)
                or any(not isinstance(k, str) or not k or not isinstance(v, str) for k, v in selector.items())):
            raise ValueError()
        if selector and all(labels.get(key) == value for key, value in selector.items()):
            raise ValueError()


def emit(snapshot, inputs, images, baseline, services):
    original = direct.emit(snapshot, inputs, images)
    # Compare the clean recorded source render, not a defaulted/UID-bearing live Job.
    if canonical(original) != canonical(baseline):
        raise ValueError()
    control = copy.deepcopy(original)
    control['metadata']['name'] = CONTROL_JOB
    labels = control['spec']['template']['metadata']['labels']
    labels['app'] = CONTROL_APP
    services_exclude_control(services, labels)
    return control


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('snapshot', 'inputs', 'images', 'baseline', 'services'):
        parser.add_argument('--'+key, required=True)
    args = parser.parse_args()
    try:
        read = lambda key: json.loads(Path(getattr(args, key)).read_text())
        print(json.dumps(emit(*(read(key) for key in ('snapshot', 'inputs', 'images', 'baseline', 'services'))), indent=2))
    except Exception:
        parser.exit(1, 'Runner selector control refused; private metadata is not printed\n')
