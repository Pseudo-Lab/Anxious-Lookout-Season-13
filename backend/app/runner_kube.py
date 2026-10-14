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
class Plan:
    identity: Identity
    image: str
    native: Reference
    control: Reference
    transport: Reference
    callback: str
    service_account: str

    def __post_init__(self):
        require(isinstance(self.identity, Identity))
        require(isinstance(self.image, str) and re.fullmatch(r'[a-zA-Z0-9./:_-]+@sha256:[a-f0-9]{64}', self.image))
        require(all(isinstance(ref, Reference) for ref in (self.native, self.control, self.transport)))
        require(self.native.volume is not None and self.control.volume is not None and self.transport.volume is None)
        require(self.native.uid != self.control.uid and self.native.name != self.control.name
                and self.native.volume != self.control.volume)
        parsed = urlsplit(self.callback)
        require(parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password
                and not parsed.query and not parsed.fragment)
        name(self.service_account)

    def manifest(self, namespace):
        identity = self.identity
        labels = {LABEL+'owner': str(identity.owner), LABEL+'state': str(identity.state_id),
                  LABEL+'generation': str(identity.generation), LABEL+'managed': 'user-runner'}
        env = {'APP_ENV': 'operator-service', 'RUNNER_ACCOUNT_ID': str(identity.owner),
               'RUNNER_STATE_ID': str(identity.state_id), 'RUNNER_STATE_DIR': '/state',
               'RUNNER_TOKEN_FILE': '/transport/token', 'CODEX_MODEL': 'gpt-6.1-sol',
               'CODEX_AUTH_MODE': 'access-service', 'CODEX_EXECUTION_SCOPE': 'operator-access-service',
               'CODEX_ACCESS_CONTROL_DIR': '/control', 'CODEX_OPERATOR_ACCOUNT_FILE': '/control/operator-account',
               'RESEARCH_TOOL_CALLBACK_URL': self.callback}
        return {'apiVersion': 'v1', 'kind': 'Pod',
                'metadata': {'name': identity.name, 'namespace': namespace, 'labels': labels},
                'spec': {'serviceAccountName': self.service_account, 'automountServiceAccountToken': False,
                         'restartPolicy': 'Never', 'terminationGracePeriodSeconds': 55,
                         'hostNetwork': False, 'hostPID': False, 'hostIPC': False,
                         'enableServiceLinks': False, 'dnsPolicy': 'ClusterFirst',
                         'securityContext': {'runAsUser': 10001, 'runAsGroup': 10001, 'runAsNonRoot': True,
                                             'seccompProfile': {'type': 'RuntimeDefault'}},
                         'containers': [{'name': 'runner', 'image': self.image, 'imagePullPolicy': 'IfNotPresent',
                            'command': ['uvicorn', 'runner.app:create_app', '--factory', '--host', '0.0.0.0', '--port', '8080',
                                        '--workers', '1', '--no-access-log', '--no-proxy-headers'],
                            'env': [{'name': key, 'value': value} for key, value in env.items()] +
                                [{'name': 'RUNNER_POD_UID', 'valueFrom': {'fieldRef': {'apiVersion': 'v1', 'fieldPath': 'metadata.uid'}}}],
                            'ports': [{'name': 'runner', 'containerPort': 8080, 'protocol': 'TCP'}],
                            'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                                                'capabilities': {'drop': ['ALL']}},
                            'volumeMounts': [{'name': 'native', 'mountPath': '/state'}, {'name': 'control', 'mountPath': '/control'},
                                             {'name': 'transport', 'mountPath': '/transport', 'readOnly': True},
                                             {'name': 'tmp', 'mountPath': '/tmp'}]}],
                         'volumes': [{'name': 'native', 'persistentVolumeClaim': {'claimName': self.native.name}},
                                     {'name': 'control', 'persistentVolumeClaim': {'claimName': self.control.name}},
                                     {'name': 'transport', 'secret': {'secretName': self.transport.name, 'defaultMode': 292,
                                                                    'items': [{'key': 'token', 'path': 'token'}]}},
                                     {'name': 'tmp', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '64Mi'}}]}}


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
        self.client = httpx.Client(base_url=endpoint.rstrip('/'), headers={'Authorization': 'Bearer '+bearer, 'Accept-Encoding': 'identity'},
            verify=ssl.create_default_context(cafile=ca_file), timeout=httpx.Timeout(timeout),
            follow_redirects=False, trust_env=False,
            transport=_transport if _transport is not None else budget.Transport(ssl.create_default_context(cafile=ca_file)))

    def close(self):
        self.client.close()

    def _request(self, method, resource, resource_name=None, *, body=None, missing=False):
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
                require(response.status_code == (201 if method == 'POST' else 200))
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
        for container in actual.get('containers', []):
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
