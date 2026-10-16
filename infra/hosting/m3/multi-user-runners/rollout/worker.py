"""Reviewed bind-mounted wrapper for the already pinned #16 provisioner image."""
import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import stat
import sys
from uuid import UUID

ROOT = Path('/var/lib/anxious-lookout/issue16-d4/provision-output')
STORAGE = Path('/var/lib/anxious-lookout/user-runners')


def require(ok):
    if not ok:
        raise ValueError('Host wrapper refused')


def manifest(path):
    require(path == path.resolve() and stat.S_ISREG(path.lstat().st_mode))
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as source:
        s = os.fstat(source.fileno())
        require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o077
                and s.st_nlink == 1 and s.st_size <= 65536)
        return json.load(source)


def restore_inventory(root=ROOT, storage=STORAGE):
    # Complete durable preparation inventories, never a glob of arbitrary image
    # paths. Refuse partial/orphan storage instead of starting k3s over bare dirs.
    for path in (root, storage):
        require(path == path.resolve() and path.is_dir() and path.stat().st_uid == 0
                and not path.stat().st_mode & 0o077)
    states = set()
    for output in root.iterdir():
        if output.name.startswith('.lock-'):
            require(UUID(hex=output.name[6:]).hex == output.name[6:]
                    and output.is_file() and not output.is_symlink())
            continue
        require(UUID(hex=output.name).hex == output.name and output.is_dir() and output == output.resolve()
                and output.stat().st_uid == 10001 and not output.stat().st_mode & 0o077)
        value = manifest(output/'storage-restore.json')
        require(set(value) == {'version', 'owner', 'state', 'path', 'layout'}
                and type(value['version']) is int and value['version'] == 1 and value['layout'] == 'bounded-ext4'
                and str(UUID(value['owner'])) == value['owner'] and UUID(value['owner']).hex == output.name
                and str(UUID(value['state'])) == value['state'])
        state = storage/UUID(value['state']).hex
        require(value['path'] == str(state) and state not in states)
        # A completed runtime is written before binding publication/Pod creation.
        # Its presence prevents adopting an interrupted preparation directory.
        from ops.runner_binding_import import retained_file
        runtime = json.loads(retained_file(output/'runtime.json'))
        require(runtime['plan']['owner'] == value['owner'] and runtime['plan']['state'] == value['state'])
        states.add(state)
    require(set(storage.iterdir()) == states)
    return sorted(states)


def run_batch(role, argv):
    modules = {'controller': 'ops.runner_provision_worker', 'observer': 'ops.runner_stop_observer',
               'supply': 'ops.runner_host_supply'}
    require(role in modules)
    sys.argv = [modules[role], *argv]
    output = io.StringIO()
    failed = False
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
        try:
            runpy.run_module(modules[role], run_name='__main__')
        except SystemExit as exc:
            failed = exc.code not in (None, 0)
        except Exception:
            failed = True
    for line in output.getvalue().splitlines():
        item = json.loads(line)
        require(set(item) == {'owner', 'stage', 'result'} and str(UUID(item['owner'])) == item['owner']
                and item['stage'] in {'provision', 'termination', 'host-supply'}
                and item['result'] in {'processed', 'observed', 'already_available', 'supplied', 'excluded',
                                       'reconciliation_required', 'auth_expired', 'supply_refused'})
        print(json.dumps(item))
        failed |= item['result'] in {'reconciliation_required', 'auth_expired', 'supply_refused'}
    return 1 if failed else 0


def main():
    try:
        require(os.geteuid() == 0)
        if sys.argv[1] == 'restore':
            require(len(sys.argv) == 2)
            from ops.runner_storage import restore
            for state in restore_inventory():
                restore(state)
            print('PASS bounded restore inventory; no formatting')
            return 0
        return run_batch(sys.argv[1], sys.argv[2:])
    except Exception:
        print('Host wrapper refused; preserve evidence and reconcile', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
