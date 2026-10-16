"""Preparation against disposable PostgreSQL and fake Kube, root Docker tmpfs only."""
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from app.runner_kube import KubePods
from app.user_runner_runtime import plan_from
from ops import user_runner_prepare as tool
from ops.user_runner_deployment import render
from runner.lifecycle import Refused
from config_tests.test_runner_registry import schema, admin, owner, read
from config_tests.test_runner_kube import response


@pytest.fixture
def prepared(admin, owner, tmp_path, monkeypatch, request):
    assert os.geteuid() == 0, 'Run with compose.user-runner-e2e-test.yml overlay'
    nsuid = str(uuid4())
    root = Path('/var/lib/anxious-lookout')/uuid4().hex
    root.mkdir(mode=0o700)
    inputs = tmp_path/'inputs'; inputs.mkdir(mode=0o700)
    token = inputs/'token'; token.write_text('k'*40); token.chmod(0o600)
    created = []
    def server(request):
        path = request.url.path
        if request.method == 'POST':
            value = json.loads(request.content)
            value['metadata']['uid'] = str(uuid4())
            created.append(value)
            return response(201, value)
        if path.endswith('/namespaces/isolated'):
            return response(200, {'metadata': {'uid': nsuid, 'labels': {'pod-security.kubernetes.io/enforce': 'restricted'}}})
        if 'networkpolicies' in path:
            return response(200, {'spec': {'podSelector': {}, 'policyTypes': ['Ingress','Egress']}})
        if 'serviceaccounts' in path:
            return response(200, {'automountServiceAccountToken': False})
        raise AssertionError(path)
    def port(*args, **kwargs):
        return KubePods(*args, **kwargs, _transport=httpx.MockTransport(server))
    monkeypatch.setattr(tool, 'KubePods', port)
    value = {'owner': str(owner), 'namespace': 'isolated', 'namespaceUid': nsuid, 'hostRoot': str(root),
        'node': 'isolated-node', 'podCIDR': '10.42.0.0/16', 'providerAccountId': 'synthetic-account',
        'image': 'registry.invalid/runner@sha256:'+'a'*64,
        'callback': 'http://api.m2-hosting.svc.cluster.local:8080/api/internal/research/tools',
        'api': 'https://kube.invalid', 'kubeTokenFile': str(token), 'caFile': None}
    output = tmp_path/'output'
    if getattr(request, 'param', False):
        value['executor'] = {'image': 'registry.invalid/executor@sha256:'+'c'*64,
                             'wallSeconds': 30, 'outputBytes': 65536}
    tool.prepare(value, output)
    return value, output, created


@pytest.mark.parametrize('prepared', [True], indirect=True)
def test_new_owner_v2_is_explicit_and_preserves_private_resource_split(prepared):
    value, output, objects = prepared
    config = json.loads((output/'runtime.json').read_text())
    assert config['plan']['version'] == 2 and config['plan']['executor'] == value['executor']
    plan = plan_from(config['plan'])
    assert plan.executor.wall_seconds == 30
    assert len(plan.manifest('isolated')['spec']['containers']) == 2
    assert len(objects) == 5


def test_one_command_binds_real_owner_and_new_storage_without_pod_or_policy(admin, owner, prepared):
    inputs, output, objects = prepared
    config = json.loads((output/'runtime.json').read_text())
    plan = plan_from(config['plan'])
    state = read(admin, owner)
    assert plan.identity.owner == owner and plan.identity.state_id == state.state_id
    assert [obj['kind'] for obj in objects] == ['PersistentVolume', 'PersistentVolumeClaim',
        'PersistentVolume', 'PersistentVolumeClaim', 'Secret']
    assert state.generation == 1 and state.pod_uid is None
    assert plan.native.name != plan.control.name
    assert config['transportTokenFile'] == '/run/user-runner/private/transport-token'
    for kind in ('native', 'control'):
        path = Path(inputs['hostRoot'])/state.state_id.hex/kind
        assert path.stat().st_uid == 10001 and path.stat().st_mode & 0o777 == 0o700
    assert (output/'transport-token').stat().st_uid == 10001
    assert (output/'runtime.json').stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        tool.prepare(inputs, output)
    assert len(objects) == 5


def test_deployment_preserves_trial_rules_and_adds_only_existing_cnp_names(prepared):
    _, output, _ = prepared
    config = json.loads((output/'runtime.json').read_text())
    trial = {'toEndpoints': [{'matchLabels': {'k8s:io.kubernetes.pod.namespace': 'codex-trial'}}]}
    existing = {'apiVersion': 'cilium.io/v2', 'kind': 'CiliumNetworkPolicy',
        'metadata': {'name': 'hosting-access-web-link-v1', 'namespace': 'm2-hosting', 'resourceVersion': '20'},
        'spec': {'endpointSelector': {'matchLabels': {'app': 'hosting-api'}},
                 'enableDefaultDeny': {'ingress': False, 'egress': False}, 'egress': [trial]}}
    resources, patch = render(config, 'registry.invalid/api@sha256:'+'b'*64, existing)
    policies = [v for v in resources['items'] if v['kind'] == 'CiliumNetworkPolicy']
    assert {v['metadata']['name'] for v in policies} == {'runner-callback-draft', 'hosting-access-web-link-v1'}
    link = policies[1]
    assert trial in link['spec']['egress'] and existing['spec']['egress'] == [trial]
    assert not any(v['kind'].startswith('Validating') for v in resources['items'])
    spec = patch['spec']['template']['spec']
    assert spec['automountServiceAccountToken'] is False
    assert spec['volumes'][2]['projected']['sources'][0]['serviceAccountToken']['expirationSeconds'] == 3600
    runner_egress = json.dumps(policies[0]['spec']['egress'])
    assert 'chatgpt.com' in runner_egress and 'kube-apiserver' not in runner_egress


def test_nonroot_init_copies_projected_secret_into_private_tmpfs():
    import subprocess
    import sys
    root = Path('/run/runner-input')
    root.chmod(0o755)
    for filename, value in [('runtime.json', '{"version":1}\n'), ('transport-token', 't'*40)]:
        target = root/filename
        target.write_text(value)
        os.chown(target, 0, 10001); target.chmod(0o440)
    def identity():
        os.setgroups([]); os.setgid(10001); os.setuid(10001)
    for _ in range(2):  # init container restarts verify identical content.
        result = subprocess.run([sys.executable, '-B', '-m', 'ops.user_runner_mount'],
                                preexec_fn=identity, capture_output=True)
        assert result.returncode == 0, result.stderr.decode()
    private = Path('/run/user-runner/private')
    assert private.stat().st_uid == 10001 and private.stat().st_mode & 0o777 == 0o700
    assert (private/'transport-token').read_text() == 't'*40
    assert (private/'transport-token').stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('native,control', [(5,1), (7,2)])
def test_controller_prepares_existing_http_intent_with_bounded_storage_hook(admin, owner, prepared, tmp_path, monkeypatch, native, control):
    from datetime import datetime, timezone
    from sqlalchemy import text
    from app.runner_registry import Registry
    inputs, _, objects = prepared
    monkeypatch.setenv('CODEX_RUNNER_NATIVE_SIZE_GIB', str(native))
    monkeypatch.setenv('CODEX_RUNNER_CONTROL_SIZE_GIB', str(control))
    other = uuid4()
    with admin.begin() as db:
        db.execute(text("INSERT INTO auth.accounts(id,github_id,login,role,is_approved) VALUES(:id,'54321','other','editor',true)"), {'id':other})
        state, _ = Registry(db).enter(other, uuid4(), datetime.now(timezone.utc))
    allocated = []
    def bounded(path):
        allocated.append(path)
        path.mkdir(mode=0o700)
        for kind in ('native','control'):
            (path/kind).mkdir(mode=0o700)
            (path/kind/'data').mkdir(mode=0o700)
    output = tmp_path/'controller-output'
    tool.prepare({**inputs,'owner':str(other)}, output, from_intent=True, storage_allocator=bounded)
    assert read(admin, other) == state
    config = json.loads((output/'runtime.json').read_text())
    assert config['plan']['state'] == str(state.state_id) and config['plan']['owner'] == str(other)
    assert len(allocated) == 1 and len(objects) == 10
    assert [obj['spec']['capacity']['storage'] for obj in objects[5:] if obj['kind']=='PersistentVolume'] == [f'{native}Gi', f'{control}Gi']
    assert [obj['spec']['resources']['requests']['storage'] for obj in objects[5:] if obj['kind']=='PersistentVolumeClaim'] == [f'{native}Gi', f'{control}Gi']
    assert all(obj['spec']['local']['path'].endswith('/data') for obj in objects[5:] if obj['kind']=='PersistentVolume')
    with pytest.raises(FileExistsError):
        tool.prepare({**inputs,'owner':str(other)}, output, from_intent=True, storage_allocator=bounded)
    assert len(objects) == 10 and read(admin, other) == state
