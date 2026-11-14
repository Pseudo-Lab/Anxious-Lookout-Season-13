"""#90 P5 r7 pins (infra/hosting/m3/chat-stream/vm/i90-p5-r7-pins.py): read only, fixed keys. Pure fakes."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "infra" / "hosting" / "m3" / "chat-stream" / "vm" / "i90-p5-r7-pins.py"
pytestmark = pytest.mark.skipif(not SCRIPT.exists(), reason="infra/ not mounted")
TURNS, DISABLE = "i90-p5-turns-r6-20261114T172503Z", "i90-p5-disable-r6-20261114T172648Z"


@pytest.fixture
def p(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("i90_p5_r7_pins", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "EVIDENCE", str(tmp_path))
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    return module


def exit_file(tmp_path, run, doc):
    (tmp_path / run).mkdir()
    raw = json.dumps(doc, sort_keys=True, indent=1).encode()
    (tmp_path / run / "exit.json").write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def budget(tmp_path, steps=("S0", "S1", "S2", "S3")):
    (tmp_path / "i90-p5-budget.json").write_text(json.dumps({"cap": 8, "turns": [
        {"n": i + 1, "step": s, "outcome": "accepted", "reservedAt": "SECRET"} for i, s in enumerate(steps)]}))


def pins_of(capsys):
    raw = capsys.readouterr().out
    assert "SECRET" not in raw
    return json.loads(raw)["kind"] == "pins" and json.loads(raw)


def test_r6_runs_are_pinned_with_typed_fields(p, tmp_path, capsys):
    turns = {"rc": 5, "stepRc": 5, "verdicts": {"S4a": None, "S4b": None, "S5": None, "S6": None, "release": None},
             "stoppedAt": None, "runnerReleased": True, "clientsGone": True, "privateRemoved": True,
             "sessionRevoked": True, "finished": "SECRET"}
    disable = {"rc": 0, "stepRc": 0, "verdicts": {}, "stoppedAt": None, "clientsGone": True, "runnerReleased": None,
               "privateRemoved": None, "sessionRevoked": None}
    h1, h2 = exit_file(tmp_path, TURNS, turns), exit_file(tmp_path, DISABLE, disable)
    exit_file(tmp_path, "i90-p5-turns-r5-20261114T170503Z", {"rc": 5})   # an earlier round is not counted
    budget(tmp_path)
    assert p.main() == 0
    out = pins_of(capsys)
    assert out["turns-r6"] == {"run": TURNS, "exitSha256": h1, "rc": 5, "fields": {
        "rc": 5, "stepRc": 5, "runnerReleased": True, "clientsGone": True, "privateRemoved": True,
        "sessionRevoked": True, "stoppedAt": "null",
        "verdicts": {"S4a": "null", "S4b": "null", "S5": "null", "S6": "null", "release": "null"}}}
    assert out["disable-r6"]["exitSha256"] == h2
    assert out["disable-r6"]["fields"] == {"rc": 0, "stepRc": 0, "runnerReleased": "null", "clientsGone": True,
                                           "privateRemoved": "null", "sessionRevoked": "null", "stoppedAt": "null",
                                           "verdicts": {}}
    assert out["budget"] == [{"n": i + 1, "step": s, "outcome": "accepted"} for i, s in enumerate(("S0", "S1", "S2", "S3"))]


def test_missing_keys_unknown_values_several_runs_and_unreadable_files(p, tmp_path, capsys):
    exit_file(tmp_path, TURNS, {"rc": "SECRET", "stoppedAt": "SECRET", "verdicts": {"SECRET": 1.5}})
    (tmp_path / "i90-p5-disable-r6-20261114T172648Z").mkdir()
    (tmp_path / "i90-p5-disable-r6-20261115T000000Z").mkdir()
    budget(tmp_path, ("S0", "SECRET"))
    assert p.main() == 0
    out = pins_of(capsys)
    assert out["turns-r6"]["rc"] is None
    assert out["turns-r6"]["fields"]["rc"] == "other" and out["turns-r6"]["fields"]["stepRc"] == "missing"
    assert out["turns-r6"]["fields"]["stoppedAt"] == "other"
    assert out["turns-r6"]["fields"]["verdicts"] == {"other": "other"}
    assert out["disable-r6"] == {"runs": 2}
    assert [b["step"] for b in out["budget"]] == ["S0", "unknown"]


def test_symlink_or_fifo_exit_reads_as_none_without_blocking(p, tmp_path, capsys):
    (tmp_path / TURNS).mkdir()
    target = tmp_path / "elsewhere.json"
    target.write_text("{}")
    os.symlink(target, tmp_path / TURNS / "exit.json")
    (tmp_path / DISABLE).mkdir()
    os.mkfifo(tmp_path / DISABLE / "exit.json")
    assert p.main() == 0
    out = pins_of(capsys)
    assert out["turns-r6"]["exitSha256"] is None and out["disable-r6"]["exitSha256"] is None
    assert out["budget"] is None


def test_refuses_when_not_root(p, monkeypatch):
    monkeypatch.setattr(p.os, "geteuid", lambda: 1000)
    assert p.main() == 3
