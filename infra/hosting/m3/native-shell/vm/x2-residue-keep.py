"""#68 X2 residue keep line (design r5 §4.3; review r5-plan question / PM conditions). Guest, root; one run after
review and PM's go; sent as text:

    ssh … 'sudo -n timeout 300 python3 -I -B - plan' < x2-residue-keep.py   # read-only: identities vs the pins
    ssh … 'sudo -n timeout 300 python3 -I -B - run'  < x2-residue-keep.py   # the moves and the receipt

What it moves: the four leftovers of the T1 X2 run (look da0ca737…): codex:v6-x2-a, native:v6-x2-c, native:v6-x2-d
(files, uid 10001, 6 bytes, the pinned sha256) and workspace:v6-x2-link (a symlink to /state/codex). Where: a new
directory `x2-residue-kept-<UTC>` in the native PV root (NATIVE), the same filesystem as every source (each st_dev is
compared first; a different device refuses: no copy + unlink path exists here), so each move is one rename(2) into an
empty, newly created directory (no destination can exist; it is also checked). Before: each source's type / ino / size /
uid / nlink and its content sha256 (file) or link target (link) equal the look's pins; control:v6-x2-b and
codex:v6-x2-e absent; no …5805 runner Pod; the base lock (evidence/v6pre.lock) free and held for the whole run. After
each move: the source absent; the kept entry the same inode, type, size and content / target. Evidence:
evidence/x2-residue-keep-<UTC>/ (new): out.txt (every line up to `outcome rc=N`), receipt.json (new, only after
every check held).
rc: 0 moved, verified, receipt written AND out.txt recorded; 2 usage / not root; 3 refused before any change (nothing
changed, the new names checked absent first); 7 (r1) ANY failure after the first change (the evidence dir created): a
refused check, a move, a check or read after a move, the receipt's creation or write, and (r2) out.txt's
open / write / flush / fsync / close: the state is kept as it is (nothing moved back, nothing deleted, no retry, no
rollback) and a per-entry partial report says what moved and what stayed; report it.
r2 (review r5+D r1): the final `result rc=N` is printed (stdout only) after out.txt is closed, never before; out.txt
ends with `outcome rc=N (… final only if stdout ends with result rc=N)`. If out.txt cannot be recorded, stdout says so,
gives the partial report and ends with `result rc=7`; any out.txt left is then incomplete and is not the record.
The kept directory and its entries are evidence: never cleaned, automatically or otherwise, without a new decision.
"""
import errno
import fcntl
import hashlib
import json
import os
import stat
import subprocess
import sys
import time

STATE_HEX = '75780e81ef7f4aeca59ced9d71f4061a'
NATIVE = '/var/lib/anxious-lookout/user-runners/%s/native/data' % STATE_HEX
CONTROL = '/var/lib/anxious-lookout/user-runners/%s/control/data' % STATE_HEX
EVIDENCE = '/root/native68/evidence'
LOCK = EVIDENCE + '/v6pre.lock'
OWNER = '00000000-0000-4000-8000-000000005805'
K3S = '/usr/local/bin/k3s'
FAILED_REF = 'v6-item-X2-20261112T130724Z'
FAILED_SHA = '8ba8b66c86287033950babf994dd76ab7c4e71e2d26faa53e3cd43513eaee5ee'
RECEIPT_VERSION = 'x2-residue-receipt-r0'
# label -> (path relative to NATIVE, pinned identity from the X2 look da0ca737…)
ENTRIES = {
    'codex:v6-x2-a': ('codex/v6-x2-a', {'type': 'file', 'ino': 131227, 'size': 6, 'uid': 10001, 'nlink': 1,
                                        'sha256': '290d0bf30a58dff1a92db8f09a4b00cb9e508d29aae0228b571af8cc5d02de03'}),
    'native:v6-x2-c': ('v6-x2-c', {'type': 'file', 'ino': 131228, 'size': 6, 'uid': 10001, 'nlink': 1,
                                   'sha256': '290d0bf30a58dff1a92db8f09a4b00cb9e508d29aae0228b571af8cc5d02de03'}),
    'native:v6-x2-d': ('v6-x2-d', {'type': 'file', 'ino': 131229, 'size': 6, 'uid': 10001, 'nlink': 1,
                                   'sha256': '290d0bf30a58dff1a92db8f09a4b00cb9e508d29aae0228b571af8cc5d02de03'}),
    'workspace:v6-x2-link': ('workspace/v6-x2-link', {'type': 'link', 'ino': 131220, 'size': 12, 'uid': 10001,
                                                      'nlink': 1, 'linkTarget': '/state/codex'}),
}
ABSENT = {'control:v6-x2-b': os.path.join(CONTROL, 'v6-x2-b'), 'codex:v6-x2-e': os.path.join(NATIVE, 'codex/v6-x2-e')}
LINES = []
EV_DIR = []  # this run's evidence directory, once created (out.txt goes there and nowhere else)
STATE = {'changed': False, 'moved': []}  # r1: after the first change any failure is rc 7 (partial), never rc 3


class Stop(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def say(text):
    LINES.append(text)
    print(text, flush=True)


def identity(path):
    """The entry's identity by one no-follow lstat (and its content / target), or None when absent."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    kind = 'link' if stat.S_ISLNK(info.st_mode) else 'file' if stat.S_ISREG(info.st_mode) else 'other'
    got = {'type': kind, 'ino': info.st_ino, 'size': info.st_size, 'uid': info.st_uid, 'nlink': info.st_nlink,
           'dev': info.st_dev}
    if kind == 'file':
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            if not same(info, os.fstat(fd)):
                raise Stop(3, '%s changed between lstat and open' % path)
            got['sha256'] = hashlib.sha256(b''.join(iter(lambda: os.read(fd, 1 << 20), b''))).hexdigest()
        finally:
            os.close(fd)
    if kind == 'link':
        got['linkTarget'] = os.readlink(path)
    return got


def same(a, b):
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def matches(got, pin):
    return got is not None and all(got.get(k) == v for k, v in pin.items())


def owner_pods():
    out = subprocess.run([K3S, 'kubectl', '-n', 'codex-runner', 'get', 'pods', '-l', 'anxious.io/owner=' + OWNER,
                          '-o', 'name'], capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise Stop(3, 'the runner Pod lookup failed (rc %d)' % out.returncode)
    return [l for l in out.stdout.splitlines() if l.strip()]


def precheck():
    """Read-only: every pin, the absent targets, one device. Returns (sources {label: path}, device)."""
    sources, devices = {}, set()
    for label, (rel, pin) in sorted(ENTRIES.items()):
        path = os.path.join(NATIVE, rel)
        got = identity(path)
        say('source %s %s: %s' % (label, path, json.dumps(got, sort_keys=True)))
        if not matches(got, pin):
            raise Stop(3, '%s is not at its pinned identity (%s)' % (label, json.dumps(pin, sort_keys=True)))
        sources[label] = path
        devices.add(got['dev'])
    for label, path in sorted(ABSENT.items()):
        try:
            os.lstat(path)
            raise Stop(3, '%s is present (it must stay absent)' % label)
        except FileNotFoundError:
            say('absent %s: yes' % label)
    root = os.lstat(NATIVE).st_dev
    devices.add(root)
    if len(devices) != 1:
        raise Stop(3, 'the sources and the native PV root are not on one device (%s): no move' % sorted(devices))
    pods = owner_pods()
    say('…5805 runner Pods: %d' % len(pods))
    if pods:
        raise Stop(3, 'a …5805 runner Pod exists: no move while the PV may be in use')
    return sources, root


def run():
    stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
    lock = os.open(LOCK, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise Stop(3, 'the base lock is held (a unit is running)')
    sources, device = precheck()
    ev = os.path.join(EVIDENCE, 'x2-residue-keep-' + stamp)
    kept = os.path.join(NATIVE, 'x2-residue-kept-' + stamp)
    for path in (ev, kept):                     # r1: both new names absent before any change (else rc 3)
        if os.path.lexists(path):
            raise Stop(3, '%s exists; nothing changed' % path)
    os.mkdir(ev, 0o700)
    STATE['changed'] = True                     # from here on: rc 7 on any failure
    EV_DIR.append(ev)
    os.mkdir(kept, 0o700)                       # new: an existing name raises (FileExistsError) before any move
    if os.lstat(kept).st_dev != device:
        raise Stop(7, 'the new kept directory is on another device (nothing moved; the empty directory stays)')
    say('kept directory %s (new, device %d); evidence %s' % (kept, device, ev))
    entries = []
    STATE['sources'] = dict(sources)
    STATE['kept'] = kept
    for label, path in sorted(sources.items()):
        pin = ENTRIES[label][1]
        dest = os.path.join(kept, os.path.basename(path))
        if identity(dest) is not None:
            raise Stop(7, 'the destination %s exists (state kept as it is)' % dest)
        before = identity(path)
        if not matches(before, pin):
            raise Stop(7, '%s changed before its move (state kept as it is)' % label)
        try:
            os.rename(path, dest)               # same device: one rename, no copy
        except OSError as error:
            raise Stop(7, 'the move of %s failed (%s; state kept as it is)' % (label, errno.errorcode.get(
                error.errno, error.errno)))
        STATE['moved'].append(label)               # recorded as soon as the rename returned
        after, gone = identity(dest), identity(path)
        say('moved %s -> %s: source now %s; kept %s' % (label, dest, gone, json.dumps(after, sort_keys=True)))
        if gone is not None or not matches(after, pin) or after['dev'] != device:
            raise Stop(7, 'the move of %s is not proved (state kept as it is)' % label)
        entry = {'label': label, 'source': path, 'kept': dest}
        entry.update({k: after[k] for k in ('type', 'ino', 'size', 'uid', 'nlink')})
        entry.update({'sha256': after['sha256']} if after['type'] == 'file' else {'linkTarget': after['linkTarget']})
        entries.append(entry)
    receipt = {'version': RECEIPT_VERSION, 'failedRef': FAILED_REF, 'failedSha': FAILED_SHA, 'keptDir': kept,
               'device': device, 'entries': entries, 'time': stamp}
    data = json.dumps(receipt, sort_keys=True, indent=1).encode()
    fd = os.open(os.path.join(ev, 'receipt.json'), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as out:
        out.write(data)
        out.flush()                                # r2: the receipt is durable before any rc 0
        os.fsync(out.fileno())
    say('receipt %s sha256 %s' % (os.path.join(ev, 'receipt.json'), hashlib.sha256(data).hexdigest()))
    return ev


def partial():
    """r1: the state after a failure past the first change, per entry (each lookup on its own; nothing is moved back,
    nothing retried): which were moved (the rename returned), whether each source and kept path exists now."""
    say('partial state: kept directory %s; moved (rename returned): %s' % (STATE.get('kept'), STATE['moved']))
    for label, path in sorted((STATE.get('sources') or {}).items()):
        dest = os.path.join(STATE['kept'], os.path.basename(path))
        try:
            src, kept = os.path.lexists(path), os.path.lexists(dest)
        except Exception as error:
            src = kept = 'lookup failed (%s)' % type(error).__name__
        say('  %s: source present %s; kept present %s' % (label, src, kept))


def main(argv):
    if len(argv) != 2 or argv[1] not in ('plan', 'run') or os.geteuid() != 0:
        print('usage (root): x2-residue-keep.py plan | run')
        return 2

    try:
        if argv[1] == 'plan':
            precheck()
            say('result rc=0 (plan, read-only)')
            return 0
        run()
        rc = 0
    except Stop as stop:
        say('stop: %s' % stop.message)
        rc = 7 if STATE['changed'] else stop.code         # r1: rc 3 only while nothing was changed
    except Exception as error:                          # r1: any other failure (OSError, the receipt's O_EXCL, …)
        say('stop: %s: %s' % (type(error).__name__, error))
        rc = 7 if STATE['changed'] else 3
    if rc == 7:
        report_partial()
    if EV_DIR:                                         # only after a change (EV_DIR is set after STATE['changed'])
        say('outcome rc=%d (recorded in out.txt; final only if stdout ends with result rc=%d)' % (rc, rc))
        try:
            record(os.path.join(EV_DIR[0], 'out.txt'))
        except Exception as error:                     # r2: open / write / flush / fsync / close
            say('stop: out.txt not recorded (%s: %s); any out.txt left is incomplete and is not the record'
                % (type(error).__name__, error))
            if rc != 7:
                report_partial()
            rc = 7
    say('result rc=%d' % rc)                           # r2: printed only now, after out.txt is closed (or failed)
    return rc


def report_partial():
    """partial(), whose own failure is reported and never changes the rc 7 it belongs to."""
    try:
        partial()
    except Exception as error:
        say('partial report failed (%s: %s)' % (type(error).__name__, error))


def record(path):
    """out.txt: new (O_EXCL, no follow), every line so far, flushed, fsynced and closed; any failure raises."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        out = os.fdopen(fd, 'w')
    except Exception:
        os.close(fd)
        raise
    with out:
        out.write('\n'.join(LINES) + '\n')
        out.flush()
        os.fsync(out.fileno())


if __name__ == '__main__':
    sys.exit(main(sys.argv))
