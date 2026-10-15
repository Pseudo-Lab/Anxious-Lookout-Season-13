"""Fixed app-selector control; refuse any other delta from the recorded render."""
import argparse
import copy
import json
from pathlib import Path

import access_runner_dns_prepare as direct

CONTROL_APP = 'access-runner-dns-control'
CONTROL_JOB = 'access-runner-selector-dns-v1'
JOB_NAME_LABELS = ('job-name', 'batch.kubernetes.io/job-name')
JOB_UID_LABELS = ('controller-uid', 'batch.kubernetes.io/controller-uid')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def services_exclude_control(services, labels, job_name=CONTROL_JOB):
    # Supply a fresh raw /api/v1/namespaces/codex-trial/services response.
    metadata = services.get('metadata', {})
    if job_name not in (CONTROL_JOB, 'access-runner-dns-l4-v1', 'access-runner-dns-l7-v1'):
        raise ValueError()
    if (services.get('kind') not in ('List', 'ServiceList') or services.get('apiVersion') != 'v1'
            or not isinstance(metadata, dict) or not isinstance(metadata.get('resourceVersion'), str)
            or not metadata['resourceVersion'] or not isinstance(services.get('items'), list)
            or not isinstance(metadata.get('continue', ''), str) or metadata.get('continue', '') != ''
            or type(metadata.get('remainingItemCount', 0)) is not int or metadata.get('remainingItemCount', 0) != 0):
        raise ValueError()
    # Predict name labels without changing the declared template. This check also
    # accepts server-defaulted/actual labels, but UID-based Service selectors stay unsupported.
    effective_labels = copy.deepcopy(labels)
    for key in JOB_NAME_LABELS:
        if key in effective_labels and effective_labels[key] != job_name:
            raise ValueError()
        effective_labels[key] = job_name
    names = set()
    typed = services['kind'] == 'ServiceList'
    for service in services['items']:
        metadata = service['metadata']
        name = metadata['name']
        # A typed v1 ServiceList already declares []Service; nested TypeMeta may be absent.
        # Explicit conflicting TypeMeta is never accepted, nor is omission in a generic List.
        if (service.get('kind', 'Service' if typed else None) != 'Service'
                or service.get('apiVersion', 'v1' if typed else None) != 'v1'
                or metadata.get('namespace') != 'codex-trial' or not isinstance(name, str)
                or not name or name in names or not isinstance(metadata.get('uid'), str)
                or not metadata['uid']):
            raise ValueError()
        names.add(name)
        selector = service['spec'].get('selector', {})
        if (not isinstance(selector, dict)
                or any(not isinstance(k, str) or not k or not isinstance(v, str) for k, v in selector.items())):
            raise ValueError()
        if any(key in selector for key in JOB_UID_LABELS):
            raise ValueError()
        if selector and all(effective_labels.get(key) == value for key, value in selector.items()):
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
    # A controller UID must never be retained from an earlier declared template.
    if any(key in labels for key in JOB_UID_LABELS):
        raise ValueError()
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
