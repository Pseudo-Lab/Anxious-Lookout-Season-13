"""#90 P5 (infra/hosting/m3/chat-stream/vm/i90-p5.py) against fakes: kubectl Deployments and patches, the turn budget
file, the step verdicts from crafted turn facts. Pure: no VM, no cluster, no network. Needs infra/ mounted."""
import hashlib
import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
VM = ROOT / "infra" / "hosting" / "m3" / "chat-stream" / "vm"
pytestmark = pytest.mark.skipif(not VM.exists(), reason="infra/ not mounted")


def load():
    spec = importlib.util.spec_from_file_location("i90_p5", VM / "i90-p5.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def m(tmp_path, monkeypatch):
    module = load()
    evidence = tmp_path / "evidence"
    evidence.mkdir(mode=0o700)
    monkeypatch.setattr(module, "EVIDENCE", str(evidence))
    monkeypatch.setattr(module, "LOCK", str(evidence / "i90-d.lock"))
    monkeypatch.setattr(module, "BUDGET", str(evidence / "i90-p5-budget.json"))
    monkeypatch.setattr(module.time, "sleep", lambda s: None)
    module.OUT.clear(); module.OUT["stream"] = None
    module.PRIVATE.clear(); module.PRIVATE["salt"] = "0" * 64
    return module


# --- budget (review major: reserve durably before the send) ------------------------------------------------------

def test_reservation_is_on_disk_before_the_post_and_counts_whatever_follows(m, monkeypatch):
    seen = []

    def call(method, path, body=None, session=True):
        if method == "GET":
            return 200, {"state": "idle", "version": 3}, ""
        seen.append(json.load(open(m.BUDGET))["turns"])  # the reservation is there when the POST goes out
        return None, None, ""  # the answer is lost

    monkeypatch.setattr(m, "call", call)
    with pytest.raises(m.Stop) as stopped:
        m.send("S1", "c" * 36, "p")
    assert stopped.value.code == 5
    assert seen == [[{"n": 1, "step": "S1", "reservedAt": seen[0][0]["reservedAt"], "outcome": "reserved"}]]
    assert m.budget_read()[0]["outcome"] == "answered None"  # still counted


def test_the_ninth_reservation_refuses_and_nothing_is_sent(m, monkeypatch):
    m.durable_json(m.BUDGET, {"cap": 8, "turns": [{"n": i + 1, "step": "x", "outcome": "accepted"} for i in range(8)]})
    posts = []
    monkeypatch.setattr(m, "call", lambda method, path, body=None, session=True:
                        (200, {"state": "idle", "version": 1}, "") if method == "GET" else posts.append(path))
    with pytest.raises(m.Stop) as stopped:
        m.send("S6", "c" * 36, "p")
    assert stopped.value.code == 3 and posts == [] and len(m.budget_read()) == 8


def test_a_failed_reservation_write_sends_nothing(m, monkeypatch):
    posts = []
    monkeypatch.setattr(m, "call", lambda method, path, body=None, session=True:
                        (200, {"state": "idle", "version": 1}, "") if method == "GET" else posts.append(path))
    monkeypatch.setattr(m, "durable_json", lambda path, value: (_ for _ in ()).throw(OSError()))
    with pytest.raises(m.Stop) as stopped:
        m.send("S1", "c" * 36, "p")
    assert stopped.value.code == 3 and posts == []


def test_an_unreadable_budget_file_refuses(m):
    Path(m.BUDGET).write_text("{")
    with pytest.raises(m.Stop) as stopped:
        m.reserve("S1")
    assert stopped.value.code == 3


def test_an_interrupt_during_the_post_keeps_the_turn_counted(m, monkeypatch):
    def call(method, path, body=None, session=True):
        if method == "GET":
            return 200, {"state": "idle", "version": 1}, ""
        raise m.Interrupted()

    monkeypatch.setattr(m, "call", call)
    with pytest.raises(m.Interrupted):
        m.send("S2", "c" * 36, "p")
    assert m.budget_read()[0]["outcome"] == "send raised"


# --- enable / disable (review major: two patches, progress on disk, explicit restore or hold) ---------------------

class Cluster:
    def __init__(self, m, refuse=(), wrong=(), rollout_fail=()):
        self.m, self.refuse, self.wrong, self.rollout_fail = m, set(refuse), set(wrong), set(rollout_fail)
        self.d = {"api": self.dep(m.API, [{"name": "api", "image": "runtime-r4", "env": [{"name": "A", "value": "1"}]},
                                          {"name": "x", "image": "y"}]),
                  "runner-controller": self.dep(m.CTRL, [{"name": "controller", "image": "runtime-old"}])}
        self.patches = []

    @staticmethod
    def dep(spec, containers):
        return {"metadata": {"uid": spec[2], "resourceVersion": "1"}, "spec": {"replicas": 1, "template": {
            "metadata": {"labels": {"a": "b"}}, "spec": {"containers": containers}}}, "status": {"readyReplicas": 1}}

    def run(self, cmd, timeout=60, stdin=None):
        args = cmd[2:]
        if "get" in args and "deployment" in args:
            return 0, json.dumps(self.d[args[args.index("deployment") + 1]])
        if "patch" in args:
            name = args[args.index("deployment") + 1]
            self.patches.append(name)
            if name in self.refuse:
                return 1, ""
            ops = json.loads(args[args.index("-p") + 1])
            out = self.m.apply_patch(self.d[name], ops)
            if out is None:
                return 1, ""
            if name in self.wrong:
                out["spec"]["template"]["metadata"]["labels"]["drift"] = "1"
            out["metadata"]["resourceVersion"] = str(int(out["metadata"]["resourceVersion"]) + 1)
            self.d[name] = out
            return 0, ""
        if "rollout" in args:
            return (1 if args[-2].split("/")[1] in self.rollout_fail else 0), ""
        return 1, ""


def enabled_setup(m, monkeypatch, tmp_path, **faults):
    cluster = Cluster(m, **faults)
    monkeypatch.setattr(m, "run", cluster.run)
    monkeypatch.setattr(m, "call", lambda *a, **k: (200, {}, ""))
    doc = {"newApiImage": "runtime-r4", "deployments": {"runner-controller": {"images": {"controller": "runtime-old"}}}}
    ev = tmp_path / "evidence" / "i90-p5-enable-20261115T000000Z"
    ev.mkdir()
    return cluster, doc, str(ev)


def test_enable_records_before_sending_then_moves_both(m, monkeypatch, tmp_path):
    cluster, doc, ev = enabled_setup(m, monkeypatch, tmp_path)
    before = {k: m.template_sha(v) for k, v in cluster.d.items()}
    m.enable(ev, doc)
    run = json.load(open(os.path.join(ev, "run.json")))
    assert run["progress"] == "api" and run["before"]["api"] == before["api"]
    assert run["before"]["controller"] == before["runner-controller"]
    ctrl = cluster.d["runner-controller"]["spec"]["template"]["spec"]["containers"][0]
    assert ctrl["image"] == "runtime-r4" and [e["name"] for e in ctrl["env"]] == ["CODEX_RUNNER_STREAM_TEXT",
                                                                                 "CODEX_RUNNER_STREAM_TRACE"]
    assert [e["name"] for e in cluster.d["api"]["spec"]["template"]["spec"]["containers"][0]["env"]] == [
        "A", "RESEARCH_STREAM_ENABLE", "CODEX_RUNNER_STREAM_TEXT", "CODEX_RUNNER_STREAM_TRACE", "STREAM_TRACE"]
    assert cluster.patches == ["runner-controller", "api"]


def test_enable_sends_nothing_when_the_record_cannot_be_saved(m, monkeypatch, tmp_path):
    cluster, doc, ev = enabled_setup(m, monkeypatch, tmp_path)
    monkeypatch.setattr(m, "durable_json", lambda path, value: (_ for _ in ()).throw(OSError()))
    with pytest.raises(m.Stop) as stopped:
        m.enable(ev, doc)
    assert stopped.value.code == 3 and cluster.patches == []


def test_enable_refused_controller_patch_changes_nothing(m, monkeypatch, tmp_path):
    cluster, doc, ev = enabled_setup(m, monkeypatch, tmp_path, refuse=("runner-controller",))
    with pytest.raises(m.Stop) as stopped:
        m.enable(ev, doc)
    assert stopped.value.code == 4 and cluster.patches == ["runner-controller"]


def test_enable_refused_api_patch_restores_the_controller_and_proves_it(m, monkeypatch, tmp_path):
    cluster, doc, ev = enabled_setup(m, monkeypatch, tmp_path, refuse=("api",))
    before = m.template_sha(cluster.d["runner-controller"])
    with pytest.raises(m.Stop) as stopped:
        m.enable(ev, doc)
    assert stopped.value.code == 4
    assert cluster.patches == ["runner-controller", "api", "runner-controller"]
    assert m.template_sha(cluster.d["runner-controller"]) == before


def test_enable_wrong_template_after_a_patch_holds(m, monkeypatch, tmp_path):
    cluster, doc, ev = enabled_setup(m, monkeypatch, tmp_path, wrong=("runner-controller",))
    with pytest.raises(m.Stop) as stopped:
        m.enable(ev, doc)
    assert stopped.value.code == 8 and cluster.patches == ["runner-controller"]


def test_enable_rollout_failure_holds_with_progress_recorded(m, monkeypatch, tmp_path):
    cluster, doc, ev = enabled_setup(m, monkeypatch, tmp_path, rollout_fail=("runner-controller",))
    with pytest.raises(m.Stop) as stopped:
        m.enable(ev, doc)
    assert stopped.value.code == 7
    assert json.load(open(os.path.join(ev, "run.json")))["progress"] == "controller"


def test_disable_restores_both_templates_exactly(m, monkeypatch, tmp_path):
    cluster, doc, ev = enabled_setup(m, monkeypatch, tmp_path)
    before = {k: m.template_sha(v) for k, v in cluster.d.items()}
    m.enable(ev, doc)
    out = tmp_path / "evidence" / "i90-p5-disable-20261115T010000Z"
    out.mkdir()
    m.disable(str(out), doc)
    assert {k: m.template_sha(v) for k, v in cluster.d.items()} == before
    assert cluster.patches == ["runner-controller", "api", "api", "runner-controller"]


def test_disable_after_a_controller_only_enable_restores_the_controller_only(m, monkeypatch, tmp_path):
    cluster, doc, ev = enabled_setup(m, monkeypatch, tmp_path, rollout_fail=("runner-controller",))
    before = m.template_sha(cluster.d["runner-controller"])
    with pytest.raises(m.Stop):
        m.enable(ev, doc)
    cluster.rollout_fail.clear()
    out = tmp_path / "evidence" / "i90-p5-disable-20261115T010000Z"
    out.mkdir()
    m.disable(str(out), doc)
    assert m.template_sha(cluster.d["runner-controller"]) == before
    assert cluster.patches == ["runner-controller", "runner-controller"]


def test_disable_refuses_when_an_entry_is_not_there_exactly_once(m, monkeypatch, tmp_path):
    cluster, doc, ev = enabled_setup(m, monkeypatch, tmp_path)
    m.enable(ev, doc)
    env = cluster.d["api"]["spec"]["template"]["spec"]["containers"][0]["env"]
    env.append({"name": "STREAM_TRACE", "value": "true"})  # another change added a second entry
    out = tmp_path / "evidence" / "i90-p5-disable-20261115T010000Z"
    out.mkdir()
    patches = list(cluster.patches)
    with pytest.raises(m.Stop) as stopped:
        m.disable(str(out), doc)
    assert stopped.value.code == 3 and cluster.patches == patches


def test_disable_is_refused_after_a_held_action(m):
    for action, rc in (("enable", 7),):
        folder = Path(m.EVIDENCE) / ("i90-p5-%s-20261115T000000Z" % action)
        folder.mkdir()
        (folder / "exit.json").write_text(json.dumps({"rc": rc, "verdicts": {}}))
    with pytest.raises(m.Stop) as stopped:
        m.guard("disable")
    assert stopped.value.code == 3 and "held" in stopped.value.message


def test_turns_needs_one_clean_enable_and_no_disable(m):
    with pytest.raises(m.Stop):
        m.guard("turns")
    folder = Path(m.EVIDENCE) / "i90-p5-enable-20261115T000000Z"
    folder.mkdir()
    (folder / "exit.json").write_text(json.dumps({"rc": 0, "verdicts": {}}))
    m.guard("turns")
    (Path(m.EVIDENCE) / "i90-p5-disable-20261115T010000Z").mkdir()
    with pytest.raises(m.Stop):
        m.guard("turns")


# --- step verdicts from crafted facts -----------------------------------------------------------------------------

class FakeClient:
    def __init__(self, records):
        self._records = records
        self.process = type("P", (), {"poll": lambda self: 0})()

    def records(self):
        return self._records

    frames = None

    def status(self, limit=20):
        return 200


for name in ("frames", "texts", "completed", "until"):
    setattr(FakeClient, name, getattr(load().Client, name))

CUR, PREV = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"


def item_hash(m, value):
    return hashlib.sha256((m.PRIVATE["salt"] + value).encode()).hexdigest()[:16]


def fr(m, event, request=CUR, t=1, **fields):
    """A client frame record as review's p5_client.py r1 writes it (salted request / item)."""
    record = {"kind": "frame", "event": event, "unixNs": t, **fields}
    if request is not None:
        record["request"] = item_hash(m, request)
    return record


def body_with(m, text, status=None, display=False):
    item = {"id": "a1", "type": "message", "role": "assistant", "text": text}
    if status:
        item["turnStatus"] = status
    if display:
        item["displayOnly"] = True
    return {"state": "idle", "items": [{"id": "u1", "type": "message", "role": "user", "text": "q"}, item]}


def summary_of(m, text):
    return {"textChars": len(text), "textSha256": hashlib.sha256(text.encode()).hexdigest()}


def completed(m, text, request=CUR, t=2):
    return fr(m, "item.completed", request, t, item=item_hash(m, "a1"), **summary_of(m, text))


def deltas(m, n, request=CUR, start=10):
    return [fr(m, "item.delta", request, start + i, item=item_hash(m, "a1"), chars=3) for i in range(n)]


# --- request scoping (review r0 majors) ------------------------------------------------------------------------

def test_scoping_drops_the_previous_turns_replay_and_keeps_request_less_frames_of_the_current_one(m):
    client = FakeClient([fr(m, "runner", None), fr(m, "snapshot.begin", PREV, turnState="completed"),
                         fr(m, "snapshot.end", None), fr(m, "turn.completed", PREV, status="completed"),
                         fr(m, "turn.started", CUR), fr(m, "snapshot.end", None, items={})])
    events = [r["event"] for r in m.scoped(client, CUR)]
    assert events == ["turn.started", "snapshot.end"] and m.completed_turn(client, CUR) is None


def test_s3_previous_turns_completion_does_not_make_a_race(m, monkeypatch):
    """review r0 adversarial: previous completion + 10 current deltas + an acknowledged interrupt = pass."""
    previous = [fr(m, "snapshot.begin", PREV, 0, turnState="completed"), fr(m, "turn.completed", PREV, 1,
                                                                             status="completed")]
    posts = []

    def fake_turn(ev, step, conversation, runner, prompt, readers, db=False, during=None):
        client = FakeClient(previous + deltas(m, 10))
        during(CUR, {"hop3": client})
        client._records += [fr(m, "turn.completed", CUR, 99, status="interrupted")]
        return {"body": body_with(m, "part", status="interrupted", display=True), "clients": {"hop3": client},
                "request": CUR}

    monkeypatch.setattr(m, "turn", fake_turn)
    monkeypatch.setattr(m, "call", lambda *a, **k: (posts.append(a[1]), (200, {"state": "interrupting"}, ""))[1])
    verdicts = {}
    m.step_s3("ev", "c" * 36, {}, verdicts)
    assert verdicts["S3"] is True and len(posts) == 1


def test_s3_without_text_sends_no_interrupt_and_is_unknown(m, monkeypatch):
    posts = []

    def fake_turn(ev, step, conversation, runner, prompt, readers, db=False, during=None):
        client = FakeClient(deltas(m, 3))
        during(CUR, {"hop3": client})
        return {"body": body_with(m, "short"), "clients": {"hop3": client}, "request": CUR}

    monkeypatch.setattr(m, "turn", fake_turn)
    monkeypatch.setattr(m, "call", lambda *a, **k: posts.append(a))
    monkeypatch.setattr(m.time, "time", iter(range(0, 10 ** 6, 100)).__next__)
    verdicts = {}
    m.step_s3("ev", "c" * 36, {}, verdicts)
    assert verdicts["S3"] is None and posts == []


def test_s3_a_completion_of_this_turn_first_is_a_race_without_an_interrupt(m, monkeypatch):
    posts = []

    def fake_turn(ev, step, conversation, runner, prompt, readers, db=False, during=None):
        client = FakeClient(deltas(m, 4) + [fr(m, "turn.completed", CUR, 50, status="completed")])
        during(CUR, {"hop3": client})
        return {"body": body_with(m, "answer"), "clients": {"hop3": client}, "request": CUR}

    monkeypatch.setattr(m, "turn", fake_turn)
    monkeypatch.setattr(m, "call", lambda *a, **k: posts.append(a))
    verdicts = {}
    m.step_s3("ev", "c" * 36, {}, verdicts)
    assert verdicts["S3"] is False and posts == []


def test_reconstruction_from_a_completed_snapshot(m):
    """review r0 adversarial: a reader joining a completed turn gets snapshot.end items, no item.completed."""
    client = FakeClient([fr(m, "snapshot.begin", CUR, turnState="completed"),
                         fr(m, "turn.completed", CUR, status="completed"),  # the server order: before snapshot.end
                         fr(m, "snapshot.end", CUR, items={item_hash(m, "a1"): summary_of(m, "answer")})])
    equal, detail = m.same_text(client, body_with(m, "answer"), CUR)
    assert equal and "snapshot.end" in detail


def test_reconstruction_needs_this_turns_completion(m):
    client = FakeClient([completed(m, "answer", PREV), fr(m, "turn.completed", PREV, status="completed")])
    equal, detail = m.same_text(client, body_with(m, "answer"), CUR)
    assert not equal and "no turn.completed" in detail


def test_the_send_time_is_taken_before_the_post(m, monkeypatch):
    clock = {"t": 10.0}

    def call(method, path, body=None, session=True):
        if method == "GET":
            return 200, {"state": "idle", "version": 1}, ""
        clock["t"] = 15.0  # the POST takes 5 s
        return 202, {"requestId": CUR}, ""

    monkeypatch.setattr(m, "call", call)
    monkeypatch.setattr(m.time, "time", lambda: clock["t"])
    request, sent, answered = m.send("S1", "c" * 36, "p")
    assert (request, sent, answered) == (CUR, 10.0, 15.0)


# --- step verdicts ---------------------------------------------------------------------------------------------

def test_s6_without_server_stall_evidence_is_unknown(m, monkeypatch):
    normal = FakeClient([completed(m, "answer"), fr(m, "turn.completed", CUR, 3)])
    paused = FakeClient([{"kind": "reader_paused", "unixNs": 1}])
    monkeypatch.setattr(m, "turn", lambda *a, **k: {"body": body_with(m, "answer"), "request": CUR,
                                                   "clients": {"n": normal, "p": paused},
                                                   "api": [{"reason": None}, {"reason": None}]})
    verdicts = {}
    m.step_s6("ev", "c" * 36, {}, verdicts)
    assert verdicts["S6"] is None


def test_s6_passes_with_ready_pause_later_output_one_stall_and_a_complete_normal_reader(m, monkeypatch):
    normal = FakeClient([fr(m, "item.output", CUR, 5, item=item_hash(m, "c1"), chars=9), completed(m, "answer", t=6),
                         fr(m, "turn.completed", CUR, 7)])
    paused = FakeClient([{"kind": "reader_paused", "unixNs": 1}])
    monkeypatch.setattr(m, "turn", lambda *a, **k: {"body": body_with(m, "answer"), "request": CUR,
                                                   "clients": {"n": normal, "p": paused},
                                                   "api": [{"reason": "stalled"}, {"reason": None}]})
    verdicts = {}
    m.step_s6("ev", "c" * 36, {}, verdicts)
    assert verdicts["S6"] is True


CAP_EVIDENCE = {"outputDroppedBytes": 92000, "deltaIn": {"commandExecution": {"count": 40, "bytes": 108894}}}

def s5_turn(m, monkeypatch, b_records, summary):
    """The fake turn runs S5's own `reload` (reader a's end, then reader b from a patched Client)."""
    a = FakeClient([fr(m, "item.started", CUR, 2, item=item_hash(m, "c1"), itemKind="commandExecution"),
                    fr(m, "item.output", CUR, 3, item=item_hash(m, "c1"), chars=16000),
                    {"kind": "end", "reason": "local_close", "unixNs": 4}])
    b = FakeClient(b_records)
    monkeypatch.setattr(m, "Client", lambda *args, **kwargs: b)

    def fake_turn(ev, step, conversation, runner, prompt, readers, db=False, during=None):
        clients = {"a": a}
        during(CUR, clients)
        return {"body": body_with(m, "answer"), "clients": clients, "request": CUR,
                "runner": [summary] if summary else []}
    return fake_turn


def b_mid_turn(m, text):
    return [fr(m, "snapshot.begin", CUR, 10, turnState="running"), fr(m, "snapshot.end", None, 11, items={}),
            fr(m, "item.delta", CUR, 12, item=item_hash(m, "a1"), chars=3), completed(m, text, t=13),
            fr(m, "turn.completed", CUR, 14, status="completed")]


def test_s5_passes_with_a_mid_turn_reload_reconstruction_and_output_cap_evidence(m, monkeypatch):
    monkeypatch.setattr(m, "turn", s5_turn(m, monkeypatch, b_mid_turn(m, "answer"), CAP_EVIDENCE))
    verdicts = {}
    m.step_s5("ev", "c" * 36, {}, verdicts)
    assert verdicts["S5"] is True


def test_s5_compares_reader_b_alone(m, monkeypatch):
    monkeypatch.setattr(m, "turn", s5_turn(m, monkeypatch, b_mid_turn(m, "answ"), CAP_EVIDENCE))
    verdicts = {}
    m.step_s5("ev", "c" * 36, {}, verdicts)
    assert verdicts["S5"] is False  # b's own reconstruction differs from GET, whatever a saw


def test_s5_r3_without_runner_cap_evidence_still_judges_the_reload(m, monkeypatch, capsys):
    monkeypatch.setattr(m, "turn", s5_turn(m, monkeypatch, b_mid_turn(m, "answer"), None))
    verdicts = {}
    m.step_s5("ev", "c" * 36, {}, verdicts)
    assert verdicts["S5"] is True and "S5 gap: output cap not measured" in capsys.readouterr().out


def test_s5_a_reader_b_that_joined_a_completed_turn_is_not_a_reload(m, monkeypatch):
    records = [fr(m, "snapshot.begin", CUR, 10, turnState="completed"),
               fr(m, "snapshot.end", None, 11, items={item_hash(m, "a1"): summary_of(m, "answer")}),
               fr(m, "turn.completed", CUR, 12, status="completed")]
    monkeypatch.setattr(m, "turn", s5_turn(m, monkeypatch, records, CAP_EVIDENCE))
    verdicts = {}
    m.step_s5("ev", "c" * 36, {}, verdicts)
    assert verdicts["S5"] is False


def test_the_stop_policy_ends_turns_at_an_unknown_step(m, monkeypatch, tmp_path):
    ran = []
    outcome = {"S1": True, "S2": None}  # S2 unknown: S3 onwards never run, their turns unspent

    def fake(step):
        def run(ev, conversation, runner, verdicts, *rest):
            name = rest[0] if rest else step
            ran.append(name)
            verdicts[name] = outcome.get(name, True)
        return run

    for name in ("s1", "s2", "s3", "s5", "s6"):
        monkeypatch.setattr(m, "step_" + name, fake(name.upper()))
    monkeypatch.setattr(m, "step_s4", fake("S4"))
    monkeypatch.setattr(m, "create_session", lambda ev: None)
    monkeypatch.setattr(m, "conversation_of_s0", lambda: "c" * 36)
    monkeypatch.setattr(m, "prepare_private", lambda ev, image: None)
    monkeypatch.setattr(m, "probe_private", lambda: None)
    monkeypatch.setattr(m, "start_runner", lambda *a: {})
    monkeypatch.setattr(m, "stop_runner", lambda *a: True)
    verdicts = {}
    stopped = m.measure(str(tmp_path), {"toolImage": "x"}, "turns", verdicts)
    assert ran == ["S1", "S2"] and stopped == "S2" and verdicts["release"] is True


@pytest.mark.parametrize("action,code,passed,want", [
    ("turns", 7, False, "HELD"), ("enable", 8, False, "HELD"),
    ("turns", 80, False, "prove read-only that no client container"), ("turns", 6, False, "runner release"),
    ("turns", 70, False, "3 hours after it was created (expiry is not proof of a revoke)"),
    ("turns", 9, False, "disable on PM go"), ("turns", 0, False, "stop here, the remaining turns unspent; disable"),
    ("turns", 5, False, "disable next"), ("s0", 5, False, "stop here, the remaining turns unspent; report"),
    ("s0", 0, True, "the next action: enable"), ("turns", 0, True, "the next action: disable"),
    ("disable", 0, True, "P5 complete")])
def test_the_printed_next_step_is_the_pm_stop_table(m, action, code, passed, want):
    assert want in m.next_step(action, code, passed)


# --- data scope -------------------------------------------------------------------------------------------------

def test_facts_and_front_evidence_never_hold_text(m, tmp_path):
    secret_text = "the answer body must never be written"
    body = body_with(m, secret_text)
    facts = m.facts_of(body)
    assert secret_text not in json.dumps(facts) and "a1" not in json.dumps(facts)
    m.front(str(tmp_path), "S1", FakeClient([completed(m, secret_text)]), body, CUR, 0.0)
    written = (tmp_path / "front-evidence.json").read_text()
    assert secret_text not in written and json.loads(written)["steps"][0]["items"][1]["textChars"] == len(secret_text)


def test_the_runner_log_follower_keeps_marked_lines_only(m, monkeypatch, tmp_path):
    lines = (b"2026 INFO: runner_stream_summary {\"requestId\": \"r\"}\n", b"2026 some answer text or command output\n",
             b"2026 INFO: Application shutdown complete.\n")

    class Proc:
        stdout = iter(lines)

        def terminate(self):
            pass

        def wait(self, limit):
            return 0

    monkeypatch.setattr(m.subprocess, "Popen", lambda *a, **k: Proc())
    path = m.follow("ns", "pod", str(tmp_path / "runner.log"))
    m.stop_followers()
    kept = open(path, "rb").read()
    assert b"runner_stream_summary" in kept and b"shutdown complete" in kept and b"answer text" not in kept


def test_final_rc_order(m):
    facts = {"runner": True, "gone": True, "private": True, "revoked": True}
    assert m.final_rc(0, facts, False) == 0
    assert m.final_rc(7, {**facts, "runner": False}, True) == 7
    assert m.final_rc(5, {**facts, "runner": False, "gone": False}, True) == 6
    assert m.final_rc(0, {**facts, "gone": False, "revoked": False}, True) == 80
    assert m.final_rc(0, {**facts, "revoked": False}, True) == 70
    assert m.final_rc(0, facts, True) == 9


def test_pins(m):
    import re
    assert m.EXPECTED_SHA == hashlib.sha256((VM / "i90r-expected.json").read_bytes()).hexdigest()
    assert re.fullmatch(r"[0-9a-f]{64}", m.CLIENT_SHA)  # review's p5_client.py r1 (outside the repo; sheet checks it)
    assert re.fullmatch(r"[0-9a-f]{40}", m.SOURCE) and m.SOURCE.startswith("bb3aa97")
    with pytest.raises(m.Stop) as stopped:
        m.read_pinned(str(VM / "i90-p5.py"), "PIN", "x")
    assert stopped.value.code == 3


def test_the_salt_is_64_hex_and_ids_hash_the_ascii_salt(m, monkeypatch, tmp_path):
    """review P5-CLIENT.md r1: 64 lowercase hex characters; sha256(ASCII salt + UTF-8 id)[:16]."""
    monkeypatch.setattr(m, "read_pinned", lambda *a: b"client")
    monkeypatch.setattr(m, "run", lambda cmd, timeout=60, stdin=None: (0, "sha256:" + "a" * 64))
    monkeypatch.setattr(m.os, "fchown", lambda fd, uid, gid: None)
    m.SESSION["token"] = "t"
    m.prepare_private(str(tmp_path), "sha256:" + "a" * 64)
    salt = (tmp_path / "private" / "salt").read_text()
    import re
    assert re.fullmatch(r"[0-9a-f]{64}", salt) and salt == m.PRIVATE["salt"]
    assert m.hashed("id-é") == hashlib.sha256((salt + "id-é").encode("utf-8")).hexdigest()[:16]
    assert oct((tmp_path / "private" / "salt").stat().st_mode & 0o777) == "0o400"


def test_apply_patch_never_mutates_the_patch(m):
    """Regression: 'add env: []' then 'add env/-' once filled the [] inside `ops`, so a second local apply differed
    and enable refused (found by test_enable_* during development)."""
    d = Cluster.dep(m.CTRL, [{"name": "controller", "image": "old"}])
    ops = m.forward_ops(d, m.CTRL, m.CTRL_ENV, "new", "old")
    frozen = json.dumps(ops, sort_keys=True)
    first, second = m.apply_patch(d, ops), m.apply_patch(d, ops)
    assert first == second and json.dumps(ops, sort_keys=True) == frozen


# --- r1 review majors: the server's snapshot order, and the S5 cap enforced -------------------------------------

def test_a_snapshot_without_its_end_is_not_finished_nor_trusted(m):
    """runner/stream.py snapshot_rest: turn.completed comes before snapshot.end."""
    records = [fr(m, "snapshot.begin", CUR, 1, turnState="completed"), fr(m, "turn.completed", CUR, 2)]
    client = FakeClient(records)
    assert m.completed_turn(client, CUR) and m.snapshot_open(client, CUR) and not m.finished(client, CUR)
    equal, detail = m.same_text(client, body_with(m, "answer"), CUR)
    assert not equal and "no snapshot.end" in detail
    records.append(fr(m, "snapshot.end", CUR, 3, items={item_hash(m, "a1"): summary_of(m, "answer")}))
    assert m.finished(client, CUR) and m.same_text(client, body_with(m, "answer"), CUR)[0]


def test_turn_waits_for_a_delayed_snapshot_end_before_closing_the_reader(m, monkeypatch, tmp_path):
    """review r1 probe through the real turn(): snapshot.end arrives some polls after turn.completed."""
    records = [fr(m, "snapshot.begin", CUR, 1, turnState="completed"), fr(m, "turn.completed", CUR, 2)]
    polls, signalled = {"n": 0}, []

    class Proc:
        def poll(self):
            polls["n"] += 1
            if polls["n"] == 5:  # the final frame is delivered late
                records.append(fr(m, "snapshot.end", CUR, 3, items={item_hash(m, "a1"): summary_of(m, "answer")}))
            return None

        def send_signal(self, sig):
            signalled.append(len(records))

    class Reader(FakeClient):
        def __init__(self, *a, **k):
            super().__init__(records)
            self.process = Proc()

        def end(self, limit):
            return {}

    monkeypatch.setattr(m, "Client", Reader)
    monkeypatch.setattr(m, "send", lambda *a: (CUR, 1.0, 1.1))
    monkeypatch.setattr(m, "wait_done", lambda *a: body_with(m, "answer"))
    monkeypatch.setattr(m, "api_lines", lambda *a: ([], 0))
    monkeypatch.setattr(m, "runner_summaries", lambda *a: [])
    monkeypatch.setattr(m, "step_end", lambda step: None)
    f = m.turn(str(tmp_path), "S1", "c" * 36, {}, "p", [("hop3", ("10.0.0.1", 80), ())])
    assert signalled == [3]  # SIGINT only after snapshot.end arrived
    assert m.same_text(f["clients"]["hop3"], f["body"], CUR)[0]


def test_s5_gross_output_overflow_fails_even_with_dropped_bytes(m, monkeypatch):
    """review r1 adversarial: one item streaming 50,000 chars (> 16 KiB in any encoding) with dropped > 0."""
    turn = s5_turn(m, monkeypatch, b_mid_turn(m, "answer"), CAP_EVIDENCE)
    original = turn

    def overflow(*args, **kwargs):
        f = original(*args, **kwargs)
        f["clients"]["a"]._records.insert(2, fr(m, "item.output", CUR, 3, item=item_hash(m, "c1"), chars=50000))
        return f

    monkeypatch.setattr(m, "turn", overflow)
    verdicts = {}
    m.step_s5("ev", "c" * 36, {}, verdicts)
    assert verdicts["S5"] is False


def test_s5_r3_the_cap_is_a_printed_gap_not_a_criterion(m, monkeypatch, capsys):
    monkeypatch.setattr(m, "turn", s5_turn(m, monkeypatch, b_mid_turn(m, "answer"), {"outputDroppedBytes": 92000}))
    verdicts = {}
    m.step_s5("ev", "c" * 36, {}, verdicts)
    assert verdicts["S5"] is True and "S5 gap: output cap not measured" in capsys.readouterr().out


# --- r3: continuation after S0 False, S2 text criterion, S5 / S6 on prose -------------------------------------

S0RUN = "i90-p5-s0-20261114T155956Z"


def s0_state(m, tmp_path, monkeypatch, exit_doc=None, conversation=CUR, budget=1, decision=None):
    folder = Path(m.EVIDENCE) / S0RUN
    folder.mkdir(exist_ok=True)
    done = {"rc": 0, "stepRc": 0, "verdicts": {"S0": False, "release": True}, "runnerReleased": None,
            "clientsGone": True, "privateRemoved": None, "sessionRevoked": True, "finished": "x"}
    (folder / "exit.json").write_text(json.dumps(exit_doc if exit_doc is not None else done))
    (folder / "run.json").write_text(json.dumps({"conversation": conversation}))
    m.durable_json(m.BUDGET, {"cap": 8, "turns": [{"n": i + 1, "step": "S0", "outcome": "accepted"}
                                                  for i in range(budget)]})
    value = decision or json.loads((VM / "i90-p5-continue.json").read_text())
    value = dict(value, s0Run=S0RUN, conversationPrefix=CUR[:8])
    data = json.dumps(value).encode()
    path = tmp_path / "continue.json"
    path.write_bytes(data)
    monkeypatch.setattr(m, "CONTINUE_FILE", str(path))
    monkeypatch.setattr(m, "CONTINUE_SHA", hashlib.sha256(data).hexdigest())
    # the root-ownership part of read_pinned cannot hold in a test; the sha check is kept
    monkeypatch.setattr(m, "read_pinned", lambda p, want, name: (
        open(p, "rb").read() if hashlib.sha256(open(p, "rb").read()).hexdigest() == want
        else (_ for _ in ()).throw(m.Stop(3, "%s is not the reviewed root file" % name))))
    return path


def test_the_committed_decision_binds_the_s0_run_facts(m):
    value = json.loads((VM / "i90-p5-continue.json").read_text())
    assert value["s0Run"] == S0RUN and value["conversationPrefix"] == "9e504d3d"
    assert value["recorded"] == {"rc": 0, "stepRc": 0, "S0": False, "runnerReleased": None, "clientsGone": True,
                                 "privateRemoved": None, "sessionRevoked": True, "turnsUsed": 1}
    assert m.CONTINUE_SHA == hashlib.sha256((VM / "i90-p5-continue.json").read_bytes()).hexdigest()


def test_enable_continues_only_through_the_bound_decision(m, tmp_path, monkeypatch):
    s0_state(m, tmp_path, monkeypatch)
    assert "continuation decision" in m.s0_accepted()
    assert json.load(open(Path(m.EVIDENCE) / S0RUN / "exit.json"))["verdicts"]["S0"] is False  # never rewritten
    assert len(m.budget_read()) == 1  # never changed


@pytest.mark.parametrize("case", ["tampered", "other_run", "other_conversation", "unreadable_exit", "budget_two",
                                  "other_facts", "rc_nonzero"])
def test_the_continuation_refuses_anything_else(m, tmp_path, monkeypatch, case):
    if case == "other_conversation":
        s0_state(m, tmp_path, monkeypatch, conversation="aaaaaaaa" + CUR[8:])
    elif case == "budget_two":
        s0_state(m, tmp_path, monkeypatch, budget=2)
    elif case == "rc_nonzero":
        s0_state(m, tmp_path, monkeypatch, exit_doc={"rc": 5, "verdicts": {"S0": False}})
    elif case == "other_facts":
        s0_state(m, tmp_path, monkeypatch, exit_doc={"rc": 0, "stepRc": 0, "verdicts": {"S0": False},
                                                     "runnerReleased": None, "clientsGone": False,
                                                     "privateRemoved": None, "sessionRevoked": True})
    else:
        path = s0_state(m, tmp_path, monkeypatch)
        if case == "tampered":
            path.write_bytes(path.read_bytes().replace(b'"turnsUsed": 1', b'"turnsUsed": 2'))
        elif case == "other_run":
            (Path(m.EVIDENCE) / "i90-p5-s0-20261115T000000Z").mkdir()
        elif case == "unreadable_exit":
            (Path(m.EVIDENCE) / S0RUN / "exit.json").write_text("{")
    with pytest.raises(m.Stop) as stopped:
        m.s0_accepted()
    assert stopped.value.code == 3


def test_s2_r3_passes_on_the_text_criterion_with_no_command_events(m, monkeypatch, capsys):
    client = FakeClient([completed(m, "answer"), fr(m, "turn.completed", CUR, 3)])
    monkeypatch.setattr(m, "turn", lambda *a, **k: {"body": body_with(m, "answer"), "request": CUR,
                                                   "clients": {"hop3": client}, "runner": []})
    verdicts = {}
    m.step_s2("ev", "c" * 36, {}, verdicts)
    out = capsys.readouterr().out
    assert verdicts["S2"] is True and "0 (not observed in this run)" in out and "not emitted" not in out


def test_s2_r3_fails_when_the_text_differs(m, monkeypatch):
    client = FakeClient([completed(m, "answ"), fr(m, "turn.completed", CUR, 3)])
    monkeypatch.setattr(m, "turn", lambda *a, **k: {"body": body_with(m, "answer"), "request": CUR,
                                                   "clients": {"hop3": client}, "runner": []})
    verdicts = {}
    m.step_s2("ev", "c" * 36, {}, verdicts)
    assert verdicts["S2"] is False


def test_s5_r3_prompts_are_prose_only(m):
    assert "shell command" not in m.PROMPTS["S5"] and "seq 1 " not in m.PROMPTS["S5"]
    assert "shell command" not in m.PROMPTS["S6"] and "seq 1 " not in m.PROMPTS["S6"]


def test_s6_r3_unknown_prints_the_gap(m, monkeypatch, capsys):
    normal = FakeClient([completed(m, "answer"), fr(m, "turn.completed", CUR, 3)])
    paused = FakeClient([{"kind": "reader_paused", "unixNs": 1}])
    monkeypatch.setattr(m, "turn", lambda *a, **k: {"body": body_with(m, "answer"), "request": CUR,
                                                   "clients": {"n": normal, "p": paused}, "api": [{"reason": None}]})
    verdicts = {"S1": True}
    m.step_s6("ev", "c" * 36, {}, verdicts)
    assert verdicts == {"S1": True, "S6": None} and "S6 gap: stall and upstream cleanup not exercised" in \
        capsys.readouterr().out
