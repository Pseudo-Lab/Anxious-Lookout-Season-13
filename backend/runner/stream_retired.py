"""#95 B1 (§6 B, user decision 2026-11-15; design r1-r4.1 on #95): a retired service access token leaves the stream
**holdback** (never the redaction) once it is proven expired. Service mode only, behind STREAM_RETIRED (default off).

What this module keeps, in `stream-retired.json` next to `stream-prefixes.json` (same projection key, own MAC, bound
to the journal's owner / trialId, private file, size caps; missing / damaged / foreign = nothing is excluded):
- `t0Rows`: every long journal row present when this code first observed the control directory (T0). These rows can
  never become `proven` through later records (review r2): their history before T0 is unknown. Releasing them is the
  **`assumed`** path - user-approved assumptions **A1** (past service tokens lived <= 10 days) and **A2** (the service
  account e-mail never changed), #95 decision comment of 2026-11-15T14:27Z - and needs all of: `t0Assumable` (at T0,
  N == R over DISTINCT snapshot fingerprints, no `migration` row), an `anchor` written by the operator's
  runner.stream_anchor from a broker-attested token (route 1, design r4.2), and `maxIat > anchor.iat +
  ATTEST_TOLERANCE + G`. Until then they are `pending`. The runner never requests an anchor by itself.
- `epoch`: a new id when the sidecar is created; an anchor is recorded only for the epoch it was reserved in.
- **Permanent off (review r0):** two journal marker literals, durable with the journal and outside the sidecar.
  `#95-retired-seen` is written before the first sidecar; `#95-retired-off!` whenever anything goes wrong - a
  failed observation (lock, read, write), a lost or damaged sidecar after SEEN, a T0 or record overflow, a lifetime over
  A1's bound, or an **incomplete history**: the distinct snapshot fingerprints must equal those at T0 plus the token
  records after T0, so a missed install (and the identifiers it carried) is caught even after a restart without the
  marker. Once off, nothing is ever excluded for that journal; there is no reset (the evidence that made it off is
  gone or untrusted).
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
ATTEST_TOLERANCE = 300      # broker/credentials.py ATTEST_TOLERANCE: the anchor may predate the request by this
FIELDS = {"version", "owner", "trialId", "epoch", "t0Rows", "t0Snapshots", "t0Assumable", "records", "maxIat",
          "iatAnomaly", "disabled", "lifetimeHoursMax", "lifetimeCount", "anchor", "anchorAttempts", "mac"}
KINDS = {"token", "identifier"}
STATES = {"off", "unproven", "pending", "proven", "assumed"}
REASONS = {None, "marked", "record_lost", "overflow", "marker_write_failed", "incomplete", "lifetime",
           "observe_failed"}
LOCK = "stream-retired.lock"
# review r0 (#95): journal marker literals (16 chars: short literals, at most a 15-char short holdback like any
# covered literal). SEEN: observation began for this journal. OFF: never exclude again for this journal.
MARKER_SEEN = "#95-retired-seen"
MARKER_OFF = "#95-retired-off!"
_lock = threading.Lock()
_observed = {}
_cache = {}
_poisoned = set()   # control dirs this process turned off (also when the OFF marker could not be written)


def enabled():
    return os.environ.get("STREAM_RETIRED", "false") == "true"


def _mac(key, payload):
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hmac.new(key, b"stream-retired-v1\0" + body, hashlib.sha256).hexdigest()


def _hex64(value):
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _hex32(value):
    return all(char in "0123456789abcdef" for char in value)


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
            and type(payload["t0Snapshots"]) is int
            and isinstance(payload["epoch"], str) and len(payload["epoch"]) == 32 and _hex32(payload["epoch"])
            and type(payload["t0Assumable"]) is bool
            and (payload["anchor"] is None or (isinstance(payload["anchor"], dict) and set(payload["anchor"]) == {"iat"}
                                               and type(payload["anchor"]["iat"]) is int))
            and type(payload["anchorAttempts"]) is int and isinstance(payload["mac"], str)):
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


class file_lock:
    """The cross-process lock of the sidecar (the runner and runner.stream_anchor): flock on a private lock file in the
    control directory, held only around a read-modify-write - never across a network call."""

    def __init__(self, control):
        self.path, self.descriptor = Path(control) / LOCK, None

    def __enter__(self):
        import fcntl
        from .service_store import private_file
        self.descriptor = private_file(self.path, create=True)
        fcntl.flock(self.descriptor, fcntl.LOCK_EX)
        return self

    def __exit__(self, *args):
        if self.descriptor is not None:
            os.close(self.descriptor)  # closing releases the flock
        return False


def assumable(journal, rows, current):
    """A2 path at T0 (#95 design r0 / user decision): every non-current long row is a retired access token only when
    N == R - N the non-current long rows, R the distinct snapshot fingerprints other than the current one - and the
    store was never migrated offline (its row kinds are unknown). False on any doubt or error."""
    try:
        with journal.auth.store.transaction() as db:
            if db.execute("SELECT 1 FROM migration").fetchone():
                return False
            ledger = journal.auth._state(db)
            distinct = db.execute("SELECT COUNT(DISTINCT fingerprint) FROM snapshots").fetchone()[0]
            current_fingerprint = db.execute("SELECT 1 FROM snapshots WHERE fingerprint=? LIMIT 1",
                                             (ledger["snapshotFingerprint"],)).fetchone()
        if not current_fingerprint or type(distinct) is not int:
            return False
        return len(set(rows) - set(current)) == distinct - 1
    except Exception:
        return False


def _markers(journal):
    """(seen, off, distinct snapshot fingerprints) from the store, one transaction. The two markers are journal
    literals (review r0, #95): durable with the journal itself, MAC'd per row, outside the sidecar - so losing,
    damaging or recreating the sidecar can neither forget that observation started nor clear a permanent off."""
    rows = {}
    with journal.auth.store.transaction() as db:
        for marker in (MARKER_SEEN, MARKER_OFF):
            rows[marker] = db.execute("SELECT 1 FROM literals WHERE length=? AND digest=?",
                                      (len(marker), journal.digest(marker))).fetchone() is not None
        distinct = db.execute("SELECT COUNT(DISTINCT fingerprint) FROM snapshots").fetchone()[0]
    return rows[MARKER_SEEN], rows[MARKER_OFF], distinct


def _mark(journal, marker):
    """Write a marker literal into the journal (the normal row path: MAC'd, schema unchanged). Returns success."""
    try:
        with journal.auth.store.transaction() as db:
            journal.remember(db, [marker])
        return True
    except Exception:
        return False


def _poison(control, journal, reason):
    """Permanent off for this journal: the durable marker, the process-level flag (if the marker cannot be written,
    this process still excludes nothing; after a restart the completeness check catches what was missed), a log."""
    _poisoned.add(str(control))
    _mark(journal, MARKER_OFF)
    _log(control, "off", None, {"reason": reason})


def _complete(payload, distinct):
    """Every snapshot taken after T0 has its install recorded: distinct fingerprints now == those at T0 + the token
    records not among the T0 rows. A missed observation (failed write, lock, crash) leaves a token - and its install's
    identifiers - unrecorded, which this detects for good (review r0 blocker)."""
    t0 = {(item[0], item[1]) for item in payload["t0Rows"]}
    tokens = {(item[0], item[1]) for item in payload["records"] if item[2] == "token"} - t0
    return type(distinct) is int and distinct == payload["t0Snapshots"] + len(tokens)


def observe_install(control, journal, token, identifiers, iat, exp):
    """After a service install's journal commit: record the kinds of this install's long literals, the provider time
    and the lifetime. Never raises into the install. Any failure here (lock, read, write, a lost or damaged sidecar
    after observation began, an incomplete history, an overflow, a lifetime over A1's bound) turns the owner off for
    good: nothing is ever excluded again for this journal (review r0)."""
    if not enabled() or control is None or journal is None:
        return
    try:
        with _lock, file_lock(control):
            seen, off, distinct = _markers(journal)
            if off:
                _poisoned.add(str(control))
                _log(control, "off", None, {"reason": "marked"})
                return
            payload, _ = load(control, journal)
            if payload is None:
                if seen:
                    # observation began earlier and its record is gone or damaged: the history is incomplete
                    _poison(control, journal, "record_lost")
                    return
                # First observation (T0): every long row now present is a T0 row - conservative (this install's
                # token included; it can never be proven). The seen marker goes first, so a sidecar write failing
                # after it reads as a loss next time.
                _, rows = journal.stream_rows(P)
                current = {(len(value), journal.digest(value)) for value in [token, *identifiers]
                           if isinstance(value, str) and len(value) > P}
                if len(rows) > LIMIT:
                    _mark(journal, MARKER_SEEN)
                    _poison(control, journal, "overflow")
                    return
                if not _mark(journal, MARKER_SEEN):
                    _poisoned.add(str(control))
                    _log(control, "off", None, {"reason": "marker_write_failed"})
                    return
                _, _, distinct = _markers(journal)
                payload = {"version": VERSION, "owner": journal.owner, "trialId": journal.trial,
                           "epoch": uuid.uuid4().hex, "t0Rows": sorted([list(row) for row in rows]),
                           "t0Snapshots": distinct, "t0Assumable": assumable(journal, rows, current), "records": [],
                           "maxIat": None, "iatAnomaly": False, "disabled": None, "lifetimeHoursMax": None,
                           "lifetimeCount": 0, "anchor": None, "anchorAttempts": 0}
            records = {(item[0], item[1], item[2], item[3]) for item in payload["records"]}
            fresh = set()
            if isinstance(token, str) and P < len(token) <= 65536 and type(exp) is int:
                fresh.add((len(token), journal.digest(token), "token", exp))
            for value in identifiers:
                if isinstance(value, str) and P < len(value) <= 65536:
                    fresh.add((len(value), journal.digest(value), "identifier", None))
            records |= fresh
            if len(records) > LIMIT:
                _poison(control, journal, "overflow")
                return
            payload["records"] = sorted([list(item) for item in records], key=str)
            if not _complete(payload, distinct):
                _poison(control, journal, "incomplete")
                return
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
                    payload["iatAnomaly"] = False
            else:
                payload["iatAnomaly"] = True
            _write(control, journal, payload)
            if payload["disabled"]:
                _poison(control, journal, payload["disabled"])  # A1 disproven for this owner: off for good
                return
            state, _, _, counts = _evaluate(payload, set())
            _log(control, state, payload, counts)
    except Exception:
        _poison(control, journal, "observe_failed")


def _evaluate(payload, protected):
    """(state, proven rows, assumed rows, counts) for a valid payload. `protected`: rows that are never excluded.
    proven: retired tokens recorded after T0, kind `token` only, `exp + MARGIN < maxIat` (no assumption).
    assumed: the T0 rows under A1 / A2 (user decision, #95), only with a broker-attested anchor (route 1, design r4.2):
    `maxIat > anchor.iat + ATTEST_TOLERANCE + G`, the T0 count proof (`t0Assumable`), no identifier record."""
    if payload is None:
        return "unproven", frozenset(), frozenset(), {}
    if payload["disabled"]:
        return "off", frozenset(), frozenset(), {}
    t0 = {(item[0], item[1]) for item in payload["t0Rows"]}
    kinds = {}
    expiry = {}
    for length, digest, kind, exp in payload["records"]:
        kinds.setdefault((length, digest), set()).add(kind)
        if kind == "token":
            expiry[(length, digest)] = max(expiry.get((length, digest), exp), exp)
    if payload["iatAnomaly"] or payload["maxIat"] is None:
        return "unproven", frozenset(), frozenset(), {"t0Rows": len(t0)}
    proven = frozenset(row for row, kind in kinds.items()
                       if kind == {"token"} and row not in t0 and row not in protected
                       and expiry[row] + MARGIN < payload["maxIat"])
    assumed = frozenset()
    anchor = payload["anchor"]
    if (anchor is not None and payload["t0Assumable"]
            and payload["maxIat"] > anchor["iat"] + ATTEST_TOLERANCE + G):
        assumed = frozenset(row for row in t0 if row not in protected and "identifier" not in kinds.get(row, set()))
    state = "assumed" if assumed else "proven" if proven else "pending"
    return state, proven, assumed, {"t0Rows": len(t0), "proven": len(proven), "assumed": len(assumed)}


def excluded(control, journal, protected):
    """(rows excludable, state, file state, the assumed part of the rows) for the holdback - service mode with the
    switch on only. review r0: nothing is excluded when this process turned the owner off, when the journal carries
    the OFF marker, when observation began (SEEN marker) and the record is missing, damaged, not private or foreign,
    or when the history is incomplete against the store's snapshots right now. The file's private attributes are
    checked on every call (not only when its content changes) and are part of the cache key."""
    from .stream_prefix import _private

    def nothing(state, file_state):
        return frozenset(), state, file_state, frozenset()
    if not enabled() or control is None or journal is None:
        return nothing("off", ("off",))
    if str(control) in _poisoned:
        return nothing("off", ("poisoned",))
    try:
        seen, off, distinct = _markers(journal)
    except Exception:
        return nothing("unproven", ("markers",))
    if off:
        return nothing("off", ("marked",))
    lost = "off" if seen else "unproven"
    try:
        info = os.lstat(Path(control) / FILE)
    except FileNotFoundError:
        return nothing(lost, ("absent",))
    except Exception:
        return nothing(lost, ("unreadable",))
    if not _private(info):
        return nothing(lost, ("not_private",))
    key = (str(control), info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns, info.st_size, info.st_mode,
           info.st_uid, info.st_nlink, journal.owner, journal.trial)
    cached = _cache.get(str(control))
    if cached is None or cached[0] != key:
        payload, state = load(control, journal)
        if len(_cache) >= 64:
            _cache.clear()
        _cache[str(control)] = (key, payload, state)
        cached = _cache[str(control)]
    _, payload, file_state = cached
    if payload is None:
        return nothing(lost, file_state)
    if not _complete(payload, distinct):
        return nothing("off", ("incomplete",))
    status, proven, assumed, _ = _evaluate(payload, protected)
    return proven | assumed, status, (file_state, distinct), assumed


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
                "assumed": (counts or {}).get("assumed"),
                "t0Assumable": None if payload is None else payload["t0Assumable"],
                "anchor": None if payload is None else payload["anchor"] is not None,
                "anchorAttempts": None if payload is None else payload["anchorAttempts"],
                "reason": (counts or {}).get("reason") if (counts or {}).get("reason") in REASONS else "other"}
        key = str(control)
        if _observed.get(key) == line:
            return
        if key not in _observed and len(_observed) >= 64:
            _observed.clear()
        _observed[key] = line
        logging.getLogger("uvicorn.error").info("runner_stream_retired %s", json.dumps(line, sort_keys=True))
    except Exception:
        pass


# --- route 1: the operator's anchor (runner.stream_anchor; design r4.2) --------------------------------------------

def reserve_anchor(control, journal):
    """Before the anchor request: a valid, complete sidecar with no anchor, for an owner not turned off ->
    anchorAttempts += 1 written durably, returns the epoch to bind the answer to; otherwise returns a fixed reason and
    nothing is requested. Never raises (review r0: a lock failure is a reason, not an exception). The count is a record
    of operator runs, not a cap: the runner never requests."""
    try:
        with _lock, file_lock(control):
            seen, off, distinct = _markers(journal)
            if off or str(control) in _poisoned:
                return None, "off"
            payload, _ = load(control, journal)
            if payload is None:
                return None, "no_record"
            if not _complete(payload, distinct) or payload["disabled"]:
                return None, "off"
            if payload["anchor"] is not None:
                return None, "already_anchored"
            payload["anchorAttempts"] += 1
            try:
                _write(control, journal, payload)
            except Exception:
                return None, "record_write_failed"
            return payload["epoch"], None
    except Exception:
        return None, "lock_failed"


def record_anchor(control, journal, epoch, iat):
    """After an attested answer: write `anchor = {iat}` only if the sidecar is still the same epoch with no anchor (a
    sidecar recreated meanwhile - new T0 - never receives an anchor from the previous one) and the owner is not off.
    Returns a fixed result; never raises."""
    if type(iat) is not int:
        return "no_iat"
    try:
        with _lock, file_lock(control):
            _, off, _ = _markers(journal)
            if off or str(control) in _poisoned:
                return "off"
            payload, _ = load(control, journal)
            if payload is None:
                return "no_record"
            if payload["epoch"] != epoch:
                return "epoch_changed"
            if payload["anchor"] is not None:
                return "already_anchored"
            payload["anchor"] = {"iat": iat}
            try:
                _write(control, journal, payload)
            except Exception:
                return "record_write_failed"
            state, _, _, counts = _evaluate(payload, set())
            _log(control, state, payload, counts)
            return "anchored"
    except Exception:
        return "lock_failed"
