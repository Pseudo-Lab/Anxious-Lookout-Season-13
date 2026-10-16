import base64
from copy import deepcopy
import json
import unittest
from uuid import uuid4

from app.user_runner_runtime import plan_from
from app.runner_kube import KubePods
from ops.user_runner_rebind import candidate


class RebindTests(unittest.TestCase):
    def setUp(self):
        self.old = 'local/old@sha256:' + 'a'*64
        self.new = 'local/new@sha256:' + 'b'*64
        self.runtime = {'version': 1, 'namespace': 'codex-runner', 'namespaceUid': str(uuid4()),
                        'transportTokenFile': '/run/private/token', 'plan': {
            'owner': str(uuid4()), 'state': str(uuid4()), 'generation': 1, 'image': self.old,
            'native': {'name': 'native', 'uid': str(uuid4()), 'volume': 'native'},
            'control': {'name': 'control', 'uid': str(uuid4()), 'volume': 'control'},
            'transport': {'name': 'transport', 'uid': str(uuid4())},
            'callback': 'http://api.m2-hosting.svc.cluster.local:8080/api/internal/research/tools', 'serviceAccount': 'runner-workload'}}
        self.pod = plan_from(self.runtime['plan']).manifest('codex-runner')
        self.pod['metadata'].update(uid=str(uuid4()), resourceVersion='123')
        self.pod['status'] = {'phase': 'Running', 'containerStatuses': [
            {'name': 'runner', 'containerID': 'containerd://old', 'state': {'running': {}}}]}
        self.secret = {'kind': 'Secret', 'metadata': {'namespace': 'm2-hosting',
            'name': 'hosting-user-runner-v1', 'uid': str(uuid4()), 'resourceVersion': '456'},
            'data': {'runtime.json': base64.b64encode(json.dumps(self.runtime).encode()).decode(),
                     'transport-token': base64.b64encode(b'x'*48).decode()}}

    def run_candidate(self):
        return candidate(self.runtime, self.pod, self.secret, self.new)

    def test_only_image_changes_and_patches_fence_snapshots(self):
        original = deepcopy((self.runtime, self.pod, self.secret))
        runtime, pod_patch, secret_patch = self.run_candidate()
        expected = deepcopy(self.runtime); expected['plan']['image'] = self.new
        self.assertEqual(runtime, expected)
        self.assertEqual((self.runtime, self.pod, self.secret), original)
        for obj, operations, section in ((self.pod, pod_patch, 'spec'), (self.secret, secret_patch, 'data')):
            self.assertEqual([op['op'] for op in operations], ['test', 'test', 'test', 'replace'])
            self.assertEqual(operations[0]['value'], obj['metadata']['uid'])
            self.assertEqual(operations[1]['value'], obj['metadata']['resourceVersion'])
            self.assertEqual(operations[2]['value'], obj[section])
        self.assertEqual(json.loads(base64.b64decode(secret_patch[-1]['value'])), runtime)
        self.assertEqual(secret_patch[-1]['path'], '/data/runtime.json')

    def test_reverse_candidate_uses_fresh_versions(self):
        runtime, pod_patch, secret_patch = self.run_candidate()
        pod = deepcopy(self.pod); pod['spec']['containers'][0]['image'] = self.new
        secret = deepcopy(self.secret); secret['data']['runtime.json'] = secret_patch[-1]['value']
        pod['metadata']['resourceVersion'] = '999'
        reverse, operations, _ = candidate(runtime, pod, secret, self.old)
        self.assertEqual(reverse, self.runtime)
        self.assertEqual(operations[1]['value'], '999')

    def test_secret_mismatch_refused(self):
        self.secret['data']['runtime.json'] = base64.b64encode(b'{}').decode()
        with self.assertRaises(ValueError): self.run_candidate()

    def test_wrong_or_terminating_pod_refused(self):
        for field, value in [('uid', 'not-a-uid'), ('deletionTimestamp', 'now'), ('resourceVersion', '')]:
            with self.subTest(field=field):
                pod = deepcopy(self.pod); pod['metadata'][field] = value
                with self.assertRaises(ValueError): candidate(self.runtime, pod, self.secret, self.new)
        self.pod['spec']['containers'][0]['image'] = self.new
        with self.assertRaises(ValueError): self.run_candidate()

    def test_mutable_image_or_no_change_refused(self):
        for image in ('local/new:latest', self.old):
            with self.assertRaises(ValueError): candidate(self.runtime, self.pod, self.secret, image)

    def test_foreign_binding_or_extra_container_refused(self):
        secret = deepcopy(self.secret); secret['metadata']['namespace'] = 'codex-trial'
        with self.assertRaises(ValueError): candidate(self.runtime, self.pod, secret, self.new)
        self.pod['spec']['containers'].append(deepcopy(self.pod['spec']['containers'][0]))
        with self.assertRaises(ValueError): self.run_candidate()

    def test_exited_container_refused(self):
        self.pod['status']['containerStatuses'][0]['state'] = {'terminated': {'exitCode': 0}}
        with self.assertRaises(ValueError): self.run_candidate()

    def test_live_validator_requires_matching_image(self):
        # No network: reference lookup is mocked, actual _pod spec checks run.
        pods = object.__new__(KubePods); pods.namespace = 'codex-runner'
        pods._references = lambda plan: None
        original = plan_from(self.runtime['plan'])
        runtime, _, _ = self.run_candidate()
        updated = plan_from(runtime['plan'])
        pods._pod(self.pod, original)
        with self.assertRaises(ValueError): pods._pod(self.pod, updated)
        self.pod['spec']['containers'][0]['image'] = self.new
        with self.assertRaises(ValueError): pods._pod(self.pod, original)
        self.assertEqual(str(pods._pod(self.pod, updated).uid), self.pod['metadata']['uid'])
