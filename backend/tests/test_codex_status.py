"""Actual API/DB gates with synthetic runner health/error metadata."""
import json

import pytest

from app.migrate import migrate
from tests.test_research import identity, headers
from tests.test_conversations import create_session


@pytest.fixture(scope="module", autouse=True)
def sessions_schema(schema):
    migrate("0003_sessions")


class HealthRunner:
    def __init__(self, reason=None):
        self.reason, self.model, self.dispatches = reason, "gpt-6.1-sol", 0

    def health(self):
        return {"available": self.reason is None, "reason": self.reason, "model": self.model, "verification": "unverified",
                "privateMetadata": "synthetic-provider-identity-and-secret-sentinel"}

    def submit(self, identity, body):
        self.dispatches += 1
        self.request = body["requestId"]

    def read(self, identity):
        return {"state": "failed", "requestId": self.request, "turnId": None, "errorCode": "codex_model_unavailable",
                "record": {"turns": []}, "privateMetadata": "synthetic-provider-identity-and-secret-sentinel"}


def enable(client, owner, runner):
    client.app.state.codex_personal_owner = owner
    client.app.state.codex_personal_enabled = True
    client.app.state.research_runners[owner] = runner


def test_private_status_is_owner_scoped_and_never_exposes_auth_metadata(client, admin):
    owner, auth = identity(client, admin)
    enable(client, owner, HealthRunner())
    result = client.get("/api/research/codex/status")
    assert result.json() == {"available": True, "reason": None, "model": "gpt-6.1-sol", "verification": "unverified"}
    assert result.headers["cache-control"] == "no-store"
    _, other_auth = identity(client, admin, "222222")
    assert client.get("/api/research/codex/status").json()["reason"] == "not_enabled_for_account"
    session = create_session(client, other_auth)
    response = client.post("/api/research/sessions/" + session["id"] + "/messages", json={"text": "Not enabled", "expectedVersion": 1}, headers=headers(other_auth))
    assert response.status_code == 503 and response.json()["error"]["code"] == "codex_not_enabled_for_account"


@pytest.mark.parametrize("reason,code", [("auth_expired", "codex_auth_expired"), ("auth_revoked", "codex_auth_revoked"),
    ("model_unavailable", "codex_model_unavailable"), ("policy_refused", "codex_policy_refused"), ("budget_exhausted", "codex_budget_exhausted")])
def test_preflight_failures_do_not_reserve_key_and_polling_failure_retains_input(client, admin, reason, code):
    owner, auth = identity(client, admin)
    runner = HealthRunner(reason)
    enable(client, owner, runner)
    session = create_session(client, auth)
    path = "/api/research/sessions/" + session["id"]
    body, retry_headers = {"text": "Synthetic retained input", "expectedVersion": 1}, headers(auth)
    response = client.post(path + "/messages", json=body, headers=retry_headers)
    assert response.status_code == 503 and response.json()["error"]["code"] == code
    assert runner.dispatches == 0 and client.get(path).json()["version"] == 1
    assert "synthetic-provider-identity" not in json.dumps(response.json())
    runner.reason = None
    accepted = client.post(path + "/messages", json=body, headers=retry_headers)
    assert accepted.status_code == 202
    detail = client.get(path).json()
    assert detail["error"]["code"] == "codex_model_unavailable" and detail["items"][0]["status"] == "not_recorded"
    assert "synthetic-provider-identity" not in json.dumps(detail)
    assert client.post(path + "/messages", json=body, headers=retry_headers).json() == accepted.json()
    assert runner.dispatches == 1


def test_model_response_mismatch_and_hosted_gate_never_dispatch(client, admin):
    owner, auth = identity(client, admin)
    runner = HealthRunner()
    runner.model = "unexpected-model"
    enable(client, owner, runner)
    assert client.get("/api/research/codex/status").json()["reason"] == "model_unavailable"
    runner.model = "gpt-6.1-sol"
    client.app.state.codex_personal_enabled = False
    assert client.get("/api/research/codex/status").json()["reason"] == "policy_refused"
    client.app.state.codex_personal_enabled = True
    session = create_session(client, auth)
    assert client.post("/api/research/sessions/" + session["id"] + "/messages", json={"text": "Cannot select another model", "expectedVersion": 1, "model": "other"}, headers=headers(auth)).status_code == 422
    assert runner.dispatches == 0


@pytest.mark.parametrize("field,value", [("reason", []), ("verification", {}), ("available", "true")])
def test_invalid_runner_status_is_unavailable_instead_of_server_error(client, admin, field, value):
    owner, _ = identity(client, admin)
    runner = HealthRunner()
    valid = runner.health()
    runner.health = lambda: {**valid, field: value}
    enable(client, owner, runner)
    result = client.get("/api/research/codex/status")
    assert result.status_code == 200 and result.json()["reason"] == "unavailable"
