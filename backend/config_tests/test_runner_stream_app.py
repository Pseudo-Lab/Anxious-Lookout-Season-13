"""#90 runner wiring: notifications into the stream buffer, Stop, the display-only record, the endpoints and the
shutdown hook. No Codex binary (Native built without __init__, scripted RPCs), no network, no DB."""
import json
import queue
import signal
import threading
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.codex_policy import MODEL
from runner import app as runner_app
from runner import native_policy as policy
from runner.app import Native, end_streams_on_signal
from runner.stream import Hub

SESSION = str(uuid.uuid4())


def bare_native(text=True, monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setenv("RUNNER_STREAM_TEXT", "true" if text else "false")
    value = Native.__new__(Native)
    value.messages, value.completed, value.next_id = queue.Queue(), {}, 7
    value.auth, value.blocked_reason, value.external, value.fixture = None, None, False, True
    value.work = Path("/state/workspace")
    value.sent = []
    value.send = value.sent.append
    value.stream = Hub(None, background=False)
    value.state_lock, value.lock = threading.RLock(), threading.Lock()
    value.mapping, value.active_session, value.closing = {}, None, False
    value.real_verified = False
    return value


def frames(hub):
    turn = hub.active or next(iter(hub.previous.values()))
    out = []
    for _, encoded in turn.events:
        lines = encoded.decode().split("\n")
        out.append((next(l[7:] for l in lines if l.startswith("event: ")),
                    json.loads(next(l[6:] for l in lines if l.startswith("data: ")))))
    return out


def test_receive_routes_stream_notifications(monkeypatch):
    native = bare_native(monkeypatch=monkeypatch)
    native.stream.begin(SESSION, "req")
    native.stream.bind(thread="th", turn_id="tu")
    native.stream.verified()
    turn = {"thread": "th", "turnId": "tu", "session": SESSION}
    for message in ({"method": "item/started", "params": {"threadId": "th", "turnId": "tu",
                                                         "item": {"type": "agentMessage", "id": "m1"}}},
                    {"method": "item/agentMessage/delta", "params": {"threadId": "th", "turnId": "tu", "itemId": "m1",
                                                                     "delta": "hello "}}):
        native.messages.put(message)
        native.receive(turn)
    native.stream.flush(native.stream.active, final=True)
    assert [kind for kind, _ in frames(native.stream)] == ["turn.started", "item.started", "item.delta"]


def test_model_reroute_discards_streamed_text(monkeypatch):
    native = bare_native(monkeypatch=monkeypatch)
    native.block_model = lambda: None
    native.stream.begin(SESSION, "req")
    native.stream.bind(thread="th", turn_id="tu")
    native.stream.verified()
    native.messages.put({"method": "model/rerouted", "params": {"threadId": "th", "turnId": "tu"}})
    with pytest.raises(runner_app.CodexFailure):
        native.receive({"thread": "th", "turnId": "tu"})
    assert frames(native.stream)[-1][0] == "turn.discard"
    assert native.sent[-1]["method"] == "turn/interrupt"


def test_interrupt_only_this_running_request_and_once(monkeypatch):
    native = bare_native(monkeypatch=monkeypatch)
    native.mapping[SESSION] = {"request": "req", "state": "running", "thread": "th", "turn_id": None}
    assert native.interrupt(SESSION, "req") == "not_running"  # not the active session
    native.active_session = SESSION
    assert native.interrupt(SESSION, "other") == "not_running"
    assert native.interrupt(str(uuid.uuid4()), "req") == "not_running"
    assert native.interrupt(SESSION, "req") == "interrupting"
    assert native.sent == []  # no turn id yet: flagged only
    native.mapping[SESSION]["turn_id"] = "tu"
    native.send_interrupt(SESSION, native.mapping[SESSION])  # run_turn, once the id is known
    assert native.interrupt(SESSION, "req") == "interrupting"
    sent = [body for body in native.sent if body.get("method") == "turn/interrupt"]
    assert sent == [{"id": 7, "method": "turn/interrupt", "params": {"threadId": "th", "turnId": "tu"}}]
    native.closing = True
    assert native.interrupt(SESSION, "req") == "not_running"


def scripted_turn(native, monkeypatch, status, read_items):
    """run_turn with scripted RPCs: thread/start, turn/start, then the completion, then thread/read."""
    monkeypatch.setattr(policy, "check_thread", lambda result, work: None)
    native.check_binding = lambda: None
    native.check_policy = lambda phase=None: None
    native.thread_web_modes, native.thread_policy, native.model = {}, {}, MODEL
    native.save = lambda: None
    native.turn_released = lambda entry: native.lock.release()
    native.mapping[SESSION] = {"thread": None, "request": "req", "requests": {}, "turn_id": None, "state": "running",
                               "record": {"turns": []}, "projectionVersion": 4, "modelMismatchTurns": []}
    native.active_session = SESSION
    calls = []

    def rpc(method, params, turn=None):
        calls.append(method)
        if method == "thread/start":
            return {"thread": {"id": "th"}, "model": MODEL}
        if method == "turn/start":
            native.interrupt(SESSION, "req")  # Stop pressed while turn/start is in flight
            return {"turn": {"id": "tu"}}
        if method == "thread/read":
            return {"thread": {"turns": [{"id": "tu", "status": status, "items": read_items}]}}
        raise AssertionError(method)
    native.rpc = rpc

    def receive(turn):
        hub = native.stream
        for message in ({"method": "item/started", "params": {"threadId": "th", "turnId": "tu",
                                                             "item": {"type": "agentMessage", "id": "m1"}}},
                        {"method": "item/agentMessage/delta", "params": {"threadId": "th", "turnId": "tu",
                                                                         "itemId": "m1", "delta": "partial answer so"}}):
            hub.note(message)
        native.completed[("th", "tu")] = {"status": status}
    native.receive = receive
    body = runner_app.Turn(requestId=uuid.uuid4(), text="q", tools=[], toolToken="t" * 40)
    native.run_turn(SESSION, body)
    assert not native.lock.locked()
    return calls


def test_user_stop_is_idle_interrupted_with_display_only_text(monkeypatch):
    native = bare_native(monkeypatch=monkeypatch)
    scripted_turn(native, monkeypatch, "interrupted", [{"id": "u1", "type": "userMessage", "content": []}])
    entry = native.mapping[SESSION]
    assert (entry["state"], entry["turnStatus"]) == ("idle", "interrupted")
    assert [body["method"] for body in native.sent if "method" in body] == ["turn/interrupt"]
    assert entry["record"]["displayOnly"] == {"tu": [{"id": "m1", "type": "agentMessage", "text": "partial answer so",
                                                      "partial": True, "displayOnly": True}]}
    result = native.read(SESSION)
    assert result["turnStatus"] == "interrupted" and result["record"]["displayOnly"]["tu"][0]["text"] == "partial answer so"
    assert frames(native.stream)[-1] == ("turn.completed", {"requestId": "req", "state": "idle",
                                                             "turnStatus": "interrupted", "errorCode": None})
    assert native.real_verified is False  # only a completed turn verifies the model


def test_codex_recorded_item_wins_even_if_shorter(monkeypatch):
    native = bare_native(monkeypatch=monkeypatch)
    scripted_turn(native, monkeypatch, "interrupted", [{"id": "m1", "type": "agentMessage", "text": "part"}])
    assert "displayOnly" not in native.mapping[SESSION]["record"]


def test_display_only_survives_later_projections_and_caps(monkeypatch):
    native = bare_native(monkeypatch=monkeypatch)
    entry = {"record": {"turns": [{"id": "t1", "items": []}], "displayOnly": {"t1": [{"id": "a", "text": "kept"}]}}}
    native.publish_record = lambda session, value, record: value.__setitem__("record", record)
    # A later thread/read (no new display-only) keeps it; a model-quarantined turn drops it.
    native._store_record(SESSION, entry, {"turns": [{"id": "t1", "items": []}, {"id": "t2", "items": []}]}, None)
    assert entry["record"]["displayOnly"] == {"t1": [{"id": "a", "text": "kept"}]}
    entry["modelMismatchTurns"] = ["t1"]
    native._store_record(SESSION, entry, {"turns": [{"id": "t1", "items": []}]}, None)
    assert "displayOnly" not in entry["record"]
    # Over the conversation limit: the oldest turn goes, and the drop is recorded for the history.
    monkeypatch.setattr(Native, "DISPLAY_ONLY_LIMIT", 10)
    entry = {"record": {"turns": [{"id": "t1"}, {"id": "t2"}], "displayOnly": {"t1": [{"id": "a", "text": "123456"}]}}}
    native._store_record(SESSION, entry, {"turns": [{"id": "t1", "items": []}, {"id": "t2", "items": []}]}, None,
                         ("t2", [{"id": "b", "type": "agentMessage", "text": "abcdef"}]))
    assert list(entry["record"]["displayOnly"]) == ["t2"] and entry["record"]["displayOnlyDropped"] == ["t1"]


def test_failed_turn_keeps_failed_mapping(monkeypatch):
    native = bare_native(monkeypatch=monkeypatch)
    scripted = scripted_turn(native, monkeypatch, "failed", [])
    assert native.mapping[SESSION]["state"] == "failed" and "displayOnly" not in native.mapping[SESSION]["record"]
    assert frames(native.stream)[-1][1]["turnStatus"] == "failed"


# --- endpoints -----------------------------------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    from runner.binding import initialize_binding
    owner, state, pod = (str(uuid.uuid4()) for _ in range(3))
    root = tmp_path / "native"
    initialize_binding(root, owner, state)
    token = tmp_path / "token"
    token.write_text("t" * 40)
    for key, value in {"APP_ENV": "test", "RUNNER_ACCOUNT_ID": owner, "RUNNER_STATE_ID": state,
                       "RUNNER_POD_UID": pod, "RUNNER_TOKEN_FILE": str(token), "RUNNER_STATE_DIR": str(root),
                       "CODEX_AUTH_MODE": "none", "RESEARCH_TOOL_CALLBACK_URL": "http://callback.invalid/tools",
                       "RUNNER_STREAM_TEXT": "true"}.items():
        monkeypatch.setenv(key, value)

    class Fake:
        fixture, real_verified = False, False

        def __init__(self, *args, **kwargs):
            self.state_lock, self.lock = threading.RLock(), threading.Lock()
            self.active_session, self.mapping = None, {}
            self.stream = Hub(None, background=False)
            self.interrupts = []

        def close(self):
            self.stream.close()
            return True

        def availability(self):
            return None

        def stream_hub(self):
            return self.stream

        def interrupt(self, session, request):
            self.interrupts.append((session, request))
            return "interrupting"
    monkeypatch.setattr("runner.app.Native", Fake)
    headers = {"Authorization": "Bearer " + "t" * 40, "X-Runner-State-Id": state}
    with TestClient(runner_app.create_app()) as test_client:
        yield test_client, headers, dict(owner=owner, state=state, pod=pod)


def read_stream(test_client, path, headers, hub, after=0.3):
    timer = threading.Timer(after, hub.close)
    timer.start()
    try:
        with test_client.stream("GET", path, headers=headers) as reply:
            body = b"".join(reply.iter_bytes())
            return reply, body
    finally:
        timer.cancel()


def test_events_hello_first_then_idle(client):
    test_client, headers, ids = client
    hub = test_client.app.state.native.stream
    reply, body = read_stream(test_client, "/sessions/" + SESSION + "/events", headers, hub)
    assert reply.status_code == 200 and reply.headers["content-type"].startswith("text/event-stream")
    assert reply.headers["cache-control"] == "no-store" and reply.headers["x-accel-buffering"] == "no"
    blocks = body.decode().split("\n\n")
    assert blocks[0].startswith("event: hello\ndata: ")
    assert json.loads(blocks[0].split("data: ")[1]) == {"ownerId": ids["owner"], "stateId": ids["state"],
                                                         "podUid": ids["pod"], "sessionId": SESSION,
                                                         "activeRequestId": None, "lastRequestId": None}
    assert blocks[1].startswith("event: idle")


def test_events_active_turn_snapshot_and_cursor(client):
    test_client, headers, ids = client
    hub = test_client.app.state.native.stream
    request = str(uuid.uuid4())
    hub.begin(SESSION, request)
    hub.verified()
    reply, body = read_stream(test_client, "/sessions/" + SESSION + "/events", headers, hub)
    text = body.decode()
    assert '"activeRequestId":"' + request + '"' in text and "event: snapshot.begin" in text
    assert "id: " + request + ":1" in text
    hub.closed = False
    reply, body = read_stream(test_client, "/sessions/" + SESSION + "/events?after=" + request + ":1", headers, hub)
    assert "snapshot" not in body.decode()


@pytest.mark.parametrize("cursor", ["x", "abc:1", str(uuid.uuid4()) + ":-1", str(uuid.uuid4()) + ":1:2",
                                    str(uuid.uuid4()).upper() + ":1"])
def test_events_invalid_cursor(client, cursor):
    test_client, headers, ids = client
    reply = test_client.get("/sessions/" + SESSION + "/events", params={"after": cursor}, headers=headers)
    assert (reply.status_code, reply.json()) == (400, {"ownerId": ids["owner"], "error": "invalid_cursor"})
    reply = test_client.get("/sessions/" + SESSION + "/events", headers={**headers, "Last-Event-ID": cursor})
    assert reply.status_code == 400


def test_events_subscriber_cap_and_auth(client):
    test_client, headers, ids = client
    hub = test_client.app.state.native.stream
    held = [hub.subscribe(SESSION, None)[0] for _ in range(6)]
    reply = test_client.get("/sessions/" + SESSION + "/events", headers=headers)
    assert (reply.status_code, reply.json()) == (429, {"ownerId": ids["owner"], "error": "stream_limit"})
    for subscription in held:
        hub.unsubscribe(subscription)
    assert test_client.get("/sessions/" + SESSION + "/events").status_code == 401


def test_events_refused_while_stopping(client):
    test_client, headers, ids = client
    test_client.app.state.stopping = True
    reply = test_client.get("/sessions/" + SESSION + "/events", headers=headers)
    assert (reply.status_code, reply.json()["error"]) == (503, "codex_runner_stopped")
    test_client.app.state.stopping = False


def test_interrupt_endpoint(client):
    test_client, headers, ids = client
    request = str(uuid.uuid4())
    reply = test_client.post("/sessions/" + SESSION + "/interrupt", json={"requestId": request}, headers=headers)
    assert reply.status_code == 200 and reply.json() == {"ownerId": ids["owner"], "stateId": ids["state"],
                                                         "podUid": ids["pod"], "sessionId": SESSION,
                                                         "requestId": request, "state": "interrupting"}
    assert test_client.app.state.native.interrupts == [(SESSION, request)]
    assert test_client.post("/sessions/" + SESSION + "/interrupt", json={"requestId": request, "x": 1},
                            headers=headers).status_code == 422
    assert test_client.post("/sessions/" + SESSION + "/interrupt", json={"requestId": request}).status_code == 401


def test_signal_chain_ends_streams_then_calls_previous():
    calls = []
    original = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, lambda signum, frame: calls.append(signum))
    try:
        hub = Hub(None, background=False)
        subscription, _ = hub.subscribe(SESSION, None)
        app = type("App", (), {"state": type("State", (), {"native": type("N", (), {"stream_hub": lambda self: hub})()})()})()
        restore = end_streams_on_signal(app)
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        assert subscription.closed == "stopping" and calls == [signal.SIGTERM]
        restore()
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        assert calls == [signal.SIGTERM, signal.SIGTERM]
    finally:
        signal.signal(signal.SIGTERM, original)


def test_signal_chain_outside_main_thread_installs_nothing():
    result = []
    thread = threading.Thread(target=lambda: result.append(end_streams_on_signal(None)))
    thread.start()
    thread.join()
    result[0]()


# --- fixture delta mode (unit tests / validate.sh only) -------------------------------------------------------------

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "codex.mock.py"


def fixture_session(tmp_path, prompt, interrupt=False):
    import subprocess
    import sys
    home = tmp_path / "codex"
    home.mkdir()
    process = subprocess.Popen([sys.executable, str(FIXTURE)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                               env={"CODEX_HOME": str(home), "PATH": "/usr/bin:/bin"})

    def send(body):
        process.stdin.write(json.dumps(body) + "\n")
        process.stdin.flush()

    def until(predicate):
        out = []
        while True:
            message = json.loads(process.stdout.readline())
            out.append(message)
            if predicate(message):
                return out
    send({"id": 1, "method": "thread/start", "params": {"model": MODEL, "cwd": "/w", "dynamicTools": []}})
    thread = until(lambda m: m.get("id") == 1)[-1]["result"]["thread"]["id"]
    send({"id": 2, "method": "turn/start", "params": {"threadId": thread, "model": MODEL,
                                                      "input": [{"type": "text", "text": prompt}]}})
    seen = until(lambda m: m.get("id") == 2)
    call = next(m for m in seen if m.get("method") == "item/tool/call")
    turn = seen[-1]["result"]["turn"]["id"]
    send({"id": call["id"], "result": {"success": True, "contentItems": [{"type": "inputText", "text": "{}"}]}})
    if interrupt:
        seen = until(lambda m: m.get("method") == "item/agentMessage/delta" and m["params"]["delta"] == "fixture word 2 ")
        send({"id": 3, "method": "turn/interrupt", "params": {"threadId": thread, "turnId": turn}})
    seen += until(lambda m: m.get("method") == "turn/completed")
    send({"id": 4, "method": "thread/read", "params": {"threadId": thread}})
    record = until(lambda m: m.get("id") == 4)[-1]["result"]["thread"]
    process.stdin.close()
    process.wait(timeout=10)
    return seen, record


def test_fixture_stream_mode(tmp_path):
    seen, record = fixture_session(tmp_path, "/fixture/stream 3 0")
    deltas = [m["params"]["delta"] for m in seen if m.get("method") == "item/agentMessage/delta"]
    assert deltas == ["fixture word 0 ", "fixture word 1 ", "fixture word 2 "]
    answer = record["turns"][-1]["items"][-1]
    assert answer["type"] == "agentMessage" and answer["text"] == "".join(deltas)
    assert seen[-1]["params"]["turn"]["status"] == "completed"


def test_fixture_stream_interrupt_mode(tmp_path):
    seen, record = fixture_session(tmp_path, "/fixture/stream-interrupt 3 0", interrupt=True)
    assert seen[-1]["params"]["turn"]["status"] == "interrupted"
    assert record["turns"][-1]["status"] == "interrupted"
    assert all(item["type"] != "agentMessage" for item in record["turns"][-1]["items"])
