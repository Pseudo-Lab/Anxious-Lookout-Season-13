"""#95 B1 (§6 B, user decision 2026-11-15; design r1-r4.1 on #95): a retired service access token leaves the stream
**holdback** (never the redaction) once it is proven expired. Service mode only, behind STREAM_RETIRED (default off).

What this module keeps, in `stream-retired.json` next to `stream-prefixes.json` (same projection key, own MAC, bound
to the journal's owner / trialId, private file, size caps; missing / damaged / foreign = nothing is excluded):
- `t0Rows`: every long journal row present when this code first observed the control directory (T0). These rows can
  never become `proven` through later records (review r2): their history before T0 is unknown. Releasing them is the
  `assumed` path (A1 / A2, #95), which stays **inactive** until its anchor (route 1, design r4) is implemented and
  reviewed - until then they are `pending`.
- `records`: per install, each long literal's kind - the envelope's access token `token` with its own `exp`, the
  account and e-mail `identifier`. A row with any `identifier` record is never excluded; a row with no record is never
  excluded.
- `maxIat`: the highest `iat` of an installed token (provider time). `proven` compares provider times only - a token is
  excluded when `exp + MARGIN < maxIat` - never the local clock (review r0 major 3). A missing or non-integer `iat`, an
  `iat` lower than `maxIat`, or `iat > exp` is an anomaly: nothing is excluded until a later token's valid `iat`
  exceeds `maxIat`. A lifetime `exp - iat` over LIFETIME_LIMIT disables the owner (A1's bound, kept permanently).
- `lifetimeHoursMax` / `lifetimeCount`: the observed lifetimes (#95 B4: max and count only).
Failure scope (stated, design r2): the times are broker-delivered claims, not signature-checked - the trust the runner
already gives the token's `exp`.

Every line logged (`runner_stream_retired`) holds states and counts only, and only when the state changes.
"""
import hashlib
import hmac
import json
import logging
import os
import stat
import threading
import uuid
from pathlib import Path

from .stream_prefix import P

FILE = "stream-retired.json"
VERSION = 1
LIMIT = 4096                 # rows and records each
MARGIN = 600                 # seconds after a token's exp before it may leave the holdback (design r1)
G = 11 * 86400               # A1 (user, #95): past service tokens lived <= 10 days; G = 11 days
LIFETIME_LIMIT = G - 86400   # an installed token living longer than this disables the owner (fail-closed)
FIELDS = {"version", "owner", "trialId", "t0Rows", "records", "maxIat", "iatAnomaly", "disabled",
          "lifetimeHoursMax", "lifetimeCount", "anchor", "anchorAttempts", "mac"}
KINDS = {"token", "identifier"}
STATES = {"off", "unproven", "pending", "proven"}  # `assumed` joins with route 1 (design r4)
_lock = threading.Lock()
_observed = {}
_cache = {}


def enabled():
    return os.environ.get("STREAM_RETIRED", "false") == "true"


def _mac(key, payload):
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hmac.new(key, b"stream-retired-v1\0" + body, hashlib.sha256).hexdigest()


def _hex64(value):
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _row(item):
    return (isinstance(item, list) and len(item) == 2 and type(item[0]) is int and P < item[0] <= 65536
            and _hex64(item[1]))


def _record(item):
    return (isinstance(item, list) and len(item) == 4 and type(item[0]) is int and P < item[0] <= 65536
            and _hex64(item[1]) and item[2] in KINDS
            and (type(item[3]) is int if item[2] == "token" else item[3] is None))


def _int_or_none(value):
    return value is None or type(value) is int


def _valid(payload, journal):
    if not (isinstance(payload, dict) and set(payload) == FIELDS and payload["version"] == VERSION
            and payload["owner"] == journal.owner and payload["trialId"] == journal.trial
            and isinstance(payload["t0Rows"], list) and len(payload["t0Rows"]) <= LIMIT
            and all(_row(item) for item in payload["t0Rows"])
            and isinstance(payload["records"], list) and len(payload["records"]) <= LIMIT
            and all(_record(item) for item in payload["records"])
            and _int_or_none(payload["maxIat"]) and type(payload["iatAnomaly"]) is bool
            and payload["disabled"] in (None, "lifetime")
            and _int_or_none(payload["lifetimeHoursMax"]) and type(payload["lifetimeCount"]) is int
            and payload["anchor"] is None and type(payload["anchorAttempts"]) is int
            and isinstance(payload["mac"], str)):
        return None
    if not hmac.compare_digest(_mac(journal.key, {k: v for k, v in payload.items() if k != "mac"}), payload["mac"]):
        return None
    return payload


def load(control, journal):
    """(payload or None, file state). The prefix module's private-file read (lstat, O_NOFOLLOW | O_NONBLOCK, fstat)."""
    from .stream_prefix import _read
    path = Path(control) / FILE
    try:
        data, state = _read(path)
    except FileNotFoundError:
        return None, ("absent",)
    except Exception:
        return None, ("unreadable",)
    try:
        return _valid(json.loads(data), journal), state
    except Exception:
        return None, state


def _write(control, journal, payload):
    payload = dict(payload)
    payload.pop("mac", None)
    payload["mac"] = _mac(journal.key, payload)
    path = Path(control) / FILE
    temporary = path.parent / (".stream-retired-" + uuid.uuid4().hex)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "w") as target:
            json.dump(payload, target, sort_keys=True, separators=(",", ":"))
            target.flush()
            os.fsync(target.fileno())
        if path.is_symlink():
            raise ValueError()
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def observe_install(control, journal, token, identifiers, iat, exp):
    """After a service install's journal commit: record the kinds of this install's long literals, the provider time
    and the lifetime. Never raises; on any failure nothing new becomes excludable (a missing record never excludes)."""
    if not enabled() or control is None or journal is None:
        return
    with _lock:
        try:
            payload, _ = load(control, journal)
            if payload is None:
                # First observation (T0), or a missing / damaged / foreign file: every long row now present becomes
                # a T0 row - conservative (this install's token included; it can never be proven).
                _, rows = journal.stream_rows(P)
                payload = {"version": VERSION, "owner": journal.owner, "trialId": journal.trial,
                           "t0Rows": sorted([list(row) for row in rows])[:LIMIT], "records": [], "maxIat": None,
                           "iatAnomaly": False, "disabled": None, "lifetimeHoursMax": None, "lifetimeCount": 0,
                           "anchor": None, "anchorAttempts": 0}
                if len(rows) > LIMIT:
                    payload["iatAnomaly"] = True  # over the cap: never exclude for this directory
            records = {(item[0], item[1], item[2], item[3]) for item in payload["records"]}
            fresh = set()
            if isinstance(token, str) and P < len(token) <= 65536 and type(exp) is int:
                fresh.add((len(token), journal.digest(token), "token", exp))
            for value in identifiers:
                if isinstance(value, str) and P < len(value) <= 65536:
                    fresh.add((len(value), journal.digest(value), "identifier", None))
            records |= fresh
            valid_time = type(iat) is int and type(exp) is int and iat <= exp
            if valid_time:
                lifetime = exp - iat
                hours = lifetime // 3600
                payload["lifetimeHoursMax"] = hours if payload["lifetimeHoursMax"] is None else max(
                    payload["lifetimeHoursMax"], hours)
                payload["lifetimeCount"] += 1
                if lifetime > LIFETIME_LIMIT:
                    payload["disabled"] = "lifetime"
                if payload["maxIat"] is not None and iat < payload["maxIat"]:
                    payload["iatAnomaly"] = True
                elif payload["maxIat"] is None or iat > payload["maxIat"]:
                    payload["maxIat"] = iat
                    payload["iatAnomaly"] = len(records) > LIMIT
            else:
                payload["iatAnomaly"] = True
            if len(records) > LIMIT:
                payload["iatAnomaly"] = True  # over the cap: never exclude, keep what is known
            payload["records"] = sorted([list(item) for item in records], key=str)[:LIMIT]
            _write(control, journal, payload)
            state, _, counts = _evaluate(payload, set())
            _log(control, state, payload, counts)
        except Exception:
            _log(control, "unproven", None, None)


def _evaluate(payload, protected):
    """(state, rows proven excludable, counts) for a valid payload. `protected`: rows that are never excluded."""
    if payload is None:
        return "unproven", frozenset(), {}
    if payload["disabled"]:
        return "off", frozenset(), {}
    t0 = {(item[0], item[1]) for item in payload["t0Rows"]}
    kinds = {}
    expiry = {}
    for length, digest, kind, exp in payload["records"]:
        kinds.setdefault((length, digest), set()).add(kind)
        if kind == "token":
            expiry[(length, digest)] = max(expiry.get((length, digest), exp), exp)
    if payload["iatAnomaly"] or payload["maxIat"] is None:
        return "unproven", frozenset(), {"t0Rows": len(t0)}
    proven = frozenset(row for row, kind in kinds.items()
                       if kind == {"token"} and row not in t0 and row not in protected
                       and expiry[row] + MARGIN < payload["maxIat"])
    return ("proven" if proven else "pending"), proven, {"t0Rows": len(t0), "proven": len(proven)}


def excluded(control, journal, protected):
    """(rows proven excludable, state, file state) for the holdback - service mode with the switch on only. A stat per
    call; the file is read again only when its (inode, mtime, size) changes."""
    if not enabled() or control is None or journal is None:
        return frozenset(), "off", ("off",)
    try:
        info = os.lstat(Path(control) / FILE)
        key = (str(control), info.st_ino, info.st_mtime_ns, info.st_size, journal.owner, journal.trial)
    except FileNotFoundError:
        return frozenset(), "unproven", ("absent",)
    except Exception:
        return frozenset(), "unproven", ("unreadable",)
    cached = _cache.get(str(control))
    if cached is None or cached[0] != key:
        payload, state = load(control, journal)
        if len(_cache) >= 64:
            _cache.clear()
        _cache[str(control)] = (key, payload, state)
        cached = _cache[str(control)]
    _, payload, file_state = cached
    status, rows, _ = _evaluate(payload, protected)
    return rows, status, file_state


def _log(control, state, payload, counts):
    try:
        line = {"state": state if state in STATES else "other",
                "t0Rows": None if payload is None else len(payload["t0Rows"]),
                "records": None if payload is None else len(payload["records"]),
                "proven": (counts or {}).get("proven"),
                "iatAnomaly": None if payload is None else payload["iatAnomaly"],
                "disabled": None if payload is None else payload["disabled"],
                "lifetimeHoursMax": None if payload is None else payload["lifetimeHoursMax"],
                "lifetimeCount": None if payload is None else payload["lifetimeCount"],
                "assumed": "inactive"}
        key = str(control)
        if _observed.get(key) == line:
            return
        if key not in _observed and len(_observed) >= 64:
            _observed.clear()
        _observed[key] = line
        logging.getLogger("uvicorn.error").info("runner_stream_retired %s", json.dumps(line, sort_keys=True))
    except Exception:
        pass
