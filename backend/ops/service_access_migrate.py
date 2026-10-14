"""Explicit offline conversion of service-v2 JSON; never a trial migration.

Original files remain byte-for-byte unchanged. This one-time reader may require
memory proportional to the old JSON; the runtime uses indexed SQLite instead.
Stop the runner/native/supplier first; locks alone do not prove child termination.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path

from app.codex_policy import CodexFailure, REASONS
from runner.auth import read_private
from runner.binding import canonical_uuid
from runner.projection import ProjectionJournal
from runner.service_access import ServiceAccessAuth, MODE, LEGACY, LEDGER, is_fingerprint
from runner.service_store import private_file, refuse

OLD_FIELDS = {'version', 'mode', 'owner', 'stateId', 'accountId', 'trialId', 'requests', 'snapshots',
              'snapshotId', 'snapshotFingerprint', 'rejectedFingerprints', 'deadline', 'blocked', 'modelBlocked'}


def original(path):
    descriptor = private_file(path)
    with os.fdopen(descriptor, 'rb') as source:
        before = os.fstat(source.fileno())
        data = source.read()
        after = os.fstat(source.fileno())
        current = path.lstat()
        refuse((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
               == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
               == (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns))
    try:
        return json.loads(data), hashlib.sha256(data).hexdigest()
    except (ValueError, UnicodeError):
        raise CodexFailure('policy_refused') from None


def migrate(auth):
    refuse(not any((auth.control / name).exists() or (auth.control / name).is_symlink()
                   for name in ('access-ledger.json', 'grant.json', 'ledger.json')))
    descriptor = private_file(auth.control / 'lease.lock', create=True)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        old, source_hash = original(auth.control / LEGACY)
        projection, projection_hash = original(auth.control / 'access-projection.json')
        refuse(isinstance(old, dict) and set(old) == OLD_FIELDS and type(old['version']) is int
               and old['version'] == 2 and old['mode'] == MODE and old['owner'] == auth.owner
               and old['stateId'] == auth.state_id and old['accountId'] == auth.expected_account)
        canonical_uuid(old['trialId']); canonical_uuid(old['snapshotId'])
        refuse(type(old['deadline']) is int and is_fingerprint(old['snapshotFingerprint'])
               and old['blocked'] in ({None} | REASONS) and type(old['modelBlocked']) is bool)
        refuse(isinstance(old['requests'], list) and isinstance(old['snapshots'], dict)
               and isinstance(old['rejectedFingerprints'], list))
        for receipt in old['requests']:
            refuse(isinstance(receipt, dict) and set(receipt) == {'id'})
            canonical_uuid(receipt['id'])
        for identity, digest in old['snapshots'].items():
            canonical_uuid(identity); refuse(is_fingerprint(digest))
        refuse(old['snapshots'].get(old['snapshotId']) == old['snapshotFingerprint']
               and all(is_fingerprint(digest) for digest in old['rejectedFingerprints']))
        journal = ProjectionJournal(projection, auth.owner, old['trialId'])
        # Idempotent re-run proves the unchanged retained input hashes, not a
        # license to reimport old data over a later service state.
        if (auth.control / LEDGER).exists():
            with auth.store.transaction() as db:
                auth.audit(db)
                refuse(db.execute('SELECT sourceHash,projectionHash FROM migration WHERE id=1').fetchone()
                       == (source_hash, projection_hash))
            return
        with auth.store.transaction(create=True, migration=True) as db:
            db.executemany('INSERT INTO requests VALUES(?)', ((item['id'],) for item in old['requests']))
            db.executemany('INSERT INTO snapshots VALUES(?,?)', old['snapshots'].items())
            db.executemany('INSERT INTO rejected VALUES(?)', ((digest,) for digest in old['rejectedFingerprints']))
            value = {key: item for key, item in old.items() if key not in {'snapshots', 'rejectedFingerprints'}}
            value.update(version=3, requests=len(old['requests']), projectionKey=journal.key.hex(), nativeAttached=True)
            auth._save(db, value)
            from runner.service_access import ServiceJournal
            destination = ServiceJournal(auth, value)
            for length, groups in journal.values.items():
                for filter_hash, digests in groups.items():
                    for digest in digests:
                        db.execute('INSERT INTO literals VALUES(?,?,?,?)',
                                   (length, str(filter_hash), digest, destination.row_mac(length, filter_hash, digest)))
            db.execute('INSERT INTO migration VALUES(1,?,?)', (source_hash, projection_hash))
            auth.audit(db)
    except (OSError, TypeError, ValueError, KeyError):
        raise CodexFailure('policy_refused') from None
    finally:
        os.close(descriptor)


def main():
    import argparse
    import sys
    parser = argparse.ArgumentParser(description='Offline service-v2 JSON conversion; preserve originals, no credentials/provider calls')
    parser.add_argument('--control', required=True)
    parser.add_argument('--owner', required=True)
    parser.add_argument('--state-id', required=True)
    parser.add_argument('--account-file', required=True)
    parser.add_argument('--check', action='store_true', help='Validate existing SQLite state instead of migrating')
    args = parser.parse_args()
    try:
        account = read_private(Path(args.account_file), json_format=False).strip()
        auth = ServiceAccessAuth(Path(args.control), args.owner, args.state_id, account)
        if args.check:
            descriptor = private_file(auth.control / 'lease.lock', create=True)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with auth.store.transaction() as db:
                    auth.audit(db)
            finally:
                os.close(descriptor)
        else:
            migrate(auth)
    except Exception:
        print('Service metadata conversion refused; input values are not printed', file=sys.stderr)
        return 1
    print('PASS service metadata check/conversion; originals retained; no provider authentication proof')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
