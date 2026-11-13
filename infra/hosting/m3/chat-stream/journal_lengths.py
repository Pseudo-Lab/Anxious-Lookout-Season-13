"""#90 L measurement (read only): the length distribution of the redaction journal literals of one runner control
volume, never a literal, digest, filter or MAC.

L (the longest recorded literal) sets the stream holdback (design r3 B1): text arrives L-1 chars late. Input is the
control directory of an access-service runner (`service-access.sqlite3`, backend/runner/service_store.py LEDGER).

Read only: the directory and file are checked with lstat (regular, not a symlink) before any open; the database is
opened as `file:...?mode=ro&immutable=1` (no lock, no journal, nothing written). A hot `-journal` sidecar is refused:
immutable mode would read a half-written state. Output is one JSON line. Exit codes: 0 measured, 2 refused input,
3 database error. Usage: python -I journal_lengths.py /control
"""
import json
import os
import sqlite3
import stat
import sys
from urllib.parse import quote

LEDGER = "service-access.sqlite3"


def refuse(reason):
    print(json.dumps({"ok": False, "reason": reason}, sort_keys=True))
    sys.exit(2)


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
        finally:
            connection.close()
    except sqlite3.Error:
        print(json.dumps({"ok": False, "reason": "database_error"}, sort_keys=True))
        sys.exit(3)
    lengths = {str(length): count for length, count in rows}
    return {"ok": True, "literals": sum(lengths.values()), "L": max((length for length, _ in rows), default=0),
            "holdbackChars": max(max((length for length, _ in rows), default=1) - 1, 0), "lengths": lengths}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        refuse("usage")
    print(json.dumps(measure(sys.argv[1]), sort_keys=True))
