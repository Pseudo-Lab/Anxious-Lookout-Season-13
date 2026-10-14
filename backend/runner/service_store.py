"""Private service metadata and indexed lifetime history, never credentials.

All writers share a separate flock, including initial publication/migration.
SQLite DELETE journals + FULL synchronization retain committed receipts across
process death. A fixed metadata row replaces unbounded JSON history arrays.
"""
from contextlib import contextmanager
import fcntl
import os
import sqlite3
import stat
import uuid

from app.codex_policy import CodexFailure
from .auth import private_directory

LEDGER = 'service-access.sqlite3'
LEGACY = 'service-access-ledger.json'
SCHEMA = (
    'CREATE TABLE metadata (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL) STRICT',
    'CREATE TABLE requests (id TEXT PRIMARY KEY) STRICT',
    'CREATE TABLE snapshots (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL) STRICT',
    'CREATE INDEX snapshot_fingerprint ON snapshots(fingerprint)',
    'CREATE TABLE rejected (fingerprint TEXT PRIMARY KEY) STRICT',
    'CREATE TABLE migration (id INTEGER PRIMARY KEY CHECK(id=1), sourceHash TEXT NOT NULL, projectionHash TEXT NOT NULL) STRICT',
    'CREATE TABLE literals (length INTEGER NOT NULL CHECK(length BETWEEN 1 AND 65536), filter TEXT NOT NULL, digest TEXT NOT NULL, mac TEXT NOT NULL, PRIMARY KEY(length,filter,digest)) STRICT',
)


def refuse(condition):
    if not condition:
        raise CodexFailure('policy_refused')


def private_file(path, create=False):
    flags = os.O_RDWR | os.O_NOFOLLOW
    if create:
        flags |= os.O_CREAT
    descriptor = os.open(path, flags, 0o600)
    try:
        info = os.fstat(descriptor)
        refuse(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
               and info.st_nlink == 1 and info.st_mode & 0o077 == 0
               and (info.st_dev, info.st_ino) == (path.lstat().st_dev, path.lstat().st_ino))
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def sync_directory(path):
    descriptor = os.open(path, os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class ServiceStore:
    def __init__(self, control):
        self.control = control

    @contextmanager
    def transaction(self, create=False, migration=False):
        """No persistent connection: hot journals are recovered on reopen.

        A crash before first atomic rename leaves only an unpublished staging DB;
        it is never adopted as a ledger. Existing databases commit in place.
        """
        lock = connection = None
        staging = None
        try:
            private_directory(self.control)
            lock = private_file(self.control / 'service-access.lock', create=True)
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = self.control / LEDGER
            fresh = not path.exists() and not path.is_symlink()
            if fresh:
                refuse(create)
                if not migration:
                    refuse(not any((self.control / name).exists() or (self.control / name).is_symlink()
                                   for name in (LEGACY, 'access-ledger.json', 'access-projection.json',
                                                'grant.json', 'ledger.json', 'projection.json')))
                staging = self.control / ('.service-stage-' + uuid.uuid4().hex)
                path = staging
                descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                os.close(descriptor)
            else:
                refuse(not migration)
            descriptor = private_file(path)
            os.close(descriptor)
            for suffix in ('-journal', '-wal', '-shm'):
                sidecar = path.with_name(path.name + suffix)
                if sidecar.exists() or sidecar.is_symlink():
                    # This format never uses WAL. Do not discard/reconfigure an
                    # unknown WAL database or follow a hostile journal link.
                    refuse(suffix == '-journal')
                    descriptor = private_file(sidecar)
                    os.close(descriptor)
            connection = sqlite3.connect(str(path), isolation_level=None, timeout=30)
            connection.execute('PRAGMA trusted_schema=OFF')
            refuse(connection.execute('PRAGMA journal_mode').fetchone()[0] == 'delete')
            connection.execute('PRAGMA synchronous=FULL')
            connection.execute('BEGIN IMMEDIATE')
            if fresh:
                for statement in SCHEMA:
                    connection.execute(statement)
                connection.execute('PRAGMA user_version=3')
            else:
                refuse(connection.execute('PRAGMA user_version').fetchone()[0] == 3)
                actual = {row[0] for row in connection.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL")}
                refuse(actual == set(SCHEMA))
            yield connection
            connection.execute('COMMIT')
            connection.close()
            connection = None
            if staging:
                # Lock excludes other compliant creators; never overwrite an
                # unexpectedly published database.
                refuse(not (self.control / LEDGER).exists() and not (self.control / LEDGER).is_symlink())
                os.rename(staging, self.control / LEDGER)
                staging = None
                sync_directory(self.control)
        except (OSError, sqlite3.Error):
            raise CodexFailure('policy_refused') from None
        finally:
            if connection:
                connection.close()  # Rolls back an unfinished transaction.
            if staging:
                for suffix in ('', '-journal'):
                    staging.with_name(staging.name + suffix).unlink(missing_ok=True)
            if lock is not None:
                os.close(lock)
