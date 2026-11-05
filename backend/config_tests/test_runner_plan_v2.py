"""#68: stored v2 binding plans stay readable; their `executor` is ignored (pure, no DB)."""
from copy import deepcopy
from uuid import uuid4

import pytest

from app.runner_bindings import decode
from runner.lifecycle import Refused

EXECUTOR = {'image': 'registry.invalid/executor@sha256:'+'c'*64, 'wallSeconds': 30, 'outputBytes': 65536}


@pytest.fixture
def v1():
    return {'owner': str(uuid4()), 'state': str(uuid4()), 'generation': 1,
            'image': 'registry.invalid/runner@sha256:'+'a'*64,
            'native': {'name': 'native', 'uid': str(uuid4()), 'volume': 'native-pv'},
            'control': {'name': 'control', 'uid': str(uuid4()), 'volume': 'control-pv'},
            'transport': {'name': 'transport', 'uid': str(uuid4())},
            'callback': 'http://api.m2-hosting.svc.cluster.local:8080/api/internal/research/tools',
            'serviceAccount': 'runner-workload'}


@pytest.fixture
def v2(v1):
    return {**deepcopy(v1), 'version': 2, 'executor': dict(EXECUTOR)}


def test_v2_executor_is_ignored_and_renders_the_same_pod_as_v1(v1, v2):
    old, new = decode(v2), decode(v1)
    assert old == new and not hasattr(old, 'executor')
    manifest = old.manifest('codex-runner')
    assert manifest == new.manifest('codex-runner')
    spec = manifest['spec']
    assert [c['name'] for c in spec['containers']] == ['runner']
    runner = spec['containers'][0]
    assert not {e['name'] for e in runner['env']} & {'SHELL_EXECUTOR_ENABLED', 'SHELL_WALL_SECONDS', 'SHELL_OUTPUT_BYTES'}
    assert all(m.get('subPath') != 'shell-ipc' and m['mountPath'] != '/run/shell-executor'
               for m in runner['volumeMounts'])
    assert EXECUTOR['image'] not in str(manifest)


@pytest.mark.parametrize('damage', ['version-1', 'version-3', 'version-bool', 'executor-none',
                                    'executor-missing-key', 'executor-extra-key', 'version-without-executor',
                                    'executor-without-version'])
def test_v2_shape_stays_strict(v2, damage):
    # Ignoring the executor does not loosen the stored plan contract.
    if damage == 'version-1': v2['version'] = 1
    if damage == 'version-3': v2['version'] = 3
    if damage == 'version-bool': v2['version'] = True
    if damage == 'executor-none': v2['executor'] = None
    if damage == 'executor-missing-key': del v2['executor']['outputBytes']
    if damage == 'executor-extra-key': v2['executor']['mounts'] = ['/control']
    if damage == 'version-without-executor': del v2['executor']
    if damage == 'executor-without-version': del v2['version']
    with pytest.raises(Refused):
        decode(v2)


def test_binding_image_tool_has_no_executor_opt_in_or_executor_image_swap():
    # #68: a plan never gains a shell executor, and a stored v2 executor is never rewritten.
    import inspect
    from ops.runner_binding_image import upgrade
    assert set(inspect.signature(upgrade).parameters) == {'db', 'fence', 'expected_plan_digest', 'new_image', 'apply'}
