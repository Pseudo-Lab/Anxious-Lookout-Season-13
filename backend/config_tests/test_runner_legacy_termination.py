"""#66: termination-only identity check for a Pod rendered by an earlier release. No DB or cluster."""
from argparse import Namespace
from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest

from app.runner_controller import Identity
from app.runner_kube import KubePods, Plan, Reference, ExecutorPlan, LABEL
from ops.runner_stop_observer import Observer, legacy_target
from runner.lifecycle import Refused
from config_tests.test_runner_kube import Server

# Production images of owner 3cb453bf's gen-9 Pod (#72 F "before" inventory, 2026-10-30).
RUNNER = 'docker.io/library/anxious-s13-issue28-runner@sha256:7b10890cf35829706b62c940103a31391cf842b610e80442a8a2d0e788176d26'
EXECUTOR = 'docker.io/library/anxious-s13-issue23-shell-executor@sha256:bae153febf222fbe2919cc50afd7c1564730b77bd0a45fbb036f567e5035d958'


def legacy_manifest(plan, namespace):
    """The Pod spec as rendered by d026b772 (production api release before #59), with executor."""
    identity = plan.identity
    labels = {LABEL+'owner': str(identity.owner), LABEL+'state': str(identity.state_id),
              LABEL+'generation': str(identity.generation), LABEL+'managed': 'user-runner'}
    env = {'APP_ENV': 'operator-service', 'RUNNER_ACCOUNT_ID': str(identity.owner),
           'RUNNER_STATE_ID': str(identity.state_id), 'RUNNER_STATE_DIR': '/state',
           'RUNNER_TOKEN_FILE': '/transport/token', 'CODEX_MODEL': 'gpt-6.1-sol',
           'CODEX_AUTH_MODE': 'access-service', 'CODEX_EXECUTION_SCOPE': 'operator-access-service',
           'CODEX_ACCESS_CONTROL_DIR': '/control', 'CODEX_OPERATOR_ACCOUNT_FILE': '/control/operator-account',
           'RESEARCH_TOOL_CALLBACK_URL': plan.callback}
    security = {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}}
    shell = [{'name': 'SHELL_WALL_SECONDS', 'value': '30'}, {'name': 'SHELL_OUTPUT_BYTES', 'value': '65536'}]
    return {'apiVersion': 'v1', 'kind': 'Pod',
        'metadata': {'name': identity.name, 'namespace': namespace, 'labels': labels},
        'spec': {'serviceAccountName': plan.service_account, 'automountServiceAccountToken': False,
                 'restartPolicy': 'Never', 'terminationGracePeriodSeconds': 55,
                 'hostNetwork': False, 'hostPID': False, 'hostIPC': False,
                 'enableServiceLinks': False, 'dnsPolicy': 'ClusterFirst', 'shareProcessNamespace': False,
                 'securityContext': {'runAsUser': 10001, 'runAsGroup': 10001, 'runAsNonRoot': True,
                                     'seccompProfile': {'type': 'RuntimeDefault'}},
                 'initContainers': [{'name': 'prepare-shell', 'image': plan.image, 'imagePullPolicy': 'IfNotPresent',
                     'command': ['python', '-I', '/app/runner/prepare_shell.py'], 'securityContext': security,
                     'resources': {'requests': {'cpu': '10m', 'memory': '32Mi'}, 'limits': {'cpu': '100m', 'memory': '64Mi'}},
                     'volumeMounts': [{'name': 'native', 'mountPath': '/state'}]}],
                 'containers': [{'name': 'runner', 'image': plan.image, 'imagePullPolicy': 'IfNotPresent',
                    'command': ['uvicorn', 'runner.app:create_app', '--factory', '--host', '0.0.0.0', '--port', '8080',
                                '--workers', '1', '--no-access-log', '--no-proxy-headers'],
                    'env': [{'name': key, 'value': value} for key, value in env.items()] +
                        [{'name': 'RUNNER_POD_UID', 'valueFrom': {'fieldRef': {'apiVersion': 'v1', 'fieldPath': 'metadata.uid'}}},
                         {'name': 'SHELL_EXECUTOR_ENABLED', 'value': 'true'}] + shell,
                    'ports': [{'name': 'runner', 'containerPort': 8080, 'protocol': 'TCP'}],
                    'resources': {'requests': {'cpu': '100m', 'memory': '256Mi'}, 'limits': {'cpu': '1', 'memory': '1Gi'}},
                    'securityContext': security,
                    'volumeMounts': [{'name': 'native', 'mountPath': '/state'}, {'name': 'control', 'mountPath': '/control'},
                                     {'name': 'transport', 'mountPath': '/transport', 'readOnly': True},
                                     {'name': 'tmp', 'mountPath': '/tmp'},
                                     {'name': 'native', 'mountPath': '/run/shell-executor', 'subPath': 'shell-ipc'}]},
                    {'name': 'shell-executor', 'image': plan.executor.image, 'imagePullPolicy': 'IfNotPresent',
                     'command': ['python', '-I', '/opt/executor/server.py'],
                     'securityContext': {**security, 'runAsUser': 20002, 'runAsGroup': 10001, 'runAsNonRoot': True},
                     'resources': {'requests': {'cpu': '50m', 'memory': '64Mi'}, 'limits': {'cpu': '500m', 'memory': '128Mi'}},
                     'env': deepcopy(shell),
                     'volumeMounts': [{'name': 'native', 'mountPath': '/state/workspace', 'subPath': 'workspace'},
                                      {'name': 'native', 'mountPath': '/run/shell-executor', 'subPath': 'shell-ipc'},
                                      {'name': 'shell-tmp', 'mountPath': '/tmp'}]}],
                 'volumes': [{'name': 'native', 'persistentVolumeClaim': {'claimName': plan.native.name}},
                             {'name': 'control', 'persistentVolumeClaim': {'claimName': plan.control.name}},
                             {'name': 'transport', 'secret': {'secretName': plan.transport.name, 'defaultMode': 292,
                                                            'items': [{'key': 'token', 'path': 'token'}]}},
                             {'name': 'tmp', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '64Mi'}},
                             {'name': 'shell-tmp', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '16Mi'}}]}}


@pytest.fixture
def plan():
    return Plan(Identity(uuid4(), uuid4(), 9), RUNNER,
                Reference('native', uuid4(), 'native-pv'), Reference('control', uuid4(), 'control-pv'),
                Reference('transport', uuid4()), 'https://api.invalid/tools', 'runner', ExecutorPlan(EXECUTOR))


@pytest.fixture
def server(plan):
    result = Server(plan)
    pod = legacy_manifest(plan, 'isolated')
    pod['metadata'].update(uid=str(uuid4()), creationTimestamp='2026-10-26T03:09:53Z')
    pod['spec'].update(nodeName='anxious-lookout', schedulerName='default-scheduler', priority=0,
                       preemptionPolicy='PreemptLowerPriority', serviceAccount='runner')
    pod['status'] = {'phase': 'Running', 'podIP': '10.42.0.9'}
    result.pod = pod
    return result


@pytest.fixture
def port(plan, server):
    result = KubePods('https://kube.invalid', 'isolated', 'k'*40, None, [plan], _transport=httpx.MockTransport(server))
    yield result
    result.close()


def live(port, plan):
    return port._request('GET', 'pods', plan.identity.name)


def test_legacy_pod_is_refused_by_exact_check_and_accepted_by_identity(port, server, plan):
    with pytest.raises(Refused):
        port.lookup(plan.identity)
    uid = UUID(server.pod['metadata']['uid'])
    pod = port.terminating_pod(live(port, plan), plan, uid)
    assert pod.identity == plan.identity and pod.uid == uid
    # GETs only: Pod, two PVCs, Secret. Nothing created or deleted.
    assert {method for method, _ in server.requests} == {'GET'}


def test_current_spec_pod_is_also_accepted_by_identity(port, server, plan):
    server.pod = None
    created = port.create_once(plan.identity)
    assert port.terminating_pod(live(port, plan), plan, created.uid) == created


@pytest.mark.parametrize('damage', ['uid', 'owner', 'state', 'generation', 'managed', 'extra-label', 'annotation',
                                    'finalizer', 'deleting', 'owner-reference', 'namespace', 'name', 'runner-image',
                                    'executor-image', 'init-image', 'extra-init', 'sidecar', 'duplicate-name', 'no-executor',
                                    'ephemeral', 'hostNetwork', 'hostPID', 'hostIPC', 'share-pid', 'serviceaccount',
                                    'legacy-serviceaccount', 'automount', 'automount-missing', 'kind'])
def test_identity_mismatch_fails_closed(port, server, plan, damage):
    obj = server.pod
    meta, spec = obj['metadata'], obj['spec']
    uid = UUID(meta['uid'])
    if damage == 'uid': uid = uuid4()
    elif damage == 'owner': meta['labels'][LABEL+'owner'] = str(uuid4())
    elif damage == 'state': meta['labels'][LABEL+'state'] = str(uuid4())
    elif damage == 'generation': meta['labels'][LABEL+'generation'] = '10'
    elif damage == 'managed': meta['labels'][LABEL+'managed'] = 'other'
    elif damage == 'extra-label': meta['labels']['app'] = 'trusted-api'
    elif damage == 'annotation': meta['annotations'] = {'k8s.v1.cni.cncf.io/networks': 'foreign'}
    elif damage == 'finalizer': meta['finalizers'] = ['example.invalid/hold']
    elif damage == 'deleting': meta['deletionTimestamp'] = '2026-10-31T17:14:09Z'
    elif damage == 'owner-reference': meta['ownerReferences'] = [{'uid': str(uuid4())}]
    elif damage == 'namespace': meta['namespace'] = 'foreign'
    elif damage == 'name': meta['name'] = 'runner-foreign'
    elif damage == 'runner-image': spec['containers'][0]['image'] = 'foreign@sha256:'+'f'*64
    elif damage == 'executor-image': spec['containers'][1]['image'] = RUNNER
    elif damage == 'init-image': spec['initContainers'][0]['image'] = EXECUTOR
    elif damage == 'extra-init': spec['initContainers'].append({'name': 'debug', 'image': RUNNER})
    elif damage == 'sidecar': spec['containers'].append({'name': 'debug', 'image': RUNNER})
    elif damage == 'duplicate-name': spec['containers'].append(deepcopy(spec['containers'][0]))
    elif damage == 'no-executor': del spec['containers'][1]
    elif damage == 'ephemeral': spec['ephemeralContainers'] = [{'name': 'debug', 'image': RUNNER}]
    elif damage in ('hostNetwork', 'hostPID', 'hostIPC'): spec[damage] = True
    elif damage == 'share-pid': spec['shareProcessNamespace'] = True
    elif damage == 'serviceaccount': spec['serviceAccountName'] = 'admin'
    elif damage == 'legacy-serviceaccount': spec['serviceAccount'] = 'admin'
    elif damage == 'automount': spec['automountServiceAccountToken'] = True
    elif damage == 'automount-missing': del spec['automountServiceAccountToken']
    elif damage == 'kind': obj['kind'] = 'Deployment'
    with pytest.raises(Refused) as caught:
        port.terminating_pod(obj, plan, uid)
    assert str(caught.value) == 'Kubernetes termination identity requires reconciliation'
    assert {method for method, _ in server.requests} <= {'GET'}


@pytest.mark.parametrize('damage', ['pvc-uid', 'pv', 'unbound', 'secret-uid', 'mutable-secret'])
def test_reference_drift_fails_closed(port, server, plan, damage):
    native, secret = server.objects['native'], server.objects['transport']
    if damage == 'pvc-uid': native['metadata']['uid'] = str(uuid4())
    elif damage == 'pv': native['spec']['volumeName'] = 'foreign-pv'
    elif damage == 'unbound': native['status']['phase'] = 'Pending'
    elif damage == 'secret-uid': secret['metadata']['uid'] = str(uuid4())
    elif damage == 'mutable-secret': secret['immutable'] = False
    with pytest.raises(Refused):
        port.terminating_pod(server.pod, plan, UUID(server.pod['metadata']['uid']))


def test_non_uuid_pod_uid_refused(port, server, plan):
    with pytest.raises(Refused):
        port.terminating_pod(server.pod, plan, server.pod['metadata']['uid'])


class Pods:
    def __init__(self):
        self.calls = []

    def _pod(self, obj, plan):
        self.calls.append('exact')
        return SimpleNamespace(uid=obj['uid'])

    def terminating_pod(self, obj, plan, uid):
        self.calls.append('identity')
        return SimpleNamespace(uid=uid)


def test_observer_uses_identity_only_for_the_named_owner_and_pod(tmp_path):
    owner, pod = uuid4(), uuid4()
    runtime = SimpleNamespace(pods=Pods(), plan=object())
    obj = {'uid': pod}
    named = Observer(None, None, None, tmp_path, legacy_pod=(owner, pod))
    assert named.stopping_pod(runtime, obj, SimpleNamespace(owner=owner, pod_uid=pod)) == pod
    # Another owner, or another UID of the same owner (a later generation), keeps the exact check.
    named.stopping_pod(runtime, obj, SimpleNamespace(owner=uuid4(), pod_uid=pod))
    named.stopping_pod(runtime, obj, SimpleNamespace(owner=owner, pod_uid=uuid4()))
    Observer(None, None, None, tmp_path).stopping_pod(runtime, obj, SimpleNamespace(owner=owner, pod_uid=pod))
    assert runtime.pods.calls == ['identity', 'exact', 'exact', 'exact']


@pytest.mark.parametrize('value', [(str(uuid4()), uuid4()), (uuid4(),), [uuid4(), uuid4()], uuid4()])
def test_observer_rejects_malformed_legacy_target(tmp_path, value):
    with pytest.raises(Refused):
        Observer(None, None, None, tmp_path, legacy_pod=value)


def test_legacy_option_requires_one_named_owner_without_batch_selection():
    owner, pod = uuid4(), uuid4()
    base = dict(owner=owner, cursor_file=None, idle=False, legacy_pod_uid=pod)
    assert legacy_target(Namespace(**base)) == (owner, pod)
    assert legacy_target(Namespace(**{**base, 'legacy_pod_uid': None})) is None
    for change in ({'owner': None}, {'cursor_file': '/cursor.json'}, {'idle': True}):
        with pytest.raises(Refused):
            legacy_target(Namespace(**{**base, **change}))
