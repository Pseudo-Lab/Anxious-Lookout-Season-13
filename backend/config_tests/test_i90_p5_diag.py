"""#90 P5 S0 observation r1 (infra/hosting/m3/chat-stream/vm/i90-p5-diag.py): read only, fixed-list counts, no a/b.
Pure: fakes for the database, a temporary codex/sessions tree. Needs infra/ mounted."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "infra" / "hosting" / "m3" / "chat-stream" / "vm" / "i90-p5-diag.py"
pytestmark = pytest.mark.skipif(not SCRIPT.exists(), reason="infra/ not mounted")
SECRET = "SECRET-MARKER-answer-prompt-argument"
THREAD = "0199aaaa-bbbb-7ccc-8ddd-eeeeffff0000"
TURN = "turn-1"
CONV = "9e504d3d-0000-4000-8000-000000000001"


@pytest.fixture
def d(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("i90_p5_diag", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    evidence = tmp_path / "evidence"
    run = evidence / "i90-p5-s0-20261114T155956Z"
    run.mkdir(parents=True)
    (run / "run.json").write_text(json.dumps({"conversation": CONV}))
    (run / "exit.json").write_text(json.dumps({"rc": 0, "verdicts": {"S0": False}}))
    (run / "runner-s0.log").write_text("a runner_stream_summary line\nApplication shutdown complete\n")
    sessions = tmp_path / "codex" / "sessions" / "2026" / "11" / "14"
    sessions.mkdir(parents=True)
    monkeypatch.setattr(module, "EVIDENCE", str(evidence))
    monkeypatch.setattr(module, "CODEX_SESSIONS", str(tmp_path / "codex" / "sessions"))
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    module.sessions = sessions
    return module


def record(items, extra=None):
    value = {"turns": [{"id": TURN, "status": "completed", "items": items}], "nativeThreadId": THREAD,
             "platformRequests": [{"text": SECRET}]}
    value.update(extra or {})
    return {"r": value}


def fake(d, monkeypatch, row, api_args=("uvicorn", "--no-access-log")):
    def run(cmd, stdin=None, timeout=60):
        if "psql" in cmd:
            return 0, json.dumps(row) + "\n"
        return 0, json.dumps({"spec": {"template": {"spec": {"containers": [{"name": "api",
                                                                             "command": list(api_args)}]}}}})
    monkeypatch.setattr(d, "run", run)


def rollout(d, events):
    path = d.sessions / ("rollout-2026-11-14T16-00-00-%s.jsonl" % THREAD)
    path.write_text("".join(json.dumps(e) + "\n" for e in events))


MESSAGES = [{"type": "userMessage", "content": [{"type": "text", "text": SECRET}]},
            {"type": "agentMessage", "text": SECRET}]
FRAME = [{"type": "session_meta", "payload": {"id": THREAD}},
         {"type": "event_msg", "payload": {"type": "task_started", "turn_id": TURN}}]
END = [{"type": "event_msg", "payload": {"type": "task_complete", "turn_id": TURN}}]


def exec_call(code):
    return [{"type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec", "call_id": "c1",
                                                  "status": "completed", "input": code}},
            {"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": "c1",
                                                  "output": SECRET}}]


def lines(capsys):
    return {x["kind"]: x for x in (json.loads(y) for y in capsys.readouterr().out.splitlines())}


def test_counts_and_the_fixed_conclusion_never_an_a_or_b(d, monkeypatch, capsys):
    fake(d, monkeypatch, record(MESSAGES))
    rollout(d, FRAME + exec_call("await tools.research_list({q: '%s'});" % SECRET) + END)
    assert d.main() == 0
    out = lines(capsys)
    assert out["conclusion"]["text"] == "observed counts only; invocation unknown unless correlated"
    assert "result" not in json.dumps(out)
    assert out["native"]["itemTypes"] == {"userMessage": 1, "agentMessage": 1}
    assert out["native"]["turnStatuses"] == {"completed": 1}
    assert out["references"]["execReferences"] == {"research_list": 1}
    assert out["references"]["note"] == "references only, not invocation evidence"
    assert out["rollout"]["lastTurn"] == {"starts": 1, "ends": 1, "startBeforeEnd": True, "aborted": False}
    assert out["rollout"]["sessionMetaThreadMatch"] is True


def test_a_dead_branch_reference_is_only_a_reference(d, monkeypatch, capsys):
    """review r0 adversarial: `if (false) { tools.research_list() }` is counted as a reference, nothing more."""
    fake(d, monkeypatch, record(MESSAGES))
    rollout(d, FRAME + exec_call("if (false) { await tools.research_list({}); }") + END)
    d.main()
    out = lines(capsys)
    assert out["references"]["execReferences"] == {"research_list": 1}
    assert out["references"]["correlatedFunctionPairs"] == 0 and "invocation unknown" in out["conclusion"]["text"]


def test_an_empty_or_truncated_rollout_shows_missing_turn_coverage(d, monkeypatch, capsys):
    """review r0 adversarial: empty = no start / end for the turn, never a conclusion."""
    fake(d, monkeypatch, record(MESSAGES))
    rollout(d, [])
    d.main()
    out = lines(capsys)
    assert out["rollout"]["lastTurn"] == {"starts": 0, "ends": 0, "startBeforeEnd": False, "aborted": False}
    assert out["rollout"]["sessionMetaThreadMatch"] is None
    rollout(d, FRAME)  # truncated: started, never ended
    d.main()
    assert lines(capsys)["rollout"]["lastTurn"]["ends"] == 0


def test_unknown_strings_are_counted_never_echoed(d, monkeypatch, capsys):
    """review r0 adversarial: a secret marker as a tool name, a record key and an event type never leaks."""
    fake(d, monkeypatch, record(MESSAGES + [{"type": SECRET}], extra={SECRET: 1}))
    rollout(d, FRAME + [{"type": "event_msg", "payload": {"type": SECRET, "turn_id": TURN}},
                        {"type": SECRET, "payload": {}},
                        {"type": "response_item", "payload": {"type": "function_call", "name": "research_" + SECRET,
                                                              "call_id": "f1"}}]
            + exec_call("tools.research_%s(); tools.%s()" % (SECRET, SECRET)) + END)
    d.main()
    raw = capsys.readouterr().out
    assert SECRET not in raw and THREAD not in raw and CONV not in raw and TURN not in raw
    out = {x["kind"]: x for x in (json.loads(y) for y in raw.splitlines())}
    assert out["native"]["itemTypes"]["unknown"] == 1 and out["native"]["unknownKeys"] == 1
    assert out["rollout"]["eventKinds"]["unknown"] == 2
    assert out["references"]["execReferences"] == {"unknown": 2}
    assert out["references"]["functionCalls"] == {"unknown": 1}


def test_correlated_function_pairs_are_counted_separately(d, monkeypatch, capsys):
    fake(d, monkeypatch, record(MESSAGES))
    rollout(d, FRAME + [{"type": "response_item", "payload": {"type": "function_call", "name": "research_list",
                                                              "call_id": "f1"}},
                        {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "f1",
                                                              "output": SECRET}},
                        {"type": "response_item", "payload": {"type": "function_call", "name": "research_get",
                                                              "call_id": "f2"}}] + END)
    d.main()
    out = lines(capsys)
    assert out["references"]["functionCalls"] == {"research_list": 1, "research_get": 1}
    assert out["references"]["correlatedFunctionPairs"] == 1


def test_callback_logging_is_unavailable_not_zero_and_runner_logs(d, monkeypatch, capsys):
    fake(d, monkeypatch, record(MESSAGES))
    rollout(d, FRAME + END)
    d.main()
    out = lines(capsys)
    assert out["callbackLogging"]["apiNoAccessLog"] is True and out["callbackLogging"]["observedZero"] is False
    assert out["callbackLogging"]["callbackLines"].startswith("unavailable")
    assert out["runnerLogs"] == {"kind": "runnerLogs", "podLogs": "unavailable", "filteredEvidenceLines": 2}


def test_no_rollout_is_reported_with_a_fixed_reason(d, monkeypatch, capsys):
    fake(d, monkeypatch, record(MESSAGES))
    assert d.main() == 0
    out = lines(capsys)
    assert out["rollout"] == {"kind": "rollout", "found": False, "reason": "no single rollout file for the thread"}
    assert out["references"]["available"] is False


def test_refuses_when_not_root_or_the_record_is_missing(d, monkeypatch, capsys):
    fake(d, monkeypatch, {"r": None})
    assert d.main() == 3
    monkeypatch.setattr(d.os, "geteuid", lambda: 1000)
    assert d.main() == 3


def test_a_symlinked_directory_in_sessions_is_not_followed(d, monkeypatch, capsys, tmp_path):
    fake(d, monkeypatch, record(MESSAGES))
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / ("rollout-x-%s.jsonl" % THREAD)).write_text(json.dumps(FRAME[0]) + "\n")
    (d.sessions / "link").symlink_to(outside)
    d.main()
    assert lines(capsys)["rollout"]["found"] is False
