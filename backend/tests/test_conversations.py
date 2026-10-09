import hashlib
import json
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import create_app
from app.migrate import migrate
from tests.test_research import identity, headers


@pytest.fixture(scope="module", autouse=True)
def conversation_schema(schema):
    migrate("0003_sessions")


class FixtureRunner:
    """Persistent test double; never a claim of native/provider resume success."""
    def __init__(self, app, owner, root):
        self.app, self.owner, self.root = app, owner, root
        self.dispatches, self.last = 0, None
        app.state.codex_verification = "fixture"

    def health(self):
        return {"model": "gpt-6.1-sol", "available": True, "reason": None, "verification": "fixture"}

    def submit(self, identity, body):
        self.dispatches += 1
        self.last = body
        record_file = self.root / (str(identity) + ".json")
        previous = json.loads(record_file.read_text()) if record_file.exists() else {"record": {"turns": []}}
        arguments = {"mutationId": str(uuid.uuid4()), "title": "Saved by tool", "content": "Complete [link](https://example.com)"}
        with TestClient(self.app, base_url=self.app.state.settings.origin, raise_server_exceptions=False) as tool_client:
            result = tool_client.post("/api/internal/research/tools", json={"sessionId": str(identity), "requestId": body["requestId"],
                         "name": "research_document_save", "arguments": arguments}, headers={"Authorization": "Bearer " + body["toolToken"]})
            assert result.status_code == 200, result.text
        turn = {"id": str(uuid.uuid4()), "items": [{"id": "user-" + body["requestId"], "type": "userMessage", "content": [{"type": "text", "text": body["text"]}]},
                {"id": "tool-" + body["requestId"], "type": "dynamicToolCall", "tool": "research_document_save", "arguments": arguments,
                 "contentItems": [{"type": "inputText", "text": json.dumps(result.json())}], "status": "completed"},
                {"id": "answer-" + body["requestId"], "type": "agentMessage", "text": "Fixture only: stored the document."}]}
        record = {"ownerId": str(self.owner), "requestId": body["requestId"], "turnId": turn["id"], "state": "idle",
                  "record": {"turns": previous["record"]["turns"] + [turn]}}
        record_file.write_text(json.dumps(record))
        return record

    def read(self, identity):
        return json.loads((self.root / (str(identity) + ".json")).read_text())


def create_session(client, auth):
    result = client.post("/api/research/sessions", json={"title": "Personal fixture"}, headers=headers(auth))
    assert result.status_code == 201, result.text
    return result.json()


def test_session_owner_unavailable_and_archive_preserves_cache(client, admin):
    _, auth = identity(client, admin)
    row = create_session(client, auth)
    root = "/api/research/sessions/" + row["id"]
    assert client.post(root + "/messages", json={"text": "hello", "expectedVersion": 1}, headers=headers(auth)).json()["error"]["code"] == "codex_unavailable"
    assert client.get(root).json()["state"] == "idle"
    assert client.get("/api/research/sessions").json()["items"][0]["id"] == row["id"]
    _, other_auth = identity(client, admin, "222222")
    for path in (root, root + "/items/guess"):
        assert client.get(path).status_code == 404
    assert client.post(root + "/messages", json={"text": "other", "expectedVersion": 1}, headers=headers(other_auth)).status_code == 404
    assert client.get("/api/research/sessions").json()["items"] == []


def test_fixture_tools_full_history_resume_reconnect_and_retry_without_dispatch(client, admin, tmp_path):
    owner, auth = identity(client, admin)
    runner = FixtureRunner(client.app, owner, tmp_path)
    client.app.state.research_runners[owner] = runner
    client.app.state.codex_verification = "fixture"
    row = create_session(client, auth)
    root = "/api/research/sessions/" + row["id"]
    key = str(uuid.uuid4())
    original_body = {"text": "Save a private draft", "expectedVersion": 1}
    sent = client.post(root + "/messages", json=original_body, headers=headers(auth, key))
    assert sent.status_code == 202
    detail = client.get(root).json()
    assert detail["state"] == "idle" and detail["version"] == 3
    assert len([item for item in detail["items"] if item.get("role") == "user"]) == 1
    tool = next(item for item in detail["items"] if item["type"] == "tool_call")
    assert tool["input"]["content"] == "Complete [link](https://example.com)"
    assert "Saved by tool" in json.dumps(tool["output"])
    raw = client.get(root + "/items/" + tool["id"]).json()
    assert raw["format"] == "json" and raw["raw"]["arguments"] == tool["input"]
    retry = client.post(root + "/messages", json=original_body, headers=headers(auth, key))
    assert retry.status_code == 202 and retry.json() == sent.json() and runner.dispatches == 1
    assert client.post(root + "/messages", json={"text": "other", "expectedVersion": detail["version"]}, headers=headers(auth, key)).status_code == 409
    cookies = dict(client.cookies)
    with TestClient(create_app(), base_url=auth["Origin"], cookies=cookies, raise_server_exceptions=False) as reconnected:
        restarted_runner = FixtureRunner(reconnected.app, owner, tmp_path)
        reconnected.app.state.research_runners[owner] = restarted_runner
        assert reconnected.get(root).json()["items"] == detail["items"]
        assert reconnected.post(root + "/messages", json=original_body, headers=headers(auth, key)).status_code == 202
        assert restarted_runner.dispatches == 0
        continued = reconnected.post(root + "/messages", json={"text": "Use my own materials next", "expectedVersion": detail["version"]}, headers=headers(auth))
        assert continued.status_code == 202
        final = reconnected.get(root).json()
        assert len([item for item in final["items"] if item["type"] == "tool_call"]) == 2
        assert reconnected.request("DELETE", root, json={"expectedVersion": final["version"]}, headers=headers(auth)).status_code == 200
        assert reconnected.get(root).status_code == 404
    with admin.connect() as db:
        saved = db.execute(text("SELECT archived,native_record FROM research.conversations WHERE id=:id"), {"id": row["id"]}).one()
        assert saved.archived is True and len(saved.native_record["turns"]) == 2
    assert (tmp_path / (row["id"] + ".json")).exists()


def test_tool_token_binds_owner_session_turn_live_login_and_current_authority(client, admin, tmp_path):
    owner, auth = identity(client, admin)
    first = create_session(client, auth)
    second = create_session(client, auth)
    secret = uuid.uuid4().hex + uuid.uuid4().hex
    request_id = uuid.uuid4()
    from app.main import session_digest
    login_hash = session_digest(client.cookies.get("anxious_session"), auth["Origin"])
    with admin.begin() as db:
        db.execute(text("UPDATE research.conversations SET state='running',request_id=:request,tool_token_hash=:token,tool_expires_at=now()+interval '1 minute',login_hash=:login WHERE id=:id"),
                    {"id": first["id"], "request": request_id, "token": hashlib.sha256(secret.encode()).hexdigest(), "login": login_hash})
    body = {"sessionId": first["id"], "requestId": str(request_id), "name": "research_list", "arguments": {"type": "material"}}
    bearer = {"Authorization": "Bearer " + secret}
    assert client.post("/api/internal/research/tools", json=body).status_code == 401
    assert client.post("/api/internal/research/tools", json=body, headers=bearer).status_code == 200
    assert client.post("/api/internal/research/tools", json={**body, "sessionId": second["id"]}, headers=bearer).status_code == 404
    assert client.post("/api/internal/research/tools", json={**body, "requestId": str(uuid.uuid4())}, headers=bearer).status_code == 404
    _, other_auth = identity(client, admin, "222222")
    other = create_session(client, other_auth)
    assert client.post("/api/internal/research/tools", json={**body, "sessionId": other["id"]}, headers=bearer).status_code == 404
    assert client.post("/api/internal/research/tools", json={**body, "arguments": {"type": "material", "ownerId": str(owner)}}, headers=bearer).status_code == 422
    with admin.begin() as db:
        db.execute(text("UPDATE auth.accounts SET is_approved=false WHERE id=:owner"), {"owner": owner})
    assert client.post("/api/internal/research/tools", json=body, headers=bearer).status_code == 403
    with admin.begin() as db:
        db.execute(text("UPDATE auth.accounts SET is_approved=true WHERE id=:owner"), {"owner": owner})
        db.execute(text("DELETE FROM auth.sessions WHERE account_id=:owner"), {"owner": owner})
    assert client.post("/api/internal/research/tools", json=body, headers=bearer).status_code == 403


class RejectedFixtureRunner:
    health = FixtureRunner.health
    def submit(self, identity, body):
        request = httpx.Request("POST", "http://fixture.invalid/turn")
        raise httpx.HTTPStatusError("not accepted", request=request, response=httpx.Response(409, request=request))

    def read(self, identity):
        raise httpx.ConnectError("fixture only")


class UnrecordedFixtureRunner:
    health = FixtureRunner.health
    def submit(self, identity, body):
        self.request = body["requestId"]

    def read(self, identity):
        return {"requestId": self.request, "state": "failed", "turnId": None, "record": {"turns": []}}


def test_definite_rejection_revokes_tool_grant_and_allows_next_explicit_input(client, admin):
    owner, auth = identity(client, admin)
    client.app.state.research_runners[owner] = RejectedFixtureRunner()
    client.app.state.codex_verification = "fixture"
    row = create_session(client, auth)
    root = "/api/research/sessions/" + row["id"]
    sent = client.post(root + "/messages", json={"text": "first rejection", "expectedVersion": 1}, headers=headers(auth))
    assert sent.status_code == 202
    detail = client.get(root).json()
    assert detail["state"] == "failed" and detail["error"]["code"] == "codex_rejected"
    with admin.connect() as db:
        assert db.execute(text("SELECT tool_token_hash FROM research.conversations WHERE id=:id"), {"id": row["id"]}).scalar_one() is None
    again = client.post(root + "/messages", json={"text": "next explicit input", "expectedVersion": detail["version"]}, headers=headers(auth))
    assert again.status_code == 202
    messages = client.get(root).json()["items"]
    assert [entry["text"] for entry in messages] == ["first rejection", "next explicit input"]
    assert all(entry["status"] == "not_recorded" for entry in messages)


def test_unrecorded_failures_survive_followup_refresh_and_api_reconnect(client, admin):
    owner, auth = identity(client, admin)
    client.app.state.research_runners[owner] = UnrecordedFixtureRunner()
    client.app.state.codex_verification = "fixture"
    row = create_session(client, auth)
    root = "/api/research/sessions/" + row["id"]
    version = 1
    for message in ("first unsent", "second unsent", "third unsent"):
        assert client.post(root + "/messages", json={"text": message, "expectedVersion": version}, headers=headers(auth)).status_code == 202
        detail = client.get(root).json()
        version = detail["version"]
    assert [entry["text"] for entry in detail["items"]] == ["first unsent", "second unsent", "third unsent"]
    first = detail["items"][0]
    assert client.get(root + "/items/" + first["id"]).json()["raw"]["source"] == "platform"
    with TestClient(create_app(), base_url=auth["Origin"], cookies=dict(client.cookies)) as reconnected:
        assert reconnected.get(root).json()["items"] == detail["items"]


def test_ambiguous_dispatch_failure_does_not_auto_retry_or_release_active_turn(client, admin):
    class Ambiguous(UnrecordedFixtureRunner):
        count = 0
        def submit(self, identity, body):
            self.count += 1
            raise httpx.ConnectError("ambiguous fixture result")
        def read(self, identity):
            raise httpx.ConnectError("fixture offline")
    owner, auth = identity(client, admin)
    runner = Ambiguous()
    client.app.state.research_runners[owner] = runner
    client.app.state.codex_verification = "fixture"
    row = create_session(client, auth)
    root = "/api/research/sessions/" + row["id"]
    body, key = {"text": "maybe accepted", "expectedVersion": 1}, str(uuid.uuid4())
    assert client.post(root + "/messages", json=body, headers=headers(auth, key)).status_code == 202
    current = client.get(root).json()
    assert current["state"] == "running"
    assert client.post(root + "/messages", json=body, headers=headers(auth, key)).status_code == 202
    assert runner.count == 1
    assert client.post(root + "/messages", json={"text": "new", "expectedVersion": current["version"]}, headers=headers(auth)).status_code == 409
