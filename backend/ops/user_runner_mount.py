"""Copy the kubelet-owned Secret into an API-owned private tmpfs at Pod init."""
import os
from pathlib import Path

from runner.lifecycle import require


def main():
    require(os.geteuid() == 10001)
    root = Path('/run/user-runner/private')
    root.mkdir(mode=0o700, exist_ok=True)
    from runner.auth import private_directory
    private_directory(root)
    for filename in ('runtime.json', 'transport-token'):
        source = Path('/run/runner-input')/filename
        value = source.read_bytes()
        require(0 < len(value) <= 65536)
        if (root/filename).exists():
            from runner.auth import read_private
            require(read_private(root/filename, json_format=False).encode() == value)
            continue
        fd = os.open(root/filename, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as target:
            target.write(value); target.flush(); os.fsync(target.fileno())


if __name__ == '__main__':
    main()
