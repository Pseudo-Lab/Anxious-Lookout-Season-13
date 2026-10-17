"""Offline rollout contracts; never contact Docker daemon, cluster or host source."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import UUID

import pytest
from ops import runner_rollout as rollout

TEMPLATES = Path('/rollout-templates')


@pytest.fixture
def values():
    return json.loads((TEMPLATES/'values.example.json').read_text())


@pytest.fixture
def baseline():
    return {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': {'name': 'api', 'namespace': 'm2-hosting',
        'uid': '00000000-0000-4000-8000-000000000001', 'resourceVersion': '123'},
        'spec': {'replicas': 0, 'template': {'metadata': {'labels': {'app': 'hosting-api'}}, 'spec': {
        'serviceAccountName': 'hosting', 'containers': [{'name': 'api', 'image': 'old',
        'envFrom': [{'configMapRef': {'name': 'config'}}],
        'env': [{'name': 'DATABASE_URL_FILE', 'value': '/run/secrets/database-url'},
                {'name': 'CODEX_PERSONAL_ENABLE', 'value': 'true'},
                {'name': 'CODEX_USER_RUNNER_ACCOUNT_ID', 'value': 'old'},
                {'name': 'CODEX_USER_RUNNER_CONFIG_FILE', 'value': 'old'},
                {'name': 'AUTH_ORIGIN', 'value': 'https://override.invalid'}],
        'volumeMounts': [{'name': 'owner-input', 'mountPath': '/run/owner-input'},
                         {'name': 'credentials', 'mountPath': '/run/secrets'}]}],
        'initContainers': [{'name': 'runner-private-input', 'image': 'old'}],
        'volumes': [{'name': 'owner-input', 'secret': {'secretName': 'old'}},
                    {'name': 'credentials', 'secret': {'secretName': 'existing'}}]}}}}


@pytest.fixture
def maps():
    return [{'kind': 'ConfigMap', 'metadata': {'name': 'config', 'namespace': 'm2-hosting'},
             'data': {'CODEX_PERSONAL_ROOT_ENABLE': 'true', 'CODEX_RUNNERS_FILE': 'old',
                      'APP_ENV': 'personal-test', 'AUTH_ORIGIN': 'https://inherited.invalid'}}]


def test_patch_removes_both_settings_sources_and_preserves_auth(values, baseline, maps):
    before = deepcopy(baseline)
    candidate, patch = rollout.deployment(values, baseline, maps)
    assert baseline == before
    assert candidate['spec']['replicas'] == 0
    spec = candidate['spec']['template']['spec']
    api = spec['containers'][0]
    env = {e['name']: e for e in api['env']}
    assert not any(k.startswith('CODEX_PERSONAL_') for k in env)
    assert not {'CODEX_RUNNERS_FILE', 'CODEX_USER_RUNNER_CONFIG_FILE', 'CODEX_USER_RUNNER_ACCOUNT_ID'} & env.keys()
    assert 'envFrom' not in api
    assert env['APP_ENV']['valueFrom'] == {'configMapKeyRef': {'name': 'config', 'key': 'APP_ENV'}}
    assert env['AUTH_ORIGIN']['value'] == 'https://override.invalid'
    assert env['DATABASE_URL_FILE']['value'] == '/run/secrets/database-url'
    assert env['CODEX_USER_RUNNER_ALLOWLIST']['value'] == values['allowlist'][0]
    assert api['imagePullPolicy'] == 'Never'
    assert 'owner-input' not in {v['name'] for v in spec['volumes']}
    assert spec['volumes'][0]['secret']['secretName'] == 'existing'
    assert [c['name'] for c in spec['initContainers']] == ['runner-pool-input']
    assert patch[:2] == [{'op': 'test', 'path': '/metadata/uid', 'value': before['metadata']['uid']},
                         {'op': 'test', 'path': '/metadata/resourceVersion', 'value': '123'}]
    assert patch[2]['value'] == spec


def test_all_approved_expansion_removes_old_allowlist(values, baseline, maps):
    values['allowlist'] = None
    baseline['spec']['template']['spec']['containers'][0]['env'].append({'name': 'CODEX_USER_RUNNER_ALLOWLIST', 'value': 'old'})
    api = rollout.deployment(values, baseline, maps)[0]['spec']['template']['spec']['containers'][0]
    assert 'CODEX_USER_RUNNER_ALLOWLIST' not in {e['name'] for e in api['env']}
    assert not any('ALLOWLIST' in a for a in rollout.docker(values, 'controller'))


@pytest.mark.parametrize('change', ['missing-map', 'secret-envfrom', 'wrong-namespace', 'sidecar'])
def test_unknown_baseline_refuses(values, baseline, maps, change):
    spec = baseline['spec']['template']['spec']
    if change == 'missing-map': maps = []
    if change == 'secret-envfrom': spec['containers'][0]['envFrom'] = [{'secretRef': {'name': 'private'}}]
    if change == 'wrong-namespace': baseline['metadata']['namespace'] = 'other'
    if change == 'sidecar': spec['containers'].append({'name': 'unknown'})
    with pytest.raises(ValueError): rollout.deployment(values, baseline, maps)


@pytest.mark.parametrize('key,value', [('maxPods', True), ('nativeSizeGiB', 0), ('allowlist', []),
    ('runtimeImage', 'runtime:latest'), ('provisionerImageId', 'worker:latest'), ('sourceUid', -1),
    ('hostPidNamespace', 'pid:[0]'), ('hostApi', 'http://127.0.0.1:6443'),
    ('kubectl', '/bin/tool;echo leak'), ('crictl', '/bin/../root/tool'), ('namespaceUid', 'not-a-uuid')])
def test_values_fail_closed(values, key, value):
    values[key] = value
    with pytest.raises((ValueError, TypeError)): rollout.checked(values)


def test_no_unrecognized_secret_field(values):
    values['databasePassword'] = 'never-accepted'
    with pytest.raises(ValueError): rollout.checked(values)


def test_role_matrix_and_retired_bindings(values):
    objects = rollout.resources(values)['items']
    lookup = {(o['kind'], o['metadata']['name']): o for o in objects}
    assert lookup['Role', 'runner-pool-api']['rules'] == [rollout.rule(['pods', 'persistentvolumeclaims', 'secrets'], ['get'])]
    assert lookup['Role', 'runner-web']['rules'] == []
    assert lookup['RoleBinding', 'runner-web']['subjects'] == []
    assert lookup['ClusterRoleBinding', 'codex-runner-preparer']['subjects'] == []
    for role in ('controller', 'observer', 'supply'):
        assert lookup['ServiceAccount', 'runner-pool-'+role]['automountServiceAccountToken'] is False
    assert rollout.rule(['pods/exec'], ['create']) in lookup['Role', 'runner-pool-supply']['rules']
    assert rollout.rule(['pods'], ['delete']) in lookup['Role', 'runner-pool-observer']['rules']
    assert rollout.rule(['nodes'], ['get'], names=[values['node']]) in lookup['ClusterRole', 'runner-pool-observer']['rules']
    assert lookup['ResourceQuota', 'runner-bootstrap-no-pods']['spec']['hard'] == {'count/pods': '3'}
    secret = lookup['Secret', 'hosting-runner-pool-v2']
    assert set(json.loads(secret['stringData']['pool.json'])) == {'version', 'api', 'namespace', 'namespaceUid', 'podCIDR', 'caFile', 'kubeTokenFile'}


def test_workers_mounts_credentials_and_restore_order(values, baseline, maps):
    files = rollout.render(values, baseline, TEMPLATES, maps)
    for role in ('controller', 'observer', 'supply', 'restore'):
        args = rollout.docker(values, role)
        assert values['provisionerImageId'] in args and '--pull=never' in args
        assert not any('docker.sock' in a for a in args)
        assert '--cidfile=/run/issue16-'+role+'/container-id' in args
        subprocess.run(['sh', '-n'], input=files[role+'.sh'].encode(), check=True)
    controller = files['controller.sh']
    assert 'bind-propagation=rshared' in controller and '--cap-add=SYS_ADMIN' in controller
    assert 'CONTROLLER_DATABASE_URL_FILE=/private/database-url' in controller
    assert '/home/opc/.codex' not in controller
    supply = files['supply.sh']
    assert 'src=/home/opc/.codex,dst=/home/opc/.codex,readonly' in supply
    assert '--pid=host' not in supply and 'containerd.sock' not in supply and '--cap-add=SYS_ADMIN' not in supply
    observer = files['observer.sh']
    assert '--idle' in observer and '--pid=host' in observer and '--cgroupns=host' in observer
    assert 'readonly,bind-propagation=rslave' in observer
    restore = files['issue16-restore.service']
    assert 'Before=k3s.service' in restore and 'After=docker.service' in restore
    assert 'k3s.service' not in restore.split('After=')[1].split('\n')[0]
    assert files['k3s-issue16.conf'] == '[Unit]\nRequires=issue16-restore.service\nAfter=issue16-restore.service\n'
    assert 'issue16-restore.service' in files['issue16-controller.service']
    assert '--network=none' in files['restore.sh'] and '/private' not in files['restore.sh']


def load_worker():
    spec = importlib.util.spec_from_file_location('rollout_worker', TEMPLATES/'worker.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('result,exit_code', [('processed', 0), ('reconciliation_required', 1), ('auth_expired', 1)])
def test_wrapper_makes_per_owner_failure_visible(monkeypatch, result, exit_code):
    worker = load_worker()
    monkeypatch.setattr(sys, 'argv', [])
    monkeypatch.setattr(worker.runpy, 'run_module', lambda *a, **kw: print(json.dumps({
        'owner': '00000000-0000-4000-8000-000000000001', 'stage': 'provision', 'result': result})))
    assert worker.run_batch('controller', []) == exit_code


def test_restore_empty_then_orphan_refuses(tmp_path):
    if os.geteuid() != 0: pytest.skip('root ownership contract')
    worker = load_worker()
    root, storage = tmp_path/'outputs', tmp_path/'storage'
    root.mkdir(mode=0o700); storage.mkdir(mode=0o700)
    assert worker.restore_inventory(root, storage) == []
    (storage/('a'*32)).mkdir()
    with pytest.raises(ValueError): worker.restore_inventory(root, storage)


def test_restore_inventory_identity_and_partial_refuse(tmp_path):
    if os.geteuid() != 0: pytest.skip('root ownership contract')
    worker = load_worker()
    root, storage = tmp_path/'outputs', tmp_path/'storage'
    root.mkdir(mode=0o700); storage.mkdir(mode=0o700)
    owner, state = UUID(int=1), UUID(int=2)
    output = root/owner.hex; output.mkdir(mode=0o700)
    os.chown(output, 10001, 10001)
    target = storage/state.hex; target.mkdir(mode=0o700)
    manifest = output/'storage-restore.json'
    value = {'version': 1, 'owner': str(owner), 'state': str(state), 'path': str(target), 'layout': 'bounded-ext4'}
    manifest.write_text(json.dumps(value)); manifest.chmod(0o600)
    with pytest.raises(FileNotFoundError): worker.restore_inventory(root, storage)
    runtime = output/'runtime.json'
    runtime.write_text(json.dumps({'plan': {'owner': str(owner), 'state': str(state)}}))
    runtime.chmod(0o600); os.chown(runtime, 10001, 10001)
    assert worker.restore_inventory(root, storage) == [target]
    value['path'] = '/outside-root'
    manifest.write_text(json.dumps(value))
    with pytest.raises(ValueError): worker.restore_inventory(root, storage)


def test_schema_keys_and_regex_match_values(values):
    import re
    schema = json.loads((TEMPLATES/'values.schema.json').read_text())
    assert set(schema['required']) == rollout.KEYS
    assert set(schema['properties']) == set(values) == rollout.KEYS | {'executor'}
    assert 'executor' not in schema['required']
    for key in ('hostPidNamespace', 'hostCgroupNamespace'):
        assert re.fullmatch(schema['properties'][key]['pattern'], values[key])


def test_cli_check_no_output_and_exclusive_render(values, baseline, maps, tmp_path):
    for name, value in [('values', values), ('deployment', baseline), ('configmaps', maps[0])]:
        (tmp_path/(name+'.json')).write_text(json.dumps(value))
    args = [sys.executable, '-B', '-m', 'ops.runner_rollout', '--values', str(tmp_path/'values.json'),
            '--deployment', str(tmp_path/'deployment.json'), '--configmaps', str(tmp_path/'configmaps.json'),
            '--templates', str(TEMPLATES), '--check']
    assert subprocess.run(args, capture_output=True).returncode == 0
    assert len(list(tmp_path.iterdir())) == 3
    args += ['--output', str(tmp_path/'result')]
    assert subprocess.run(args, capture_output=True).returncode == 0
    assert (tmp_path/'result/api-patch.json').stat().st_mode & 0o777 == 0o600
    assert subprocess.run(args, capture_output=True).returncode == 1
    (tmp_path/'values.json').write_text('{"version":1,"version":1}')
    result = subprocess.run(args[:-2], capture_output=True)
    assert result.returncode == 1 and b'Rollout render refused' in result.stderr


def test_executor_opt_in_flows_only_to_new_owner_preparation(values):
    executor = values.pop('executor')
    assert 'executor' not in rollout.inputs(rollout.checked(values))['controller']
    values['executor'] = executor
    assert rollout.inputs(rollout.checked(values))['controller']['executor'] == executor
    values['executor']['image'] = 'mutable:latest'
    with pytest.raises(ValueError): rollout.checked(values)


def test_executor_schema_limits_agree_with_renderer(values):
    import re
    schema = json.loads((TEMPLATES/'values.schema.json').read_text())
    executor = schema['properties']['executor']
    assert executor['type'] == 'object' and executor['additionalProperties'] is False
    assert set(executor['required']) == set(executor['properties']) == set(values['executor'])
    image = executor['properties']['image']
    assert image == {'$ref': '#/$defs/image'}
    pattern = schema['$defs']['image']['pattern']
    assert re.fullmatch(pattern, values['executor']['image'])
    for key in ('wallSeconds', 'outputBytes'):
        bounds = executor['properties'][key]
        assert bounds['type'] == 'integer'
        for limit in (bounds['minimum'], bounds['maximum']):
            candidate = deepcopy(values)
            candidate['executor'][key] = limit
            assert rollout.checked(candidate) == candidate
        for invalid in (bounds['minimum'] - 1, bounds['maximum'] + 1, True, '30'):
            candidate = deepcopy(values)
            candidate['executor'][key] = invalid
            with pytest.raises(ValueError): rollout.checked(candidate)
    for key in executor['required']:
        candidate = deepcopy(values)
        del candidate['executor'][key]
        with pytest.raises(ValueError): rollout.checked(candidate)
    for invalid in (None, {**values['executor'], 'token': 'forbidden'},
                    {**values['executor'], 'image': 'mutable:latest'}):
        candidate = {**values, 'executor': invalid}
        with pytest.raises(ValueError): rollout.checked(candidate)


def test_post_images_pvc_mount_and_repeat_render(values, baseline, maps):
    candidate, _ = rollout.deployment(values, baseline, maps)
    spec = candidate['spec']['template']['spec']
    assert {'name': 'post-images', 'persistentVolumeClaim': {'claimName': 'hosting-post-images'}} in spec['volumes']
    assert {'name': 'post-images', 'mountPath': '/var/lib/posts/images'} in spec['containers'][0]['volumeMounts']
    assert {'name': 'POST_IMAGE_ROOT', 'value': '/var/lib/posts/images/data'} in spec['containers'][0]['env']
    repeated, _ = rollout.deployment(values, candidate, maps)
    assert repeated['spec']['template']['spec'] == spec
    rendered = rollout.render(values, baseline, TEMPLATES, maps)
    pvc = json.loads(rendered['post-images-pvc.json'])
    assert pvc['spec']['accessModes'] == ['ReadWriteOnce']
    assert pvc['metadata']['name'] == 'hosting-post-images'
