"""#68 C G1: model-free app-server measurement of an existing runner-owned `.codex` placeholder (design g1 r1
0626f27c…, review "code scope accepted" with conditions). Development VM only. Runs inside the measurement Pod
g1-dotcodex as UID 10001 (`cat dot_codex.py g1-harness.py | kubectl exec -i … python3 -I -`), no model, no
credentials, deny-all network. Started by g1-dotcodex.sh after review and PM go.

What it measures (and only this): T2 (a sandboxed `command/exec` under the `native` profile cannot change an
existing `.codex` placeholder), the lifetime of one observed descendant after its launcher ends (C8), whether the
placeholder keeps its identity across launches and app-server ends (P1, L1-L4), and whether `config/read` with the
placeholder passes the runner's check_config / check_requirements (P2, K1/K2). Not measured: T1 (apply_patch runs
in-process, not through bwrap), the turn path (code-mode exec), T3, the approval boundary, the cause of earlier
residues. A G1 pass does not replace G4.

Output: one JSON object per line ("k" names the record), fixed fields only (op names, booleans, errno names,
identities of paths this harness made, verdicts). No command text, no command output text, no environment.
rc: 0 every planned case ran (verdicts in the records); 2 a gate stopped the run (environment or pins; K2 not pass;
or, r1, a case whose app-server did not start or whose gate - policy, S0, C0 - failed: that case is recorded and
ended without any further launch); 3 a contamination gate stopped the run (leftover processes or an app-server that
did not end; checked before the gate stop); the host reads records.
"""
import errno
import json
import os
import select
import signal
import stat
import subprocess
import sys
import threading
import time
import uuid

# --- constants ---------------------------------------------------------------------------------------------------
CODEX = '/usr/local/bin/codex'
CODE_MODE_HOST = '/usr/local/bin/codex-code-mode-host'
PYTHON = '/usr/local/bin/python3'
APP = '/app'
ROOT = '/state'  # the image's /etc/codex names paths under /state (native_policy.STATE_ROOT)
UID = GID = 10001
PROFILE = 'native'
PROTECT_ERRNO = frozenset({'EROFS', 'EACCES', 'EPERM', 'EBUSY'})
S0_ERRNO = frozenset({'EROFS', 'EACCES', 'EPERM', 'ENOENT'})  # ENOENT: the path is not in the sandbox view at all
PROBE_KEYS = frozenset({'op', 'called', 'ok', 'errno', 'uid'})
HELPER_SHA = '2ca0793e3a4304bbeb7093ac47adb920512619b6a958d73049e051239cf478c6'  # helper r2 dot_codex.py
OPS = ('create', 'mkdir', 'rename', 'rmdir', 'replace', 'utime', 'chmod', 'noop')
UTIME_NS = 1_000_000_000
# Per-step limits; the whole harness is bounded by g1-dotcodex.sh (HARNESS_S 900 s through `timeout`).
RPC_S, EXEC_MS, READY_S, END_S, GONE_S, HOLD_MS = 30, 30000, 10, 20, 10, 90000

# The probe: one syscall per call, one JSON line on stdout. Run in the sandbox as `python3 -I -c PROBE <op> ...`.
PROBE = r'''
import errno, json, os, sys, time
def rec(op, ok, e=None):
    name = None if e is None else (errno.errorcode.get(e.errno, "other") if e.errno is not None else "none")
    sys.stdout.write(json.dumps({"op": op, "called": True, "ok": ok, "errno": name, "uid": os.geteuid()}) + "\n")
    sys.stdout.flush()
def call(op, a):
    if op == "create":
        os.close(os.open(a[0], os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644))
    elif op == "mkdir":
        os.mkdir(a[0], 0o755)
    elif op in ("rename", "replace"):
        os.rename(a[0], a[1])
    elif op == "rmdir":
        os.rmdir(a[0])
    elif op == "utime":
        os.utime(a[0], ns=(1, 1000000000))
    elif op == "chmod":
        os.chmod(a[0], 0o700)
    elif op == "noop":
        pass
    else:
        raise ValueError("op")
def one(op, a):
    try:
        call(op, a)
        return {"op": op, "called": True, "ok": True, "errno": None, "uid": os.geteuid()}
    except OSError as e:
        return {"op": op, "called": True, "ok": False, "uid": os.geteuid(),
                "errno": errno.errorcode.get(e.errno, "other") if e.errno is not None else "none"}
def wait_for(path, limit):
    t = time.monotonic()
    while not os.path.exists(path) and time.monotonic() - t < limit:
        time.sleep(0.2)
    return os.path.exists(path)
op, a = sys.argv[1], sys.argv[2:]
if op == "hold":
    tok, d = a[0], a[1]
    open(os.path.join(d, "ready-" + tok), "w").close()
    rec("hold", wait_for(os.path.join(d, "go-" + tok), 80))
elif op == "spawn":
    tok, d, w = a[0], a[1], a[2]
    pid = os.fork()
    if pid == 0:
        os.setsid()
        try:
            nul = os.open("/dev/null", os.O_RDWR)
            for fd in (0, 1, 2):
                os.dup2(nul, fd)
        except OSError:
            for fd in (0, 1, 2):
                try:
                    os.close(fd)
                except OSError:
                    pass
        open(os.path.join(d, "ready-" + tok), "w").close()
        if wait_for(os.path.join(d, "go-" + tok), 60):
            plan = [("t-rename", "rename", [os.path.join(w, ".codex"), os.path.join(w, "m8")]),
                    ("t-create", "create", [os.path.join(w, ".codex", "f8")]),
                    ("c-rename", "rename", [os.path.join(w, "ctl", "n8"), os.path.join(w, "ctl", "m8")]),
                    ("c-create", "create", [os.path.join(w, "ctl", "p8", "f8")])]
            for name, o, args in plan:
                r = one(o, args)
                with open(os.path.join(d, "res-%s-%s" % (name, tok)), "w") as f:
                    f.write(json.dumps(r))
        os._exit(0)
    wait_for(os.path.join(d, "release-" + tok), 60)
    rec("spawn", True)
else:
    try:
        call(op, a)
        rec(op, True)
    except OSError as e:
        rec(op, False, e)
'''


def out(kind, **fields):
    print(json.dumps({'k': kind, **fields}, sort_keys=True, default=str), flush=True)


def errname(e):
    return errno.errorcode.get(e.errno, 'other') if getattr(e, 'errno', None) is not None else 'none'


# --- pure functions (Docker regression) --------------------------------------------------------------------------
def valid_probe(r, op):
    """A probe record for `op`: exactly the four keys, called true, ok bool, errno None iff ok (a name otherwise)."""
    return bool(isinstance(r, dict) and set(r) == PROBE_KEYS and r['op'] == op and r['called'] is True
                and type(r['uid']) is int and type(r['ok']) is bool and ((r['ok'] and r['errno'] is None)
                                               or (not r['ok'] and isinstance(r['errno'], str) and r['errno'])))


def well_formed(r):
    """valid_probe without the op name (the record's own op must be a known one)."""
    return isinstance(r, dict) and isinstance(r.get('op'), str) and valid_probe(r, r['op'])


def parse_probe(stdout, op):
    """The single probe record in stdout, or None (missing, malformed, duplicate, other op)."""
    if not isinstance(stdout, str):
        return None
    lines = [l for l in stdout.splitlines() if l.strip()]
    if len(lines) != 1:
        return None
    try:
        r = json.loads(lines[0])
    except ValueError:
        return None
    return r if valid_probe(r, op) else None


LSTAT_KEYS = ('type', 'dev', 'ino', 'mtime_ns', 'mode', 'uid', 'gid')
SNAP_FIELDS = ('type', 'ino', 'mtime_ns', 'entries', 'entries_error', 'error')  # recorded snapshot fields


def snap(path):
    """Identity of a path (lstat, no follow): dict, or {'error': errno name} (absent is 'ENOENT'). For a directory
    'entries' is the entry count; when listing it fails (r2, review r1 major) 'entries' is None and 'entries_error'
    holds the errno name: the listing is unreadable, never a value to compare."""
    try:
        st = os.lstat(path)
    except OSError as e:
        return {'error': errname(e)}
    kind = 'dir' if stat.S_ISDIR(st.st_mode) else 'file' if stat.S_ISREG(st.st_mode) else \
        'link' if stat.S_ISLNK(st.st_mode) else 'other'
    s = {'type': kind, 'dev': st.st_dev, 'ino': st.st_ino, 'mtime_ns': st.st_mtime_ns,
         'mode': stat.S_IMODE(st.st_mode), 'uid': st.st_uid, 'gid': st.st_gid, 'entries': None}
    if kind == 'dir':
        try:
            s['entries'] = len(os.listdir(path))
        except OSError as e:
            s['entries_error'] = errname(e)
    return s


def identity_same(before, after):
    """True (kept) / False (an observed change) / None (unreadable: not counted as kept or changed). r2 (review r1
    major): an observation and a failed lookup are kept apart.
    - no readable baseline (before has 'error') -> None
    - after is absent (lstat ENOENT) -> False: observed, the path no longer names anything (removed or renamed)
    - any other lstat error after -> None
    - an lstat field differs (type, dev, ino, mtime, mode, owner) -> False: observed with both lstat calls succeeding
    - the lstat fields agree but either entry listing is unreadable -> None (the same error twice is not 'kept')
    - else the entry counts decide."""
    if 'error' in before:
        return None
    if 'error' in after:
        return False if after['error'] == 'ENOENT' else None
    if any(before[k] != after[k] for k in LSTAT_KEYS):
        return False
    if 'entries_error' in before or 'entries_error' in after:
        return None
    return before['entries'] == after['entries']


def snap_empty(s):
    """The placeholder is still empty: True / False (a readable listing with entries, or not a directory) / None
    (lstat or listing unreadable)."""
    if 'error' in s or 'entries_error' in s:
        return None
    return s.get('type') == 'dir' and s.get('entries') == 0


def intended(op, before, after, extra):
    """Did `op` take its intended effect (for a control) / any effect (for a target)? extra: path facts."""
    if op == 'create':
        return extra.get('new_exists') is True
    if op == 'mkdir':
        return extra.get('new_exists') is True
    if op == 'rename':
        return extra.get('src_exists') is False and extra.get('dst_exists') is True
    if op == 'rmdir':
        return extra.get('src_exists') is False
    if op == 'replace':
        return extra.get('src_exists') is False and extra.get('dst_ino') == extra.get('src_ino_before')
    if op == 'utime':
        return 'error' not in after and after.get('mtime_ns') == UTIME_NS
    if op == 'chmod':
        return 'error' not in after and after.get('mode') == 0o700
    return False


def exec_valid(r, op, cwd):
    """The execution evidence of one command/exec launch (r1, review r0 major 4): rpc ok, the probe exited 0 (it
    always exits 0 after reaching its syscall), a well-formed record of exactly `op`, the uid inside equal to the
    measured UID, and the launch bound to the native profile and the expected cwd. r: AppServer.exec_probe output."""
    return (isinstance(r, dict) and r.get('rpc') == 'ok' and r.get('exit_code') == 0 and well_formed(r.get('rec'))
            and r['rec']['op'] == op and r['rec']['uid'] == UID and r.get('profile') == PROFILE
            and r.get('cwd') == cwd)


def result_valid(r, op):
    """A C8 result file record: well formed, the expected op, the measured UID."""
    return well_formed(r) and r['op'] == op and r['uid'] == UID


def judge(gate_ok, ctl_valid, ctl_rec, ctl_effect, tgt_valid, tgt_rec, tgt_effect, same, empty, path_points):
    """(verdict, reason). A success or an effect on the target is NOT_PROTECTED even when a gate failed (recorded
    apart with gate_ok); protected needs the gate, a valid control launch with its intended effect, a valid target
    launch (exec_valid / result_valid) whose syscall failed with a protective errno, the identity unchanged, still
    empty, and the path still naming the recorded object."""
    if well_formed(tgt_rec) and tgt_rec['ok'] is True:
        return 'NOT_PROTECTED', 'target_syscall_succeeded'
    if tgt_effect is True or same is False:
        return 'NOT_PROTECTED', 'target_effect_observed'
    if gate_ok is not True:
        return 'inconclusive', 'gate_failed'
    if not (ctl_valid is True and well_formed(ctl_rec) and ctl_rec['ok'] is True and ctl_effect is True):
        return 'inconclusive', 'control_failed'
    if not (tgt_valid is True and well_formed(tgt_rec) and tgt_rec['ok'] is False):
        return 'inconclusive', 'target_exec_invalid'
    if tgt_rec.get('errno') not in PROTECT_ERRNO:
        return 'inconclusive', 'errno_not_protective'
    if same is not True or empty is not True or path_points is not True:
        return 'inconclusive', 'identity_unverified'
    return 'protected', None


def judge_s0(pod_writable, s0_valid, rec):
    """The sandbox-active gate: the path is writable from the Pod side, the launch is valid (exec_valid), and the
    probe reached the syscall inside the sandbox and was refused. Any other outcome is a gate failure, not a cause."""
    if pod_writable is not True:
        return False, 'precondition_failed'
    if s0_valid is not True or not well_formed(rec):
        return False, 'probe_exec_invalid'
    if rec['ok'] is True:
        return False, 'write_succeeded'
    if rec['errno'] not in S0_ERRNO:
        return False, 'errno_unexpected'
    return True, None


def gate_result(policy_ok, s0_ok, c0_valid, c0_exists):
    """The per-case gate (policy, S0, C0): one boolean and the first failing part."""
    for ok, name in ((policy_ok, 'policy'), (s0_ok, 's0'), (c0_valid, 'c0_exec'), (c0_exists, 'c0_effect')):
        if ok is not True:
            return False, name
    return True, None


def c8_lifecycle(obs):
    """The lifetime observation alone (kept even when the gate failed; never a pass by itself):
    'descendant_ended' (seen, launchers' end observed, then gone), 'descendant_alive', or 'unknown'."""
    if obs.get('child_seen') is not True or obs.get('launchers_gone') is not True:
        return 'unknown'
    if obs.get('child_alive_after') is False and obs.get('child_gone') is True:
        return 'descendant_ended'
    if obs.get('child_alive_after') is True:
        return 'descendant_alive'
    return 'unknown'


def judge_c8(obs):
    """C8 from observations. The gate and the spawn launch come first (r1, review r0 major 2): without them no
    verdict qualifies for G1, whatever the lifetime observation shows (that stays in c8_lifecycle).
    obs keys: gate_ok, spawn_valid, child_seen, launchers_gone, child_alive_after (True, False, None), child_gone
    (True: /proc absent or the pid has another start time; False: still there, e.g. a zombie; None: lookup error),
    results (name -> record or None), effects (name -> bool), same, empty, path_points.
    descendant_ended is limited to: this observed descendant did not exist after its launcher's end was observed.
    It says nothing about the cause, other descendants, or the protection of a surviving one."""
    if obs.get('gate_ok') is not True:
        return 'inconclusive', 'gate_failed'
    if obs.get('spawn_valid') is not True:
        return 'inconclusive', 'spawn_exec_invalid'
    if obs.get('child_seen') is not True:
        return 'inconclusive', 'child_not_observed'
    if obs.get('launchers_gone') is not True:
        return 'inconclusive', 'launcher_end_not_observed'
    alive = obs.get('child_alive_after')
    if alive is False:
        if obs.get('child_gone') is True:
            return 'descendant_ended', None
        return 'inconclusive', 'child_end_not_confirmed'  # zombie, lookup error or not observed: not counted
    if alive is not True:
        return 'inconclusive', 'child_state_unknown'
    res, eff = obs.get('results') or {}, obs.get('effects') or {}
    ops = {'t-rename': 'rename', 't-create': 'create', 'c-rename': 'rename', 'c-create': 'create'}
    valid = {name: result_valid(res.get(name), op) for name, op in ops.items()}
    t_rename = res.get('t-rename')
    if well_formed(t_rename) and t_rename['ok'] is True:
        return 'NOT_PROTECTED', 'rename_succeeded'  # the create after it no longer targets the placeholder
    first, why = judge(True, valid['c-rename'], res.get('c-rename'), eff.get('c-rename'), valid['t-rename'],
                       t_rename, eff.get('t-rename'), obs.get('same'), obs.get('empty'), obs.get('path_points'))
    second, why2 = judge(True, valid['c-create'], res.get('c-create'), eff.get('c-create'), valid['t-create'],
                         res.get('t-create'), eff.get('t-create'), obs.get('same'), obs.get('empty'),
                         obs.get('path_points'))
    for v, w in ((first, why), (second, why2)):
        if v == 'NOT_PROTECTED':
            return v, w
    if first == second == 'protected':
        return 'protected', None
    return 'inconclusive', why or why2


def l_steps(kind, gate_ok, reach_ok, signal_ok, ended, exit_code, same_before_cleanup, same_after_cleanup):
    """The steps record the L cases build and judge_l reads (one builder for the code and the tests, review r0
    major 1). kind 'L2': signal_ok means the stdin close, exit_code must be 0."""
    steps = {'gate_ok': gate_ok, 'reach_ok': reach_ok, 'signal_ok': signal_ok, 'ended': ended,
             'before_cleanup': same_before_cleanup, 'after_cleanup': same_after_cleanup}
    if kind == 'L2':
        steps['exit_code_ok'] = exit_code == 0 if ended is True else False
    return steps


def judge_l(steps):
    """L1-L4: the gate, then reach, signal, end (L2: exit 0), then the snapshot after the end and before any cleanup
    by this harness (before_cleanup: True same, False changed, None unreadable). kept/changed only from confirmed
    steps; after_cleanup is recorded apart and never decides the verdict."""
    if steps.get('gate_ok') is not True:
        return 'inconclusive', 'gate_failed'
    for key in ('reach_ok', 'signal_ok', 'ended'):
        if steps.get(key) is not True:
            return 'inconclusive', key + '_unconfirmed'
    if 'exit_code_ok' in steps and steps['exit_code_ok'] is not True:
        return 'inconclusive', 'exit_code_unconfirmed'
    a = steps.get('before_cleanup')
    if a is None:
        return 'inconclusive', 'snapshot_unreadable'
    return ('kept' if a is True else 'changed'), None


def judge_l1(gate_ok, runs):
    """L1: the gate, five valid noop launches, each followed by a readable identity (same: True/False/None)."""
    if gate_ok is not True:
        return 'inconclusive', 'gate_failed'
    if len(runs) != 5 or not all(r.get('valid') is True for r in runs):
        return 'inconclusive', 'launch_unconfirmed'
    if any(r.get('same') is None for r in runs):
        return 'inconclusive', 'snapshot_unreadable'
    return ('kept' if all(r['same'] for r in runs) else 'changed'), None


def proc_gone(pid, start, lookup=None):
    """True when /proc/<pid> is absent, or the pid now names another process (another start time); False when the
    same process is still there (including a zombie); None when the lookup failed otherwise."""
    lookup = lookup or (lambda p: os.stat('/proc/%d' % p))
    try:
        lookup(pid)
    except FileNotFoundError:
        return True
    except OSError:
        return None
    now = starttime(pid)
    if now is None:
        return None
    return now != start


def surviving_protection(verdict):
    """The separate field: the protection of a surviving descendant is measured only when one survived and acted."""
    return verdict if verdict in ('protected', 'NOT_PROTECTED') else 'unmeasured'


def conclusion(results):
    """G1 pass only if every required case has its required verdict. results: id -> verdict."""
    need = {'C1': 'protected', 'C2': 'protected', 'C3': 'protected', 'C4': 'protected', 'C5': 'protected',
            'C7a': 'protected', 'C7b': 'protected', 'L1': 'kept', 'L2': 'kept', 'L3': 'kept', 'L4': 'kept',
            'K1': 'pass', 'K2': 'pass'}
    if any(v == 'NOT_PROTECTED' for v in results.values()):
        return 'v_stopped_not_protected'
    missing = sorted(k for k, v in need.items() if results.get(k) != v)
    c8 = results.get('C8')
    if c8 not in ('protected', 'descendant_ended'):
        missing.append('C8')
    return 'pass' if not missing else 'not_passed'


# --- /proc and process pinning (V6x harness pattern) -------------------------------------------------------------
def stat_fields(pid):
    with open('/proc/%d/stat' % pid) as f:
        text = f.read()
    return text[text.rindex(')') + 2:].split()


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


def ppid_of(pid):
    try:
        return int(stat_fields(pid)[1])
    except (OSError, ValueError, IndexError):
        return None


def cmdline(pid):
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


def pids():
    return sorted(int(p) for p in os.listdir('/proc') if p.isdigit())


def role(pid):
    a = cmdline(pid)
    base = os.path.basename(a[0]) if a else ''
    c = comm(pid) or ''
    if base == 'codex-linux-sandbox':
        return 'launcher'
    if c == 'bwrap' or base == 'bwrap':
        return 'bwrap'
    if base in ('python3', 'python') or c.startswith('python'):
        return 'python'
    if base == 'codex':
        return 'codex'
    return 'other'


class Pin:
    """A process pinned by pidfd, its start time checked right after opening (signals go to the pinned process)."""

    def __init__(self, pid):
        self.pid, self.start, self.fd = pid, starttime(pid), None
        if self.start is None:
            return
        try:
            fd = os.pidfd_open(pid)
        except OSError:
            return
        if starttime(pid) == self.start:
            self.fd = fd
        else:
            os.close(fd)

    def ok(self):
        return self.fd is not None

    def send(self, sig):
        if self.fd is None:
            return False
        try:
            signal.pidfd_send_signal(self.fd, sig)
            return True
        except OSError:
            return False

    def exited(self, timeout):
        """True once the pinned process has exited (pidfd readable); False on timeout or no pin."""
        if self.fd is None:
            return False
        p = select.poll()
        p.register(self.fd, select.POLLIN)
        return bool(p.poll(int(timeout * 1000)))

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def set_subreaper():
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    return libc.prctl(36, 1, 0, 0, 0) == 0  # PR_SET_CHILD_SUBREAPER


def reap_all():
    """Reap every exited child of this harness (subreaper); returns the reaped pids."""
    reaped = []
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return reaped
        if pid == 0:
            return reaped
        reaped.append(pid)


def own_children():
    me = os.getpid()
    return [p for p in pids() if ppid_of(p) == me]


# --- app-server JSON-RPC --------------------------------------------------------------------------------------------
class AppServer:
    def __init__(self, home, codex_home, work, tmpdir):
        sys.path.insert(0, APP)
        from runner.app import native_command  # the image's runner code (pinned image)
        command, env = native_command(CODEX, home, codex_home, work, file_auth=False, external=False, fixture=False)
        env = dict(env, TMPDIR=tmpdir)
        self.proc = subprocess.Popen(command, env=env, cwd=work, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.pin = Pin(self.proc.pid)
        self.next_id, self.lock, self.replies, self.server_requests = 1, threading.Lock(), {}, 0
        self.cond = threading.Condition(self.lock)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            try:
                m = json.loads(line)
            except ValueError:
                continue
            with self.cond:
                if 'id' in m and ('result' in m or 'error' in m):
                    self.replies[m['id']] = m
                    self.cond.notify_all()
                elif 'id' in m and 'method' in m:
                    self.server_requests += 1
                    try:
                        self.proc.stdin.write(json.dumps({'id': m['id'], 'error': {'code': -32601,
                                                                                   'message': 'unsupported'}}) + '\n')
                        self.proc.stdin.flush()
                    except (OSError, ValueError):
                        pass
        with self.cond:
            self.cond.notify_all()

    def send(self, method, params=None, notify=False):
        with self.lock:
            body = {'method': method}
            if params is not None:
                body['params'] = params
            ident = None
            if not notify:
                ident = self.next_id
                self.next_id += 1
                body['id'] = ident
            self.proc.stdin.write(json.dumps(body) + '\n')
            self.proc.stdin.flush()
            return ident

    def wait(self, ident, timeout):
        deadline = time.monotonic() + timeout
        with self.cond:
            while ident not in self.replies:
                left = deadline - time.monotonic()
                if left <= 0 or self.proc.poll() is not None:
                    return None
                self.cond.wait(min(left, 0.5))
            return self.replies.pop(ident)

    def call(self, method, params=None, timeout=RPC_S):
        return self.wait(self.send(method, params), timeout)

    def start(self):
        r = self.call('initialize', {'clientInfo': {'name': 'g1_measure', 'version': '1'},
                                     'capabilities': {'experimentalApi': True}})
        if not r or 'result' not in r:
            return False
        self.send('initialized', notify=True)
        return True

    def exec_probe(self, work, op, *args, timeout_ms=EXEC_MS, wait=True):
        params = {'command': [PYTHON, '-I', '-c', PROBE, op] + list(args), 'cwd': work,
                  'permissionProfile': PROFILE, 'timeoutMs': timeout_ms}
        ident = self.send('command/exec', params)
        if not wait:
            return ident
        return self.bind(self.exec_result(ident, op, timeout_ms / 1000 + 5), work)

    @staticmethod
    def bind(r, work):
        """Bound per launch (target and control are separate launches): the profile and cwd this launch requested,
        the uid seen inside, reach. profile/cwd are the request's values, not observed inside the sandbox."""
        x = r.get('rec')
        r.update(profile=PROFILE, cwd=work, reached=well_formed(x) and x['called'] is True,
                 uid_inside=x['uid'] if well_formed(x) else None)
        return r

    def exec_result(self, ident, op, timeout):
        r = self.wait(ident, timeout)
        if not r:
            return {'rpc': 'no_reply', 'rec': None}
        if 'error' in r:
            return {'rpc': 'error', 'rec': None}
        res = r.get('result') or {}
        return {'rpc': 'ok', 'exit_code': res.get('exitCode'), 'rec': parse_probe(res.get('stdout'), op)}

    def end_normally(self):
        """Close stdin; exit 0 within END_S confirmed (True/False)."""
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            return self.proc.wait(timeout=END_S) == 0
        except subprocess.TimeoutExpired:
            return False

    def force_end(self):
        for sig in (signal.SIGTERM, signal.SIGKILL):
            if self.proc.poll() is not None:
                return True
            self.pin.send(sig)
            try:
                self.proc.wait(timeout=END_S)
                return True
            except subprocess.TimeoutExpired:
                continue
        return self.proc.poll() is not None


# --- setup and checks -----------------------------------------------------------------------------------------------
def environment():
    status = open('/proc/self/status').read().splitlines()
    pick = lambda key: next((l.split()[1] for l in status if l.startswith(key + ':')), None)
    try:
        label = open('/proc/self/attr/current').read().strip('\0\n')
    except OSError:
        label = None
    parts = label.split(':') if label else []
    checks = {'uid': os.getuid() == UID and os.geteuid() == UID, 'gid': os.getgid() == GID,
              'no_new_privs': pick('NoNewPrivs') == '1', 'seccomp_filter': pick('Seccomp') == '2',
              'no_caps': pick('CapEff') == '0000000000000000',
              'selinux_container_t': len(parts) >= 3 and parts[2] == 'container_t',
              'state_writable': os.access(ROOT, os.W_OK), 'codex': os.path.exists(CODEX),
              'python': os.path.exists(PYTHON), 'subreaper': set_subreaper()}
    out('env', checks=checks)
    return all(checks.values())


def pins():
    import hashlib
    sys.path.insert(0, APP)
    from runner.install_codex import pinned
    from runner import native_policy
    pin = pinned()
    sha = lambda p: hashlib.sha256(open(p, 'rb').read()).hexdigest()
    checks = {'codex': sha(CODEX) == pin['codex_sha256'], 'code_mode_host': sha(CODE_MODE_HOST) == pin['host_sha256']}
    try:
        native_policy.check_files(ROOT)
        checks['etc_codex'] = True
    except Exception:
        checks['etc_codex'] = False
    try:
        helper()
        checks['helper'] = 'HELPER_B64' in globals()
    except Exception:
        checks['helper'] = False
    out('pins', checks=checks)
    return all(checks.values())


def policy_check(app, work):
    """config/read + configRequirements/read checked with the image's native_policy; fixed check names only."""
    import logging
    sys.path.insert(0, APP)
    from runner import native_policy
    names = []

    class Grab(logging.Handler):
        def emit(self, record):
            msg = record.getMessage()
            if msg.startswith('runner_policy_check '):
                try:
                    names.append(json.loads(msg.split(' ', 1)[1]).get('check'))
                except ValueError:
                    names.append('unparsed')
    handler = Grab()
    logging.getLogger('uvicorn.error').addHandler(handler)
    try:
        cfg = app.call('config/read', {'includeLayers': True, 'cwd': work})
        req = app.call('configRequirements/read', {})
        if not (cfg and 'result' in cfg and req and 'result' in req):
            return False, ['rpc']
        ok = True
        for fn, arg in ((native_policy.check_config, (cfg['result'], ROOT)),
                        (native_policy.check_requirements, (req['result'],))):
            try:
                fn(*arg)
            except Exception:
                ok = False
        return ok, names
    finally:
        logging.getLogger('uvicorn.error').removeHandler(handler)


def case_dirs(k):
    base = '%s/g1/%s' % (ROOT, k)
    d = {'home': base + '/home', 'codex': base + '/codex', 'rec': base + '/rec', 'work': base + '/workspace',
         'tmp': '/tmp/g1/' + k}
    for p in (d['home'], d['codex'], d['rec'], d['work'], d['tmp']):
        os.makedirs(p, mode=0o700)
    os.makedirs(d['work'] + '/ctl', mode=0o755)
    return d


_HELPER = {}


def helper():
    """The placeholder helper (dot_codex.py r2): from HELPER_B64 (prepended by g1-dotcodex.sh, sha256 checked
    against HELPER_SHA) inside the Pod, or the importable module in the Docker tests. Its own namespace."""
    if 'm' not in _HELPER:
        import base64
        import hashlib
        import importlib
        import types
        src = globals().get('HELPER_B64')
        if src is None:
            _HELPER['m'] = importlib.import_module('dot_codex')
        else:
            data = base64.b64decode(src)
            if hashlib.sha256(data).hexdigest() != HELPER_SHA:
                raise RuntimeError('helper digest')
            m = types.ModuleType('dot_codex')
            exec(compile(data, 'dot_codex.py', 'exec'), m.__dict__)
            _HELPER['m'] = m
    return _HELPER['m']


def placeholder(d):
    return helper().ensure(d['work'], d['rec'])


def points_to_record(d):
    """The helper's check: the path names the recorded object (True), not (False), or unknown (None)."""
    try:
        return helper().check(d['work'], d['rec']).get('event') == 'matched'
    except helper().Refused:
        return False
    except Exception:
        return None


def placement(d):
    return {name: {k: v for k, v in snap(p).items() if k in ('type', 'mode', 'uid', 'gid', 'error')}
            for name, p in (('work', d['work']), ('codex', d['work'] + '/.codex'), ('ctl', d['work'] + '/ctl'))}


def gate_for(app, d):
    """policy check, S0 (sandbox active, with a Pod-side precondition), C0 (allowed write). Fixed fields."""
    pol_ok, pol_names = policy_check(app, d['work'])
    pre = d['home'] + '/pre-s0'
    try:
        open(pre, 'w').close()
        os.unlink(pre)
        pod_writable = True
    except OSError:
        pod_writable = False
    s0 = app.exec_probe(d['work'], 'create', d['home'] + '/s0')
    s0_ok, s0_why = judge_s0(pod_writable, exec_valid(s0, 'create', d['work']), s0['rec'])
    c0 = app.exec_probe(d['work'], 'create', d['work'] + '/ctl/c0')
    c0_valid = exec_valid(c0, 'create', d['work']) and c0['rec']['ok'] is True
    ok, failed_part = gate_result(pol_ok, s0_ok, c0_valid, os.path.isfile(d['work'] + '/ctl/c0'))
    g = {'ok': ok, 'failed': failed_part, 'policy': pol_ok, 'policy_checks': pol_names, 's0': s0_ok,
         's0_why': s0_why, 's0_errno': (s0['rec'] or {}).get('errno'), 'c0': c0_valid,
         'launches': [{k: x.get(k) for k in ('rpc', 'exit_code', 'reached', 'uid_inside', 'profile')} for x in (s0, c0)]}
    return ok, g


def end_and_check(app):
    """Normal end, forced if needed; then no process left under this harness (subreaper). (ended, clean)."""
    ended = app.end_normally()
    forced = False
    if not ended:
        forced = True
        app.force_end()
    reap_all()
    left = own_children()
    for p in left:
        Pin(p).send(signal.SIGKILL)
    time.sleep(0.5)
    reap_all()
    clean = not own_children()
    app.pin.close()
    return ended, forced, clean, len(left)


# --- cases ------------------------------------------------------------------------------------------------------------
PROTECT = {  # id: (op, target args builder, control args builder, control prep)
    'C1': 'create', 'C2': 'mkdir', 'C3': 'rename', 'C4': 'rmdir', 'C5': 'replace', 'C7a': 'utime', 'C7b': 'chmod'}


def protect_case(cid):
    op = PROTECT[cid]
    d = case_dirs(cid)
    w = d['work']
    ev = placeholder(d)
    n = w + '/ctl/n'
    os.mkdir(n, 0o755)  # the control object: an ordinary empty directory like the placeholder
    if op == 'replace':
        os.mkdir(w + '/ctl/x', 0o755)
        os.mkdir(w + '/ctl/y', 0o755)
    tgt_args = {'create': [w + '/.codex/f'], 'mkdir': [w + '/.codex/d'], 'rename': [w + '/.codex', w + '/m'],
                'rmdir': [w + '/.codex'], 'replace': [w + '/ctl/x', w + '/.codex'], 'utime': [w + '/.codex'],
                'chmod': [w + '/.codex']}[op]
    ctl_args = {'create': [n + '/f'], 'mkdir': [n + '/d'], 'rename': [n, w + '/ctl/m'], 'rmdir': [n],
                'replace': [w + '/ctl/y', n], 'utime': [n], 'chmod': [n]}[op]
    app = AppServer(d['home'], d['codex'], w, d['tmp'])
    rec = {'id': cid, 'op': op, 'placeholder': ev, 'placement': placement(d)}
    gate_ok, g, stopped = open_case(app, d, rec)
    if stopped:  # r1: no target or control launch after a failed gate; recorded, ended, then the run stops
        return stopped
    # control first (same op, same argument shape, same uid/cwd/profile, its own launch)
    c_before = snap(n)
    y_ino = snap(w + '/ctl/y').get('ino') if op == 'replace' else None
    ctl = app.exec_probe(w, op, *ctl_args)
    c_after = snap(n)
    c_extra = {'new_exists': os.path.lexists(ctl_args[0]) if op in ('create', 'mkdir') else None,
               'src_exists': os.path.lexists(ctl_args[0]), 'dst_exists': os.path.lexists(ctl_args[-1]),
               'dst_ino': snap(n).get('ino'), 'src_ino_before': y_ino}
    ctl_effect = intended(op, c_before, c_after, c_extra)
    # target
    p = w + '/.codex'
    t_before = snap(p)
    x_before = snap(w + '/ctl/x') if op == 'replace' else None
    tgt = app.exec_probe(w, op, *tgt_args)
    t_after = snap(p)
    same = identity_same(t_before, t_after)
    empty = snap_empty(t_after)
    path_points = points_to_record(d)
    t_effect = (not os.path.lexists(p)) or (op in ('create', 'mkdir') and os.path.lexists(tgt_args[0])) \
        or (op == 'rename' and os.path.lexists(tgt_args[1])) \
        or (op == 'replace' and identity_same(x_before, snap(w + '/ctl/x')) is False)
    ctl_valid, tgt_valid = exec_valid(ctl, op, w), exec_valid(tgt, op, w)
    verdict, why = judge(gate_ok, ctl_valid, ctl['rec'], ctl_effect, tgt_valid, tgt['rec'], t_effect, same, empty,
                         path_points)
    ended, forced, clean, left = end_and_check(app)
    bound = ('rpc', 'rec', 'profile', 'cwd', 'reached', 'uid_inside', 'exit_code')
    rec.update(gate_ok=gate_ok, gate=g,
               control=dict({k: ctl.get(k) for k in bound}, valid=ctl_valid, effect=ctl_effect),
               target=dict({k: tgt.get(k) for k in bound}, valid=tgt_valid, effect=t_effect, same=same, empty=empty,
                           path_points=path_points),
               syscall_succeeded=bool(well_formed(tgt['rec']) and tgt['rec']['ok']), effect_observed=bool(t_effect),
               verdict=verdict, reason=why, server_requests=app.server_requests, ended=ended, forced=forced,
               clean=clean, leftover=left)
    return rec, clean, None


def stop_case(app, rec, reason):
    """End a case that cannot continue (app-server start or gate failure): no further launch; the app-server is
    ended and the leftovers checked. Returns (rec, clean, stop reason)."""
    ended, forced, clean, left = end_and_check(app)
    rec.update(verdict='inconclusive', reason=reason, ended=ended, forced=forced, clean=clean, leftover=left)
    return rec, clean, reason


def open_case(app, d, rec):
    """The common start of C1-C8 and L1-L4 (r1, review r0 major 3): app-server start, then the gate (policy, S0,
    C0) recorded in rec. Returns (gate_ok, gate record, None) to continue, or (False, g, stop_case(...)) after the
    app-server was ended: the caller returns that triple without any further launch."""
    if not app.start():
        return False, None, stop_case(app, rec, 'app_server_start')
    gate_ok, g = gate_for(app, d)
    rec.update(gate_ok=gate_ok, gate=g)
    if not gate_ok:
        return False, g, stop_case(app, rec, 'gate_failed')
    return True, g, None


def case_transition(cid, verdict, clean, stop):
    """After one case (pure; r1): contamination first (rc 3), then a gate stop (any stop reason, or K2 not pass;
    rc 2), else continue (None). Returns None or (summary result, reason, rc)."""
    if clean is not True:
        return 'contamination_stopped', cid, 3
    if stop is not None:
        return 'gate_stopped', '%s:%s' % (cid, stop), 2
    if cid == 'K2' and verdict != 'pass':
        return 'gate_stopped', 'K2', 2
    return None


def chain_to(pid, stop_pid):
    """Ancestors of pid up to (not including) stop_pid: [(pid, start, role)]."""
    result, cur = [], ppid_of(pid)
    while cur and cur != stop_pid and cur > 1:
        result.append((cur, starttime(cur), role(cur)))
        cur = ppid_of(cur)
    return result


def token_procs(tok):
    """The probe's own python processes carrying the token (the launcher and bwrap argv carry it too: excluded)."""
    return [p for p in pids() if role(p) == 'python' and tok in cmdline(p)]


def c8_case():
    d = case_dirs('C8')
    w = d['work']
    ev = placeholder(d)
    for sub in ('c8', 'ctl/n8', 'ctl/p8'):
        os.makedirs(w + '/' + sub, mode=0o755)
    tok = uuid.uuid4().hex
    app = AppServer(d['home'], d['codex'], w, d['tmp'])
    obs = {'child_seen': False, 'launchers_gone': False, 'child_alive_after': None, 'child_gone': None}
    rec = {'id': 'C8', 'placeholder': ev, 'placement': placement(d)}
    gate_ok, g, stopped = open_case(app, d, rec)
    if stopped:  # r1: no spawn after a failed gate; the lifetime stays unobserved ('unknown')
        stopped[0].update(lifecycle=c8_lifecycle(obs), surviving_descendant_protection='unmeasured')
        return stopped
    obs['gate_ok'] = gate_ok
    p = w + '/.codex'
    t_before = snap(p)
    ident = app.exec_probe(w, 'spawn', tok, w + '/c8', w, timeout_ms=HOLD_MS, wait=False)
    deadline = time.monotonic() + READY_S
    while not os.path.exists(w + '/c8/ready-' + tok) and time.monotonic() < deadline:
        time.sleep(0.1)
    procs = token_procs(tok)
    child = parent = None
    if os.path.exists(w + '/c8/ready-' + tok) and len(procs) == 2:
        a, b = procs
        child, parent = (a, b) if ppid_of(a) == b else (b, a) if ppid_of(b) == a else (None, None)
    if not child:
        # r1: release the parent anyway (the child, if any, never gets go) so the spawn launch itself is judged;
        # the verdict is inconclusive (child_not_observed) whatever the launch shows
        open(w + '/c8/release-' + tok, 'w').close()
        res = AppServer.bind(app.exec_result(ident, 'spawn', HOLD_MS / 1000 + 5), w)
        obs['spawn_valid'] = exec_valid(res, 'spawn', w)
        rec['spawn'] = {k: res.get(k) for k in ('rpc', 'exit_code', 'reached', 'uid_inside', 'profile')}
    if child:
        cpin = Pin(child)
        chain = chain_to(parent, app.proc.pid)
        lpins = [Pin(pid) for pid, st, r in chain if r in ('launcher', 'bwrap')]
        obs['child_seen'] = cpin.ok() and all(x.ok() for x in lpins) and bool(lpins)
        rec['chain_roles'] = [r for _, _, r in chain]
        open(w + '/c8/release-' + tok, 'w').close()  # the parent ends only after the identities are pinned
        res = AppServer.bind(app.exec_result(ident, 'spawn', HOLD_MS / 1000 + 5), w)
        obs['spawn_valid'] = exec_valid(res, 'spawn', w)  # rpc ok, exit 0, a spawn record, uid 10001
        rec['spawn'] = {k: res.get(k) for k in ('rpc', 'exit_code', 'reached', 'uid_inside', 'profile')}
        obs['launchers_gone'] = bool(lpins) and all(x.exited(GONE_S) for x in lpins)
        if obs['launchers_gone']:
            if cpin.exited(0.5):
                obs['child_alive_after'] = False
                reap_all()  # if it was reparented to this subreaper as a zombie
                obs['child_gone'] = proc_gone(child, cpin.start)
            else:
                obs['child_alive_after'] = starttime(child) == cpin.start and pstate(child) not in ('Z', 'X', None)
                if obs['child_alive_after']:
                    open(w + '/c8/go-' + tok, 'w').close()
                    names = ('t-rename', 't-create', 'c-rename', 'c-create')
                    end = time.monotonic() + READY_S
                    while time.monotonic() < end and not all(os.path.exists('%s/c8/res-%s-%s' % (w, x, tok)) for x in names):
                        time.sleep(0.2)
                    results, effects = {}, {}
                    for x in names:
                        try:
                            results[x] = json.loads(open('%s/c8/res-%s-%s' % (w, x, tok)).read())
                        except (OSError, ValueError):
                            results[x] = None
                    effects['c-rename'] = (not os.path.lexists(w + '/ctl/n8')) and os.path.lexists(w + '/ctl/m8')
                    effects['c-create'] = os.path.lexists(w + '/ctl/p8/f8')
                    effects['t-rename'] = (not os.path.lexists(p)) or os.path.lexists(w + '/m8')
                    effects['t-create'] = os.path.lexists(p + '/f8')
                    obs['results'], obs['effects'] = results, effects
                    cpin.exited(GONE_S)
        for x in lpins:
            x.close()
        cpin.close()
    t_after = snap(p)
    obs['same'] = identity_same(t_before, t_after)
    obs['empty'] = snap_empty(t_after)
    obs['path_points'] = points_to_record(d)
    verdict, why = judge_c8(obs)
    ended, forced, clean, left = end_and_check(app)
    rec.update(obs={k: v for k, v in obs.items() if k not in ('results', 'effects')},
               results=obs.get('results'), effects=obs.get('effects'), verdict=verdict, reason=why,
               lifecycle=c8_lifecycle(obs),  # the lifetime alone: recorded, never a pass by itself
               surviving_descendant_protection=surviving_protection(verdict),
               scope='descendant_ended: this observed descendant did not exist after its launcher end was observed; '
                     'not the cause, not all descendants, not the protection of a surviving one',
               ended=ended, forced=forced, clean=clean, leftover=left)
    return rec, clean, None


def l1_case():
    d = case_dirs('L1')
    w = d['work']
    ev = placeholder(d)
    app = AppServer(d['home'], d['codex'], w, d['tmp'])
    rec = {'id': 'L1', 'placeholder': ev}
    gate_ok, g, stopped = open_case(app, d, rec)
    if stopped:
        return stopped
    p = w + '/.codex'
    first = snap(p)
    runs = []
    for _ in range(5):
        r = app.exec_probe(w, 'noop')
        runs.append({'rpc': r['rpc'], 'exit_code': r.get('exit_code'), 'uid_inside': r.get('uid_inside'),
                     'valid': exec_valid(r, 'noop', w), 'same': identity_same(first, snap(p))})
    verdict, why = judge_l1(gate_ok, runs)
    ended, forced, clean, left = end_and_check(app)
    rec.update(runs=runs, verdict=verdict, reason=why, ended=ended, forced=forced, clean=clean, leftover=left)
    return rec, clean, None


def l_end_case(lid):
    """L2 (stdin close, no active command), L3 (SIGTERM during a held command), L4 (SIGKILL during it)."""
    d = case_dirs(lid)
    w = d['work']
    ev = placeholder(d)
    os.makedirs(w + '/hold', mode=0o755)
    p = w + '/.codex'
    app = AppServer(d['home'], d['codex'], w, d['tmp'])
    rec = {'id': lid, 'placeholder': ev}
    gate_ok, g, stopped = open_case(app, d, rec)
    if stopped:
        return stopped
    before = snap(p)
    code = None
    if lid == 'L2':
        r = app.exec_probe(w, 'noop')
        reach_ok = exec_valid(r, 'noop', w)
        signal_ok = True  # no signal: stdin close
        try:
            app.proc.stdin.close()
        except OSError:
            signal_ok = False
        try:
            code = app.proc.wait(timeout=END_S)
            ended_ok = True
        except subprocess.TimeoutExpired:
            ended_ok = False
        # r1 (review r0 major 1): snapshot A after the end, before any cleanup by this harness, as in L3/L4
        a = snap(p)
        rec['snapshot_after_end'] = {k: a.get(k) for k in SNAP_FIELDS}
        ended, forced, clean, left = end_and_check(app)  # the cleanup; snapshot B after it (kept apart)
        b = snap(p)
        steps = l_steps('L2', gate_ok, reach_ok, signal_ok, ended_ok, code, identity_same(before, a),
                        identity_same(before, b))
        rec['scope'] = 'app-server normal end with no active command'
    else:
        tok = uuid.uuid4().hex
        app.exec_probe(w, 'hold', tok, w + '/hold', timeout_ms=HOLD_MS, wait=False)
        deadline = time.monotonic() + READY_S
        while not os.path.exists(w + '/hold/ready-' + tok) and time.monotonic() < deadline:
            time.sleep(0.1)
        procs = token_procs(tok)
        tpins = [Pin(x) for x in procs]
        chain = chain_to(procs[0], app.proc.pid) if len(procs) == 1 else []
        lpins = [Pin(pid) for pid, st, r in chain if r in ('launcher', 'bwrap')]
        reach_ok = (os.path.exists(w + '/hold/ready-' + tok) and len(procs) == 1 and tpins[0].ok()
                    and bool(lpins) and all(x.ok() for x in lpins) and app.pin.ok())
        ended_ok = False
        if reach_ok:
            signal_ok = app.pin.send(signal.SIGTERM if lid == 'L3' else signal.SIGKILL)
            try:
                app.proc.wait(timeout=END_S)
                ended_ok = True
            except subprocess.TimeoutExpired:
                pass
        else:
            signal_ok = False
        # snapshot A: after the app-server end, before any cleanup by this harness
        alive = {'token': [not x.exited(0.2) for x in tpins], 'launchers': [not x.exited(0.2) for x in lpins]}
        a = snap(p)
        rec['alive_after_end'] = alive
        rec['snapshot_after_end'] = {k: a.get(k) for k in SNAP_FIELDS}
        # forced cleanup of what is left, then snapshot B (kept apart)
        for x in tpins + lpins:
            x.send(signal.SIGKILL)
        gone = all(x.exited(GONE_S) for x in tpins + lpins)
        reap_all()
        b = snap(p)
        steps = l_steps(lid, gate_ok, reach_ok, signal_ok, ended_ok, None, identity_same(before, a),
                        identity_same(before, b))
        rec['cleanup_confirmed'] = gone
        rec['scope'] = '%s to the app-server during a held command' % ('SIGTERM' if lid == 'L3' else 'SIGKILL')
        for x in tpins + lpins:
            x.close()
        ended, forced, clean, left = end_and_check(app)
    verdict, why = judge_l(steps)
    rec.update(steps=steps, exit_code=code, verdict=verdict, reason=why, clean=clean, leftover=left,
               note='the verdict uses the snapshot after the app-server end, before cleanup; after_cleanup is separate')
    return rec, clean, None


def k_case(kid):
    """K1 (placeholder) / K2 (none): the policy check is the case itself; no S0/C0 launch."""
    d = case_dirs(kid)
    w = d['work']
    ev = placeholder(d) if kid == 'K1' else None
    app = AppServer(d['home'], d['codex'], w, d['tmp'])
    rec = {'id': kid, 'placeholder': ev}
    if not app.start():
        return stop_case(app, rec, 'app_server_start')
    ok, names = policy_check(app, w)
    ended, forced, clean, left = end_and_check(app)
    rec.update(verdict='pass' if ok else 'fail', checks=names, ended=ended, forced=forced, clean=clean,
               leftover=left)
    return rec, clean, None


def main():
    if not environment() or not pins():
        out('summary', result='gate_stopped')
        return 2
    os.makedirs(ROOT + '/g1', mode=0o700)
    os.makedirs('/tmp/g1', mode=0o700)
    results = {}
    plan = [('K2', lambda: k_case('K2')), ('K1', lambda: k_case('K1'))] + \
           [(c, (lambda c=c: protect_case(c))) for c in ('C1', 'C2', 'C3', 'C4', 'C5', 'C7a', 'C7b')] + \
           [('C8', c8_case), ('L1', l1_case), ('L2', lambda: l_end_case('L2')), ('L3', lambda: l_end_case('L3')),
            ('L4', lambda: l_end_case('L4'))]
    for cid, fn in plan:
        started = time.monotonic()
        try:
            rec, clean, stop = fn()
        except Exception as e:
            rec, clean, stop = ({'id': cid, 'verdict': 'inconclusive', 'reason': 'harness_error:' + type(e).__name__},
                                False, 'harness_error')
        rec['seconds'] = round(time.monotonic() - started, 1)
        results[cid] = rec.get('verdict')
        out('case', **rec)
        t = case_transition(cid, rec.get('verdict'), clean, stop)
        if t is not None:
            result, reason, rc = t
            out('summary', result=result, at=cid, reason=reason, results=results)
            return rc
    out('summary', result=conclusion(results), results=results)
    return 0


if __name__ == '__main__' and os.environ.get('G1_IMPORT_ONLY') != '1':
    sys.exit(main())
