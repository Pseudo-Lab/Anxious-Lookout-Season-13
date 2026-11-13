"""#90 API side of the chat stream: a relay from the account's runner to the browser (design r1–r3, issue #90).

The API keeps no event buffer and writes nothing: the runner holds the turn's events (backend/runner/stream.py). Every
DB or Kubernetes read here is a short threadpool call (connect, rebind, 60 s revalidation); the relay itself runs on the
event loop and holds no DB session. Turn frames are forwarded only for the bound request (design r3 Q1).
"""
import asyncio
import json
import os
import time
import uuid

import httpx
from starlette.concurrency import run_in_threadpool

PER_OWNER, GLOBAL = 3, 32
LINE_LIMIT = FRAME_LIMIT = 72 * 1024
CONNECT_SECONDS, HELLO_SECONDS, READ_SECONDS = 5.0, 5.0, 45.0
HEARTBEAT_SECONDS, REVALIDATE_SECONDS, STATUS_RETRY_SECONDS = 15.0, 60.0, 5.0
NOT_READY_LIMIT_SECONDS = 600.0
LAUNCH_WAIT_SECONDS, REBIND_ATTEMPTS = 30.0, 3
TURN_EVENTS = {"snapshot.begin", "snapshot.item", "snapshot.end", "turn.started", "turn.completed", "turn.discard",
               "item.started", "item.status", "item.delta", "item.output", "item.completed", "stream.stopped"}
OTHER_EVENTS = {"hello", "idle", "sync"}


def enabled():
    value = os.environ.get("RESEARCH_STREAM_ENABLE", "false")
    if value not in {"true", "false"}:
        raise RuntimeError("RESEARCH_STREAM_ENABLE must be true or false")
    return value == "true"


def trace_enabled():
    return os.environ.get("STREAM_TRACE", "false") == "true"


def parse_cursor(value):
    """`<requestId>:<seq>` or None; anything else is ValueError (400)."""
    if value is None or value == "":
        return None
    if len(value) > 80 or value.count(":") != 1:
        raise ValueError("invalid cursor")
    request, seq = value.split(":")
    if str(uuid.UUID(request)) != request or not seq.isdigit() or len(seq) > 12:
        raise ValueError("invalid cursor")
    return request, int(seq)


def frame(kind, data, event_id=None):
    head = ("id: " + event_id + "\n" if event_id else "") + "event: " + kind + "\ndata: "
    return head.encode() + json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode() + b"\n\n"


class Mismatch(Exception):
    """The upstream broke the protocol or the binding: fail closed (sync{mismatch})."""


class Parser:
    """Incremental SSE parser with hard line and frame limits (design r2 M4). Comments are returned as ('comment', text)."""

    def __init__(self):
        self.buffer = bytearray()
        self.fields = {}
        self.size = 0

    def feed(self, chunk):
        self.buffer += chunk
        out = []
        while True:
            end = self.buffer.find(b"\n")
            if end < 0:
                if len(self.buffer) > LINE_LIMIT:
                    raise Mismatch("line over limit")
                return out
            line = bytes(self.buffer[:end])
            del self.buffer[:end + 1]
            if line.endswith(b"\r"):
                line = line[:-1]
            if len(line) > LINE_LIMIT:
                raise Mismatch("line over limit")
            if not line:
                if self.fields:
                    out.append(("frame", self.fields))
                self.fields, self.size = {}, 0
                continue
            if line.startswith(b":"):
                out.append(("comment", line[1:].decode("utf-8", "replace").strip()))
                continue
            name, _, value = line.partition(b":")
            value = value[1:] if value.startswith(b" ") else value
            name = name.decode("ascii", "replace")
            if name not in {"id", "event", "data"} or name in self.fields:
                raise Mismatch("unexpected field")
            self.size += len(line) + 1
            if self.size > FRAME_LIMIT:
                raise Mismatch("frame over limit")
            self.fields[name] = value.decode("utf-8")


class Limits:
    """Concurrent streams per owner and per process. Only the event loop touches it."""

    def __init__(self):
        self.owners, self.total = {}, 0

    def acquire(self, owner):
        if self.total >= GLOBAL or self.owners.get(owner, 0) >= PER_OWNER:
            return False
        self.owners[owner] = self.owners.get(owner, 0) + 1
        self.total += 1
        return True

    def release(self, owner):
        self.owners[owner] -= 1
        if not self.owners[owner]:
            del self.owners[owner]
        self.total -= 1


class Relay:
    """One browser connection. `deps` are blocking callables, run in the threadpool:

    - target() -> {"url", "headers", "ownerId", "stateId", "podUid"|None}; raises for an unavailable runner
    - row() -> {"requestId", "state"} after re-checking the login session and ownership; raises when either is gone
    - status() -> the /codex/status body for this owner
    """

    def __init__(self, session, cursor, deps, client, stats=None):
        self.session, self.cursor, self.deps, self.client = session, cursor, deps, client
        self.bound = None
        self.last_id = None
        self.last_status = None
        self.launch_deadline = None  # set while the DB names a running turn the runner has not shown; kept across reconnects
        self.stats = stats if stats is not None else {}
        self.trace = trace_enabled()
        self.revalidate_at = time.monotonic() + REVALIDATE_SECONDS
        for key in ("eventsRelayed", "bytes", "upstreamReconnects", "dropped", "rebinds"):
            self.stats.setdefault(key, 0)
        self.stats.setdefault("reason", None)

    async def call(self, function):
        return await run_in_threadpool(function)

    async def events(self, row):
        """The browser body. `row` is the connect-time check result."""
        self.bound = row["requestId"]
        if self.cursor and self.cursor[0] != self.bound:
            self.cursor = None  # another turn's cursor: the runner answers with the active turn's snapshot
        started = time.monotonic()
        try:
            yield await self.status_frame(force=True)
            attempts = 0
            while True:
                try:
                    target = await self.call(self.deps["target"])
                except Exception:
                    if time.monotonic() - started > NOT_READY_LIMIT_SECONDS:
                        self.stats["reason"] = "not_ready_limit"
                        return
                    status = await self.status_frame()
                    if status:
                        yield status
                    async for heartbeat in self.wait(STATUS_RETRY_SECONDS):
                        yield heartbeat
                    continue
                ended = None
                upstream = self.upstream(target)
                try:
                    async for item in upstream:
                        if isinstance(item, tuple):
                            ended = item
                            break
                        yield item
                finally:
                    await upstream.aclose()  # cancels and awaits the reader task now, not at garbage collection
                reason = ended[1] if ended else "end"
                if reason in {"mismatch", "gone"}:
                    if reason == "mismatch":
                        yield frame("sync", {"reason": "mismatch"})
                    self.stats["reason"] = reason
                    return
                if reason == "rebind":
                    attempts += 1
                    self.stats["rebinds"] += 1
                    if attempts > REBIND_ATTEMPTS:
                        yield frame("sync", {"reason": "mismatch"})
                        self.stats["reason"] = "rebind_limit"
                        return
                    continue
                self.stats["upstreamReconnects"] += 1
                status = await self.status_frame()
                if status:
                    yield status
                async for heartbeat in self.wait(1.0):
                    yield heartbeat
        finally:
            self.stats["durationMs"] = round((time.monotonic() - started) * 1000, 3)

    async def wait(self, seconds):
        """Sleep, revalidating on schedule; yields nothing (an async generator so callers can relay heartbeats)."""
        await asyncio.sleep(seconds)
        await self.maybe_revalidate()
        if False:
            yield b""

    async def maybe_revalidate(self):
        if time.monotonic() >= self.revalidate_at:
            self.revalidate_at = time.monotonic() + REVALIDATE_SECONDS
            row = await self.call(self.deps["row"])  # raises (closing the stream) when the login or row is gone
            if row["requestId"] != self.bound and self.bound is not None:
                pass  # a newer turn exists: the next turn.started rebinds; nothing older is forwarded meanwhile

    async def status_frame(self, force=False):
        try:
            value = await self.call(self.deps["status"])
        except Exception:
            value = {"available": False, "reason": "unavailable"}
        if force or value != self.last_status:
            self.last_status = value
            return frame("runner", value)
        return None

    async def upstream(self, target):
        """Relayed frames as bytes, then one ('end', reason) tuple."""
        params = {}
        if self.cursor is not None and self.cursor[0] == self.bound:
            params["after"] = self.cursor[0] + ":" + str(self.cursor[1])
        queue = asyncio.Queue(maxsize=1)  # one parsed frame in flight: the M4 budget
        timeout = httpx.Timeout(READ_SECONDS, connect=CONNECT_SECONDS)

        async def read():
            parser = Parser()
            try:
                async with self.client.stream("GET", target["url"], params=params, headers=target["headers"],
                                              timeout=timeout) as response:
                    if response.status_code != 200 or not response.headers.get("content-type", "").startswith("text/event-stream"):
                        await queue.put(("end", "upstream_status"))
                        return
                    async for chunk in response.aiter_raw():
                        for item in parser.feed(chunk):
                            await queue.put(item)
                await queue.put(("end", "end"))
            except Mismatch:
                await queue.put(("end", "mismatch"))
            except (httpx.HTTPError, OSError, UnicodeDecodeError):
                await queue.put(("end", "error"))

        task = asyncio.create_task(read())
        try:
            hello = await self.next(queue, HELLO_SECONDS)
            if hello is None or hello[0] != "frame" or hello[1].get("event") != "hello":
                yield ("end", "error" if hello is None or hello[0] == "end" and hello[1] != "mismatch" else "mismatch")
                return
            self.check_hello(json_data(hello[1]), target)
            row_state = await self.call(self.deps["row"])
            waiting = row_state["state"] == "running" and row_state["requestId"] == self.bound
            if not waiting:
                self.launch_deadline = None
            elif self.launch_deadline is None:
                self.launch_deadline = time.monotonic() + LAUNCH_WAIT_SECONDS
            seen_bound = False
            last_send = time.monotonic()

            def launch_overdue():
                return waiting and not seen_bound and self.launch_deadline is not None \
                    and time.monotonic() > self.launch_deadline
            while True:
                timeout_for = HEARTBEAT_SECONDS - (time.monotonic() - last_send)
                item = await self.next(queue, max(0.05, timeout_for))
                await self.maybe_revalidate()
                if launch_overdue() and (item is None or item[0] == "end"):
                    # The DB says R_bind runs and the runner never showed it within the window (also across
                    # reconnects): count a rebind attempt, then a fresh window.
                    self.launch_deadline = None
                    yield ("end", "rebind")
                    return
                if item is None:
                    yield b": hb\n\n"
                    last_send = time.monotonic()
                    continue
                if item[0] == "end":
                    yield item
                    return
                if item[0] == "comment":
                    if self.trace and item[1].startswith("t "):
                        yield (": " + item[1] + "\n").encode()
                    continue
                fields = item[1]
                kind = fields.get("event")
                if kind == "hello" or kind not in TURN_EVENTS | OTHER_EVENTS:
                    raise Mismatch("unexpected event")
                data = json_data(fields)
                if kind in TURN_EVENTS:
                    request = data.get("requestId")
                    if request != self.bound:
                        if kind in {"turn.started", "snapshot.begin"} and isinstance(request, str):
                            row = await self.call(self.deps["row"])  # re-checks login and ownership too
                            if row["requestId"] == request:
                                self.bound = request
                                self.stats["rebinds"] += 1
                            else:
                                self.stats["dropped"] += 1
                                continue
                        else:
                            self.stats["dropped"] += 1
                            continue
                    seen_bound, self.launch_deadline = True, None
                    if fields.get("id"):
                        if not fields["id"].startswith(self.bound + ":"):
                            raise Mismatch("id outside the bound turn")
                        self.cursor = parse_cursor(fields["id"])
                elif kind == "idle":
                    if data.get("requestId") not in (None, self.bound):
                        data = {**data, "requestId": None}
                encoded = frame(kind, data, fields.get("id"))
                self.stats["eventsRelayed"] += 1
                self.stats["bytes"] += len(encoded)
                yield encoded
                last_send = time.monotonic()
                if kind == "sync" and data.get("reason") in {"overflow", "mismatch"}:
                    yield ("end", "gone")  # the runner closed this subscription: the browser reconnects without a cursor
        except Mismatch:
            yield ("end", "mismatch")
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    @staticmethod
    async def next(queue, seconds):
        try:
            return await asyncio.wait_for(queue.get(), seconds)
        except asyncio.TimeoutError:
            return None

    def check_hello(self, hello, target):
        expected = {"ownerId": target["ownerId"], "stateId": target["stateId"], "sessionId": self.session}
        if target.get("podUid") is not None:
            expected["podUid"] = target["podUid"]
        for key, value in expected.items():
            if hello.get(key) != value:
                raise Mismatch("hello " + key)
        for key in ("activeRequestId", "lastRequestId"):
            if hello.get(key) is not None and not isinstance(hello.get(key), str):
                raise Mismatch("hello " + key)


def json_data(fields):
    try:
        value = json.loads(fields.get("data", ""))
    except ValueError:
        raise Mismatch("data") from None
    if not isinstance(value, dict):
        raise Mismatch("data")
    return value
