"""#68 P-A exec observer (P-A design r3 §4; PM D-V2, D-P2 (a)): a library loaded at its pin by pa-start.py (and later
v6-item.py). Development VM only. Nothing here runs by itself; there is no main.

What it observes (bpftrace on the guest, one child process, the pinned PROGRAM below):
- kprobe:wake_up_new_task: every new process (not threads) whose parent is tracked or in the runner container's
  cgroup, with BOTH identities (pid, start_time) in one event, so every parent -> child edge is exact (no time window);
- tracepoint:cgroup:cgroup_attach_task into the runner container cgroup ($1): a process entering the container (the
  way a `kubectl exec` session does);
- sched_process_exec / sched_process_exit, sys_enter_execve / sys_enter_execveat, filtered to the same set;
- all event times are nsecs(monotonic) (CLOCK_MONOTONIC), never the bare `nsecs` (the boot clock, which includes
  suspend). start_time is an identity key only; start_boottime is printed to match /proc starttime ticks of the
  processes that existed at attach (the R1 roots).
Recorded per event: no arguments, no environment; the executable path's first 64 bytes and comm only.

Lineage (§4.3): exact edges; accepted roots are only R0 (the K4 driver), R1 (the container's processes at attach, from
cgroup.procs + /proc/<pid>/stat) and R2 (a pid attached into $1 inside one of the unit's own K3 / K2 windows, joined to
that pid's next own event with no exit in between). Anything else is unattributed.

Validation (§4.5, D-P2 (a)): K4 (20 execve + 20 execveat by syscall, from a driver whose child pids are written before
they run), K3 (one exec of /bin/true in the container ns), K2 (the TMPDIR form of `codex sandbox -- /bin/true`, whose
/bin/true descends from bwrap / codex-linux-sandbox and has a user or mnt ns different from the container's). Pass also
needs a clean exit (exit 0 after our SIGINT, END seen and equal to the lines read, no Lost line, no output cap), no
unattributed K event, the runtime clock check, the Pod /tmp (emptyDir, read on the node, no-follow, depth 3)
compared before K3 and after K2 with every observed difference inside this K2's own directory T (the one it printed)
or the single allowed path /tmp/codex-daemon-10001 (created as a 0700 dir of uid 10001, or present before with all
observed fields equal), no new process under the container's own processes in that window, and the R1 processes all
alive and unchanged.

Limits (recorded, not judged away): M1 was a partial pre-measurement (the command form and the file traces before
the sandbox failed to start), not a pass: this host's AppArmor user-namespace restriction stopped bubblewrap and it was
not worked around, so the VM K2 is the first measurement of the namespaces, the launcher chain and the files of a
successful run (D-P2 r1, #68 comment 2026-11-11). File writes are not traced: a /tmp change cannot be attributed to K2
or to the app-server, so any difference outside T and the allowed path is unverified and never a pass. The /tmp
verdict of a pass is exactly TMP_VERDICT below, with TMP_UNVERIFIED recorded beside it. New processes under the
container's own processes during the window are recorded and also block a pass (attribution unknown); the R1 roots
being alive is a separate check and says nothing about such processes. K2 is a known execution for the observer
only; it is not turn evidence and not evidence that a turn cannot bypass the policy.

r4 (PM diagnosis pa-diag baa6784e…: the r3 program failed bpftrace v0.24.2's dry run with five `Argument mismatch for
@t` errors: the map's key type came from its first write, `@t[$2]` (int64), and the `pid` builtin (uint64) read and
deleted it): every @t key is written in one form, `(uint64)<expr>` (BEGIN, parent, child, attach, the four probe
reads and the delete), and $1 is compared only as `(uint64)$1` (cgroup and dst_id are u64). pid / tgid values are
positive, so the cast keeps every value and the identity / reuse logic is unchanged. The precheck keeps every call's
rc and stderr (capture_run; the r3 precheck used the base run(), which discarded stderr).
"""
import hashlib
import json
import os
import queue
import re
import signal
import stat
import subprocess
import threading
import time

BPFTRACE = '/usr/bin/bpftrace'
HZ = os.sysconf('SC_CLK_TCK') if hasattr(os, 'sysconf') else 100
CAP_OUT, CAP_ERR, CAP_PRE = 8 << 20, 1 << 20, 64 << 10
T_ATTACH, T_K4, T_K3, T_K2, T_DRAIN, T_STOP = 30, 60, 30, 60, 5, 30
EPS_NS = 1_000_000
DAEMON = 'codex-daemon-10001'
LAUNCHERS = ('bwrap', 'codex-linux-sandbox')
K3_COMMAND = ['/bin/true']
# D-P2 r1 (#68 comment 2026-11-11): the TMPDIR form of `codex sandbox -- <command>` (the 0.160.1 CLI form).
# The first stdout line names K2's own directory: only that exact directory is K2's (no /tmp/k2.* wildcard).
K2_SCRIPT = ('T=$(mktemp -d /tmp/k2.XXXXXX) && echo "T=$T" && mkdir "$T/codex" "$T/tmp" && exec env -i HOME="$T" '
             'CODEX_HOME="$T/codex" TMPDIR="$T/tmp" PATH=/usr/local/bin:/usr/bin:/bin /usr/local/bin/codex sandbox '
             '-- /bin/true')
K2_T = re.compile(r'T=/tmp/(k2\.[A-Za-z0-9]{6})')
# The only /tmp verdict text of a pass (review / PM 2026-11-11), and what stays unverified beside it.
TMP_VERDICT = '관측 범위 내 전후 metadata 차이는 허용 T 와 지정 예외에 한정'
TMP_UNVERIFIED = ('a create-then-delete between the two observations', 'a content change under the same inode',
                  'a daemon connection or its end', 'entries below the walk depth (3)',
                  'anything a failed or raced read did not see (such a case is not a pass)')
K2_COMMAND = ['/bin/sh', '-c', K2_SCRIPT]

PROGRAM = r'''config = { perf_rb_pages = 256 }
BEGIN { @t[(uint64)$2] = 1; printf("READY\t%llu\n", nsecs(monotonic)); }
kprobe:wake_up_new_task {
  $p = (struct task_struct *)arg0;
  if ($p->pid == $p->tgid && (@t[(uint64)curtask->tgid] || cgroup == (uint64)$1)) {
    @t[(uint64)$p->tgid] = 1;
    printf("F\t%llu\t%d\t%llu\t%llu\t%d\t%llu\t%llu\n", nsecs(monotonic), curtask->tgid,
           curtask->group_leader->start_time, curtask->group_leader->start_boottime, $p->tgid, $p->start_time,
           $p->start_boottime);
    @n = count();
  }
}
tracepoint:cgroup:cgroup_attach_task /args.dst_id == (uint64)$1/ {
  @t[(uint64)args.pid] = 1;
  printf("A\t%llu\t%d\n", nsecs(monotonic), args.pid);
  @n = count();
}
tracepoint:syscalls:sys_enter_execve /@t[(uint64)pid] || cgroup == (uint64)$1/ {
  printf("S\t%llu\t%d\t%llu\t%llu\texecve\n", nsecs(monotonic), pid, curtask->group_leader->start_time,
         curtask->group_leader->start_boottime);
  @n = count();
}
tracepoint:syscalls:sys_enter_execveat /@t[(uint64)pid] || cgroup == (uint64)$1/ {
  printf("S\t%llu\t%d\t%llu\t%llu\texecveat\n", nsecs(monotonic), pid, curtask->group_leader->start_time,
         curtask->group_leader->start_boottime);
  @n = count();
}
tracepoint:sched:sched_process_exec /@t[(uint64)pid] || cgroup == (uint64)$1/ {
  printf("E\t%llu\t%d\t%llu\t%llu\t%d\t%u\t%u\t%u\t%llu\t%s\t%s\n", nsecs(monotonic), pid,
         curtask->group_leader->start_time, curtask->group_leader->start_boottime, uid,
         curtask->cred->user_ns->ns.inum, curtask->nsproxy->pid_ns_for_children->ns.inum,
         curtask->nsproxy->mnt_ns->ns.inum, cgroup, comm, str(args.filename, 64));
  @n = count();
}
tracepoint:sched:sched_process_exit /(@t[(uint64)pid] || cgroup == (uint64)$1) && tid == pid/ {
  printf("X\t%llu\t%d\t%llu\n", nsecs(monotonic), pid, curtask->group_leader->start_time);
  @n = count();
  delete(@t[(uint64)pid]);  /* review round 1: a pid leaves the tracked set when its process exits (no reuse match) */
}
END { printf("END\t%d\n", (int64)@n); clear(@t); clear(@n); }
'''

DRIVER = r'''
import ctypes, json, os, platform, sys
if platform.machine() != 'x86_64':
    print(json.dumps({'error': 'not x86_64'}), flush=True); sys.exit(3)
libc = ctypes.CDLL(None, use_errno=True)
SYS_EXECVEAT, AT_EMPTY_PATH = 322, 0x1000
sys.stdin.readline()
r, w = os.pipe()
kids = {'execve': [], 'execveat': []}
for index in range(40):
    way = 'execve' if index < 20 else 'execveat'
    pid = os.fork()
    if pid == 0:
        os.close(w)
        os.read(r, 1)
        if way == 'execve':
            os.execv('/usr/bin/true', ['true'])
        fd = os.open('/usr/bin/true', os.O_RDONLY)
        argv = (ctypes.c_char_p * 2)(b'true', None)
        envp = (ctypes.c_char_p * 1)(None)
        libc.syscall(SYS_EXECVEAT, fd, b'', argv, envp, AT_EMPTY_PATH)
        os._exit(127)
    kids[way].append(pid)
print(json.dumps(kids), flush=True)
sys.stdin.readline()
os.close(w)
codes = [os.waitpid(p, 0)[1] for way in ('execve', 'execveat') for p in kids[way]]
print(json.dumps({'statuses': codes}), flush=True)
'''


class ObserverError(Exception):
    def __init__(self, code, message, detail=None):
        super().__init__(message)
        self.code, self.message, self.detail = code, message, detail


# --- static and precheck ----------------------------------------------------------------------------------------------

def bare_nsecs(program=PROGRAM):
    """Every nsecs in the program must be nsecs(monotonic): the bare form is the boot clock (bpftrace docs)."""
    return [m.start() for m in re.finditer(r'\bnsecs\b(?!\(monotonic\))', program)]


def capture_run(argv, timeout):
    """(rc, stdout, stderr) of one precheck call (r4: stderr kept, never discarded); each text capped at CAP_PRE
    characters; 124 timeout, 125 not started."""
    try:
        done = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=max(1, timeout), check=False)
    except subprocess.TimeoutExpired as expired:
        return 124, '', (expired.stderr or b'').decode('utf-8', 'replace')[:CAP_PRE] + '\n[timeout]'
    except Exception as error:
        return 125, '', '[not started: %s]' % type(error).__name__
    return (done.returncode, done.stdout.decode('utf-8', 'replace')[:CAP_PRE].strip(),
            done.stderr.decode('utf-8', 'replace')[:CAP_PRE])


def precheck(capture=None):
    """Read-only (rc 3 on any failure, nothing started): the installed version, BTF, the two probes with the needed
    field, and a dry run of the exact program. `capture(argv, timeout) -> (rc, stdout, stderr)` (capture_run). No
    fallback. r4: every call's rc and stderr are kept in order (facts['calls'] on a pass, error.detail on a refusal),
    so a refusal shows the compiler's own message (the r3 precheck used the base run(), which discarded stderr)."""
    capture = capture or capture_run
    calls = []
    facts = {'calls': calls, 'programSha256': hashlib.sha256(PROGRAM.encode()).hexdigest()}

    def call(name, argv, timeout):
        rc, out, err = capture(argv, timeout)
        calls.append({'call': name, 'rc': rc, 'stderr': err})
        return rc, out

    def refuse(message):
        raise ObserverError(3, message, detail=calls)

    rc, out = call('version', [BPFTRACE, '--version'], 30)
    if rc != 0 or not out.startswith('bpftrace v'):
        refuse('bpftrace version lookup failed')
    facts['version'] = out.split()[1]
    if bare_nsecs():
        refuse('the program uses a bare nsecs')
    if not os.path.isfile('/sys/kernel/btf/vmlinux'):
        refuse('no kernel BTF (/sys/kernel/btf/vmlinux)')
    rc, out = call('dst_id', [BPFTRACE, '-lv', 'tracepoint:cgroup:cgroup_attach_task'], 30)
    if rc != 0 or not re.search(r'\bdst_id\b', out):
        refuse('tracepoint cgroup_attach_task has no dst_id field')
    rc, out = call('kprobe', [BPFTRACE, '-l', 'kprobe:wake_up_new_task'], 30)
    if rc != 0 or 'kprobe:wake_up_new_task' not in out.split():
        refuse('kprobe wake_up_new_task is not available')
    rc, _ = call('dry-run', [BPFTRACE, '--dry-run', '-e', PROGRAM, '0', '0'], 60)
    if rc != 0:
        refuse('the pinned program does not compile / attach in a dry run (its stderr is in the detail); no fallback')
    facts['dryRun'] = 0
    return facts


# --- cgroup, /proc, /tmp --------------------------------------------------------------------------------------------

def resolve_cgroup(container_id, root='/sys/fs/cgroup', depth=8):
    """The runner container's cgroup v2 directory (its name holds the container id) and its id (the directory
    inode). Exactly one match, else ObserverError(4)."""
    cid = container_id.split('://', 1)[-1]
    if not re.fullmatch(r'[0-9a-f]{64}', cid):
        raise ObserverError(4, 'the container id is not 64 hex')
    found, stack = [], [(root, 0)]
    while stack:
        path, level = stack.pop()
        try:
            entries = list(os.scandir(path))
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                if cid in entry.name:
                    found.append(entry.path)
                elif level < depth:
                    stack.append((entry.path, level + 1))
    if len(found) != 1:
        raise ObserverError(4, 'the container cgroup was found %d times' % len(found))
    return found[0], os.lstat(found[0]).st_ino


def proc_identity(pid, proc='/proc'):
    """(pid, starttime ticks, user ns inode, mnt ns inode) of a running process, read-only."""
    data = open('%s/%d/stat' % (proc, pid)).read()
    ticks = int(data[data.rindex(')') + 2:].split()[19])
    ns = {}
    for name in ('user', 'mnt'):
        link = os.readlink('%s/%d/ns/%s' % (proc, pid, name))
        ns[name] = int(re.fullmatch(r'%s:\[(\d+)\]' % name, link).group(1))
    return pid, ticks, ns['user'], ns['mnt']


def snapshot_r1(cg_path, proc='/proc'):
    """The container's processes at attach (R1 roots): cgroup.procs, each with its identity and namespaces."""
    pids = [int(line) for line in open(os.path.join(cg_path, 'cgroup.procs')).read().split()]
    if not pids:
        raise ObserverError(4, 'the container cgroup has no process')
    return [proc_identity(pid, proc) for pid in pids]


def time_namespace(r1, proc='/proc'):
    """P-A r3 §0 (review round 1): the runner processes (the R1 roots, by pid AND start ticks) share the host's time
    namespace, so the runner's time.monotonic_ns() and the observer's nsecs(monotonic) are one clock. Read-only:
    /proc/1/ns/time against /proc/<pid>/ns/time, with the process identity read again after the link (a reused pid
    fails). A different namespace or any failed lookup is ObserverError (the observer is then not validated, rc 16)."""
    try:
        host = os.readlink('%s/1/ns/time' % proc)
    except OSError as error:
        raise ObserverError(4, 'the host time namespace is unreadable (errno %d)' % error.errno) from None
    runner = {}
    for pid, ticks, _, _ in r1:
        try:
            link = os.readlink('%s/%d/ns/time' % (proc, pid))
            again = proc_identity(pid, proc)[1]
        except (OSError, ValueError, AttributeError, IndexError):
            raise ObserverError(4, 'the time namespace of runner pid %d is unreadable' % pid) from None
        if again != ticks:
            raise ObserverError(4, 'runner pid %d changed while its time namespace was read' % pid)
        if link != host:
            raise ObserverError(4, 'runner pid %d is in another time namespace (%s; host %s)' % (pid, link, host))
        runner[pid] = link
    if not runner:
        raise ObserverError(4, 'no runner process to compare the time namespace with')
    return {'host': host, 'runner': runner}


def tmp_walk(root, depth=3):
    """Read-only, no-follow walk of the Pod's /tmp emptyDir on the node. Returns {'entries': {relative path: (type,
    mode, uid, size, ino, mtime_ns)}, 'truncated': {directory at the depth limit: its entry count}, 'errors': [...]}.
    Contents are never read; a symlink is recorded as a link and never followed. What lies below the depth limit is
    only counted, and a read failure or a race (an entry gone between listing and lstat) is an error: the caller
    treats both as limits of the observation (unverified), never as "unchanged"."""
    out, truncated, errors = {}, {}, []

    def kind(mode):
        for test, name in ((stat.S_ISDIR, 'dir'), (stat.S_ISREG, 'file'), (stat.S_ISLNK, 'link'),
                           (stat.S_ISSOCK, 'socket'), (stat.S_ISFIFO, 'fifo')):
            if test(mode):
                return name
        return 'other'

    def walk(fd, prefix, level):
        for name in sorted(os.listdir(fd)):
            rel = prefix + name
            try:
                info = os.lstat(name, dir_fd=fd)
            except OSError as error:
                errors.append('%s: lstat errno %d' % (rel, error.errno))
                continue
            out[rel] = (kind(info.st_mode), stat.S_IMODE(info.st_mode), info.st_uid, info.st_size, info.st_ino,
                        info.st_mtime_ns)
            if stat.S_ISDIR(info.st_mode):
                try:
                    sub = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                except OSError as error:
                    errors.append('%s: open errno %d' % (rel, error.errno))
                    continue
                try:
                    if level < depth:
                        walk(sub, rel + '/', level + 1)
                    else:
                        truncated[rel] = len(os.listdir(sub))
                except OSError as error:
                    errors.append('%s: list errno %d' % (rel, error.errno))
                finally:
                    os.close(sub)

    try:
        top = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as error:
        return {'entries': {}, 'truncated': {}, 'errors': ['root: open errno %d' % error.errno]}
    try:
        walk(top, '', 1)
    except OSError as error:
        errors.append('root: list errno %d' % error.errno)
    finally:
        os.close(top)
    return {'entries': out, 'truncated': truncated, 'errors': errors}


def tmp_judge(before, after, t_name):
    """D-P2 r1 + review scope. `t_name` is the one directory this K2 created and printed (k2.XXXXXX); only it is K2's
    own (recorded, not judged). The single allowed path codex-daemon-10001 may be created as a 0700 dir of uid 10001,
    or, if present before, keep all observed fields. Anything else that differs between the two observations, any
    other k2.* directory, a directory cut off by the depth limit outside T, or any walk error is a problem: an
    attribution to K2 or the app-server is not possible (file writes are not traced), so it is never a pass.
    What a clean result means (review wording): within the observed range, the metadata differences between the two
    observations are limited to T and the allowed path. It is not evidence about a create-then-delete in between, a
    content change under the same inode, or a daemon connection."""
    problems, note = [], None
    if not t_name or not re.fullmatch(r'k2\.[A-Za-z0-9]{6}', t_name):
        return ['K2 did not name its own directory (no T= line)'], None
    for side, walked in (('before', before), ('after', after)):
        for error in walked['errors']:
            problems.append('walk %s: %s (unverified)' % (side, error))
    old_all, new_all = before['entries'], after['entries']

    def own(path):
        return path == t_name or path.startswith(t_name + '/')
    for side, walked in (('before', before), ('after', after)):
        for path in walked['truncated']:
            if not own(path):
                problems.append('%s %s: below the walk depth (unverified)' % (side, path))
    for path in sorted(set(old_all) | set(new_all)):
        if own(path):
            continue
        if path.split('/')[0].startswith('k2.'):
            problems.append('%s: another k2.* directory than this K2\'s own' % path)
            continue
        old, new = old_all.get(path), new_all.get(path)
        if path == DAEMON:
            if old is None and new is not None and new[:3] == ('dir', 0o700, 10001):
                note = 'created during the window'
            elif old is not None and old == new:
                note = 'present before the window, unchanged (app-server created: an assumption, not observed)'
            else:
                problems.append('%s: not the allowed form (%s -> %s)' % (path, old and old[:3], new and new[:3]))
            continue
        if old != new:
            problems.append('%s %s' % (path, 'created' if old is None else 'removed' if new is None else 'changed'))
    return problems, note


# --- the observer process -------------------------------------------------------------------------------------------

def _proc_start(pid):
    try:
        data = open('/proc/%d/stat' % pid).read()
        return int(data[data.rindex(')') + 2:].split()[19])
    except (OSError, ValueError, IndexError):
        return None


class Observer:
    """One bpftrace child and one K4 driver child, both held by handle; nothing is looked up by name. Every failure
    is an ObserverError; stop() is idempotent and only ever signals its own child (pid + start time checked)."""

    def __init__(self, evdir, cg_id, popen=subprocess.Popen, clock=time.monotonic_ns, bpftrace=BPFTRACE,
                 python='/usr/bin/python3'):
        self.evdir, self.cg_id, self.popen, self.clock, self.bpftrace, self.python = (
            evdir, cg_id, popen, clock, bpftrace, python)
        self.proc = self.driver = self.driver_id = None
        self.lines, self.err_lines, self.windows, self.k4 = [], [], {}, None
        self.capped, self.lost, self.end, self.exit_status, self.ready = False, False, None, None, None
        self.state = 'new'
        self.clock_window = None
        self._readers = []
        self._ready = threading.Event()

    def _reader(self, stream, path, cap, sink, is_out):
        total = 0
        with open(path, 'xb') as out:
            for raw in stream:
                total += len(raw)
                if total > cap:
                    self.capped = True
                    break
                out.write(raw)
                line = raw.decode('utf-8', 'replace').rstrip('\n')
                if 'Lost' in line and 'events' in line:
                    self.lost = True
                if is_out and line.startswith('READY\t'):
                    self.ready = line
                    self._ready.set()
                elif is_out and line.startswith('END\t'):
                    self.end = line
                sink.append(line)
            out.flush()
            os.fsync(out.fileno())

    def start(self):
        """Driver first (its pid seeds the trace), then bpftrace; READY within T_ATTACH and its value between two
        monotonic reads (the runtime clock check)."""
        self.state = 'starting'
        self.driver = self.popen([self.python, '-I', '-B', '-c', DRIVER], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
        self.driver_id = (self.driver.pid, _proc_start(self.driver.pid))
        before = self.clock()
        self.proc = self.popen([self.bpftrace, '-e', PROGRAM, str(self.cg_id), str(self.driver.pid)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        self.proc_start = _proc_start(self.proc.pid)
        for stream, name, cap, sink, is_out in ((self.proc.stdout, 'observer.out', CAP_OUT, self.lines, True),
                                                (self.proc.stderr, 'observer.err', CAP_ERR, self.err_lines, False)):
            thread = threading.Thread(target=self._reader, args=(stream, os.path.join(self.evdir, name), cap, sink,
                                                                 is_out), daemon=True)
            thread.start()
            self._readers.append(thread)
        if not self._ready.wait(T_ATTACH):
            raise ObserverError(4, 'observer not attached (no READY within %d s)' % T_ATTACH)
        after = self.clock()
        value = int(self.ready.split('\t')[1])
        self.clock_window = (before, value, after)
        if not before <= value <= after:
            raise ObserverError(4, 'observer clock not monotonic (READY %d outside [%d, %d])' % (value, before, after))
        self.state = 'running'

    def run_k4(self):
        """The K4 driver: 40 children, their pids written before they may run; then released and waited. One deadline
        (T_K4) covers the first line, the driver's exit and its remaining output (review round 1); on any failure the
        driver (our own child, by its handle) is killed and reaped, then ObserverError(4)."""
        deadline = time.monotonic() + T_K4
        lines = queue.Queue()

        def pump():
            try:
                for raw in self.driver.stdout:
                    lines.put(raw)
            except (OSError, ValueError):
                pass
            lines.put(None)
        threading.Thread(target=pump, daemon=True).start()

        def next_line():
            remaining = deadline - time.monotonic()
            try:
                if remaining <= 0:
                    raise queue.Empty
                return lines.get(timeout=remaining)
            except queue.Empty:
                raise ObserverError(4, 'K4 driver timed out') from None
        try:
            self.driver.stdin.write(b'go\n')
            self.driver.stdin.flush()
            try:
                kids = json.loads(next_line() or b'')
            except ValueError:
                raise ObserverError(4, 'K4 driver gave no pid list') from None
            if not isinstance(kids, dict) or set(kids) != {'execve', 'execveat'} or any(
                    len(kids[k]) != 20 for k in kids):
                raise ObserverError(4, 'K4 driver pid list malformed')
            with open(os.path.join(self.evdir, 'k4-pids.json'), 'x') as out:
                json.dump(kids, out, sort_keys=True)
                out.flush()
                os.fsync(out.fileno())
            self.driver.stdin.write(b'released\n')
            self.driver.stdin.flush()
            tail = []
            while True:
                raw = next_line()
                if raw is None:
                    break
                tail.append(raw.decode('utf-8', 'replace'))
            try:
                self.driver.wait(max(0.01, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                raise ObserverError(4, 'K4 driver timed out') from None
            try:
                statuses = json.loads(''.join(tail).strip().splitlines()[-1])['statuses']
            except (ValueError, IndexError, KeyError, TypeError):
                raise ObserverError(4, 'K4 driver gave no statuses') from None
        except (ObserverError, OSError):
            if self.driver.poll() is None:
                self.driver.kill()
                self.driver.wait(5)
            raise
        self.k4 = {'pids': kids, 'statuses': statuses, 'driver': self.driver_id}
        time.sleep(T_DRAIN)
        return self.k4

    def run_window(self, name, argv, timeout, run):
        """One K exec (K3 / K2): its monotonic window brackets the call; `run(argv, timeout) -> (rc, out)`."""
        start = self.clock()
        rc, out = run(argv, timeout)
        end = self.clock()
        self.windows[name] = (start, end, rc)
        time.sleep(T_DRAIN)
        return rc

    def stop(self):
        """SIGINT to our own bpftrace (pid + start time checked), END expected, joined and flushed. Idempotent."""
        if self.proc is None or self.state in ('stopped',):
            return
        if self.proc.poll() is None:
            if _proc_start(self.proc.pid) not in (None, self.proc_start):
                self.state = 'stopped'
                raise ObserverError(4, 'the observer pid now names another process; not signalled')
            self.proc.send_signal(signal.SIGINT)
            try:
                self.proc.wait(T_STOP)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(5)
                self.exit_status = 'killed after the stop timeout'
        if self.exit_status is None:
            self.exit_status = self.proc.returncode
        for thread in self._readers:
            thread.join(10)
        if self.driver is not None and self.driver.poll() is None:
            self.driver.kill()
            self.driver.wait(5)
        self.state = 'stopped'

    def summary(self):
        return {'state': self.state, 'exit': self.exit_status, 'lines': len(self.lines), 'lost': self.lost,
                'capped': self.capped, 'endSeen': self.end is not None, 'endLine': self.end, 'clock': self.clock_window,
                'windows': self.windows}


# --- pure judges --------------------------------------------------------------------------------------------------

def parse(lines):
    """Event tuples from the program's tab-separated lines; anything else (stderr text, attach notes) is ignored here
    and judged by clean_exit."""
    events = []
    for line in lines:
        part = line.split('\t')
        try:
            if part[0] == 'F' and len(part) == 8:
                events.append({'k': 'F', 't': int(part[1]), 'parent': (int(part[2]), int(part[3])),
                               'pbst': int(part[4]), 'child': (int(part[5]), int(part[6])), 'cbst': int(part[7])})
            elif part[0] == 'A' and len(part) == 3:
                events.append({'k': 'A', 't': int(part[1]), 'pid': int(part[2])})
            elif part[0] == 'S' and len(part) == 6:
                events.append({'k': 'S', 't': int(part[1]), 'id': (int(part[2]), int(part[3])),
                               'bst': int(part[4]), 'call': part[5]})
            elif part[0] == 'E' and len(part) >= 12:
                events.append({'k': 'E', 't': int(part[1]), 'id': (int(part[2]), int(part[3])), 'bst': int(part[4]),
                               'uid': int(part[5]), 'userns': int(part[6]), 'pidns': int(part[7]),
                               'mntns': int(part[8]), 'cg': int(part[9]), 'comm': part[10],
                               'file': '\t'.join(part[11:])})
            elif part[0] == 'X' and len(part) == 4:
                events.append({'k': 'X', 't': int(part[1]), 'id': (int(part[2]), int(part[3]))})
        except ValueError:
            continue
    return events


def ticks(bst, hz=HZ):
    return bst // (1_000_000_000 // hz)


def clean_exit(summary, n_events):
    reasons = []
    if summary.get('exit') != 0:
        reasons.append('observer exit %r (not 0 after our SIGINT)' % (summary.get('exit'),))
    if not summary.get('endSeen'):
        reasons.append('no END line')
    elif summary.get('endCount') is not None and summary['endCount'] != n_events:
        reasons.append('END count %s != %d event lines' % (summary['endCount'], n_events))
    if summary.get('lost'):
        reasons.append('a Lost line')
    if summary.get('capped'):
        reasons.append('the output cap was reached')
    return reasons


def lineage(events, r1, driver, windows, hz=HZ):
    """Exact lineage. r1: [(pid, ticks, userns, mntns)]; driver: (pid, ticks); windows: {name: (start, end, rc)}.
    Returns (nodes, parent_of, roots, unattributed): node = (pid, start_time); roots: {node: 'R0'|'R1'|'R2:<name>'}."""
    r1_keys = {(pid, t) for pid, t, _, _ in r1}
    parent_of, roots, seen, exits = {}, {}, [], {}
    for event in events:
        if event['k'] == 'F':
            parent_of[event['child']] = event['parent']
            for node, bst in ((event['parent'], event['pbst']), (event['child'], event['cbst'])):
                seen.append((event['t'], node, bst))
        elif event['k'] in ('E', 'S'):
            seen.append((event['t'], event['id'], event['bst']))
        elif event['k'] == 'X':
            exits.setdefault(event['id'][0], []).append(event['t'])
    for _, node, bst in seen:
        key = (node[0], ticks(bst, hz))
        if key in r1_keys and node not in parent_of:
            roots[node] = 'R1'
        if driver and key == driver:
            roots[node] = 'R0'
    for attach in (e for e in events if e['k'] == 'A'):
        window = [name for name, (start, end, _) in windows.items() if start <= attach['t'] <= end]
        nxt = sorted((t, node) for t, node, _ in seen if node[0] == attach['pid'] and t >= attach['t'])
        if not window or not nxt:
            continue
        if any(attach['t'] <= x <= nxt[0][0] for x in exits.get(attach['pid'], [])):
            continue
        if nxt[0][1] not in parent_of:
            roots[nxt[0][1]] = 'R2:' + window[0]
    nodes = {node for _, node, _ in seen}
    unattributed = []
    for node in sorted(nodes):
        cur, guard = node, 0
        while cur not in roots and cur in parent_of and guard < 1000:
            cur, guard = parent_of[cur], guard + 1
        if cur not in roots:
            unattributed.append(node)
    return nodes, parent_of, roots, unattributed


def ancestors(node, parent_of):
    chain, cur = [], node
    while cur in parent_of and len(chain) < 1000:
        cur = parent_of[cur]
        chain.append(cur)
    return chain


def root_of(node, parent_of, roots):
    for cur in [node] + ancestors(node, parent_of):
        if cur in roots:
            return roots[cur]
    return None


def judge_k4(events, k4, parent_of, roots):
    """Exactly the 40 written pids, each with one sys_enter of its set's syscall then one exec; no other exec in the
    driver's tree."""
    reasons = []
    expected = {pid: way for way in ('execve', 'execveat') for pid in k4['pids'][way]}
    execs = [e for e in events if e['k'] == 'E' and root_of(e['id'], parent_of, roots) == 'R0']
    calls = [e for e in events if e['k'] == 'S' and root_of(e['id'], parent_of, roots) == 'R0']
    by_pid = {}
    for e in execs:
        by_pid.setdefault(e['id'][0], []).append(e)
    if set(by_pid) != set(expected):
        reasons.append('K4 exec pids differ from the written set (%d seen, %d expected)' % (len(by_pid), len(expected)))
    for pid, way in expected.items():
        mine = by_pid.get(pid, [])
        sys_calls = [c for c in calls if c['id'][0] == pid]
        if len(mine) != 1:
            reasons.append('K4 pid %d: %d exec events' % (pid, len(mine)))
            continue
        before = [c for c in sys_calls if c['t'] <= mine[0]['t'] and c['id'] == mine[0]['id']]
        if not before or before[-1]['call'] != way:
            reasons.append('K4 pid %d: no %s syscall before its exec' % (pid, way))
    if any(status != 0 for status in k4.get('statuses', [1])):
        reasons.append('K4 child exit statuses not all 0')
    return reasons


def judge_k3_k2(events, parent_of, roots, container_ns, windows):
    """K3: one /bin/true exec under the K3 session root, in the container's user and mnt ns. K2: a /bin/true exec
    under the K2 session root, descended from bwrap / codex-linux-sandbox, with user or mnt ns different."""
    reasons = []
    for name in ('K3', 'K2'):
        if windows.get(name, (None, None, 1))[2] != 0:
            reasons.append('%s command rc %s' % (name, windows.get(name, (None, None, None))[2]))
    true = [e for e in events if e['k'] == 'E' and e['file'].rstrip('\x00') in ('/bin/true', '/usr/bin/true')]
    k3 = [e for e in true if root_of(e['id'], parent_of, roots) == 'R2:K3']
    k2 = [e for e in true if root_of(e['id'], parent_of, roots) == 'R2:K2']
    if len(k3) != 1 or (k3[0]['userns'], k3[0]['mntns']) != container_ns:
        reasons.append('K3: expected one /bin/true in the container ns, saw %d' % len(k3))
    execs = {e['id']: e for e in events if e['k'] == 'E'}
    good = []
    for e in k2:
        chain = [execs.get(a) for a in ancestors(e['id'], parent_of)]
        launched = any(a is not None and os.path.basename(a['file'].rstrip('\x00')) in LAUNCHERS for a in chain)
        other_ns = (e['userns'], e['mntns']) != container_ns and (
            e['userns'] != container_ns[0] or e['mntns'] != container_ns[1])
        if launched and other_ns:
            good.append(e)
    if not good:
        reasons.append('K2: no /bin/true descended from %s in a different user or mnt ns' % '/'.join(LAUNCHERS))
    return reasons


def validate(summary, lines, r1, driver, k4, windows, container_ns, tmp_problems, r1_after):
    """The whole observer validation; returns (validated, reasons). Any reason -> not validated (rc 16)."""
    events = parse(lines)
    if summary.get('endSeen') and summary.get('endLine'):
        try:
            summary = dict(summary, endCount=int(summary['endLine'].split('\t')[1]))
        except (ValueError, IndexError):
            summary = dict(summary, endCount=-1)
    reasons = clean_exit(summary, len(events))
    nodes, parent_of, roots, unattributed = lineage(events, r1, driver, windows)
    k_nodes = [n for n in unattributed]
    if k_nodes:
        reasons.append('%d unattributed node(s) (no exact edge, not R0 / R1 / R2)' % len(k_nodes))
    reasons += judge_k4(events, k4, parent_of, roots) if k4 else ['K4 did not run']
    reasons += judge_k3_k2(events, parent_of, roots, container_ns, windows)
    reasons += tmp_problems
    if {(p, t) for p, t, _, _ in r1} - {(p, t) for p, t, _, _ in r1_after}:
        reasons.append('an R1 process (uvicorn / app-server) disappeared or changed between K3 and K2')
    reasons += window_processes(events, parent_of, roots, windows)
    return not reasons, reasons


def window_processes(events, parent_of, roots, windows):
    """PM D-P2 r1 §3 (same-uid processes): a new process under an R1 root (the container's own processes, i.e. not a
    K3 / K2 session) created between the start of K3 and the end of K2. Recorded with attribution unknown; it blocks
    a pass. This is separate from the R1 roots being alive."""
    if 'K3' not in windows or 'K2' not in windows:
        return []
    start, end = windows['K3'][0], windows['K2'][1]
    found = [e['child'] for e in events if e['k'] == 'F' and start <= e['t'] <= end
             and root_of(e['child'], parent_of, roots) == 'R1']
    return ['%d new process(es) under the container\'s own processes during the K3..K2 window (attribution unknown)'
            % len(found)] if found else []


# --- the approval time rule (P-A r3 §0), used by v6-item's judge; fixtures in the P-A tests (L11) ---------------------

def classify_root(t_root, m0, m1, linked, records_ok, observer_ok, eps=EPS_NS):
    """A sandbox-launch root at t_root tied to an item with send boundaries m0 / m1 (CLOCK_MONOTONIC ns). Without an
    established link the root says nothing about that approval in any time range (review round 1): unverified. The
    approval-free rule below is the only judgement made without a link."""
    if not (records_ok and observer_ok and linked):
        return 'unverified'
    if t_root < m0 - eps:
        return 'counterexample candidate'
    if t_root <= m1 + eps:
        return 'unverified'
    return 'consistent'


def approval_free(records_complete, dispatched, observer_clean, sandbox_roots, rollout_exec_calls):
    """The only case judged without an id link: complete records with 0 dispatched, a clean observer, >= 1 sandbox
    root in the turn window and >= 1 rollout exec call -> True (counterexample); False otherwise."""
    return bool(records_complete and dispatched == 0 and observer_clean and sandbox_roots >= 1
                and rollout_exec_calls >= 1)
