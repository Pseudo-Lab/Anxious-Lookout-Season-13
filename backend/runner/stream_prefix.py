"""#90 design r4 (B1 prefix optimization): the stream holdback's per-literal prefix fingerprints.

The projection journal (formats unchanged) keeps each literal's length and keyed digest. This module keeps, in a
separate control-directory file `stream-prefixes.json`, for every literal longer than P its first-P-chars fingerprint
(`HMAC(key, "stream-prefix-v1\\0" + value[:P])`, plus the rolling filter as a search hint) next to the literal's
length and journal digest. A fingerprint is made only where the plaintext is in memory (the auth modes' remember
paths, after the journal write). The file is MAC'd with the same key; a missing, unreadable, malformed or foreign file
counts as no fingerprint at all (every long literal is then "legacy": the r3 holdback). A failed fingerprint write
never fails the caller: that literal stays legacy (safe, slower). A rolled-back image ignores the file.

`policy()` builds the holdback for one plan (design r4 §3-§4; r4.1): for an unemitted window of n chars the cut is
the minimum of
  n - (L_short - 1)  when a literal of length <= P exists (L_short its longest),
  n - (P - 1)        when a covered long literal exists (its whole P-char prefix must be visible),
  n - (L_legacy - 1) when a legacy long literal exists,
  the smallest a (a <= n - P) whose P chars carry a covered prefix fingerprint and a + lmax(prefix) > n.
The caller keeps r3's left-only rule (a detected span crossing the cut pulls it back to the span's start).
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

from .projection import BASE, MASK, rolling

P = 16
FILE = "stream-prefixes.json"
LIMIT = 4096
VERSION = 1
FIELDS = {"version", "owner", "trialId", "prefixLength", "entries", "mac"}
RESULTS = {"written", "unchanged", "full", "failed", "no_control"}  # #94 (8) runner_stream_prefix results
_write_lock = threading.Lock()


def prefix_digest(key, text):
    return hmac.new(key, b"stream-prefix-v1\0" + text.encode("utf-8"), hashlib.sha256).hexdigest()


def file_mac(key, payload):
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hmac.new(key, b"stream-prefixes-v1\0" + body, hashlib.sha256).hexdigest()


def entry(journal, value):
    head = value[:P]
    return (rolling(head), prefix_digest(journal.key, head), len(value), journal.digest(value))


def _hex64(value):
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _private(info):
    return (stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and not info.st_mode & 0o077
            and info.st_nlink == 1 and info.st_size <= 2_000_000)


def _read(path):
    """(bytes, state) of a private regular file: lstat type first, then O_NOFOLLOW | O_NONBLOCK and fstat."""
    if not _private(os.lstat(path)):
        raise ValueError()
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not _private(info):
            raise ValueError()
        chunks, size = [], 0
        while True:
            chunk = os.read(descriptor, 1 << 16)
            if not chunk:
                break
            size += len(chunk)
            if size > 2_000_000:
                raise ValueError()
            chunks.append(chunk)
        return b"".join(chunks), (info.st_ino, info.st_mtime_ns, info.st_size)
    finally:
        os.close(descriptor)


def _valid(payload, journal):
    if not (isinstance(payload, dict) and set(payload) == FIELDS and payload["version"] == VERSION
            and payload["owner"] == journal.owner and payload["trialId"] == journal.trial
            and type(payload["prefixLength"]) is int and payload["prefixLength"] == P
            and isinstance(payload["entries"], list) and len(payload["entries"]) <= LIMIT
            and isinstance(payload["mac"], str)):
        return None
    entries = set()
    for item in payload["entries"]:
        if not (isinstance(item, list) and len(item) == 4):
            return None
        prefix_filter, digest, length, literal = item
        if not (type(prefix_filter) is int and 0 <= prefix_filter <= MASK and _hex64(digest)
                and type(length) is int and P < length <= 65536 and _hex64(literal)):
            return None
        entries.add((prefix_filter, digest, length, literal))
    if not hmac.compare_digest(file_mac(journal.key, {k: v for k, v in payload.items() if k != "mac"}), payload["mac"]):
        return None
    return frozenset(entries)


def load(control, journal):
    """(entries or None, file state). None = no usable fingerprint file: every long literal is legacy."""
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


def _write(path, payload):
    temporary = path.parent / (".stream-prefixes-" + uuid.uuid4().hex)
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


def remember(control, journal, literals):
    """Add the fingerprints of `literals` (plaintext in memory) after the journal write. Never raises; True when the
    file was written. Content already present is a no-op (no rewrite, no generation change, design r4.1). A file that
    is missing or invalid is rewritten with the current literals only (the others stay legacy).

    #94 (8): the outcome is observable (`observe`, the `runner_stream_prefix` line): the result from a fixed list and
    how many long literals of this call are covered by the file this call wrote or found valid. Counts only; never a
    value, digest or path. review r2: the literals of a call are what the caller passes - in the personal / access
    modes the cumulative `secrets` (credentials rotated out in this process included), in the service mode the
    installed credential - so this line is the coverage of that set, **not** of the latest credential; the latest
    credential's state is the summary split (`policy`, `legacy_current`). review r1: `remember` also runs on the stream's hot path (every refresh / sanitize), so the
    counts come from the sets already in hand (no extra read) and a line is logged only when the observed state of
    this control directory changes - a Pod whose credential and file stay the same logs a constant number of lines,
    whatever the number of deltas or sanitize calls (PM)."""
    if control is None or journal is None:
        with _write_lock:
            observe(control, "no_control", 0, None, None)
        return False
    with _write_lock:
        new, known, merged, valid = None, set(), None, False
        status = "failed"
        try:
            new = {entry(journal, value) for value in literals
                   if isinstance(value, str) and P < len(value) <= 65536}
            current, _ = load(control, journal)
            valid, known = current is not None, set(current or ())
            if valid and new <= known:
                status = "unchanged"
                return False
            room = max(LIMIT - len(known), 0)
            merged = known | set(sorted(new - known)[:room])  # over the limit: the rest stay legacy
            if valid and merged == known:
                # A valid file that would not change (full: the new literals stay legacy) is never rewritten, so the
                # plan's generation stays stable and the stream progresses on the legacy holdback (review 732e278).
                status = "full"
                return False
            payload = {"version": VERSION, "owner": journal.owner, "trialId": journal.trial, "prefixLength": P,
                       "entries": sorted([list(item) for item in merged])}
            payload["mac"] = file_mac(journal.key, payload)
            _write(Path(control) / FILE, payload)
            status = "written"
            return True
        except Exception:
            status = "failed"
            return False
        finally:
            if new is None:
                observe(control, status, None, None, None)
            else:
                held = merged if status == "written" else known if valid else None
                observe(control, status, len(new), len(new & held) if held is not None else 0,
                        len(held) if held is not None else None)


_observed = {}  # str(control) -> the last logged state (bounded; see observe)
OBSERVED_LIMIT = 64


def observe(control, status, literals, covered, entries):
    """#94 (8): the `runner_stream_prefix` log line, only when the state of `control` differs from the last one logged
    for it (a change of result, of the covered count or of the file's entry count; a failure and its recovery are both
    logged). `literals`: long literals of this call (the caller's set, possibly cumulative); `covered`: how many of them
    the file covers (None: unknown); `allCovered`: all of them (true / false / null) - review r2: an aggregate, never
    by itself a verdict on the latest credential; `entries`: the file's entry count (None: no valid file); `limit`:
    LIMIT. Called under _write_lock. Never raises."""
    try:
        state = (status if status in RESULTS else "other", literals, covered, entries)
        key = str(control)
        if _observed.get(key) == state:
            return
        if key not in _observed and len(_observed) >= OBSERVED_LIMIT:
            _observed.clear()  # bounded: an evicted directory logs its state once more, never more often
        _observed[key] = state
        logging.getLogger("uvicorn.error").info("runner_stream_prefix %s", json.dumps({
            "result": state[0], "literals": literals, "covered": covered,
            "allCovered": None if covered is None or literals is None else covered == literals,
            "entries": entries, "limit": LIMIT}, sort_keys=True))
    except Exception:
        pass


class HoldPolicy:
    """The cut start for one plan (see the module docstring). `none()` = no recorded literal: nothing is held."""

    def __init__(self, short=0, covered=False, legacy=0, groups=None, filters=None, key=None, state=None,
                 legacy_current=0, legacy_retired=0):
        self.short, self.covered, self.legacy = short, covered, legacy
        self.groups, self.filters, self.key, self.state = groups or {}, filters or {}, key, state
        # #94 (8): where `legacy` comes from (observation only; `legacy` = max of the two). current: a long literal
        # of the credential in memory has no fingerprint; retired: only a journal row of an earlier credential has none.
        self.legacy_current, self.legacy_retired = legacy_current, legacy_retired
        self.retired_state, self.retired_excluded = "off", 0  # #95 B1 observation (set by policy)

    @classmethod
    def none(cls):
        return cls()

    def ident(self):
        return (self.short, self.covered, self.legacy, tuple(sorted(self.groups.items())), self.state)

    def __eq__(self, other):
        return isinstance(other, HoldPolicy) and self.ident() == other.ident()

    def __hash__(self):
        return hash(self.ident())

    def start(self, window):
        n = len(window)
        cut = n
        if self.short:
            cut = min(cut, n - (self.short - 1))
        if self.covered:
            cut = min(cut, n - (P - 1))
        if self.legacy:
            cut = min(cut, n - (self.legacy - 1))
        if self.groups and n >= P:
            factor = pow(BASE, P - 1, 1 << 64)
            current = rolling(window[:P])
            for a in range(0, n - P + 1):
                if a >= cut:
                    break
                candidates = self.filters.get(current)
                if candidates:
                    digest = prefix_digest(self.key, window[a:a + P])
                    if digest in candidates and a + self.groups[digest] > n:
                        cut = a
                        break
                if a + P < n:
                    current = ((current - ord(window[a]) * factor) * BASE + ord(window[a + P])) & MASK
        return max(cut, 0)


def policy(journal, control, current=(), latest=None, retired=None):
    """The HoldPolicy of `journal` (verified rows) and the fingerprint file in `control`. Raises when the journal
    cannot be read (the caller stops text); a fingerprint problem only makes literals legacy. `current`: the secrets
    in memory (refresh writes them to the journal first; any not covered still counts, fail-safe).

    #94 (8) observation only: `latest` is the literal set of the credential installed last (the auth's
    `latest_secrets`); `current` may also hold literals of credentials rotated out in this process (review r1). The
    split names a long literal of `latest` without a fingerprint `legacy_current` and every other uncovered long literal
    `legacy_retired`. The holdback (`legacy`, the cut) is computed exactly as before from rows and `current`; with no
    or an empty `latest`, the split treats all of `current` as the latest credential (an over-report of "current",
    never of "retired").

    #95 B1: `retired` = (rows, state, file state) from stream_retired.excluded - journal rows proven to be retired,
    expired service access tokens. They leave the `legacy` length only: the journal, the spans (redaction) and every
    literal of `current` / `latest` are untouched, whatever `retired` says. The file state joins the policy state, so
    a change of the record discards an in-flight plan."""
    short, rows = journal.stream_rows(P)
    entries, state = load(control, journal) if control is not None else (None, ("no-control",))
    covered_entries = {item for item in (entries or ()) if (item[2], item[3]) in rows}
    covered_rows = {(item[2], item[3]) for item in covered_entries}
    protected = set()
    for value in list(current) + list(latest or ()):
        if isinstance(value, str) and len(value) > P:
            protected.add((len(value), journal.digest(value)))
    dropped = frozenset()
    retired_state, retired_file = "off", None
    if retired is not None and retired[1] == "off":
        retired = None  # the switch off: exactly the pre-#95 policy (same identity)
    if retired is not None:
        dropped = frozenset(row for row in retired[0] if row in rows and row not in protected)
        retired_state, retired_file = retired[1], retired[2]
    kept = rows - covered_rows - dropped
    legacy = max((length for length, _ in kept), default=0)
    uncovered = set()
    for value in current:
        if not isinstance(value, str) or not value:
            continue
        if len(value) <= P:
            short = max(short, len(value))
        elif (len(value), journal.digest(value)) not in covered_rows:
            legacy = max(legacy, len(value))
            uncovered.add((len(value), journal.digest(value)))
    split = latest if latest else current  # none or empty: no information, count `current` as current (see above)
    latest_rows = {(len(value), journal.digest(value)) for value in split
                   if isinstance(value, str) and len(value) > P}
    legacy_current = max((length for length, _ in latest_rows - covered_rows), default=0)
    legacy_retired = max((row[0] for row in kept | uncovered if row not in latest_rows), default=0)
    groups, filters = {}, {}
    for prefix_filter, digest, length, _ in covered_entries:
        groups[digest] = max(groups.get(digest, 0), length)
        filters.setdefault(prefix_filter, set()).add(digest)
    hold = HoldPolicy(short, bool(covered_entries), legacy, groups, filters, journal.key,
                      state if retired is None else (state, retired_file, retired_state),
                      legacy_current, legacy_retired)
    hold.retired_state = retired_state
    hold.retired_excluded = len(dropped - covered_rows)  # rows that would have held text without #95
    return hold
