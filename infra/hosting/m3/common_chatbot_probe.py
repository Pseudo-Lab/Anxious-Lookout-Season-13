"""Reviewed stdin-exec read-only checks. No model RPC, supply or automatic retry."""
import argparse
import hashlib
import hmac
import json
import os
import signal
import stat
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PROTECTED_FILES = ("native/account-id", "native/state-binding.json", "native/broker.json",
                   "control/access-ledger.json", "control/access-projection.json")
AUTH_REASONS = {"not_configured", "auth_expired", "auth_revoked", "budget_exhausted", "provider_rate_limited",
                "model_unavailable", "policy_refused", "unavailable"}
SAFE_ERRORS = {"codex_rejected", "codex_failed", "codex_unavailable", "codex_auth_expired", "codex_auth_revoked",
               "codex_budget_exhausted", "codex_policy_refused", "codex_model_unavailable", "codex_rate_limited", "codex_not_enabled_for_account", None}


def opaque(kind, value):
    if value is None: return None
    if not isinstance(value, str) or not value or len(value) > 200: raise ValueError()
    return hashlib.sha256(("chatbot-link-v1\0" + kind + "\0" + value).encode()).hexdigest()


def health_fields(value, owner, state, pod):
    if (value.get("ownerId") != owner or value.get("stateId") != state or value.get("podUid") != pod
            or value.get("model") != "gpt-6.1-sol" or value.get("verification") not in {"unverified", "real"}
            or type(value.get("available")) is not bool or value.get("reason") not in AUTH_REASONS | {None}
            or value["available"] != (value.get("reason") is None)):
        raise ValueError()
    return {"authCheckVerified": True, "ownerStateVerified": True, "podDigest": opaque("pod", pod),
            "ownerDigest": opaque("owner", owner), "stateDigest": opaque("state", state),
            "authAvailable": value["available"], "authReason": value.get("reason"), "verification": value["verification"]}


def health_check():
    token_path = os.environ["RUNNER_TOKEN_FILE"]
    if token_path != "/run/transport/transport-token": raise ValueError()
    owner, state, pod = (os.environ[key] for key in ("RUNNER_ACCOUNT_ID", "RUNNER_STATE_ID", "RUNNER_POD_UID"))
    token = Path(token_path).read_text().strip()
    if len(token) < 32 or len(token) > 10000 or "\n" in token or "\r" in token: raise ValueError()
    request = urllib.request.Request("http://127.0.0.1:8080/health", headers={
        "Authorization": "Bearer " + token, "X-Runner-State-Id": state})
    # Fixed localhost only; no proxy environment, redirects or session/history RPC.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs): return None
    with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(request, timeout=2) as response:
        raw = response.read(10001)
    if len(raw) > 10000: raise ValueError()
    return health_fields(json.loads(raw), owner, state, pod)


def api_links():
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import NullPool
    from app.settings import secret
    # Existing mounted mapping supplies only owner/state; never read transport token.
    if api_check() != {"admissionHeld": True, "schemaReady": True, "apiRunning": 0, "ambiguous": 0}: raise ValueError()
    owner = os.environ["CODEX_PERSONAL_ACCOUNT_ID"]
    mapping = json.loads(Path(os.environ["CODEX_RUNNERS_FILE"]).read_text())
    if set(mapping) != {owner}: raise ValueError()
    state = mapping[owner]["stateId"]
    engine = create_engine(secret("DATABASE_URL"), poolclass=NullPool, hide_parameters=True, connect_args={"connect_timeout": 3})
    try:
        with engine.begin() as connection:
            connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            connection.execute(text("SET LOCAL statement_timeout='3000ms'"))
            connection.execute(text("SET LOCAL lock_timeout='1000ms'"))
            rows = connection.execute(text("""SELECT id::text,request_id::text,state,error_code,archived,version,
                native_record->>'nativeThreadId',encode(sha256(convert_to(native_record::text,'UTF8')),'hex'),
                coalesce((SELECT jsonb_agg(jsonb_build_object('request',p->>'requestId','turn',p->>'turnId'))
                  FROM jsonb_array_elements(coalesce(native_record->'platformRequests','[]'::jsonb)) p),'[]'::jsonb),
                coalesce((SELECT jsonb_agg(t->>'id') FROM jsonb_array_elements(coalesce(native_record->'turns','[]'::jsonb)) t),'[]'::jsonb)
                FROM research.conversations WHERE owner_id=:owner ORDER BY id LIMIT 10001"""), {"owner": owner}).all()
        if len(rows) > 10000: raise ValueError()
        result = []
        for session, request, status, error, archived, version, thread, record_hash, requests, turns in rows:
            if status not in {"idle", "failed"} or error not in SAFE_ERRORS: raise ValueError()
            result.append({"session": opaque("session", session), "request": opaque("request", request), "thread": opaque("thread", thread),
                "state": status, "error": error, "archived": archived, "version": version, "recordSha256": record_hash,
                "turns": sorted(opaque("turn", turn) for turn in turns),
                "platformRequests": [{"request": opaque("request", item["request"]), "turn": opaque("turn", item["turn"])} for item in requests]})
        return {"admissionHeld": True, "ownerDigest": opaque("owner", owner), "stateDigest": opaque("state", state), "sessions": result}
    finally: engine.dispose()


def runner_links(root, control, owner, state):
    result = runner_check(root, control, owner, state)
    if result["nativeRunning"] or result["ambiguous"]: raise ValueError()
    raw = read_owned(root / "broker.json")
    if hashlib.sha256(raw).hexdigest() != result["protectedState"]["native/broker.json"]: raise ValueError()
    sessions = []
    for session, entry in sorted(json.loads(raw).items()):
        sessions.append({"session": opaque("session", session), "request": opaque("request", entry["request"]),
            "thread": opaque("thread", entry.get("thread")), "state": entry["state"],
            "requests": sorted(opaque("request", request) for request in entry["requests"]),
            "turns": sorted(opaque("turn", turn["id"]) for turn in entry["record"].get("turns", []))})
    return {**result, "ownerDigest": opaque("owner", owner), "stateDigest": opaque("state", state), "sessions": sessions}


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
    parser.add_argument("mode", choices=("api", "runner", "api-links", "runner-links", "health"))
    args = parser.parse_args()
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError()))
    signal.alarm(20)
    try:
        if args.mode == "api": result = api_check()
        elif args.mode == "api-links": result = api_links()
        elif args.mode == "health": result = health_check()
        else:
            root, control = os.environ["RUNNER_STATE_DIR"], os.environ["CODEX_ACCESS_CONTROL_DIR"]
            if root != "/native/access-web" or control != "/control/access-web":
                raise ValueError()
            checker = runner_links if args.mode == "runner-links" else runner_check
            result = checker(Path(root), Path(control), os.environ["RUNNER_ACCOUNT_ID"], os.environ["RUNNER_STATE_ID"])
        result["checkedAt"] = datetime.now(timezone.utc).isoformat()
        print(json.dumps(result))
        if result.get("apiRunning", result.get("nativeRunning", 0)) or result.get("ambiguous", 0):
            raise SystemExit(2)
    except Exception:
        raise SystemExit("Chatbot read-only check failed; credentials, rows and exception details are not printed")
    finally:
        signal.alarm(0)


if __name__ == "__main__":
    main()
