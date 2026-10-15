"""Synthetic owner/state isolation, real adapter fixture RPC/ledger, no provider."""
import json
import os
import uuid
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.codex_policy import MODEL, CodexFailure
from app.conversations import Runner, runners_from_file
from runner.app import Native, Turn, create_app
from runner.auth import PersonalAuth, read_private, write_private
from runner.binding import initialize_binding, require_binding
from tests.test_runner_auth import grant_fixture, message
from tests.test_runner_protocol import wait_idle

HTTP_CLIENT = httpx.Client


def test_explicit_fresh_binding_cannot_adopt_or_reinitialize_state(tmp_path):
    owner, state = str(uuid.uuid4()), str(uuid.uuid4())
    root = tmp_path / "native"
    initialize_binding(root, owner, state)
    require_binding(root, owner, state)
    assert root.stat().st_mode & 0o777 == 0o700
    assert (root / "state-binding.json").stat().st_mode & 0o777 == 0o600
    before = (root / "state-binding.json").read_bytes()
    with pytest.raises(CodexFailure): initialize_binding(root, owner, state)
    assert (root / "state-binding.json").read_bytes() == before
    with pytest.raises(CodexFailure): require_binding(root, str(uuid.uuid4()), state)
    with pytest.raises(CodexFailure): require_binding(root, owner, str(uuid.uuid4()))
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "broker.json").write_text("{}")
    with pytest.raises(CodexFailure): initialize_binding(legacy, owner, state)
    assert not (legacy / "state-binding.json").exists()


def pod_app(monkeypatch, root, owner, state, token_file, pod_uid):
    for name, value in {"APP_ENV": "test", "RUNNER_ACCOUNT_ID": owner, "RUNNER_STATE_ID": state,
        "RUNNER_POD_UID": pod_uid, "RUNNER_STATE_DIR": str(root), "RUNNER_TOKEN_FILE": str(token_file),
        "CODEX_MODEL": MODEL, "CODEX_AUTH_MODE": "none", "CODEX_BIN": "/usr/local/bin/codex-fixture",
        "RESEARCH_TOOL_CALLBACK_URL": "http://callback.invalid/tools"}.items():
        monkeypatch.setenv(name, value)
    return create_app()


@pytest.mark.parametrize("damage", ["missing", "foreign-owner", "foreign-state", "version-bool"])
def test_bad_startup_marker_refuses_before_any_native_construction(tmp_path, monkeypatch, damage):
    owner, state = str(uuid.uuid4()), str(uuid.uuid4())
    root = tmp_path / "root"
    initialize_binding(root, owner, state)
    marker = root / "state-binding.json"
    if damage == "missing": marker.unlink()
    else:
        value = read_private(marker)
        value[{"foreign-owner": "ownerId", "foreign-state": "stateId", "version-bool": "version"}[damage]] = (
            True if damage == "version-bool" else str(uuid.uuid4()))
        write_private(marker, value)
    before = sorted(p.name for p in root.iterdir())
    constructions = []
    monkeypatch.setattr("runner.app.Native", lambda *a, **k: constructions.append(True))
    token = tmp_path / "token"
    token.write_text("t" * 40)
    app = pod_app(monkeypatch, root, owner, state, token, str(uuid.uuid4()))
    with pytest.raises(CodexFailure):
        with TestClient(app): pass
    assert constructions == [] and sorted(p.name for p in root.iterdir()) == before
    assert not (root / "broker.json").exists()


def test_health_a_submit_b_rejected_before_native_tools_or_real_fixture_ledger(tmp_path, monkeypatch):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    control_a, _, owner = grant_fixture(tmp_path / "a")
    control_b, _, _ = grant_fixture(tmp_path / "b")
    grant_b = read_private(control_b / "grant.json")
    grant_b["platformAccountId"] = owner
    write_private(control_b / "grant.json", grant_b)
    state_a, state_b = str(uuid.uuid4()), str(uuid.uuid4())
    root_a, root_b = tmp_path / "state-a", tmp_path / "state-b"
    initialize_binding(root_a, owner, state_a)
    initialize_binding(root_b, owner, state_b)
    token = tmp_path / "token"
    token.write_text("t" * 40)  # Same owner transport credentials: state guard is necessary.
    callbacks, accepted = [], []
    controls = {root_a: control_a, root_b: control_b}
    def native_factory(root, model, binary, callback, auth=None, binding_check=None):
        native = Native(root, model, binary, callback, auth=PersonalAuth(controls[root], owner), binding_check=binding_check)
        original_accept = native.accept
        def accept(*args):
            accepted.append(root)
            return original_accept(*args)
        native.accept = accept
        return native
    monkeypatch.setattr("runner.app.Native", native_factory)
    a_app = pod_app(monkeypatch, root_a, owner, state_a, token, str(uuid.uuid4()))
    b_app = pod_app(monkeypatch, root_b, owner, state_b, token, str(uuid.uuid4()))
    with TestClient(a_app) as a, TestClient(b_app) as b:
        before_ledger = (control_b / "ledger.json").read_bytes()
        before_broker = (root_b / "broker.json").read_bytes()
        def transport(request):
            if request.url.host == "callback.invalid":
                callbacks.append(json.loads(request.content))
                return httpx.Response(200, json={"content": "fixture output"})
            # A answers the successful health. Endpoint changes before POST.
            response = (a if request.method == "GET" else b).request(
                request.method, request.url.path, content=request.content, headers=dict(request.headers))
            return httpx.Response(response.status_code, json=response.json())
        monkeypatch.setattr(httpx, "Client", lambda **kw: HTTP_CLIENT(transport=httpx.MockTransport(transport), **kw))
        connection = Runner("http://slot.invalid", "t" * 40, uuid.UUID(owner), state_a)
        assert connection.health()["stateId"] == state_a
        with pytest.raises(httpx.HTTPStatusError) as rejected:
            connection.submit(uuid.uuid4(), {"requestId": str(uuid.uuid4()), "text": "explicit fixture", "tools": [], "toolToken": "u" * 40})
        assert rejected.value.response.status_code == 503
        assert accepted == callbacks == []
        assert (control_b / "ledger.json").read_bytes() == before_ledger
        assert (root_b / "broker.json").read_bytes() == before_broker
        assert not (root_b / "codex/fixture-model-rpc.json").exists()


def test_same_state_process_replacement_preserves_thread_model_and_ledger(tmp_path, monkeypatch):
    control, _, owner = grant_fixture(tmp_path)
    root, state = tmp_path / "bound-native", str(uuid.uuid4())
    initialize_binding(root, owner, state)
    calls = []
    def callback(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"content": "complete fixture tool output"})
    monkeypatch.setattr(httpx, "Client", lambda **kw: HTTP_CLIENT(transport=httpx.MockTransport(callback), **kw))
    check = lambda: require_binding(root, owner, state)
    session, first = str(uuid.uuid4()), message()
    native = Native(root, MODEL, "/usr/local/bin/codex-fixture", "http://callback.invalid/tools",
                    auth=PersonalAuth(control, owner), binding_check=check)
    native.accept(session, first)
    record = wait_idle(native, session)
    thread = native.mapping[session]["thread"]
    native.close()
    restarted = Native(root, MODEL, "/usr/local/bin/codex-fixture", "http://callback.invalid/tools",
                       auth=PersonalAuth(control, owner), binding_check=check)
    try:
        assert restarted.read(session)["record"] == record["record"]
        restarted.accept(session, first)
        assert len(read_private(control / "ledger.json")["requests"]) == len(calls) == 1
        follow_up = message("same user reconnect follow-up")
        follow_up.expectedThreadId = thread
        restarted.accept(session, follow_up)
        assert wait_idle(restarted, session)["state"] == "idle"
        assert restarted.mapping[session]["thread"] == thread
        trace = json.loads((root / "codex/fixture-model-rpc.json").read_text())
        assert sum(v["method"] == "thread/start" for v in trace) == 1
        assert sum(v["method"] == "thread/resume" for v in trace) == 1
        assert all(v["model"] == MODEL for v in trace)
        assert len(read_private(control / "ledger.json")["requests"]) == len(calls) == 2
    finally:
        restarted.close()


def test_missing_session_or_changed_expected_thread_never_starts_or_charges(tmp_path, monkeypatch):
    control, _, owner = grant_fixture(tmp_path)
    root, state = tmp_path / "bound-native", str(uuid.uuid4())
    initialize_binding(root, owner, state)
    native = Native(root, MODEL, "/usr/local/bin/codex-fixture", "http://callback.invalid/tools",
        auth=PersonalAuth(control, owner), binding_check=lambda: require_binding(root, owner, state))
    before_ledger = (control / "ledger.json").read_bytes()
    before_broker = (root / "broker.json").read_bytes()
    try:
        body = message()
        body.expectedThreadId = "private-established-thread"
        with pytest.raises(CodexFailure): native.accept(str(uuid.uuid4()), body)
        assert (control / "ledger.json").read_bytes() == before_ledger
        assert (root / "broker.json").read_bytes() == before_broker
        assert not (root / "codex/fixture-model-rpc.json").exists()
    finally:
        native.close()


def test_resume_response_cannot_switch_thread_or_dispatch_another_turn(tmp_path, monkeypatch):
    control, _, owner = grant_fixture(tmp_path)
    root, state = tmp_path / "bound-native", str(uuid.uuid4())
    initialize_binding(root, owner, state)
    calls = []
    monkeypatch.setattr(httpx, "Client", lambda **kw: HTTP_CLIENT(transport=httpx.MockTransport(
        lambda request: calls.append(json.loads(request.content)) or httpx.Response(200, json={"content": "fixture"})), **kw))
    native = Native(root, MODEL, "/usr/local/bin/codex-fixture", "http://callback.invalid/tools",
        auth=PersonalAuth(control, owner), binding_check=lambda: require_binding(root, owner, state))
    session = str(uuid.uuid4())
    try:
        native.accept(session, message())
        assert wait_idle(native, session)["state"] == "idle"
        thread, rpc = native.mapping[session]["thread"], native.rpc
        native.rpc = lambda method, params=None, turn=None: ({"thread": {"id": "wrong-native-thread", "model": MODEL}}
            if method == "thread/resume" else rpc(method, params, turn))
        follow_up = message()
        follow_up.expectedThreadId = thread
        native.accept(session, follow_up)
        result = wait_idle(native, session)
        assert result["state"] == "failed" and result["errorCode"] == "codex_policy_refused"
        assert native.mapping[session]["thread"] == thread and len(calls) == 1
        trace = json.loads((root / "codex/fixture-model-rpc.json").read_text())
        assert sum(v["method"] == "turn/start" for v in trace) == 1
        assert len(read_private(control / "ledger.json")["requests"]) == 2  # Failed resume consumes its reserved slot.
    finally:
        native.close()


@pytest.mark.parametrize("damage", ["removed", "changed", "restored"])
def test_runtime_marker_change_stops_admission_history_and_tools(tmp_path, monkeypatch, damage):
    control, _, owner = grant_fixture(tmp_path)
    root, state = tmp_path / "bound-native", str(uuid.uuid4())
    initialize_binding(root, owner, state)
    callbacks = []
    monkeypatch.setattr(httpx, "Client", lambda **kw: HTTP_CLIENT(transport=httpx.MockTransport(
        lambda request: callbacks.append(True) or httpx.Response(200, json={"ok": True})), **kw))
    native = Native(root, MODEL, "/usr/local/bin/codex-fixture", "http://callback.invalid/tools",
                    auth=PersonalAuth(control, owner), binding_check=lambda: require_binding(root, owner, state))
    before_ledger = (control / "ledger.json").read_bytes()
    before_broker = (root / "broker.json").read_bytes()
    marker = root / "state-binding.json"
    if damage == "removed": marker.unlink()
    else: write_private(marker, {"version": 1, "ownerId": owner, "stateId": str(uuid.uuid4())})
    try:
        assert native.availability() == "policy_refused"
        if damage == "restored":
            write_private(marker, {"version": 1, "ownerId": owner, "stateId": state})
            assert native.availability() == "policy_refused"  # Restoring bytes is not reconciliation.
        with pytest.raises(CodexFailure): native.accept(str(uuid.uuid4()), message())
        with pytest.raises(CodexFailure): native.read(str(uuid.uuid4()))
        assert callbacks == [] and (control / "ledger.json").read_bytes() == before_ledger
        assert (root / "broker.json").read_bytes() == before_broker
        assert not (root / "codex/fixture-model-rpc.json").exists()
    finally:
        native.close()
    assert read_private(control / "ledger.json")["nativeActive"] is True
    if damage == "restored":
        with pytest.raises(CodexFailure) as restart:
            Native(root, MODEL, "/usr/local/bin/codex-fixture", "http://callback.invalid/tools",
                   auth=PersonalAuth(control, owner), binding_check=lambda: require_binding(root, owner, state))
        assert restart.value.reason == "auth_revoked"


def test_http_observed_binding_failure_survives_restore_close_and_restart(tmp_path, monkeypatch):
    control, _, owner = grant_fixture(tmp_path)
    root, state = tmp_path / "bound-native", str(uuid.uuid4())
    initialize_binding(root, owner, state)
    token = tmp_path / "token"
    token.write_text("t" * 40)
    def factory(root, model, binary, callback, auth=None, binding_check=None):
        return Native(root, model, binary, callback, auth=PersonalAuth(control, owner), binding_check=binding_check)
    monkeypatch.setattr("runner.app.Native", factory)
    app = pod_app(monkeypatch, root, owner, state, token, str(uuid.uuid4()))
    headers = {"Authorization": "Bearer " + "t" * 40, "X-Runner-State-Id": state}
    with TestClient(app) as client:
        marker = root / "state-binding.json"
        original = read_private(marker)
        write_private(marker, {**original, "stateId": str(uuid.uuid4())})
        assert client.get("/health", headers=headers).status_code == 503
        assert app.state.native.binding_failed is True
        write_private(marker, original)
        result = client.get("/health", headers=headers)
        assert result.json()["available"] is False and result.json()["reason"] == "policy_refused"
    assert read_private(control / "ledger.json")["nativeActive"] is True
    with pytest.raises(CodexFailure) as restart:
        Native(root, MODEL, "/usr/local/bin/codex-fixture", "http://callback.invalid/tools",
               auth=PersonalAuth(control, owner), binding_check=lambda: require_binding(root, owner, state))
    assert restart.value.reason == "auth_revoked"


def test_real_runner_mapping_needs_state_identity_and_owner_response(tmp_path, monkeypatch):
    token, config = tmp_path / "token", tmp_path / "mapping.json"
    token.write_text("t" * 40)
    monkeypatch.setenv("CODEX_RUNNERS_FILE", str(config))
    monkeypatch.setenv("APP_ENV", "personal-test")
    owner, state = str(uuid.uuid4()), str(uuid.uuid4())
    entry = {"url": "http://slot.invalid", "tokenFile": str(token)}
    config.write_text(json.dumps({owner: entry}))
    with pytest.raises(ValueError): runners_from_file()
    entry["stateId"] = state
    config.write_text(json.dumps({owner: entry}))
    assert runners_from_file()[uuid.UUID(owner)].state_id == state
    for wrong in ({"ownerId": str(uuid.uuid4()), "stateId": state}, {"ownerId": owner, "stateId": str(uuid.uuid4())}):
        monkeypatch.setattr(httpx, "Client", lambda **kw: HTTP_CLIENT(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=wrong)), **kw))
        with pytest.raises(ValueError): runners_from_file()[uuid.UUID(owner)].health()


def test_pod_uid_is_auxiliary_and_can_change_with_same_owner_state(tmp_path, monkeypatch):
    owner, state = str(uuid.uuid4()), str(uuid.uuid4())
    root = tmp_path / "bound"
    initialize_binding(root, owner, state)
    token = tmp_path / "token"
    token.write_text("t" * 40)
    class IdleNative:
        fixture, real_verified = True, False
        def __init__(self, *a, **kw): pass
        def availability(self): return None
        def close(self): pass
    monkeypatch.setattr("runner.app.Native", IdleNative)
    seen = []
    for pod in (str(uuid.uuid4()), str(uuid.uuid4())):
        app = pod_app(monkeypatch, root, owner, state, token, pod)
        with TestClient(app) as client:
            response = client.get("/health", headers={"Authorization": "Bearer " + "t" * 40, "X-Runner-State-Id": state})
            assert response.status_code == 200 and response.json()["ownerId"] == owner
            assert response.json()["stateId"] == state and response.json()["podUid"] == pod
            seen.append(response.json()["podUid"])
    assert seen[0] != seen[1]
