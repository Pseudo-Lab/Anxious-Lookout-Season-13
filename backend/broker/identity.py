"""Runner Pod identity from a projected ServiceAccount token (#60).

TokenReview proves the bound Pod name/UID; a fresh Pod GET proves that Pod is a
running, controller-labelled user runner. Only that owner/state may receive access.
"""
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import httpx

from runner.lifecycle import require

SA_DIR = Path('/var/run/secrets/kubernetes.io/serviceaccount')
LABEL = 'anxious.io/'
POD_NAME = 'authentication.kubernetes.io/pod-name'
POD_UID = 'authentication.kubernetes.io/pod-uid'


@dataclass(frozen=True)
class RunnerIdentity:
    owner: UUID
    state_id: UUID
    generation: int
    pod_name: str
    pod_uid: UUID


def canonical(value):
    require(isinstance(value, str))
    parsed = UUID(value)
    require(str(parsed) == value)
    return parsed


class Kube:
    """Minimal in-cluster API client; the broker's own token is re-read per call (rotation)."""
    def __init__(self, base='https://kubernetes.default.svc', token_file=SA_DIR/'token', ca_file=SA_DIR/'ca.crt',
                 transport=None):
        self.token_file = Path(token_file)
        self.client = httpx.Client(base_url=base, timeout=5, trust_env=False, follow_redirects=False,
                                   verify=str(ca_file) if transport is None else True, transport=transport)

    def request(self, method, path, body=None):
        token = self.token_file.read_text().strip()
        response = self.client.request(method, path, json=body, headers={'Authorization': 'Bearer '+token})
        require(response.status_code in (200, 201) and len(response.content) <= 1_000_000)
        return response.json()

    def close(self):
        self.client.close()


class Verifier:
    def __init__(self, kube, *, namespace='codex-runner', audience='codex-broker', service_account='runner-workload'):
        self.kube, self.namespace, self.audience = kube, namespace, audience
        self.username = 'system:serviceaccount:'+namespace+':'+service_account
        self.service_account = service_account

    def verify(self, token):
        """Return the runner identity or raise Refused. Never echoes the presented token."""
        require(isinstance(token, str) and 20 < len(token) <= 16384 and token.isascii() and ' ' not in token)
        review = self.kube.request('POST', '/apis/authentication.k8s.io/v1/tokenreviews', {
            'apiVersion': 'authentication.k8s.io/v1', 'kind': 'TokenReview',
            'spec': {'token': token, 'audiences': [self.audience]}})
        status = review.get('status') or {}
        user = status.get('user') or {}
        extra = user.get('extra') or {}
        require(status.get('authenticated') is True and self.audience in (status.get('audiences') or [])
                and user.get('username') == self.username)
        names, uids = extra.get(POD_NAME), extra.get(POD_UID)
        require(type(names) is list and type(uids) is list and len(names) == len(uids) == 1)
        name, uid = names[0], canonical(uids[0])
        require(isinstance(name, str) and 0 < len(name) <= 253)
        pod = self.kube.request('GET', '/api/v1/namespaces/'+self.namespace+'/pods/'+name)
        metadata, spec, state = pod.get('metadata') or {}, pod.get('spec') or {}, pod.get('status') or {}
        labels = metadata.get('labels') or {}
        require(pod.get('kind') == 'Pod' and metadata.get('name') == name and metadata.get('namespace') == self.namespace
                and metadata.get('uid') == str(uid) and not metadata.get('deletionTimestamp')
                and spec.get('serviceAccountName') == self.service_account
                and labels.get(LABEL+'managed') == 'user-runner' and state.get('phase') == 'Running')
        generation = labels.get(LABEL+'generation')
        require(isinstance(generation, str) and generation.isdecimal() and str(int(generation)) == generation
                and int(generation) > 0)
        owner, state_id = canonical(labels.get(LABEL+'owner')), canonical(labels.get(LABEL+'state'))
        # The controller names each Pod after its owner; a relabelled Pod cannot claim another owner.
        require(name == 'runner-'+owner.hex)
        return RunnerIdentity(owner, state_id, int(generation), name, uid)
