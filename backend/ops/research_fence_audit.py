"""Read-only fence audit for verified, quiescent owner/state/control PVC pairs.

Run in the candidate image with native/control PVCs mounted read-only. Never
imports Native, starts Codex, creates locks, settles, deletes or repairs files.
Unknown/missing binding evidence is not a zero-fence result.
"""
import argparse
from contextlib import closing
import fcntl
import os
from pathlib import Path
import sqlite3
import stat
from urllib.parse import quote

from runner.auth import private_directory, read_private
from runner.binding import require_binding
from runner.research_authority import AuthorityError, canonical, strict_json
from runner.research_fence import canonical_uuid, validate
from runner.service_access import MODE
from runner.service_store import SCHEMA


def read_only_file(path, limit):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600 or (limit is not None and info.st_size > limit)):
            raise AuthorityError('audit_file')
        return os.read(descriptor, limit + 1) if limit is not None else b''
    finally:
        os.close(descriptor)


def control_binding(control, owner, state):
    # No ServiceStore.transaction: that would create a lock or recover/migrate.
    path = control / 'service-access.sqlite3'
    read_only_file(path, None)
    if any(os.path.lexists(path.with_name(path.name + suffix)) for suffix in ('-journal', '-wal', '-shm')):
        raise AuthorityError('audit_unsettled_ledger')
    with closing(sqlite3.connect('file:' + quote(str(path), safe='/') + '?mode=ro',
                                uri=True, timeout=0, isolation_level=None)) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA trusted_schema=OFF')
        if db.execute('PRAGMA user_version').fetchone()[0] != 3:
            raise AuthorityError('audit_control_binding')
        actual = {row[0] for row in db.execute('SELECT sql FROM sqlite_master WHERE sql IS NOT NULL')}
        rows = db.execute('SELECT payload FROM metadata').fetchmany(2)
        if actual != set(SCHEMA) or len(rows) != 1 or len(rows[0][0]) > 4096:
            raise AuthorityError('audit_control_binding')
        value = strict_json(rows[0][0])
        if (not isinstance(value, dict) or value.get('owner') != owner or value.get('stateId') != state
                or type(value.get('version')) is not int or value['version'] != 3 or value.get('mode') != MODE):
            raise AuthorityError('audit_control_binding')


def audit(native, control, inputs):
    expected = {'ownerId', 'stateId', 'nativePvcUid', 'controlPvcUid', 'writersQuiescent'}
    result = {'status': 'unknown', 'bindingValidated': False}
    try:
        if not isinstance(inputs, dict) or set(inputs) != expected or inputs['writersQuiescent'] is not True:
            raise AuthorityError('audit_inventory')
        for key in expected - {'writersQuiescent'}:
            canonical_uuid(inputs[key])
        result.update({key: inputs[key] for key in expected - {'writersQuiescent'}})
        control = private_directory(Path(control))
        require_binding(Path(native), inputs['ownerId'], inputs['stateId'])
        control_binding(control, inputs['ownerId'], inputs['stateId'])
        # External inventory/quiescence is mandatory even if this lock is absent
        # (e.g. an older runner never created a fence). Never create it in audit.
        lock, path = None, control / 'research-fence.lock'
        try:
            if os.path.lexists(path):
                read_only_file(path, 0)
                lock = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            path = control / 'research-fence.json'
            if not os.path.lexists(path):
                result.update(status='absent', bindingValidated=True)
            else:
                value = validate(strict_json(read_only_file(path, 8192)), inputs['ownerId'], inputs['stateId'])
                result.update(status='unsettled' if value['phase'] == 'execute_intent' else 'terminal',
                              bindingValidated=True, outcome=value['outcome'])
        finally:
            if lock is not None:
                os.close(lock)
    except Exception:
        result.update(status='unknown', bindingValidated=False)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native', required=True)
    parser.add_argument('--control', required=True)
    parser.add_argument('--inputs', required=True, help='Private verified inventory JSON; no credentials')
    args = parser.parse_args()
    try:
        result = audit(args.native, args.control, read_private(Path(args.inputs)))
    except Exception:
        result = {'status': 'unknown', 'bindingValidated': False}
    print(canonical(result).decode())
    return 0 if result['status'] in {'absent', 'terminal'} and result['bindingValidated'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
