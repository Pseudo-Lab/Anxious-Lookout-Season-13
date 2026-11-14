"""#90 API relay (backend/app/chat_stream.py) and the display items it implies. No DB, no network: the runner is an
httpx.MockTransport and the blocking dependencies are plain functions."""
import asyncio
import json
import types
import uuid

import httpx
import pytest

from app import chat_stream
from app.chat_stream import Limits, Mismatch, Parser, Relay, parse_cursor
from app.conversations import display_items

OWNER, STATE, POD, SESSION = (str(uuid.uuid4()) for _ in range(4))
R1, R2 = str(uuid.uuid4()), str(uuid.uuid4())


def sse(kind, data, event_id=None):
    return chat_stream.frame(kind, data, event_id)


def hello(**changes):
    value = {"ownerId": OWNER, "stateId": STATE, "podUid": POD, "sessionId": SESSION,
             "activeRequestId": R1, "lastRequestId": None}
    value.update(changes)
    return sse("hello", value)


def target():
    return {"url": "http://runner.invalid/sessions/" + SESSION + "/events", "headers": {"Authorization": "Bearer x"},
            "ownerId": OWNER, "stateId": STATE, "podUid": POD}


def upstreams(*bodies, seen=None):
    """One mock runner response per upstream connection, in order; then 503."""
    queue = list(bodies)

    async def body_iter(parts):
        for part in parts:
            if isinstance(part, float):
                await asyncio.sleep(part)
            else:
                yield part

    def handler(request):
        if seen is not None:
            seen.append(dict(request.url.params))
        if not queue:
            return httpx.Response(503)
        parts = queue.pop(0)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body_iter(parts))
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def run(relay, row, limit=40, seconds=5.0):
    async def collect():
        out = []

        async def inner():
            async for chunk in relay.events(row):
                out.append(chunk)
                if len(out) >= limit:
                    return
        try:
            await asyncio.wait_for(inner(), seconds)
        except asyncio.TimeoutError:
            pass
        return out
    return asyncio.run(collect())


def kinds(chunks):
    result = []
    for chunk in chunks:
        for line in chunk.decode().split("\n"):
            if line.startswith("event: "):
                result.append(line[7:])
    return result


def datas(chunks, kind):
    result = []
    for chunk in chunks:
        lines = chunk.decode().split("\n")
        if "event: " + kind in lines:
            result.append(json.loads(next(l[6:] for l in lines if l.startswith("data: "))))
    return result


def deps(rows=None, status=None, target_fn=None):
    rows = rows if rows is not None else [{"requestId": R1, "state": "running"}]
    calls = {"row": 0, "target": 0}

    def row():
        calls["row"] += 1
        value = rows[min(calls["row"] - 1, len(rows) - 1)]
        if isinstance(value, Exception):
            raise value
        return value

    def tgt():
        calls["target"] += 1
        return (target_fn or target)()
    return {"row": row, "status": status or (lambda: {"available": True, "reason": None}), "target": tgt}, calls


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(chat_stream, "HEARTBEAT_SECONDS", 0.2)
    monkeypatch.setattr(chat_stream, "HELLO_SECONDS", 0.3)
    monkeypatch.setattr(chat_stream, "STATUS_RETRY_SECONDS", 0.05)
    monkeypatch.setattr(chat_stream, "LAUNCH_WAIT_SECONDS", 0.3)


# --- parser / cursor / limits --------------------------------------------------------------------------------------

def test_parser_frames_comments_and_crlf():
    parser = Parser()
    out = parser.feed(b"id: a:1\r\nevent: x\r\ndata: {}\r\n\r\n: hb\n\nevent: y\ndata: {\"k\":1}\n")
    assert out == [("frame", {"id": "a:1", "event": "x", "data": "{}"}), ("comment", "hb")]
    assert parser.feed(b"\n") == [("frame", {"event": "y", "data": "{\"k\":1}"})]


@pytest.mark.parametrize("chunk", [b"x" * (chat_stream.LINE_LIMIT + 1), b"data: " + b"x" * chat_stream.LINE_LIMIT + b"\n",
                                   b"retry: 5\n", b"data: 1\ndata: 2\n"])
def test_parser_refuses_oversize_and_unknown(chunk):
    with pytest.raises(Mismatch):
        Parser().feed(chunk)


def test_parser_frame_limit_across_lines():
    parser = Parser()
    with pytest.raises(Mismatch):
        parser.feed(b"id: " + b"1" * 40000 + b"\nevent: x\ndata: " + b"2" * 40000 + b"\n")


@pytest.mark.parametrize("value,expected", [(None, None), ("", None), (R1 + ":0", (R1, 0)), (R1 + ":12", (R1, 12))])
def test_cursor_ok(value, expected):
    assert parse_cursor(value) == expected


@pytest.mark.parametrize("value", ["x", R1, R1 + ":", R1 + ":-1", R1 + ":1:2", R1.upper() + ":1", R1 + ":" + "1" * 13])
def test_cursor_refused(value):
    with pytest.raises(ValueError):
        parse_cursor(value)


def test_limits():
    limits = Limits()
    assert all(limits.acquire("a") for _ in range(chat_stream.PER_OWNER)) and not limits.acquire("a")
    for index in range(chat_stream.GLOBAL - chat_stream.PER_OWNER):
        assert limits.acquire("o" + str(index))
    assert not limits.acquire("new")
    limits.release("a")
    assert limits.acquire("new") and limits.total == chat_stream.GLOBAL


def test_flag(monkeypatch):
    monkeypatch.setenv("RESEARCH_STREAM_ENABLE", "yes")
    with pytest.raises(RuntimeError):
        chat_stream.enabled()
    monkeypatch.delenv("RESEARCH_STREAM_ENABLE")
    assert chat_stream.enabled() is False


# --- relay ---------------------------------------------------------------------------------------------------------

def test_status_first_then_bound_frames_only():
    body = [hello(),
            sse("snapshot.begin", {"requestId": R1, "turnState": "running", "seq": 2}),
            sse("snapshot.end", {"requestId": R1, "seq": 2}, R1 + ":2"),
            sse("item.delta", {"requestId": R2, "itemId": "x", "text": "other turn"}, R2 + ":3"),
            sse("item.delta", {"requestId": R1, "itemId": "m", "text": "mine"}, R1 + ":3")]
    dep, calls = deps()
    relay = Relay(SESSION, None, dep, upstreams(body))
    chunks = run(relay, {"requestId": R1, "state": "running"}, limit=4)
    assert kinds(chunks) == ["runner", "snapshot.begin", "snapshot.end", "item.delta"]
    assert datas(chunks, "item.delta") == [{"requestId": R1, "itemId": "m", "text": "mine"}]
    assert relay.stats["dropped"] == 1 and relay.cursor == (R1, 3)


@pytest.mark.parametrize("field", ["ownerId", "stateId", "podUid", "sessionId"])
def test_hello_mismatch_fails_closed(field):
    dep, _ = deps()
    relay = Relay(SESSION, None, dep, upstreams([hello(**{field: str(uuid.uuid4())}),
                                                sse("item.delta", {"requestId": R1, "itemId": "m", "text": "leak"}, R1 + ":1")]))
    chunks = run(relay, {"requestId": R1, "state": "running"})
    assert kinds(chunks) == ["runner", "sync"] and datas(chunks, "sync") == [{"reason": "mismatch"}]
    assert b"leak" not in b"".join(chunks)


def test_frame_before_hello_or_second_hello_fails_closed():
    dep, _ = deps()
    early = sse("item.delta", {"requestId": R1, "itemId": "m", "text": "leak"}, R1 + ":1")
    for body in ([early, hello()], [hello(), hello()]):
        chunks = run(Relay(SESSION, None, dep, upstreams(body)), {"requestId": R1, "state": "running"})
        assert "sync" in kinds(chunks) and b"leak" not in b"".join(chunks)


def test_new_turn_rebinds_only_when_the_row_says_so():
    body = [hello(activeRequestId=None, lastRequestId=R1),
            sse("turn.started", {"requestId": R2}, R2 + ":1"),
            sse("item.delta", {"requestId": R2, "itemId": "m", "text": "two"}, R2 + ":2")]
    dep, calls = deps(rows=[{"requestId": R1, "state": "idle"}, {"requestId": R2, "state": "running"}])
    relay = Relay(SESSION, None, dep, upstreams(body))
    chunks = run(relay, {"requestId": R1, "state": "idle"}, limit=3)
    assert kinds(chunks) == ["runner", "turn.started", "item.delta"] and relay.bound == R2
    # The row does not name the new request: nothing of it leaves.
    dep, calls = deps(rows=[{"requestId": R1, "state": "idle"}])
    relay = Relay(SESSION, None, dep, upstreams(body))
    chunks = run(relay, {"requestId": R1, "state": "idle"}, limit=3, seconds=1.0)
    assert "turn.started" not in kinds(chunks) and "item.delta" not in kinds(chunks)


def test_rebind_rechecks_login_and_ownership():
    body = [hello(activeRequestId=None, lastRequestId=R1), sse("turn.started", {"requestId": R2}, R2 + ":1")]
    dep, calls = deps(rows=[{"requestId": R1, "state": "idle"}, PermissionError("login gone")])
    relay = Relay(SESSION, None, dep, upstreams(body))

    async def collect():
        out = []
        with pytest.raises(PermissionError):
            async for chunk in relay.events({"requestId": R1, "state": "idle"}):
                out.append(chunk)
        return out
    chunks = asyncio.run(collect())
    assert "turn.started" not in kinds(chunks)


def test_idle_names_only_the_bound_request():
    body = [hello(activeRequestId=None, lastRequestId=R2), sse("idle", {"requestId": R2, "turnState": "completed"})]
    dep, _ = deps(rows=[{"requestId": R1, "state": "idle"}])
    chunks = run(Relay(SESSION, None, dep, upstreams(body)), {"requestId": R1, "state": "idle"}, limit=2)
    assert datas(chunks, "idle") == [{"requestId": None, "turnState": "completed"}]


@pytest.mark.parametrize("bad", [sse("weird", {"requestId": R1}),
                                 sse("item.delta", {"requestId": R1, "itemId": "m", "text": "x"}, R2 + ":1"),
                                 b"event: item.delta\ndata: [1]\n\n", b"event: item.delta\ndata: {\n\n"])
def test_protocol_violations_fail_closed(bad):
    dep, _ = deps()
    chunks = run(Relay(SESSION, None, dep, upstreams([hello(), bad])), {"requestId": R1, "state": "running"})
    assert kinds(chunks)[-1] == "sync" and datas(chunks, "sync")[-1] == {"reason": "mismatch"}


def test_cursor_goes_upstream_only_for_the_bound_turn():
    seen = []
    dep, _ = deps()
    run(Relay(SESSION, (R1, 7), dep, upstreams([hello()], seen=seen)), {"requestId": R1, "state": "running"},
        limit=2, seconds=0.5)
    assert seen[0] == {"after": R1 + ":7"}
    seen = []
    run(Relay(SESSION, (R2, 7), dep, upstreams([hello()], seen=seen)), {"requestId": R1, "state": "running"},
        limit=2, seconds=0.5)
    assert seen[0] == {}


def test_launch_wait_is_bounded():
    """DB says R1 runs, the runner never shows it: reconnect at most REBIND_ATTEMPTS times, then sync{mismatch}."""
    idle = [hello(activeRequestId=None, lastRequestId=R2), 1.0]  # the runner keeps the stream open
    dep, calls = deps(rows=[{"requestId": R1, "state": "running"}])
    relay = Relay(SESSION, None, dep, upstreams(*([idle] * 6)))
    chunks = run(relay, {"requestId": R1, "state": "running"}, limit=100, seconds=4.0)
    assert datas(chunks, "sync")[-1] == {"reason": "mismatch"} and relay.stats["reason"] == "rebind_limit"
    assert calls["target"] == chat_stream.REBIND_ATTEMPTS + 1


def test_unavailable_runner_reports_status_and_stops_after_limit(monkeypatch):
    monkeypatch.setattr(chat_stream, "NOT_READY_LIMIT_SECONDS", 0.3)
    statuses = iter([{"available": False, "reason": "provisioning"}] * 3 + [{"available": False, "reason": "stopping"}] * 50)

    def failing():
        raise ValueError("no pod")
    dep, calls = deps(status=lambda: next(statuses), target_fn=failing)
    relay = Relay(SESSION, None, dep, upstreams())
    chunks = run(relay, {"requestId": R1, "state": "idle"}, limit=100, seconds=3.0)
    assert [value["reason"] for value in datas(chunks, "runner")] == ["provisioning", "stopping"]
    assert relay.stats["reason"] == "not_ready_limit"


def test_upstream_end_reconnects_with_cursor_and_status():
    seen = []
    first = [hello(), sse("item.delta", {"requestId": R1, "itemId": "m", "text": "a"}, R1 + ":4")]
    second = [hello(), sse("item.delta", {"requestId": R1, "itemId": "m", "text": "b"}, R1 + ":5")]
    dep, _ = deps()
    relay = Relay(SESSION, None, dep, upstreams(first, second, seen=seen))
    chunks = run(relay, {"requestId": R1, "state": "running"}, limit=4, seconds=3.0)
    assert [value["text"] for value in datas(chunks, "item.delta")] == ["a", "b"]
    assert seen[1] == {"after": R1 + ":4"} and relay.stats["upstreamReconnects"] >= 1


def test_heartbeat_when_quiet():
    dep, _ = deps()
    chunks = run(Relay(SESSION, None, dep, upstreams([hello(), 1.0])), {"requestId": R1, "state": "running"},
                 limit=3, seconds=2.0)
    assert b": hb\n\n" in chunks


# --- display items -------------------------------------------------------------------------------------------------

def row_with(record, state="idle"):
    return types.SimpleNamespace(native_record=record, pending_text=None, request_id=uuid.uuid4(), state=state)


def test_interrupted_turn_display_only_and_removed_notice():
    record = {"turns": [
        {"id": "t1", "status": "interrupted", "items": [{"id": "u1", "type": "userMessage", "content": [{"type": "text", "text": "q"}]}]},
        {"id": "t2", "status": "interrupted", "items": [{"id": "u2", "type": "userMessage", "content": []},
                                                        {"id": "a2", "type": "agentMessage", "text": "recorded"}]},
        {"id": "t3", "status": "completed", "items": [{"id": "a3", "type": "agentMessage", "text": "done"}]}],
        "displayOnly": {"t1": [{"id": "p1", "type": "agentMessage", "text": "partial", "partial": True, "displayOnly": True}]},
        "displayOnlyDropped": ["t2"]}
    items = display_items(row_with(record))
    assert items[0] == {"id": "u1", "type": "message", "role": "user", "text": "q"}  # user input: no stop marker
    assert items[1] == {"id": "p1", "type": "message", "role": "assistant", "text": "partial", "partial": True,
                        "displayOnly": True, "turnStatus": "interrupted"}
    assert items[3] == {"id": "a2", "type": "message", "role": "assistant", "text": "recorded", "turnStatus": "interrupted"}
    assert items[4] == {"id": "partial-removed-1", "type": "notice", "code": "partial_removed", "turnStatus": "interrupted"}
    assert items[5] == {"id": "a3", "type": "message", "role": "assistant", "text": "done"}


def test_model_mismatch_turn_never_shows_display_only():
    record = {"turns": [{"id": "t1", "status": "interrupted", "modelMismatch": True, "items": []}],
              "displayOnly": {"t1": [{"id": "p1", "text": "partial"}]}}
    assert display_items(row_with(record)) == []


def test_launch_wait_survives_a_runner_that_keeps_closing():
    """The window is not reset by reconnects: a runner whose stream keeps ending still reaches the rebind limit."""
    idle = [hello(activeRequestId=None, lastRequestId=R2)]  # ends right after hello
    dep, calls = deps(rows=[{"requestId": R1, "state": "running"}])
    relay = Relay(SESSION, None, dep, upstreams(*([idle] * 40)))
    chunks = run(relay, {"requestId": R1, "state": "running"}, limit=200, seconds=8.0)
    assert relay.stats["reason"] == "rebind_limit" and datas(chunks, "sync")[-1] == {"reason": "mismatch"}


def test_runner_overflow_sync_closes_the_browser_stream():
    body = [hello(), sse("sync", {"reason": "overflow"}), sse("item.delta", {"requestId": R1, "itemId": "m", "text": "after"}, R1 + ":9")]
    dep, calls = deps()
    relay = Relay(SESSION, None, dep, upstreams(body, body))
    chunks = run(relay, {"requestId": R1, "state": "running"}, limit=10, seconds=2.0)
    assert kinds(chunks) == ["runner", "sync"] and calls["target"] == 1 and relay.stats["reason"] == "gone"


def test_no_reader_task_outlives_the_relay():
    body = [hello(), sse("sync", {"reason": "overflow"}), 5.0]  # the runner keeps the stream open after its sync

    async def main():
        dep, _ = deps()
        relay = Relay(SESSION, None, dep, upstreams(body))
        async for _ in relay.events({"requestId": R1, "state": "running"}):
            pass
        await asyncio.sleep(0)
        return [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    assert asyncio.run(asyncio.wait_for(main(), 3.0)) == []


def test_shutdown_event_ends_the_relay_at_once(monkeypatch):
    monkeypatch.setattr(chat_stream, "HEARTBEAT_SECONDS", 10.0)

    async def main():
        shutdown = asyncio.Event()
        dep, _ = deps()
        relay = Relay(SESSION, None, dep, upstreams([hello(), 30.0]), shutdown=shutdown)
        out = []

        async def consume():
            async for chunk in relay.events({"requestId": R1, "state": "running"}):
                out.append(chunk)
        task = asyncio.create_task(consume())
        await asyncio.sleep(0.3)
        started = asyncio.get_running_loop().time()
        shutdown.set()
        await asyncio.wait_for(task, 2.0)
        return asyncio.get_running_loop().time() - started, relay.stats["reason"]
    elapsed, reason = asyncio.run(main())
    assert elapsed < 0.5 and reason == "shutdown"


def test_install_shutdown_chains_signals():
    import signal
    calls = []
    original = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, lambda signum, frame: calls.append(signum))
    try:
        async def main():
            event = asyncio.Event()
            restore = chat_stream.install_shutdown(event, asyncio.get_running_loop())
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            await asyncio.sleep(0)
            restore()
            return event.is_set()
        assert asyncio.run(main()) is True and calls == [signal.SIGTERM]
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        assert calls == [signal.SIGTERM, signal.SIGTERM]  # restored: only the previous handler now
    finally:
        signal.signal(signal.SIGTERM, original)


def test_not_ready_wait_counts_from_the_outage_not_from_the_stream_start(monkeypatch):
    """D3-e (2026-11-14): a stream open longer than NOT_READY_LIMIT_SECONDS closed at the first failed target after
    the runner stopped (reason not_ready_limit at once). The wait must start at the outage."""
    import time as clock
    monkeypatch.setattr(chat_stream, "NOT_READY_LIMIT_SECONDS", 0.4)
    marks = {}

    def first_then_fail():
        if "fail" not in marks:
            marks["fail"] = 0
            return target()
        marks.setdefault("first", clock.monotonic())
        raise ValueError("runner gone")
    dep, calls = deps(status=lambda: {"available": False, "reason": "stopping"}, target_fn=first_then_fail)
    # the runner keeps the first stream open longer than the limit, then ends it
    relay = Relay(SESSION, None, dep, upstreams([hello(activeRequestId=None), 0.8]))
    chunks = run(relay, {"requestId": R1, "state": "idle"}, limit=1000, seconds=4.0)
    ended = clock.monotonic()
    assert relay.stats["reason"] == "not_ready_limit"
    assert ended - marks["first"] >= 0.4  # waited the limit after the outage, not closed at once
    assert datas(chunks, "runner")[-1] == {"available": False, "reason": "stopping"}
