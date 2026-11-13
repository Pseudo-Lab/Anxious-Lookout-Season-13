"""#90 review 8debb4f: an event stream to a reader that stopped reading must not hold a SIGTERM. Loopback only."""
import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx

from app import chat_stream
from app.serve import stream_aware_h11
from runner.stream import STREAM_ABORT_MARK, StoppableStreamingResponse

HERE = Path(__file__).resolve().parent


def test_review_probe_idle_and_slow_reader_exit_promptly():
    """Review's real-uvicorn probe (two changes, see its header): both modes exit within its 3 s window."""
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(HERE.parent), str(HERE)])}
    result = subprocess.run([sys.executable, str(HERE / "shutdown_probe.py")], cwd="/tmp", env=env,
                            capture_output=True, text=True, timeout=60)
    lines = [line for line in result.stdout.splitlines() if line.startswith(("idle ", "slow "))]
    assert [line.split()[:2] for line in lines] == [["idle", "exit"], ["slow", "exit"]], result.stdout
    assert all(float(line.split()[-1]) < 3 for line in lines)


def test_stoppable_response_ends_while_send_is_blocked():
    """The ASGI part alone: send blocks after the first chunk (a reader that stopped reading); the stop event ends
    the response promptly, the body generator's cleanup runs, on_close runs, and the connection is marked."""
    async def main():
        cleaned, closed, stop = [], [], asyncio.Event()

        async def body():
            try:
                while True:
                    yield b"data: x\n\n"
            finally:
                cleaned.append(True)
        blocked = asyncio.Event()

        async def send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                blocked.set()
                await asyncio.Event().wait()  # never drains

        async def receive():
            await asyncio.Event().wait()
        scope = {"type": "http", "state": {}, "asgi": {"spec_version": "2.4"}}
        response = StoppableStreamingResponse(body(), stop, on_close=lambda: closed.append(True),
                                              media_type="text/event-stream")
        task = asyncio.create_task(response(scope, receive, send))
        await asyncio.wait_for(blocked.wait(), 2)
        started = asyncio.get_running_loop().time()
        stop.set()
        await asyncio.wait_for(task, 2)
        return asyncio.get_running_loop().time() - started, cleaned, closed, scope["state"]
    elapsed, cleaned, closed, state = asyncio.run(main())
    assert elapsed < 0.5 and cleaned == [True] and closed == [True] and state.get(STREAM_ABORT_MARK) is True


def test_protocol_aborts_only_marked_unfinished_streams():
    protocol_class = stream_aware_h11()
    calls = []

    def protocol(state, complete):
        value = protocol_class.__new__(protocol_class)
        value.transport = SimpleNamespace(abort=lambda: calls.append("abort"), close=lambda: calls.append("close"))
        value.cycle = SimpleNamespace(scope={"state": state}, response_complete=complete, keep_alive=True)
        value.conn = SimpleNamespace(send=lambda event: calls.append("conn"))
        return value
    protocol({STREAM_ABORT_MARK: True}, False).shutdown()
    assert calls == ["abort"]
    calls.clear()
    plain = protocol({}, False)
    plain.shutdown()  # an ordinary unfinished response: uvicorn's own behaviour (finish, then close)
    assert calls == [] and plain.cycle.keep_alive is False
    protocol({STREAM_ABORT_MARK: True}, True).shutdown()  # finished: uvicorn's normal close
    assert calls == ["conn", "close"]


def test_relay_cuts_a_stalled_browser_and_closes_the_runner_side(monkeypatch):
    """The browser stops taking frames: after STALL_SECONDS the relay stops reading the runner and closes that
    connection (so the runner's own send cannot stay blocked past its Pod grace)."""
    monkeypatch.setattr(chat_stream, "STALL_SECONDS", 0.2)
    from test_chat_stream_relay import R1, SESSION, deps, hello, sse
    upstream_closed = []

    async def body():
        try:
            yield hello()
            for index in range(1, 1000):
                yield sse("item.delta", {"requestId": R1, "itemId": "m", "text": "x"}, R1 + ":" + str(index))
            await asyncio.sleep(30)
        finally:
            upstream_closed.append(True)

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body())

    async def main():
        dep, _ = deps()
        relay = chat_stream.Relay(SESSION, None, dep, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        events = relay.events({"requestId": R1, "state": "running"})
        first = [await events.__anext__() for _ in range(3)]  # status, two deltas
        await asyncio.sleep(1.0)  # the browser stops reading
        rest = [chunk async for chunk in events]
        return first, rest, relay
    first, rest, relay = asyncio.run(asyncio.wait_for(main(), 10))
    assert relay.stalled and relay.stats["reason"] == "stalled" and upstream_closed == [True]
    assert rest[-1] == chat_stream.frame("sync", {"reason": "overflow"})
    assert len(rest) < 10  # only what was already queued, then the end
