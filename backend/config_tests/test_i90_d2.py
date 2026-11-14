"""#90 stage D2 (infra/hosting/m3/chat-stream/vm/i90-d2.py, P3 L measurement) against a fake guest: kubectl, psql
and docker answered in memory. Pure: no VM, no Docker. Needs infra/ mounted (skipped otherwise)."""
import hashlib
import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CS = ROOT / "infra" / "hosting" / "m3" / "chat-stream"
VM = CS / "vm"
pytestmark = pytest.mark.skipif(not VM.exists(), reason="infra/ not mounted")
TOOL = "sha256:" + "f" * 64
CONTROL = {"name": "control-x", "uid": "pvc-uid-1", "volume": "pv-control-x"}


def load():
    spec = importlib.util.spec_from_file_location("i90_d2", VM / "i90-d2.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeGuest:
    def __init__(self, module, path, **faults):
        self.m, self.path, self.faults, self.docker_runs = module, path, faults, []

    def run(self, cmd, timeout=60, stdin=None):
        if cmd[0] == self.m.DOCKER:
            return self.docker(cmd[1:], stdin)
        args = cmd[2:]
        if "exec" in args:
            tag = re.search(r"/\*i90d2:(\w+)\*/", stdin.decode()).group(1)
            if tag == "registry":
                phase = "ready" if self.faults.get("running") else "idle_stopped"
                return 0, json.dumps({"phase": phase, "gen": 30, "pod": None, "active": 0})
            return 0, json.dumps({"control": CONTROL})
        if args[2:4] == ["get", "pods"]:
            return 0, json.dumps({"items": []})
        if args[2:4] == ["get", "pvc"]:
            uid = "other" if self.faults.get("pvc_uid") else CONTROL["uid"]
            return 0, json.dumps({"metadata": {"uid": uid}, "spec": {"volumeName": CONTROL["volume"]},
                                  "status": {"phase": "Bound"}})
        if args[:2] == ["get", "pv"]:
            return 0, json.dumps({"spec": {"local": {"path": str(self.path)}, "claimRef": {
                "namespace": "codex-runner", "name": CONTROL["name"], "uid": CONTROL["uid"]}}})
        return 2, ""

    def docker(self, args, stdin):
        if args[:2] == ["image", "inspect"]:
            return 0, TOOL
        if args[0] == "run":
            self.docker_runs.append((args, stdin))
            if self.faults.get("refused"):
                return 2, json.dumps({"ok": False, "reason": "ledger_missing"})
            return 0, json.dumps({"ok": True, "literals": 3, "L": 41, "holdbackChars": 40, "lengths": {"8": 2, "41": 1}})
        if args[:2] == ["ps", "-a"]:
            return 0, "running" if self.faults.get("stuck") else ""
        return 0, ""


@pytest.fixture
def guest(tmp_path, monkeypatch):
    def make(**faults):
        module = load()
        control = tmp_path / "control"
        control.mkdir(exist_ok=True)
        fake = FakeGuest(module, control, **faults)
        script = (CS / "journal_lengths.py").read_bytes()
        expected = json.dumps({"toolImage": TOOL}).encode()
        files = {module.EXPECTED_FILE: (module.EXPECTED_SHA, expected), module.SCRIPT: (module.JOURNAL_LENGTHS_SHA, script)}
        monkeypatch.setattr(module, "read_root_file", lambda path, limit=1 << 20: files[path])
        monkeypatch.setattr(module, "run", fake.run)
        monkeypatch.setattr(module.os, "geteuid", lambda: 0)
        monkeypatch.setattr(module.time, "sleep", lambda s: None)
        return module, fake, script
    return make


def test_measures_in_a_locked_down_container_with_the_hashed_bytes(guest, capsys):
    module, fake, script = guest()
    assert module.main(["i90-d2.py", "measure"]) == 0
    (args, stdin), = fake.docker_runs
    for flag in ("--pull=never", "--read-only", "no-new-privileges", "label=disable"):
        assert flag in args
    assert args[args.index("--network") + 1] == "none" and args[args.index("--cap-drop") + 1] == "ALL"
    assert args[args.index("--user") + 1] == "10001:10001"
    assert "%s:/control:ro" % fake.path in args and stdin == script
    assert args[-4:] == ["-I", "-B", "-", "/control"]
    out = capsys.readouterr().out
    assert '"L": 41' in out and "result rc=0 (read-only)" in out


@pytest.mark.parametrize("fault", ["running", "pvc_uid"])
def test_refuses_before_any_container(guest, fault):
    module, fake, _ = guest(**{fault: True})
    assert module.main(["i90-d2.py", "measure"]) == 3 and fake.docker_runs == []


def test_refused_input_is_rc2_and_a_stuck_container_rc80(guest):
    module, fake, _ = guest(refused=True)
    assert module.main(["i90-d2.py", "measure"]) == 2
    module, fake, _ = guest(stuck=True)
    assert module.main(["i90-d2.py", "measure"]) == 80


def test_pins_are_the_committed_files():
    module = load()
    assert module.EXPECTED_SHA == hashlib.sha256((VM / "i90-expected.json").read_bytes()).hexdigest()
    assert module.JOURNAL_LENGTHS_SHA == hashlib.sha256((CS / "journal_lengths.py").read_bytes()).hexdigest()


def test_a_placeholder_pin_refuses(monkeypatch):
    module = load()
    monkeypatch.setattr(module, "JOURNAL_LENGTHS_SHA", "PIN")
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    assert module.main(["i90-d2.py", "measure"]) == 3


def test_read_root_file_refuses_a_fifo_without_blocking(tmp_path):
    import os
    module = load()
    fifo = tmp_path / "f"
    os.mkfifo(fifo)
    with pytest.raises(module.Stop):
        module.read_root_file(str(fifo))
