"""#90 P5 S0 diagnosis (infra/hosting/m3/chat-stream/vm/i90-p5-diag.py): read only, bounded output. Pure: fakes for
the database, a temporary codex/sessions tree. Needs infra/ mounted."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "infra" / "hosting" / "m3" / "chat-stream" / "vm" / "i90-p5-diag.py"
pytestmark = pytest.mark.skipif(not SCRIPT.exists(), reason="infra/ not mounted")
SECRET = "answer text, prompt and tool arguments never printed"
THREAD = "0199aaaa-bbbb-7ccc-8ddd-eeeeffff0000"
CONV = "9e504d3d-0000-4000-8000-000000000001"


@pytest.fixture
def d(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("i90_p5_diag", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    evidence = tmp_path / "evidence"
    run = evidence / "i90-p5-s0-20261114T155956Z"
    run.mkdir(parents=True)
    (run / "run.json").write_text(json.dumps({"conversation": CONV, "started": "2026-11-14T15:59:56Z"}))
    (run / "exit.json").write_text(json.dumps({"rc": 0, "verdicts": {"S0": False}, "finished": "x"}))
    (run / "runner-s0.log").write_text("a runner_stream_summary line\nApplication shutdown complete\n")
    sessions = tmp_path / "codex" / "sessions" / "2026" / "11" / "14"
    sessions.mkdir(parents=True)
    monkeypatch.setattr(module, "EVIDENCE", str(evidence))
    monkeypatch.setattr(module, "CODEX_SESSIONS", str(tmp_path / "codex" / "sessions"))
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    module.sessions = sessions
    return module


def record(items, thread=THREAD):
    return {"r": {"turns": [{"status": "completed", "items": items}], "nativeThreadId": thread,
                  "platformRequests": [{"text": SECRET}]}, "state": "idle", "updated": "t", "version": 3}


def fake(d, monkeypatch, row, api_args=("uvicorn", "--no-access-log")):
    def run(cmd, stdin=None, timeout=60):
        if "psql" in cmd:
            return 0, json.dumps(row) + "\n"
        return 0, json.dumps({"spec": {"template": {"spec": {"containers": [{"name": "api",
                                                                             "command": list(api_args)}]}}}})
    monkeypatch.setattr(d, "run", run)


def rollout(d, events):
    path = d.sessions / ("rollout-2026-11-14T16-00-00-%s.jsonl" % THREAD)
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")


MESSAGES = [{"type": "userMessage", "content": [{"type": "text", "text": SECRET}]},
            {"type": "agentMessage", "text": SECRET}]


def exec_call(code):
    return [{"type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec", "call_id": "c1",
                                                  "status": "completed", "input": code}},
            {"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": "c1",
                                                  "output": SECRET}}]


def lines(capsys):
    return [json.loads(x) for x in capsys.readouterr().out.splitlines()]


def test_b_when_the_rollout_has_a_code_mode_research_call_and_the_record_no_tool_item(d, monkeypatch, capsys):
    fake(d, monkeypatch, record(MESSAGES))
    rollout(d, [{"type": "event_msg", "payload": {"type": "task_started"}}]
            + exec_call("const r = await tools.research_list({q: '%s'});" % SECRET))
    assert d.main() == 0
    out = lines(capsys)
    assert out[-1]["result"] == "b"
    roll = [x for x in out if x["kind"] == "rollout"][0]
    assert roll["nestedToolNames"] == {"research_list": 1} and roll["customToolCalls"] == {"exec": 1}
    text = json.dumps(out)
    assert SECRET not in text and THREAD not in text and CONV not in text


def test_a_when_the_rollout_is_intact_and_has_no_tool_record(d, monkeypatch, capsys):
    fake(d, monkeypatch, record(MESSAGES))
    rollout(d, [{"type": "event_msg", "payload": {"type": "task_started"}},
                {"type": "response_item", "payload": {"type": "message", "content": [{"text": SECRET}]}}])
    assert d.main() == 0
    assert lines(capsys)[-1]["result"] == "a"


def test_unknown_when_the_rollout_is_missing_or_unreadable(d, monkeypatch, capsys):
    fake(d, monkeypatch, record(MESSAGES))
    assert d.main() == 0
    assert lines(capsys)[-1]["result"] == "unknown"
    rollout(d, [])
    (d.sessions / ("rollout-2026-11-14T16-00-00-%s.jsonl" % THREAD)).write_text("{not json\n")
    assert d.main() == 0
    assert lines(capsys)[-1]["result"] == "unknown"


def test_unknown_when_calls_name_no_research_tool(d, monkeypatch, capsys):
    fake(d, monkeypatch, record(MESSAGES))
    rollout(d, exec_call("await tools.exec_command({cmd: 'ls'}); await tools.secret_tool();"))
    assert d.main() == 0
    out = lines(capsys)
    assert out[-1]["result"] == "unknown"
    assert [x for x in out if x["kind"] == "rollout"][0]["nestedToolNames"] == {"exec_command": 1, "other": 1}


def test_native_record_reports_types_and_counts_only(d, monkeypatch, capsys):
    fake(d, monkeypatch, record(MESSAGES + [{"type": "dynamicToolCall", "arguments": {"q": SECRET}}]))
    rollout(d, exec_call("tools.research_list()"))
    assert d.main() == 0
    out = lines(capsys)
    native = [x for x in out if x["kind"] == "native"][0]
    assert native["turns"] == [{"status": "completed", "itemTypes": {"userMessage": 1, "agentMessage": 1,
                                                                     "dynamicToolCall": 1}}]
    assert native["platformRequests"] == 1 and out[-1]["result"] == "unknown"  # the record has a tool item
    assert SECRET not in json.dumps(out)


def test_callback_logging_coverage_and_runner_logs_are_reported(d, monkeypatch, capsys):
    fake(d, monkeypatch, record(MESSAGES))
    rollout(d, [])
    d.main()
    out = {x["kind"]: x for x in lines(capsys)}
    assert out["callbackLogging"]["apiNoAccessLog"] is True and "proves nothing" in out["callbackLogging"]["meaning"]
    assert out["runnerLogs"]["filteredEvidenceLines"] == 2 and "unavailable" in out["runnerLogs"]["podLogs"]


def test_refuses_when_not_root_or_the_record_is_missing(d, monkeypatch, capsys):
    fake(d, monkeypatch, {"r": None})
    assert d.main() == 3
    monkeypatch.setattr(d.os, "geteuid", lambda: 1000)
    assert d.main() == 3


def test_a_symlinked_directory_in_sessions_is_not_followed(d, monkeypatch, capsys, tmp_path):
    fake(d, monkeypatch, record(MESSAGES))
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / ("rollout-x-%s.jsonl" % THREAD)).write_text(json.dumps(exec_call("tools.research_list()")[0]) + "\n")
    (d.sessions / "link").symlink_to(outside)
    assert d.main() == 0
    out = lines(capsys)
    assert [x for x in out if x["kind"] == "rollout"][0]["found"] is False and out[-1]["result"] == "unknown"
