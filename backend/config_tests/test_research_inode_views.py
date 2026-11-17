"""Run the exact INNER against explicit filesystem-view doubles and real temp FDs.

No subprocess, Codex sandbox, namespace, socket, VM or credential is used. These
counterexamples test the decision logic, not the actual file-bind implementation.
"""
import builtins
import json
import os
from pathlib import Path
from types import SimpleNamespace
import time

import pytest

from ops.research_dev_probe import INNER

OLD = {'available': False, 'probeMarker': 'old'}
NEW = {'available': False, 'probeMarker': 'new'}
GRANT = '/state/research-api/turn.json'


def run_view(tmp_path, view):
    work, outside = tmp_path/'work', tmp_path/'outside'
    work.mkdir(); outside.mkdir()
    old, new, copy = (tmp_path/name for name in ('old.json', 'new.json', 'copy.json'))
    for path, value in ((old, OLD), (new, NEW), (copy, OLD)):
        path.write_text(json.dumps(value))
        path.chmod(0o400)
    for name in ('sibling', 'parent-canary', 'control'):
        (outside/name).write_text('private-canary')
        assert (outside/name).read_text() == 'private-canary'  # Trusted positive control.
    state = {'after': False}
    checks, reads, descriptors = [], [], []

    class WorkPath:
        def __init__(self, path): self.path = str(path)
        def __truediv__(self, child): return WorkPath(self.path + '/' + child)
        def __str__(self): return self.path
        def write_text(self, value): return (work/Path(self.path).name).write_text(value)
        def exists(self):
            assert self.path == '/state/workspace/advance'
            state['after'] = True  # Trusted parent completed the atomic replacement.
            return True

    def grant_path():
        return new if state['after'] and view == 'next-path' else old

    with (outside/'control').open('rb') as control:
        parent = str(os.getpid())
        parent_fd = '/proc/' + parent + '/fd/' + str(control.fileno())
        def open_view(path, mode='r', *args, **kwargs):
            path = str(path); reads.append(path)
            if path == GRANT:
                target = grant_path()
            elif path.startswith('/proc/self/fd/'):
                if view == 'self-fd-denied': raise PermissionError()
                if state['after'] and view in {'next-self-fd', 'copied-self-fd'}:
                    target = new if view == 'next-self-fd' else copy
                else:
                    target = path  # Real /proc alias for the already authorized temp FD.
            elif path == '/state/research-api/sibling' and view == 'sibling':
                target = outside/'sibling'
            elif path == '/state/research-api/parent-canary' and view == 'parent-canary':
                target = outside/'parent-canary'
            elif path == '/state/workspace/private-alias' and view == 'private-alias':
                target = outside/'sibling'
            elif path == parent_fd and view == 'parent-control-fd':
                target = path  # Deliberately expose the trusted process's real control FD.
            else:
                raise PermissionError()
            return builtins.open(target, mode, *args, **kwargs)

        def open_fd(path, flags):
            assert path == GRANT
            descriptor = os.open(old, flags)
            descriptors.append(descriptor)
            return descriptor

        def close_fd(descriptor):
            os.close(descriptor)
            descriptors.remove(descriptor)

        def listing(path):
            if path == '/state/research-api':
                if view == 'parent-denied': raise PermissionError()
                if view == 'parent-empty': return []
                if view == 'parent-leak': return ['turn.json', 'parent-canary']
                return ['turn.json']
            if path == '/proc/' + parent + '/fd' and view == 'parent-fd-list':
                return [str(control.fileno())]
            raise PermissionError()

        fake_os = SimpleNamespace(open=open_fd, read=os.read, fstat=os.fstat,
            stat=lambda path: os.stat(grant_path()) if path == GRANT else pytest.fail('Unexpected stat'),
            listdir=listing, lseek=os.lseek, close=close_fd, environ={}, O_RDONLY=os.O_RDONLY, O_NOFOLLOW=os.O_NOFOLLOW)
        fake_sys = SimpleNamespace(argv=['probe', parent, str(control.fileno())],
                                   exit=lambda status: (_ for _ in ()).throw(SystemExit(status)))
        def imports(name, *args, **kwargs):
            return {'os': fake_os, 'pathlib': SimpleNamespace(Path=WorkPath),
                    'sys': fake_sys, 'time': time, 'json': json}[name]
        scope = {'__builtins__': {**vars(builtins), '__import__': imports, 'open': open_view,
                                 'print': lambda value, **kwargs: checks.append(json.loads(value))}}
        try:
            with pytest.raises(SystemExit) as caught:
                exec(compile(INNER, '<exact-INNER>', 'exec'), scope)
            assert len(checks) == 1 and all(type(value) is bool for value in checks[0].values())
            assert parent_fd in reads  # External control-FD check is actually attempted.
            return caught.value.code, checks[0]
        finally:
            for descriptor in descriptors:
                try: os.close(descriptor)
                except OSError: pass  # INNER normally closed it already.


@pytest.mark.parametrize('view', ['allowed-only', 'self-fd-denied', 'parent-denied', 'parent-empty'])
def test_safe_old_inode_view_passes_with_or_without_self_fd_or_virtual_parent_listing(tmp_path, view):
    code, checks = run_view(tmp_path, view)
    assert code == 0 and all(checks.values())
    assert checks['proc-self-fd'] and checks['proc-self-fd-after']
    assert checks['old-fd'] and checks['old-path'] and checks['old-inode']


@pytest.mark.parametrize('view,failed', [
    ('next-self-fd', 'proc-self-fd-after'),
    ('copied-self-fd', 'proc-self-fd-after'),  # Old content on a different inode still fails.
    ('next-path', 'old-path'),
    ('parent-leak', 'parent-list'),
    ('sibling', 'sibling'),
    ('parent-canary', 'parent-canary'),
    ('private-alias', 'alias'),
    ('parent-control-fd', 'proc-control-fd'),
    ('parent-fd-list', 'proc-parent-fds'),
])
def test_next_inode_or_original_private_view_is_rejected(tmp_path, view, failed):
    code, checks = run_view(tmp_path, view)
    assert code == 1 and checks[failed] is False
