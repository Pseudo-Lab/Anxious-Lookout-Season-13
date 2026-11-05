#!/usr/bin/env python3
"""Stub systemctl for test_issue66.sh: /mock/unit.<name> holds 'LoadState ActiveState'."""
import os
import sys
from pathlib import Path

args = sys.argv[1:]
if args[0] != 'show' or os.environ.get('MOCK_SYSTEMCTL_FAIL'):
    sys.exit(1)
props = [args[i + 1] for i, a in enumerate(args) if a == '-p']
unit = [a for a in args[1:] if not a.startswith('-') and a not in props][0]
p = Path('/mock') / ('unit.' + unit)
state = dict(zip(('LoadState', 'ActiveState'), p.read_text().split() if p.exists() else ['not-found', 'inactive']))
for name in props:
    print(name + '=' + state[name])
