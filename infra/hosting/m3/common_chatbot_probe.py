"""Reviewed stdin-exec read-only checks. No model RPC, supply or automatic retry."""
import argparse
import hashlib
import json
import os
import signal
import stat
from datetime import datetime, timezone
from pathlib import Path


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
    binding = read_json(root / "state-binding.json", 10000)
    if type(binding.get("version")) is not int or binding != {"version": 1, "ownerId": owner, "stateId": state} or read_owned(root / "account-id", 1000).decode().strip() != owner:
        raise ValueError()
    broker = read_json(root / "broker.json") if (root / "broker.json").exists() else {}
    if not isinstance(broker, dict):
        raise ValueError()
    if any(not isinstance(entry, dict) or entry.get("state") not in {"running", "idle", "failed"} for entry in broker.values()):
        raise ValueError()
    running = sum(entry.get("state") == "running" for entry in broker.values())
    ambiguous = sum(entry.get("error") == "codex_unavailable" for entry in broker.values())
    ledger = control / "access-ledger.json"
    ledger_digest = None
    reservations = None
    if ledger.exists():
        raw = read_owned(ledger, 2_000_000)
        value = json.loads(raw)
        if value.get("owner") != owner or value.get("stateId") != state or not isinstance(value.get("requests"), list):
            raise ValueError()
        ledger_digest = hashlib.sha256(raw).hexdigest()
        reservations = len(value["requests"])
    return {"ownerStateVerified": True, "nativeRunning": running, "ambiguous": ambiguous,
            "ledgerSha256": ledger_digest, "lifetimeReservations": reservations}


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
