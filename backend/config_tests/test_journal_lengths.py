"""#90 infra/hosting/m3/chat-stream/journal_lengths.py: lengths only, read only, refusals."""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from runner.service_store import SCHEMA

SCRIPT = Path(__file__).resolve().parents[2] / "infra" / "hosting" / "m3" / "chat-stream" / "journal_lengths.py"
SECRET = "sk-live-" + "Z" * 40


def ledger(directory, literals):
    connection = sqlite3.connect(directory / "service-access.sqlite3")
    for statement in SCHEMA:
        connection.execute(statement)
    for value in literals:
        connection.execute("INSERT INTO literals VALUES(?,?,?,?)",
                           (len(value), "123", hashlib.sha256(value.encode()).hexdigest(), "m" * 64))
    connection.commit()
    connection.close()


def run(path):
    result = subprocess.run([sys.executable, "-I", str(SCRIPT), str(path)], capture_output=True, text=True, timeout=30)
    return result.returncode, result.stdout


@pytest.fixture
def control(tmp_path):
    directory = tmp_path / "control"
    directory.mkdir()
    return directory


@pytest.mark.skipif(not SCRIPT.exists(), reason="infra/ not mounted (the unit command mounts backend/ only)")
def test_lengths_only_and_nothing_written(control):
    ledger(control, [SECRET, "a" * 43, "b" * 43, "user@example.invalid"])
    before = {path.name: (path.stat().st_mtime_ns, path.read_bytes()) for path in control.iterdir()}
    code, out = run(control)
    assert code == 0
    value = json.loads(out)
    assert value == {"ok": True, "literals": 4, "L": 48, "holdbackChars": 47,
                     "lengths": {"20": 1, "43": 2, "48": 1}}
    assert SECRET not in out and hashlib.sha256(SECRET.encode()).hexdigest() not in out and "123" not in out
    after = {path.name: (path.stat().st_mtime_ns, path.read_bytes()) for path in control.iterdir()}
    assert after == before  # no sidecar, no change


@pytest.mark.skipif(not SCRIPT.exists(), reason="infra/ not mounted (the unit command mounts backend/ only)")
@pytest.mark.parametrize("case,reason", [("missing", "ledger_missing"), ("symlink", "ledger_not_a_regular_file"),
                                         ("fifo", "ledger_not_a_regular_file"), ("journal", "ledger_has_journal"),
                                         ("nodir", "directory_missing")])
def test_refusals(control, tmp_path, case, reason):
    target = control
    if case == "symlink":
        real = tmp_path / "real"
        real.mkdir()
        ledger(real, [SECRET])
        os.symlink(real / "service-access.sqlite3", control / "service-access.sqlite3")
    elif case == "fifo":
        os.mkfifo(control / "service-access.sqlite3")  # never opened: lstat refuses first (no blocking read)
    elif case == "journal":
        ledger(control, [SECRET])
        (control / "service-access.sqlite3-journal").write_bytes(b"x")
    elif case == "nodir":
        target = tmp_path / "absent"
    code, out = run(target)
    assert code == 2 and json.loads(out) == {"ok": False, "reason": reason}
