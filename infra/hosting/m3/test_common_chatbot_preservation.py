"""Synthetic state/archives only; never host auth, provider or live state."""
import io
import json
import socket
import tarfile

import pytest

from common_chatbot_archive import stream, verify
from common_chatbot_probe import runner_check
from runner.projection import ProjectionJournal


@pytest.fixture
def state(tmp_path):
    native, control = tmp_path / "native", tmp_path / "control"
    native.mkdir(); control.mkdir()
    (native / "account-id").write_text("synthetic-owner")
    (native / "state-binding.json").write_text(json.dumps({"version": 1, "ownerId": "synthetic-owner", "stateId": "synthetic-state"}))
    journal = ProjectionJournal.create("synthetic-owner", "synthetic-trial")
    journal.observe({"synthetic-private-credential"})
    record = {"turns": [{"id": "synthetic-turn", "content": "Private native history"}]}
    (native / "broker.json").write_text(json.dumps({"session": {"state": "idle", "request": "already-reserved",
        "requests": {"already-reserved": "f" * 64}, "thread": "synthetic-thread", "record": record, "projectionVersion": 4,
        "projectionMac": journal.projection_mac("session", "synthetic-thread", record)}}))
    (control / "access-ledger.json").write_text(json.dumps({"version": 1, "owner": "synthetic-owner", "stateId": "synthetic-state",
        "trialId": "synthetic-trial", "accountId": "synthetic-account", "windowId": "prior-window",
        "requests": [{"id": "already-reserved", "windowId": "prior-window"}], "windows": ["prior-window"]}))
    (control / "access-projection.json").write_text(json.dumps(journal.payload()))
    return native, control


def test_probe_and_archive_preserve_full_history_ledger_and_integrity_without_restore(state, tmp_path):
    native, control = state
    before = {str(path): path.read_bytes() for root in state for path in root.iterdir()}
    result = runner_check(native, control, "synthetic-owner", "synthetic-state")
    assert result["nativeRunning"] == result["ambiguous"] == 0 and result["lifetimeReservations"] == 1
    assert "Private native history" not in json.dumps(result) and "prior-window" not in json.dumps(result)
    archive = tmp_path / "backup.tar"
    with archive.open("wb") as output: stream(native, control, output)
    assert verify(archive, result)["nativeControlArchiveVerified"] is True and verify(archive, result)["restored"] is False
    with tarfile.open(archive) as saved:
        assert saved.extractfile("native/broker.json").read() == before[str(native / "broker.json")]
        assert saved.extractfile("control/access-ledger.json").read() == before[str(control / "access-ledger.json")]
        assert saved.extractfile("control/access-projection.json").read() == before[str(control / "access-projection.json")]
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
    ledger = json.loads((control / "access-ledger.json").read_text())
    ledger["requests"].append({"id": "unknown-request", "windowId": "prior-window"})
    (control / "access-ledger.json").write_text(json.dumps(ledger))
    original = json.dumps({name: {"state": status, "request": request, "requests": {request: "f" * 64},
        "record": {"turns": []}, "projectionVersion": 4, **({"error": "codex_unavailable"} if name == "ambiguous" else {})}
        for name, status, request in (("running", "running", "already-reserved"), ("ambiguous", "failed", "unknown-request"))})
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


@pytest.mark.parametrize("name", ["native/broker.json", "control/access-ledger.json", "control/access-projection.json"])
def test_existing_used_owner_missing_file_refused_by_probe_and_archive(state, tmp_path, name):
    native, control = state
    expected = runner_check(native, control, "synthetic-owner", "synthetic-state")
    ((native if name.startswith("native/") else control) / name.split("/", 1)[1]).unlink()
    with pytest.raises(ValueError): runner_check(native, control, "synthetic-owner", "synthetic-state")
    path = tmp_path / "incomplete.tar"
    with path.open("wb") as output: stream(native, control, output)
    with pytest.raises(ValueError): verify(path, expected)


@pytest.mark.parametrize("damage", ["empty-broker", "empty-ledger", "reservation", "duplicate-request", "orphan-reservation", "lost-dedup", "window", "journal-owner",
    "journal-trial", "journal-mac", "history-mac", "journal-missing-key"])
def test_existing_protected_state_inconsistency_is_not_initialized_or_repaired(state, damage):
    native, control = state
    broker = json.loads((native / "broker.json").read_text())
    ledger = json.loads((control / "access-ledger.json").read_text())
    journal = json.loads((control / "access-projection.json").read_text())
    if damage == "empty-broker": broker = {}
    elif damage == "empty-ledger": ledger["requests"] = []
    elif damage == "reservation": broker["session"]["request"] = "not-reserved"
    elif damage == "duplicate-request": ledger["requests"] *= 2
    elif damage == "orphan-reservation": ledger["requests"].append({"id": "orphan", "windowId": "prior-window"})
    elif damage == "lost-dedup": broker["session"]["requests"] = {}
    elif damage == "window": ledger["requests"][0]["windowId"] = "unknown-window"
    elif damage == "history-mac": broker["session"]["record"]["turns"][0]["content"] = "tampered"
    elif damage == "journal-owner": journal["owner"] = "foreign"
    elif damage == "journal-trial": journal["trialId"] = "foreign"
    elif damage == "journal-mac": journal["mac"] = "0" * 64
    else: del journal["key"]
    for path, value in ((native / "broker.json", broker), (control / "access-ledger.json", ledger), (control / "access-projection.json", journal)):
        path.write_text(json.dumps(value))
    before = {str(path): path.read_bytes() for root in state for path in root.iterdir()}
    with pytest.raises((ValueError, KeyError)): runner_check(native, control, "synthetic-owner", "synthetic-state")
    assert before == {str(path): path.read_bytes() for root in state for path in root.iterdir()}


def test_complete_consistent_archive_still_refuses_changed_protected_state(state, tmp_path):
    native, control = state
    expected = runner_check(native, control, "synthetic-owner", "synthetic-state")
    # A valid JSON/MAC with different byte encoding is still not the original protected data.
    path = control / "access-ledger.json"
    path.write_text(json.dumps(json.loads(path.read_text()), indent=2))
    saved = tmp_path / "changed.tar"
    with saved.open("wb") as output: stream(native, control, output)
    with pytest.raises(ValueError): verify(saved, expected)
