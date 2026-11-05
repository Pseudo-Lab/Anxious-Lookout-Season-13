"""#68 build gate: the runner image as built, checked as the runner user.

The Dockerfile `runner` stage runs `python -m runner.image_check` after `USER 10001:10001`, so every
build (development and production) fails if the image could not run the native shell runner. Unit tests
cannot see this: a file mode, owner or missing package only exists in the built image (#68 V1 found
`/etc/codex` at mode 644). Offline; reads files and runs `bwrap --version` only.
"""
import hashlib
import os
import shutil
import stat
import subprocess
import sys

from app.codex_policy import CodexFailure
from . import native_policy
from .install_codex import pinned

RUNNER_IDENTITY = (10001, 10001)
BINARIES = (('/usr/local/bin/codex', 'codex_sha256'), ('/usr/local/bin/codex-code-mode-host', 'host_sha256'))
SANDBOX_LINK = '/usr/local/bin/codex-linux-sandbox'


def problems():
    """Names of the failed checks; empty when the image is usable."""
    found = []
    if (os.getuid(), os.getgid()) != RUNNER_IDENTITY:
        found.append('identity')
    try:
        pin = pinned()
        for path, key in BINARIES:
            with open(path, 'rb') as executable:
                if hashlib.file_digest(executable, 'sha256').hexdigest() != pin[key] or not os.access(path, os.X_OK):
                    found.append('pin ' + path)
    except (OSError, RuntimeError):
        found.append('pin')
    bwrap = shutil.which('bwrap')
    try:
        version = subprocess.run([bwrap, '--version'], capture_output=True, text=True, timeout=10).stdout if bwrap else ''
    except (OSError, subprocess.SubprocessError):
        version = ''
    if not version.startswith('bubblewrap '):
        found.append('bwrap')
    try:
        if os.readlink(SANDBOX_LINK) != 'codex':
            found.append('sandbox link')
    except OSError:
        found.append('sandbox link')
    try:
        info = os.lstat('/etc/codex')
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o755:
            found.append('/etc/codex directory')
    except OSError:
        found.append('/etc/codex directory')
    try:
        native_policy.check_files(native_policy.STATE_ROOT)
    except CodexFailure:
        found.append('policy files')
    if shutil.which('git'):
        found.append('git present')  # Codex would run it outside the sandbox in the cwd (native-shell README 7).
    if not os.access('/app/runner/app.py', os.R_OK):
        found.append('/app readable')
    return found


def main():
    found = problems()
    for name in found:
        print('runner image check failed: ' + name, file=sys.stderr)
    if found:
        raise SystemExit(1)
    print('runner image check ok')


if __name__ == '__main__':
    main()
