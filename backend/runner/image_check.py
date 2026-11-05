"""#68 build gate: the runner image as built, checked as the runner user.

The Dockerfile `runner` stage runs `python -m runner.image_check` after `USER 10001:10001`, so every
build (development and production) fails if the image could not run the native shell runner. Unit tests
cannot see this: a file mode, owner or missing package only exists in the built image (#68 V1 found
`/etc/codex` at mode 644), and only the real Codex can say whether it accepts the config (#68 V3 found
`approval_policy = "untrusted"` refused). Offline (the Dockerfile runs it with --network=none): reads files,
runs `bwrap --version`, and starts the pinned `codex app-server` in the runner's own layout (`/state/home`,
`/state/codex`, `/state/workspace`, removed afterwards) for two config reads.

What it cannot do: create a thread. Codex creates a thread through bubblewrap, and a build `RUN` has no user
namespace (Docker's default seccomp and AppArmor), so thread/start always fails here. The static unit test
`test_no_deny_entry_covers_the_codex_runtime_root` and the pre-release probe (native-shell/README.md section 7)
cover what only a thread shows.
"""
import hashlib
import json
import os
import queue
import shutil
import stat
import subprocess
import sys
import threading

from app.codex_policy import CodexFailure
from . import native_policy
from .install_codex import pinned

RUNNER_IDENTITY = (10001, 10001)
BINARIES = (('/usr/local/bin/codex', 'codex_sha256'), ('/usr/local/bin/codex-code-mode-host', 'host_sha256'))
SANDBOX_LINK = '/usr/local/bin/codex-linux-sandbox'


def codex_accepts_config():
    """The image's pinned Codex itself loads /etc/codex: start `codex app-server` offline (no login, no model)
    with the runner's real paths under /state, read configRequirements/read and config/read, and apply the
    runner's own checks. Returns a problem name or None. The process is always stopped and the three
    directories removed before returning; they must not exist before (the image ships /state empty)."""
    from .app import native_command  # the runner's own start arguments (operator access mode, as deployed)
    root = native_policy.STATE_ROOT
    home, codex_home, work = (os.path.join(root, name) for name in ('home', 'codex', 'workspace'))
    try:
        if os.listdir(root):
            return 'state not empty'
        for path in (home, codex_home, work):
            os.mkdir(path, 0o700)
    except OSError:
        return 'state layout'
    command, env = native_command('/usr/local/bin/codex', home, codex_home, work, file_auth=False, external=True, fixture=False)
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   text=True, env=env, cwd=work)
    except OSError:
        return 'codex app-server start'
    lines = queue.Queue()
    threading.Thread(target=lambda: [lines.put(line) for line in process.stdout] and None, daemon=True).start()

    def rpc(identity, method, params):
        process.stdin.write(json.dumps({'id': identity, 'method': method, 'params': params}) + '\n')
        process.stdin.flush()
        while True:
            message = json.loads(lines.get(timeout=30))
            if message.get('id') == identity and 'method' not in message:
                if 'error' in message:
                    raise RuntimeError(method)
                return message['result']

    try:
        rpc(1, 'initialize', {'clientInfo': {'name': 'image_check', 'version': '1'}, 'capabilities': {'experimentalApi': True}})
        process.stdin.write(json.dumps({'method': 'initialized'}) + '\n')
        process.stdin.flush()
        native_policy.check_requirements(rpc(2, 'configRequirements/read', {}))
        native_policy.check_config(rpc(3, 'config/read', {'includeLayers': True, 'cwd': work}), native_policy.STATE_ROOT)
        return None
    except CodexFailure:
        return 'codex config checks'
    except (queue.Empty, ValueError, RuntimeError, OSError, KeyError):
        return 'codex refused or did not answer the config reads'
    finally:
        try:
            process.stdin.close()
            process.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
            process.wait(timeout=10)
        for path in (home, codex_home, work):
            shutil.rmtree(path, ignore_errors=True)


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
    if not found:  # Only with the pinned binary and both policy files in place.
        codex = codex_accepts_config()
        if codex:
            found.append(codex)
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
