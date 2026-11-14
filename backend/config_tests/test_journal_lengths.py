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
    assert {k: value[k] for k in ("ok", "literals", "L", "holdbackChars", "lengths")} == {
        "ok": True, "literals": 4, "L": 48, "holdbackChars": 47, "lengths": {"20": 1, "43": 2, "48": 1}}  # v1 fields
    assert value["snapshots"] == 0 and value["prefixFile"] == "absent"
    assert value["structural"] == {"macVerified": False, "covered": {}, "legacy": {"20": 1, "43": 2, "48": 1},
                                   "LLegacy": 48}
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


# --- v2 (design r4): structural covered / legacy and the snapshots count -------------------------------------------

def prefix_file(directory, literals, raw=None):
    path = directory / "stream-prefixes.json"
    if raw is not None:
        path.write_text(raw)
        return path
    entries = [[1, "c" * 64, len(value), hashlib.sha256(value.encode()).hexdigest()] for value in literals]
    path.write_text(json.dumps({"version": 1, "owner": "o", "trialId": "t", "prefixLength": 16, "entries": entries,
                                "mac": "0" * 64}))
    return path


@pytest.mark.skipif(not SCRIPT.exists(), reason="infra/ not mounted (the unit command mounts backend/ only)")
def test_structural_coverage_and_snapshots(control):
    token = "eyJ" + "t" * 1673  # 1676 chars, like the dev VM's L
    ledger(control, [token, "a" * 43, "b" * 43, "short-account-id"])
    connection = sqlite3.connect(control / "service-access.sqlite3")
    connection.execute("INSERT INTO snapshots VALUES(?,?)", ("s1", "f" * 64))
    connection.commit()
    connection.close()
    prefix_file(control, [token, "a" * 43])
    before = {path.name: (path.stat().st_mtime_ns, path.read_bytes()) for path in control.iterdir()}
    code, out = run(control)
    value = json.loads(out)
    assert code == 0 and value["snapshots"] == 1 and value["prefixFile"] == "present"
    assert value["structural"] == {"macVerified": False, "covered": {"1676": 1, "43": 1}, "legacy": {"43": 1},
                                   "LLegacy": 43}
    assert value["L"] == 1676  # the v1 L is unchanged: every literal
    assert token not in out and hashlib.sha256(token.encode()).hexdigest() not in out and "c" * 64 not in out
    after = {path.name: (path.stat().st_mtime_ns, path.read_bytes()) for path in control.iterdir()}
    assert after == before


@pytest.mark.skipif(not SCRIPT.exists(), reason="infra/ not mounted (the unit command mounts backend/ only)")
@pytest.mark.parametrize("case,state", [("malformed", "malformed"), ("not-json", "malformed"), ("symlink", "unreadable"),
                                        ("fifo", "unreadable"), ("directory", "unreadable")])
def test_a_bad_prefix_file_counts_everything_legacy(control, tmp_path, case, state):
    token = "eyJ" + "u" * 100
    ledger(control, [token])
    path = control / "stream-prefixes.json"
    if case == "malformed":
        prefix_file(control, [], raw=json.dumps({"entries": [[1, "p", "103", "x"]]}))
    elif case == "not-json":
        prefix_file(control, [], raw="{")
    elif case == "symlink":
        real = prefix_file(tmp_path, [token])
        os.symlink(real, path)
    elif case == "fifo":
        os.mkfifo(path)  # lstat refuses first: never opened, no blocking read
    elif case == "directory":
        path.mkdir()
    code, out = run(control)
    value = json.loads(out)
    assert code == 0 and value["prefixFile"] == state
    assert value["structural"]["covered"] == {} and value["structural"]["LLegacy"] == len(token)


# --- v2 r1 (review acb6c57 major): only the runtime's public shape credits coverage -------------------------------

def shaped(token, change):
    entry = [1, "c" * 64, len(token), hashlib.sha256(token.encode()).hexdigest()]
    payload = {"version": 1, "owner": "o", "trialId": "t", "prefixLength": 16, "entries": [entry], "mac": "0" * 64}
    change(payload, entry)
    return payload


SHAPES = {
    "missing_metadata": lambda p, e: [p.pop(k) for k in ("version", "owner", "trialId", "prefixLength", "mac")],
    "extra_top_field": lambda p, e: p.update(extra=1),
    "version_2": lambda p, e: p.update(version=2),
    "version_true": lambda p, e: p.update(version=True),
    "owner_empty": lambda p, e: p.update(owner=""),
    "trial_not_str": lambda p, e: p.update(trialId=7),
    "prefix_length_8": lambda p, e: p.update(prefixLength=8),
    "prefix_length_str": lambda p, e: p.update(prefixLength="16"),
    "entries_not_list": lambda p, e: p.update(entries={"0": e}),
    "entries_over_limit": lambda p, e: p.update(entries=[e] * 4097),
    "mac_not_hex": lambda p, e: p.update(mac="not-a-mac"),
    "entry_five": lambda p, e: e.append("unexpected"),
    "entry_three": lambda p, e: e.pop(0),
    "entry_tuple_like": lambda p, e: p.update(entries=[dict(enumerate(e))]),
    "filter_str": lambda p, e: e.__setitem__(0, "not-a-filter"),
    "filter_bool": lambda p, e: e.__setitem__(0, True),
    "filter_negative": lambda p, e: e.__setitem__(0, -1),
    "filter_over_mask": lambda p, e: e.__setitem__(0, 1 << 64),
    "prefix_not_hex": lambda p, e: e.__setitem__(1, "not-a-digest"),
    "prefix_upper_hex": lambda p, e: e.__setitem__(1, "C" * 64),
    "literal_not_hex": lambda p, e: e.__setitem__(3, e[3][:63] + "z"),
    "length_bool": lambda p, e: e.__setitem__(2, True),
    "length_over": lambda p, e: e.__setitem__(2, 65537),
    # one bad entry spoils the file, even next to a good one (the runtime discards the whole file)
    "one_bad_of_two": lambda p, e: p["entries"].append([1, "c" * 64, 16, "d" * 64]),
}


@pytest.mark.skipif(not SCRIPT.exists(), reason="infra/ not mounted (the unit command mounts backend/ only)")
@pytest.mark.parametrize("bad", sorted(SHAPES))
def test_a_structurally_invalid_file_credits_nothing(control, bad):
    token = "eyJ" + "v" * 100
    ledger(control, [token])
    prefix_file(control, [], raw=json.dumps(shaped(token, SHAPES[bad])))
    code, out = run(control)
    value = json.loads(out)
    assert code == 0 and value["prefixFile"] == "malformed"
    assert value["structural"] == {"macVerified": False, "covered": {}, "legacy": {"103": 1}, "LLegacy": 103}


@pytest.mark.skipif(not SCRIPT.exists(), reason="infra/ not mounted (the unit command mounts backend/ only)")
def test_the_public_shape_bounds_are_inclusive(control):
    tokens = ["eyJ" + "w" * 14, "eyJ" + "x" * 100]  # 17 chars (P + 1, the shortest long literal) and 103
    ledger(control, tokens)

    def change(payload, entry):
        entry[0] = (1 << 64) - 1  # MASK
        payload["entries"] = [entry, [0, "e" * 64, 17, hashlib.sha256(tokens[0].encode()).hexdigest()]]

    prefix_file(control, [], raw=json.dumps(shaped(tokens[1], change)))
    code, out = run(control)
    value = json.loads(out)
    assert code == 0 and value["prefixFile"] == "present"
    assert value["structural"] == {"macVerified": False, "covered": {"17": 1, "103": 1}, "legacy": {}, "LLegacy": 0}


def test_the_mirrored_constants_match_the_runtime():
    if not SCRIPT.exists():
        pytest.skip("infra/ not mounted (the unit command mounts backend/ only)")
    import importlib.util
    from runner import projection, stream_prefix

    spec = importlib.util.spec_from_file_location("journal_lengths_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (module.P, module.VERSION, module.FIELDS, module.LIMIT, module.MASK) == (
        stream_prefix.P, stream_prefix.VERSION, stream_prefix.FIELDS, stream_prefix.LIMIT, projection.MASK)
