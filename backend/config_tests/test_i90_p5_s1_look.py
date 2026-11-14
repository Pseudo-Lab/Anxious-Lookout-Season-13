"""#90 P5 S1 look (infra/hosting/m3/chat-stream/vm/i90-p5-s1-look.py): read only, bounded output, text hashes compared
in order without claiming id correspondence. Pure: fake evidence and database. Needs infra/ mounted."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "infra" / "hosting" / "m3" / "chat-stream" / "vm" / "i90-p5-s1-look.py"
pytestmark = pytest.mark.skipif(not SCRIPT.exists(), reason="infra/ not mounted")
SECRET = "SECRET-answer-text"
CONV = "9e504d3d-0000-4000-8000-000000000001"
PREV, CUR, LIVE = "h-prev-request-0", "h-cur-request-01", "h-live-item-0001"


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture
def look(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("i90_p5_s1_look", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    evidence = tmp_path / "evidence"
    (evidence / "i90-p5-s0-20261114T155956Z").mkdir(parents=True)
    (evidence / "i90-p5-s0-20261114T155956Z" / "run.json").write_text(json.dumps({"conversation": CONV}))
    turns = evidence / "i90-p5-turns-20261114T163033Z"
    turns.mkdir()
    monkeypatch.setattr(module, "EVIDENCE", str(evidence))
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    module.turns = turns
    return module


def frames(answer=SECRET):
    return [{"kind": "headers", "status": 200},
            {"kind": "frame", "event": "snapshot.begin", "request": PREV, "turnState": "completed"},
            {"kind": "frame", "event": "turn.completed", "request": PREV, "status": "completed"},
            {"kind": "frame", "event": "snapshot.end", "items": {}},
            {"kind": "frame", "event": "turn.started", "request": CUR},
            {"kind": "frame", "event": "item.started", "request": CUR, "item": LIVE, "itemKind": "agentMessage"},
            {"kind": "frame", "event": "item.delta", "request": CUR, "item": LIVE, "chars": 3},
            {"kind": "frame", "event": "item.completed", "request": CUR, "item": LIVE, "textChars": len(answer),
             "textSha256": sha(answer), "textObserved": True},
            {"kind": "frame", "event": "turn.completed", "request": CUR, "status": "completed"},
            {"kind": "end", "reason": "local_interrupt"}]


def setup(look, monkeypatch, reader_frames=None, record_answers=(SECRET,), turns=2):
    (look.turns / "client-S1-hop3.jsonl").write_text("".join(json.dumps(f) + "\n" for f in (reader_frames or frames())))
    (look.turns / "front-evidence.json").write_text(json.dumps({"steps": [{"step": "S1", "items": [
        {"type": "message", "role": "user", "textChars": 5}, {"type": "message", "role": "assistant",
                                                              "textChars": len(record_answers[-1])}]}]}))
    last = {"status": "completed", "items": [{"type": "userMessage", "content": []}] +
            [{"type": "agentMessage", "id": "raw-id-%d" % i, "text": t} for i, t in enumerate(record_answers)]}
    record = {"turns": [{"status": "completed", "items": []}] * (turns - 1) + [last]}

    class Done:
        returncode, stdout = 0, (json.dumps({"r": record}) + "\n").encode()

    monkeypatch.setattr(look.subprocess, "run", lambda *a, **k: Done())


def lines(capsys):
    return {x["kind"]: x for x in (json.loads(y) for y in capsys.readouterr().out.splitlines())}


def test_different_ids_with_equal_final_text_compare_equal_and_nothing_leaks(look, monkeypatch, capsys):
    setup(look, monkeypatch)
    assert look.main() == 0
    raw = capsys.readouterr().out
    assert SECRET not in raw and "raw-id" not in raw and LIVE not in raw and CUR not in raw and CONV not in raw
    out = {x["kind"]: x for x in (json.loads(y) for y in raw.splitlines())}
    assert out["compare"]["result"] == "equal" and out["compare"]["readerSource"] == "item.completed"
    assert "not item correspondence" in out["compare"]["note"]
    assert [r["request"] for r in out["reader"]["requests"]] == [0, 1]  # order labels, never hashes
    assert out["reader"]["requests"][0]["events"]["snapshot.end"] == 1  # the prior replay stays with request 0
    assert out["record"]["s1TurnFresh"] is True
    assert out["front"]["S1Items"][1] == {"type": "message", "role": "assistant", "textChars": len(SECRET)}


def test_wrong_final_text_is_different(look, monkeypatch, capsys):
    setup(look, monkeypatch, record_answers=(SECRET + "x",))
    look.main()
    assert lines(capsys)["compare"]["result"] == "different"


def test_the_final_of_several_agent_messages_is_selected(look, monkeypatch, capsys):
    first = "first message"
    f = frames()
    f[5:5] = [{"kind": "frame", "event": "item.started", "request": CUR, "item": "h-live-item-0000",
               "itemKind": "agentMessage"},
              {"kind": "frame", "event": "item.completed", "request": CUR, "item": "h-live-item-0000",
               "textChars": len(first), "textSha256": sha(first), "textObserved": True}]
    setup(look, monkeypatch, reader_frames=f, record_answers=(first, SECRET))
    look.main()
    out = lines(capsys)["compare"]
    assert out["result"] == "equal" and out["readerPosition"] == 1 and out["readerCandidates"] == 2
    assert out["recordPosition"] == 1 and out["recordCandidates"] == 2


@pytest.mark.parametrize("case,reason", [
    ("stale_record", "the record is not exactly S0 + one completed S1 turn"),
    ("open_snapshot", "a snapshot of the request is still open on the reader"),
    ("no_summary", "the reader has no summary with observed text for its final agentMessage"),
    ("no_completion", "no completed request on the reader")])
def test_missing_or_ambiguous_evidence_is_unknown(look, monkeypatch, capsys, case, reason):
    f = frames()
    if case == "open_snapshot":
        f.insert(8, {"kind": "frame", "event": "snapshot.begin", "request": CUR, "turnState": "completed"})
    elif case == "no_summary":
        f = [x for x in f if x.get("event") != "item.completed"]
    elif case == "no_completion":
        f = [x for x in f if not (x.get("event") == "turn.completed" and x.get("request") == CUR)]
    setup(look, monkeypatch, reader_frames=f, turns=3 if case == "stale_record" else 2)
    look.main()
    out = lines(capsys)["compare"]
    assert out == {**out, "result": "unknown", "reason": reason}


def test_snapshot_only_reconstruction_uses_snapshot_kinds_and_end_items(look, monkeypatch, capsys):
    f = [{"kind": "frame", "event": "snapshot.begin", "request": CUR, "turnState": "completed"},
         {"kind": "frame", "event": "snapshot.item", "request": CUR, "item": LIVE, "itemKind": "agentMessage"},
         {"kind": "frame", "event": "turn.completed", "request": CUR, "status": "completed"},
         {"kind": "frame", "event": "snapshot.end", "request": CUR,
          "items": {LIVE: {"textChars": len(SECRET), "textSha256": sha(SECRET), "textObserved": True}}}]
    setup(look, monkeypatch, reader_frames=f)
    look.main()
    out = lines(capsys)["compare"]
    assert out["result"] == "equal" and out["readerSource"] == "snapshot.end"


def test_refuses_without_the_reader_file_or_root(look, monkeypatch, capsys):
    assert look.main() == 3
    monkeypatch.setattr(look.os, "geteuid", lambda: 1000)
    assert look.main() == 3


def test_pins_for_r5_hash_the_turns_and_disable_exits_and_list_the_budget(look, monkeypatch, capsys):
    setup(look, monkeypatch)
    evidence = look.turns.parent
    turns_exit = json.dumps({"rc": 0, "verdicts": {"S1": False}}).encode()
    (look.turns / "exit.json").write_bytes(turns_exit)
    disable = evidence / "i90-p5-disable-20261114T163347Z"
    disable.mkdir()
    (disable / "exit.json").write_bytes(b'{"rc": 0}')
    (evidence / "i90-p5-budget.json").write_text(json.dumps({"cap": 8, "turns": [
        {"n": 1, "step": "S0", "outcome": "accepted", "reservedAt": "x"},
        {"n": 2, "step": "S1", "outcome": "accepted", "reservedAt": "y"}]}))
    look.main()
    out = lines(capsys)["pins"]
    assert out["turns"] == {"run": look.turns.name, "exitSha256": sha256_bytes(turns_exit), "rc": 0}
    assert out["disable"] == {"run": disable.name, "exitSha256": sha256_bytes(b'{"rc": 0}'), "rc": 0}
    assert out["budget"] == [{"n": 1, "step": "S0", "outcome": "accepted"}, {"n": 2, "step": "S1", "outcome": "accepted"}]


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()
