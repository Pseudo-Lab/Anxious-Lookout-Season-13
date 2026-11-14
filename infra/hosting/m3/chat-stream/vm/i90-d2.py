"""#90 stage D2 (chat-stream/DEV-VM.md P3), development VM only, read only, no Codex turn: the L length
distribution of …5805's redaction journal. Run as root on the guest, once, after D1:

    python3 -I -B /root/issue90/i90-d2.py measure

Steps (each refuses with rc 3 before the measurement):
1. …5805 is idle_stopped with no Pod, and no runner Pod exists (the control volume is not in use, so the journal
   is not hot).
2. The binding plan's `control` reference ({name, uid, volume}) is matched to the cluster: PVC
   codex-runner/<name> with that uid, Bound to PV <volume>; the PV's claimRef is that PVC (name and uid); its path
   (`spec.local.path` or `spec.hostPath.path`) is an absolute, existing, non-symlink directory.
3. /root/issue90/journal_lengths.py is the reviewed file (JOURNAL_LENGTHS_SHA), and the tool image is the one
   i90-expected.json names (toolImage), read at its reviewed sha256 (EXPECTED_SHA).
4. journal_lengths.py runs in a throwaway guest Docker container: `--pull=never --network none --read-only
   --cap-drop ALL --security-opt no-new-privileges --security-opt label=disable --user 10001:10001`, the PV path
   mounted read only at /control; the script is not mounted (it is root 0600): the exact bytes whose sha256 was
   checked go to `python3 -I -B - /control` on stdin. `label=disable` keeps SELinux from denying the read without a
   `:z` relabel (a relabel would write the volume's labels). The container is named issue90-i90-d2-<pid> and proved
   gone afterwards (i90-switch.py's present / proved_gone).

Output: the measurement's one JSON line (`{"ok", "literals", "L", "holdbackChars", "lengths"}`; never a literal,
digest, filter or MAC) and fixed lines naming the PVC/PV match. Nothing is written on the guest.
rc: 0 measured; 2 the measurement refused its input (journal_lengths rc 2: missing, symlink, non-regular, hot
sidecar); 3 refused before the measurement (a check above, a lookup, usage); 4 database error (journal_lengths rc 3)
or unreadable output; 80 the container was not proved gone (report; nothing else is run).
"""
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time

OWNER = '00000000-0000-4000-8000-000000005805'
EXPECTED_FILE = '/root/issue90/i90-expected.json'
EXPECTED_SHA = '2476bdb2535e7733a58494fbac63057a02d8724b6b9f599b94fbfb07efa31b9f'  # i90-expected.json, from sha256sum
SCRIPT = '/root/issue90/journal_lengths.py'
JOURNAL_LENGTHS_SHA = '0750d3a711c4278168d42265e222c6d2b216b87c8919bc8ab2238791f2a117f8'  # journal_lengths.py, from sha256sum
K3S, DOCKER = '/usr/local/bin/k3s', '/usr/bin/docker'
RUNNER_NS = 'codex-runner'


class Stop(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def run(cmd, timeout=60, stdin=None):
    try:
        done = subprocess.run(cmd, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=max(1, timeout), check=False)
    except subprocess.TimeoutExpired:
        return 124, ''
    except Exception:
        return 125, ''
    return done.returncode, done.stdout.decode('utf-8', 'replace').strip()


def kjson(args, message):
    rc, out = run([K3S, 'kubectl'] + args + ['-o', 'json'], 60)
    if rc != 0:
        raise Stop(3, message)
    try:
        return json.loads(out)
    except ValueError:
        raise Stop(3, message) from None


def sql(tag, query):
    text = 'BEGIN READ ONLY;\n/*i90d2:%s*/ %s\nCOMMIT;\n' % (tag, query)
    rc, out = run([K3S, 'kubectl', '-n', 'm2-hosting', 'exec', '-i', 'postgres-0', '--', 'psql', '-X', '-U', 'postgres',
                   '-d', 'hosting', '-Atq', '-v', 'ON_ERROR_STOP=1', '-f', '-'], 60, text.encode())
    if rc != 0:
        raise Stop(3, 'database lookup failed (%s)' % tag)
    try:
        return [json.loads(line) for line in out.splitlines() if line.strip() not in ('', 'BEGIN', 'COMMIT')]
    except ValueError:
        raise Stop(3, 'database result unreadable (%s)' % tag) from None


def read_root_file(path, limit=1 << 20):
    """(sha256, bytes) of a root-owned regular file without group/other write: lstat type first, then an
    O_NOFOLLOW | O_NONBLOCK descriptor and fstat (a FIFO or symlink never blocks or is followed)."""
    if not stat.S_ISREG(os.lstat(path).st_mode):
        raise Stop(3, '%s is not a regular file' % path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not (stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022):
            raise Stop(3, '%s is not a root regular file without group/other write' % path)
        data = b''
        while len(data) <= limit:
            chunk = os.read(fd, 1 << 16)
            if not chunk:
                return hashlib.sha256(data).hexdigest(), data
            data += chunk
        raise Stop(3, '%s is larger than expected' % path)
    finally:
        os.close(fd)


def present(name):
    rc, out = run([DOCKER, 'ps', '-a', '--filter', 'name=^%s$' % name, '--format', '{{.State}}'], 30)
    if rc != 0:
        return None
    return out != ''


def proved_gone(name):
    for _ in range(3):
        state = present(name)
        if state is False:
            return True
        if state is None:
            return False
        run([DOCKER, 'stop', '-t', '30', name], 60)
        run([DOCKER, 'rm', '-f', name], 30)
        time.sleep(1)
    return present(name) is False


def tool_image():
    for name, want in (('EXPECTED_SHA', EXPECTED_SHA), ('JOURNAL_LENGTHS_SHA', JOURNAL_LENGTHS_SHA)):
        if not re.fullmatch(r'[0-9a-f]{64}', want):
            raise Stop(3, '%s is still a placeholder' % name)
    try:
        got, data = read_root_file(EXPECTED_FILE)
        doc = json.loads(data)
    except Stop:
        raise
    except Exception:
        raise Stop(3, 'i90-expected.json missing or unreadable') from None
    if got != EXPECTED_SHA:
        raise Stop(3, 'i90-expected.json is not the reviewed file')
    try:
        got, script = read_root_file(SCRIPT)
    except Stop:
        raise
    except Exception:
        raise Stop(3, 'journal_lengths.py missing or unreadable') from None
    if got != JOURNAL_LENGTHS_SHA:
        raise Stop(3, 'journal_lengths.py is not the reviewed file')
    image = doc.get('toolImage')
    if not (isinstance(image, str) and re.fullmatch(r'sha256:[0-9a-f]{64}', image)):
        raise Stop(3, 'i90-expected.json has no tool image id')
    rc, found = run([DOCKER, 'image', 'inspect', '--format', '{{.Id}}', image], 30)
    if rc != 0 or found != image:
        raise Stop(3, 'the tool image is not present under its recorded id')
    return image, script


def quiet():
    rows = sql('registry', "SELECT json_build_object('phase',phase,'gen',generation,'pod',pod_uid,'active',"
               "active_requests) FROM runner_runtime.registry WHERE owner_id='%s';" % OWNER)
    if len(rows) != 1 or (rows[0]['phase'], rows[0]['pod'], rows[0]['active']) != ('idle_stopped', None, 0):
        raise Stop(3, '…5805 is not idle_stopped with no Pod (%s)' % json.dumps(rows, sort_keys=True))
    pods = kjson(['-n', RUNNER_NS, 'get', 'pods'], 'runner Pod lookup failed')['items']
    if pods:
        raise Stop(3, 'a runner Pod exists (%d)' % len(pods))
    print('…5805 registry %s; runner Pods 0' % json.dumps(rows[0], sort_keys=True), flush=True)


def control_path():
    rows = sql('binding', "SELECT json_build_object('control',plan->'control') FROM runner_bindings.bindings WHERE "
               "owner_id='%s';" % OWNER)
    control = rows[0].get('control') if len(rows) == 1 else None
    if not (isinstance(control, dict) and set(control) == {'name', 'uid', 'volume'}
            and all(isinstance(v, str) and v for v in control.values())):
        raise Stop(3, 'the binding plan has no control {name, uid, volume}')
    pvc = kjson(['-n', RUNNER_NS, 'get', 'pvc', control['name']], 'control PVC lookup failed')
    pv = kjson(['get', 'pv', control['volume']], 'control PV lookup failed')
    claim = (pv.get('spec') or {}).get('claimRef') or {}
    path = (pv['spec'].get('local') or {}).get('path') or (pv['spec'].get('hostPath') or {}).get('path')
    checks = {
        'PVC uid is the plan uid': pvc['metadata'].get('uid') == control['uid'],
        'PVC Bound to the plan volume': pvc.get('status', {}).get('phase') == 'Bound'
                                         and pvc['spec'].get('volumeName') == control['volume'],
        'PV claimRef is that PVC': (claim.get('namespace'), claim.get('name'), claim.get('uid'))
                                   == (RUNNER_NS, control['name'], control['uid']),
        'PV path absolute, a directory, not a symlink': isinstance(path, str) and path.startswith('/')
                                                        and os.path.isdir(path) and not os.path.islink(path),
    }
    for name, value in checks.items():
        print('check %s: %s' % (name, value), flush=True)
    if not all(checks.values()):
        raise Stop(3, 'the control PVC/PV do not match the binding plan')
    print('control PVC %s (uid %s) → PV %s' % (control['name'], control['uid'], control['volume']), flush=True)
    return path


def measure(image, script, path):
    name = 'issue90-i90-d2-%d' % os.getpid()
    rc, out = run([DOCKER, 'run', '--rm', '-i', '--name', name, '--pull=never', '--network', 'none', '--read-only',
                   '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--security-opt', 'label=disable',
                   '--user', '10001:10001', '-v', '%s:/control:ro' % path, '--entrypoint', 'python3', image,
                   '-I', '-B', '-', '/control'], 300, script)
    if not proved_gone(name):
        print('container %s not proved gone' % name, flush=True)
        return 80
    print('measurement rc %d: %s' % (rc, out.splitlines()[-1] if out else ''), flush=True)
    try:
        result = json.loads(out.splitlines()[-1])
    except (ValueError, IndexError):
        return 4
    if rc == 0 and result.get('ok') is True and set(result) == {'ok', 'literals', 'L', 'holdbackChars', 'lengths'}:
        return 0
    return {2: 2, 3: 4}.get(rc, 4)


def main(argv):
    if len(argv) != 2 or argv[1] != 'measure':
        print('usage: i90-d2.py measure\nresult rc=3')
        return 3
    try:
        if os.geteuid() != 0:
            raise Stop(3, 'not root')
        image, script = tool_image()
        quiet()
        path = control_path()
        code = measure(image, script, path)
    except Stop as stop:
        print('STOPPED (%d): %s' % (stop.code, stop.message))
        code = stop.code
    print('result rc=%d (read-only)' % code)
    return code


if __name__ == '__main__':
    sys.exit(main(sys.argv))
