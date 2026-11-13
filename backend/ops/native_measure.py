"""#68 development-VM measurement Pods: the runner Pod's own security settings, measurement-only volumes.

The Pod is derived from `runner_kube.Plan.manifest`, so its Pod and container security context, resources,
host namespaces, service-account token and service links are the runner's, field for field. Only what a
measurement must not carry changes: no PVC, no Secret, no projected broker token, no environment, and the
command is `sleep infinity` (the measurement runs its own commands with `kubectl exec`). No SELinux type is
set, so the Pod runs as `container_t` like the runner. Development VM only.

    python -m ops.native_measure <name> <namespace> <runner image digest reference>   # prints the Pod JSON
"""
import json
import sys
from copy import deepcopy
from uuid import UUID

from app.runner_kube import Identity, Plan, Reference

POD_FIELDS = ('automountServiceAccountToken', 'restartPolicy', 'terminationGracePeriodSeconds', 'hostNetwork',
              'hostPID', 'hostIPC', 'enableServiceLinks', 'dnsPolicy', 'securityContext')
PURPOSE = 'native68-measure'


def runner_manifest(image):
    """The runner Pod for a fixed throwaway identity; only its spec fields are reused."""
    plan = Plan(Identity(UUID(int=1), UUID(int=2), 1), image,
                Reference('native', UUID(int=3), 'native-pv'), Reference('control', UUID(int=4), 'control-pv'),
                Reference('transport', UUID(int=5)),
                'http://api.m2-hosting.svc.cluster.local:8080/api/internal/research/tools', 'runner-workload')
    return plan.manifest('codex-runner')


def measurement_pod(name, namespace, image):
    runner = runner_manifest(image)
    spec, container = runner['spec'], runner['spec']['containers'][0]
    tmp = next(volume for volume in spec['volumes'] if volume['name'] == 'tmp')
    return {
        'apiVersion': 'v1', 'kind': 'Pod',
        'metadata': {'name': name, 'namespace': namespace, 'labels': {'anxious.io/purpose': PURPOSE}},
        'spec': {**{field: deepcopy(spec[field]) for field in POD_FIELDS},
                 'containers': [{'name': 'runner', 'image': image, 'imagePullPolicy': 'Never',
                                 'command': ['sleep', 'infinity'],
                                 'resources': deepcopy(container['resources']),
                                 'securityContext': deepcopy(container['securityContext']),
                                 'volumeMounts': [{'name': 'native', 'mountPath': '/state'},
                                                  {'name': 'control', 'mountPath': '/control'},
                                                  {'name': 'tmp', 'mountPath': '/tmp'}]}],
                 'volumes': [{'name': 'native', 'emptyDir': {'sizeLimit': '1Gi'}},
                             {'name': 'control', 'emptyDir': {'sizeLimit': '16Mi'}},
                             deepcopy(tmp)]}}


def main():
    name, namespace, image = sys.argv[1:4]
    print(json.dumps(measurement_pod(name, namespace, image), indent=1, sort_keys=True))


if __name__ == '__main__':
    main()
