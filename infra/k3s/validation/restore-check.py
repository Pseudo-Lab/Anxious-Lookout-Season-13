"""Read-only archive/SQLite restore proof; run in a network-none disposable container.

Mount the archive as /backup.tar.gz:ro. No live host k3s volumes are mounted.
This extracts only state.db(+WAL) into container-local temporary storage.
It checks database integrity, expected schema and policy resources, not a full
replacement-server boot or a PostgreSQL/tenant-volume restore.
"""
import pathlib
import sqlite3
import tarfile
import tempfile

required = {
    "var/lib/rancher/k3s/server/db/state.db",
    "var/lib/rancher/k3s/server/token",
    "etc/rancher/k3s/config.yaml",
    "recovery/network-policies.yaml",
}
with tempfile.TemporaryDirectory() as temporary, tarfile.open("/backup.tar.gz", "r:gz") as archive:
    members = {member.name.removeprefix("./"): member for member in archive.getmembers()}
    if not required.issubset(members):
        raise RuntimeError("archive is missing required recovery files")
    if not any(name.startswith("var/lib/rancher/k3s/server/tls/") for name in members):
        raise RuntimeError("archive has no TLS recovery material")
    if not any(name.startswith("var/lib/rancher/k3s/server/cred/") for name in members):
        raise RuntimeError("archive has no credential recovery material")
    for suffix in ("", "-wal", "-shm"):
        name = "var/lib/rancher/k3s/server/db/state.db" + suffix
        if name not in members:
            continue
        member = members[name]
        if not member.isfile():
            raise RuntimeError("SQLite archive member must be a regular file")
        # Never tar.extract() arbitrary entries from a credential-bearing archive.
        with archive.extractfile(member) as source:
            pathlib.Path(temporary, "state.db" + suffix).write_bytes(source.read())
    connection = sqlite3.connect(str(pathlib.Path(temporary, "state.db")))
    result = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        raise RuntimeError("SQLite integrity check failed")
    # Kine is k3s SQLite's Kubernetes storage table. Print only non-secret counts.
    count = connection.execute("SELECT count(*) FROM kine").fetchone()[0]
    if not count:
        raise RuntimeError("restored Kine datastore is empty")
    for policy in ("anxious-lookout-tenant-boundary", "anxious-lookout-node-protection"):
        row = connection.execute(
            "SELECT deleted FROM kine WHERE name LIKE ? ORDER BY id DESC LIMIT 1",
            ("%/" + policy,),
        ).fetchone()
        if row is None or row[0]:
            raise RuntimeError("expected active network policy absent in restored DB: " + policy)
    print("PASS: SQLite integrity, nonempty Kine data, both active network policies and recovery inputs")
    connection.close()
