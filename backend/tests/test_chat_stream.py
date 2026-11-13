"""#90 API stream endpoints against PostgreSQL (review's DB suite; back cannot run these). The runner is a fixture
object and an httpx.MockTransport; nothing here is a claim about native Codex streaming."""
import json
import uuid

import httpx
import pytest
from sqlalchemy import text

from app import chat_stream
from tests.test_conversations import conversation_schema, create_session  # noqa: F401 (module schema fixture)
from tests.test_research import headers, identity


class StreamRunner:
    """Keeps the turn running (submit records nothing) and answers stream/interrupt as a runner would."""

    def __init__(self, owner):
        self.owner, self.request, self.interrupts = str(owner), None, []
        self.record = {"turns": []}
        self.state = "running"

    def health(self):
        return {"model": "gpt-6.1-sol", "available": True, "reason": None, "verification": "fixture"}

    def submit(self, identity, body):
        self.request = body["requestId"]

    def read(self, identity):
        return {"ownerId": self.owner, "requestId": self.request, "turnId": "turn-1" if self.record["turns"] else None,
                "state": self.state, "record": self.record}

    def interrupt(self, identity, request_id):
        self.interrupts.append((str(identity), str(request_id)))
        return {"ownerId": self.owner, "requestId": str(request_id), "state": "interrupting"}

    def stream_target(self, identity):
        return {"url": "http://runner.invalid/sessions/" + str(identity) + "/events", "headers": {},
                "ownerId": self.owner, "stateId": None, "podUid": None}


def sse(kind, data, event_id=None):
    return chat_stream.frame(kind, data, event_id)


@pytest.fixture
def stream_on(monkeypatch):
    monkeypatch.setenv("RESEARCH_STREAM_ENABLE", "true")
    # Bounded test runs: the relay gives up quickly once the fixture runner stops answering.
    monkeypatch.setattr(chat_stream, "NOT_READY_LIMIT_SECONDS", 0.5)
    monkeypatch.setattr(chat_stream, "STATUS_RETRY_SECONDS", 0.05)
    monkeypatch.setattr(chat_stream, "HELLO_SECONDS", 0.5)


def running_session(client, admin, number="311111"):
    owner, auth = identity(client, admin, number)
    runner = StreamRunner(owner)
    client.app.state.research_runners[owner] = runner
    client.app.state.codex_verification = "fixture"
    row = create_session(client, auth)
    root = "/api/research/sessions/" + row["id"]
    sent = client.post(root + "/messages", json={"text": "stream me", "expectedVersion": 1}, headers=headers(auth))
    assert sent.status_code == 202
    assert sent.json()["requestId"] == runner.request  # #90: the Stop target from the 202 on
    return owner, auth, runner, row, root


def test_flag_off_hides_both_endpoints(client, admin, monkeypatch):
    monkeypatch.delenv("RESEARCH_STREAM_ENABLE", raising=False)
    owner, auth, runner, row, root = running_session(client, admin)
    assert client.get(root + "/events").json()["error"]["code"] == "stream_disabled"
    reply = client.post(root + "/interrupt", json={"requestId": runner.request}, headers=auth)
    assert (reply.status_code, reply.json()["error"]["code"]) == (404, "stream_disabled")
    assert runner.interrupts == []


def test_interrupt_checks_csrf_turn_and_writes_nothing(client, admin, stream_on):
    owner, auth, runner, row, root = running_session(client, admin)
    with admin.connect() as db:
        before = db.execute(text("SELECT version, state, request_id FROM research.conversations WHERE id=:id"),
                            {"id": row["id"]}).one()
    assert client.post(root + "/interrupt", json={"requestId": runner.request}).status_code == 403
    assert client.post(root + "/interrupt", json={"requestId": runner.request},
                       headers={**auth, "X-CSRF-Token": "wrong"}).status_code == 403
    assert client.post(root + "/interrupt", json={"requestId": str(uuid.uuid4())}, headers=auth).status_code == 409
    assert client.post(root + "/interrupt", json={"requestId": runner.request.upper()}, headers=auth).status_code == 422
    assert client.post(root + "/interrupt", json={"requestId": runner.request, "x": 1}, headers=auth).status_code == 422
    reply = client.post(root + "/interrupt", json={"requestId": runner.request}, headers=auth)
    assert reply.status_code == 200 and reply.json() == {"requestId": runner.request, "state": "interrupting"}
    assert runner.interrupts == [(row["id"], runner.request)]
    with admin.connect() as db:
        after = db.execute(text("SELECT version, state, request_id FROM research.conversations WHERE id=:id"),
                           {"id": row["id"]}).one()
    assert tuple(after) == tuple(before)
    _, other = identity(client, admin, "322222")
    assert client.post(root + "/interrupt", json={"requestId": runner.request}, headers=other).status_code == 404


def test_events_relay_bound_turn_only_then_end(client, admin, stream_on):
    owner, auth, runner, row, root = running_session(client, admin)
    other = str(uuid.uuid4())
    body = (sse("hello", {"ownerId": str(owner), "stateId": None, "sessionId": row["id"],
                          "activeRequestId": runner.request, "lastRequestId": None})
            + sse("snapshot.begin", {"requestId": runner.request, "turnState": "running", "seq": 1})
            + sse("snapshot.end", {"requestId": runner.request, "seq": 1}, runner.request + ":1")
            + sse("item.delta", {"requestId": other, "itemId": "x", "text": "foreign"}, other + ":2")
            + sse("item.delta", {"requestId": runner.request, "itemId": "m", "text": "mine"}, runner.request + ":2"))
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=httpx.ByteStream(body))  # a raw, not pre-read, body (review d319fcb)
        return httpx.Response(503)
    client.app.state.chat_stream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    def stop_runner(identity):
        raise ValueError("fixture runner gone")
    targets = [runner.stream_target]
    runner.stream_target = lambda identity: targets.pop(0)(identity) if targets else stop_runner(identity)
    with client.stream("GET", root + "/events") as reply:
        assert reply.status_code == 200 and reply.headers["content-type"].startswith("text/event-stream")
        assert reply.headers["cache-control"] == "no-store"
        data = b"".join(reply.iter_bytes()).decode()
    assert data.index("event: runner") < data.index("event: snapshot.begin")
    assert '"text":"mine"' in data and "foreign" not in data
    assert "event: hello" not in data  # the runner's hello is checked, never forwarded


def test_events_auth_ownership_and_cursor(client, admin, stream_on):
    owner, auth, runner, row, root = running_session(client, admin)
    assert client.get(root + "/events", params={"after": "bad"}).status_code == 400
    assert client.get(root + "/events", headers={"Last-Event-ID": "bad"}).status_code == 400
    _, other = identity(client, admin, "333333")  # the client cookie is now the other account's
    assert client.get(root + "/events").status_code == 404
    client.cookies.clear()
    assert client.get(root + "/events").status_code == 401


def test_display_only_and_interrupted_turn_reach_the_history(client, admin, stream_on):
    owner, auth, runner, row, root = running_session(client, admin)
    runner.state = "idle"
    runner.record = {"turns": [{"id": "turn-1", "status": "interrupted", "items": [
        {"id": "u1", "type": "userMessage", "content": [{"type": "text", "text": "stream me"}]}]}],
        "displayOnly": {"turn-1": [{"id": "p1", "type": "agentMessage", "text": "partial", "partial": True,
                                    "displayOnly": True}]}}
    detail = client.get(root).json()
    assert detail["state"] == "idle" and detail["error"] is None
    assert detail["items"][-1] == {"id": "p1", "type": "message", "role": "assistant", "text": "partial",
                                   "partial": True, "displayOnly": True, "turnStatus": "interrupted"}
    raw = client.get(root + "/items/p1").json()
    assert raw["raw"]["type"] == "displayOnlyAgentMessage" and raw["raw"]["text"] == "partial"
