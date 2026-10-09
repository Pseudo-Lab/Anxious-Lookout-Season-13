"""Synthetic C1/C2/C3 and reroute regressions; no live auth/provider."""
import json
import threading
import time
import uuid

import pytest

from app.codex_policy import CodexFailure
from app.migrate import migrate
from runner.auth import read_private, write_private, release_model_block
from tests.test_runner_auth import grant_fixture, native_fixture, message
from tests.test_runner_protocol import wait_idle
from tests.test_research import identity, headers
from tests.test_conversations import create_session
from tests.test_codex_status import HealthRunner, enable


def test_rotation_before_projection_and_retired_literals_across_partial_final_restart(tmp_path, monkeypatch):
    control, root, owner = grant_fixture(tmp_path)
    native = native_fixture(control, root, owner, monkeypatch, [])
    session = str(uuid.uuid4())
    native.accept(session, message())
    wait_idle(native, session)
    cache = read_private(root / "codex/auth.json")
    old = cache["tokens"]["access_token"]
    cache["tokens"]["access_token"] = "synthetic-rotated-access-value"
    write_private(root / "codex/auth.json", cache)
    projected = native.record({"turns": [{"id": "rotation", "items": [{"type": "agentMessage", "text": cache["tokens"]["access_token"]}]}]})
    assert cache["tokens"]["access_token"] not in json.dumps(projected)
    original = root / "codex/fixture-native-record.json"
    source = json.loads(original.read_text())
    thread = next(iter(source.values()))
    turn = thread["turns"][0]
    turn["status"] = "inProgress"
    turn["items"][-1]["text"] = "partial " + old
    native.store_record(session, native.mapping[session], {"turns": [turn]})
    native.save()
    native.close()
    turn["status"] = "completed"
    turn["items"][-1]["text"] = "completed prefix:" + old + ":suffix"
    original.write_text(json.dumps(source))
    native = native_fixture(control, root, owner, monkeypatch, [])
    try:
        result = native.read(session)
        assert result["record"]["turns"][0]["status"] == "completed"
        assert "completed prefix:[redacted]:suffix" in json.dumps(result)
        assert old not in json.dumps(result) and old not in (root / "broker.json").read_text()
        assert old not in (control / "projection.json").read_text()  # Fingerprints, no retired plaintext stash.
    finally:
        native.close()


@pytest.mark.parametrize("corruption", ["version", "legacy", "record", "journal", "baseline"])
def test_legacy_and_corrupted_safe_projection_are_not_trusted(tmp_path, monkeypatch, corruption):
    control, root, owner = grant_fixture(tmp_path)
    native = native_fixture(control, root, owner, monkeypatch, [])
    session = str(uuid.uuid4())
    native.accept(session, message())
    wait_idle(native, session)
    if corruption == "baseline":
        native.accept(session, message("/fixture/early-started-mismatch"))
        assert wait_idle(native, session)["state"] == "failed"
    native.close()
    if corruption == "journal":
        payload = read_private(control / "projection.json")
        payload["values"] = []
        write_private(control / "projection.json", payload)
    else:
        payload = read_private(root / "broker.json")
        if corruption == "version":
            payload[session].pop("projectionVersion")
        elif corruption == "legacy":
            payload[session]["projectionVersion"] = 2  # Pre-C4 projections may already contain unmarked failed assistants.
        elif corruption == "baseline":
            payload[session]["record"]["modelMismatchBaseline"].append(payload[session]["record"]["turns"][-1]["id"])
        else:
            payload[session]["record"]["turns"][0]["items"][-1]["text"] = "untrusted tampered cache"
        write_private(root / "broker.json", payload)
    if corruption == "baseline":
        with pytest.raises(CodexFailure):
            release_model_block(control, root)
    with pytest.raises(CodexFailure):
        native_fixture(control, root, owner, monkeypatch, [])


def test_poll_during_completed_projection_never_observes_unpaired_mac(tmp_path, monkeypatch):
    control, root, owner = grant_fixture(tmp_path)
    calls = []
    native = native_fixture(control, root, owner, monkeypatch, calls)
    session = str(uuid.uuid4())
    publishing, release, finished = threading.Event(), threading.Event(), threading.Event()
    results, failures = [], []
    reader = None
    try:
        native.accept(session, message())
        assert wait_idle(native, session)["state"] == "idle"
        mac = native.auth.projection_mac
        def held_mac(session_id, thread, record):
            if threading.current_thread() is native.worker and len(record["turns"]) == 2:
                publishing.set()
                assert release.wait(5)
            return mac(session_id, thread, record)
        monkeypatch.setattr(native.auth, "projection_mac", held_mac)
        native.accept(session, message())
        assert publishing.wait(5)
        def poll():
            try:
                results.append(native.read(session))
            except Exception as failure:
                failures.append(failure)
            finally:
                finished.set()
        reader = threading.Thread(target=poll)
        reader.start()
        assert not finished.wait(0.1), "Poll verified a partially published projection"
        release.set()
        reader.join(5)
        assert finished.is_set() and not failures
        assert len(results[0]["record"]["turns"]) == 2
        assert wait_idle(native, session)["state"] == "idle" and len(calls) == 2
    finally:
        release.set()
        if reader:
            reader.join(5)
        native.close()


@pytest.mark.parametrize("phase", ["idle", "active", "timeout"])
def test_unclean_close_keeps_tombstone_and_never_promotes_runtime_cache(tmp_path, monkeypatch, phase):
    control, root, owner = grant_fixture(tmp_path)
    native = native_fixture(control, root, owner, monkeypatch, [])
    original = read_private(control / "auth.json")
    if phase == "active":
        native.accept(str(uuid.uuid4()), message("/fixture/hold"))
        deadline = time.monotonic() + 5
        trace = root / "codex/fixture-model-rpc.json"
        while time.monotonic() < deadline:
            if trace.exists() and any(item["method"] == "turn/start" for item in json.loads(trace.read_text())):
                break
            time.sleep(0.01)
        assert native.active_session is not None
    elif phase == "timeout":
        session = str(uuid.uuid4())
        native.accept(session, message("/fixture/shutdown-timeout"))
        wait_idle(native, session)
    changed = read_private(root / "codex/auth.json")
    changed["tokens"]["access_token"] = "unconfirmed-runtime-rotation"
    write_private(root / "codex/auth.json", changed)
    if phase != "timeout":
        native.process.kill()
        native.process.wait(timeout=5)
    native.close()
    native.close()
    assert read_private(control / "ledger.json")["nativeActive"] is True
    assert read_private(control / "auth.json") == original
    assert not (root / "codex/auth.json").exists()
    with pytest.raises(CodexFailure):
        native_fixture(control, root, owner, monkeypatch, [])


def test_background_preflight_is_definite_and_same_key_new_input_recover(client, admin):
    migrate("0003_sessions")
    owner, auth = identity(client, admin)
    class Changes(HealthRunner):
        probes, allow = 0, False
        def health(self):
            self.probes += 1
            self.reason = None if self.allow or self.probes == 1 else "auth_expired"
            return super().health()
    runner = Changes()
    enable(client, owner, runner)
    session = create_session(client, auth)
    path = "/api/research/sessions/" + session["id"]
    body, retry_headers = {"text": "Undispatched retained input", "expectedVersion": 1}, headers(auth)
    first = client.post(path + "/messages", json=body, headers=retry_headers)
    assert first.status_code == 202 and runner.dispatches == 0
    detail = client.get(path).json()
    assert detail["state"] == "failed" and detail["version"] == 3
    assert detail["error"]["code"] == "codex_auth_expired" and detail["items"][0]["status"] == "not_recorded"
    assert client.post(path + "/messages", json=body, headers=retry_headers).json() == first.json()
    assert runner.probes == 2 and runner.dispatches == 0
    runner.allow = True
    new = client.post(path + "/messages", json={"text": "New explicit input", "expectedVersion": detail["version"]}, headers=headers(auth))
    assert new.status_code == 202 and runner.dispatches == 1
    detail = client.get(path).json()
    assert [item["text"] for item in detail["items"]] == [body["text"], "New explicit input"]


def test_reroute_is_account_wide_persistent_and_bad_assistant_never_projected(tmp_path, monkeypatch):
    control, root, owner = grant_fixture(tmp_path)
    calls = []
    native = native_fixture(control, root, owner, monkeypatch, calls)
    session = str(uuid.uuid4())
    native.accept(session, message())
    first = wait_idle(native, session)
    good_assistant = first["record"]["turns"][0]["items"][-1]
    native.accept(session, message("/fixture/rerouted"))
    failed = wait_idle(native, session)
    assert failed["state"] == "failed" and failed["errorCode"] == "codex_model_unavailable"
    bad = failed["record"]["turns"][-1]
    assert bad["modelMismatch"] and not any(item["type"] == "agentMessage" for item in bad["items"])
    assert failed["record"]["turns"][0]["items"][-1] == good_assistant
    for identity in (session, str(uuid.uuid4())):
        with pytest.raises(CodexFailure) as refused:
            native.accept(identity, message())
        assert refused.value.reason == "model_unavailable"
    native.close()
    native = native_fixture(control, root, owner, monkeypatch, calls)
    try:
        assert native.availability() == "model_unavailable"
        with pytest.raises(CodexFailure):
            native.accept(session, message())
    finally:
        native.close()
    before = list(read_private(control / "ledger.json")["requests"])
    release_model_block(control, root)
    assert read_private(control / "ledger.json")["requests"] == before
    native = native_fixture(control, root, owner, monkeypatch, calls)
    try:
        native.accept(session, message())
        assert wait_idle(native, session)["state"] == "idle"
        assert len(calls) == 2  # Original good turn + explicitly released followup only.
    finally:
        native.close()


@pytest.mark.parametrize("event", ["started", "updated", "completed"])
@pytest.mark.parametrize("identity", ["current", "missing", "stale", "foreign"])
def test_early_model_failure_quarantines_new_turns_through_read_and_restart(tmp_path, monkeypatch, event, identity):
    control, root, owner = grant_fixture(tmp_path)
    calls = []
    native = native_fixture(control, root, owner, monkeypatch, calls)
    session = str(uuid.uuid4())
    try:
        native.accept(session, message())
        previous = wait_idle(native, session)["record"]["turns"][0]
        native.accept(session, message(f"/fixture/early-{event}-{identity}-mismatch"))
        result = wait_idle(native, session)
        assert result["state"] == "failed" and result["errorCode"] == "codex_model_unavailable"
        assert len(calls) == 1 and native.availability() == "model_unavailable"
        assert result["record"]["turns"][0] == previous
        failed = result["record"]["turns"][-1]
        assert failed["modelMismatch"] is True
        assert all(item["modelMismatch"] is True for item in failed["items"])
        assert not any(item["type"] == "agentMessage" for item in failed["items"])
        # The quarantined turn survives another original read, not just cache.
        assert native.read(session)["record"] == result["record"]
    finally:
        native.close()
    original = root / "codex/fixture-native-record.json"
    source = json.loads(original.read_text())
    turns = next(iter(source.values()))["turns"]
    turns[-1]["items"].append({"id": "late-answer", "type": "agentMessage", "text": "Late unsafe answer"})
    turns.append({"id": "previously-unidentified-turn", "items": [
        {"id": "unknown-answer", "type": "agentMessage", "text": "Unidentified unsafe answer"}
    ]})
    original.write_text(json.dumps(source))
    native = native_fixture(control, root, owner, monkeypatch, calls)
    try:
        assert native.availability() == "model_unavailable"
        result = native.read(session)
        assert result["record"]["turns"][0] == previous
        assert len(result["record"]["turns"]) == 3
        for failed in result["record"]["turns"][1:]:
            assert failed["modelMismatch"] is True
            assert not any(item["type"] == "agentMessage" for item in failed["items"])
        assert len(calls) == 1
        native.verify_projection(session, native.mapping[session])
    finally:
        native.close()
    before = list(read_private(control / "ledger.json")["requests"])
    release_model_block(control, root)
    assert read_private(control / "ledger.json")["requests"] == before
    native = native_fixture(control, root, owner, monkeypatch, calls)
    try:
        native.accept(session, message())
        result = wait_idle(native, session)
        assert result["state"] == "idle"
        assert result["record"]["turns"][0] == previous
        assert all(turn.get("modelMismatch") for turn in result["record"]["turns"][1:3])
        assert not result["record"]["turns"][-1].get("modelMismatch")
        assert any(item["type"] == "agentMessage" for item in result["record"]["turns"][-1]["items"])
        assert len(calls) == 2  # Original good turn + explicitly released followup only.
    finally:
        native.close()
