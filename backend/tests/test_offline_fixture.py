import json
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.offline_fixture_setup import configure
from tests.offline_services import require_offline, create_api, create_runner
from tests.test_research import identity


def test_fixture_requires_explicit_test_environment_and_rejects_model_inputs(monkeypatch):
    monkeypatch.setenv("OFFLINE_RUNNER_FIXTURE", "true")
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(RuntimeError):
        require_offline()
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("OPENAI_API_KEY_FILE", "/never-read-provider-secret")
    with pytest.raises(RuntimeError):
        require_offline()
    monkeypatch.delenv("OPENAI_API_KEY_FILE")
    require_offline()
    monkeypatch.setenv("CODEX_BIN", "/usr/local/bin/codex")
    monkeypatch.setenv("CODEX_MODEL", "gpt-6.1-sol")
    with pytest.raises(RuntimeError):
        create_runner()


def test_offline_mapping_uses_only_distinct_approved_accounts_and_private_outputs(client, admin, tmp_path, monkeypatch):
    monkeypatch.setenv("OFFLINE_RUNNER_FIXTURE", "true")
    first, _ = identity(client, admin)
    second, _ = identity(client, admin, "222222", approved=False)
    root = tmp_path / "mapping"
    with pytest.raises(ValueError):
        configure(os.environ["DATABASE_URL"], root, first, second)
    assert not root.exists()
    with admin.begin() as db:
        db.execute(text("UPDATE auth.accounts SET is_approved=true WHERE id=:id"), {"id": second})
    configure(os.environ["DATABASE_URL"], root, first, second)
    mapping = json.loads((root / "runners.json").read_text())
    assert set(mapping) == {str(first), str(second)}
    assert (root / "runner-a.token").read_text() != (root / "runner-b.token").read_text()
    assert root.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in root.iterdir())
    with pytest.raises(ValueError):
        configure(os.environ["DATABASE_URL"], root, first, second)


def test_fixture_api_marks_mock_without_adding_control_routes(admin, monkeypatch):
    monkeypatch.setenv("OFFLINE_RUNNER_FIXTURE", "true")
    with TestClient(create_api(), base_url=os.environ["AUTH_ORIGIN"]) as client:
        assert client.app.state.codex_verification == "fixture"
        assert client.get("/__mock/offline").status_code == 404


def test_offline_runner_rejects_intake_before_native_work_and_authenticates(tmp_path, monkeypatch):
    monkeypatch.setenv("OFFLINE_RUNNER_FIXTURE", "true")
    monkeypatch.setenv("CODEX_BIN", "/usr/local/bin/codex-fixture")
    monkeypatch.setenv("CODEX_MODEL", "gpt-6.1-sol")
    monkeypatch.setenv("RUNNER_ACCOUNT_ID", str(uuid.uuid4()))
    monkeypatch.setenv("RUNNER_STATE_DIR", str(tmp_path / "home"))
    monkeypatch.setenv("RESEARCH_TOOL_CALLBACK_URL", "http://never-called.invalid/tools")
    token = tmp_path / "token"
    token.write_text("x" * 40)
    monkeypatch.setenv("RUNNER_TOKEN_FILE", str(token))
    session = uuid.uuid4()
    body = {"requestId": str(uuid.uuid4()), "text": "/fixture/reject", "tools": [], "toolToken": "t" * 40}
    with TestClient(create_runner()) as client:
        assert client.post(f"/sessions/{session}/turn", json=body).status_code == 401
        assert client.post(f"/sessions/{session}/turn", json=body, headers={"Authorization": "Bearer " + "x" * 40}).status_code == 409
        assert client.app.state.native.mapping == {}
