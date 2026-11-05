"""Namespace-scoped, no-retry Pod REST port. No credentials loaded at import.

Plans are operator-reviewed private bindings, never HTTP/user input. PVCs, private
state markers, immutable transport Secrets and network policy must already exist.
This adapter creates only the Pod; it cannot delete, exec, provision or refresh auth.
"""
from copy import deepcopy
from dataclasses import dataclass
import base64
import json
import ipaddress
import re
import ssl
import time
from urllib.parse import urlsplit
from uuid import UUID

import httpx
import httpcore

from .runner_controller import Identity, Pod
from . import runner_http_budget as budget
from runner.lifecycle import require, Refused

LABEL = 'anxious.io/'
# Measured (#59 review): DELETE to Pod gone 3.6s idle, 4.0s with a running shell command; both
# containers exit 0. 20s covers the app-server stop escalation (EOF, terminate, kill: 5s each);
# it is not an upper bound of every close path (Native worker join alone allows 20s).
GRACE_SECONDS = 20
# #60: runner pulls operator access with a short-lived token bound to its Pod.
BROKER_URL = 'http://codex-broker.codex-broker.svc:8080'
BROKER_AUDIENCE = 'codex-broker'


def name(value):
    require(isinstance(value, str) and len(value) <= 63 and
            re.fullmatch(r'[a-z0-9](?:[-a-z0-9]*[a-z0-9])?', value) is not None)
    return value


@dataclass(frozen=True)
class Reference:
    name: str
    uid: UUID
    volume: str | None = None

    def __post_init__(self):
        name(self.name)
        require(isinstance(self.uid, UUID))
        if self.volume is not None:
            name(self.volume)


@dataclass(frozen=True)
class ExecutorPlan:
    image: str
    wall_seconds: int = 30
    output_bytes: int = 65536

    def __post_init__(self):
        require(isinstance(self.image, str) and re.fullmatch(r'[a-zA-Z0-9./:_-]+@sha256:[a-f0-9]{64}', self.image))
        require(type(self.wall_seconds) is int and 1 <= self.wall_seconds <= 300)
        require(type(self.output_bytes) is int and 1024 <= self.output_bytes <= 1048576)


@dataclass(frozen=True)
class Plan:
    identity: Identity
    image: str
    native: Reference
    control: Reference
    transport: Reference
    callback: str
    service_account: str
    executor: ExecutorPlan | None = None

    def __post_init__(self):
        require(isinstance(self.identity, Identity))
        require(isinstance(self.image, str) and re.fullmatch(r'[a-zA-Z0-9./:_-]+@sha256:[a-f0-9]{64}', self.image))
        require(all(isinstance(ref, Reference) for ref in (self.native, self.control, self.transport)))
        require(self.native.volume is not None and self.control.volume is not None and self.transport.volume is None)
        require(self.native.uid != self.control.uid and self.native.name != self.control.name
                and self.native.volume != self.control.volume)
        parsed = urlsplit(self.callback)
        internal = (parsed.scheme == 'http' and parsed.hostname is not None
                    and re.fullmatch(r'[a-z0-9-]+\.[a-z0-9-]+\.svc\.cluster\.local', parsed.hostname)
                    and parsed.port == 8080 and parsed.path == '/api/internal/research/tools')
        require((parsed.scheme == 'https' or internal) and parsed.hostname and not parsed.username and not parsed.password
                and not parsed.query and not parsed.fragment)
        name(self.service_account)
        require(self.executor is None or isinstance(self.executor, ExecutorPlan))

    def manifest(self, namespace):
        identity = self.identity
        labels = {LABEL+'owner': str(identity.owner), LABEL+'state': str(identity.state_id),
                  LABEL+'generation': str(identity.generation), LABEL+'managed': 'user-runner'}
        env = {'APP_ENV': 'operator-service', 'RUNNER_ACCOUNT_ID': str(identity.owner),
               'RUNNER_STATE_ID': str(identity.state_id), 'RUNNER_STATE_DIR': '/state',
               'CODEX_MODEL': 'gpt-6.1-sol',
               'CODEX_AUTH_MODE': 'access-service', 'CODEX_EXECUTION_SCOPE': 'operator-access-service',
               'CODEX_ACCESS_CONTROL_DIR': '/control', 'CODEX_OPERATOR_ACCOUNT_FILE': '/control/operator-account',
               'RESEARCH_TOOL_CALLBACK_URL': self.callback, 'BROKER_URL': BROKER_URL,
               'BROKER_TOKEN_FILE': '/run/codex-broker/token'}
        manifest = {'apiVersion': 'v1', 'kind': 'Pod',
                'metadata': {'name': identity.name, 'namespace': namespace, 'labels': labels},
                'spec': {'serviceAccountName': self.service_account, 'automountServiceAccountToken': False,
                         'restartPolicy': 'Never', 'terminationGracePeriodSeconds': GRACE_SECONDS,
                         'hostNetwork': False, 'hostPID': False, 'hostIPC': False,
                         'enableServiceLinks': False, 'dnsPolicy': 'ClusterFirst',
                         'securityContext': {'runAsUser': 10001, 'runAsGroup': 10001, 'runAsNonRoot': True,
                                             'seccompProfile': {'type': 'RuntimeDefault'}},
                         'containers': [{'name': 'runner', 'image': self.image, 'imagePullPolicy': 'IfNotPresent',
                            'command': ['uvicorn', 'runner.app:create_app', '--factory', '--host', '0.0.0.0', '--port', '8080',
                                        '--workers', '1', '--no-access-log', '--no-proxy-headers'],
                            # Transport token as an env reference, runner container only: a 0400
                            # Secret file cannot be owned by UID 10001 without a Pod fsGroup, and
                            # fsGroup would relabel the private PVCs group-writable (#59).
                            'env': [{'name': key, 'value': value} for key, value in env.items()] +
                                [{'name': 'RUNNER_TOKEN', 'valueFrom': {'secretKeyRef': {'name': self.transport.name, 'key': 'token'}}},
                                 {'name': 'RUNNER_POD_UID', 'valueFrom': {'fieldRef': {'apiVersion': 'v1', 'fieldPath': 'metadata.uid'}}}],
                            'ports': [{'name': 'runner', 'containerPort': 8080, 'protocol': 'TCP'}],
                            'resources': {'requests': {'cpu': '100m', 'memory': '192Mi'}, 'limits': {'cpu': '500m', 'memory': '768Mi'}},
                            'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                                                'capabilities': {'drop': ['ALL']}},
                            'volumeMounts': [{'name': 'native', 'mountPath': '/state'}, {'name': 'control', 'mountPath': '/control'},
                                             {'name': 'broker-token', 'mountPath': '/run/codex-broker', 'readOnly': True},
                                             {'name': 'tmp', 'mountPath': '/tmp'}]}],
                         'volumes': [{'name': 'native', 'persistentVolumeClaim': {'claimName': self.native.name}},
                                     {'name': 'control', 'persistentVolumeClaim': {'claimName': self.control.name}},
                                     # automountServiceAccountToken stays false; only this audience-bound token.
                                     {'name': 'broker-token', 'projected': {'defaultMode': 292, 'sources': [
                                         {'serviceAccountToken': {'audience': BROKER_AUDIENCE, 'expirationSeconds': 600, 'path': 'token'}}]}},
                                     {'name': 'tmp', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '64Mi'}}]}}
        if self.executor is not None:
            self._executor_manifest(manifest)
        return manifest

    def _executor_manifest(self, manifest):
        executor = self.executor
        spec = manifest['spec']
        spec['shareProcessNamespace'] = False
        # The native PVC root remains private. Only these pre-created directories
        # are mounted into the executor via fixed subPath binds.
        security = {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                    'capabilities': {'drop': ['ALL']}}
        spec['initContainers'] = [{'name': 'prepare-shell', 'image': self.image, 'imagePullPolicy': 'IfNotPresent',
            'command': ['python', '-I', '/app/runner/prepare_shell.py'], 'securityContext': security,
            'resources': {'requests': {'cpu': '10m', 'memory': '32Mi'}, 'limits': {'cpu': '100m', 'memory': '64Mi'}},
            'volumeMounts': [{'name': 'native', 'mountPath': '/state'}]}]
        runner = spec['containers'][0]
        runner['env'] += [{'name': 'SHELL_EXECUTOR_ENABLED', 'value': 'true'},
                          {'name': 'SHELL_WALL_SECONDS', 'value': str(executor.wall_seconds)},
                          {'name': 'SHELL_OUTPUT_BYTES', 'value': str(executor.output_bytes)}]
        runner['volumeMounts'].append({'name': 'native', 'mountPath': '/run/shell-executor', 'subPath': 'shell-ipc'})
        spec['containers'].append({'name': 'shell-executor', 'image': executor.image, 'imagePullPolicy': 'IfNotPresent',
            'command': ['python', '-I', '/opt/executor/server.py'],
            'securityContext': {**security, 'runAsUser': 20002, 'runAsGroup': 10001, 'runAsNonRoot': True},
            'resources': {'requests': {'cpu': '100m', 'memory': '128Mi'}, 'limits': {'cpu': '1', 'memory': '512Mi'}},
            'env': [{'name': 'SHELL_WALL_SECONDS', 'value': str(executor.wall_seconds)},
                    {'name': 'SHELL_OUTPUT_BYTES', 'value': str(executor.output_bytes)}],
            'volumeMounts': [{'name': 'native', 'mountPath': '/state/workspace', 'subPath': 'workspace'},
                             {'name': 'native', 'mountPath': '/run/shell-executor', 'subPath': 'shell-ipc'},
                             {'name': 'shell-tmp', 'mountPath': '/tmp'}]})
        # Disk-backed: a Memory medium would count the scratch pages against the 512Mi limit.
        spec['volumes'].append({'name': 'shell-tmp', 'emptyDir': {'sizeLimit': '256Mi'}})


class KubePods:
    def __init__(self, endpoint, namespace, bearer, ca_file, plans, *, timeout=2.0, deadline=10.0, _transport=None):
        parsed = urlsplit(endpoint)
        require(parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password
                and parsed.path in {'', '/'} and not parsed.query and not parsed.fragment)
        if _transport is None:
            try:
                ipaddress.ip_address(parsed.hostname)
            except ValueError:
                raise Refused('A literal Kubernetes API IP with a matching TLS certificate is required') from None
        self.namespace = name(namespace)
        require(isinstance(bearer, str) and len(bearer) >= 32 and not any(ch.isspace() for ch in bearer))
        require(type(timeout) in (float, int) and 0 < timeout <= 10 and type(deadline) in (float, int) and 0 < deadline <= 30)
        self.plans = {plan.identity: plan for plan in plans}
        require(len(self.plans) == len(plans))
        for plan in plans:
            for other in plans:
                if plan.identity.owner == other.identity.owner:
                    require(plan.identity.state_id == other.identity.state_id and
                            (plan.native, plan.control, plan.transport) == (other.native, other.control, other.transport))
        # Reject cross-owner reuse of storage, PV or transport credentials.
        refs = [(kind, ref.name, ref.uid, ref.volume, plan.identity.owner, plan.identity.state_id)
                for plan in plans for kind, ref in [('pvc', plan.native), ('pvc', plan.control), ('secret', plan.transport)]]
        for i, (kind, ref_name, uid, volume, owner, state) in enumerate(refs):
            for other_kind, other_name, other_uid, other_volume, other_owner, other_state in refs[:i]:
                overlap = kind == other_kind and (ref_name == other_name or uid == other_uid or (volume and volume == other_volume))
                require(not overlap or (owner, state) == (other_owner, other_state))
        self.deadline, self.timeout = deadline, timeout
        self.bearer_provider = None
        self.client = httpx.Client(base_url=endpoint.rstrip('/'), headers={'Authorization': 'Bearer '+bearer, 'Accept-Encoding': 'identity'},
            verify=ssl.create_default_context(cafile=ca_file), timeout=httpx.Timeout(timeout),
            follow_redirects=False, trust_env=False,
            transport=_transport if _transport is not None else budget.Transport(ssl.create_default_context(cafile=ca_file)))

    def close(self):
        self.client.close()

    def refresh_bearer(self):
        # Optional projected service-account token, rotated by kubelet. The
        # configured trusted file is never browser input or runner workload input.
        if self.bearer_provider is not None:
            bearer = self.bearer_provider()
            require(isinstance(bearer, str) and len(bearer) >= 32 and not any(c.isspace() for c in bearer))
            self.client.headers['Authorization'] = 'Bearer '+bearer

    def _request(self, method, resource, resource_name=None, *, body=None, missing=False):
        self.refresh_bearer()
        path = '/api/v1/namespaces/' + self.namespace + '/' + resource
        if resource_name is not None:
            path += '/' + name(resource_name)
        started = time.monotonic()
        token = budget.begin(started + self.deadline, lambda: time.monotonic())
        try:
            with self.client.stream(method, path, json=body, timeout=min(self.timeout, self.deadline)) as response:
                require(time.monotonic() - started < self.deadline)
                if missing and response.status_code == 404:
                    return None
                expected = {201} if method == 'POST' else {200, 202} if method == 'DELETE' else {200}
                require(response.status_code in expected)
                require(response.headers.get('content-encoding', 'identity') == 'identity')
                content = bytearray()
                for chunk in response.iter_raw():
                    require(time.monotonic() - started < self.deadline)
                    content.extend(chunk)
                    require(len(content) <= 2 * 1024 * 1024)
                require(time.monotonic() - started < self.deadline)
                value = json.loads(content)
                require(time.monotonic() - started < self.deadline)
                require(type(value) is dict)
                return value
        except (httpx.HTTPError, httpcore.TimeoutException, ValueError, TypeError, KeyError):
            # Do not expose response bodies, URLs, Authorization or Secret data.
            raise Refused('Kubernetes operation requires reconciliation') from None
        finally:
            budget.finish(token)

    def _metadata(self, obj, kind, resource_name, identity, uid=None, generation=False):
        require(obj.get('apiVersion') == 'v1' and obj.get('kind') == kind)
        metadata = obj.get('metadata', {})
        require(metadata.get('name') == resource_name and metadata.get('namespace') == self.namespace
                and not metadata.get('deletionTimestamp') and not metadata.get('ownerReferences'))
        actual_uid = UUID(metadata.get('uid', ''))
        require(str(actual_uid) == metadata['uid'] and (uid is None or actual_uid == uid))
        labels = metadata.get('labels', {})
        require(labels.get(LABEL+'owner') == str(identity.owner) and labels.get(LABEL+'state') == str(identity.state_id))
        if generation:
            require(labels.get(LABEL+'generation') == str(identity.generation) and labels.get(LABEL+'managed') == 'user-runner')
        return actual_uid

    def _references(self, plan):
        for ref in (plan.native, plan.control):
            claim = self._request('GET', 'persistentvolumeclaims', ref.name)
            self._metadata(claim, 'PersistentVolumeClaim', ref.name, plan.identity, ref.uid)
            require(claim.get('status', {}).get('phase') == 'Bound' and claim.get('spec', {}).get('volumeName') == ref.volume)
        secret = self._request('GET', 'secrets', plan.transport.name)
        self._metadata(secret, 'Secret', plan.transport.name, plan.identity, plan.transport.uid)
        require(secret.get('immutable') is True and secret.get('type') == 'Opaque'
                and type(secret.get('data')) is dict and set(secret['data']) == {'token'})
        token = base64.b64decode(secret['data']['token'], validate=True).decode('ascii')
        require(len(token) >= 32 and not any(ch.isspace() for ch in token))
        return token

    def _pod(self, obj, plan):
        uid = self._metadata(obj, 'Pod', plan.identity.name, plan.identity, generation=True)
        manifest = plan.manifest(self.namespace)
        # Extra labels can match another workload's network allow policy. Unknown
        # network/admission annotations are not silently trusted either.
        require(obj['metadata'].get('labels') == manifest['metadata']['labels']
                and not obj['metadata'].get('annotations') and not obj['metadata'].get('finalizers'))
        expected = manifest['spec']
        actual = deepcopy(obj.get('spec'))
        require(type(actual) is dict)
        # Accept only narrowly known API defaults/scheduling metadata. Everything
        # affecting volumes, env, command, security or extra containers is exact.
        actual.pop('nodeName', None)  # API-assigned scheduling result, never a URL.
        # Native Pod JSON omits these non-pointer bools when false. Do not
        # normalize pointer booleans or accept true for any host namespace.
        for key in ('hostNetwork', 'hostPID', 'hostIPC'):
            actual.setdefault(key, False)
        for key, default in [('schedulerName', 'default-scheduler'), ('priority', 0),
                             ('preemptionPolicy', 'PreemptLowerPriority')]:
            if key in actual:
                require(actual.pop(key) == default)
        if 'serviceAccount' in actual:
            require(actual.pop('serviceAccount') == plan.service_account)
        tolerations = actual.pop('tolerations', [])
        require(type(tolerations) is list and len(tolerations) <= 2)
        for value in tolerations:
            require(value in [{'key': 'node.kubernetes.io/'+key, 'operator': 'Exists',
                               'effect': 'NoExecute', 'tolerationSeconds': 300}
                              for key in ('not-ready', 'unreachable')])
        for container in actual.get('containers', []) + actual.get('initContainers', []):
            if container.get('terminationMessagePath') == '/dev/termination-log':
                container.pop('terminationMessagePath')
            if container.get('terminationMessagePolicy') == 'File':
                container.pop('terminationMessagePolicy')
            if container.get('resources') == {}:
                container.pop('resources')
        require(actual == expected)
        self._references(plan)
        return Pod(plan.identity, uid)

    def lookup(self, identity):
        try:
            require(identity in self.plans)
            plan = self.plans[identity]
            obj = self._request('GET', 'pods', identity.name, missing=True)
            return None if obj is None else self._pod(obj, plan)
        except (ValueError, TypeError, KeyError, AttributeError):
            raise Refused('Kubernetes lookup requires reconciliation') from None

    def create_once(self, identity):
        try:
            require(identity in self.plans)
            plan = self.plans[identity]
            self._references(plan)
            obj = self._request('POST', 'pods', body=plan.manifest(self.namespace))
            return self._pod(obj, plan)
        except (ValueError, TypeError, KeyError, AttributeError):
            raise Refused('Kubernetes creation requires reconciliation') from None
