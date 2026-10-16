"""Two live adapter processes using offline native RPC fixture, no provider."""
import json
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from app.conversations import Runner, runners_from_file
from app.migrate import migrate
from app.research_tools import definitions
from runner.app import Native, Turn
from runner.binding import initialize_binding
from tests.test_conversations import create_session
from tests.test_research import identity, headers
from tests.test_runner_binding import pod_app
from tests.test_runner_protocol import wait_idle

HTTP_CLIENT = httpx.Client


@pytest.fixture(scope="module", autouse=True)
def runner_schema(schema):
    migrate("0004_publication")


def test_same_native_thread_owner_pages_then_public_and_legacy_refuse(tmp_path, monkeypatch, caplog):
    caplog.set_level("INFO", logger="uvicorn.error")
    calls = []
    def callback(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"saved": True})
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: HTTP_CLIENT(transport=httpx.MockTransport(callback), **kwargs))
    native = Native(tmp_path / "native", "gpt-6.1-sol", "/usr/local/bin/codex-fixture", "http://callback.invalid/tools")
    session = str(uuid.uuid4())
    try:
        first = Turn(requestId=uuid.uuid4(), text="create draft", tools=definitions(), toolToken="x" * 40,
                     scope={"page": {"kind": "document_workspace"}})
        native.accept(session, first)
        saved = wait_idle(native, session)
        assert len(calls) == 1 and saved["record"]["turns"][0]["items"][1]["status"] == "completed"
        second = Turn(requestId=uuid.uuid4(), text="general question", tools=definitions(), toolToken="y" * 40,
                      expectedThreadId=saved["threadId"], scope={"page": {"kind": "general"}})
        native.accept(session, second)
        denied = wait_idle(native, session)
        assert denied["threadId"] == saved["threadId"] and len(denied["record"]["turns"]) == 2
        assert len(calls) == 2  # General and detail pages share owner authority.
        tool = denied["record"]["turns"][-1]["items"][1]
        assert tool["status"] == "completed"
        prompt = denied["record"]["turns"][-1]["items"][0]["content"][0]["text"]
        assert 'allowedTools' not in prompt and "general question" in prompt and "Page hint" in prompt
        native.accept(session, second)
        assert len(calls) == 2
        with pytest.raises(RuntimeError):
            native.accept(session, second.model_copy(update={"scope": {"page": {"kind": "document_workspace"}}}))
        stored = json.loads((tmp_path / "native/codex/fixture-native-record.json").read_text())
        assert len(stored[saved["threadId"]]["tools"]) == 13
        trace = json.loads((tmp_path / "native/codex/fixture-model-rpc.json").read_text())
        assert [entry["method"] for entry in trace] == ["thread/start", "turn/start", "thread/resume", "turn/start"]
        owner_pid = native.process.pid
        assert native.thread_web_modes[saved["threadId"]] == "live"
        native.accept(session, Turn(requestId=uuid.uuid4(), text="public question", tools=definitions(), toolToken="p" * 40,
                      scope={"page": {"kind": "public_document", "resourceId": str(uuid.uuid4())}}))
        public = wait_idle(native, session)
        assert native.process.pid != owner_pid and public["threadId"] == saved["threadId"]
        assert native.thread_web_modes[saved["threadId"]] == "disabled"
        public_pid = native.process.pid
        assert len(calls) == 2
        assert public["record"]["turns"][-1]["items"][1]["status"] == "failed"
        # Legacy omitted scope fails closed too, regardless of registered tools/context.
        native.accept(session, Turn(requestId=uuid.uuid4(), text="legacy caller", tools=definitions(), toolToken="z" * 40))
        assert wait_idle(native, session)["state"] == "idle" and len(calls) == 2
        assert native.process.pid == public_pid
        native.accept(session, Turn(requestId=uuid.uuid4(), text="owner again", tools=definitions(), toolToken="w" * 40,
                      scope={"page": {"kind": "general"}}))
        restored = wait_idle(native, session)
        assert native.process.pid != public_pid and restored["threadId"] == saved["threadId"]
        assert len(calls) == 3 and len(restored["record"]["turns"]) == 5
        assert native.thread_web_modes[saved["threadId"]] == "live"
        metrics = [json.loads(record.message.removeprefix("runner_turn_timing "))
                   for record in caplog.records if record.message.startswith("runner_turn_timing ")]
        assert len(metrics) == 5
        assert {item["requestId"] for item in metrics} >= {str(first.requestId), str(second.requestId)}
        for item in metrics:
            assert item["state"] == "idle" and item["toolCalls"] == 1
            assert all(item[key] >= 0 for key in ("queueMs", "threadMs", "dispatchMs", "nativeMs", "toolMs", "historyMs", "totalMs"))
            assert item["totalMs"] >= item["nativeMs"] >= item["toolMs"]
        assert first.toolToken not in caplog.text and "create draft" not in caplog.text
    finally:
        native.close()


def test_two_users_dedicated_transport_native_process_history_and_tool_owner(client, admin, tmp_path, monkeypatch):
    owner_a, auth_a = identity(client, admin)
    session_a = create_session(client, auth_a)
    cookies_a = dict(client.cookies)
    owner_b, auth_b = identity(client, admin, "222222")
    session_b = create_session(client, auth_b)
    cookies_b = dict(client.cookies)
    applications = []
    for label, owner in (("a", owner_a), ("b", owner_b)):
        root, state, token = tmp_path / label, str(uuid.uuid4()), tmp_path / (label + "-token")
        initialize_binding(root, str(owner), state)
        token.write_text(label * 40)
        applications.append((pod_app(monkeypatch, root, str(owner), state, token, str(uuid.uuid4())), state))
    with TestClient(applications[0][0]) as runner_a, TestClient(applications[1][0]) as runner_b:
        servers = {"runner-a": runner_a, "runner-b": runner_b}
        callbacks = []
        def route(request):
            data = json.loads(request.content) if request.content else None
            if request.url.host == "callback.invalid":
                callbacks.append(data)
                result = client.post("/api/internal/research/tools", json=data, headers=dict(request.headers))
            else:
                result = servers[request.url.host].request(request.method, request.url.path, json=data, headers=dict(request.headers))
            return httpx.Response(result.status_code, json=result.json())
        monkeypatch.setattr(httpx, "Client", lambda **kw: HTTP_CLIENT(transport=httpx.MockTransport(route), **kw))
        client.app.state.codex_verification = "fixture"
        client.app.state.research_runners = {
            owner_a: Runner("http://runner-a", "a" * 40, owner_a, applications[0][1]),
            owner_b: Runner("http://runner-b", "b" * 40, owner_b, applications[1][1]),
        }
        assert runner_a.app.state.native.process.pid != runner_b.app.state.native.process.pid
        details = []
        for cookies, auth, session, adapter in ((cookies_a, auth_a, session_a, runner_a), (cookies_b, auth_b, session_b, runner_b)):
            client.cookies.clear()
            client.cookies.update(cookies)
            sent = client.post("/api/research/sessions/" + session["id"] + "/messages", json={"text": "my private draft", "expectedVersion": 1,
                "page": {"kind": "document_workspace"}}, headers=headers(auth))
            assert sent.status_code == 202, sent.text
            assert wait_idle(adapter.app.state.native, session["id"])["state"] == "idle"
            detail = client.get("/api/research/sessions/" + session["id"]).json()
            assert detail["state"] == "idle" and next(item for item in detail["items"] if item["type"] == "tool_call")["status"] == "completed"
            assert detail["items"][0]["text"] == "my private draft"
            docs = client.get("/api/research/documents").json()["items"]
            assert len(docs) == 1
            details.append((detail, docs))
        assert len(callbacks) == 2 and {item["sessionId"] for item in callbacks} == {session_a["id"], session_b["id"]}
        assert details[0][1][0]["id"] != details[1][1][0]["id"]
        assert runner_a.app.state.native.read(session_b["id"]) is None and runner_b.app.state.native.read(session_a["id"]) is None
        assert client.get("/api/research/sessions/" + session_a["id"]).status_code == 404
        assert client.get("/api/research/documents/" + details[0][1][0]["id"]).status_code == 404
        with pytest.raises(httpx.HTTPStatusError):
            Runner("http://runner-b", "a" * 40, owner_a, applications[0][1]).health()
        with pytest.raises(ValueError):
            Runner("http://runner-b", "b" * 40, owner_a, applications[1][1]).health()
        client.cookies.clear()
        client.cookies.update(cookies_a)
        general = client.post("/api/research/sessions/" + session_a["id"] + "/messages", json={"text": "general followup", "expectedVersion": details[0][0]["version"],
            "page": {"kind": "general"}}, headers=headers(auth_a))
        assert general.status_code == 202
        assert wait_idle(runner_a.app.state.native, session_a["id"])["state"] == "idle"
        continued = client.get("/api/research/sessions/" + session_a["id"]).json()
        refused = [item for item in continued["items"] if item["type"] == "tool_call"][-1]
        assert refused["status"] == "completed"
        assert [item["page"]["kind"] for item in continued["items"] if item.get("role") == "user"] == ["document_workspace", "general"]
        assert len(callbacks) == 3 and len(client.get("/api/research/documents").json()["items"]) == 2
        # A third, unmapped editor never borrows either runner.
        client.cookies.clear()
        _, auth_c = identity(client, admin, "333333")
        session_c = create_session(client, auth_c)
        assert client.get("/api/research/codex/status").json()["reason"] == "not_configured"
        assert client.post("/api/research/sessions/" + session_c["id"] + "/messages", json={"text": "hello", "expectedVersion": 1}, headers=headers(auth_c)).status_code == 503
        assert len(callbacks) == 3


@pytest.mark.parametrize("damage", ["state", "secret", "url-default-port", "url-case", "owner-alias", "duplicate-key"])
def test_mapping_rejects_shared_or_ambiguous_bindings(tmp_path, monkeypatch, damage):
    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    state = str(uuid.uuid4())
    tokens = [tmp_path / "a", tmp_path / "b"]
    tokens[0].write_text("a" * 40)
    tokens[1].write_text("b" * 40)
    entries = {first: {"url": "http://runner-a", "tokenFile": str(tokens[0]), "stateId": state},
               second: {"url": "http://runner-b", "tokenFile": str(tokens[1]), "stateId": str(uuid.uuid4())}}
    if damage == "state": entries[second]["stateId"] = state
    elif damage == "secret": entries[second]["tokenFile"] = str(tokens[0])
    elif damage == "url-default-port": entries[second]["url"] = "http://runner-a:80"
    elif damage == "url-case": entries[second]["url"] = "http://RUNNER-A"
    elif damage == "owner-alias": entries[first.replace("-", "")] = entries.pop(second)
    raw = json.dumps(entries)
    if damage == "duplicate-key": raw = '{"' + first + '":' + json.dumps(entries[first]) + ',"' + first + '":' + json.dumps(entries[second]) + '}'
    mapping = tmp_path / "mapping.json"
    mapping.write_text(raw)
    monkeypatch.setenv("CODEX_RUNNERS_FILE", str(mapping))
    with pytest.raises(ValueError): runners_from_file()
