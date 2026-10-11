import copy
import uuid

import pytest

import access_web_prepare as web
from test_owner_prepare import snapshot as old_snapshot
from test_prepare import apply


@pytest.fixture
def package():
    original = {name: getattr(web.base, name) for name in ('OLD_API', 'API', 'RUNNER', 'NAME', 'LABEL', 'API_INPUT', 'TRANSPORT', 'CONFIG')}
    snapshot = old_snapshot.__wrapped__()
    snapshot['api']['spec']['template']['spec']['containers'][0]['image'] = web.BASELINE_API
    images = {'sourceSha': 'a'*40, 'api': 'docker.io/library/anxious-s13-access-api-aaaaaaa@sha256:'+'a'*64,
              'runner': 'docker.io/library/anxious-s13-access-runner-aaaaaaa@sha256:'+'b'*64}
    web.configure(images)
    inputs = {'ownerId': str(uuid.uuid4()), 'stateId': str(uuid.uuid4())}
    yield snapshot, inputs, images
    for name, value in original.items():
        setattr(web.base, name, value)


def test_access_runner_expiry_keeps_service_ready_and_exact_nonrefresh_egress(package):
    snapshot, inputs, _ = package
    runner = web.emit(snapshot, inputs, 'runner')
    container = runner['spec']['template']['spec']['containers'][0]
    env = {v['name']: v.get('value') for v in container['env']}
    assert env['CODEX_AUTH_MODE'] == 'access-only'
    assert env['CODEX_ACCESS_CONTROL_DIR'] == '/control/access-web'
    assert env['RUNNER_STATE_DIR'] == '/native/access-web'
    assert 'available' not in container['readinessProbe']['exec']['command'][2]
    assert 'CODEX_PERSONAL_CONTROL_DIR' not in env
    policy = web.emit(snapshot, inputs, 'policy')['items'][0]
    assert policy['spec']['egress'][-1]['toFQDNs'] == [{'matchName': 'chatgpt.com'}]
    assert 'auth.openai.com' not in str(policy) and 'api.openai.com' not in str(policy)


def test_access_init_fresh_subdirectories_and_same_retained_claims(package):
    snapshot, inputs, _ = package
    job = web.emit(snapshot, inputs, 'init')
    pod = job['spec']['template']['spec']
    command = pod['containers'][0]['command'][2]
    assert '/native/access-web' in command and '/control/access-web' in command and 'assert not p.exists()' in command
    assert {v['persistentVolumeClaim']['claimName'] for v in pod['volumes'] if 'persistentVolumeClaim' in v} == {'trial-native', 'trial-control'}
    assert pod['automountServiceAccountToken'] is False


def test_access_api_upgrade_enable_disable_rollback_preserves_https_and_identity(package):
    snapshot, inputs, images = package
    current = copy.deepcopy(snapshot['api'])
    upgraded = apply(current, web.base.patch(snapshot, inputs, current, 'upgrade'))
    assert upgraded['spec']['template']['spec']['containers'][0]['image'] == images['api']
    enabled = apply(upgraded, web.base.patch(snapshot, inputs, upgraded, 'enable'))
    disabled = apply(enabled, web.base.patch(snapshot, inputs, enabled, 'disable'))
    assert disabled['spec'] == upgraded['spec']
    rollback = apply(disabled, web.base.patch(snapshot, inputs, disabled, 'rollback-upgrade'))
    assert rollback['spec'] == snapshot['api']['spec']
    drift = copy.deepcopy(upgraded)
    drift['metadata']['uid'] = 'wrong'
    with pytest.raises(ValueError):
        web.base.patch(snapshot, inputs, drift, 'enable')


def test_runner_lifecycle_requires_recorded_defaulted_access_spec_and_uid(package):
    snapshot, inputs, _ = package
    baseline = web.emit(snapshot, inputs, 'runner')
    baseline['metadata'].update(uid='selected-runner', resourceVersion='selected-rv')
    snapshot['runner'] = baseline
    snapshot['runnerDryRun'] = copy.deepcopy(baseline)
    stopped = apply(baseline, web.patch(snapshot, inputs, baseline, 'runner-stop'))
    assert stopped['spec']['replicas'] == 0
    resumed = apply(stopped, web.patch(snapshot, inputs, stopped, 'runner-start'))
    assert resumed['spec'] == baseline['spec']
    wrong = copy.deepcopy(stopped)
    wrong['metadata']['uid'] = 'other-runner'
    with pytest.raises(ValueError):
        web.patch(snapshot, inputs, wrong, 'runner-start')
