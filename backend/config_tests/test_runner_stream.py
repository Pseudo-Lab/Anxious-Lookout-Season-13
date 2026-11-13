"""#90 runner stream buffer (backend/runner/stream.py). Pure: no Codex binary, no network, no DB.

The holdback tests compare against the oracle the design promises (r3 B1): whatever the split, the emitted text joined
is exactly the existing sanitizer's result on the whole text. They use the real ProjectionJournal.
"""
import json
import random
import uuid

import pytest

from runner import stream as stream_module
from runner.projection import ProjectionJournal
from runner.stream import Holdback, Hub, MARKER

OWNER, TRIAL = str(uuid.uuid4()), str(uuid.uuid4())


def journal(*literals):
    value = ProjectionJournal.create(OWNER, TRIAL)
    value.observe(set(literals))
    return value


def run_holdback(record, chunks, longest=None):
    longest = longest if longest is not None else record.generation()[1]
    holdback, out = Holdback(), []
    for chunk in chunks:
        holdback.feed(chunk)
        emitted, consumed, joined = Holdback.plan(holdback.raw, record.spans(holdback.raw), longest, False, holdback.joined)
        holdback.commit(consumed, joined, len(holdback.raw))
        out.append(emitted)
    emitted, consumed, joined = Holdback.plan(holdback.raw, record.spans(holdback.raw), longest, True, holdback.joined)
    holdback.commit(consumed, joined, len(holdback.raw))
    out.append(emitted)
    assert holdback.raw == ""
    return out


def splits(text, rng, count):
    for _ in range(count):
        cuts = sorted(rng.sample(range(1, len(text)), rng.randint(0, min(len(text) - 1, 8)))) if len(text) > 1 else []
        yield [text[a:b] for a, b in zip([0] + cuts, cuts + [len(text)])]


CASES = {
    # review r2 counterexample: complete A overlapping incomplete B
    "review-overlap": (("abcdefghijklmnop", "klmnopqrstuvwxyz"), "abcdefghijklmnopqrstuvwxyz and more"),
    "nested": (("0123456789abcdef", "45678"), "xx0123456789abcdefyy 45678 zz"),
    "chained": (("aaaabbbb", "bbbbcccc", "ccccdddd"), "--aaaabbbbccccdddd--"),
    "adjacent-short": (("x", "y"), "axyb xy yx"),
    "whitespace-literal": (("alpha beta", "gamma"), "say alpha beta then alpha  beta and gammagamma"),
    "korean": (("비밀토큰값", "토큰"), "앞 비밀토큰값 뒤 토큰 끝"),
    "long-and-short": (("T" * 40 + "end", "Tend"), "x" + "T" * 40 + "end" + "Tend" + "T" * 39),
    "prefix-only": (("secretvalue123456",), "secretvalue12345 secretvalue123456"),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_holdback_matches_whole_sanitize_for_every_split(name):
    literals, text = CASES[name]
    record = journal(*literals)
    expected = record.text(text)
    # every single split position, one char at a time, and random chunkings (fixed seed)
    for position in range(1, len(text)):
        assert "".join(run_holdback(record, [text[:position], text[position:]])) == expected
    assert "".join(run_holdback(record, list(text))) == expected
    for chunks in splits(text, random.Random(name), 200):
        assert "".join(run_holdback(record, chunks)) == expected


@pytest.mark.parametrize("name", sorted(CASES))
def test_no_emitted_chunk_carries_part_of_a_literal(name):
    """Stronger than the joined oracle: each emission alone never holds a literal fragment that the whole text
    redacts (a fragment of length >= 2 of any literal that overlaps a redacted range)."""
    literals, text = CASES[name]
    record = journal(*literals)
    for chunks in [list(text)] + list(splits(text, random.Random("frag" + name), 100)):
        emitted = run_holdback(record, chunks)
        position = 0
        expected = record.text(text)
        for piece in emitted:
            assert expected[position:position + len(piece)] == piece  # each piece is a slice of the oracle, in order
            position += len(piece)


def test_review_counterexample_trace():
    record = journal("abcdefghijklmnop", "klmnopqrstuvwxyz")
    holdback = Holdback()
    holdback.feed("abcdefghijklmnopqrst")
    plan = Holdback.plan(holdback.raw, record.spans(holdback.raw), 16, False, False)
    assert plan == ("", 0, False)  # c0 = 5 crosses A=[0,16): cut back to 0
    holdback.feed("uvwxyz")
    plan = Holdback.plan(holdback.raw, record.spans(holdback.raw), 16, False, False)
    assert plan == ("", 0, False)  # A∪B = [0,26) crosses c0 = 11
    assert Holdback.plan(holdback.raw, record.spans(holdback.raw), 16, True, False)[0] == MARKER


def test_adjacent_ranges_across_emissions_merge_into_one_marker():
    record = journal("x", "y")
    assert run_holdback(record, ["x", "y"]) in (["[redacted]", "", ""], ["[redacted]", "", ""])
    assert "".join(run_holdback(record, ["x", "y"])) == "[redacted]" == record.text("xy")
    assert "".join(run_holdback(record, ["ax", "yb"])) == record.text("axyb") == "a[redacted]b"


def test_unknown_length_holds_everything():
    record = journal("zz")
    # A larger L than any literal only holds more; it never emits a fragment.
    assert run_holdback(record, ["abc", "zz", "def"], longest=1000)[:-1] == ["", "", ""]


# --- hub -----------------------------------------------------------------------------------------------------------

class FakeAuth:
    def __init__(self, *literals):
        self.journal = journal(*literals)
        self.secrets = set()
        self.fail = False
        self.observed = 0

    def refresh_projection_secrets(self):
        if self.fail:
            raise RuntimeError("synthetic") if self.fail == "runtime" else OSError("synthetic")
        if self.secrets:
            self.journal.observe(set(self.secrets))

    def sanitize(self, value):
        self.refresh_projection_secrets()
        return self.journal.sanitize(value)


SESSION = str(uuid.uuid4())


@pytest.fixture
def text_on(monkeypatch):
    monkeypatch.setenv("RUNNER_STREAM_TEXT", "true")


def new_hub(auth=None, session=SESSION):
    hub = Hub(auth, background=False)
    request = str(uuid.uuid4())
    hub.begin(session, request)
    hub.bind(thread="thread-1", turn_id="turn-1")
    return hub, request


def note(hub, method, **params):
    hub.note({"method": method, "params": {"threadId": "thread-1", "turnId": "turn-1", **params}})


def start_item(hub, identity="msg-1", kind="agentMessage", **extra):
    note(hub, "item/started", item={"type": kind, "id": identity, **extra})


def events(hub, request=None):
    turn = hub.active or next(iter(hub.previous.values()))
    result = []
    for seq, encoded in turn.events:
        lines = encoded.decode().split("\n")
        kind = next(line[7:] for line in lines if line.startswith("event: "))
        data = json.loads(next(line[6:] for line in lines if line.startswith("data: ")))
        result.append((seq, kind, data))
    return result


def test_text_off_by_default_emits_structure_only(monkeypatch):
    monkeypatch.delenv("RUNNER_STREAM_TEXT", raising=False)
    hub, request = new_hub(FakeAuth("secret-token-value"))
    hub.verified()
    start_item(hub)
    note(hub, "item/agentMessage/delta", itemId="msg-1", delta="hello secret-token-")
    note(hub, "item/agentMessage/delta", itemId="msg-1", delta="value world")
    note(hub, "item/completed", item={"type": "agentMessage", "id": "msg-1", "status": "completed"})
    hub.flush(hub.active, final=False)
    kinds = [kind for _, kind, _ in events(hub)]
    assert kinds == ["turn.started", "item.started", "item.completed"]
    # Display-only text is still kept, sanitized.
    assert hub.partial_items() == [{"id": "msg-1", "type": "agentMessage", "text": "hello [redacted] world"}]


def test_invalid_flag_value_is_refused(monkeypatch):
    monkeypatch.setenv("RUNNER_STREAM_TEXT", "TRUE")
    with pytest.raises(RuntimeError):
        Hub(None, background=False)


def test_text_deltas_are_sanitized_across_split_and_carry_request_id(text_on):
    auth = FakeAuth()
    auth.secrets = {"tool-token-0123456789abcdef"}
    hub, request = new_hub(auth)
    hub.verified()
    start_item(hub)
    full = "start tool-token-0123456789abcdef middle tool-token-0123456789abcdef end"
    for index in range(0, len(full), 5):
        note(hub, "item/agentMessage/delta", itemId="msg-1", delta=full[index:index + 5])
        hub.flush(hub.active, final=False)
    note(hub, "item/completed", item={"type": "agentMessage", "id": "msg-1", "status": "completed"})
    hub.flush(hub.active, final=False)
    hub.complete("idle", "completed", None)
    seen = events(hub)
    text = "".join(data["text"] for _, kind, data in seen if kind == "item.delta")
    assert text == auth.journal.text(full) == "start [redacted] middle [redacted] end"
    assert all(data["requestId"] == request for _, _, data in seen)
    assert [kind for _, kind, _ in seen][-2:] == ["item.completed", "turn.completed"]
    raw = b"".join(encoded for _, encoded in hub.previous[SESSION].events)
    assert b"tool-token" not in raw


def test_output_split_secret_and_command_summary(text_on):
    auth = FakeAuth("ghp_SECRETSECRETSECRET")
    hub, request = new_hub(auth)
    hub.verified()
    start_item(hub, "cmd-1", "commandExecution", command="echo ghp_SECRETSECRETSECRET " + "x" * 5000)
    hub.flush(hub.active, final=False)
    for piece in ("out ghp_SECRET", "SECRETSECRET tail\n"):
        note(hub, "item/commandExecution/outputDelta", itemId="cmd-1", delta=piece)
        hub.flush(hub.active, final=False)
    note(hub, "item/completed", item={"type": "commandExecution", "id": "cmd-1", "status": "completed"})
    hub.flush(hub.active, final=True)
    seen = events(hub)
    started = next(data for _, kind, data in seen if kind == "item.started")
    assert started["command"].startswith("echo [redacted] ")
    assert len(json.dumps(started["command"], ensure_ascii=False).encode()) <= stream_module.SUMMARY_LIMIT
    output = "".join(data["text"] for _, kind, data in seen if kind == "item.output")
    assert "SECRET" not in output and "[redacted]" in output


def test_sanitize_failure_stops_text_fail_closed(text_on):
    auth = FakeAuth("abc")
    hub, request = new_hub(auth)
    hub.verified()
    start_item(hub)
    note(hub, "item/agentMessage/delta", itemId="msg-1", delta="some text here")
    auth.fail = "os"
    hub.flush(hub.active, final=False)
    kinds = [kind for _, kind, _ in events(hub)]
    assert "stream.stopped" in kinds and "item.delta" not in kinds
    assert next(data for _, kind, data in events(hub) if kind == "stream.stopped")["reason"] == "sanitize"
    assert hub.partial_items() == []


def test_generation_change_discards_the_plan(text_on):
    auth = FakeAuth("abc")
    hub, request = new_hub(auth)
    hub.verified()
    start_item(hub)
    note(hub, "item/agentMessage/delta", itemId="msg-1", delta="plain words that are long enough")
    calls = {"n": 0}
    original = auth.refresh_projection_secrets

    def rotating():
        calls["n"] += 1
        auth.secrets = {"rotated-literal-" + str(calls["n"])}  # a new literal each time
        original()
    auth.refresh_projection_secrets = rotating
    hub.flush(hub.active, final=False)
    assert hub.active.stats["staleFlushes"] == 1
    assert hub.active.items["msg-1"].text.raw == "plain words that are long enough"  # nothing committed
    hub.flush(hub.active, final=True)
    assert "stream.stopped" in [kind for _, kind, _ in events(hub)]  # final pass with a moving journal: fail closed


def test_events_wait_for_model_check_and_discard(text_on):
    hub, request = new_hub(None)
    start_item(hub)
    note(hub, "item/agentMessage/delta", itemId="msg-1", delta="before verify ")
    hub.flush(hub.active, final=False)
    assert events(hub) == []
    hub.discard("model_unavailable")
    hub.verified()
    assert events(hub) == [] and hub.partial_items() == []


def test_foreign_thread_turn_and_kinds_are_ignored(text_on):
    hub, request = new_hub(None)
    hub.verified()
    hub.note({"method": "item/started", "params": {"threadId": "other", "turnId": "turn-1",
                                                   "item": {"type": "agentMessage", "id": "a"}}})
    hub.note({"method": "item/started", "params": {"threadId": "thread-1", "turnId": "other",
                                                   "item": {"type": "agentMessage", "id": "b"}}})
    start_item(hub, "r-1", "reasoning")
    start_item(hub, "x" * 129)
    assert [kind for _, kind, _ in events(hub)] == ["turn.started"]


def test_budget_limits(text_on, monkeypatch):
    hub, request = new_hub(None)
    hub.verified()
    start_item(hub)
    note(hub, "item/agentMessage/delta", itemId="msg-1", delta="x" * (stream_module.DELTA_LIMIT + 1))
    assert hub.active.stopped == "budget"
    hub, request = new_hub(None)
    hub.verified()
    for index in range(stream_module.MAX_ITEMS + 1):
        start_item(hub, "item-" + str(index), "fileChange", changes=[])
    assert hub.active.stopped == "budget"
    assert len([1 for _, kind, _ in events(hub) if kind == "item.started"]) == stream_module.MAX_ITEMS


def test_event_bytes_are_serialized_utf8_and_bounded(text_on):
    hub, request = new_hub(None)
    hub.verified()
    start_item(hub)
    for _ in range(3):  # one native delta stays under DELTA_LIMIT (64 KiB): 10000 x 3 bytes
        note(hub, "item/agentMessage/delta", itemId="msg-1", delta="한" * 10000)
    note(hub, "item/agentMessage/delta", itemId="msg-1", delta="\u0001" * 2000)
    note(hub, "item/completed", item={"type": "agentMessage", "id": "msg-1", "status": "completed"})
    hub.flush(hub.active, final=True)
    for _, kind, data in events(hub):
        size = len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode())
        assert size <= (stream_module.DELTA_DATA_LIMIT if kind in {"item.delta", "item.output"} else stream_module.STRUCTURE_DATA_LIMIT)
    text = "".join(data["text"] for _, kind, data in events(hub) if kind == "item.delta")
    assert text == "한" * 30000 + "\u0001" * 2000


def test_rate_bucket_merges_instead_of_dropping(text_on):
    hub, request = new_hub(None)
    hub.verified()
    start_item(hub)
    for _ in range(100):
        note(hub, "item/agentMessage/delta", itemId="msg-1", delta="ab")
        hub.flush(hub.active, final=False)
    deltas = [data for _, kind, data in events(hub) if kind == "item.delta"]
    assert len(deltas) <= stream_module.BUCKET_CAPACITY + 2
    hub.flush(hub.active, final=True)
    assert "".join(data["text"] for _, kind, data in events(hub) if kind == "item.delta") == "ab" * 100


# --- reconnect (design r2 M2) --------------------------------------------------------------------------------------

def frames_kinds(frames):
    return [encoded.split(b"\n")[0 if not encoded.startswith(b"id: ") else 1].decode()[7:] for encoded in frames]


def test_no_cursor_gets_snapshot_and_cursor_is_atomic(text_on):
    hub, request = new_hub(None)
    hub.verified()
    start_item(hub)
    note(hub, "item/agentMessage/delta", itemId="msg-1", delta="hello there ")
    hub.flush(hub.active, final=True)
    subscription, frames = hub.subscribe(SESSION, None)
    frames = frames + hub.drain_snapshot(subscription)  # the lazy rest after snapshot.begin
    kinds = frames_kinds(frames)
    assert kinds[0] == "snapshot.begin" and kinds[-1] == "snapshot.end"
    assert subscription.cursor == hub.active.seq
    assert frames[-1].startswith(("id: " + request + ":" + str(hub.active.seq)).encode())
    note(hub, "item/agentMessage/delta", itemId="msg-1", delta="more words ")
    hub.flush(hub.active, final=True)
    later = hub.read(subscription)
    assert [seq for seq, _ in later] == list(range(subscription.cursor - len(later) + 1, subscription.cursor + 1))
    assert later[0][0] == hub.active.seq - len(later) + 1


def test_cursor_rules(text_on):
    hub, request = new_hub(None)
    hub.verified()
    start_item(hub)
    latest = hub.active.seq
    subscription, frames = hub.subscribe(SESSION, (request, 1))
    assert frames == [] and subscription.cursor == 1
    assert frames_kinds(hub.subscribe(SESSION, (request, latest + 5))[1])[0] == "snapshot.begin"
    assert frames_kinds(hub.subscribe(SESSION, (str(uuid.uuid4()), 3))[1])[0] == "snapshot.begin"  # older turn's cursor
    other = hub.subscribe(str(uuid.uuid4()), None)[1]
    assert frames_kinds(other) == ["idle"]
    assert frames_kinds(hub.subscribe(str(uuid.uuid4()), (request, 1))[1]) == ["sync"]


def test_completed_turn_in_grace_then_next_turn(text_on):
    hub, first = new_hub(None)
    hub.verified()
    subscription, _ = hub.subscribe(SESSION, None)
    hub.complete("idle", "completed", None)
    kinds = [encoded.split(b"\n")[1] for _, encoded in hub.read(subscription)]
    assert kinds[-1] == b"event: turn.completed"
    second = str(uuid.uuid4())
    hub.begin(SESSION, second)
    hub.bind(thread="thread-1", turn_id="turn-1")
    hub.verified()
    frames = hub.read(subscription)
    assert subscription.request == second and frames[0][0] == 1
    late, frames = hub.subscribe(SESSION, (first, 1))  # previous turn's cursor while the next runs
    assert frames_kinds(frames)[0] == "snapshot.begin" and late.request == second


def test_completion_snapshot_carries_result(text_on):
    hub, request = new_hub(None)
    hub.verified()
    hub.complete("idle", "interrupted", None)
    subscription, frames = hub.subscribe(SESSION, (request, 10 ** 6))
    frames = frames + hub.drain_snapshot(subscription)
    kinds = frames_kinds(frames)
    assert kinds == ["snapshot.begin", "turn.completed", "snapshot.end"]


def test_snapshot_limit_and_compaction(text_on, monkeypatch):
    monkeypatch.setattr(stream_module, "MAX_EVENTS", 5)
    hub, request = new_hub(None)
    hub.verified()
    subscription, _ = hub.subscribe(SESSION, (request, 1))
    for index in range(20):
        start_item(hub, "f-" + str(index), "fileChange", changes=[])
    assert hub.active.floor > 1
    assert frames_kinds([encoded for _, encoded in hub.read(subscription)])[0] == "snapshot.begin"
    assert frames_kinds(hub.drain_snapshot(subscription))[-1] == "snapshot.end"
    for index in range(20, 40):
        start_item(hub, "f-" + str(index), "fileChange", changes=[])
    assert frames_kinds([encoded for _, encoded in hub.read(subscription)])[0] == "snapshot.begin"
    assert frames_kinds(hub.drain_snapshot(subscription))[-1] == "snapshot.end"
    for index in range(40, 60):
        start_item(hub, "f-" + str(index), "fileChange", changes=[])
    assert frames_kinds([encoded for _, encoded in hub.read(subscription)]) == ["sync"]
    assert subscription.closed == "overflow"


def test_subscriber_cap_and_close(text_on):
    hub, request = new_hub(None)
    subscriptions = [hub.subscribe(SESSION, None)[0] for _ in range(stream_module.MAX_SUBSCRIBERS)]
    assert hub.subscribe(SESSION, None) == (None, [])
    hub.unsubscribe(subscriptions[0])
    assert hub.subscribe(SESSION, None)[0] is not None
    hub.close()
    assert all(subscription.closed == "stopping" for subscription in hub.subscribers)


def test_hello_names_active_or_last():
    hub, request = new_hub(None)
    assert hub.hello(SESSION, {"request": "old"}) == {"sessionId": SESSION, "activeRequestId": request,
                                                       "lastRequestId": None}
    hub.complete("idle", "completed", None)
    assert hub.hello(SESSION, {"request": request}) == {"sessionId": SESSION, "activeRequestId": None,
                                                         "lastRequestId": request}


def test_background_flusher_and_final_pass_never_duplicate(text_on):
    """The flusher thread and the turn thread's final pass race on the same item: the joined text stays exact."""
    import time as clock
    auth = FakeAuth("needle-literal-0123456789")
    hub = Hub(auth, background=True)
    for round_ in range(5):
        request = str(uuid.uuid4())
        hub.begin(SESSION, request)
        hub.bind(thread="thread-1", turn_id="turn-1")
        hub.verified()
        start_item(hub, "msg-" + str(round_))
        full = "".join("chunk %d needle-literal-0123456789 " % index for index in range(300))
        for index in range(0, len(full), 7):
            note(hub, "item/agentMessage/delta", itemId="msg-" + str(round_), delta=full[index:index + 7])
            if index % 70 == 0:
                clock.sleep(0.001)
        hub.complete("idle", "completed", None)
        turn = hub.previous[SESSION]
        text = []
        for _, encoded in turn.events:
            lines = encoded.decode().split("\n")
            if "event: item.delta" in lines:
                text.append(json.loads(next(l[6:] for l in lines if l.startswith("data: ")))["text"])
        assert "".join(text) == auth.journal.text(full)
    hub.close()


def test_stale_plan_is_never_committed(text_on):
    """Deterministic interleaving: while one pass plans, another pass commits the same window and the same text is
    appended again. The first pass must not commit its now stale plan. With a one-char literal the stale plan carries
    the old merged-marker state and would emit a second marker where the whole-text sanitizer has one."""
    auth = FakeAuth("x")
    hub, request = new_hub(auth)
    hub.verified()
    start_item(hub)
    note(hub, "item/agentMessage/delta", itemId="msg-1", delta="x")
    turn = hub.active
    original, calls = hub.sanitizer.generation, {"n": 0}

    def generation():
        calls["n"] += 1
        if calls["n"] == 2:  # the outer pass has planned (with the old merged-marker state) and checks staleness
            hub._flush(turn, final=False)  # another pass commits first ...
            note(hub, "item/agentMessage/delta", itemId="msg-1", delta="x")  # ... and the same text arrives again
        return original()
    hub.sanitizer.generation = generation
    hub._flush(turn, final=False)
    hub.sanitizer.generation = original
    assert calls["n"] == 4  # outer before/after, nested before/after
    hub.flush(turn, final=True)
    text = "".join(data["text"] for _, kind, data in events(hub) if kind == "item.delta")
    assert text == auth.journal.text("xx") == "[redacted]"


# --- review stage-1 follow-ups ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("final", [False, True])
def test_generation_change_withholds_command_summary(text_on, final):
    """A literal observed during the pass: neither text nor the command summary of that pass is published."""
    auth = FakeAuth()
    hub, request = new_hub(auth)
    hub.verified()
    start_item(hub, "cmd", "commandExecution", command="echo NEW_SECRET_VALUE now")
    note(hub, "item/commandExecution/outputDelta", itemId="cmd", delta="NEW_SECRET_VALUE out\n")
    calls = {"n": 0}
    original = auth.refresh_projection_secrets

    def rotate():
        calls["n"] += 1
        if calls["n"] == 2:  # between the plan and its staleness check
            auth.secrets.add("NEW_SECRET_VALUE")
        original()
    auth.refresh_projection_secrets = rotate
    hub.flush(hub.active, final=final)
    data = b"".join(encoded for _, encoded in hub.active.events)
    assert b"NEW_SECRET_VALUE" not in data
    if final:
        assert hub.active.stopped == "sanitize"
    else:
        hub.flush(hub.active, final=True)  # planned again with the new journal
        data = b"".join(encoded for _, encoded in hub.active.events)
        assert b"NEW_SECRET_VALUE" not in data and b"echo [redacted] now" in data


def test_long_command_keeps_only_a_safe_prefix(text_on):
    auth = FakeAuth("S" * 40)
    hub, request = new_hub(auth)
    hub.verified()
    command = "x" * (stream_module.SUMMARY_RAW_LIMIT - 20) + "S" * 40 + "tail"
    start_item(hub, "cmd", "commandExecution", command=command)
    assert len(hub.active.items["cmd"].summary_raw) == stream_module.SUMMARY_RAW_LIMIT
    hub.flush(hub.active, final=True)
    started = [data for _, kind, data in events(hub) if kind == "item.started"][0]
    assert "S" not in started["command"] and set(started["command"]) == {"x"}


@pytest.mark.parametrize("ending", ["discard", "never-verified"])
def test_unverified_turn_never_reaches_a_subscriber(text_on, ending):
    hub, request = new_hub(None)
    start_item(hub, "cmd", "commandExecution", command="echo early-command")
    note(hub, "item/commandExecution/outputDelta", itemId="cmd", delta="early-output\n")
    start_item(hub)
    note(hub, "item/agentMessage/delta", itemId="msg-1", delta="early text ")
    hub.flush(hub.active, final=False)
    subscription, frames = hub.subscribe(SESSION, None)
    seen = b"".join(frames + hub.drain_snapshot(subscription))
    if ending == "discard":
        hub.discard("model_unavailable")
        hub.verified()  # a late verify after the discard opens nothing
    hub.complete("failed", "failed", "codex_model_unavailable", hub.active)
    seen += b"".join(encoded for _, encoded in hub.read(subscription)) + b"".join(hub.drain_snapshot(subscription))
    again, frames = hub.subscribe(SESSION, (request, 0))
    seen += b"".join(frames + hub.drain_snapshot(again))
    assert b"early" not in seen
    assert b"turn.completed" in seen


def test_dropped_grace_turn_cursor_resyncs(text_on):
    hub, first = new_hub(None)
    hub.verified()
    subscription, _ = hub.subscribe(SESSION, None)
    hub.complete("idle", "completed", None, hub.active)
    hub.read(subscription)
    other = str(uuid.uuid4())
    hub.begin(other, str(uuid.uuid4()))
    hub.complete("idle", "completed", None, hub.active)  # another session's turn takes the only grace slot
    assert list(hub.previous) == [other]
    assert [encoded for _, encoded in hub.read(subscription)] == [stream_module.frame("sync", {"reason": "expired"})]
    assert hub.read(subscription) == []


def test_snapshot_is_lazy_and_bounded_per_frame(text_on):
    """review c894465: subscribing allocates no copy of the turn; the rest is produced one frame at a time and still
    reproduces exactly the text sent up to the snapshot's seq."""
    import tracemalloc
    hub, request = new_hub(None)
    hub.verified()
    start_item(hub)
    text = "".join("word%05d " % index for index in range(40000))  # ~400 KB
    for index in range(0, len(text), 32000):
        note(hub, "item/agentMessage/delta", itemId="msg-1", delta=text[index:index + 32000])
    hub.flush(hub.active, final=True)
    retained = hub.active.retained()
    tracemalloc.start()
    subscription, frames = hub.subscribe(SESSION, None)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert peak < 64 * 1024 and hub.active.retained() == retained
    sizes, pieces = [], []
    for encoded in subscription.pending:
        sizes.append(len(encoded))
        lines = encoded.decode().split("\n")
        data = json.loads(next(l[6:] for l in lines if l.startswith("data: ")))
        pieces.append(data.get("text", ""))
    assert max(sizes) <= stream_module.SNAPSHOT_DATA_LIMIT + 200
    assert "".join(pieces) == text  # all sent by the snapshot's seq (final flush), nothing more


def test_snapshot_text_stops_at_its_seq(text_on):
    """Text kept but not yet sent as item.delta (rate limit) is not in the snapshot: it follows as events after N."""
    hub, request = new_hub(None)
    hub.verified()
    start_item(hub)
    note(hub, "item/agentMessage/delta", itemId="msg-1", delta="sent ")
    hub.flush(hub.active, final=True)
    hub.active.tokens = 0
    note(hub, "item/agentMessage/delta", itemId="msg-1", delta="queued ")
    hub.flush(hub.active, final=False)  # kept, but no token: unsent
    subscription, frames = hub.subscribe(SESSION, None)
    texts = [json.loads(next(l[6:] for l in e.decode().split("\n") if l.startswith("data: "))).get("text", "")
             for e in hub.drain_snapshot(subscription)]
    assert "".join(texts) == "sent "


def test_item_status_events_are_bounded(text_on):
    hub, request = new_hub(None)
    hub.verified()
    start_item(hub, "patch", "fileChange", changes=[])
    for index in range(20):
        hub.note({"method": "item/fileChange/requestApproval",
                  "params": {"threadId": "thread-1", "turnId": "turn-1", "itemId": "patch"}},
                 "requested" if index % 2 == 0 else "decided")
    statuses = [data["status"] for _, kind, data in events(hub) if kind == "item.status"]
    assert statuses == ["approval", "inProgress", "approval", "inProgress"]
