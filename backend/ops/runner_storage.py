"""New-owner bounded local filesystems. Privileged host helper, never API code.

No adoption, resizing, unmount or deletion. Failed partial allocation is retained
for explicit reconciliation. Host mount namespace/propagation is a rollout gate.
"""
import os
from pathlib import Path
import shutil
import subprocess
from uuid import UUID

from runner.lifecycle import require

BYTES = 1024 ** 3


def allocate(storage, *, run=subprocess.run):
    storage = Path(storage)
    require(os.geteuid() == 0 and storage.is_absolute() and storage == storage.resolve()
            and str(storage).startswith('/var/lib/anxious-lookout/'))
    require(UUID(hex=storage.name).hex == storage.name)
    mounts = Path('/proc/self/mountinfo').read_text().splitlines()
    require(any(line.split()[4] == str(storage.parent)
                and any(field.startswith('shared:') for field in line.split()[6:line.split().index('-')])
                for line in mounts))
    # Retained owners consume space even when their Pods are idle. Keep root-disk
    # headroom independent of the concurrent Pod count.
    require(shutil.disk_usage(storage.parent).free >= 2 * BYTES + 10 * BYTES)
    # This mkdir is the durable no-replay permit. Any failure keeps it in place.
    storage.mkdir(mode=0o700)
    for kind in ('native', 'control'):
        target = storage/kind
        target.mkdir(mode=0o700)
        backing = storage/(kind+'.ext4')
        descriptor = os.open(backing, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            os.posix_fallocate(descriptor, 0, BYTES)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        run(['mkfs.ext4', '-q', '-F', '-m', '0', str(backing)], check=True, timeout=60,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        run(['mount', '-o', 'loop,nodev,nosuid', str(backing), str(target)], check=True, timeout=30,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        require(os.path.ismount(target))
        # ext4 creates lost+found. It is empty on this just-created filesystem;
        # preserve it outside the runner-visible state root via a subdirectory.
        data = target/'data'; data.mkdir(mode=0o700)
    parent_fd = os.open(storage, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(parent_fd)
    finally: os.close(parent_fd)


def data_path(storage, kind):
    return Path(storage)/kind/'data'


def verify(storage):
    storage = Path(storage)
    require(storage == storage.resolve())
    for kind in ('native', 'control'):
        target = storage/kind
        require(target == target.resolve() and os.path.ismount(target))
        info = os.statvfs(target)
        require(0 < info.f_blocks * info.f_frsize <= BYTES)
        backing = storage/(kind+'.ext4')
        require(backing == backing.resolve() and backing.is_file() and backing.stat().st_size == BYTES
                and backing.stat().st_uid == 0 and backing.stat().st_nlink == 1
                and not backing.stat().st_mode & 0o077)


def restore(storage, *, run=subprocess.run):
    """Mount existing images only; never create or format on reboot."""
    storage = Path(storage)
    require(os.geteuid() == 0 and storage.is_absolute() and storage == storage.resolve()
            and str(storage).startswith('/var/lib/anxious-lookout/')
            and UUID(hex=storage.name).hex == storage.name and storage.stat().st_uid == 0)
    mounts = Path('/proc/self/mountinfo').read_text().splitlines()
    require(any(line.split()[4] == str(storage.parent)
                and any(field.startswith('shared:') for field in line.split()[6:line.split().index('-')])
                for line in mounts))
    for kind in ('native', 'control'):
        backing, target = storage/(kind+'.ext4'), storage/kind
        require(backing == backing.resolve() and backing.is_file() and backing.stat().st_size == BYTES
                and backing.stat().st_uid == 0 and backing.stat().st_nlink == 1
                and not backing.stat().st_mode & 0o077 and target == target.resolve() and target.is_dir())
        if not os.path.ismount(target):
            require(not any(target.iterdir()))
            run(['mount', '-o', 'loop,nodev,nosuid', str(backing), str(target)], check=True, timeout=30,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    verify(storage)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Restore one reviewed existing owner volume pair without formatting')
    parser.add_argument('--restore', required=True)
    args = parser.parse_args()
    try:
        restore(args.restore)
    except Exception:
        raise SystemExit('Storage restore refused; no formatting or cleanup performed') from None
    print('PASS existing bounded filesystems mounted; binding audit still required')
