"""#90 L measurement (read only): the length distribution of the redaction journal literals of one runner control
volume, never a literal, digest, filter or MAC.

L (the longest recorded literal) sets the r3 stream holdback (text arrives L-1 chars late). Input is the control
directory of an access-service runner (`service-access.sqlite3`, backend/runner/service_store.py LEDGER).

v2 (design r4 / r4.1): also the `snapshots` row count and a **structural** covered / legacy split of the long
literals (length > P = 16, runner/stream_prefix.py) against `stream-prefixes.json`: a literal row counts as covered
when an entry of that file names its (length, journal digest). v2 r1 (review acb6c57 major): an entry is credited
only when the whole file has the runtime's public shape (stream_prefix.py `_valid`: exact top-level fields, version,
prefixLength = P, at most LIMIT entries, four-element entries, filter in [0, MASK], hex digests, P < length <= 65536);
any other shape is `malformed` and every long literal legacy. The file's MAC and its owner / trialId binding are **not**
verified (this tool never reads the key or the ledger metadata) and no value from it is printed; the runtime
`legacyL` (runner_stream_summary) is the authoritative answer (review r4 minor). `prefixFile` is absent / unreadable /
malformed / present.

Read only: the directory and files are checked with lstat (regular, not a symlink) before any open; the database is
opened as `file:...?mode=ro&immutable=1` (no lock, no journal, nothing written); the prefix file is read through an
O_NOFOLLOW | O_NONBLOCK descriptor after lstat. A hot `-journal` sidecar is refused: immutable mode would read a
half-written state. Output is one JSON line. Exit codes: 0 measured, 2 refused input, 3 database error.
Usage: python -I journal_lengths.py /control
"""
import json
import os
import sqlite3
import stat
import sys
from urllib.parse import quote

LEDGER = "service-access.sqlite3"
PREFIXES = "stream-prefixes.json"
P = 16  # runner/stream_prefix.py P, VERSION, FIELDS, LIMIT; projection.py MASK (this tool imports nothing from the runner)
VERSION = 1
FIELDS = {"version", "owner", "trialId", "prefixLength", "entries", "mac"}
LIMIT = 4096
MASK = (1 << 64) - 1
PREFIX_LIMIT = 2_000_000


def refuse(reason):
    print(json.dumps({"ok": False, "reason": reason}, sort_keys=True))
    sys.exit(2)


def prefix_entries(directory):
    """(state, {(length, digest)}) of the fingerprint file, structurally only (no MAC check)."""
    path = os.path.join(directory, PREFIXES)
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return "absent", set()
    except OSError:
        return "unreadable", set()
    if not stat.S_ISREG(info.st_mode) or info.st_size > PREFIX_LIMIT:
        return "unreadable", set()
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                return "unreadable", set()
            data = os.read(descriptor, PREFIX_LIMIT + 1)
        finally:
            os.close(descriptor)
    except OSError:
        return "unreadable", set()
    try:
        found = public_schema(json.loads(data))
    except Exception:
        found = None
    return ("malformed", set()) if found is None else ("present", found)


def hex64(value):
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def public_schema(payload):
    """{(length, digest)} when `payload` has the runtime's public shape (runner/stream_prefix.py `_valid`), else None.
    Not checked (they need the journal key or its metadata): the MAC value and owner / trialId equality."""
    if not (isinstance(payload, dict) and set(payload) == FIELDS and type(payload["version"]) is int
            and payload["version"] == VERSION and isinstance(payload["owner"], str) and payload["owner"]
            and isinstance(payload["trialId"], str) and payload["trialId"]
            and type(payload["prefixLength"]) is int and payload["prefixLength"] == P
            and isinstance(payload["entries"], list) and len(payload["entries"]) <= LIMIT and hex64(payload["mac"])):
        return None
    found = set()
    for item in payload["entries"]:
        if not (isinstance(item, list) and len(item) == 4):
            return None
        prefix_filter, digest, length, literal = item
        if not (type(prefix_filter) is int and 0 <= prefix_filter <= MASK and hex64(digest)
                and type(length) is int and P < length <= 65536 and hex64(literal)):
            return None
        found.add((length, literal))
    return found


def measure(directory):
    try:
        info = os.lstat(directory)
    except OSError:
        refuse("directory_missing")
    if not stat.S_ISDIR(info.st_mode):
        refuse("directory_not_a_directory")
    path = os.path.join(directory, LEDGER)
    try:
        info = os.lstat(path)
    except OSError:
        refuse("ledger_missing")
    if not stat.S_ISREG(info.st_mode):
        refuse("ledger_not_a_regular_file")
    for suffix in ("-journal", "-wal"):
        if os.path.lexists(path + suffix):
            refuse("ledger_has_" + suffix[1:])
    try:
        connection = sqlite3.connect("file:" + quote(path) + "?mode=ro&immutable=1", uri=True)
        try:
            rows = connection.execute("SELECT length, COUNT(*) FROM literals GROUP BY length ORDER BY length").fetchall()
            long_rows = connection.execute("SELECT length, digest FROM literals WHERE length > ?", (P,)).fetchall()
            snapshots = connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        finally:
            connection.close()
    except sqlite3.Error:
        print(json.dumps({"ok": False, "reason": "database_error"}, sort_keys=True))
        sys.exit(3)
    lengths = {str(length): count for length, count in rows}
    state, entries = prefix_entries(directory)
    covered, legacy = {}, {}
    for length, digest in long_rows:
        bucket = covered if (length, digest) in entries else legacy
        bucket[str(length)] = bucket.get(str(length), 0) + 1
    return {"ok": True, "literals": sum(lengths.values()), "L": max((length for length, _ in rows), default=0),
            "holdbackChars": max(max((length for length, _ in rows), default=1) - 1, 0), "lengths": lengths,
            "snapshots": snapshots, "prefixFile": state,
            "structural": {"macVerified": False, "covered": covered, "legacy": legacy,
                           "LLegacy": max((int(length) for length in legacy), default=0)}}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        refuse("usage")
    print(json.dumps(measure(sys.argv[1]), sort_keys=True))
