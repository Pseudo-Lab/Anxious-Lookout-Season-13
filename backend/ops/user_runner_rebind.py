"""Offline image-only rebind candidate; never calls Kubernetes or the database.

Run inside Docker as the private files' owner. Inputs must be current snapshots
from a quiesced API. Patches fence the exact Pod/Secret UID and resourceVersion.
The operator must verify image provenance, container replacement and live state.
"""
import argparse
import base64
from copy import deepcopy
import json
from pathlib import Path
from uuid import UUID

from app.user_runner_runtime import plan_from
from runner.auth import read_private, write_private
from runner.lifecycle import require


def patch(obj, path, value):
    metadata = obj['metadata']
    require(str(UUID(metadata['uid'])) == metadata['uid'])
    require(isinstance(metadata['resourceVersion'], str) and metadata['resourceVersion'])
    require(not metadata.get('deletionTimestamp'))
    return [
        {'op': 'test', 'path': '/metadata/uid', 'value': metadata['uid']},
        {'op': 'test', 'path': '/metadata/resourceVersion', 'value': metadata['resourceVersion']},
        {'op': 'test', 'path': '/spec' if path.startswith('/spec/') else '/data',
         'value': deepcopy(obj['spec' if path.startswith('/spec/') else 'data'])},
        {'op': 'replace', 'path': path, 'value': value},
    ]


def candidate(runtime, pod, secret, image):
    require(runtime['version'] == 1)
    old = plan_from(runtime['plan'])
    require(image != old.image)
    updated = deepcopy(runtime)
    updated['plan']['image'] = image
    new = plan_from(updated['plan'])  # Includes digest-only image validation.
    require(new.identity == old.identity)
    require(pod['kind'] == 'Pod' and pod['metadata']['name'] == old.identity.name
            and pod['metadata']['namespace'] == runtime['namespace'])
    require(pod['metadata'].get('labels') == old.manifest(runtime['namespace'])['metadata']['labels'])
    require(not pod['metadata'].get('annotations') and not pod['metadata'].get('finalizers'))
    containers = pod['spec']['containers']
    require(len(containers) == 1 and containers[0]['name'] == 'runner' and containers[0]['image'] == old.image)
    # With restartPolicy Never, an already exited container cannot be repaired
    # by assuming image mutation will restart it. Only a running one is eligible.
    status = pod.get('status', {})
    states = status.get('containerStatuses', [])
    require(status.get('phase') == 'Running' and len(states) == 1
            and states[0]['name'] == 'runner' and bool(states[0].get('containerID'))
            and set(states[0].get('state', {})) == {'running'})
    require(secret['kind'] == 'Secret' and secret['metadata']['namespace'] == 'm2-hosting'
            and secret['metadata']['name'] == 'hosting-user-runner-v1'
            and not secret.get('immutable', False))
    require(set(secret['data']) == {'runtime.json', 'transport-token'})
    require(json.loads(base64.b64decode(secret['data']['runtime.json'], validate=True)) == runtime)
    require(len(base64.b64decode(secret['data']['transport-token'], validate=True)) >= 32)
    encoded = base64.b64encode((json.dumps(updated, indent=2)+'\n').encode()).decode()
    return updated, patch(pod, '/spec/containers/0/image', image), patch(secret, '/data/runtime.json', encoded)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', required=True)
    parser.add_argument('--pod', required=True)
    parser.add_argument('--binding-secret', required=True)
    parser.add_argument('--image', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        result = candidate(read_private(Path(args.runtime)), read_private(Path(args.pod)),
                           read_private(Path(args.binding_secret)), args.image)
        output = Path(args.output)
        require(output.is_absolute() and output == output.resolve())
        output.mkdir(mode=0o700)  # Existing/partial output is never overwritten.
        for name, value in zip(('runtime.json', 'pod-image-patch.json', 'binding-secret-patch.json'), result):
            write_private(output/name, value)
    except Exception:
        parser.exit(1, 'Image rebind candidate refused; retain private inputs/output. No live changes made.\n')
    print('PASS offline image-only candidate; live image/state/quiescence not verified. Patches contain private data.')


if __name__ == '__main__':
    main()
