"""Synthetic credentials only. Never reads a host or live provider cache."""
import json
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.codex_policy import MODEL, CodexFailure
from runner.app import Native, Turn, bind_owner
from runner.auth import PersonalAuth, cleanup, read_private, write_private
from tests.test_runner_protocol import wait_idle

HTTP_CLIENT = httpx.Client


def grant_fixture(tmp_path):
    control, root = tmp_path / "private-control", tmp_path / "native-owner"
    control.mkdir(mode=0o700)
    owner = str(uuid.uuid4())
    bind_owner(root, owner)
    grant = {"version": 1, "platformAccountId": owner, "providerAccountId": "synthetic-provider-identity",
             "trialId": str(uuid.uuid4()), "expiresAt": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
             "maxDispatches": 3, "executionScope": "personal-private", "refreshOwnership": "exclusive-managed-native",
             "procedureReviewed": True, "hostGrantQuiescent": True, "revoked": False}
    write_private(control / "grant.json", grant)
    write_private(control / "auth.json", {"OPENAI_API_KEY": None, "tokens": {"account_id": grant["providerAccountId"],
                  "access_token": "synthetic-access-secret-sentinel", "refresh_token": "synthetic-refresh-secret-sentinel", "id_token": "synthetic-id-secret-sentinel"}})
    return control, root, owner


def native_fixture(control, root, owner, monkeypatch, calls):
    original = HTTP_CLIENT
    def callback(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"content": "Synthetic callback complete"})
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(callback), **kwargs))
    return Native(root, MODEL, "/usr/local/bin/codex-fixture", "http://fixture.invalid/tool", auth=PersonalAuth(control, owner))


def message(text="Synthetic explicit turn"):
    return Turn(requestId=uuid.uuid4(), text=text, tools=[], toolToken="s" * 40)


def test_fixed_model_applies_to_start_resume_turn_and_rejects_override(tmp_path, monkeypatch):
    control, root, owner = grant_fixture(tmp_path)
    calls = []
    native = native_fixture(control, root, owner, monkeypatch, calls)
    session = str(uuid.uuid4())
    try:
        for _ in range(2):
            native.accept(session, message())
            assert wait_idle(native, session)["state"] == "idle"
        trace = json.loads((root / "codex/fixture-model-rpc.json").read_text())
        assert {item["method"] for item in trace} == {"thread/start", "thread/resume", "turn/start"}
        assert all(item["model"] == MODEL for item in trace)
        assert native.real_verified is False  # Fixture completion never proves model entitlement.
        with pytest.raises(CodexFailure):
            Native(tmp_path / "wrong", "another-model", "/usr/local/bin/codex-fixture", "http://fixture.invalid/tool")
    finally:
        native.close()


@pytest.mark.parametrize("prompt", ["/fixture/rerouted", "/fixture/model-response"])
def test_model_change_blocks_all_later_callbacks_and_success(tmp_path, monkeypatch, prompt):
    control, root, owner = grant_fixture(tmp_path)
    calls = []
    native = native_fixture(control, root, owner, monkeypatch, calls)
    session = str(uuid.uuid4())
    try:
        native.accept(session, message(prompt))
        result = wait_idle(native, session)
        assert result["state"] == "failed" and result["errorCode"] == "codex_model_unavailable"
        assert calls == [] and native.availability() == "model_unavailable" and not native.real_verified
        with pytest.raises(CodexFailure):
            native.accept(session, message())
    finally:
        native.close()


def test_three_dispatch_limit_survives_restart_resume_retry_and_native_restore(tmp_path, monkeypatch):
    control, root, owner = grant_fixture(tmp_path)
    calls = []
    native = native_fixture(control, root, owner, monkeypatch, calls)
    session, first = str(uuid.uuid4()), message()
    native.accept(session, first)
    assert wait_idle(native, session)["state"] == "idle"
    native.close()
    assert not (root / "codex/auth.json").exists()
    restored = tmp_path / "restored-native"
    shutil.copytree(root, restored)
    native = native_fixture(control, root, owner, monkeypatch, calls)
    try:
        native.accept(session, first)
        assert len(read_private(control / "ledger.json")["requests"]) == 1
        for _ in range(2):
            native.accept(session, message())
            assert wait_idle(native, session)["state"] == "idle"
        with pytest.raises(CodexFailure) as denied:
            native.accept(session, message())
        assert denied.value.reason == "budget_exhausted"
        assert len(read_private(control / "ledger.json")["requests"]) == 3
    finally:
        native.close()
    native = native_fixture(control, restored, owner, monkeypatch, calls)
    try:
        with pytest.raises(CodexFailure) as denied:
            native.accept(session, message())
        assert denied.value.reason == "budget_exhausted"  # External ledger wasn't rolled back with native home.
    finally:
        native.close()


def test_secret_tool_arguments_never_reach_storage_and_projection_is_redacted(tmp_path, monkeypatch):
    control, root, owner = grant_fixture(tmp_path)
    calls = []
    native = native_fixture(control, root, owner, monkeypatch, calls)
    session = str(uuid.uuid4())
    try:
        native.accept(session, message("/fixture/credential-tool"))
        result = wait_idle(native, session)
        assert calls == []
        projection = json.dumps(result)
        assert "synthetic-access-secret-sentinel" not in projection
        assert "synthetic-provider-identity" not in projection
        assert "synthetic-access-secret-sentinel" not in (root / "broker.json").read_text()
    finally:
        native.close()


def test_expiry_revocation_cleanup_and_abandoned_native_block_reactivation(tmp_path):
    control, root, owner = grant_fixture(tmp_path)
    home = root / "codex"
    home.mkdir(mode=0o700)
    auth = PersonalAuth(control, owner)
    auth.attach(home)
    grant = read_private(control / "grant.json")
    grant["expiresAt"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    write_private(control / "grant.json", grant)
    with pytest.raises(CodexFailure) as expired:
        auth.consume(str(uuid.uuid4()))
    assert expired.value.reason == "auth_expired"
    auth.close()
    assert not (home / "auth.json").exists()
    # The non-quiescent shutdown remained recorded; no silent use of an old cache.
    assert read_private(control / "ledger.json")["nativeActive"] is True
    cleanup(control)
    assert not (control / "auth.json").exists()
    with pytest.raises(CodexFailure):
        PersonalAuth(control, owner).attach(home)


def test_wrong_owner_unsafe_input_and_conflicting_refresh_owner_are_rejected(tmp_path):
    control, root, owner = grant_fixture(tmp_path)
    with pytest.raises(CodexFailure):
        PersonalAuth(control, str(uuid.uuid4())).grant()
    grant = read_private(control / "grant.json")
    grant["hostGrantQuiescent"] = False
    write_private(control / "grant.json", grant)
    with pytest.raises(CodexFailure):
        PersonalAuth(control, owner).grant()
    grant["hostGrantQuiescent"] = True
    write_private(control / "grant.json", grant)
    home = root / "codex"
    home.mkdir(mode=0o700)
    first = PersonalAuth(control, owner)
    first.attach(home)
    try:
        contender = PersonalAuth(control, owner)
        with pytest.raises(CodexFailure):
            contender.attach(home)
        contender.close()
        assert (home / "auth.json").exists()
    finally:
        first.close()
    (control / "auth.json").chmod(0o644)
    with pytest.raises(CodexFailure):
        PersonalAuth(control, owner).attach(home)


@pytest.mark.parametrize("prompt,code", [("/fixture/auth-error", "codex_auth_expired"), ("/fixture/model-error", "codex_model_unavailable"), ("/fixture/policy-error", "codex_policy_refused")])
def test_failed_provider_requests_consume_budget_but_never_expose_raw_errors(tmp_path, monkeypatch, prompt, code):
    control, root, owner = grant_fixture(tmp_path)
    calls = []
    native = native_fixture(control, root, owner, monkeypatch, calls)
    session = str(uuid.uuid4())
    try:
        native.accept(session, message(prompt))
        result = wait_idle(native, session)
        assert result["state"] == "failed" and result["errorCode"] == code
        assert len(read_private(control / "ledger.json")["requests"]) == 1
        assert calls == []
        assert "synthetic-access-secret-sentinel" not in json.dumps(result)
        assert "synthetic-provider-identity" not in (root / "broker.json").read_text()
    finally:
        native.close()


def test_response_model_change_during_resume_consumes_attempt_without_writing_tools(tmp_path, monkeypatch):
    control, root, owner = grant_fixture(tmp_path)
    calls = []
    native = native_fixture(control, root, owner, monkeypatch, calls)
    session = str(uuid.uuid4())
    native.accept(session, message())
    assert wait_idle(native, session)["state"] == "idle"
    native.close()
    cache_path = root / "codex/fixture-native-record.json"
    cached = json.loads(cache_path.read_text())
    next(iter(cached.values()))["returnModel"] = "wrong-response-model"
    cache_path.write_text(json.dumps(cached))
    native = native_fixture(control, root, owner, monkeypatch, calls)
    try:
        native.accept(session, message())
        result = wait_idle(native, session)
        assert result["state"] == "failed" and result["errorCode"] == "codex_model_unavailable"
        assert len(calls) == 1 and len(read_private(control / "ledger.json")["requests"]) == 2
    finally:
        native.close()


def test_private_runtime_cannot_label_dummy_completion_as_real(tmp_path, monkeypatch):
    control, root, owner = grant_fixture(tmp_path)
    monkeypatch.setenv("APP_ENV", "personal-test")
    with pytest.raises(CodexFailure):
        Native(root, MODEL, "/usr/local/bin/codex-fixture", "http://fixture.invalid/tools", auth=PersonalAuth(control, owner))
    assert not (control / "ledger.json").exists()


def test_private_validator_and_failed_start_cleanup_cli_never_print_inputs(tmp_path):
    control, root, owner = grant_fixture(tmp_path)
    validate = subprocess.run([sys.executable, "-m", "runner.auth", "--control", str(control), "--validate"], capture_output=True, text=True)
    assert validate.returncode == 0
    cleaned = subprocess.run([sys.executable, "-m", "runner.auth", "--control", str(control), "--cleanup", "--native-root", str(root)], capture_output=True, text=True)
    assert cleaned.returncode == 0
    assert not (control / "auth.json").exists() and read_private(control / "grant.json")["revoked"] is True
    validate_after = subprocess.run([sys.executable, "-m", "runner.auth", "--control", str(control), "--validate"], capture_output=True, text=True)
    assert validate_after.returncode != 0
    output = validate.stdout + validate.stderr + cleaned.stdout + cleaned.stderr + validate_after.stdout + validate_after.stderr
    assert owner not in output and "synthetic-provider-identity" not in output and "synthetic-access-secret-sentinel" not in output
