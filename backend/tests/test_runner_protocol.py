import json
import shutil
import time
import uuid

import httpx
import pytest

from app.research_tools import definitions
from runner.app import Native, Turn


def wait_idle(native, session):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = native.read(session)
        if result and result["state"] != "running":
            return result
        time.sleep(0.01)
    raise AssertionError("Offline native protocol fixture did not finish")


def test_offline_native_protocol_tool_roundtrip_and_process_restart(tmp_path, monkeypatch):
    requests = []
    original = httpx.Client
    def tool_callback(request):
        assert request.headers["Authorization"].startswith("Bearer ")
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(200, json={"id": str(uuid.uuid4()), "content": "Complete fixture output"})
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(tool_callback), **kwargs))
    root = tmp_path / "account-one"
    native = Native(root, "fixture-no-provider", "/usr/local/bin/codex-fixture", "https://fixture.invalid/tools")
    session, request = str(uuid.uuid4()), uuid.uuid4()
    body = Turn(requestId=request, text="Save fixture", tools=definitions(), toolToken="t" * 40)
    try:
        native.accept(session, body)
        first = wait_idle(native, session)
        assert first["state"] == "idle" and len(first["record"]["turns"]) == 1
        assert requests[0]["sessionId"] == session and requests[0]["requestId"] == str(request)
        native.accept(session, body)
        assert len(requests) == 1  # Persistent dispatch reservation deduplicates.
    finally:
        native.close()
    restarted = Native(root, "fixture-no-provider", "/usr/local/bin/codex-fixture", "https://fixture.invalid/tools")
    try:
        assert restarted.read(session)["record"] == first["record"]
        restarted.accept(session, body)
        assert len(requests) == 1
        restarted.accept(session, Turn(requestId=uuid.uuid4(), text="Continue fixture", tools=definitions(), toolToken="u" * 40))
        continued = wait_idle(restarted, session)
        assert len(continued["record"]["turns"]) == 2 and len(requests) == 2
        item = continued["record"]["turns"][0]["items"][1]
        assert item["arguments"]["content"] == "Complete fixture tool input"
        assert "Complete fixture output" in item["contentItems"][0]["text"]
    finally:
        restarted.close()
    restored_root = tmp_path / "restored-owner"
    shutil.copytree(root, restored_root)  # Quiesced full-state backup to a fresh fixture home.
    restored = Native(restored_root, "fixture-no-provider", "/usr/local/bin/codex-fixture", "https://fixture.invalid/tools")
    try:
        assert restored.read(session)["record"] == continued["record"]
        restored.accept(session, body)  # An older request ID must remain deduplicated after restore.
        assert len(requests) == 2
    finally:
        restored.close()
    isolated = Native(tmp_path / "account-two", "fixture-no-provider", "/usr/local/bin/codex-fixture", "https://fixture.invalid/tools")
    try:
        assert isolated.read(session) is None
        assert not (tmp_path / "account-two" / "codex" / "fixture-native-record.json").exists()
    finally:
        isolated.close()


def test_runner_config_cannot_share_destination_or_secret(tmp_path, monkeypatch):
    from app.conversations import runners_from_file
    token = tmp_path / "token"
    token.write_text("x" * 40)
    configuration = tmp_path / "mapping.json"
    monkeypatch.setenv("CODEX_RUNNERS_FILE", str(configuration))
    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    configuration.write_text(json.dumps({first: {"url": "http://fixture-one", "tokenFile": str(token)},
                                         second: {"url": "http://fixture-one", "tokenFile": str(token)}}))
    with pytest.raises(ValueError):
        runners_from_file()
    configuration.write_text(json.dumps({first: {"url": "http://fixture-one", "tokenFile": str(token)},
                                         second: {"url": "http://fixture-two", "tokenFile": str(token)}}))
    with pytest.raises(ValueError):
        runners_from_file()


def test_populated_or_other_owner_state_volume_cannot_be_adopted(tmp_path):
    from runner.app import bind_owner
    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    root = tmp_path / "bound"
    bind_owner(root, first)
    bind_owner(root, first)
    with pytest.raises(RuntimeError):
        bind_owner(root, second)
    unbound = tmp_path / "unbound"
    unbound.mkdir()
    (unbound / "broker.json").write_text("{}")
    with pytest.raises(RuntimeError):
        bind_owner(unbound, first)


def test_same_volume_cannot_run_two_native_processes(tmp_path):
    from runner.app import volume_lease
    with volume_lease(tmp_path):
        with pytest.raises(RuntimeError):
            with volume_lease(tmp_path):
                raise AssertionError("A second adapter must not share the paid-turn ledger")
    with volume_lease(tmp_path):
        pass
