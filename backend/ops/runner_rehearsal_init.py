"""Fixed synthetic #10 binding initializer. No provider input or network operation."""
import argparse
import os
from pathlib import Path
import stat

from ops.runner_policy import OWNER, STATE
from runner.auth import private_directory, read_private
from runner.binding import initialize_binding, require_binding
from runner.lifecycle import require

SYNTHETIC_ACCOUNT = 'synthetic-provider-excluded-rehearsal'


def initialize(native, control):
    require(os.geteuid() == 10001 and os.getegid() == 10001)
    # Validate both before any mutation. Partial success is deliberately not
    # retryable: preserve evidence and require a new reviewed recovery decision.
    for path in (native, control):
        private_directory(path)
        require(stat.S_IMODE(path.stat().st_mode) == 0o700 and not any(path.iterdir()))
    require(native.resolve() != control.resolve())
    initialize_binding(native, OWNER, STATE)
    fd = os.open(control/'operator-account', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as target:
        target.write(SYNTHETIC_ACCOUNT)
        target.flush()
        os.fsync(target.fileno())
    directory = os.open(control, os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    verify(native, control)


def verify(native, control):
    require(os.geteuid() == 10001 and os.getegid() == 10001)
    for name in ('account-id', 'state-binding.json'):
        require(stat.S_ISREG((native/name).lstat().st_mode))
    require_binding(native, OWNER, STATE)
    # No FIFO/device read, even if a preparation path was damaged.
    target = private_directory(control)/'operator-account'
    require(stat.S_ISREG(target.lstat().st_mode))
    require(read_private(target, json_format=False) == SYNTHETIC_ACCOUNT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--initialize', action='store_true')
    group.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    try:
        (initialize if args.initialize else verify)(Path('/state'), Path('/control'))
    except Exception:
        parser.exit(1, 'Rehearsal binding refused; preserve state, no retry or cleanup\n')
    print('PASS synthetic owner/state binding and non-provider control marker; no auth/native operation')


if __name__ == '__main__':
    main()
