"""#90 runner side of the chat stream: an in-memory, bounded event buffer per turn (design r3, issue #90).

Nothing here writes a file or a database, and nothing here decides a turn: every failure stops the stream for the
turn (`stream.stopped`) and the record path (`thread/read` -> signed projection) stays the source of truth.

Text (agentMessage deltas, command output, the command summary) leaves this module only through the holdback in
`Holdback.plan` (design r3 B1, r4 prefix fingerprints): the cut never moves right of the policy's start (r4: see
stream_prefix; r3: `len(raw) - (L - 1)`, where L is the longest recorded
literal, and a detected range that crosses the cut pulls the cut back to its start. Text events are emitted only when
RUNNER_STREAM_TEXT=true (default false until the B1 review gate and the VM checks).
"""
import asyncio
import json
import os
import threading
import time
from collections import deque

from .stream_prefix import HoldPolicy, policy as hold_policy

MARKER = "[redacted]"

# Design r2 M1/M3 and r3: one budget per turn for everything this module retains.
TURN_BUDGET = 2 * 1024 * 1024
MAX_ITEMS = 256
MAX_EVENTS = 2000
MAX_EVENT_BYTES = 1024 * 1024
TEXT_LIMIT = 512 * 1024          # agentMessage text kept per turn (UTF-8 bytes, sanitized)
OUTPUT_ITEM_LIMIT = 16 * 1024    # command output streamed per item
OUTPUT_TURN_LIMIT = 128 * 1024   # command output streamed per turn
DELTA_LIMIT = 64 * 1024          # one native delta
SUMMARY_LIMIT = 2 * 1024         # serialized command summary
SUMMARY_RAW_LIMIT = 16 * 1024    # raw command chars kept for the summary (a longer command keeps a safe prefix only)
STATUS_EVENTS_PER_ITEM = 4
SNAPSHOT_TEXT_SLICE = 8192       # chars per snapshot text frame (bounded working space while a snapshot is sent)
STREAM_ABORT_MARK = "anxious.stream_abort_on_shutdown"  # scope["state"] key read by app.serve.StreamAwareH11
ITEM_ID_LIMIT = 128
STRUCTURE_DATA_LIMIT = 4 * 1024
DELTA_DATA_LIMIT = 8 * 1024
SNAPSHOT_DATA_LIMIT = 64 * 1024
TEXT_CHUNK = 4 * 1024            # text per item.delta before serialization
BUCKET_CAPACITY, BUCKET_RATE = 20.0, 10.0
TEXT_TICK, OUTPUT_TICK = 0.1, 0.5
GRACE_SECONDS = 120
MAX_SUBSCRIBERS = 6
SNAPSHOTS_PER_TURN = 2
CONNECTION_TURN_BYTES = 6 * 1024 * 1024
HEARTBEAT_SECONDS = 15
SAMPLE_SECONDS, SAMPLE_RING = 1.0, 600

STREAMED_KINDS = {"agentMessage", "commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall"}
STATUS_VALUES = {"inProgress", "completed", "failed", "declined", "approval", "interrupted"}


def text_enabled():
    value = os.environ.get("RUNNER_STREAM_TEXT", "false")
    if value not in {"true", "false"}:
        raise RuntimeError("RUNNER_STREAM_TEXT must be true or false")
    return value == "true"


def trace_enabled():
    return os.environ.get("STREAM_TRACE", "false") == "true"


def parse_cursor(value):
    """`<requestId>:<seq>` (the SSE id) or None. Anything else is ValueError (400 at both endpoints)."""
    if value is None or value == "":
        return None
    if len(value) > 80 or value.count(":") != 1:
        raise ValueError("invalid cursor")
    request, seq = value.split(":")
    import uuid
    if str(uuid.UUID(request)) != request or not seq.isdigit() or len(seq) > 12:
        raise ValueError("invalid cursor")
    return request, int(seq)


def utf8(value):
    return len(value.encode("utf-8", "surrogatepass"))


def data_bytes(data):
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8", "surrogatepass")


def frame(kind, data, event_id=None):
    head = ("id: " + event_id + "\n" if event_id else "") + "event: " + kind + "\ndata: "
    return head.encode() + data_bytes(data) + b"\n\n"


class SanitizeUnavailable(Exception):
    pass


class Sanitizer:
    """The runner auth's projection journal, read for the holdback. `auth=None` (fixture/test runner) has no recorded
    literals. Any failure is SanitizeUnavailable: the caller stops text for the turn (fail-closed)."""

    def __init__(self, auth):
        self.auth = auth

    def generation(self):
        """(journal identity, literal count, HoldPolicy). Computed before and after a plan; a change (a literal, or the
        prefix fingerprint file, design r4) discards the plan. The journal unreadable -> SanitizeUnavailable (text
        stops); a fingerprint problem only makes long literals legacy (stream_prefix)."""
        if self.auth is None:
            return (None, 0, HoldPolicy.none())
        for _ in range(3):  # `secrets` can change size while another thread installs a credential
            try:
                self.auth.refresh_projection_secrets()  # current secrets enter the journal (and fingerprints) first
                journal = self.auth.journal
                count, _ = journal.generation()
                current = [value for value in list(self.auth.secrets) if isinstance(value, str)]
                return (id(journal), count, hold_policy(journal, getattr(self.auth, "control", None), current))
            except RuntimeError:
                continue
            except Exception:
                break
        raise SanitizeUnavailable()

    def spans(self, value):
        if self.auth is None or not value:
            return []
        try:
            return self.auth.journal.spans(value)
        except Exception:
            raise SanitizeUnavailable() from None

    def whole(self, value):
        """A complete string (command summary, final text): the same as the record's sanitize."""
        if self.auth is None:
            return value
        try:
            return self.auth.sanitize(value)
        except Exception:
            raise SanitizeUnavailable() from None


class Holdback:
    """One item's unemitted raw text (design r3 B1). Pure: `plan` reads, `commit` applies."""

    def __init__(self):
        self.raw = ""        # raw[e:] of the design; everything before was emitted (sanitized)
        self.joined = False  # the last emission ended with a marker whose range ended exactly at e
        self.version = 0     # commits so far: a plan made before another commit is stale

    def feed(self, text):
        self.raw += text

    @staticmethod
    def plan(window, spans, hold, final, joined):
        """(emitted text, consumed raw chars, joined after). `spans` are the merged ranges of `window`. `hold` is the
        design r4 HoldPolicy (its `start` gives the cut before the left-only rule) or, as in r3, the longest literal
        length L (cut at len - (L - 1))."""
        if final:
            c0 = len(window)
        elif isinstance(hold, HoldPolicy):
            c0 = hold.start(window)
        else:
            c0 = max(0, len(window) - (max(hold, 1) - 1))
        cut = c0
        for start, end in spans:
            if start < c0 < end:
                cut = start  # never right: the crossing range waits whole
                break
        parts, previous, last_end = [], 0, None
        for start, end in spans:
            if end > cut:
                break
            parts.append(window[previous:start])
            if not (start == 0 and joined):
                parts.append(MARKER)  # a range touching the previous emission's marker merges into it
            previous, last_end = end, end
        parts.append(window[previous:cut])
        if cut == 0:
            return "", 0, joined
        return "".join(parts), cut, last_end == cut

    def reset(self):
        """Drop the unemitted text (discard, fail-closed). Any plan made before is stale."""
        self.raw, self.joined = "", False
        self.version += 1

    def commit(self, consumed, joined, window_length):
        # Only appends happen while a plan runs, so raw[:window_length] is still the planned window.
        self.raw = self.raw[consumed:] if consumed else self.raw
        self.version += 1
        self.joined = joined if consumed else self.joined


class Item:
    def __init__(self, identity, kind):
        self.identity, self.kind = identity, kind
        self.status = "inProgress"
        self.summary = {}
        self.text = Holdback()
        self.output = Holdback()
        self.text_out = []          # emitted sanitized agentMessage text (snapshot / display-only source)
        self.text_bytes = 0
        self.unsent = ""            # sanitized text not yet in an event (rate limit)
        self.output_tail = ""       # last OUTPUT_ITEM_LIMIT bytes of sanitized output (snapshot)
        self.output_sent = 0
        self.output_dropped = 0
        self.output_unsent = ""
        self.completed = False
        self.meta_bytes = 0

    def text_bytes_chars(self):
        return sum(len(chunk) for chunk in self.text_out)


class Turn:
    def __init__(self, session, request):
        self.session, self.request = session, request
        self.thread = None
        self.turn_id = None
        self.verified = False
        self.pre = []               # structural events before the model check passed
        self.pre_bytes = 0
        self.seq = 0
        self.events = deque()       # (seq, frame bytes)
        self.event_bytes = 0
        self.floor = 0              # events with seq <= floor are gone (snapshot instead)
        self.items, self.order = {}, []
        self.state = "running"
        self.result = None
        self.stopped = None
        self.discarded = False
        self.completed_at = None
        self.tokens, self.refilled = BUCKET_CAPACITY, time.monotonic()
        self.next_output = 0.0
        self.raw_bytes = 0
        self.started_at = time.monotonic()
        self.stats = {"deltaIn": {}, "eventsOut": 0, "eventBytes": 0, "flushes": 0, "compactions": 0,
                      "firstDeltaMs": None, "firstEventMs": None, "holdbackPeakChars": 0, "legacyL": 0, "cutDelayPeakChars": 0,
                      "bufferPeakBytes": 0, "outputDroppedBytes": 0, "subscribersPeak": 0, "staleFlushes": 0}
        self.samples = deque(maxlen=SAMPLE_RING)
        self.peaks = {"rssKb": 0, "messagesQueue": 0, "flushLagMs": 0}


    def retained(self):
        items = sum(item.meta_bytes + item.text_bytes + utf8(item.unsent) + utf8(item.output_tail)
                    + utf8(item.output_unsent) + utf8(item.text.raw) + utf8(item.output.raw)
                    + utf8(getattr(item, "summary_raw", "")) + utf8(json.dumps(item.summary, ensure_ascii=False))
                    for item in self.items.values())

        return self.event_bytes + items + self.pre_bytes  # snapshots are lazy views: nothing retained


class SnapshotView:
    """References captured for one subscriber's snapshot at seq N (Hub.snapshot). `cancel` drops them all."""

    def __init__(self, subscription, request, seq, result):
        self.subscription, self.request, self.seq, self.result = subscription, request, seq, result
        self.items = []  # (itemId, kind, status, summary, text_out list, chars sent by N, had output)
        self.cancelled = False
        subscription.view = self

    def __iter__(self):
        return iter(self.items)

    def cancel(self):
        """Lock held. The turn left the runner: release, and let the stream resynchronize once (sync{expired})."""
        self.cancelled, self.items = True, []
        subscription, self.subscription = self.subscription, None
        if subscription is not None:
            if subscription.request == self.request:
                subscription.request, subscription.cursor = None, 0
            subscription.notify()


class Subscription:
    def __init__(self, hub, session):
        self.hub, self.session = hub, session
        self.request, self.cursor = None, 0
        self.pending = None   # the rest of a snapshot (a generator), sent before any later event
        self.view = None
        self.snapshots = {}
        self.sent = {}
        self.closed = None
        self.loop = None
        self.wake = None

    def notify(self):
        if self.loop is not None and self.wake is not None:
            try:
                self.loop.call_soon_threadsafe(self.wake.set)
            except RuntimeError:
                pass

    def stop(self):
        """Shutdown/drain: ends the HTTP response itself, even one stuck sending to a slow reader (review 8debb4f)."""
        stop_event = getattr(self, "stop_event", None)
        if self.loop is not None and stop_event is not None:
            try:
                self.loop.call_soon_threadsafe(stop_event.set)
            except RuntimeError:
                pass


class StoppableStreamingResponse:
    """A StreamingResponse raced against `stop_event` (review 8debb4f). The body generator can be suspended inside
    ASGI send (backpressure from a reader that stopped reading) where no flag of its own is ever seen; on the event
    the whole response task is cancelled, then the body generator is closed so its own cleanup runs, then
    `on_close`. Used by the runner's /events and the API relay; other responses are untouched."""

    def __new__(cls, content, stop_event, on_close=None, **kwargs):
        from starlette.responses import StreamingResponse

        class Racing(StreamingResponse):
            async def __call__(self, scope, receive, send):
                # Marks the connection for app.serve.StreamAwareH11: at server shutdown it is aborted instead of
                # flushed (a reader that stopped reading would hold a graceful close forever). Other responses: none.
                if isinstance(scope.get("state"), dict):
                    scope["state"][STREAM_ABORT_MARK] = True
                task = asyncio.ensure_future(StreamingResponse.__call__(self, scope, receive, send))
                stopper = asyncio.ensure_future(stop_event.wait()) if stop_event is not None else None
                try:
                    waiting = {task} | ({stopper} if stopper is not None else set())
                    done, _ = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
                    if task in done:
                        task.result()
                    else:
                        task.cancel()
                        try:
                            await task
                        except BaseException:
                            pass
                finally:
                    if stopper is not None:
                        stopper.cancel()
                    if not task.done():
                        task.cancel()
                    closer = getattr(self.body_iterator, "aclose", None)
                    if closer is not None:
                        try:
                            await closer()
                        except BaseException:
                            pass
                    if on_close is not None:
                        on_close()
        return Racing(content, **kwargs)


class Hub:
    """Per Native. `lock` is held only for appends and short copies; sanitize runs outside it (design r3 M1)."""

    def __init__(self, auth, queue_size=None, work=None, background=True):
        self.sanitizer = Sanitizer(auth)
        self.queue_size = queue_size or (lambda: 0)
        self.work = work
        self.lock = threading.RLock()
        self.active = None
        self.previous = {}           # session -> completed Turn in grace
        self.views = {}              # request -> live SnapshotViews (cancelled when the turn leaves)
        self.subscribers = set()
        self.text = text_enabled()
        self.wakeup = threading.Event()
        self.closed = False
        self.thread = None
        self.background = background  # tests drive flush() themselves
        # One flush at a time (flusher thread vs the turn thread's final pass): a plan is committed by the pass that made it.
        self.flush_lock = threading.Lock()

    # --- turn lifecycle (turn thread) -----------------------------------------------------------------------------

    def begin(self, session, request):
        """Returns the Turn: the caller names it explicitly in bind/verified/partial_items/complete (review stage-1)."""
        with self.lock:
            turn = self.active = Turn(session, request)
            self.ensure_flusher()
        self.wakeup.set()
        return turn

    def bind(self, thread=None, turn_id=None, turn=None):
        with self.lock:
            turn = turn or self.active
            if turn is not None:
                turn.thread = thread or turn.thread
                turn.turn_id = turn_id or turn.turn_id

    def verified(self, turn=None):
        """turn/start answered with the pinned model: structural events may leave the buffer."""
        with self.lock:
            turn = turn or self.active
            if turn is None or turn.verified or turn.discarded:
                return
            turn.verified = True
            self.append(turn, "turn.started", {})
            for kind, data in turn.pre:
                self.append(turn, kind, data)
            turn.pre, turn.pre_bytes = [], 0

    def discard(self, reason):
        with self.lock:
            turn = self.active
            if turn is None or turn.discarded:
                return
            turn.discarded = True
            self.cancel_views(turn.request)
            for item in turn.items.values():
                item.text.reset()
                item.unsent, item.text_out, item.text_bytes = "", [], 0
            if turn.verified:
                self.append(turn, "turn.discard", {"reason": reason})

    def stop(self, turn, reason):
        """Stops streamed text and new item events for the turn; turn events continue. Caller holds the lock."""
        if turn.stopped is None:
            turn.stopped = reason
            if turn.verified:
                self.append(turn, "stream.stopped", {"reason": reason})

    def complete(self, state, turn_status, error_code, turn=None):
        """After the record is stored and saved: `turn`'s last event (run_turn always names it). Only this turn: a turn
        admitted meanwhile is never completed or cleared by it. The runner holds at most one completed turn (design:
        active 1 + grace 1)."""
        turn = turn or self.active
        if turn is None or turn.state == "completed":
            return
        self.flush(turn, final=True)
        with self.lock:
            if not turn.verified and not turn.discarded:
                # Never verified (failed before turn/start answered): only the turn events, no item or text.
                turn.verified = True
                turn.pre, turn.pre_bytes = [], 0
                for item in turn.items.values():
                    item.text.reset()
                    item.output.reset()
                    item.unsent, item.output_unsent, item.text_out, item.text_bytes = "", "", [], 0
                turn.items, turn.order = {}, []
                self.append(turn, "turn.started", {})
            turn.state = "completed"
            turn.result = {"state": state, "turnStatus": turn_status, "errorCode": error_code}
            self.append(turn, "turn.completed", dict(turn.result))
            turn.completed_at = time.monotonic()
            for evicted in self.previous.values():
                if evicted is not turn:
                    self.cancel_views(evicted.request)  # its slow subscribers stop holding it
            self.previous = {turn.session: turn}  # one completed turn per runner, any session
            if self.active is turn:
                self.active = None
            for subscription in list(self.subscribers):
                subscription.notify()
        self.log_summary(turn)

    # --- native notifications (turn thread, O(1) work) ------------------------------------------------------------

    def note(self, message, approval=False):
        try:
            self._note(message, approval)
        except Exception:
            with self.lock:
                if self.active is not None:
                    self.stop(self.active, "internal")

    def _note(self, message, approval):
        method, params = message.get("method"), message.get("params")
        if not isinstance(params, dict):
            return
        with self.lock:
            turn = self.active
            if turn is None or turn.discarded or params.get("threadId") != turn.thread:
                return
            if turn.turn_id is not None and params.get("turnId") not in (None, turn.turn_id):
                return
            if approval:
                item = turn.items.get(params.get("itemId"))
                if item is None or item.completed:
                    return
                item.status = "approval" if approval == "requested" else "inProgress"
                item.statuses = getattr(item, "statuses", 0) + 1
                if item.statuses > STATUS_EVENTS_PER_ITEM:  # bounded per item: the send estimate holds
                    return
                if turn.stopped is None and not hasattr(item, "summary_raw"):  # else sent after its item.started
                    self.structural(turn, "item.status", {"itemId": item.identity, "status": item.status})
                return
            if method in {"item/started", "item/completed"}:
                raw = params.get("item")
                if not isinstance(raw, dict) or raw.get("type") not in STREAMED_KINDS:
                    return
                identity = raw.get("id")
                if not isinstance(identity, str) or not 1 <= len(identity) <= ITEM_ID_LIMIT:
                    return
                item = turn.items.get(identity)
                if method == "item/started" and item is None:
                    if turn.stopped is not None:
                        return
                    if len(turn.items) >= MAX_ITEMS:
                        self.stop(turn, "budget")
                        return
                    command = raw.get("command") if raw["type"] == "commandExecution" and isinstance(raw.get("command"), str) else None
                    item = turn.items[identity] = Item(identity, raw["type"])
                    turn.order.append(identity)
                    item.meta_bytes = 256 + utf8(identity)
                    # Charged before anything is kept (review stage-1): the whole command counts against the budget;
                    # over it, the item keeps its metadata only and the turn's stream stops.
                    if command is not None and turn.retained() + utf8(command) > TURN_BUDGET:
                        self.stop(turn, "budget")
                        return
                    if command is not None:
                        # Sanitized by the flusher before any event: whole when complete, otherwise only the holdback-safe
                        # part of a SUMMARY_RAW_LIMIT prefix (a cut could split a literal).
                        item.summary_raw = command[:SUMMARY_RAW_LIMIT]
                        item.summary_partial = len(command) > SUMMARY_RAW_LIMIT
                    if raw["type"] == "fileChange" and isinstance(raw.get("changes"), list):
                        item.summary = {"changes": len(raw["changes"])}
                    item.meta_bytes = 256 + utf8(identity)
                    if self.over_budget(turn):
                        return
                    if not hasattr(item, "summary_raw"):
                        self.structural(turn, "item.started", {"itemId": identity, "kind": item.kind, **item.summary})
                elif method == "item/completed" and item is not None:
                    status = raw.get("status") if raw.get("status") in STATUS_VALUES else "completed"
                    item.status, item.completed = status, True
                    if item.kind == "agentMessage" and not item.text.raw and not item.text_out and isinstance(raw.get("text"), str):
                        # No deltas were seen (or none arrive for this kind): the completed text goes through the
                        # same holdback, final.
                        self.feed(turn, item, raw["text"], output=False)
                self.wakeup.set()
                return
            delta = params.get("delta")
            if method not in {"item/agentMessage/delta", "item/commandExecution/outputDelta"} or not isinstance(delta, str):
                return
            item = turn.items.get(params.get("itemId"))
            if item is None:
                return
            self.feed(turn, item, delta, output=method != "item/agentMessage/delta")

    def feed(self, turn, item, delta, output):
        stats = turn.stats["deltaIn"].setdefault(item.kind, {"count": 0, "bytes": 0})
        size = utf8(delta)
        stats["count"] += 1
        stats["bytes"] += size
        if turn.stats["firstDeltaMs"] is None:
            turn.stats["firstDeltaMs"] = round((time.monotonic() - turn.started_at) * 1000, 3)
        if turn.stopped is not None:
            return
        if size > DELTA_LIMIT:  # checked before the append (design r3 M1)
            self.stop(turn, "budget")
            return
        if turn.retained() + size > TURN_BUDGET:
            self.stop(turn, "budget")
            return
        (item.output if output else item.text).feed(delta)
        turn.raw_bytes += size

    def over_budget(self, turn):
        if turn.retained() > TURN_BUDGET:
            self.stop(turn, "budget")
            return True
        return False

    # --- events (lock held) ---------------------------------------------------------------------------------------

    def structural(self, turn, kind, data):
        if turn.verified:
            self.append(turn, kind, data)
        else:
            turn.pre.append((kind, data))
            turn.pre_bytes += len(data_bytes(data))

    def append(self, turn, kind, data):
        data = {"requestId": turn.request, **data}
        body = data_bytes(data)
        limit = DELTA_DATA_LIMIT if kind in {"item.delta", "item.output"} else STRUCTURE_DATA_LIMIT
        if len(body) > limit:
            raise ValueError("stream event over its size limit")
        turn.seq += 1
        encoded = frame(kind, data, turn.request + ":" + str(turn.seq))
        turn.events.append((turn.seq, encoded))
        turn.event_bytes += len(encoded)
        turn.stats["eventsOut"] += 1
        turn.stats["eventBytes"] += len(encoded)
        if turn.stats["firstEventMs"] is None and kind == "item.delta":
            turn.stats["firstEventMs"] = round((time.monotonic() - turn.started_at) * 1000, 3)

        while len(turn.events) > MAX_EVENTS or turn.event_bytes > MAX_EVENT_BYTES:
            seq, old = turn.events.popleft()
            turn.event_bytes -= len(old)
            turn.floor = seq
            turn.stats["compactions"] += 1
        turn.stats["bufferPeakBytes"] = max(turn.stats["bufferPeakBytes"], turn.retained())
        for subscription in list(self.subscribers):
            subscription.notify()

    # --- flusher (own thread; sanitize outside the lock) ----------------------------------------------------------

    def ensure_flusher(self):
        if self.background and (self.thread is None or not self.thread.is_alive()):
            self.thread = threading.Thread(target=self.run, daemon=True)
            self.thread.start()

    def run(self):
        next_sample = 0.0
        while not self.closed:
            self.wakeup.wait(TEXT_TICK)
            self.wakeup.clear()
            turn = self.active
            if turn is not None:
                started = time.monotonic()
                try:
                    self.flush(turn, final=False)
                except Exception:
                    with self.lock:
                        self.stop(turn, "internal")
                if time.monotonic() >= next_sample:
                    next_sample = time.monotonic() + SAMPLE_SECONDS
                    self.sample(turn, (time.monotonic() - started) * 1000)
            self.expire()

    def sample(self, turn, lag_ms):
        rss = 0
        try:
            with open("/proc/self/status") as status:
                for line in status:
                    if line.startswith("VmRSS:"):
                        rss = int(line.split()[1])
        except (OSError, ValueError):
            pass
        try:
            queued = int(self.queue_size())
        except Exception:
            queued = -1
        value = {"rssKb": rss, "messagesQueue": queued, "flushLagMs": round(lag_ms, 3),
                 "bufferBytes": turn.retained()}
        turn.samples.append(value)
        for key in ("rssKb", "messagesQueue", "flushLagMs"):
            turn.peaks[key] = max(turn.peaks[key], value[key])

    def expire(self):
        now = time.monotonic()
        with self.lock:
            for session, turn in list(self.previous.items()):
                if now - turn.completed_at > GRACE_SECONDS:
                    del self.previous[session]
                    self.cancel_views(turn.request)

    def flush(self, turn, final):
        with self.flush_lock:
            self._flush(turn, final)

    def _flush(self, turn, final):
        """One pass over the turn's items. `final` sanitizes every remaining raw char (item or turn end)."""
        turn.stats["flushes"] += 1
        with self.lock:
            if turn.discarded:
                return
            work = [(item, item.text.raw, item.output.raw, getattr(item, "summary_raw", None), item.text.version, item.output.version)
                    for item in (turn.items[name] for name in turn.order)
                    if item.text.raw or item.output.raw or hasattr(item, "summary_raw")]
        if not work:
            self.emit_pending(turn, final)
            return
        try:
            before = self.sanitizer.generation()
            plans, delays = [], []
            for item, text, output, summary, text_version, output_version in work:
                done = final or item.completed
                if not done:  # the candidate-cut delay (r4.1), apart from the final buffer peak (holdbackPeakChars)
                    delays += [len(window) - before[2].start(window) for window in (text, output) if window]
                text_plan = Holdback.plan(text, self.sanitizer.spans(text), before[2], done, item.text.joined) if text else None
                output_plan = Holdback.plan(output, self.sanitizer.spans(output), before[2], done, item.output.joined) if output else None
                summary_text = None
                if summary is not None:
                    if getattr(item, "summary_partial", False):  # a prefix of a longer command: never cut a literal
                        summary_text = Holdback.plan(summary, self.sanitizer.spans(summary), before[2], False, False)[0]
                    else:
                        summary_text = self.sanitizer.whole(summary)
                plans.append((item, text, output, text_plan, output_plan, summary_text, text_version, output_version))
            after = self.sanitizer.generation()
        except SanitizeUnavailable:
            with self.lock:
                self.drop_text(turn, work)
            return
        with self.lock:
            turn.stats["legacyL"] = max(turn.stats["legacyL"], before[2].legacy)  # r4: L_legacy (verified)
            turn.stats["cutDelayPeakChars"] = max([turn.stats["cutDelayPeakChars"]] + delays)
            if before != after:
                # A literal was observed meanwhile: nothing of this pass is published (text, output or summary).
                turn.stats["staleFlushes"] += 1
                if final:
                    self.drop_text(turn, work)  # no next pass to plan again: fail closed
                return
            for item, text, output, text_plan, output_plan, summary_text, text_version, output_version in plans:
                if summary_text is not None:
                    summary = {"command": self.clip(summary_text)} if self.text else {}
                    del item.summary_raw
                    item.summary = summary
                    if turn.stopped is None and not turn.discarded:
                        self.structural(turn, "item.started", {"itemId": item.identity, "kind": item.kind, **summary})
                        if item.status == "approval":
                            self.structural(turn, "item.status", {"itemId": item.identity, "status": "approval"})
                if text_plan is not None and item.text.version == text_version:
                    emitted, consumed, joined = text_plan
                    item.text.commit(consumed, joined, len(text))
                    if emitted:
                        self.keep_text(turn, item, emitted)
                if output_plan is not None and item.output.version == output_version:
                    emitted, consumed, joined = output_plan
                    item.output.commit(consumed, joined, len(output))
                    if emitted:
                        self.keep_output(turn, item, emitted)
                turn.stats["holdbackPeakChars"] = max(turn.stats["holdbackPeakChars"], len(item.text.raw), len(item.output.raw))
        self.emit_pending(turn, final)

    def drop_text(self, turn, work):
        """Fail closed for the turn: stop, and drop every unpublished raw text and command summary. Lock held."""
        self.stop(turn, "sanitize")
        for item, *_ in work:
            item.text.reset()
            item.output.reset()
            if hasattr(item, "summary_raw"):
                del item.summary_raw

    @staticmethod
    def clip(text):
        """A sanitized summary cut so that its serialized form stays within SUMMARY_LIMIT."""
        while len(data_bytes(text)) > SUMMARY_LIMIT:
            text = text[: max(0, len(text) - max(1, (len(data_bytes(text)) - SUMMARY_LIMIT) // 6 + 1))]
        return text

    def keep_text(self, turn, item, emitted):
        size = utf8(emitted)
        total = sum(other.text_bytes for other in turn.items.values())
        if total + size > TEXT_LIMIT:
            self.stop(turn, "budget")
            return
        item.text_out.append(emitted)
        item.text_bytes += size
        if self.text and turn.stopped is None:
            item.unsent += emitted
        self.over_budget(turn)

    def keep_output(self, turn, item, emitted):
        size = utf8(emitted)
        item.output_tail = (item.output_tail + emitted)[-OUTPUT_ITEM_LIMIT:]
        streamed = sum(other.output_sent + utf8(other.output_unsent) for other in turn.items.values())
        if not self.text or turn.stopped is not None or item.output_sent + utf8(item.output_unsent) + size > OUTPUT_ITEM_LIMIT \
                or streamed + size > OUTPUT_TURN_LIMIT:
            item.output_dropped += size
            turn.stats["outputDroppedBytes"] += size
            return
        item.output_unsent += emitted

    def take_token(self, turn):
        now = time.monotonic()
        turn.tokens = min(BUCKET_CAPACITY, turn.tokens + (now - turn.refilled) * BUCKET_RATE)
        turn.refilled = now
        if turn.tokens >= 1:
            turn.tokens -= 1
            return True
        return False

    def emit_pending(self, turn, final):
        with self.lock:
            if not turn.verified or turn.discarded:
                return
            for name in turn.order:
                item = turn.items[name]
                while item.unsent and turn.stopped is None and (final or self.take_token(turn)):
                    chunk = item.unsent[:TEXT_CHUNK]
                    while len(data_bytes({"requestId": turn.request, "itemId": name, "text": chunk})) > DELTA_DATA_LIMIT:
                        chunk = chunk[: len(chunk) // 2]
                    item.unsent = item.unsent[len(chunk):]
                    self.append(turn, "item.delta", {"itemId": name, "text": chunk})
                if item.output_unsent and turn.stopped is None and (final or time.monotonic() >= turn.next_output) \
                        and (final or self.take_token(turn)):
                    chunk = item.output_unsent[-TEXT_CHUNK:]
                    dropped = utf8(item.output_unsent) - utf8(chunk)
                    item.output_dropped += dropped
                    while len(data_bytes({"requestId": turn.request, "itemId": name, "text": chunk, "truncated": True,
                                          "droppedBytes": item.output_dropped})) > DELTA_DATA_LIMIT:
                        chunk = chunk[len(chunk) // 2:]
                    item.output_sent += utf8(chunk)
                    item.output_unsent = ""
                    turn.next_output = time.monotonic() + OUTPUT_TICK
                    self.append(turn, "item.output", {"itemId": name, "text": chunk,
                                                      "truncated": item.output_dropped > 0,
                                                      "droppedBytes": item.output_dropped})
                if item.completed and not item.text.raw and not item.output.raw and not item.unsent \
                        and not getattr(item, "completed_sent", False) and turn.stopped is None:
                    item.completed_sent = True
                    self.append(turn, "item.completed", {"itemId": name, "status": item.status})

    # --- display-only partial text (design r1 §4, r2 Q3) ----------------------------------------------------------

    def partial_items(self, turn=None):
        """The sanitized agentMessage text of `turn`, after a final pass: [{id, type, text}]."""
        turn = turn or self.active
        if turn is None or turn.discarded or not turn.verified:
            return []
        self.flush(turn, final=True)
        with self.lock:
            if turn.stopped == "sanitize":
                return []
            return [{"id": item.identity, "type": "agentMessage", "text": "".join(item.text_out)}
                    for item in (turn.items[name] for name in turn.order)
                    if item.kind == "agentMessage" and item.text_out]

    # --- subscribers ----------------------------------------------------------------------------------------------

    def subscribe(self, session, after):
        """Design r2 M2 table. Returns (subscription, initial frames); the snapshot and the cursor are set atomically."""
        with self.lock:
            if len(self.subscribers) >= MAX_SUBSCRIBERS:
                return None, []
            subscription = Subscription(self, session)
            self.subscribers.add(subscription)
            if self.active is not None:
                self.active.stats["subscribersPeak"] = max(self.active.stats["subscribersPeak"], len(self.subscribers))
            frames = self.position(subscription, after)
            return subscription, frames

    def unsubscribe(self, subscription):
        with self.lock:
            self.subscribers.discard(subscription)
            view, subscription.view, subscription.pending = subscription.view, None, None
            if view is not None:  # a snapshot never drained: release its references now
                registered = self.views.get(view.request)
                if registered is not None:
                    registered.discard(view)
                    if not registered:
                        del self.views[view.request]
                view.cancelled, view.items = True, []

    def turn_for(self, session):
        if self.active is not None and self.active.session == session:
            return self.active
        return None

    def position(self, subscription, after):
        session = subscription.session
        active = self.turn_for(session)
        previous = self.previous.get(session)
        request, seq = after if after else (None, None)
        if active is not None:
            if request == active.request and active.floor <= seq <= active.seq:
                return self.follow(subscription, active, seq)
            return self.snapshot(subscription, active)
        if previous is not None and request == previous.request:
            if previous.floor <= seq <= previous.seq:
                return self.follow(subscription, previous, seq)
            return self.snapshot(subscription, previous)
        if request is not None:
            return [frame("sync", {"reason": "expired"})]
        last = previous.request if previous is not None else None
        return [frame("idle", {"requestId": last, "turnState": previous.state if previous else None})]

    def follow(self, subscription, turn, seq):
        subscription.request, subscription.cursor = turn.request, seq
        return []

    def snapshot(self, subscription, turn):
        count = subscription.snapshots.get(turn.request, 0)
        if count >= SNAPSHOTS_PER_TURN:
            subscription.closed = "overflow"
            return [frame("sync", {"reason": "overflow"})]
        subscription.snapshots[turn.request] = count + 1
        subscription.request, subscription.cursor = turn.request, turn.seq
        # Lazy (review c894465): only snapshot.begin is built here. The rest is a view of references captured at seq N
        # (the item order, each item's status/summary objects and its append-only text_out list with the length sent
        # by N) and turns into frames one at a time as the stream sends them; nothing is copied or cached. Command
        # output is never in a snapshot (its tail is a sliding window): the item says it was omitted.
        view = SnapshotView(subscription, turn.request, turn.seq, dict(turn.result) if turn.result is not None else None)
        if turn.verified and not turn.discarded:  # nothing of an unverified turn (model check) leaves, snapshot included
            for name in turn.order:
                item = turn.items[name]
                if hasattr(item, "summary_raw"):
                    continue
                sent = item.text_bytes_chars() - len(item.unsent) if self.text and turn.stopped is None else 0
                view.items.append((name, item.kind, item.status, item.summary, item.text_out, sent,
                                   item.output_sent > 0 or bool(item.output_unsent)))
        # Registered with the hub: a view of a turn that leaves the runner (evicted from the grace slot, expired,
        # discarded, hub closed) is cancelled, which drops every reference it holds (review 01d30ce).
        self.views.setdefault(turn.request, set()).add(view)
        subscription.pending = self.snapshot_rest(view)
        return [frame("snapshot.begin", {"requestId": turn.request, "turnState": turn.state, "seq": turn.seq,
                                         **({"stopped": turn.stopped} if turn.stopped else {})})]

    def cancel_views(self, request):
        """Lock held. Views of `request` drop their references now; their streams get one sync{expired}."""
        for view in self.views.pop(request, ()):
            view.cancel()

    def snapshot_rest(self, view):
        """The snapshot after snapshot.begin, one frame at a time. Bounded working space (review 01d30ce): text is read
        in slices of at most SNAPSHOT_TEXT_SLICE chars straight from the item's chunk list, and no local holds the
        turn's text or summary across a yield (everything is re-read from `view` by index), so a cancelled view
        releases them even while its consumer is suspended."""
        request, i, chunk_index, position, done, meta_sent = view.request, 0, 0, 0, 0, False
        try:
            while True:
                if view.cancelled:
                    yield frame("sync", {"reason": "expired"})
                    return
                if i >= len(view.items):
                    break
                name, kind, status, summary, chunks, sent, had_output = view.items[i]
                if not meta_sent:
                    encoded = frame("snapshot.item", {"requestId": request, "itemId": name, "kind": kind, "status": status,
                                                      **summary, **({"output": {"omitted": True}} if had_output else {})})
                    meta_sent = True
                    del summary, chunks
                    yield encoded
                    continue
                if done >= sent or chunk_index >= len(chunks):
                    i, chunk_index, position, done, meta_sent = i + 1, 0, 0, 0, False
                    del summary, chunks
                    continue
                chunk = chunks[chunk_index]
                take = min(SNAPSHOT_TEXT_SLICE, len(chunk) - position, sent - done)
                piece = chunk[position:position + take]
                while len(data_bytes({"requestId": request, "itemId": name, "text": piece})) > SNAPSHOT_DATA_LIMIT:
                    piece = piece[: len(piece) // 2]
                position, done = position + len(piece), done + len(piece)
                if position >= len(chunk):
                    chunk_index, position = chunk_index + 1, 0
                encoded = frame("snapshot.item", {"requestId": request, "itemId": name, "text": piece})
                del summary, chunks, chunk, piece
                yield encoded
            if view.result is not None:
                yield frame("turn.completed", {"requestId": request, **view.result})
            yield frame("snapshot.end", {"requestId": request, "seq": view.seq}, request + ":" + str(view.seq))
        finally:
            with self.lock:
                registered = self.views.get(request)
                if registered is not None:
                    registered.discard(view)
                    if not registered:
                        del self.views[request]
            view.items = []

    def drain_snapshot(self, subscription):
        """The pending snapshot frames, materialized (tests and small callers only; the stream iterates lazily)."""
        pending, subscription.pending = subscription.pending, None
        return list(pending) if pending is not None else []

    def read(self, subscription):
        """[(seq or None, frame)] after the subscription's cursor; follows into the session's next turn."""
        with self.lock:
            if subscription.closed:
                return []
            turn = self.turn_for(subscription.session)
            current = None
            for candidate in (turn, self.previous.get(subscription.session)):
                if candidate is not None and candidate.request == subscription.request:
                    current = candidate
            finished = (current is not None and current is not turn and current.state == "completed"
                        and subscription.cursor >= current.seq)
            if current is None or finished:
                if turn is None or turn is current:
                    if current is None and subscription.request is not None:
                        # The followed turn left the grace slot (another turn completed meanwhile): resynchronize.
                        subscription.request, subscription.cursor = None, 0
                        return [(None, frame("sync", {"reason": "expired"}))]
                    return []
                # A new turn of this session started since: from its first event, or a snapshot once compacted.
                if turn.floor == 0:
                    subscription.request, subscription.cursor = turn.request, 0
                    current = turn
                else:
                    return [(None, encoded) for encoded in self.snapshot(subscription, turn)]
            if subscription.cursor < current.floor:
                return [(None, encoded) for encoded in self.snapshot(subscription, current)]
            frames = []
            for seq, encoded in current.events:
                if seq > subscription.cursor:
                    frames.append((seq, encoded))
                    subscription.cursor = seq
            return frames

    def hello(self, session, mapping_entry):
        with self.lock:
            active = self.turn_for(session)
            last = mapping_entry.get("request") if mapping_entry else None
            return {"sessionId": session, "activeRequestId": active.request if active else None,
                    "lastRequestId": None if active else last}

    def close(self):
        """Drain/shutdown: every open stream ends now (it does not wait for its heartbeat)."""
        self.closed = True
        with self.lock:
            for request in list(self.views):
                self.cancel_views(request)
        self.wakeup.set()
        with self.lock:
            for subscription in list(self.subscribers):
                subscription.closed = subscription.closed or "stopping"
                subscription.stop()
                subscription.notify()

    # --- metrics --------------------------------------------------------------------------------------------------

    def log_summary(self, turn):
        try:
            import logging
            logging.getLogger("uvicorn.error").info("runner_stream_summary %s", json.dumps({
                "requestId": turn.request, "text": self.text, "stopped": turn.stopped, "discarded": turn.discarded,
                **turn.stats, "retainedBytes": turn.retained(), "rawBytes": turn.raw_bytes, "items": len(turn.items),
                "peaks": turn.peaks, "samples": len(turn.samples)}, sort_keys=True))
        except Exception:
            pass


async def stream(hub, subscription, initial, hello, stopping):
    """The SSE body: hello, the initial frames, then events as they are appended; a heartbeat every 15 s."""
    subscription.loop, subscription.wake = asyncio.get_running_loop(), asyncio.Event()
    trace = trace_enabled()
    def lazy_snapshot():
        """The rest of a pending snapshot, one frame at a time ((None, frame) pairs, never all at once)."""
        pending, subscription.pending = subscription.pending, None
        for encoded in pending or ():
            yield None, encoded

    try:
        yield frame("hello", hello)
        for encoded in initial:
            yield encoded
        while subscription.closed is None and not stopping():
            if subscription.pending is not None:  # a snapshot's rest goes out before any later event
                frames = lazy_snapshot()
            else:
                frames = hub.read(subscription)  # a snapshot here is its begin frame; the rest is drained next round
            if frames == []:
                try:
                    await asyncio.wait_for(subscription.wake.wait(), HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield b": hb\n\n"
                subscription.wake.clear()
                continue
            for seq, encoded in frames:
                key = subscription.request
                sent = subscription.sent.get(key, 0) + len(encoded)
                if sent > CONNECTION_TURN_BYTES:  # checked before the frame goes out
                    yield frame("sync", {"reason": "overflow"})
                    return
                subscription.sent[key] = sent
                if trace and seq is not None:  # payload-free: seq and send time only (design r2 M5)
                    yield (": t " + str(seq) + " " + str(int(time.time() * 1000)) + "\n").encode()
                yield encoded
            if subscription.closed:  # an overflow snapshot refusal already carried its sync frame
                return
    finally:
        hub.unsubscribe(subscription)
