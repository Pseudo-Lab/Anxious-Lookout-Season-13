"""#90 stage D3 (infra/hosting/m3/chat-stream/vm/i90-d3.py, P4) against a fake guest: kubectl (api Deployment,
patch, rollout, Pods, logs), psql, docker and the measuring client answered in memory, with a fake clock. Pure: no
VM, no Docker, no network. Needs infra/ mounted (skipped otherwise)."""
import importlib.util
import json
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
VM = ROOT / "infra" / "hosting" / "m3" / "chat-stream" / "vm"
pytestmark = pytest.mark.skipif(not VM.exists(), reason="infra/ not mounted")
TOOL = "sha256:" + "f" * 64
CONVERSATION = "11111111-2222-4333-8444-555555555555"


def load():
    spec = importlib.util.spec_from_file_location("i90_d3", VM / "i90-d3.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stamp(m, t):
    return m.time.strftime("%Y-%m-%dT%H:%M:%S", m.time.gmtime(t)) + ".000000000Z"


class Guest:
    def __init__(self, m, doc, **faults):
        self.m, self.doc, self.faults = m, doc, faults
        self.clock = 1_800_000_000.0
        self.rv = 100
        self.api = {"metadata": {"uid": m.API[2], "resourceVersion": str(self.rv)},
                    "spec": {"replicas": 1, "template": {"metadata": {"labels": {"app": "hosting-api"}}, "spec": {
                        "containers": [{"name": "api", "image": doc["newApiImage"],
                                        "env": [{"name": "CODEX_USER_RUNNER_IDLE_SECONDS", "value": "1800"}]}],
                        "initContainers": [{"name": "runner-pool-input", "image": "init"}]}}},
                    "status": {"readyReplicas": 1, "replicas": 1}}
        if faults.get("baseline_restart"):
            self.api["spec"]["template"]["metadata"]["annotations"] = {m.RESTARTED: "2026-11-01T00:00:00Z"}
        self.api_pods = [{"metadata": {"name": "api-1", "uid": "api-pod-1"}, "spec": {"terminationGracePeriodSeconds": 30}}]
        self.old_gone_at = None
        self.registry = {"phase": "idle_stopped", "gen": 30, "pod": None, "active": 0, "state": m.STATE}
        self.runner = []
        self.stops = []
        self.sessions = {}
        self.open = []           # (start, end) of accepted client streams
        self.summary_at = []     # end times of accepted streams (one api_stream_summary each)
        self.requests = []
        self.idle_at = None
        self.patches = []
        self.clients_started = 0
        self.restarts = 0

    # clock
    def time(self):
        return self.clock

    def sleep(self, seconds):
        self.clock += max(seconds, 0.01)

    # subprocess.run
    def run(self, cmd, timeout=60, stdin=None):
        self.tick()
        if cmd[0] == self.m.DOCKER:
            if cmd[1:3] == ["image", "inspect"]:
                return 0, TOOL
            if cmd[1] == "run":  # the private-file probe container
                return (1, "") if self.faults.get("probe_fails") else (0, "readable")
            if cmd[1:3] == ["ps", "-a"]:
                return 0, "leftover" if self.faults.get("leftover") and "^issue90-i90-d3-" in " ".join(cmd) else ""
            return 0, ""
        args = cmd[2:]
        if "exec" in args:
            return self.sql(stdin.decode())
        if args[:2] == ["-n", "m2-hosting"]:
            rest = args[2:]
            if rest[:3] == ["get", "deployment", "api"]:
                return 0, json.dumps(self.api)
            if rest[:3] == ["patch", "deployment", "api"]:
                ops = json.loads(rest[-1])
                self.patches.append(ops)
                result = self.m.apply_patch(self.api, ops)
                if result is None:
                    return 1, ""
                self.api = result
                self.bump()
                return 0, ""
            if rest[:2] == ["rollout", "status"]:
                return 0, ""
            if rest[:2] == ["rollout", "restart"]:
                self.restarts += 1
                meta = self.api["spec"]["template"].setdefault("metadata", {})
                meta.setdefault("annotations", {})[self.m.RESTARTED] = "2026-11-14T07:00:00Z"
                self.bump()
                old = self.api_pods[0]
                old["metadata"]["deletionTimestamp"] = "x"
                self.old_gone_at = self.clock + 3
                self.api_pods.append({"metadata": {"name": "api-2", "uid": "api-pod-2"},
                                      "spec": {"terminationGracePeriodSeconds": 30}})
                return 0, ""
            if rest[:2] == ["get", "pods"]:
                return 0, json.dumps({"items": self.api_pods})
            if rest[0] == "logs":
                since = [a for a in rest if a.startswith("--since-time=")][0].split("=", 1)[1]
                lines = ["%s INFO api_stream_summary {\"reason\": \"client_closed\", \"session\": \"%s\"}"
                         % (stamp(self.m, t), CONVERSATION) for t in self.summary_at
                         if t <= self.clock and stamp(self.m, t)[:19] >= since[:19]]
                return 0, "\n".join(lines)
        if args[:2] == ["-n", "codex-runner"]:
            return 0, json.dumps({"items": self.runner})
        return 2, ""

    def bump(self):
        self.rv += 1
        self.api["metadata"]["resourceVersion"] = str(self.rv)

    def tick(self):
        if self.old_gone_at is not None and self.clock >= self.old_gone_at:
            self.api_pods = [p for p in self.api_pods if not p["metadata"].get("deletionTimestamp")]
            self.old_gone_at = None
        if self.idle_at is not None and self.clock >= self.idle_at:
            self.stop_runner()
            self.idle_at = None

    def sql(self, text):
        tag = re.search(r"/\*i90d3:(\w+)\*/", text).group(1)
        if tag == "registry":
            return 0, json.dumps(self.registry)
        if tag == "sessions":
            return 0, json.dumps({"n": sum(1 for v in self.sessions.values() if v)})
        if tag == "session" and "INSERT" in text:
            token_hash = re.search(r"VALUES \('([0-9a-f]{64})'", text).group(1)
            self.sessions[token_hash] = True
            return 0, json.dumps({"id": token_hash[:16]})
        if tag == "session":
            token_hash = re.search(r"token_hash='([0-9a-f]{64})'", text).group(1)
            return 0, json.dumps({"n": 1 if self.sessions.get(token_hash) else 0})
        if tag == "revoke" and self.faults.get("revoke_fails"):
            return 3, ""
        if tag == "revoke":
            token_hash = re.search(r"token_hash='([0-9a-f]{64})'", text).group(1)
            self.sessions[token_hash] = False
            return 0, json.dumps({"deleted": 1})
        if tag == "stops":
            generation = int(re.search(r"generation=(\d+)", text).group(1))
            return 0, "\n".join(json.dumps(s) for s in self.stops if s["gen"] == generation)
        return 3, ""

    def stop_runner(self):
        if self.faults.get("stuck"):  # the stop is accepted but the runner never goes away
            return
        if self.registry["pod"]:
            self.stops.append({"gen": self.registry["gen"], "receipt": 1, "clean": True, "completed": True,
                               "requested": self.clock, "completedAt": self.clock + 2})
        self.registry.update(phase="idle_stopped", pod=None)
        self.runner = []

    # HTTP
    def call(self, method, path, body=None, session=True):
        self.tick()
        self.requests.append((method, path))
        if path == "/api/health":
            return 200, {"status": "ok"}, ""
        if path.startswith(self.m.BASE + "/sessions?"):
            return 200, {"items": [{"id": CONVERSATION}], "nextCursor": None}, ""
        if path == self.m.BASE + "/codex/runner" and method == "POST":
            if self.faults.get("signal_at_entry"):
                self.faults["signal_at_entry"] = False
                raise self.m.Interrupted()
            if self.faults.get("entry_refused"):
                return 503, None, ""
            gen = self.registry["gen"] + 1
            self.registry.update(gen=gen, phase="ready", pod="runner-pod-%d" % gen)
            started = self.m.time.strftime("%Y-%m-%dT%H:%M:%SZ", self.m.time.gmtime(self.clock))
            self.runner = [{"metadata": {"name": self.m.POD_NAME, "uid": "runner-pod-%d" % gen},
                            "status": {"phase": "Running", "containerStatuses": [
                                {"ready": True, "state": {"running": {"startedAt": started}}}]}}]
            if self.faults.get("idle"):
                self.idle_at = self.clock + 1830
            return 202, {}, ""
        if path == self.m.BASE + "/codex/status":
            ready = not self.faults.get("never_ready")
            return 200, {"available": ready, "reason": None if ready else "runner_unreachable"}, ""
        if path == self.m.BASE + "/codex/runner" and method == "DELETE":
            if self.faults.get("signal_at_delete"):
                raise self.m.Interrupted()
            if self.faults.get("delete_503"):
                return 503, None, ""
            self.stop_runner()
            return 200, {"terminationRequested": True}, ""
        return 500, None, ""

    # subprocess.Popen
    def popen(self, cmd, stdout=None, stderr=None):
        return FakeProcess(self, cmd, stdout)


class FakeProcess:
    """A client container (records written as the client would, its `end` only once it ended) or a log follower."""

    def __init__(self, guest, cmd, stream):
        self.guest, self.cmd, self.stream, self.returncode, self.ended = guest, cmd, stream, None, False
        g = guest
        if cmd[0] != g.m.DOCKER:  # a log follower
            stream.write(b"2026-11-14T07:00:00Z INFO: Application shutdown complete.\n")
            stream.flush()
            self.end_at = None
            return
        mode = cmd[cmd.index("--mode") + 1]
        duration = float(cmd[cmd.index("--duration") + 1])
        assert cmd[cmd.index("--connect-address") + 1] == g.m.API_HOST
        assert cmd[cmd.index("--url") + 1].startswith("http://192.88.99.1/api/research/sessions/")
        assert "label=disable" in cmd and cmd[cmd.index("--user") + 1] == "10001:10001"
        g.clients_started += 1
        live = [s for s in g.open if s[0] <= g.clock < s[1]]
        status = 200 if len(live) < 3 or g.faults.get("no_cap") else 429
        records = [{"kind": "headers", "status": status}]
        if status == 200:
            self.end_at = g.clock + duration
            if g.faults.get("dies_early"):
                self.end_at = g.clock + 1
            g.open.append((g.clock, self.end_at))
            self.slot = len(g.summary_at)
            g.summary_at.append(self.end_at + 1)
            if mode == "no-read":
                if not g.faults.get("no_pause"):
                    records.append({"kind": "reader_paused"})
            else:
                records.append({"kind": "frame", "event": "runner", "available": False})
            self.code, self.reason = 0, "local_deadline"
        else:
            self.end_at = g.clock
            self.code, self.reason = 2, "http_error"
        self.write(records)

    def write(self, records):
        self.stream.write(("\n".join(json.dumps(r) for r in records) + "\n").encode())
        self.stream.flush()

    def finish(self):
        if not self.ended and self.end_at is not None:
            self.ended = True
            self.write([{"kind": "end", "reason": self.reason}])

    def poll(self):
        if self.end_at is None or self.guest.clock < self.end_at:
            return None
        self.finish()
        self.returncode = self.code
        return self.code

    def wait(self, timeout=None):
        if self.end_at is not None:
            self.guest.clock = max(self.guest.clock, self.end_at)
            self.finish()
            self.returncode = self.code
        else:
            self.returncode = 0
        return self.returncode

    def terminate(self):
        if self.end_at is not None:
            self.end_at = min(self.end_at, self.guest.clock)
            if hasattr(self, 'slot'):  # a closed stream logs its summary at the close
                self.guest.summary_at[self.slot] = min(self.guest.summary_at[self.slot], self.guest.clock + 1)
            self.finish()
        else:
            self.end_at = self.guest.clock
            self.code = 0

    def kill(self):
        self.terminate()


@pytest.fixture
def guest(tmp_path, monkeypatch):
    def make(**faults):
        m = load()
        doc = json.loads((VM / "i90-expected.json").read_text())
        doc["toolImage"] = TOOL
        g = Guest(m, doc, **faults)
        doc["apiTemplateSha256"] = m.template_sha(g.api)  # the fake api is the "stage C" template
        evidence = tmp_path / "evidence"
        evidence.mkdir(mode=0o700, exist_ok=True)
        monkeypatch.setattr(m, "EVIDENCE", str(evidence))
        monkeypatch.setattr(m, "LOCK", str(evidence / "i90-d.lock"))
        monkeypatch.setattr(m, "expected", lambda: doc)
        monkeypatch.setattr(m, "read_pinned", lambda path, want, name: b"print('client')\n")
        monkeypatch.setattr(m, "run", g.run)
        monkeypatch.setattr(m, "call", g.call)
        monkeypatch.setattr(m.subprocess, "Popen", g.popen)
        monkeypatch.setattr(m.time, "time", g.time)
        monkeypatch.setattr(m.time, "sleep", g.sleep)
        monkeypatch.setattr(m.os, "geteuid", lambda: 0)
        monkeypatch.setattr(m.os, "fchown", lambda fd, uid, gid: None)
        monkeypatch.setattr(m.signal, "signal", lambda *a: None)
        return m, g, evidence, doc
    return make


def run_dir(evidence, action):
    found = [d for d in evidence.iterdir() if d.name.startswith("i90-d3-%s-" % action)]
    assert len(found) == 1
    return found[0]


def out_of(evidence, action):
    return (run_dir(evidence, action) / "out.txt").read_text()


def test_check_is_read_only(guest):
    m, g, evidence, _ = guest()
    assert m.main(["i90-d3.py", "check"]) == 0
    assert list(evidence.iterdir()) == [] and g.patches == [] and g.requests == []


def test_flag_on_adds_only_the_entry_and_records_the_baseline(guest):
    m, g, evidence, doc = guest()
    assert m.main(["i90-d3.py", "flag-on"]) == 0
    assert m.flag_state(g.api) == "present" and m.template_sha(g.api, without_flag=True) == doc["apiTemplateSha256"]
    assert json.loads((run_dir(evidence, "flag-on") / "run.json").read_text())["restartedAtBefore"] == {"absent": True}
    tests = [op for op in g.patches[0] if op["op"] == "test"]
    assert {op["path"] for op in tests} == {"/metadata/uid", "/metadata/resourceVersion",
                                            "/spec/template/spec/containers/0/name"}
    assert m.main(["i90-d3.py", "flag-on"]) == 3  # once


def test_full_sequence_restores_the_stage_c_template(guest):
    m, g, evidence, doc = guest()
    assert m.main(["i90-d3.py", "flag-on"]) == 0
    assert m.main(["i90-d3.py", "p4"]) == 0
    out = out_of(evidence, "p4")
    for item in ("P4-0", "P4-1", "P4-3", "P4-4"):
        assert "verdict %s: True" % item in out, out
    assert "verdict overall pass: True" in out
    assert m.RESTARTED in g.api["spec"]["template"]["metadata"]["annotations"]
    assert not any(g.sessions.values()) and not (run_dir(evidence, "p4") / "private").exists()
    assert m.main(["i90-d3.py", "flag-off"]) == 0
    out = out_of(evidence, "flag-off")
    assert "equal: True" in out and m.template_sha(g.api) == doc["apiTemplateSha256"]
    assert m.flag_state(g.api) == "absent" and "annotations" not in g.api["spec"]["template"]["metadata"]


def test_flag_off_restores_a_baseline_restarted_value(guest):
    m, g, evidence, doc = guest(baseline_restart=True)
    assert m.main(["i90-d3.py", "flag-on"]) == 0
    assert m.main(["i90-d3.py", "p4"]) == 0
    assert m.main(["i90-d3.py", "flag-off"]) == 0
    assert g.api["spec"]["template"]["metadata"]["annotations"][m.RESTARTED] == "2026-11-01T00:00:00Z"
    assert m.template_sha(g.api) == doc["apiTemplateSha256"]


def test_flag_off_refuses_when_the_annotation_was_changed_by_someone_else(guest):
    m, g, evidence, doc = guest()
    assert m.main(["i90-d3.py", "flag-on"]) == 0
    assert m.main(["i90-d3.py", "p4"]) == 0
    g.api["spec"]["template"]["metadata"]["annotations"][m.RESTARTED] = "another-writer"
    before = len(g.patches)
    assert m.main(["i90-d3.py", "flag-off"]) == 3 and len(g.patches) == before  # nothing sent


def test_a_false_p4_0_stops_before_any_runner_entry(guest):
    """PM stop policy: P4-0 False (a 4th stream accepted) -> no runner entry, no restart, no later client."""
    m, g, evidence, doc = guest(no_cap=True)
    assert m.main(["i90-d3.py", "flag-on"]) == 0
    assert m.main(["i90-d3.py", "p4"]) == 0
    out = out_of(evidence, "p4")
    assert "verdict P4-0: False" in out and "verdict overall pass: False" in out and "stopped at: P4-0" in out
    for item in ("P4-1", "P4-3", "P4-4"):
        assert "verdict %s: unknown" % item in out
    assert not [p for _, p in g.requests if p.endswith("/codex/runner")] and g.restarts == 0
    assert g.clients_started == 5 and not any(g.sessions.values())


def test_no_reader_paused_skips_the_restart_and_stops(guest):
    """Review major: a missing reader_paused is not a pass: P4-3 False, no restart, P4-4 never starts."""
    m, g, evidence, doc = guest(no_pause=True)
    assert m.main(["i90-d3.py", "flag-on"]) == 0
    assert m.main(["i90-d3.py", "p4"]) == 0
    out = out_of(evidence, "p4")
    assert "verdict P4-1: True" in out and "verdict P4-3: False" in out and "verdict P4-4: unknown" in out
    assert g.restarts == 0 and sum(1 for c, p in g.requests if c == "POST" and p.endswith("/codex/runner")) == 1


def test_a_client_gone_before_the_stop_is_false(guest):
    m, g, evidence, doc = guest(dies_early=True)
    assert m.main(["i90-d3.py", "flag-on"]) == 0
    assert m.main(["i90-d3.py", "p4"]) == 0
    assert "verdict overall pass: False" in out_of(evidence, "p4")


def test_check_and_flag_on_enforce_the_baseline(guest):
    m, g, evidence, doc = guest()
    g.api["metadata"]["uid"] = "another"
    assert m.main(["i90-d3.py", "check"]) == 3
    g.api["metadata"]["uid"] = m.API[2]
    g.sessions["x" * 64] = True  # a live session
    assert m.main(["i90-d3.py", "check"]) == 3 and m.main(["i90-d3.py", "flag-on"]) == 3 and g.patches == []
    g.sessions.clear()
    g.registry["pod"] = "pod"
    assert m.main(["i90-d3.py", "check"]) == 3


def test_a_baseline_that_cannot_be_saved_sends_nothing(guest, monkeypatch):
    m, g, evidence, doc = guest()
    real = m.durable_json
    def failing(path, value):
        if "restartedAtBefore" in value:
            raise OSError(28, "No space left on device")
        return real(path, value)
    monkeypatch.setattr(m, "durable_json", failing)
    assert m.main(["i90-d3.py", "flag-on"]) == 3 and g.patches == [] and m.flag_state(g.api) == "absent"


def test_a_failed_cleanup_stop_is_rc6_with_the_step_rc_kept(guest):
    m, g, evidence, doc = guest(never_ready=True, delete_503=True)
    assert m.main(["i90-d3.py", "flag-on"]) == 0
    assert m.main(["i90-d3.py", "p4"]) == 6
    done = json.loads((run_dir(evidence, "p4") / "exit.json").read_text())
    assert done["stepRc"] == 5 and done["runnerReleased"] is False and g.runner  # the runner still runs
    assert "verdict overall pass: False" in out_of(evidence, "p4")


def test_private_files_left_are_rc80_and_not_a_pass(guest, monkeypatch):
    m, g, evidence, doc = guest()
    assert m.main(["i90-d3.py", "flag-on"]) == 0
    monkeypatch.setattr(m.shutil, "rmtree", lambda path: (_ for _ in ()).throw(OSError("busy")))
    assert m.main(["i90-d3.py", "p4"]) == 80
    out = out_of(evidence, "p4")
    assert "NOT REMOVED" in out and "verdict overall pass: False" in out
    assert json.loads((run_dir(evidence, "p4") / "exit.json").read_text())["privateRemoved"] is False


def test_an_unreadable_private_file_stops_before_any_change(guest):
    m, g, evidence, doc = guest(probe_fails=True)
    assert m.main(["i90-d3.py", "flag-on"]) == 0
    assert m.main(["i90-d3.py", "p4"]) == 5
    assert g.clients_started == 0 and not [p for _, p in g.requests if p.endswith("/codex/runner")]
    assert not (run_dir(evidence, "p4") / "private").exists() and not any(g.sessions.values())


def test_a_lost_restarted_value_is_rc9_and_flag_off_refuses(guest, monkeypatch, capsys):
    m, g, evidence, doc = guest()
    assert m.main(["i90-d3.py", "flag-on"]) == 0
    real = m.durable_json
    def failing(path, value):
        if "restartedAtAfter" in value:
            raise OSError(28, "No space left on device")
        return real(path, value)
    monkeypatch.setattr(m, "durable_json", failing)
    assert m.main(["i90-d3.py", "p4"]) == 9
    out = capsys.readouterr().out  # out.txt stops at the failed write; the journal (stdout) has the rest
    assert "P4-3: restartedAt now 2026-11-14T07:00:00Z" in out and "verdict P4-4: unknown" in out
    monkeypatch.setattr(m, "durable_json", real)
    before = len(g.patches)
    assert m.main(["i90-d3.py", "flag-off"]) == 3 and len(g.patches) == before


def test_a_failed_start_still_stops_the_runner_and_reports_the_open_flag(guest):
    m, g, evidence, doc = guest(never_ready=True)
    assert m.main(["i90-d3.py", "flag-on"]) == 0
    assert m.main(["i90-d3.py", "p4"]) == 5
    out = out_of(evidence, "p4")
    assert ("DELETE", m.BASE + "/codex/runner") in g.requests and g.runner == []
    assert "verdict P4-1: unknown" in out and "verdict overall pass: False" in out
    assert "THE FLAG IS OPEN after a failure" in out
    assert not any(g.sessions.values()) and not (run_dir(evidence, "p4") / "private").exists()


def test_a_leftover_client_container_is_rc80(guest):
    m, g, evidence, doc = guest(leftover=True)
    assert m.main(["i90-d3.py", "flag-on"]) == 0
    assert m.main(["i90-d3.py", "p4"]) == 80
    assert not any(g.sessions.values())


def test_p4_2_idle_stop(guest):
    m, g, evidence, doc = guest(idle=True)
    assert m.main(["i90-d3.py", "flag-on"]) == 0
    assert m.main(["i90-d3.py", "p4-2"]) == 0
    out = out_of(evidence, "p4-2")
    assert "verdict P4-2: True" in out and "flag-off is the next step" in out
    # only the three ready reads: nothing the script does during the idle wait could extend the idle clock
    assert sum(1 for c, p in g.requests if c == "GET" and p.endswith("/codex/status")) == 3


def test_p4_refuses_without_the_flag(guest):
    m, g, evidence, doc = guest()
    assert m.main(["i90-d3.py", "p4"]) == 3 and g.sessions == {}


def test_pins_are_the_committed_files():
    import hashlib
    m = load()
    assert m.EXPECTED_SHA == hashlib.sha256((VM / "i90-expected.json").read_bytes()).hexdigest()
    assert re.fullmatch(r"[0-9a-f]{64}", m.CLIENT_SHA)


def test_a_failed_revoke_is_rc70_whatever_the_step_rc(guest):
    """Review 65291e6 major: a live session is never hidden behind a measurement failure (step rc kept)."""
    m, g, evidence, doc = guest(never_ready=True)
    assert m.main(["i90-d3.py", "flag-on"]) == 0
    g.faults["revoke_fails"] = True
    assert m.main(["i90-d3.py", "p4"]) == 70
    done = json.loads((run_dir(evidence, "p4") / "exit.json").read_text())
    assert done["stepRc"] == 5 and done["sessionRevoked"] is False and done["runnerReleased"] is True
    assert any(g.sessions.values())  # the session is still there, and the rc says so


def test_an_evidence_failure_after_a_step_failure_is_rc9_with_the_step_rc_kept(guest, monkeypatch):
    m, g, evidence, doc = guest(never_ready=True)
    assert m.main(["i90-d3.py", "flag-on"]) == 0
    real_fsync = m.os.fsync
    def failing(fd):
        if m.OUT["stream"] is not None and fd == m.OUT["stream"].fileno() and ("POST", m.BASE + "/codex/runner") in g.requests:
            raise OSError(28, "No space left on device")
        return real_fsync(fd)
    monkeypatch.setattr(m.os, "fsync", failing)
    assert m.main(["i90-d3.py", "p4"]) == 9
    assert json.loads((run_dir(evidence, "p4") / "exit.json").read_text())["stepRc"] == 5


# --- the exit matrix (PM 2026-11-14: one rule for every exit path; review d3-exit-matrix) -------------------------

class FailingStream:
    """out.txt whose write fails once a trigger appears in a line (and every write after it)."""

    def __init__(self, real, trigger):
        self.real, self.trigger, self.broken = real, trigger, False

    def write(self, text):
        if self.broken or (self.trigger and self.trigger in text):
            self.broken = True
            raise OSError(28, "No space left on device")
        return self.real.write(text)

    def flush(self):
        return self.real.flush()

    def fileno(self):
        return self.real.fileno()

    def close(self):
        return self.real.close()


OUTCOMES = {"normal": 0, "step5": 5, "exception": 5, "signal": 130}
INJECTIONS = {"none": None, "run.json": 9, "out.txt-mid": 9, "out.txt-verdict": 9, "out.txt-result": 9,
              "exit.json": 9, "release": 6, "container": 80, "cookie": 80, "revoke": 70}


def inject(m, g, monkeypatch, outcome, names):
    if outcome == "step5":
        g.faults["never_ready"] = True
    elif outcome == "exception":
        monkeypatch.setattr(m, "p4_0", lambda *a: (_ for _ in ()).throw(RuntimeError("unexpected")))
    elif outcome == "signal":
        g.faults["signal_at_entry"] = True
        monkeypatch.setattr(m, "p4_0", lambda ev, conversation, verdicts: verdicts.__setitem__("P4-0", True))
    real_json, real_fdopen = m.durable_json, m.os.fdopen
    for name in names:
        if name == "run.json":
            def failing(path, value, real=real_json):
                if path.endswith("run.json") and "session" in value:
                    raise OSError(28, "No space left on device")
                return real(path, value)
            real_json = failing
            monkeypatch.setattr(m, "durable_json", failing)
        elif name == "exit.json":
            def failing(path, value, real=real_json):
                if path.endswith("exit.json"):
                    raise OSError(28, "No space left on device")
                return real(path, value)
            real_json = failing
            monkeypatch.setattr(m, "durable_json", failing)
        elif name.startswith("out.txt"):
            trigger = {"out.txt-mid": "conversation ", "out.txt-verdict": "verdict ", "out.txt-result": "result rc="}[name]
            def fdopen(fd, mode="r", *args, trigger=trigger, real=real_fdopen):
                stream = real(fd, mode, *args)
                # only out.txt is wrapped (durable_json's temporary files use os.fdopen too)
                return FailingStream(stream, trigger) if os.readlink("/proc/self/fd/%d" % fd).endswith("out.txt") \
                    else stream
            monkeypatch.setattr(m.os, "fdopen", fdopen)
        elif name == "release":
            g.faults["stuck"] = True
            if outcome in ("normal",):
                pass
        elif name == "container":
            g.faults["leftover"] = True
        elif name == "cookie":
            monkeypatch.setattr(m.shutil, "rmtree", lambda path: (_ for _ in ()).throw(OSError("busy")))
        elif name == "revoke":
            g.faults["revoke_fails"] = True


def expected_rc(outcome, names):
    step = OUTCOMES[outcome]
    order = [6, 80, 70, 9]
    found = [INJECTIONS[name] for name in names if INJECTIONS[name] is not None]
    # a runner that never goes away only matters after an entry (exception and signal stop before one)
    if outcome in ("exception", "signal"):
        found = [rc for rc in found if rc != 6]
    for rc in order:
        if rc in found:
            return rc
    return step


def check_exit(m, g, evidence, out, rc, outcome, names):
    results = [line for line in out.splitlines() if line.startswith("result rc=")]
    assert results and results[-1].split()[1] == "rc=%d" % rc, results
    overall = [line for line in out.splitlines() if line.startswith("verdict overall pass:")]
    assert overall and (overall[-1].startswith("verdict overall pass: True") == (rc == 0)), overall
    folder = run_dir(evidence, "p4")
    exit_file = folder / "exit.json"
    if "exit.json" in names:
        assert not exit_file.exists()
    else:
        done = json.loads(exit_file.read_text())
        assert done["rc"] == rc and done["stepRc"] in (OUTCOMES[outcome], 6, 9, 80)
    assert any(g.sessions.values()) == ("revoke" in names)
    assert (folder / "private").exists() == ("cookie" in names)
    if "release" not in names or outcome in ("exception", "signal"):
        assert g.runner == []  # the runner was released (or never started)


CASES = [(o, (i,)) for o in OUTCOMES for i in INJECTIONS if not (o == "exception" and i == "release")] + [
    ("normal", ("revoke", "exit.json")), ("step5", ("container", "revoke", "out.txt-result")),
    ("signal", ("release", "cookie", "revoke", "exit.json")), ("step5", ("cookie", "run.json")),
    ("exception", ("revoke", "out.txt-verdict"))]


@pytest.mark.parametrize("outcome,names", CASES)
def test_exit_matrix(guest, monkeypatch, capsys, outcome, names):
    m, g, evidence, doc = guest()
    assert m.main(["i90-d3.py", "flag-on"]) == 0
    names = tuple(n for n in names if n != "none")
    inject(m, g, monkeypatch, outcome, names)
    capsys.readouterr()
    rc = m.main(["i90-d3.py", "p4"])
    want = expected_rc(outcome, names)
    assert rc == want, (outcome, names, rc)
    check_exit(m, g, evidence, capsys.readouterr().out, rc, outcome, names)


@pytest.mark.parametrize("names", [("exit.json",), ("revoke", "out.txt-result"), ("container",)])
def test_a_held_rollout_failure_keeps_7_over_cleanup_and_evidence(guest, monkeypatch, capsys, names):
    m, g, evidence, doc = guest()
    real = g.run
    def failing_rollout(cmd, timeout=60, stdin=None):
        if cmd[2:6] == ["-n", "m2-hosting", "rollout", "status"]:
            return 1, ""
        return real(cmd, timeout, stdin)
    monkeypatch.setattr(m, "run", failing_rollout)
    inject(m, g, monkeypatch, "normal", [n for n in names if n in ("exit.json", "out.txt-result")])
    assert m.main(["i90-d3.py", "flag-on"]) == 7
    assert [l for l in capsys.readouterr().out.splitlines() if l.startswith("result rc=")][-1].startswith("result rc=7")


def test_a_baseline_that_cannot_be_saved_stays_rc3_with_nothing_sent(guest, monkeypatch):
    m, g, evidence, doc = guest()
    real = m.durable_json
    monkeypatch.setattr(m, "durable_json", lambda path, value: (_ for _ in ()).throw(OSError(28, "full"))
                        if "restartedAtBefore" in value else real(path, value))
    assert m.main(["i90-d3.py", "flag-on"]) == 3 and g.patches == []


def test_a_signal_during_the_cleanup_stop_still_runs_the_rest_of_the_cleanup(guest, capsys):
    """Review exit matrix: a signal inside the cleanup (here at its DELETE) never skips the remaining cleanup."""
    m, g, evidence, doc = guest(never_ready=True, signal_at_delete=True)
    assert m.main(["i90-d3.py", "flag-on"]) == 0
    capsys.readouterr()
    assert m.main(["i90-d3.py", "p4"]) == 6  # the release is not proved: 6 over the step's 5
    out = capsys.readouterr().out
    assert [l for l in out.splitlines() if l.startswith("result rc=")][-1] == "result rc=6"
    assert not any(g.sessions.values()) and not (run_dir(evidence, "p4") / "private").exists()
    done = json.loads((run_dir(evidence, "p4") / "exit.json").read_text())
    assert done["stepRc"] == 5 and done["runnerReleased"] is False and done["sessionRevoked"] is True
