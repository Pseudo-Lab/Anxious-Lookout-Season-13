"""Synthetic access-only service lifecycle, not real provider authentication."""
import base64
import json
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.codex_policy import CodexFailure
from ops.access_snapshot import selected_file, service_envelope
from runner.access import AccessAuth
from runner.auth import write_private, read_private
from runner.binding import initialize_binding
from runner.service_access import ServiceAccessAuth, LEDGER
from runner.app import create_app

ACCOUNT = "synthetic-selected-operator-account"


def jwt(expiry=None, account=ACCOUNT):
    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
    payload = {"exp": expiry or int(time.time()) + 7200, "nonce": str(uuid4()),
               "email": "synthetic-operator@example.invalid",
               "https://api.openai.com/auth": {"chatgpt_account_id": account}}
    return encode({"alg":"RS256"}) + "." + encode(payload) + ".synthetic-signature"


def setup(root, account=ACCOUNT):
    control, native = root / "control", root / "native"
    control.mkdir(parents=True, mode=0o700)
    owner, state = str(uuid4()), str(uuid4())
    initialize_binding(native, owner, state)
    home = native / "codex"
    home.mkdir(mode=0o700)
    token = jwt(account=account)
    source = (token, account, json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))["exp"])
    envelope = service_envelope(source, {"ownerId":owner, "stateId":state, "snapshotId":str(uuid4()), "expectedAccountId":account})
    return ServiceAccessAuth(control, owner, state, account), home, envelope


def refreshed(envelope):
    token = jwt()
    payload = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
    return {**envelope, "accessToken":token, "expiresAt":payload["exp"], "snapshotId":str(uuid4())}


def test_more_than_three_admissions_and_beyond_fifteen_minutes_keep_real_expiry(tmp_path, monkeypatch):
    auth, home, envelope = setup(tmp_path)
    start = time.time()
    auth.install(envelope); auth.attach(home)
    for _ in range(8): auth.consume(str(uuid4()))
    monkeypatch.setattr("runner.service_access.time.time", lambda: start + 1801)
    auth.check()
    assert len(auth.state()["requests"]) == 8 and auth.state()["deadline"] == envelope["expiresAt"] - 60
    assert auth.status() == {} and not (home / "auth.json").exists()
    monkeypatch.setattr("runner.service_access.time.time", lambda: envelope["expiresAt"] - 60)
    with pytest.raises(CodexFailure, match="codex_auth_expired"): auth.check()
    auth.close()


def test_legacy_trial_still_has_three_admissions_and_fifteen_minute_deadline(tmp_path, monkeypatch):
    prototype, home, envelope = setup(tmp_path)
    trial = AccessAuth(prototype.control, prototype.owner, prototype.state_id)
    initial = time.time()
    old = {key:value for key,value in envelope.items() if key != "snapshotId"}
    old.update(version=1, windowId=str(uuid4()), newWindow=True, maxDispatches=3)
    trial.install(old); trial.attach(home)
    for _ in range(3): trial.consume(str(uuid4()))
    with pytest.raises(CodexFailure, match="codex_budget_exhausted"): trial.check()
    assert trial.state()["deadline"] <= initial + 900
    monkeypatch.setattr("runner.service_access.time.time", lambda: initial + 901)
    with pytest.raises(CodexFailure, match="codex_auth_expired"): trial.check()
    assert not (prototype.control / LEDGER).exists()
    trial.close()


def test_source_token_refresh_never_resets_reservations_projection_or_identity(tmp_path):
    auth, home, envelope = setup(tmp_path)
    auth.install(envelope); auth.attach(home)
    request = str(uuid4()); auth.consume(request)
    key, context = auth.journal.key, auth.state()["trialId"]
    update = refreshed(envelope)
    auth.install(update)
    assert auth.journal.key == key and auth.state()["trialId"] == context
    assert auth.state()["requests"] == [{"id":request}]
    assert auth.sanitize({"old":envelope["accessToken"], "new":update["accessToken"]}) == {"old":"[redacted]", "new":"[redacted]"}
    with pytest.raises(CodexFailure): auth.consume(request)
    with pytest.raises(CodexFailure): auth.install(envelope)  # Old snapshot cannot roll back the current source.
    with pytest.raises(CodexFailure): auth.install({**update, "accessToken":envelope["accessToken"]})
    contents = "".join(path.read_text() for path in auth.control.iterdir() if path.is_file())
    assert envelope["accessToken"] not in contents and update["accessToken"] not in contents
    assert not (home / "auth.json").exists()
    auth.close()
    restarted = ServiceAccessAuth(auth.control, auth.owner, auth.state_id, ACCOUNT)
    with pytest.raises(CodexFailure, match="codex_unavailable"): restarted.check()
    restarted.install(update); restarted.attach(home)
    assert restarted.journal.key == key and restarted.state()["requests"] == [{"id":request}]
    assert restarted.sanitize(envelope["accessToken"]) == "[redacted]"
    with pytest.raises(CodexFailure): restarted.consume(request)
    restarted.close()


@pytest.mark.parametrize("reason", ["auth_expired", "auth_revoked"])
def test_auth_failure_needs_different_valid_snapshot_not_relabeling_old_token(tmp_path, reason):
    auth, home, envelope = setup(tmp_path)
    auth.install(envelope); auth.attach(home)
    auth.consume(str(uuid4())); auth.block(reason)
    with pytest.raises(CodexFailure): auth.install({**envelope, "snapshotId":str(uuid4())})
    auth.install(refreshed(envelope)); auth.check()
    assert len(auth.state()["requests"]) == 1
    auth.close()


@pytest.mark.parametrize("reason", ["model_unavailable", "provider_rate_limited", "policy_refused"])
def test_credential_copy_does_not_release_model_quota_or_policy_block(tmp_path, reason):
    auth, home, envelope = setup(tmp_path)
    auth.install(envelope); auth.attach(home)
    auth.block_model() if reason == "model_unavailable" else auth.block(reason)
    with pytest.raises(CodexFailure): auth.install(refreshed(envelope))
    with pytest.raises(CodexFailure): auth.check()
    auth.close()


def test_two_platform_owners_share_only_operator_credential_not_control_or_native_state(tmp_path):
    a, home_a, envelope_a = setup(tmp_path / "a")
    b, home_b, envelope_b = setup(tmp_path / "b")
    shared = {**envelope_b, "accessToken":envelope_a["accessToken"], "expiresAt":envelope_a["expiresAt"]}
    a.install(envelope_a); a.attach(home_a)
    b.install(shared); b.attach(home_b)
    assert a.owner != b.owner and a.state_id != b.state_id and a.journal.key != b.journal.key
    a.consume(str(uuid4()))
    assert len(a.state()["requests"]) == 1 and b.state()["requests"] == []
    before = (b.control / LEDGER).read_bytes()
    with pytest.raises(CodexFailure): b.install(envelope_a)
    assert (b.control / LEDGER).read_bytes() == before
    a.close(); b.close()


@pytest.mark.parametrize("damage", [{"version":1}, {"ownerId":str(uuid4())}, {"stateId":str(uuid4())},
                                    {"snapshotId":"invalid"}, {"maxDispatches":3}, {"windowId":str(uuid4())},
                                    {"refreshToken":"SYNTHETIC_REFRESH"}, {"accountId":"another-account"},
                                    {"expiresAt":True}])
def test_wrong_envelopes_never_create_service_ledger_or_native_auth(tmp_path, damage):
    auth, home, envelope = setup(tmp_path)
    with pytest.raises(CodexFailure): auth.install({**envelope, **damage})
    assert not (auth.control / LEDGER).exists() and not (home / "auth.json").exists()


def test_different_selected_provider_and_missing_renewed_snapshot_fail_closed(tmp_path):
    auth, home, envelope = setup(tmp_path)
    other = jwt(account="different-provider")
    with pytest.raises(CodexFailure): auth.install({**envelope, "accessToken":other, "accountId":"different-provider"})
    stale = jwt(expiry=int(time.time()) - 1)
    with pytest.raises(CodexFailure, match="codex_auth_expired"):
        auth.install({**envelope, "accessToken":stale, "expiresAt":int(time.time()) - 1})
    with pytest.raises(CodexFailure): auth.check()
    assert not (auth.control / LEDGER).exists() and not (home / "auth.json").exists()


def test_existing_trial_history_is_not_silently_adopted_or_erased(tmp_path):
    auth, home, envelope = setup(tmp_path)
    old = auth.control / "access-ledger.json"
    write_private(old, {"syntheticProtectedTrial":"retain"})
    before = old.read_bytes()
    with pytest.raises(CodexFailure): auth.install(envelope)
    assert old.read_bytes() == before and not (auth.control / LEDGER).exists()


def test_selected_source_service_export_is_read_only_access_only_and_account_bound(tmp_path):
    auth, _, envelope = setup(tmp_path)
    source = tmp_path / "selected.json"
    write_private(source, {"auth_mode":"chatgpt", "tokens": {"account_id":ACCOUNT,
                          "access_token":envelope["accessToken"], "id_token":"SYNTHETIC_ID", "refresh_token":"SYNTHETIC_REFRESH"}})
    before = source.read_bytes()
    inputs = {"ownerId":auth.owner, "stateId":auth.state_id, "snapshotId":str(uuid4()), "expectedAccountId":ACCOUNT}
    payload = service_envelope(selected_file(source), inputs)
    assert source.read_bytes() == before and payload["version"] == 2
    assert "SYNTHETIC_REFRESH" not in json.dumps(payload) and "SYNTHETIC_ID" not in json.dumps(payload)
    assert "maxDispatches" not in payload and "windowId" not in payload
    with pytest.raises(CodexFailure): service_envelope(selected_file(source), {**inputs, "expectedAccountId":"different"})


@pytest.mark.parametrize("field,value", [("requests",[{"id":"not-uuid"}]), ("deadline",True),
                                        ("blocked",{}), ("modelBlocked",1)])
def test_corrupted_service_control_refused(tmp_path, field, value):
    auth, _, envelope = setup(tmp_path)
    auth.install(envelope)
    ledger = read_private(auth.control / LEDGER); ledger[field] = value
    write_private(auth.control / LEDGER, ledger)
    with pytest.raises(CodexFailure): auth.check()


@pytest.mark.parametrize("environment,scope,expected", [("personal-test","personal-private","policy_refused"),
                                                      ("operator-service","operator-access-service","not_configured")])
def test_service_factory_is_explicit_and_no_native_starts_without_private_supply(tmp_path, monkeypatch, environment, scope, expected):
    auth, home, _ = setup(tmp_path)
    token, account_file = tmp_path / "transport-token", tmp_path / "operator-account"
    token.write_text("x" * 40); token.chmod(0o600)
    account_file.write_text(ACCOUNT); account_file.chmod(0o600)
    for key, value in {"RUNNER_ACCOUNT_ID":auth.owner, "RUNNER_STATE_ID":auth.state_id, "RUNNER_POD_UID":str(uuid4()),
                       "RUNNER_TOKEN_FILE":str(token), "RUNNER_STATE_DIR":str(home.parent), "CODEX_MODEL":"gpt-6.1-sol",
                       "CODEX_AUTH_MODE":"access-service", "CODEX_ACCESS_CONTROL_DIR":str(auth.control),
                       "CODEX_OPERATOR_ACCOUNT_FILE":str(account_file), "CODEX_EXECUTION_SCOPE":scope,
                       "APP_ENV":environment, "RESEARCH_TOOL_CALLBACK_URL":"http://callback.invalid/tools"}.items():
        monkeypatch.setenv(key, value)
    with TestClient(create_app()) as client:
        headers = {"Authorization":"Bearer " + "x" * 40, "X-Runner-State-Id":auth.state_id}
        response = client.get("/health", headers=headers)
        assert response.status_code == 200 and response.json()["reason"] == expected
        assert response.json()["available"] is False and client.app.state.native is None
        assert "accessExpiresAt" not in response.json() and "remainingAdmissions" not in response.json()
    assert not (auth.control / LEDGER).exists() and not (home / "auth.json").exists()
