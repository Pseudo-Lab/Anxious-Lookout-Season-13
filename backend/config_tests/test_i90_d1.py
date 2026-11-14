"""#90 stage D1 (infra/hosting/m3/chat-stream/vm/i90-d1.py) against a fake VM: kubectl, psql and the API answered in
memory, a fake clock. Pure: no VM, no cluster, no network. Needs infra/ mounted (skipped otherwise)."""
import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
VM = ROOT / "infra" / "hosting" / "m3" / "chat-stream" / "vm"
pytestmark = pytest.mark.skipif(not VM.exists(), reason="infra/ not mounted")


def load():
    spec = importlib.util.spec_from_file_location("i90_d1", VM / "i90-d1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def expected_doc():
    return json.loads((VM / "i90-expected.json").read_text())


class FakeVM:
    """The …5805 registry moves idle_stopped gen 29 → (POST) gen 30 with a Pod → (DELETE) idle_stopped gen 30."""

    def __init__(self, module, doc, **faults):
        self.m, self.doc, self.faults = module, doc, faults
        self.clock = 1_800_000_000.0
        self.registry = {"phase": "idle_stopped", "gen": 29, "rev": 116, "pod": None, "active": 0,
                         "ambiguous": False, "state": module.STATE}
        self.pods, self.sessions, self.stops, self.requests = [], {}, [], []
        self.status_reads = 0

    # clock
    def time(self):
        return self.clock

    def sleep(self, seconds):
        self.clock += max(seconds, 0.001)

    # kubectl / psql
    def run(self, cmd, timeout=60, stdin=None):
        args = cmd[2:]
        if args[:2] == ["-n", "m2-hosting"] and "exec" in args:
            return self.sql(stdin.decode())
        if args[-2:] == ["-o", "json"] and args[2:4] == ["get", "pods"]:
            return 0, json.dumps({"items": self.pods})
        if args[-2:] == ["-o", "json"] and args[2:4] == ["get", "deployment"]:
            return 0, json.dumps(self.deployment(args[4]))
        return 2, ""

    def deployment(self, name):
        doc = self.doc
        if name == "api":
            images = dict(doc["deployments"]["api"]["images"], api=doc["newApiImage"])
            uid, main = self.m.DEPLOYMENTS[1][2], "api"
        else:
            images, uid, main = doc["deployments"]["runner-controller"]["images"], self.m.DEPLOYMENTS[0][2], "controller"
        env = [{"name": "CODEX_USER_RUNNER_ALLOWLIST", "value": self.m.OWNER}]
        if self.faults.get("stream_flag") and name == "api":
            env.append({"name": "RESEARCH_STREAM_ENABLE", "value": "true"})
        containers = [{"name": n, "image": i, "env": env if n == main else []} for n, i in images.items()
                      if n != "runner-pool-input"]
        init = [{"name": n, "image": i} for n, i in images.items() if n == "runner-pool-input"]
        return {"metadata": {"uid": uid}, "spec": {"replicas": 1, "template": {"spec": {
            "containers": containers, "initContainers": init}}}, "status": {"readyReplicas": 1}}

    def sql(self, text):
        tag = re.search(r"/\*i90d1:(\w+)\*/", text).group(1)
        if tag == "registry":
            return 0, json.dumps(self.registry)
        if tag == "binding":
            return 0, json.dumps({"image": self.doc["newImage"]})
        if tag == "others":
            rows = []
            for owner, row in self.doc["others"].items():
                plan = self.plans[owner]
                rows.append({"owner": owner, "state": row["state"], "phase": "idle_stopped", "gen": row["generation"],
                             "rev": row["revision"], "pod": None, "plan": plan})
            return 0, "\n".join(json.dumps(r) for r in rows)
        if tag == "pending":
            live = sum(1 for s in self.sessions.values() if s)
            return 0, json.dumps({"sessions": live, "stops": 0, "admissions": 0, "outbox": 0, "running": 0})
        if tag == "session" and "INSERT" in text:
            token_hash = re.search(r"VALUES \('([0-9a-f]{64})'", text).group(1)
            self.sessions[token_hash] = True
            return 0, json.dumps({"id": token_hash[:16]})
        if tag == "session":
            token_hash = re.search(r"token_hash='([0-9a-f]{64})'", text).group(1)
            return 0, json.dumps({"n": 1 if self.sessions.get(token_hash) else 0})
        if tag == "revoke":
            token_hash = re.search(r"token_hash='([0-9a-f]{64})'", text).group(1)
            self.sessions[token_hash] = False
            return 0, json.dumps({"deleted": 1})
        if tag == "stops":
            return 0, "\n".join(json.dumps(s) for s in self.stops)
        return 3, ""

    # API
    def call(self, method, path, body=None, session=True):
        self.requests.append((method, path))
        if path == "/api/health":
            return 200, {"status": "ok"}, "application/json"
        if path == "/api/version":
            return 200, {"sha": self.m.SOURCE, "builtAt": "2026-11-14T05:27:14Z"}, "application/json"
        if path.endswith("/events") or path.endswith("/interrupt"):
            if self.faults.get("events_open") and path.endswith("/events"):
                return 200, None, "text/event-stream"
            return 404, {"error": {"code": "stream_disabled", "message": "Streaming is not enabled"}}, "application/json"
        if path == self.m.BASE + "/codex/status":
            if self.registry["pod"]:
                self.status_reads += 1
                ready = self.status_reads >= 3 and not self.faults.get("never_ready")
                return 200, {"available": ready, "reason": None if ready else "runner_unreachable",
                             "verification": "verified" if ready else "unverified", "model": "m"}, "application/json"
            return 200, {"available": False, "reason": "idle_stopped", "verification": "unverified",
                         "model": "m"}, "application/json"
        if path.startswith(self.m.BASE + "/sessions?"):
            items = [{"id": "c%d" % i, "title": "t", "state": "idle", "version": 1, "createdAt": "x",
                      "updatedAt": "y", "context": None} for i in range(2)]
            return 200, {"items": items, "nextCursor": None}, "application/json"
        if path.startswith(self.m.BASE + "/sessions/"):
            ident = path.rsplit("/", 1)[1]
            return 200, {"id": ident, "title": "t", "state": "idle", "version": 1, "createdAt": "x", "updatedAt": "y",
                         "context": None, "items": [{"id": "i1"}], "error": None}, "application/json"
        if path == self.m.BASE + "/codex/runner" and method == "POST":
            if self.faults.get("interrupt_at_entry"):
                raise self.m.Interrupted()
            self.registry.update(gen=30, phase="ready", pod="pod-uid-30")
            if self.faults.get("malformed_pod"):  # an answer the start check cannot parse (KeyError, not a Stop)
                self.pods = [{"status": {}}]
                return 202, {"available": False, "reason": "provisioning"}, "application/json"
            started = self.m.time.strftime("%Y-%m-%dT%H:%M:%SZ", self.m.time.gmtime(self.clock))
            self.pods = [{"metadata": {"name": self.m.POD_NAME, "uid": "pod-uid-30"},
                          "spec": {"containers": [{"name": "runner", "image": self.doc["newImage"]}]},
                          "status": {"phase": "Running", "containerStatuses": [
                              {"ready": True, "containerID": "containerd://c1", "restartCount": 0,
                               "state": {"running": {"startedAt": started}}}]}}]
            return 202, {"available": False, "reason": "provisioning", "verification": "unverified",
                         "model": "m"}, "application/json"
        if path == self.m.BASE + "/codex/runner" and method == "DELETE":
            self.registry.update(phase="idle_stopped", pod=None)
            self.pods = []
            self.stops = [{"gen": 30, "receipt": 1, "clean": True, "completed": True, "requested": self.clock,
                           "completedAt": self.clock + 3}]
            return 200, {"terminationRequested": True}, "application/json"
        return 500, None, ""


@pytest.fixture
def vm(tmp_path, monkeypatch):
    def make(**faults):
        module = load()
        doc = expected_doc()
        fake = FakeVM(module, doc, **faults)
        # plans whose digests are the recorded ones: the fake hands the digest function the owner's recorded digest
        fake.plans = {owner: {"owner": owner} for owner in doc["others"]}
        real_digest = module.digest
        by_plan = {json.dumps(plan, sort_keys=True): doc["others"][owner]["planSha256"]
                   for owner, plan in fake.plans.items()}
        monkeypatch.setattr(module, "digest", lambda value: by_plan.get(json.dumps(value, sort_keys=True),
                                                                        real_digest(value)))
        for owner, row in doc["others"].items():
            fake.plans[owner]["image"] = row["image"]
            by_plan[json.dumps(fake.plans[owner], sort_keys=True)] = row["planSha256"]
        evidence = tmp_path / "evidence"
        evidence.mkdir(mode=0o700)
        monkeypatch.setattr(module, "EVIDENCE", str(evidence))
        monkeypatch.setattr(module, "LOCK", str(evidence / "i90-d.lock"))
        monkeypatch.setattr(module, "expected", lambda: doc)
        monkeypatch.setattr(module, "run", fake.run)
        monkeypatch.setattr(module, "call", fake.call)
        monkeypatch.setattr(module.time, "time", fake.time)
        monkeypatch.setattr(module.time, "sleep", fake.sleep)
        monkeypatch.setattr(module.os, "geteuid", lambda: 0)
        monkeypatch.setattr(module.signal, "signal", lambda *a: None)
        return module, fake, evidence
    return make


def run_dir(evidence):
    dirs = [d for d in evidence.iterdir() if d.is_dir()]
    assert len(dirs) == 1
    return dirs[0]


def test_check_is_read_only(vm):
    module, fake, evidence = vm()
    assert module.main(["i90-d1.py", "check"]) == 0
    assert list(evidence.iterdir()) == [] and fake.sessions == {}
    assert all(method == "GET" or path.endswith("/interrupt") for method, path in fake.requests)


def test_run_passes_and_stops_the_runner_and_revokes(vm):
    module, fake, evidence = vm()
    assert module.main(["i90-d1.py", "run"]) == 0
    folder = run_dir(evidence)
    done = json.loads((folder / "exit.json").read_text())
    assert done == {**done, "rc": 0, "stepRc": 0, "sessionRevoked": True, "finalInvariants": True}
    out = (folder / "out.txt").read_text()
    assert "status read +65 s: 200" in out and "verdict runner available at the last read (explicit signal): True" in out
    assert ("POST", module.BASE + "/codex/runner") in fake.requests and ("DELETE", module.BASE + "/codex/runner") in fake.requests
    assert not any(fake.sessions.values())
    token = module.SESSION["token"]
    assert token not in out and token not in (folder / "run.json").read_text()
    assert module.main(["i90-d1.py", "run"]) == 3  # one run only


def test_a_regression_failure_never_starts_the_runner(vm):
    module, fake, evidence = vm(events_open=True)
    assert module.main(["i90-d1.py", "run"]) == 3  # the session-less events check in precheck already refuses
    assert not [p for m, p in fake.requests if p.endswith("/codex/runner")] and fake.sessions == {}


def test_a_stream_flag_on_a_deployment_refuses(vm):
    module, fake, evidence = vm(stream_flag=True)
    assert module.main(["i90-d1.py", "run"]) == 3 and fake.sessions == {}


def test_runner_not_ready_still_stops_and_reports_rc5(vm):
    module, fake, evidence = vm(never_ready=True)
    assert module.main(["i90-d1.py", "run"]) == 5
    assert ("DELETE", module.BASE + "/codex/runner") in fake.requests
    done = json.loads((run_dir(evidence) / "exit.json").read_text())
    assert done["rc"] == 5 and done["sessionRevoked"] is True and done["finalInvariants"] is True


def test_expected_refuses_placeholder_and_other_bytes(tmp_path, monkeypatch):
    module = load()
    monkeypatch.setattr(module, "EXPECTED_SHA", "PIN")
    with pytest.raises(module.Stop) as stopped:
        module.expected()
    assert stopped.value.code == 3
    other = tmp_path / "e.json"
    other.write_text("{}")
    monkeypatch.setattr(module, "EXPECTED_SHA", "0" * 64)
    monkeypatch.setattr(module, "EXPECTED_FILE", str(other))
    with pytest.raises(module.Stop) as stopped:
        module.expected()
    assert stopped.value.code == 3


def test_expected_pin_is_the_committed_expected_file():
    import hashlib
    module = load()
    assert module.EXPECTED_SHA == hashlib.sha256((VM / "i90-expected.json").read_bytes()).hexdigest()


# Review 518c088 majors: cleanup boundaries -------------------------------------------------------------------------

def test_an_unexpected_start_exception_still_sends_the_stop(vm):
    module, fake, evidence = vm(malformed_pod=True)
    assert module.main(["i90-d1.py", "run"]) == 5
    assert ("DELETE", module.BASE + "/codex/runner") in fake.requests and fake.pods == []
    done = json.loads((run_dir(evidence) / "exit.json").read_text())
    assert done["rc"] == 5 and done["sessionRevoked"] is True
    assert "unexpected KeyError in the start check; the stop is still sent" in (run_dir(evidence) / "out.txt").read_text()


def test_an_evidence_write_failure_never_skips_the_revoke(vm, monkeypatch):
    module, fake, evidence = vm()
    real_fsync = module.os.fsync
    def failing_fsync(fd):
        if any(fake.sessions.values()):  # from the session insert on, every evidence fsync fails
            raise OSError("disk")
        return real_fsync(fd)
    monkeypatch.setattr(module.os, "fsync", failing_fsync)
    code = module.main(["i90-d1.py", "run"])  # returns: never raises out of main
    assert code != 0 and fake.sessions and not any(fake.sessions.values())
    assert not [p for m, p in fake.requests if p.endswith("/codex/runner")]  # failed before the entry


def test_a_second_interrupt_at_the_entry_revokes_but_sends_no_stop(vm):
    module, fake, evidence = vm(interrupt_at_entry=True)
    assert module.main(["i90-d1.py", "run"]) == 130
    assert ("DELETE", module.BASE + "/codex/runner") not in fake.requests
    assert fake.sessions and not any(fake.sessions.values())
    assert json.loads((run_dir(evidence) / "exit.json").read_text())["sessionRevoked"] is True


def test_evidence_failure_after_a_full_pass_is_rc9(vm, monkeypatch):
    module, fake, evidence = vm()
    real_fsync = module.os.fsync
    state = {"deleted": False}
    def failing_fsync(fd):
        if ("DELETE", module.BASE + "/codex/runner") in fake.requests and not state["deleted"]:
            state["deleted"] = True
            raise OSError("disk")
        return real_fsync(fd)
    monkeypatch.setattr(module.os, "fsync", failing_fsync)
    assert module.main(["i90-d1.py", "run"]) == 9


# Review 7d80875 major: a write failing after the result line never stays a success -------------------------------

def test_an_exit_json_failure_after_a_full_pass_is_rc9(vm, monkeypatch, capsys):
    module, fake, evidence = vm()
    real = module.durable_json
    def failing(path, value):
        if path.endswith("exit.json"):
            raise OSError(28, "No space left on device")
        return real(path, value)
    monkeypatch.setattr(module, "durable_json", failing)
    assert module.main(["i90-d1.py", "run"]) == 9
    lines = [l for l in capsys.readouterr().out.splitlines() if l.startswith("result rc=")]
    assert lines[0] == "result rc=0" and lines[-1].startswith("result rc=9")
    assert not (run_dir(evidence) / "exit.json").exists()


def test_an_out_txt_failure_after_the_revoke_is_rc9(vm, monkeypatch):
    module, fake, evidence = vm()
    real_fsync = module.os.fsync
    def failing_fsync(fd):
        if module.OUT["stream"] is not None and fd == module.OUT["stream"].fileno() and fake.sessions \
                and not any(fake.sessions.values()) and ("DELETE", module.BASE + "/codex/runner") in fake.requests:
            raise OSError(28, "No space left on device")  # out.txt writes after the revoke fail
        return real_fsync(fd)
    monkeypatch.setattr(module.os, "fsync", failing_fsync)
    assert module.main(["i90-d1.py", "run"]) == 9
    assert json.loads((run_dir(evidence) / "exit.json").read_text())["rc"] == 9
