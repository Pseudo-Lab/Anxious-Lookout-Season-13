import os
from pathlib import Path

import pytest
from ops.runner_rehearsal_init import initialize, verify
from runner.lifecycle import Refused
from app.codex_policy import CodexFailure


@pytest.fixture
def roots(tmp_path):
    assert os.geteuid() == os.getegid() == 10001
    native, control = tmp_path/'state', tmp_path/'control'
    native.mkdir(mode=0o700); control.mkdir(mode=0o700)
    return native, control


def test_initialize_verify_and_refuse_replay(roots):
    initialize(*roots)
    verify(*roots)
    before = {str(p): p.read_bytes() for root in roots for p in root.iterdir()}
    with pytest.raises((Refused, CodexFailure)): initialize(*roots)
    assert before == {str(p): p.read_bytes() for root in roots for p in root.iterdir()}
    assert all(p.stat().st_mode & 0o777 == 0o600 for root in roots for p in root.iterdir())


@pytest.mark.parametrize('damage', ['control-populated', 'native-populated', 'mode', 'symlink'])
def test_preflight_refuses_without_partial_initialization(roots, damage):
    native, control = roots
    if damage.endswith('populated'):
        (control if damage.startswith('control') else native).joinpath('marker').write_text('preserve')
    elif damage == 'mode': control.chmod(0o755)
    else:
        control.rmdir(); control.symlink_to(native)
    with pytest.raises((Refused, CodexFailure)): initialize(*roots)
    assert not (native/'account-id').exists()


def test_foreign_binding_refused(roots):
    initialize(*roots)
    (roots[0]/'account-id').write_text('00000000-0000-0000-0000-000000000000')
    with pytest.raises((Refused, CodexFailure)): verify(*roots)
