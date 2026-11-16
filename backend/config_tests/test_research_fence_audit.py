"""Offline audit is read-only and never equates inaccessible state with zero."""
import json
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

from ops.research_fence_audit import audit
from runner.binding import initialize_binding
from runner.research_authority import Call
from runner.research_fence import BINDING, MutationFence
from runner.service_access import MODE
from runner.service_store import ServiceStore


@pytest.fixture
def inventory(tmp_path):
    owner, state = str(uuid4()), str(uuid4())
    native, control = tmp_path / 'native', tmp_path / 'control'
    initialize_binding(native, owner, state)
    control.mkdir(mode=0o700)
    with ServiceStore(control).transaction(create=True) as db:
        db.execute('INSERT INTO metadata VALUES(1,?)', (json.dumps({'owner': owner,
                   'stateId': state, 'version': 3, 'mode': MODE}),))
    inputs = dict(ownerId=owner, stateId=state, nativePvcUid=str(uuid4()),
                  controlPvcUid=str(uuid4()), writersQuiescent=True)
    fence = MutationFence(control, owner, state)
    work = SimpleNamespace(session=str(uuid4()), request=str(uuid4()), generation='generation',
        call=Call.of('thread', 'turn', 'item', 'research_document_save', {}))
    return SimpleNamespace(native=native, control=control, inputs=inputs, fence=fence, work=work)


def take_snapshot(inventory):
    return {str(path): (path.read_bytes(), path.stat().st_mtime_ns)
            for root in (inventory.native, inventory.control) for path in root.iterdir() if path.is_file()}


@pytest.mark.parametrize('phase', ['absent', 'unsettled', 'terminal'])
def test_read_only_audit_reports_bound_state_without_writes(inventory, monkeypatch, phase):
    setup = inventory
    if phase != 'absent':
        value = setup.fence.begin(setup.work, str(uuid4()), 'a' * 64)
        if phase == 'terminal':
            setup.fence.terminal({key: value[key] for key in BINDING}, 'applied')
    before = take_snapshot(setup)
    original_open = os.open
    def read_only(path, flags, *args, **kwargs):
        assert flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC) == 0
        return original_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(os, 'open', read_only)
    result = audit(setup.native, setup.control, setup.inputs)
    assert result['status'] == phase and result['bindingValidated'] is True
    assert take_snapshot(setup) == before
    output = json.dumps(result)
    assert 'fingerprint' not in output and 'generation' not in output and 'sessionId' not in output


@pytest.mark.parametrize('failure', ['wrong_owner', 'wrong_state', 'no_control_ledger', 'corrupt', 'journal', 'quiescence_unknown'])
def test_unproved_or_unreadable_inventory_is_unknown_never_zero(inventory, failure):
    setup = inventory
    if failure == 'wrong_owner': setup.inputs['ownerId'] = str(uuid4())
    if failure == 'wrong_state': setup.inputs['stateId'] = str(uuid4())
    if failure == 'no_control_ledger': (setup.control / 'service-access.sqlite3').unlink()
    if failure == 'corrupt':
        setup.fence.begin(setup.work, str(uuid4()), 'a' * 64)
        setup.fence.path.write_text('{broken')
    if failure == 'journal': (setup.control / 'service-access.sqlite3-journal').write_bytes(b'')
    if failure == 'quiescence_unknown': setup.inputs['writersQuiescent'] = False
    result = audit(setup.native, setup.control, setup.inputs)
    assert result['status'] == 'unknown' and result['bindingValidated'] is False


@pytest.mark.parametrize('failure', ['symlink', 'hardlink', 'inaccessible', 'wal', 'shm'])
def test_hostile_fence_or_unsettled_sqlite_sidecars_are_unknown(inventory, failure):
    setup = inventory
    setup.fence.begin(setup.work, str(uuid4()), 'a' * 64)
    path = setup.fence.path
    other = setup.control / 'other-file'
    if failure == 'symlink':
        path.rename(other)
        path.symlink_to(other)
    if failure == 'hardlink': os.link(path, other)
    if failure == 'inaccessible': path.chmod(0)
    if failure in {'wal', 'shm'}:
        (setup.control / ('service-access.sqlite3-' + failure)).write_bytes(b'pending')
    result = audit(setup.native, setup.control, setup.inputs)
    assert result['status'] == 'unknown' and result['bindingValidated'] is False
