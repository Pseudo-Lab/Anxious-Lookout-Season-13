#!/usr/bin/env python3
"""Stub findmnt / losetup / umount for test_issue66.sh (tool name as the first argument).
/mock/mounts: 'TARGET SOURCE FSTYPE' lines; /mock/loops: 'FILE DEVICE' lines. umount autoclears the loop."""
import os
import sys
from pathlib import Path

M = Path('/mock')
tool, args = sys.argv[1], sys.argv[2:]


def lines(name):
    p = M / name
    return [l.split() for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def save(name, rows):
    (M / name).write_text(''.join(' '.join(r) + '\n' for r in rows))


mounts, loops = lines('mounts'), lines('loops')
if tool == 'findmnt' and os.environ.get('MOCK_FINDMNT_FAIL'):
    sys.exit(1)
if tool == 'findmnt':
    columns = args[args.index('-o') + 1].split(',') if '-o' in args else ['TARGET', 'SOURCE', 'FSTYPE']
    rows = mounts
    if '-M' in args:
        rows = [m for m in mounts if m[0] == args[args.index('-M') + 1]]
        if not rows:
            sys.exit(1)
    for m in rows:
        print(' '.join(dict(zip(('TARGET', 'SOURCE', 'FSTYPE'), m))[c] for c in columns))
    sys.exit(0)
if tool == 'losetup':
    assert args[:3] == ['-n', '-O', 'NAME'] and args[3] == '-j'
    counter = M / 'losetup.calls'
    calls = int(counter.read_text()) + 1 if counter.exists() else 1
    counter.write_text(str(calls))
    # MOCK_LOSETUP_FAIL: every call fails; MOCK_LOSETUP_FAIL_FROM=N: the Nth call on fails; MOCK_LOSETUP_EMPTY: rc 0, no rows.
    if os.environ.get('MOCK_LOSETUP_FAIL') or calls >= int(os.environ.get('MOCK_LOSETUP_FAIL_FROM') or 10**9):
        sys.stderr.write('losetup: cannot get loop info\n'); sys.exit(1)
    if os.environ.get('MOCK_LOSETUP_EMPTY'):
        sys.exit(0)
    for f, dev in loops:
        if f == args[4]:
            print(dev)
    sys.exit(0)
if tool == 'umount':
    target = args[0]
    if os.environ.get('MOCK_UMOUNT_BUSY') == target:
        sys.stderr.write('umount: target is busy\n')
        sys.exit(32)
    gone = [m for m in mounts if m[0] == target]
    if not gone:
        sys.exit(32)
    save('mounts', [m for m in mounts if m[0] != target])
    if not os.environ.get('MOCK_LOOP_STAYS'):
        save('loops', [l for l in loops if l[1] not in {m[1] for m in gone}])
    sys.exit(0)
sys.exit(99)
