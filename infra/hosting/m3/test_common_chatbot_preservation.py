"""Synthetic state/archives only; never host auth, provider or live state."""
import io
import json
import socket
import tarfile

import pytest

from common_chatbot_archive import stream, verify
from common_chatbot_probe import runner_check


@pytest.fixture
def state(tmp_path):
    native, control = tmp_path / "native", tmp_path / "control"
    native.mkdir(); control.mkdir()
    (native / "account-id").write_text("synthetic-owner")
    (native / "state-binding.json").write_text(json.dumps({"version": 1, "ownerId": "synthetic-owner", "stateId": "synthetic-state"}))
    (native / "broker.json").write_text(json.dumps({"session": {"state": "idle", "request": "already-reserved", "record": {"turns": [{"content": "Private native history"}]}}}))
    (control / "access-ledger.json").write_text(json.dumps({"owner": "synthetic-owner", "stateId": "synthetic-state", "requests": ["already-reserved"], "windows": ["prior-window"]}))
    (control / "projection.json").write_text('{"key":"synthetic-private-integrity-key"}')
    return native, control


def test_probe_and_archive_preserve_full_history_ledger_and_integrity_without_restore(state, tmp_path):
    native, control = state
    before = {str(path): path.read_bytes() for root in state for path in root.iterdir()}
    result = runner_check(native, control, "synthetic-owner", "synthetic-state")
    assert result["nativeRunning"] == result["ambiguous"] == 0 and result["lifetimeReservations"] == 1
    assert "Private native history" not in json.dumps(result) and "prior-window" not in json.dumps(result)
    archive = tmp_path / "backup.tar"
    with archive.open("wb") as output: stream(native, control, output)
    assert verify(archive)["nativeControlArchiveVerified"] is True and verify(archive)["restored"] is False
    with tarfile.open(archive) as saved:
        assert saved.extractfile("native/broker.json").read() == before[str(native / "broker.json")]
        assert saved.extractfile("control/access-ledger.json").read() == before[str(control / "access-ledger.json")]
        assert saved.extractfile("control/projection.json").read() == before[str(control / "projection.json")]
    assert before == {str(path): path.read_bytes() for root in state for path in root.iterdir()}


@pytest.mark.parametrize("damage", ["owner", "state", "version-bool", "broker-state", "symlink"])
def test_unsafe_state_probe_refuses(state, tmp_path, damage):
    native, control = state
    if damage in {"owner", "state", "version-bool"}:
        marker = json.loads((native / "state-binding.json").read_text())
        marker[{"owner": "ownerId", "state": "stateId", "version-bool": "version"}[damage]] = True if damage == "version-bool" else "foreign"
        (native / "state-binding.json").write_text(json.dumps(marker))
    elif damage == "broker-state": (native / "broker.json").write_text('{"session":{"state":"unknown"}}')
    else:
        (native / "account-id").unlink()
        target = tmp_path / "outside"; target.write_text("synthetic-owner")
        (native / "account-id").symlink_to(target)
    with pytest.raises((ValueError, OSError)): runner_check(native, control, "synthetic-owner", "synthetic-state")


def test_running_and_ambiguous_checks_are_explicit_not_auto_repaired(state):
    native, control = state
    path = native / "broker.json"
    original = '{"running":{"state":"running"},"ambiguous":{"state":"failed","error":"codex_unavailable"}}'
    path.write_text(original)
    result = runner_check(native, control, "synthetic-owner", "synthetic-state")
    assert result["nativeRunning"] == result["ambiguous"] == 1 and path.read_text() == original


def test_backup_refuses_symlink_and_ignores_only_stopped_supply_socket(state, tmp_path):
    native, control = state
    connection = socket.socket(socket.AF_UNIX)
    try:
        connection.bind(str(control / "access.sock"))
        output = io.BytesIO(); stream(native, control, output)
        with tarfile.open(fileobj=io.BytesIO(output.getvalue())) as saved:
            assert "control/access.sock" not in saved.getnames()
        (native / "linked").symlink_to(tmp_path / "outside")
        with pytest.raises(ValueError): stream(native, control, io.BytesIO())
    finally:
        connection.close()


def test_backup_detects_concurrent_change_and_does_not_overwrite_state(state, monkeypatch):
    native, control = state
    original = tarfile.TarFile.add
    def mutation(archive, *args, **kwargs):
        (control / "late-writer").write_text("Not an accepted consistent backup")
        return original(archive, *args, **kwargs)
    monkeypatch.setattr(tarfile.TarFile, "add", mutation)
    with pytest.raises(ValueError): stream(native, control, io.BytesIO())
    assert (control / "late-writer").read_text() == "Not an accepted consistent backup"


@pytest.mark.parametrize("member", ["../outside", "/absolute", "native/../../outside", "foreign/file"])
def test_verify_never_extracts_or_accepts_unsafe_member_paths(tmp_path, member):
    path = tmp_path / "unsafe.tar"
    with tarfile.open(path, "w") as archive:
        info = tarfile.TarInfo(member); info.size = 1
        archive.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(ValueError): verify(path)
