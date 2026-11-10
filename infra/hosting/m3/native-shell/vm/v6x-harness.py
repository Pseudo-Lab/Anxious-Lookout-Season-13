"""#68 V6x carve-out reproduction harness (design r3, carveout-repro-design-r3.md 976e04ac…, review round 1 fixes):
runs inside the measurement Pod v6x-carveout (`kubectl exec -i … python3 -I - < v6x-harness.py`), as UID 10001,
with no model, no network outside the Pod, no credentials. Development VM only; started by v6x-carveout.sh.

Question: does a `codex sandbox -P native` launch (the image's Codex 0.160.1, its own argv and permission profile
from /etc/codex) create the four protected metadata paths (.git .agents .codex .aws) in its writable root while it
runs, does its cleanup launcher remove them on a normal end, on SIGTERM, and what stays when the cleanup launcher
is killed or stopped before it can clean up. A met result reproduces the mechanism under measurement-Pod
conditions only; it does not establish the cause of the run-b T5 paths.

Output: one JSON object per line on stdout ("k" names the record), fixed fields only: booleans, counts, fixed
names (roles, errno names, change categories, special-path kinds), inode numbers and nanosecond times of the paths
this harness made. No command text, no command output, no environment, no token, no other path names (round 4: a
disallowed change is described by fixed fields, its names only as an exact match of a fixed candidate name or as a
sha256; see CANDIDATES). Exit 0 when
every phase was attempted (results are in the records), 2 when the environment, the baseline or the gate stopped
the run before the cases, 3 when a case stopped the run, 4 when the shared daemon root precondition (round 5) is
not met; the host script reads the records, not only the rc.

Round 5 (C2, review r1 conditions): the measurement models one condition only, "a valid, empty shared daemon root
already exists". Codex 0.160.1 keeps that root at the fixed /tmp/codex-daemon-<euid> (uds/src/daemon_directory.rs;
static source r1 28c6f650…), outside TMPDIR. Before the baseline the harness creates exactly that one path, mode
0700. If the path already exists in the fresh Pod, or the lookup fails, it stops with rc 4 and changes nothing: it
never deletes or chmods it to fit. It records that it made the path (record "daemon"). After the gate and after
each case it compares the path's lstat type, uid, gid, full st_mode, inode and emptiness with that record; an error
or any difference stops the run, as do (through the unchanged snapshot rules) its replacement, removal, a symlink
or any entry below it. Sequential observations cannot see a change that is undone between two of them. Results hold
under this precondition only; they do not show the naturally created path, or which process made it in earlier
runs. The allowance (Phase.category and the existing stop rules) is unchanged.

Structure (design r3 sections):
- §6 environment: rw mounts against ALLOWED_RW (fixed below), the required security values (UID/GID 10001,
  NoNewPrivs 1, seccomp filter mode, no effective/permitted capabilities, SELinux type container_t), volumes.
- The deny marker /control/fake-marker and HOME/CODEX_HOME are made before the baseline snapshot (round 1 blocker).
- §3 gate in /state/gate (TMPDIR /tmp/gate): G1 allowed write, G2 intended write refusal (.codex/x, errno EROFS or
  EACCES), G3 deny read (EACCES or ENOENT), G4 loopback contrast (unsandboxed control connects and is accepted;
  the sandboxed client fails at once with ECONNREFUSED, ENETUNREACH, EPERM or EACCES and is never accepted), G5 the
  real process tree (one cleanup launcher under the wrapper, one identified bwrap kind: /usr/bin/bwrap, or Codex's
  fixed bundled path; anything else is unknown and fails) and the launcher's own
  argv/profile against the expected native profile, the gate launch ending with rc 0.
- §4 cases ① to ⑧, each in /state/c<N> (cwd and writable root) with TMPDIR /tmp/c<N> (⑦ shares /tmp/c7 on
  purpose, ⑧ uses /tmp/c8a and /tmp/c8b, ⑤ reuses /state/c4 with /tmp/c5).
- Every launch is checked before its case is judged (else harness_failed after recovery): launcher identified and
  pinned, its --sandbox-policy-cwd/--command-cwd and the profile's only write root equal the case root (measured
  cwd_is_root), the profile equals the expected native profile, the waiter's parent write failed and its marker is
  absent outside the sandbox; and after: a go ends with wrapper rc 0, a signalled launcher is gone.
- Change detection per phase (§6): a recursive lstat snapshot of /state, /control, /tmp, /dev/shm (+ identity of
  the runtime rw files) before and after; allowed changes are exact: the phase's own root(s) (the root itself, its
  fixed control files, the four protected names but nothing under them), the phase's TMPDIR(s) (the directory and
  the Codex registry codex-bwrap-synthetic-mount-targets-<uid> below it), the entries CODEX_HOME and CODEX_HOME/tmp
  and the tmp/arg0 subtree (Codex's execve wrapper and helper links). Anything else, any observation error, and any protected name outside the roots used so far stop the run.
  Round 4 (allowance unchanged): each disallowed change is also recorded (unexpected_detail, at most DETAIL_MAX)
  and observation errors are counted per kind (observation_error_kinds), so a stop says what kind of entry changed.
- The waiter inside the sandbox never forks or execs: it writes probe-<id> (0/1: did a write to the parent
  succeed), then ready-<id>, then polls for go-<id>.
- Signals go only through pidfds whose start time was checked against /proc/<pid>/stat right after opening.
- Leftovers (round 2): the harness is the child subreaper (recorded), so descendants orphaned by a killed launcher
  come to it. After the gate and each case, live launch processes are killed and stop the run; processes that
  already ended (state Z/X) are never signalled and never counted as live: those that are the harness's children
  are reaped (waitpid on their PID), and what stays unreaped is recorded as exited_unreaped.
- Q3/Q4: the launcher must be gone after its signal. Q5 (case ⑥, review r3 correction, round 1 enforced): the
  cleanup launcher is stopped first (SIGSTOP, state T confirmed), the tree is enumerated twice and must be the same
  and non-empty below the launcher, every member must be pinned, the launcher must still be stopped before the
  kills and after the members are gone, every member and then the launcher must be gone; any failure is recovery
  and harness_failed (not evaluated). This is not cgroup.kill: the kills are sequential and only the enumerated
  tree is covered.
"""
import errno
import hashlib
import json
import os
import select
import signal
import socket
import stat
import subprocess
import sys
import threading
import time

CODEX = '/usr/local/bin/codex'
PYTHON = '/usr/local/bin/python3'
NAMES = ('.git', '.agents', '.codex', '.aws')
HOME, CODEX_HOME = '/state/home', '/state/codex'
CODEX_TMP = CODEX_HOME + '/tmp'
ENV = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8', 'HOME': HOME, 'CODEX_HOME': CODEX_HOME}
MARKER = '/control/fake-marker'
SYSTEM_BWRAP = '/usr/bin/bwrap'
# Codex's bundled bubblewrap locations for CODEX (bundled_bwrap.rs legacy_candidates_for_exe); /usr/local/bin/bwrap
# is left out on purpose: on PATH it would be picked as a system bwrap, so the name alone does not identify it.
BUNDLED_BWRAP = ('/usr/local/bin/codex-resources/bwrap', '/usr/local/codex-resources/bwrap')
READY_S, EXIT_S, STOP_S, PATHS_S = 10, 10, 2, 2
UID = GID = 10001
REGISTRY = 'codex-bwrap-synthetic-mount-targets-%d' % UID
# Round 5 (C2): Codex's shared daemon root for the measured UID (the environment check requires UID 10001 first).
DAEMON_ROOT = '/tmp/codex-daemon-%d' % UID
DAEMON_MODE = stat.S_IFDIR | 0o700
DAEMON_FIELDS = ('exists', 'type', 'uid', 'gid', 'mode', 'ino', 'empty')
SNAP_ROOTS = ('/state', '/control', '/tmp', '/dev/shm')
IDENTITY_FILES = ('/etc/hosts', '/etc/hostname', '/etc/resolv.conf', '/dev/termination-log')
# rw mounts allowed in the Pod (review r3: a fixed set in code): the three emptyDirs, the runtime's /dev, /dev/pts,
# /dev/mqueue, /dev/shm, /proc and the files kubelet binds; plus runtime masks under /proc/ (tmpfs, devtmpfs or a
# proc bind), recorded by mountpoint. Any other rw mount stops the run before the gate.
ALLOWED_RW = {'/state', '/control', '/tmp', '/dev', '/dev/pts', '/dev/mqueue', '/dev/shm', '/proc',
              '/etc/hosts', '/etc/hostname', '/etc/resolv.conf', '/dev/termination-log'}
MASK_FSTYPES = {'tmpfs', 'devtmpfs', 'proc'}
# Expected refusals (errno names). G2: a write under the read-only .codex mount (remount-ro; EACCES if the 555
# permission check comes first). G3: the deny mask (--perms 000 tmpfs: EACCES; ENOENT if /control is not present in
# the sandbox at all). G4: a new network namespace (ENETUNREACH with lo down, ECONNREFUSED with lo up) or the
# seccomp network filter at socket()/connect() (EPERM/EACCES). A timeout or any other errno is not a refusal here.
G2_ERRNO = {'EROFS', 'EACCES'}
G3_ERRNO = {'EACCES', 'ENOENT'}
G4_ERRNO = {'ECONNREFUSED', 'ENETUNREACH', 'EPERM', 'EACCES'}
G4_MAX_MS = 1000
# The expected native profile (backend/runner/codex-etc/config.toml, /etc/codex in the image): every entry except
# the one write root, as (normalized path, access). The write root must be :project_roots (resolved against
# --sandbox-policy-cwd) or the absolute case root, and resolve to the case root.
# Codex 0.160.1 always makes CODEX_HOME/tmp/arg0 readable (backend/runner/native_policy.py codex_runtime_root);
# if it does so through the profile, exactly that one read entry is the other accepted set (recorded which).
PROFILE_FIXED = sorted([(':minimal', 'read'), ('/control', 'deny'), ('/run/codex-broker', 'deny'),
                        ('/state/home', 'deny')])
PROFILE_FIXED_WITH_RUNTIME = sorted(PROFILE_FIXED + [(CODEX_TMP + '/arg0', 'read')])

WAITER = r'''
import os, sys, time
i = sys.argv[1]
try:
    open('../parent-probe-' + i, 'w').close()
    r = '1'
except OSError:
    r = '0'
open('probe-' + i, 'w').write(r)
open('ready-' + i, 'w').close()
while not os.path.exists('go-' + i):
    time.sleep(0.05)
'''

GATE = r'''
import errno, json, os, socket, sys, time
port = int(sys.argv[1])
def en(e):
    return errno.errorcode.get(e.errno, 'other') if e.errno is not None else 'none'
r = {}
try:
    with open('g1', 'w') as f:
        f.write('1')
    r['g1'] = True
except OSError as e:
    r['g1'] = en(e)
try:
    with open('.codex/x', 'w') as f:
        f.write('1')
    r['g2'] = True
except OSError as e:
    r['g2'] = en(e)
try:
    with open('/control/fake-marker') as f:
        f.read()
    r['g3'] = True
except OSError as e:
    r['g3'] = en(e)
t = time.monotonic()
r['g4_stage'] = 'socket'
try:
    s = socket.socket()
    try:
        r['g4_stage'] = 'connect'
        s.settimeout(3)
        s.connect(('127.0.0.1', port))
        r['g4'] = True
    finally:
        s.close()
except OSError as e:
    r['g4'] = en(e)
r['g4_ms'] = int((time.monotonic() - t) * 1000)
open('gate-result', 'w').write(json.dumps(r, sort_keys=True))
open('ready-gate', 'w').close()
while not os.path.exists('go-gate'):
    time.sleep(0.05)
'''

CLIENT = r'''
import socket, sys
s = socket.socket(); s.settimeout(3)
s.connect(('127.0.0.1', int(sys.argv[1])))
s.close()
'''


def errname(e):
    return errno.errorcode.get(e.errno, 'other') if getattr(e, 'errno', None) is not None else 'none'


def out(kind, **fields):
    print(json.dumps({'k': kind, **fields}, sort_keys=True), flush=True)


def now():
    return time.monotonic_ns()


# --- /proc reading ----------------------------------------------------------------------------------------------
def stat_fields(pid):
    with open('/proc/%d/stat' % pid) as f:
        text = f.read()
    rest = text[text.rindex(')') + 2:].split()
    return rest  # rest[0] state, rest[1] ppid, rest[19] starttime


def starttime(pid):
    try:
        return int(stat_fields(pid)[19])
    except (OSError, ValueError, IndexError):
        return None


def pstate(pid):
    try:
        return stat_fields(pid)[0]
    except (OSError, IndexError):
        return None


def children(pid):
    found = set()
    try:
        for task in os.listdir('/proc/%d/task' % pid):
            try:
                with open('/proc/%d/task/%s/children' % (pid, task)) as f:
                    found.update(int(x) for x in f.read().split())
            except OSError:
                pass
    except OSError:
        pass
    return found


def argv(pid):
    try:
        with open('/proc/%d/cmdline' % pid, 'rb') as f:
            return [a.decode(errors='replace') for a in f.read().split(b'\0') if a]
    except OSError:
        return []


def comm(pid):
    try:
        with open('/proc/%d/comm' % pid) as f:
            return f.read().strip()
    except OSError:
        return None


def exe(pid):
    try:
        return os.readlink('/proc/%d/exe' % pid)
    except OSError:
        return None


def tree(root):
    """[(pid, parent, start)] of root and its descendants, breadth first."""
    result, queue = [], [(root, None)]
    while queue:
        pid, parent = queue.pop(0)
        st = starttime(pid)
        if st is None:
            continue
        result.append((pid, parent, st))
        queue.extend((c, pid) for c in sorted(children(pid)))
    return result


def role(pid):
    a = argv(pid)
    base = os.path.basename(a[0]) if a else ''
    c = comm(pid) or ''
    if base == 'codex-linux-sandbox':
        return 'launcher'
    if c == 'bwrap' or base == 'bwrap':
        return 'bwrap'
    if base in ('python3', 'python') or c.startswith('python'):
        return 'python'
    if base == 'codex':
        return 'wrapper'
    return 'other'


class Ident:
    """A process pinned by pidfd, its start time checked right after opening."""

    def __init__(self, pid, start):
        self.pid, self.start, self.fd = pid, start, None
        if start is None:
            return
        try:
            fd = os.pidfd_open(pid)
        except OSError:
            return
        if starttime(pid) == start:
            self.fd = fd
        else:
            os.close(fd)

    def ok(self):
        return self.fd is not None

    def send(self, sig):
        """True if the signal was sent."""
        if self.fd is None:
            return False
        try:
            signal.pidfd_send_signal(self.fd, sig)
            return True
        except OSError:
            return False

    def gone(self, timeout):
        if self.fd is None:
            return False
        p = select.poll()
        p.register(self.fd, select.POLLIN)
        return bool(p.poll(int(timeout * 1000)))

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def bwrap_kind(path):
    """'system' for the PATH bwrap the image installs, 'bundled' only for Codex's own fixed bundled locations next
    to the codex binary (linux-sandbox/src/bundled_bwrap.rs legacy_candidates_for_exe: it execs that file through
    /proc/self/fd, so /proc/<pid>/exe names the file), anything else (or unreadable) 'unknown' (round 2 minor)."""
    if path == SYSTEM_BWRAP:
        return 'system'
    if path in BUNDLED_BWRAP:
        return 'bundled'
    return 'unknown'


def find_launcher(wrapper_pid):
    """The cleanup launcher: argv0 basename codex-linux-sandbox, parent the wrapper (or the wrapper itself after an
    exec), with a bwrap child. Returns (Ident or None, structure)."""
    t = tree(wrapper_pid)
    structure = {'members': len(t), 'roles': sorted(role(pid) for pid, _, _ in t),
                 'wrapper_is_launcher': role(wrapper_pid) == 'launcher'}
    candidates = []
    for pid, parent, st in t:
        if role(pid) != 'launcher':
            continue
        if not (pid == wrapper_pid or parent == wrapper_pid):
            continue
        if not any(role(c) == 'bwrap' for c in children(pid)):
            continue
        candidates.append((pid, st))
    structure['launcher_candidates'] = len(candidates)
    bw = [pid for pid, _, _ in t if role(pid) == 'bwrap']
    kinds = sorted({bwrap_kind(exe(pid)) for pid in bw})
    structure['bwrap'] = kinds
    structure['bwrap_identified'] = len(kinds) == 1 and kinds[0] in ('system', 'bundled')
    if len(candidates) != 1:
        return None, structure
    ident = Ident(*candidates[0])
    return (ident if ident.ok() else None), structure


def norm_entry(e):
    """(normalized path, access) of one serialized profile entry; fixed names only."""
    if not isinstance(e, dict):
        return ('?entry', None)
    path, acc = e.get('path'), e.get('access')
    if not isinstance(path, dict):
        return ('?path', acc)
    t = path.get('type')
    if t == 'path' and isinstance(path.get('path'), str):
        return (path['path'], acc)
    if t == 'special' and isinstance(path.get('value'), dict):
        v = path['value']
        sub = v.get('subpath')
        return (':' + str(v.get('kind')) + ('' if sub in (None, '', '.') else '/' + str(sub)), acc)
    return ('?' + str(t), acc)


def profile_check(lpid, cwd):
    """The launcher's own argv against the expected native profile and the case root. Returns a dict of booleans and
    counts with 'ok' and the measured 'cwd_is_root'."""
    a = argv(lpid)

    def opt(name):
        return a[a.index(name) + 1] if name in a[:-1] else None

    spc, ccwd, prof = opt('--sandbox-policy-cwd'), opt('--command-cwd'), opt('--permission-profile')
    r = {'policy_cwd_is_root': spc == cwd, 'command_cwd_is_root': ccwd == cwd, 'profile_arg': prof is not None,
         'legacy_landlock': '--use-legacy-landlock' in a, 'managed_network': '--managed-network' in a}
    try:
        p = json.loads(prof) if prof is not None else None
    except ValueError:
        p = None
    fs = p.get('file_system') if isinstance(p, dict) else None
    entries = fs.get('entries') if isinstance(fs, dict) else None
    r['shape'] = (isinstance(p, dict) and p.get('type') == 'managed' and p.get('network') == 'restricted'
                  and isinstance(fs, dict) and fs.get('type') == 'restricted' and isinstance(entries, list))
    norm = [norm_entry(e) for e in entries] if isinstance(entries, list) else []
    writes = [k for k, acc in norm if acc == 'write']
    rest = sorted((k, acc) for k, acc in norm if acc != 'write')
    resolved = [spc if k == ':project_roots' else k for k in writes]
    r['entries'] = len(norm)
    r['write_entries'] = len(writes)
    r['runtime_root_entry'] = rest == PROFILE_FIXED_WITH_RUNTIME
    r['fixed_entries_match'] = rest == PROFILE_FIXED or r['runtime_root_entry']
    r['missing_fixed'] = len([x for x in PROFILE_FIXED if x not in rest])
    extra = [x for x in rest if x not in PROFILE_FIXED_WITH_RUNTIME]
    r['extra_entries'] = len(extra)
    # Only fixed names are reported for extras: the special kind, or the top directory class of a path.
    r['extra_kinds'] = sorted({(k.split('/')[0] if k.startswith(':') else
                                'path:/state' if k.startswith('/state/') or k == '/state' else
                                'path:/tmp' if k.startswith('/tmp/') or k == '/tmp' else 'path:other') + ':' + str(acc)
                               for k, acc in extra})
    r['entry_extra_keys'] = sum(1 for e in (entries or []) if isinstance(e, dict)
                                and set(e) - {'path', 'access', 'missing_path_behavior'})
    r['cwd_is_root'] = bool(r['policy_cwd_is_root'] and r['command_cwd_is_root'] and resolved == [cwd])
    r['ok'] = bool(r['profile_arg'] and r['shape'] and r['fixed_entries_match'] and r['extra_entries'] == 0
                   and r['entry_extra_keys'] == 0 and r['cwd_is_root'] and not r['legacy_landlock']
                   and not r['managed_network'])
    return r


# --- filesystem observation -------------------------------------------------------------------------------------
def observe(root):
    """The four protected names in root. An lstat error other than 'not found' raises (an observation error)."""
    result = {}
    for name in NAMES:
        p = os.path.join(root, name)
        try:
            s = os.lstat(p)
        except FileNotFoundError:
            result[name] = {'exists': False}
            continue
        kind = 'dir' if stat.S_ISDIR(s.st_mode) else 'file' if stat.S_ISREG(s.st_mode) else \
            'symlink' if stat.S_ISLNK(s.st_mode) else 'other'
        empty = None
        if kind == 'dir':
            try:
                empty = not os.listdir(p)
            except OSError:
                empty = None
        result[name] = {'exists': True, 'type': kind, 'empty': empty, 'ino': s.st_ino, 'mtime_ns': s.st_mtime_ns,
                        'uid': s.st_uid, 'mode': oct(stat.S_IMODE(s.st_mode))}
    return result


def all_present(obs):
    return all(obs[n]['exists'] for n in NAMES)


def none_present(obs):
    return not any(obs[n]['exists'] for n in NAMES)


def snapshot():
    """(entries, observation error count, error kinds). A walk or lstat error is counted, never skipped silently;
    round 4: the count is also kept per kind as '<where>:<errno name>' (where: walk, lstat, identity)."""
    snap, kinds = {}, {}

    def note(where, e):
        k = '%s:%s' % (where, errname(e))
        kinds[k] = kinds.get(k, 0) + 1

    for top in SNAP_ROOTS:
        for dirpath, dirnames, filenames in os.walk(top, onerror=lambda e: note('walk', e), followlinks=False):
            for name in dirnames + filenames:
                p = os.path.join(dirpath, name)
                try:
                    s = os.lstat(p)
                except OSError as e:
                    note('lstat', e)
                    continue
                snap[p] = (stat.S_IFMT(s.st_mode), s.st_ino, s.st_size, s.st_mtime_ns)
    for p in IDENTITY_FILES:
        try:
            s = os.lstat(p)
            # round 4: the file type leads the tuple as for the other entries (the change detail reports it).
            snap['id:' + p] = (stat.S_IFMT(s.st_mode), s.st_ino, s.st_size, s.st_mtime_ns)
        except OSError as e:
            note('identity', e)
    return snap, sum(kinds.values()), kinds


def under(p, d):
    return p == d or p.startswith(d + '/')


class Phase:
    """The exact changes a phase may make: its roots (with fixed control file names), its TMPDIRs (the directory and
    the Codex registry below it), the entries CODEX_HOME and CODEX_HOME/tmp, and the tmp/arg0 subtree."""

    def __init__(self, roots, tmpdirs, files):
        self.roots, self.tmpdirs, self.files = list(roots), list(tmpdirs), set(files)

    def category(self, p):
        """None if allowed, else a fixed category name."""
        if p.startswith('id:'):
            return 'identity'
        if p in (CODEX_HOME, CODEX_TMP) or under(p, CODEX_TMP + '/arg0'):
            return None
        for t in self.tmpdirs:
            if p == t or under(p, t + '/' + REGISTRY):
                return None
        for r in self.roots:
            if p == r:
                return None
            if p.startswith(r + '/'):
                rel = p[len(r) + 1:]
                return None if (rel in NAMES or rel in self.files) else 'root_other'
        for t in self.tmpdirs:
            if p.startswith(t + '/'):
                return 'tmpdir_other'
        for prefix, name in ((CODEX_HOME, 'codex_home_other'), (HOME, 'home'), ('/control', 'control'),
                             ('/state', 'state_other'), ('/tmp', 'tmp_other'), ('/dev/shm', 'dev_shm')):
            if under(p, prefix):
                return name
        return 'other'

    def prefix(self, p, cat):
        """The directory a category is counted below (for the detail's depth); category() itself is unchanged."""
        if cat == 'root_other':
            return next(r for r in self.roots if p.startswith(r + '/'))
        if cat == 'tmpdir_other':
            return next(t for t in self.tmpdirs if p.startswith(t + '/'))
        return {'codex_home_other': CODEX_HOME, 'home': HOME, 'control': '/control', 'state_other': '/state',
                'tmp_other': '/tmp', 'dev_shm': '/dev/shm'}.get(cat, '/')


# Round 4 observation detail for changes that are NOT allowed (the allowance itself is unchanged). Each one is
# described by fixed fields only: added/removed/changed, the file type before and after, which of the snapshot values
# differ, the category, the depth below the category's directory, and its basename (and, deeper than one level, the
# first name below that directory) as a name from CANDIDATES on an exact, whole-name match, else only the sha256 of
# the name. A candidate match says only that the name is equal to one this harness already knows; it is not evidence
# of which process made the entry, or whether that was inside or outside the sandbox. A hash identifies neither the
# path nor its origin; it only lets two runs be compared. No path text leaves the Pod.
CANDIDATES = frozenset(
    set(NAMES) | {REGISTRY, os.path.basename(DAEMON_ROOT), 'home', 'codex', 'tmp', 'arg0', 'gate', 'g1',
                  'gate-result'}
    | {'c%s' % i for i in ('1', '2', '3', '4', '5', '6', '7', '8', '8a', '8b')}
    | {n + i for i in ('gate', '1', '2', '3', '4', '5', '6', '7a', '7b', '8a', '8b')
       for n in ('probe-', 'ready-', 'go-', 'parent-probe-')})
DETAIL_MAX = 20
FTYPES = {stat.S_IFREG: 'file', stat.S_IFDIR: 'dir', stat.S_IFLNK: 'symlink'}


def ftype(v):
    return 'absent' if v is None else FTYPES.get(v[0], 'other')


def name_field(name):
    """Exact candidate name, else the sha256 of the name's filesystem bytes (review r4: os.fsencode keeps a
    non-UTF-8 name's original bytes from os.walk's surrogateescape; str.encode() would raise on it)."""
    return {'name': name} if name in CANDIDATES else {'name_sha256': hashlib.sha256(os.fsencode(name)).hexdigest()}


def detail(p, cat, b, a, phase):
    d = {'change': 'added' if b is None else 'removed' if a is None else 'changed', 'before': ftype(b),
         'after': ftype(a), 'category': cat}
    if b is not None and a is not None:
        d['differs'] = [f for f, i in (('type', 0), ('inode', 1), ('size', 2), ('mtime', 3)) if b[i] != a[i]]
    if cat == 'identity':
        d['identity_file'] = p[3:]  # one of the fixed IDENTITY_FILES
        return d
    base = phase.prefix(p, cat)
    rel = p[len(base):].lstrip('/').split('/')
    d['depth'] = len(rel)
    d.update(name_field(rel[-1]))
    if len(rel) > 1:
        d.update({'top_' + k: v for k, v in name_field(rel[0]).items()})
    return d


def changes(before, after, phase):
    """(changed count, categories, details): details describe the disallowed changes only (round 4), at most
    DETAIL_MAX in path order, with the rest counted as omitted."""
    changed = sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))
    cats, details = {}, []
    for p in changed:
        c = phase.category(p)
        if c is not None:
            cats[c] = cats.get(c, 0) + 1
            details.append(detail(p, c, before.get(p), after.get(p), phase))
    return len(changed), cats, {'unexpected_detail': details[:DETAIL_MAX],
                                'unexpected_detail_omitted': max(0, len(details) - DETAIL_MAX)}


def protected_elsewhere(snap, roots):
    """Entries named like a protected path outside the roots used so far (anywhere in the snapshot)."""
    return sum(1 for p in snap if not p.startswith('id:') and os.path.basename(p) in NAMES
               and os.path.dirname(p) not in roots)


def control_files(*ids):
    return [n + i for i in ids for n in ('probe-', 'ready-', 'go-')]


# --- launches ---------------------------------------------------------------------------------------------------
def launch(cwd, tmpdir, script, arg):
    os.makedirs(tmpdir, exist_ok=True)
    env = dict(ENV, TMPDIR=tmpdir)
    proc = subprocess.Popen([CODEX, 'sandbox', '-P', 'native', '-C', cwd, '--', PYTHON, '-I', '-c', script, arg],
                            env=env, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    wrapper = Ident(proc.pid, starttime(proc.pid))
    return proc, wrapper


def wait_ready(cwd, ident, proc):
    """Barrier: the waiter's ready file (written after bwrap set the sandbox up), then up to PATHS_S for the four
    mount targets. Their absence is a result (Q1 not met), not a harness failure; the record says which."""
    deadline = time.monotonic() + READY_S
    while time.monotonic() < deadline:
        if os.path.exists(os.path.join(cwd, 'ready-' + ident)):
            until = time.monotonic() + PATHS_S
            while time.monotonic() < until and not all_present(observe(cwd)):
                time.sleep(0.05)
            return True
        if proc.poll() is not None:
            return False
        time.sleep(0.05)
    return False


def set_subreaper():
    """Make this harness the child subreaper (PR_SET_CHILD_SUBREAPER), so descendants orphaned by a killed launcher
    are reparented here and can be reaped (PID 1 of the Pod is `sleep infinity`, which never reaps)."""
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        return libc.prctl(36, 1, 0, 0, 0) == 0
    except (OSError, AttributeError, ImportError):
        return False


def ppid(pid):
    try:
        return int(stat_fields(pid)[1])
    except (OSError, ValueError, IndexError):
        return None


def strays():
    """After a case (every launch of it already waited for): (live, exited, reaped).

    live: launch processes still running outside any wrapper tree (launcher, bwrap, wrapper, or the waiter); each is
    pinned and killed, and a non-zero count stops the run, as before. exited: processes that already ended but were
    not reaped (state Z or X); they are never signalled and never counted as running leftovers (round 2). Those whose
    parent is this harness (direct children, or orphans reparented to it as subreaper) are reaped with waitpid on
    their own PID; exited counts what is still unreaped after that (for example under the Pod's PID 1)."""
    live = reaped = 0
    me = os.getpid()
    zombies = []
    for entry in os.listdir('/proc'):
        if not entry.isdigit() or int(entry) in (1, me):
            continue
        pid = int(entry)
        st = pstate(pid)
        if st is None:
            continue
        if st in ('Z', 'X'):
            zombies.append(pid)
            continue
        a = argv(pid)
        r = role(pid)
        if r in ('launcher', 'bwrap', 'wrapper') or (r == 'python' and WAITER in a):
            i = Ident(pid, starttime(pid))
            if i.ok():
                live += 1
                i.send(signal.SIGKILL)
                i.gone(5)
                i.close()
    for pid in zombies:
        if ppid(pid) == me:
            try:
                if os.waitpid(pid, os.WNOHANG)[0] == pid:
                    reaped += 1
            except ChildProcessError:
                pass
    exited = sum(1 for pid in zombies if pstate(pid) in ('Z', 'X'))
    return live, exited, reaped


def wait_exit(proc):
    try:
        return proc.wait(timeout=EXIT_S)
    except subprocess.TimeoutExpired:
        return None


def reap_tree(proc, wrapper):
    """Recovery: kill whatever is left of a launch tree (pidfd-pinned), then reap the wrapper. Returns the count."""
    left = 0
    if wrapper.ok() and proc.poll() is None:
        for pid, _, st in reversed(tree(proc.pid)):
            i = Ident(pid, st)
            if i.ok():
                left += 1
                i.send(signal.SIGKILL)
                i.gone(5)
                i.close()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass
    wrapper.close()
    return left


def probe_value(cwd, ident):
    try:
        with open(os.path.join(cwd, 'probe-' + ident)) as f:
            return f.read().strip()
    except OSError:
        return None


def failed(rec, why, proc, wrapper, *pins):
    """Recovery and harness_failed: the case is not evaluated."""
    for i in pins:
        if i is not None:
            i.send(signal.SIGKILL)
            i.gone(5)
            i.close()
    return dict(rec, result='harness_failed', why=why, left=reap_tree(proc, wrapper))


def premises(rec, prefix, cwd, ident, proc):
    """The launch premises before any action (round 1): launcher identified and pinned, bwrap identified, the
    profile and its root measured equal to the case root, the waiter's parent write refused and its marker absent
    outside the sandbox. Returns (launcher Ident or None, why or None)."""
    launcher, structure = find_launcher(proc.pid)
    rec[prefix + 'structure'] = structure
    if launcher is None:
        return None, 'launcher not identified or not pinned'
    prof = profile_check(launcher.pid, cwd)
    rec[prefix + 'profile'] = prof
    rec[prefix + 'cwd_is_root'] = prof['cwd_is_root']
    pw = probe_value(cwd, ident)
    marker = os.path.lexists(os.path.join(os.path.dirname(cwd), 'parent-probe-' + ident))
    rec[prefix + 'parent_write'] = pw
    rec[prefix + 'parent_marker_outside'] = marker
    if not structure['bwrap_identified']:
        return launcher, 'bwrap not identified'
    if not prof['ok']:
        return launcher, 'profile or root not as expected'
    if pw != '0' or marker:
        return launcher, 'parent write not refused'
    return launcher, None


# --- phases -----------------------------------------------------------------------------------------------------
def environment():
    rw, masks, other = [], [], []
    fstypes = {}
    with open('/proc/self/mountinfo') as f:
        for line in f:
            left, right = line.split(' - ', 1)
            fields, rfields = left.split(), right.split()
            mountpoint, options, fstype = fields[4], fields[5].split(','), rfields[0]
            fstypes[mountpoint] = fstype
            if 'rw' not in options:
                continue
            if mountpoint in ALLOWED_RW:
                rw.append(mountpoint)
            elif mountpoint.startswith('/proc/') and fstype in MASK_FSTYPES:
                masks.append(mountpoint)
            else:
                other.append(mountpoint)
    volumes = {}
    for d in ('/state', '/control', '/tmp'):
        s = os.stat(d)
        volumes[d] = {'uid': s.st_uid, 'gid': s.st_gid, 'mode': oct(stat.S_IMODE(s.st_mode)),
                      'fstype': fstypes.get(d), 'writable': os.access(d, os.W_OK), 'rw_mount': d in rw}
    status = open('/proc/self/status').read().splitlines()
    pick = lambda key: next((l.split()[1] for l in status if l.startswith(key + ':')), None)
    try:
        label = open('/proc/self/attr/current').read().strip('\0\n')
    except OSError:
        label = None
    parts = label.split(':') if label else []
    sel_type = parts[2] if len(parts) >= 3 else None
    checks = {
        'uid': os.getuid() == UID and os.geteuid() == UID,
        'gid': os.getgid() == GID and os.getegid() == GID,
        'no_new_privs': pick('NoNewPrivs') == '1',
        'seccomp_filter': pick('Seccomp') == '2',
        'no_caps': pick('CapEff') == '0000000000000000' and pick('CapPrm') == '0000000000000000',
        'selinux_container_t': sel_type == 'container_t',
        'rw_mounts_allowed': not other,
        'volumes_writable': all(v['writable'] and v['rw_mount'] for v in volumes.values()),
        'codex': os.path.exists(CODEX), 'python': os.path.exists(PYTHON),
    }
    out('env', checks=checks, rw_mounts=sorted(rw), proc_masks=len(masks), unexpected_rw_mounts=len(other),
        volumes=volumes, selinux_type=sel_type, seccomp=pick('Seccomp'), no_new_privs=pick('NoNewPrivs'),
        python=sys.version.split()[0], bwrap_on_path=os.path.exists('/usr/bin/bwrap'))
    return all(checks.values())


def gate():
    root, tmpdir = '/state/gate', '/tmp/gate'
    os.makedirs(root)
    accepts = []
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(8)
    port = listener.getsockname()[1]

    def serve():
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            accepts.append(1)
            conn.close()

    threading.Thread(target=serve, daemon=True).start()
    control = subprocess.run([PYTHON, '-I', '-c', CLIENT, str(port)], env=ENV, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    time.sleep(0.2)
    control_accepts = len(accepts)
    marker_before = os.path.isfile(MARKER)
    proc, wrapper = launch(root, tmpdir, GATE, str(port))
    ready = False
    deadline = time.monotonic() + READY_S
    while time.monotonic() < deadline:
        if os.path.exists(os.path.join(root, 'ready-gate')):
            ready = True
            break
        if proc.poll() is not None:
            break
        time.sleep(0.05)
    g5 = {'ready': ready, 'wrapper_pinned': wrapper.ok()}
    during = observe(root)
    prof = {}
    launcher = None
    if ready and wrapper.ok():
        launcher, structure = find_launcher(proc.pid)
        g5.update(structure)
        g5['launcher_found'] = launcher is not None
        if launcher is not None:
            prof = profile_check(launcher.pid, root)
        sandboxed = [pid for pid, _, _ in tree(proc.pid) if role(pid) == 'python']
        try:
            ns_in = os.readlink('/proc/%d/ns/net' % sandboxed[-1]) if sandboxed else None
            g5['netns'] = 'same' if ns_in == os.readlink('/proc/self/ns/net') else 'different'
        except OSError:
            g5['netns'] = 'unreadable'
        if launcher:
            launcher.close()
    g5['profile'] = prof
    open(os.path.join(root, 'go-gate'), 'w').close()
    rc = wait_exit(proc)
    if rc is None:
        reap_tree(proc, wrapper)
    else:
        wrapper.close()
    time.sleep(0.2)
    listener.close()
    try:
        with open(os.path.join(root, 'gate-result')) as f:
            result = json.load(f)
    except (OSError, ValueError):
        result = {}
    if not isinstance(result, dict):
        result = {}
    after = observe(root)
    g1_file = os.path.join(root, 'g1')
    g1 = False
    if result.get('g1') is True and os.path.isfile(g1_file):
        with open(g1_file) as f:
            g1 = f.read() == '1'
    # G2 counts only when .codex existed during the launch (the protected mount) and the refusal errno is expected.
    g2 = (during['.codex']['exists'] and result.get('g2') in G2_ERRNO
          and not os.path.lexists(os.path.join(root, '.codex', 'x')))
    g3 = marker_before and result.get('g3') in G3_ERRNO and os.path.isfile(MARKER)
    g4_ms = result.get('g4_ms')
    g4 = (control.returncode == 0 and control_accepts == 1 and result.get('g4') in G4_ERRNO
          and isinstance(g4_ms, int) and g4_ms < G4_MAX_MS and len(accepts) == control_accepts)
    g5ok = bool(ready and g5.get('wrapper_pinned') and g5.get('launcher_found') and g5.get('bwrap_identified')
                and prof.get('ok') and rc == 0)
    out('gate', g1=g1, g2=g2, g3=g3, g4=g4, g5=g5ok, g1_value=result.get('g1'), g2_value=result.get('g2'),
        g3_value=result.get('g3'), g4_value=result.get('g4'), g4_stage=result.get('g4_stage'), g4_ms=g4_ms,
        g4_control_rc=control.returncode, g4_control_accepts=control_accepts, g4_total_accepts=len(accepts),
        g5_detail=g5, wrapper_rc=rc, during_all_present=all_present(during), after_none_present=none_present(after))
    return g1 and g2 and g3 and g4 and g5ok


def case_dir(n):
    d = '/state/c%s' % n
    os.makedirs(d, exist_ok=True)
    return d


def single(n, cwd, tmpdir, action, ident=None):
    """One launch: ready, the premises, the action, the end, the observation. Returns the record."""
    ident = ident or str(n)
    rec = {'case': n, 'action': action}
    proc, wrapper = launch(cwd, tmpdir, WAITER, ident)
    if not wrapper.ok():
        return failed(rec, 'wrapper not pinned', proc, wrapper)
    if not wait_ready(cwd, ident, proc):
        return failed(rec, 'not ready', proc, wrapper)
    rec['during'] = observe(cwd)
    launcher, why = premises(rec, '', cwd, ident, proc)
    if why:
        return failed(rec, why, proc, wrapper, launcher)
    t0 = now()
    if action == 'go':
        launcher.close()
        open(os.path.join(cwd, 'go-' + ident), 'w').close()
    elif action in ('term', 'kill'):
        sent = launcher.send(signal.SIGTERM if action == 'term' else signal.SIGKILL)
        rec['launcher_signalled'] = sent
        rec['launcher_gone'] = sent and launcher.gone(EXIT_S)
        if not rec['launcher_gone']:
            return failed(rec, 'launcher not gone after its signal', proc, wrapper, launcher)
        launcher.close()
    elif action == 'stopkill':
        rec['launcher_stop_sent'] = launcher.send(signal.SIGSTOP)
        deadline = time.monotonic() + STOP_S
        while time.monotonic() < deadline and pstate(launcher.pid) != 'T':
            time.sleep(0.01)
        rec['launcher_stopped'] = pstate(launcher.pid) == 'T'
        if not (rec['launcher_stop_sent'] and rec['launcher_stopped']):
            return failed(rec, 'stop not confirmed; tree killed for recovery', proc, wrapper, launcher)
        stable, members = False, None
        for _ in range(5):
            first = tree(proc.pid)
            time.sleep(0.2)
            second = tree(proc.pid)
            if first == second:
                stable, members = True, second
                break
        rec['tree_stable'] = stable
        if not stable:
            return failed(rec, 'tree not stable; killed for recovery', proc, wrapper, launcher)
        below = [(pid, st) for pid, parent, st in members if pid not in (proc.pid, launcher.pid)]
        rec['below_members'] = len(below)
        pinned = [Ident(pid, st) for pid, st in reversed(below)]
        rec['pinned'] = sum(1 for i in pinned if i.ok())
        if not below or rec['pinned'] != len(below):
            return failed(rec, 'tree empty below the launcher or not every member pinned', proc, wrapper,
                          launcher, *pinned)
        rec['still_stopped_before_kill'] = pstate(launcher.pid) == 'T'
        if not rec['still_stopped_before_kill']:
            return failed(rec, 'launcher not stopped before the kills', proc, wrapper, launcher, *pinned)
        rec['below_signalled'] = all([i.send(signal.SIGKILL) for i in pinned])
        rec['below_gone'] = all([i.gone(5) for i in pinned])
        rec['launcher_still_stopped'] = pstate(launcher.pid) == 'T'
        for i in pinned:
            i.close()
        if not (rec['below_signalled'] and rec['below_gone'] and rec['launcher_still_stopped']):
            return failed(rec, 'members not all gone or launcher resumed before its kill', proc, wrapper, launcher)
        rec['launcher_signalled'] = launcher.send(signal.SIGKILL)
        rec['launcher_gone'] = rec['launcher_signalled'] and launcher.gone(5)
        if not rec['launcher_gone']:
            return failed(rec, 'launcher not gone after SIGKILL', proc, wrapper, launcher)
        launcher.close()
    rc = wait_exit(proc)
    rec['wrapper_rc_raw'] = rc
    rec['end_ms'] = (now() - t0) // 1000000
    if rc is None:
        return failed(rec, 'wrapper did not end', proc, wrapper)
    wrapper.close()
    if action == 'go' and rc != 0:
        rec['result'], rec['why'] = 'harness_failed', 'go ended with wrapper rc not 0'
        return rec
    time.sleep(0.2)
    rec['after'] = observe(cwd)
    return rec


def judge(rec, expect_remaining):
    if rec.get('result'):
        return rec
    during_ok = all_present(rec['during'])
    after = rec['after']
    met = during_ok and (all_present(after) if expect_remaining else none_present(after))
    rec['result'] = 'met' if met else 'not_met'
    rec['prediction'] = 'remain' if expect_remaining else 'removed'
    return rec


def concurrent(n, cwd, tmp_a, tmp_b, expect_remaining):
    rec = {'case': n, 'action': 'go', 'shared_tmpdir': tmp_a == tmp_b}
    ia, ib = n + 'a', n + 'b'
    pa, wa = launch(cwd, tmp_a, WAITER, ia)
    if not (wa.ok() and wait_ready(cwd, ia, pa)):
        return failed(rec, 'A not ready', pa, wa)
    la, why = premises(rec, 'a_', cwd, ia, pa)
    if why:
        return failed(rec, 'A: ' + why, pa, wa, la)
    la.close()
    t_a = now()
    rec['a_ready'] = observe(cwd)
    pb, wb = launch(cwd, tmp_b, WAITER, ib)
    if not (wb.ok() and wait_ready(cwd, ib, pb)):
        rec = failed(rec, 'B not ready', pb, wb)
        rec['left'] += reap_tree(pa, wa)
        return rec
    lb, why = premises(rec, 'b_', cwd, ib, pb)
    if why:
        rec = failed(rec, 'B: ' + why, pb, wb, lb)
        rec['left'] += reap_tree(pa, wa)
        return rec
    lb.close()
    rec['b_ready_ms'] = (now() - t_a) // 1000000
    rec['b_ready'] = observe(cwd)
    open(os.path.join(cwd, 'go-' + ia), 'w').close()
    rc_a = wait_exit(pa)
    rec['a_rc_raw'] = rc_a
    if rc_a is None or rc_a != 0:
        rec = failed(rec, 'A did not end with rc 0', pa, wa)
        rec['left'] += reap_tree(pb, wb)
        return rec
    wa.close()
    rec['a_ended_ms'] = (now() - t_a) // 1000000
    time.sleep(0.2)
    rec['after_a'] = observe(cwd)
    open(os.path.join(cwd, 'go-' + ib), 'w').close()
    rc_b = wait_exit(pb)
    rec['b_rc_raw'] = rc_b
    if rc_b is None or rc_b != 0:
        return failed(rec, 'B did not end with rc 0', pb, wb)
    wb.close()
    rec['b_ended_ms'] = (now() - t_a) // 1000000
    time.sleep(0.2)
    rec['after'] = observe(cwd)
    rec['during'] = rec['b_ready']
    return judge(rec, expect_remaining)


# --- round 5 (C2): the shared daemon root precondition --------------------------------------------------------
def daemon_obs():
    """lstat of DAEMON_ROOT as fixed fields: exists, type, uid, gid, full st_mode (octal), inode, empty; an error
    other than 'not found' is recorded as its errno name (an observation error)."""
    try:
        s = os.lstat(DAEMON_ROOT)
    except FileNotFoundError:
        return {'exists': False}
    except OSError as e:
        return {'error': errname(e)}
    o = {'exists': True, 'type': FTYPES.get(stat.S_IFMT(s.st_mode), 'other'), 'uid': s.st_uid, 'gid': s.st_gid,
         'mode': oct(s.st_mode), 'ino': s.st_ino}
    if stat.S_ISDIR(s.st_mode):
        try:
            o['empty'] = not os.listdir(DAEMON_ROOT)
        except OSError as e:
            o['error'] = errname(e)
    return o


def daemon_prepare():
    """(ok, record). Refuses a path that already exists or cannot be looked up (nothing is changed then; its
    inode is not reported, the harness did not make it). Else creates exactly DAEMON_ROOT with mode 0700 and
    requires: a directory, owned by the effective uid and gid, st_mode exactly S_IFDIR|0700, empty. A path made but
    not as required is left as it is (no chmod) and the run stops."""
    pre = daemon_obs()
    if 'error' in pre or pre.get('exists'):
        return False, {'name': os.path.basename(DAEMON_ROOT), 'created': False, 'pre_existing': pre.get('exists'),
                       'before': {k: pre[k] for k in ('exists', 'type', 'uid', 'gid', 'mode', 'empty', 'error')
                                  if k in pre}}
    try:
        os.mkdir(DAEMON_ROOT, 0o700)
    except OSError as e:
        return False, {'name': os.path.basename(DAEMON_ROOT), 'created': False, 'pre_existing': False,
                       'mkdir_error': errname(e)}
    o = daemon_obs()
    good = ('error' not in o and o.get('exists') is True and o.get('type') == 'dir' and o.get('uid') == os.geteuid()
            and o.get('gid') == os.getegid() and o.get('mode') == oct(DAEMON_MODE) and o.get('empty') is True)
    return good, {'name': os.path.basename(DAEMON_ROOT), 'created': True, 'pre_existing': False,
                  'as_required': good, 'state': o}


def daemon_check(ref):
    """Fixed fields comparing DAEMON_ROOT now with the state recorded when the harness made it."""
    o = daemon_obs()
    differs = [k for k in DAEMON_FIELDS if o.get(k) != ref.get(k)]
    return {'daemon_same': not differs and 'error' not in o, 'daemon_differs': differs,
            'daemon_error': o.get('error')}


def main():
    # Prepared before the baseline (round 1 blocker): HOME, CODEX_HOME and the deny marker for G3.
    for d in (HOME, CODEX_HOME):
        os.makedirs(d, mode=0o700, exist_ok=True)
    with open(MARKER, 'x') as f:
        f.write('fake')
    # Before any launch: orphans of a killed launcher come to this harness and are reaped by strays() (round 2).
    out('reaper', subreaper=set_subreaper())
    if not environment():
        out('summary', phase='environment', aborted=True)
        return 2
    # Round 5 (C2): the one shared daemon root, made here before the baseline, or the run stops (rc 4).
    daemon_ok, daemon_rec = daemon_prepare()
    out('daemon', **daemon_rec)
    if not daemon_ok:
        out('summary', phase='daemon_precondition', aborted=True)
        return 4
    daemon_ref = daemon_rec['state']
    base, err, err_kinds = snapshot()
    out('change', after='baseline', observation_errors=err, observation_error_kinds=err_kinds)
    if err:
        out('summary', phase='baseline', aborted=True)
        return 2
    roots = {'/state/gate'}
    ok = gate()
    live, exited, reaped = strays()
    snap, err, err_kinds = snapshot()
    count, cats, det = changes(base, snap, Phase(['/state/gate'], ['/tmp/gate'],
                                                 ['g1', 'gate-result', 'ready-gate', 'go-gate']))
    elsewhere = protected_elsewhere(snap, roots)
    dchk = daemon_check(daemon_ref)
    out('change', after='gate', changed=count, unexpected=sum(cats.values()), categories=cats,
        observation_errors=err, observation_error_kinds=err_kinds, protected_elsewhere=elsewhere, strays=live,
        exited_unreaped=exited, reaped=reaped, **det, **dchk)
    if not ok or cats or err or elsewhere or live:
        out('summary', phase='gate', aborted=True)
        return 2
    if not dchk['daemon_same']:  # round 5: an added stop; the rule above is unchanged
        out('summary', phase='gate', aborted=True, stopped_by='daemon_root')
        return 2
    plan = [
        ('1', Phase(['/state/c1'], ['/tmp/c1'], control_files('1')),
         lambda: judge(single('1', case_dir(1), '/tmp/c1', 'go'), False)),
        ('2', Phase(['/state/c2'], ['/tmp/c2'], control_files('2')),
         lambda: judge(single('2', case_dir(2), '/tmp/c2', 'go'), False)),
        ('3', Phase(['/state/c3'], ['/tmp/c3'], control_files('3')),
         lambda: judge(single('3', case_dir(3), '/tmp/c3', 'term'), False)),
        ('4', Phase(['/state/c4'], ['/tmp/c4'], control_files('4')),
         lambda: judge(single('4', case_dir(4), '/tmp/c4', 'kill'), True)),
        ('5', Phase(['/state/c4'], ['/tmp/c5'], control_files('5')), None),
        ('6', Phase(['/state/c6'], ['/tmp/c6'], control_files('6')),
         lambda: judge(single('6', case_dir(6), '/tmp/c6', 'stopkill'), True)),
        ('7', Phase(['/state/c7'], ['/tmp/c7'], control_files('7a', '7b')),
         lambda: concurrent('7', case_dir(7), '/tmp/c7', '/tmp/c7', False)),
        ('8', Phase(['/state/c8'], ['/tmp/c8a', '/tmp/c8b'], control_files('8a', '8b')),
         lambda: concurrent('8', case_dir(8), '/tmp/c8a', '/tmp/c8b', True)),
    ]
    results, case4, aborted = {}, None, None
    for n, phase, run in plan:
        before = snap
        roots |= set(phase.roots)
        if n == '5':
            left4 = case4 and case4.get('after') and any(case4['after'][x]['exists'] for x in NAMES)
            if not left4:
                rec = {'case': '5', 'result': 'precondition_unmet', 'why': 'case 4 left no protected path'}
            else:
                rec = single('5', '/state/c4', '/tmp/c5', 'go')
                if not rec.get('result'):
                    kept = [x for x in NAMES if case4['after'][x]['exists']]
                    same = all(rec['after'][x].get('ino') == case4['after'][x].get('ino') for x in kept)
                    rec['same_identity_as_case4'] = same
                    rec['result'] = 'met' if same and all(rec['after'][x]['exists'] for x in kept) else 'not_met'
                    rec['prediction'] = 'kept'
        else:
            rec = run()
        if n == '4':
            case4 = rec
        rec['strays'], rec['exited_unreaped'], rec['reaped'] = strays()
        snap, err, err_kinds = snapshot()
        count, cats, det = changes(before, snap, phase)
        rec['changed'], rec['unexpected'], rec['categories'] = count, sum(cats.values()), cats
        rec.update(det)
        rec['observation_errors'], rec['observation_error_kinds'] = err, err_kinds
        rec['protected_elsewhere'] = protected_elsewhere(snap, roots)
        rec.update(daemon_check(daemon_ref))
        out('case', **rec)
        results[n] = rec['result']
        if rec['result'] == 'harness_failed' or cats or err or rec['protected_elsewhere'] or rec['strays']:
            aborted = 'case %s' % n
            break
        if not rec['daemon_same']:  # round 5: an added stop; the rule above is unchanged
            aborted = 'case %s daemon_root' % n
            break
    out('summary', phase='cases', aborted=bool(aborted), stopped_at=aborted, results=results)
    return 3 if aborted else 0


if __name__ == '__main__':
    try:
        code = main()
    except Exception as e:  # an observation or harness error: recover launch processes, report the type only
        live, exited, reaped = strays()
        out('summary', phase='exception', aborted=True, error=type(e).__name__, strays=live,
            exited_unreaped=exited, reaped=reaped)
        code = 3
    sys.exit(code)
