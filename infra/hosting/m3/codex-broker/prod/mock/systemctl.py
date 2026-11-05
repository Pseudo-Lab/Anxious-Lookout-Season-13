#!/usr/bin/env python3
"""Stub systemctl for test_steps.sh: unit state in /mock/unit.<name> as 'Load Active UnitFile'."""
import os
import sys
import time
from pathlib import Path

M = Path('/mock')
args = sys.argv[1:]
names = ('LoadState', 'ActiveState', 'UnitFileState')


def state(u):
    p = M / f'unit.{u}'
    return p.read_text().split() if p.exists() else ['not-found', 'inactive', '']


if os.environ.get('MOCK_SYSTEMCTL_FAIL'):
    sys.exit(1)
if args[0] == 'show':
    if os.environ.get('MOCK_SYSTEMCTL_DELAY'):  # a slow but successful lookup
        time.sleep(float(os.environ['MOCK_SYSTEMCTL_DELAY']))
    props = [args[i + 1] for i, a in enumerate(args) if a == '-p']
    unit = [a for a in args[1:] if not a.startswith('-') and a not in props][0]
    s = dict(zip(names, state(unit)))
    for p in props:
        print(s[p] if '--value' in args else f'{p}={s[p]}')
    # Review round 1 probe: correct-looking stdout, then a failure status.
    sys.exit(1 if os.environ.get('MOCK_SYSTEMCTL_PARTIAL') else 0)
if args[0] == 'disable':
    if os.environ.get('MOCK_DISABLE_FAIL'):
        sys.exit(1)
    for u in [a for a in args[1:] if not a.startswith('-')]:
        s = state(u)
        if u.endswith('.service'):  # a failed oneshot stays failed; disable does not reset it
            active = 'failed' if s[1] == 'failed' else 'inactive'
            (M / f'unit.{u}').write_text(f'{s[0]} {active} {s[2] if s[2] in ("disabled", "static") else "disabled"}')
        else:
            (M / f'unit.{u}').write_text(f'{s[0]} inactive disabled')
    sys.exit(0)
if args[0] == 'daemon-reload':
    for u in ('issue16-supply.timer', 'issue16-supply.service'):
        if not Path('/etc/systemd/system', u).exists():
            (M / f'unit.{u}').write_text('not-found inactive -')
    sys.exit(0)
sys.exit(99)
