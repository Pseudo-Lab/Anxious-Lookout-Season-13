"""Reviewed stdin-exec read-only checks. No model RPC, supply or automatic retry."""
import argparse
import hashlib
import hmac
import json
import os
import signal
import stat
from datetime import datetime, timezone
from pathlib import Path

PROTECTED_FILES = ("native/account-id", "native/state-binding.json", "native/broker.json",
                   "control/access-ledger.json", "control/access-projection.json")


def protected_check(files, owner, state):
    """Existing used access-only owner. Missing state is never a new installation."""
    binding = json.loads(files["native/state-binding.json"])
    if type(binding.get("version")) is not int or binding != {"version": 1, "ownerId": owner, "stateId": state} or files["native/account-id"].decode().strip() != owner:
        raise ValueError()
    ledger = json.loads(files["control/access-ledger.json"])
    if (type(ledger.get("version")) is not int or ledger["version"] != 1 or ledger.get("owner") != owner
            or ledger.get("stateId") != state or not isinstance(ledger.get("accountId"), str) or not ledger["accountId"]
            or not isinstance(ledger.get("trialId"), str) or not ledger["trialId"]
            or not isinstance(ledger.get("windows"), list) or not ledger["windows"]
            or any(not isinstance(window, str) or not window for window in ledger["windows"])
            or len(set(ledger["windows"])) != len(ledger["windows"]) or ledger.get("windowId") not in ledger["windows"]
            or not isinstance(ledger.get("requests"), list) or not ledger["requests"]):
        raise ValueError()
    requests = set()
    for request in ledger["requests"]:
        if (not isinstance(request, dict) or set(request) != {"id", "windowId"}
                or not isinstance(request["id"], str) or not request["id"] or request["id"] in requests
                or request["windowId"] not in ledger["windows"]):
            raise ValueError()
        requests.add(request["id"])
    journal = json.loads(files["control/access-projection.json"])
    if (set(journal) != {"version", "owner", "trialId", "key", "values", "mac"}
            or type(journal["version"]) is not int or journal["version"] != 1
            or journal["owner"] != owner or journal["trialId"] != ledger["trialId"]
            or not isinstance(journal["values"], list) or len(journal["values"]) > 512):
        raise ValueError()
    key = bytes.fromhex(journal["key"])
    if len(key) != 32: raise ValueError()
    encoded = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    signature = lambda prefix, value: hmac.new(key, prefix + encoded(value), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature(b"journal-v1\0", {name: value for name, value in journal.items() if name != "mac"}), journal["mac"]):
        raise ValueError()
    for length, filter_hash, fingerprint in journal["values"]:
        if type(length) is not int or not 1 <= length <= 65536 or type(filter_hash) is not int or not 0 <= filter_hash < 2**64 or len(bytes.fromhex(fingerprint)) != 32:
            raise ValueError()
    broker = json.loads(files["native/broker.json"])
    if not isinstance(broker, dict) or not broker: raise ValueError()
    recorded = set()
    for session, entry in broker.items():
        if (not isinstance(entry, dict) or entry.get("state") not in {"running", "idle", "failed"}
                or type(entry.get("projectionVersion")) is not int or entry["projectionVersion"] != 4
                or entry.get("request") not in requests or not isinstance(entry.get("requests"), dict)
                or entry["request"] not in entry["requests"] or not set(entry["requests"]) <= requests):
            raise ValueError()
        if (recorded & set(entry["requests"]) or any(not isinstance(value, str) or len(value) != 64
                or any(char not in "0123456789abcdef" for char in value) for value in entry["requests"].values())):
            raise ValueError()
        recorded.update(entry["requests"])
        record = entry.get("record")
        if not isinstance(record, dict): raise ValueError()
        if record.get("turns") or "modelMismatchBaseline" in record:
            expected = signature(b"projection-v4\0", [owner, ledger["trialId"], session, entry.get("thread"), record])
            if not isinstance(entry.get("projectionMac"), str) or not hmac.compare_digest(expected, entry["projectionMac"]):
                raise ValueError()
    # Orphaned reservations may represent lost broker history or an ambiguous
    # interrupted save. Neither is accepted as intact existing history.
    if recorded != requests: raise ValueError()
    return {"ownerStateVerified": True, "nativeRunning": sum(entry["state"] == "running" for entry in broker.values()),
            "ambiguous": sum(entry.get("error") == "codex_unavailable" for entry in broker.values()),
            "ledgerSha256": hashlib.sha256(files["control/access-ledger.json"]).hexdigest(), "lifetimeReservations": len(requests),
            "protectedState": {name: hashlib.sha256(files[name]).hexdigest() for name in PROTECTED_FILES}}


def read_owned(path, maximum=64_000_000):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > maximum:
            raise ValueError()
        data = source.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError()
    return data


def read_json(path, maximum=64_000_000):
    return json.loads(read_owned(path, maximum))


def api_check():
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import NullPool
    from app.settings import secret
    from app.schema_contract import validate_v1
    from app.research_schema_contract import validate_research
    if os.getenv("CODEX_PERSONAL_ENABLE") != "false":
        raise ValueError()
    engine = create_engine(secret("DATABASE_URL"), poolclass=NullPool, hide_parameters=True, connect_args={"connect_timeout": 3})
    try:
        with engine.begin() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            connection.execute(text("SET LOCAL statement_timeout='3000ms'"))
            connection.execute(text("SET LOCAL lock_timeout='1000ms'"))
            validate_v1(connection)
            if validate_research(connection) != "0004_publication":
                raise ValueError()
            counts = connection.execute(text("SELECT count(*) FILTER (WHERE state='running'), count(*) FILTER (WHERE state='failed' AND error_code='codex_unavailable' AND request_id IS NOT NULL) FROM research.conversations")).one()
            return {"admissionHeld": True, "schemaReady": True, "apiRunning": counts[0], "ambiguous": counts[1]}
    finally:
        engine.dispose()


def runner_check(root, control, owner, state):
    for path in (root, control):
        if path.resolve() != path or not path.is_dir() or path.stat().st_uid != os.getuid():
            raise ValueError()
    try:
        files = {name: read_owned((root if name.startswith("native/") else control) / name.split("/", 1)[1],
                                 64_000_000 if name == "native/broker.json" else 2_000_000) for name in PROTECTED_FILES}
    except OSError:
        raise ValueError() from None
    result = protected_check(files, owner, state)
    # Detect replacement/concurrent changes even during this bounded check.
    for name, raw in files.items():
        if read_owned((root if name.startswith("native/") else control) / name.split("/", 1)[1]) != raw:
            raise ValueError()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("api", "runner"))
    args = parser.parse_args()
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError()))
    signal.alarm(20)
    try:
        if args.mode == "api": result = api_check()
        else:
            root, control = os.environ["RUNNER_STATE_DIR"], os.environ["CODEX_ACCESS_CONTROL_DIR"]
            if root != "/native/access-web" or control != "/control/access-web":
                raise ValueError()
            result = runner_check(Path(root), Path(control), os.environ["RUNNER_ACCOUNT_ID"], os.environ["RUNNER_STATE_ID"])
        result["checkedAt"] = datetime.now(timezone.utc).isoformat()
        print(json.dumps(result))
        if result.get("apiRunning", result.get("nativeRunning", 0)) or result["ambiguous"]:
            raise SystemExit(2)
    except Exception:
        raise SystemExit("Chatbot read-only check failed; credentials, rows and exception details are not printed")
    finally:
        signal.alarm(0)


if __name__ == "__main__":
    main()
