"""#90 P5 r6 part a (infra/hosting/m3/chat-stream/vm/i90-p5-r6-pins.py): read only, fixed keys. Pure fakes."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "infra" / "hosting" / "m3" / "chat-stream" / "vm" / "i90-p5-r6-pins.py"
pytestmark = pytest.mark.skipif(not SCRIPT.exists(), reason="infra/ not mounted")


@pytest.fixture
def p(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("i90_p5_r6_pins", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "EVIDENCE", str(tmp_path))
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    return module


def services(*items):
    class Done:
        returncode, stdout = 0, json.dumps({"items": list(items)}).encode()
    return lambda *a, **k: Done()


def svc(name, selector, ports, ip="10.43.0.10"):
    return {"metadata": {"name": name}, "spec": {"selector": selector, "clusterIP": ip, "ports": ports}}


def test_two_api_services_are_listed_with_fixed_names_and_ports(p, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(p.subprocess, "run", services(
        svc("api", {"app": "hosting-api"}, [{"name": "http", "port": 8080}]),
        svc("api-internal", {"app": "hosting-api"}, [{"name": "internal", "port": 8081}]),
        svc("SECRET-other", {"app": "hosting-api"}, [{"name": "SECRET-port", "port": 1}], ip="None"),
        svc("web", {"app": "hosting-web"}, [{"name": "http", "port": 8080}])))
    for name, rc in (("i90-p5-turns-r5-20261114T170503Z", 5), ("i90-p5-disable-r5-20261114T170745Z", 0)):
        (tmp_path / name).mkdir()
        (tmp_path / name / "exit.json").write_bytes(json.dumps({"rc": rc}).encode())
    (tmp_path / "i90-p5-budget.json").write_text(json.dumps({"turns": [
        {"n": i + 1, "step": s, "outcome": "accepted", "reservedAt": "x"} for i, s in enumerate(("S0", "S1", "S2", "S3"))]}))
    assert p.main() == 0
    raw = capsys.readouterr().out
    assert "SECRET" not in raw
    out = {x["kind"]: x for x in (json.loads(y) for y in raw.splitlines())}
    assert out["services"]["count"] == 3
    assert out["services"]["selectorHostingApi"] == [
        {"name": "api", "clusterIP": True, "ports": [{"name": "http", "port": 8080}]},
        {"name": "api-internal", "clusterIP": True, "ports": [{"name": "internal", "port": 8081}]},
        {"name": "other", "clusterIP": "None-headless", "ports": [{"name": "other", "port": 1}]}]
    assert {k: out["pins"]["turns-r5"][k] for k in ("run", "rc", "exitSha256")} == {"run": "i90-p5-turns-r5-20261114T170503Z", "rc": 5,
                                       "exitSha256": hashlib.sha256(json.dumps({"rc": 5}).encode()).hexdigest()}
    assert out["pins"]["disable-r5"]["rc"] == 0
    assert [b["step"] for b in out["pins"]["budget"]] == ["S0", "S1", "S2", "S3"]


def test_refuses_when_not_root_or_the_lookup_fails(p, monkeypatch):
    class Failed:
        returncode, stdout = 1, b""
    monkeypatch.setattr(p.subprocess, "run", lambda *a, **k: Failed())
    assert p.main() == 3
    monkeypatch.setattr(p.os, "geteuid", lambda: 1000)
    assert p.main() == 3


def test_typed_exit_fields_distinguish_null_missing_and_unknown_keys(p, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(p.subprocess, "run", services(svc("api", {"app": "hosting-api"}, [{"name": "http", "port": 8080}])))
    run = tmp_path / "i90-p5-turns-r5-20261114T170503Z"
    run.mkdir()
    (run / "exit.json").write_text(json.dumps({"rc": 5, "stepRc": 5, "verdicts": {"S2": True, "S3": True, "SECRET": 1},
                                               "stoppedAt": None, "runnerReleased": True, "clientsGone": True,
                                               "privateRemoved": True, "finished": "x"}))
    p.main()
    raw = capsys.readouterr().out
    assert "SECRET" not in raw
    fields = [json.loads(y) for y in raw.splitlines() if '"pins"' in y][0]["turns-r5"]["fields"]
    assert fields == {"rc": 5, "stepRc": 5, "runnerReleased": True, "clientsGone": True, "privateRemoved": True,
                      "sessionRevoked": "missing", "stoppedAt": "null",
                      "verdicts": {"S2": True, "S3": True, "other": 1}}
