"""Trusted non-root init-container preparation; never runs model input."""
import os
from pathlib import Path


def prepare(root=Path('/state')):
    if os.getuid() != 10001 or os.getgid() != 10001 or root.is_symlink():
        raise RuntimeError('Private runner identity required')
    for name in ('workspace', 'shell-ipc'):
        path = root / name
        path.mkdir(mode=0o2770, exist_ok=True)
        if path.is_symlink() or not path.is_dir() or path.stat().st_uid != 10001 or path.stat().st_gid != 10001:
            raise RuntimeError('Unsafe shell directory')
        path.chmod(0o2770)


if __name__ == '__main__':
    prepare()
