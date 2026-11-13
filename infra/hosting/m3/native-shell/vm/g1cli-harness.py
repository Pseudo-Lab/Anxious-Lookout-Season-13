"""#68 G1-CLI: the G1 measurement with the CLI launch `codex sandbox -P native` (design g1cli r1 `7ce1eea5…`, review
"accepted for code" with implementation conditions). Development VM only. It runs inside the measurement Pod
g1-dotcodex as UID 10001, with no model, no credentials and deny-all network. It is started by g1cli-dotcodex.sh after
review and a PM go.

**Scope** (fixed wording, on every record): protection behaviour in the `native` profile's bwrap view under the CLI's
config build (codex sandbox -P native, Codex 0.160.1, this measurement Pod). It does not verify app-server
command/exec, actual-turn T2, T1, T3 or the approval boundary, and it does not replace G4.

**Base.** Everything that is not the launch vehicle comes from the approved G1 harness r4 (`g1-harness-r4.py`,
sha256 BASE_SHA, sent by the host script as BASE_B64 and checked here), loaded unchanged as a module:
- the probe, the diagnosis §A/§B;
- every verdict function (judge, judge_s0, gate_result, judge_c8, l_steps, judge_l, judge_l1, conclusion,
  case_transition);
- snapshots, the process helpers (Pin, reap_all, chain_to, token_procs);
- the placeholder helper, policy_check and AppServer (start, the RPC and end_normally only; used for the policy
  proxy and K1/K2), environment, pins, case_dirs.
Not used (r2): own_children, end_and_check, force_end, stop_case, k_case. Their cleanup reaps and scans without
keeping failures; the CLI side ends every process itself (strict_collect, strict_end_app).

**New here:**
- **R1:** the launch handle (CliLaunch: start, wait_own, pin_tree, collect as separate steps; nothing collects on
  its own), and the per-case order.
- **R2:** per-stream reader states (eof / read_error / stopped) and one launch deadline. `rec` comes only from
  stdout at EOF within the deadline, untruncated, without a read error.
- **R3:** require_clean / next_step before every next launch; contamination stops at once.
- **R4:** the launcher chain as observed.
- **R5:** policy_proxy, reported apart, with its limit and side effects.

**r1** (review code r0, majors 1-5 and minors):
- the gate stops at the failing step: no launch after a policy failure, no C0 after an S0 failure;
- path facts are present / absent / error; a lookup error is never an effect or an absence;
- strict_collect remembers every lookup, pin and signal failure (also after policy_proxy and K1/K2);
- C8 waits are bounded by the one launch deadline; go only with GO_MIN_S left;
- an exception after a start: the handle is collected (owned), the case is harness_error and the run stops;
- the real byte counts with the reason apart; scope and the proxy limit on every output.

**r2** (review code r1, majors 1-2):
- the diagnosis adapter (cli_diagnose) decides each stream's completeness from the reader state and makes the base
  mark it incomplete whatever the bytes (non-UTF-8 included); raw counts recorded, the inspected unit named;
- the policy proxy and K1/K2 own their app-server from start to end (app_session / strict_end_app): the app-server's
  own wait first, then strict_collect; no base cleanup runs before it.

**rc:**
- 0: every planned case ran;
- 2: a gate stopped the run (environment or pins; K2; a case's start or gate);
- 3: contamination (leftovers, an unconfirmed end, a pin or lookup failure in a collect, a reader not joined), or
  a harness error after a start (collected first).
"""
import json
import os
import select
import signal
import subprocess
import sys
import threading
import time
import uuid

BASE_SHA = '3672bf3417ab4e83c3b3294f9cbfe9e1f48c047515c99dde103b326813f6bb33'  # g1-harness r4 (approved, 7dc0697)
VEHICLE = 'codex_sandbox_cli'
SCOPE = ("protection behaviour in the native profile's bwrap view under the CLI's config build (codex sandbox -P "
         "native, Codex 0.160.1, this measurement Pod); not app-server command/exec, not actual-turn T2, not T1, T3 "
         "or the approval boundary; does not replace G4")
PROXY_LIMIT = ("policy_proxy: app-server config/read + configRequirements/read for the case paths, checked with the "
               "image's native_policy; a proxy for the CLI's config build, not the CLI's resolved policy (equal "
               "effective policy not shown); it runs no command but has side effects (app-server creates its arg0 "
               "alias directory and lock in the case CODEX_HOME)")
CAP = 64 * 1024                 # bytes kept per stream; the rest is read and counted
EXEC_S, HOLD_S, READY_S, END_S, GONE_S, JOIN_S = 30, 90, 10, 20, 10, 2
GO_MIN_S = 2                    # C8: go only with at least this much of the launch deadline left (r1 major 4)

B = None                        # the base module (g1-harness r4), set by base()


def base():
    """The approved G1 harness r4 as a module: from BASE_B64 (sha256 == BASE_SHA) in the Pod, or from G1_BASE (a path)
    in the Docker tests. Its main() never runs (its __name__ is not __main__). HELPER_B64 is passed on to it."""
    global B
    if B is None:
        import hashlib
        import types
        src = globals().get('BASE_B64')
        if src is not None:
            import base64
            data = base64.b64decode(src)
        else:
            data = open(os.environ['G1_BASE'], 'rb').read()
        if hashlib.sha256(data).hexdigest() != BASE_SHA:
            raise RuntimeError('base digest')
        m = types.ModuleType('g1base')
        m.__dict__['__name__'] = 'g1base'
        if 'HELPER_B64' in globals():
            m.__dict__['HELPER_B64'] = globals()['HELPER_B64']
        exec(compile(data, 'g1-harness-r4.py', 'exec'), m.__dict__)
        B = m
    return B


# --- r1 (review code r0 major 3): strict process scans that remember every failure ------------------------------
def _read_stat(pid_name):
    with open('/proc/%s/stat' % pid_name) as f:
        return f.read()


def strict_children(listdir=os.listdir, read_stat=_read_stat):
    """(children, lookup_errors): this harness's children from /proc. A process that vanished during the scan
    (FileNotFoundError / ProcessLookupError) is not an error; any other lookup failure, including the /proc listing,
    is counted and never read as "not a child"."""
    me = os.getpid()
    try:
        names = [n for n in listdir('/proc') if n.isdigit()]
    except OSError:
        return [], 1
    kids, errors = [], 0
    for n in names:
        try:
            text = read_stat(n)
            ppid = int(text[text.rindex(')') + 2:].split()[1])
        except (FileNotFoundError, ProcessLookupError):
            continue
        except (OSError, ValueError, IndexError):
            errors += 1
            continue
        if ppid == me:
            kids.append(int(n))
    return kids, errors


def vanished(pid):
    try:
        os.stat('/proc/%d' % pid)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def strict_collect(gone_s, scan=None, pin=None):
    """Reap, then pin (pidfd + start time) and SIGKILL every child of this harness, until a scan finds no child and
    no lookup error, or gone_s passes. Every lookup, pin and signal failure is counted and kept: a later empty scan
    never clears it. failures > 0 means the collect is not clean (R3)."""
    b = base()
    scan = scan or strict_children
    pin = pin or b.Pin
    end = time.monotonic() + gone_s
    st = {'none_left': False, 'lookup_errors': 0, 'pin_failures': 0, 'signal_failures': 0, 'signalled': 0,
          'rounds': 0}
    while True:
        b.reap_all()
        kids, errs = scan()
        st['lookup_errors'] += errs
        if not kids and not errs:
            st['none_left'] = True
            break
        if time.monotonic() >= end:
            break
        st['rounds'] += 1
        for pid in kids:
            p = pin(pid)
            if not p.ok():
                if not vanished(pid):          # not pinnable and still there: a failure, not "gone"
                    st['pin_failures'] += 1
            elif p.send(signal.SIGKILL):
                st['signalled'] += 1
            elif not p.exited(0):
                st['signal_failures'] += 1
            p.close()
        time.sleep(0.2)
    st['failures'] = st['lookup_errors'] + st['pin_failures'] + st['signal_failures']
    return st


# --- R2: one reader per stream ----------------------------------------------------------------------------------
class Stream:
    """Reads one pipe in its own thread: keeps at most `cap` bytes, counts all, ends in exactly one state: 'eof',
    'read_error:<ERRNO>' (never treated as EOF) or 'stopped' (stop_and_join before EOF). A select timeout keeps every
    wait finite, so a stop always ends the thread within JOIN_S even when a descendant holds the pipe."""

    def __init__(self, fd, cap=CAP, read_fn=None):
        self.fd, self.cap = fd, cap
        self.read_fn = read_fn or os.read
        self.kept, self.seen, self.state, self.eof_at = bytearray(), 0, 'reading', None
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                ready, _, _ = select.select([self.fd], [], [], 0.2)
            except (OSError, ValueError) as e:
                self.state = 'read_error:' + (base().errname(e) if isinstance(e, OSError) else 'other')
                return
            if not ready:
                continue
            try:
                chunk = self.read_fn(self.fd, 65536)
            except OSError as e:
                self.state = 'read_error:' + base().errname(e)
                return
            if not chunk:
                self.eof_at = time.monotonic()
                self.state = 'eof'
                return
            self.seen += len(chunk)
            room = self.cap - len(self.kept)
            if room > 0:
                self.kept += chunk[:room]
        self.state = 'stopped'

    def wait_eof(self, until):
        """Wait for the thread to end on its own (EOF or a read error) until `until` (monotonic); True at EOF."""
        self._t.join(timeout=max(0.0, until - time.monotonic()))
        return self.state == 'eof'

    def stop_and_join(self, timeout=JOIN_S):
        """Stop and join within `timeout`; the fd is closed by its owner (CliLaunch closes the Popen file objects)."""
        self._stop.set()
        self._t.join(timeout)
        return not self._t.is_alive()

    def view(self, t0):
        return {'state': self.state, 'bytes_seen': self.seen, 'truncated': self.seen > self.cap,
                'eof_at': round(self.eof_at - t0, 3) if self.eof_at is not None else None}


# --- R1: the launch handle ----------------------------------------------------------------------------------------
class CliLaunch:
    """One CLI launch. start (here) -> wait_own -> pin_tree / handshake -> collect, each called explicitly.
    wait_own is the only place the wrapper's status is taken (Popen.wait), and collect never reaps anything else before
    the wrapper's own status was collected. The status is decided by one deadline (t0 + limit_s)."""

    def __init__(self, argv, env, cwd, limit_s, read_fn=None, cap=CAP):
        self.limit_s = limit_s
        self.t0 = time.monotonic()
        self.deadline = self.t0 + limit_s
        self.proc = subprocess.Popen(argv, env=env, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, start_new_session=True)
        self.pin = base().Pin(self.proc.pid)
        self.streams = {'stdout': Stream(self.proc.stdout.fileno(), cap, read_fn),
                        'stderr': Stream(self.proc.stderr.fileno(), cap, read_fn)}
        self.exit_code, self.wrapper_exit_at, self.collected, self.tree = None, None, None, None

    def wait_own(self, until):
        """Popen.wait on the wrapper until `until`. True once its status is collected; False on timeout (not reaped)."""
        if self.proc.returncode is not None:
            return True
        try:
            self.exit_code = self.proc.wait(timeout=max(0.0, until - time.monotonic()))
        except subprocess.TimeoutExpired:
            return False
        self.wrapper_exit_at = time.monotonic()
        return True

    def wait_streams(self, until):
        for s in self.streams.values():
            s.wait_eof(until)

    def status(self):
        """ok: the wrapper's exit was observed by the deadline; timeout: not by the deadline; running: not yet known."""
        if self.wrapper_exit_at is None:
            return 'running' if self.collected is None else 'timeout'
        return 'ok' if self.wrapper_exit_at <= self.deadline else 'timeout'

    def pin_tree(self, tok, expect):
        """R4: the probe's python processes carrying `tok` (role python), `expect` of them, pinned; their observed
        ancestors up to (not including) this harness, pinned. reached needs: the expected count, the wrapper among the
        ancestors, every pin valid. Returns the tree record; the pins stay in self.tree for the observation steps."""
        b = base()
        procs = b.token_procs(tok)
        tree = {'procs': len(procs), 'expect': expect, 'reached': False, 'chain_roles': None}
        if len(procs) != expect:
            self.tree = dict(tree, pins=[], top=None, child=None)
            return tree
        child = top = None
        if expect == 2:
            a, c = procs
            child, top = (a, c) if b.ppid_of(a) == c else (c, a) if b.ppid_of(c) == a else (None, None)
        else:
            top = procs[0]
        if top is None:
            self.tree = dict(tree, pins=[], top=None, child=None)
            return tree
        chain = b.chain_to(top, os.getpid())
        pins = [b.Pin(pid) for pid, _, _ in chain]
        top_pin = b.Pin(top)
        child_pin = b.Pin(child) if child else None
        wrapper_in = any(pid == self.proc.pid for pid, _, _ in chain)
        ok = (wrapper_in and top_pin.ok() and all(p.ok() for p in pins) and (child_pin is None or child_pin.ok()))
        tree.update(reached=ok, chain_roles=[r for _, _, r in chain], wrapper_in_chain=wrapper_in)
        self.tree = dict(tree, pins=pins, top=top_pin, child=child_pin)
        return tree

    def collect(self, gone_s=GONE_S):
        """The only cleanup. If the wrapper was not reaped: SIGKILL to its own process group (its pid is not reaped,
        so the group id is still ours), then Popen.wait for its real status. Only after that: strict_collect (every
        lookup, pin and signal failure remembered). Then the readers are stopped (bounded). end_confirmed needs the
        wrapper's status collected, none left and no failure."""
        t = time.monotonic()
        group_killed = None
        if self.proc.returncode is None:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
                group_killed = True
            except OSError:
                group_killed = False
            try:
                self.exit_code = self.proc.wait(timeout=gone_s)
            except subprocess.TimeoutExpired:
                pass
        wrapper_reaped = self.proc.returncode is not None
        st = {'none_left': False, 'failures': None}
        if wrapper_reaped:                      # only after the wrapper's own status was collected (①)
            st = strict_collect(gone_s)
        joined = [s.stop_and_join() for s in self.streams.values()]
        if all(joined):  # the pipes are closed only when no reader can still use them
            for f in (self.proc.stdout, self.proc.stderr):
                try:
                    f.close()
                except OSError:
                    pass
        for p in ((self.tree or {}).get('pins') or []) + [x for x in ((self.tree or {}).get('top'),
                                                                     (self.tree or {}).get('child')) if x]:
            p.close()
        self.pin.close()
        none_left = st['none_left'] is True
        self.collected = dict(st, wrapper_reaped=wrapper_reaped, none_left=none_left,
                              end_confirmed=wrapper_reaped and none_left and st.get('failures') == 0,
                              readers_joined=all(joined), group_killed=group_killed,
                              collect_s=round(time.monotonic() - t, 2))
        return self.collected

    def result(self, op, codex_home, cwd):
        """The launch record. rec only from stdout at EOF by the deadline, untruncated, no read error (R2). For the
        diagnosis a stream not at EOF counts as incomplete."""
        b = base()
        so, se = self.streams['stdout'], self.streams['stderr']
        complete = (so.state == 'eof' and so.eof_at is not None and so.eof_at <= self.deadline and so.seen <= so.cap)
        stdout = bytes(so.kept).decode('utf-8', 'replace')
        rec = b.parse_probe(stdout, op) if complete else None
        diag = cli_diagnose({n: {'kept': bytes(s.kept), 'seen': s.seen, 'state': s.state, 'cap': s.cap}
                             for n, s in self.streams.items()}, self.exit_code, op, rec is not None, codex_home)
        diag['compare'] = b.compare_effective(diag.get('effective'), cwd)
        c = self.collected or {}
        return {'vehicle': VEHICLE, 'status': self.status(), 'exit_code': self.exit_code, 'rec': rec, 'diag': diag,
                'streams': {n: s.view(self.t0) for n, s in self.streams.items()},
                'wrapper_exit_at': round(self.wrapper_exit_at - self.t0, 3) if self.wrapper_exit_at else None,
                'requested': {'profile': b.PROFILE, 'cwd': cwd, 'limit_s': self.limit_s},
                'end_confirmed': c.get('end_confirmed') is True, 'none_left': c.get('none_left') is True,
                'readers_joined': c.get('readers_joined') is True, 'collect': c,
                'reached': b.well_formed(rec) and rec['called'] is True,
                'uid_inside': rec['uid'] if b.well_formed(rec) else None, 'profile': b.PROFILE}


# --- r2 (review code r1 major 1): the diagnosis adapter; the stream's completeness is decided here ---------------
def stream_complete(state, seen, cap):
    """A stream is complete only at EOF with every received byte kept. read_error / stopped / over the cap: not."""
    return state == 'eof' and seen <= cap


def diag_input(kept, seen, complete):
    """(text, received) for the base diagnose. The base reads one channel for completeness: a stream is incomplete
    when `received` exceeds the UTF-8 length of the text as it re-encodes it (after a replacement decode, U+FFFD is 3
    bytes, so that length can exceed the raw byte count). For an incomplete stream `received` is therefore that same
    re-encoded length + 1, which always marks it incomplete whatever the bytes were (r1's seen + 1 did not, with
    non-UTF-8 input). This is an adapter value: it is never recorded; cli_diagnose records the real counts."""
    text = bytes(kept).decode('utf-8', 'replace')
    return text, (seen if complete else len(text.encode('utf-8', 'replace')) + 1)


def cli_diagnose(streams, exit_code, op, record_valid, codex_home):
    """The base §A diagnosis for one CLI launch. streams: name -> {'kept': raw bytes, 'seen': raw byte count, 'state',
    'cap'}. Completeness comes from the reader state (stream_complete), not from any decoded length; the adapter
    checks that the base marked every incomplete stream incomplete (else RuntimeError: never a silent pass).
    Recorded per stream:
    - bytes_received: raw bytes the reader received; bytes_kept: raw bytes kept (<= cap);
    - bytes_inspected (base): UTF-8 bytes of the kept prefix after a replacement decode, at most INSPECT_BYTES; a
      count of that representation, not of raw bytes (bytes_inspected_unit says so);
    - stream_complete and incomplete_reason (None / truncated / stream_read_error / stream_stopped / stream_reading).
    codex_cap_hint is recomputed from the raw counts (the base computed it from the adapter value)."""
    b = base()
    done = {n: stream_complete(s['state'], s['seen'], s['cap']) for n, s in streams.items()}
    inp = {n: diag_input(s['kept'], s['seen'], done[n]) for n, s in streams.items()}
    diag = b.diagnose(inp['stdout'][0], inp['stderr'][0], exit_code, op, record_valid,
                      {n: v[1] for n, v in inp.items()}, codex_home=codex_home)
    for n, s in streams.items():
        if not done[n] and diag[n].get('incomplete') is not True:
            raise RuntimeError('diagnosis adapter: an incomplete stream was not marked incomplete')
        diag[n] = dict(diag[n], bytes_received=s['seen'], bytes_kept=len(s['kept']), stream_complete=done[n],
                       bytes_inspected_unit='utf8_of_replacement_decoded_kept_prefix',
                       incomplete_reason=None if done[n] else
                       ('truncated' if s['seen'] > s['cap'] else 'stream_' + s['state'].split(':')[0]))
    diag['codex_cap_hint'] = any(s['seen'] >= b.CODEX_CAP_BYTES for s in streams.values())
    return diag


# --- pure: execution evidence and the transition (R2/R3) ---------------------------------------------------------
def cli_exec_valid(r, op, cwd):
    """The same conditions as exec_valid with the RPC replaced by the process end: status ok (the wrapper exited by the
    deadline) and end confirmed, exit 0, a well-formed record of exactly `op`, uid 10001, the requested native
    profile and the case cwd."""
    b = base()
    req = r.get('requested') if isinstance(r, dict) else None
    return (isinstance(r, dict) and r.get('status') == 'ok' and r.get('end_confirmed') is True
            and r.get('exit_code') == 0 and b.well_formed(r.get('rec')) and r['rec']['op'] == op
            and r['rec']['uid'] == b.UID and isinstance(req, dict) and req.get('profile') == b.PROFILE
            and req.get('cwd') == cwd)


def require_clean(r):
    """R3: a launch is clean only with its end confirmed, none left and both readers joined."""
    return (isinstance(r, dict) and r.get('end_confirmed') is True and r.get('none_left') is True
            and r.get('readers_joined') is True)


def next_step(clean):
    """R3 (pure): continue only when the last collect was clean; otherwise the case stops as contamination."""
    return 'continue' if clean is True else 'contamination_stopped'


LAUNCH_VIEW = ('vehicle', 'status', 'exit_code', 'rec', 'diag', 'streams', 'wrapper_exit_at', 'requested',
               'end_confirmed', 'none_left', 'readers_joined', 'collect', 'reached', 'uid_inside', 'profile')


def launch_view(r):
    return {k: r.get(k) for k in LAUNCH_VIEW} if isinstance(r, dict) else r


# --- launching ---------------------------------------------------------------------------------------------------
def cli_argv(d, op, args):
    b = base()
    return [b.CODEX, 'sandbox', '-P', b.PROFILE, '-C', d['work'], '--', b.PYTHON, '-I', '-c', b.PROBE, op] + list(args)


def run_simple(d, op, args, limit_s=EXEC_S):
    """R1 simple launch: start -> wait_own(deadline) -> readers to EOF or deadline -> collect -> record."""
    launch = CliLaunch(cli_argv(d, op, args), d['env'], d['work'], limit_s)
    with owned(launch):
        launch.wait_own(launch.deadline)
        launch.wait_streams(launch.deadline)
        launch.collect()
    return launch.result(op, d['codex'], d['work'])


def stop_contaminated(rec, where, r=None):
    """R3: no further launch; the case is not clean (case_transition -> contamination_stopped, rc 3)."""
    rec.setdefault('contamination', []).append({'at': where, 'collect': (r or {}).get('collect')})
    rec.setdefault('verdict', 'inconclusive')
    rec.setdefault('reason', 'contamination:' + where)
    rec['clean'] = False
    return rec, False, 'contamination'


# --- r2 (review code r1 major 2): the app-server of policy_proxy and K1/K2, owned and ended on the CLI side -------
def strict_end_app(app, gone_s=GONE_S):
    """End the app-server without the base end_and_check / force_end. Those reap and scan before keeping any
    failure: a pin failure on a child that then ends on its own would leave no trace. Order:
    1. app.end_normally(): stdin closed, Popen.wait on the app-server itself only (no other reap);
    2. still running: SIGTERM, then SIGKILL, through its pidfd pin, each followed by Popen.wait on it. A pin or
       send failure while it still runs is counted;
    3. only once its own status was collected: strict_collect (every lookup, pin, signal failure kept);
    4. its pin closed.
    clean needs its status collected, none left and no failure in 2 or 3."""
    t = time.monotonic()
    ended = app.end_normally() is True                 # exit 0 within the base END_S
    forced, force_failures = False, 0
    for sig, wait_s in ((signal.SIGTERM, END_S), (signal.SIGKILL, gone_s)):
        if app.proc.returncode is not None:
            break
        forced = True
        if not (app.pin.ok() and app.pin.send(sig)) and app.proc.poll() is None:
            force_failures += 1
        try:
            app.proc.wait(timeout=wait_s)
        except subprocess.TimeoutExpired:
            pass
    reaped = app.proc.returncode is not None
    st = strict_collect(gone_s) if reaped else {'none_left': False, 'failures': None}
    app.pin.close()
    clean = reaped and st['none_left'] is True and st['failures'] == 0 and force_failures == 0
    return {'ended': ended, 'forced': forced, 'force_failures': force_failures, 'reaped': reaped,
            'exit_code': app.proc.returncode, 'strict': st, 'clean': clean, 'end_s': round(time.monotonic() - t, 2)}


def app_session(d, check_fn=None, app_fn=None):
    """policy_proxy / K1 / K2: an app-server for the case paths is started, check_fn(app, work) reads config and
    requirements (no command), then strict_end_app; on an exception the app-server is ended the same way first and
    the exception (with .cleanup) is re-raised, so the run stops. app_fn / check_fn are the base AppServer /
    policy_check; the tests pass a local stand-in process. Returns a dict."""
    b = base()
    app = (app_fn or b.AppServer)(d['home'], d['codex'], d['work'], d['tmp'])
    try:
        environment = b.environment_record(app)
        env = dict(app.env)
        started = app.start() is True
        ok, names = (check_fn or b.policy_check)(app, d['work']) if started else (False, ['app_server_start'])
    except Exception as e:
        e.cleanup = strict_end_app(app)
        raise
    return {'started': started, 'ok': ok, 'checks': names, 'end': strict_end_app(app), 'environment': environment,
            'env': env}


def policy_proxy(d, app_fn=None, check_fn=None):
    """R5: an app-server for the case paths reads config and requirements (no command), is ended and its processes
    collected (strict_end_app); it also provides the CLI's environment (native_command env + TMPDIR, same values)."""
    s = app_session(d, check_fn, app_fn)
    d['env'] = s['env']
    e = s['end']
    return s['ok'], s['checks'], {'ok': s['ok'], 'checks': s['checks'], 'ended': e['ended'], 'forced': e['forced'],
                                  'clean': e['clean'], 'end': e, 'limit': PROXY_LIMIT}, s['environment']


def new_rec(cid, d, ev, op=None):
    rec = {'id': cid, 'vehicle': VEHICLE, 'scope': SCOPE, 'policy_proxy_limit': PROXY_LIMIT, 'placeholder': ev,
           'placement': base().placement(d)}
    if op:
        rec['op'] = op
    return rec


def gate_stop(rec, g, ok, failed):
    """r1 (major 1): the gate record completed at the failing step, then gate_stopped with no further launch."""
    g.update(ok=ok, failed=failed)
    rec.update(gate_ok=ok, gate=g, verdict='inconclusive', reason='gate_failed', clean=True)
    return None, (rec, True, 'gate_failed')


def cli_gate(d, rec, proxy_fn=None, run_fn=None):
    """policy_proxy -> R3(a) -> [policy failed: stop, no launch] -> S0 -> R3(b) -> [S0 failed: stop, no C0] -> C0
    -> R3(c) -> gate_result. Returns (gate, None) to continue or (None, stopped triple). proxy_fn / run_fn are the
    real policy_proxy / run_simple; the tests pass recorders to show which launches happen."""
    b = base()
    proxy_fn = proxy_fn or policy_proxy
    run_fn = run_fn or run_simple
    pol_ok, names, proxy, environment = proxy_fn(d)
    rec.update(policy_proxy=proxy, environment=environment, state_after_proxy=b.ref_state(d))
    if next_step(proxy['clean']) != 'continue':
        return None, stop_contaminated(rec, 'policy_proxy')
    g = {'policy_proxy_ok': pol_ok, 'policy_checks': names, 's0': None, 's0_why': None, 's0_errno': None, 'c0': None,
         'launches': []}
    if pol_ok is not True:
        return gate_stop(rec, g, *b.gate_result(pol_ok, None, None, None))
    pre = d['home'] + '/pre-s0'
    try:
        open(pre, 'w').close()
        os.unlink(pre)
        pod_writable = True
    except OSError:
        pod_writable = False
    w = d['work']
    s0 = run_fn(d, 'create', [d['home'] + '/s0'])
    g['launches'].append(launch_view(s0))
    if next_step(require_clean(s0)) != 'continue':
        rec['gate'] = g
        return None, stop_contaminated(rec, 's0', s0)
    s0_ok, s0_why = b.judge_s0(pod_writable, cli_exec_valid(s0, 'create', w), s0['rec'])
    g.update(s0=s0_ok, s0_why=s0_why, s0_errno=(s0['rec'] or {}).get('errno'))
    if s0_ok is not True:
        return gate_stop(rec, g, *b.gate_result(pol_ok, s0_ok, None, None))
    c0 = run_fn(d, 'create', [w + '/ctl/c0'])
    g['launches'].append(launch_view(c0))
    if next_step(require_clean(c0)) != 'continue':
        rec['gate'] = g
        return None, stop_contaminated(rec, 'c0', c0)
    c0_valid = cli_exec_valid(c0, 'create', w) and c0['rec']['ok'] is True
    g['c0'] = c0_valid
    ok, failed_part = b.gate_result(pol_ok, s0_ok, c0_valid, path_fact(w + '/ctl/c0') == 'present')
    if not ok:
        return gate_stop(rec, g, ok, failed_part)
    g.update(ok=True, failed=None)
    rec.update(gate_ok=True, gate=g)
    return g, None


# --- r1 (review code r0 major 2): path facts with three values; a lookup error is never an effect or an absence --
def path_fact(p):
    """'present' (lstat ok), 'absent' (ENOENT), or 'error:<ERRNO>' (any other lookup failure, e.g. EACCES)."""
    try:
        os.lstat(p)
        return 'present'
    except FileNotFoundError:
        return 'absent'
    except OSError as e:
        return 'error:' + base().errname(e)


def fact_bool(f):
    """present -> True, absent -> False, an error (or None) -> None (unknown)."""
    return True if f == 'present' else False if f == 'absent' else None


def not3(v):
    return None if v is None else (not v)


def or3(*vals):
    """Three-valued or: True if any is True; else None if any is unknown; else False."""
    if any(v is True for v in vals):
        return True
    return None if any(v is None for v in vals) else False


def and3(*vals):
    """Three-valued and: False if any is False; else None if any is unknown; else True."""
    if any(v is False for v in vals):
        return False
    return None if any(v is None for v in vals) else True


def target_effect(op, placeholder, new=None, dst=None, x_same=None):
    """Pure. The target's observed effect from path facts: True only from observed facts, None when a needed fact
    is a lookup error (never True), False otherwise. placeholder/new/dst are path_fact values; x_same is
    identity_same of the replace source (None when unreadable). The r0 terms, three-valued:
    placeholder absent, or (create/mkdir) the new path present, or (rename) the destination present, or (replace)
    the source's identity changed."""
    terms = [not3(fact_bool(placeholder))]
    if op in ('create', 'mkdir'):
        terms.append(fact_bool(new))
    if op == 'rename':
        terms.append(fact_bool(dst))
    if op == 'replace':
        terms.append(None if x_same is None else (x_same is False))
    return or3(*terms)


def control_extra(op, ctl_args, n_snap_ino, y_ino):
    """The control's path facts for base.intended: True/False only from observed presence/absence, None from a
    lookup error (intended then sees no effect, so the control fails: inconclusive, never NOT_PROTECTED)."""
    known = n_snap_ino is not None and y_ino is not None   # two unreadable inodes (None == None) are not a match
    return {'new_exists': fact_bool(path_fact(ctl_args[0])) if op in ('create', 'mkdir') else None,
            'src_exists': fact_bool(path_fact(ctl_args[0])), 'dst_exists': fact_bool(path_fact(ctl_args[-1])),
            'dst_ino': n_snap_ino if known else 'unknown', 'src_ino_before': y_ino}


def c8_effects(w, p):
    """C8 effects, three-valued (major 2)."""
    f = lambda q: fact_bool(path_fact(q))
    return {'c-rename': and3(not3(f(w + '/ctl/n8')), f(w + '/ctl/m8')), 'c-create': f(w + '/ctl/p8/f8'),
            't-rename': or3(not3(f(p)), f(w + '/m8')), 't-create': f(p + '/f8')}


# --- r1 (major 5): an active launch is always collected, also on an exception ------------------------------------
class owned:
    """`with owned(launch):` collects the launch on the way out if nothing collected it yet (normal or exception
    path). The exception is not swallowed: the case turns it into a harness_error record with clean False, so the run
    stops before the next case."""

    def __init__(self, launch):
        self.launch = launch

    def __enter__(self):
        return self.launch

    def __exit__(self, et, ev, tb):
        if self.launch.collected is None:
            self.launch.wait_own(time.monotonic())   # the wrapper's own status first, if already exited
            self.launch.collect()
        return False


def harness_error(rec, e, launch=None):
    rec.update(verdict='inconclusive', reason='harness_error:' + type(e).__name__, clean=False,
               cleanup=(launch.collected if launch is not None else None))
    return rec, False, 'harness_error'


def left_s(launch, cap):
    """r1 (major 4): a wait bounded by both its own cap and the launch deadline (0 once the deadline has passed)."""
    return max(0.0, min(cap, launch.deadline - time.monotonic()))


# --- cases -------------------------------------------------------------------------------------------------------
TGT_ARGS = {'create': lambda w, n: [w + '/.codex/f'], 'mkdir': lambda w, n: [w + '/.codex/d'],
            'rename': lambda w, n: [w + '/.codex', w + '/m'], 'rmdir': lambda w, n: [w + '/.codex'],
            'replace': lambda w, n: [w + '/ctl/x', w + '/.codex'], 'utime': lambda w, n: [w + '/.codex'],
            'chmod': lambda w, n: [w + '/.codex']}
CTL_ARGS = {'create': lambda w, n: [n + '/f'], 'mkdir': lambda w, n: [n + '/d'],
            'rename': lambda w, n: [n, w + '/ctl/m'], 'rmdir': lambda w, n: [n],
            'replace': lambda w, n: [w + '/ctl/y', n], 'utime': lambda w, n: [n], 'chmod': lambda w, n: [n]}


def protect_case(cid):
    """C1-C5, C7a, C7b: gate -> control -> R3(d) -> target -> R3(e); the r4 judge unchanged (a direct target success
    or effect stays NOT_PROTECTED, R6)."""
    b = base()
    op = b.PROTECT[cid]
    d = b.case_dirs(cid)
    w = d['work']
    ev = b.placeholder(d)
    n = w + '/ctl/n'
    os.mkdir(n, 0o755)
    if op == 'replace':
        os.mkdir(w + '/ctl/x', 0o755)
        os.mkdir(w + '/ctl/y', 0o755)
    tgt_args, ctl_args = TGT_ARGS[op](w, n), CTL_ARGS[op](w, n)
    rec = new_rec(cid, d, ev, op)
    g, stopped = cli_gate(d, rec)
    if stopped:
        return stopped
    c_before = b.snap(n)
    y_ino = b.snap(w + '/ctl/y').get('ino') if op == 'replace' else None
    ctl = run_simple(d, op, ctl_args)
    rec['control'] = launch_view(ctl)
    if next_step(require_clean(ctl)) != 'continue':
        return stop_contaminated(rec, 'control', ctl)   # the target is never started (R3 d)
    c_after = b.snap(n)
    c_extra = control_extra(op, ctl_args, b.snap(n).get('ino'), y_ino)
    ctl_effect = b.intended(op, c_before, c_after, c_extra)
    p = w + '/.codex'
    t_before = b.snap(p)
    x_before = b.snap(w + '/ctl/x') if op == 'replace' else None
    tgt = run_simple(d, op, tgt_args)
    t_after = b.snap(p)
    same = b.identity_same(t_before, t_after)
    empty = b.snap_empty(t_after)
    path_points = b.points_to_record(d)
    t_effect = target_effect(op, path_fact(p),
                             new=path_fact(tgt_args[0]) if op in ('create', 'mkdir') else None,
                             dst=path_fact(tgt_args[1]) if op == 'rename' else None,
                             x_same=b.identity_same(x_before, b.snap(w + '/ctl/x')) if op == 'replace' else None)
    ctl_valid, tgt_valid = cli_exec_valid(ctl, op, w), cli_exec_valid(tgt, op, w)
    verdict, why = b.judge(True, ctl_valid, ctl['rec'], ctl_effect, tgt_valid, tgt['rec'], t_effect, same, empty,
                           path_points)
    clean = require_clean(tgt)
    rec['control'] = dict(rec['control'], valid=ctl_valid, effect=ctl_effect)
    rec.update(target=dict(launch_view(tgt), valid=tgt_valid, effect=t_effect, same=same, empty=empty,
                           path_points=path_points),
               syscall_succeeded=bool(b.well_formed(tgt['rec']) and tgt['rec']['ok']), effect_observed=bool(t_effect),
               verdict=verdict, reason=why, clean=clean)
    if next_step(clean) != 'continue':
        return stop_contaminated(rec, 'target', tgt)    # the verdict above is kept as recorded (R6)
    return rec, True, None


def c8_observe(launch, w, tok, p, cdir):
    """C8 observation, in the R1 order; nothing is collected here. r1 (major 4): every wait is bounded by the one
    launch deadline (left_s) and `go` is written only with at least GO_MIN_S of it left (a conservative condition:
    with less, go is skipped and the case stays inconclusive). GO_MIN_S does not guarantee that the child finishes;
    only the evidence collected by the deadline is judged. The pins are those in launch.tree.
    ready -> pin_tree -> release -> wait_own -> the parent and every pinned ancestor ended -> go only if the pinned
    child is still alive and GO_MIN_S is left -> its result files and its end. Returns (obs, tree)."""
    b = base()
    obs = {'gate_ok': True, 'child_seen': False, 'launchers_gone': False, 'child_alive_after': None,
           'child_gone': None, 'go_written': False}
    ready_by = min(time.monotonic() + READY_S, launch.deadline)
    while not os.path.exists(cdir + '/ready-' + tok) and time.monotonic() < ready_by:
        time.sleep(0.1)
    tree = launch.pin_tree(tok, expect=2) if os.path.exists(cdir + '/ready-' + tok) else {'reached': False}
    obs['child_seen'] = tree.get('reached') is True
    open(cdir + '/release-' + tok, 'w').close()
    launch.wait_own(launch.deadline)
    t = launch.tree or {}
    if obs['child_seen']:
        top, cpin, pins = t['top'], t['child'], t['pins']
        obs['launchers_gone'] = (top.exited(left_s(launch, GONE_S))
                                 and all(x.exited(left_s(launch, GONE_S)) for x in pins))
        if obs['launchers_gone']:
            if cpin.exited(left_s(launch, 0.5)):
                obs['child_alive_after'] = False
                obs['child_gone'] = b.proc_gone(cpin.pid, cpin.start)
            else:
                obs['child_alive_after'] = (b.starttime(cpin.pid) == cpin.start
                                            and b.pstate(cpin.pid) not in ('Z', 'X', None))
                if obs['child_alive_after'] and launch.deadline - time.monotonic() >= GO_MIN_S:
                    open(cdir + '/go-' + tok, 'w').close()
                    obs['go_written'] = True
                    names = ('t-rename', 't-create', 'c-rename', 'c-create')
                    end = min(time.monotonic() + READY_S, launch.deadline)
                    while time.monotonic() < end and not all(
                            os.path.exists('%s/res-%s-%s' % (cdir, x, tok)) for x in names):
                        time.sleep(max(0.0, min(0.2, end - time.monotonic())))
                    results = {}
                    for x in names:
                        try:
                            results[x] = json.loads(open('%s/res-%s-%s' % (cdir, x, tok)).read())
                        except (OSError, ValueError):
                            results[x] = None
                    obs['results'] = results
                    obs['effects'] = c8_effects(w, p)
                    cpin.exited(left_s(launch, GONE_S))
    obs['deadline_passed'] = time.monotonic() >= launch.deadline
    return obs, tree


def c8_case():
    """C8 (R1 order): start(spawn) -> c8_observe (deadline-bounded) -> snapshots -> collect -> R3(g); an exception
    after the start is collected (owned) and stops the run (harness_error, not clean)."""
    b = base()
    d = b.case_dirs('C8')
    w = d['work']
    ev = b.placeholder(d)
    for sub in ('c8', 'ctl/n8', 'ctl/p8'):
        os.makedirs(w + '/' + sub, mode=0o755)
    rec = new_rec('C8', d, ev)
    g, stopped = cli_gate(d, rec)
    if stopped:
        stopped[0].update(lifecycle='unknown', surviving_descendant_protection='unmeasured')
        return stopped
    tok = uuid.uuid4().hex
    p = w + '/.codex'
    t_before = b.snap(p)
    launch = CliLaunch(cli_argv(d, 'spawn', [tok, w + '/c8', w]), d['env'], w, HOLD_S)
    try:
        with owned(launch):
            obs, tree = c8_observe(launch, w, tok, p, w + '/c8')
            rec['tree'] = {k: v for k, v in tree.items() if k in ('procs', 'expect', 'reached', 'chain_roles',
                                                                'wrapper_in_chain')}
            t_after = b.snap(p)
            obs['same'] = b.identity_same(t_before, t_after)
            obs['empty'] = b.snap_empty(t_after)
            obs['path_points'] = b.points_to_record(d)
            launch.wait_streams(launch.deadline)
            launch.collect()                           # only after the observation (R1)
    except Exception as e:                             # r1 major 5: collected by owned, then the run stops
        rec.update(lifecycle='unknown', surviving_descendant_protection='unmeasured')
        return harness_error(rec, e, launch)
    spawn = launch.result('spawn', d['codex'], w)
    obs['spawn_valid'] = cli_exec_valid(spawn, 'spawn', w)
    verdict, why = b.judge_c8(obs)
    clean = require_clean(spawn)
    rec.update(spawn=launch_view(spawn), obs={k: v for k, v in obs.items() if k not in ('results', 'effects')},
               results=obs.get('results'), effects=obs.get('effects'), verdict=verdict, reason=why,
               lifecycle=b.c8_lifecycle(obs), surviving_descendant_protection=b.surviving_protection(verdict),
               scope_c8='descendant_ended: this observed descendant did not exist after its launchers ended; not the '
                        'cause, not all descendants, not the protection of a surviving one', clean=clean)
    if next_step(clean) != 'continue':
        return stop_contaminated(rec, 'c8_final', spawn)
    return rec, True, None


def l1_case():
    """L1: five noop launches, R3(f) after each; judge_l1."""
    b = base()
    d = b.case_dirs('L1')
    w = d['work']
    ev = b.placeholder(d)
    rec = new_rec('L1', d, ev)
    g, stopped = cli_gate(d, rec)
    if stopped:
        return stopped
    p = w + '/.codex'
    first = b.snap(p)
    runs = []
    for _ in range(5):
        r = run_simple(d, 'noop', [])
        runs.append({'launch': launch_view(r), 'valid': cli_exec_valid(r, 'noop', w),
                     'same': b.identity_same(first, b.snap(p))})
        if next_step(require_clean(r)) != 'continue':
            rec['runs'] = runs
            return stop_contaminated(rec, 'l1_run_%d' % len(runs), r)
    verdict, why = b.judge_l1(True, runs)
    rec.update(runs=runs, verdict=verdict, reason=why, clean=True)
    return rec, True, None


def l_end_case(lid):
    """L2 (the wrapper's normal end after go), L3 (SIGTERM to the wrapper), L4 (SIGKILL to the wrapper), during a held
    command. R1 order: start(hold) -> ready -> pin_tree -> go / signal -> wait_own -> the snapshot before cleanup and
    the pinned processes' state -> collect -> R3(g) -> the snapshot after cleanup."""
    b = base()
    d = b.case_dirs(lid)
    w = d['work']
    ev = b.placeholder(d)
    os.makedirs(w + '/hold', mode=0o755)
    rec = new_rec(lid, d, ev)
    g, stopped = cli_gate(d, rec)
    if stopped:
        return stopped
    p = w + '/.codex'
    before = b.snap(p)
    tok = uuid.uuid4().hex
    launch = CliLaunch(cli_argv(d, 'hold', [tok, w + '/hold']), d['env'], w, HOLD_S)
    try:
        with owned(launch):
            ready_by = min(time.monotonic() + READY_S, launch.deadline)
            while not os.path.exists(w + '/hold/ready-' + tok) and time.monotonic() < ready_by:
                time.sleep(0.1)
            tree = launch.pin_tree(tok, expect=1) if os.path.exists(w + '/hold/ready-' + tok) else {'reached': False}
            reach_ok = tree.get('reached') is True and launch.pin.ok()
            rec['tree'] = {k: v for k, v in tree.items() if k in ('procs', 'expect', 'reached', 'chain_roles',
                                                                'wrapper_in_chain')}
            signal_ok = False
            if reach_ok and time.monotonic() < launch.deadline:
                if lid == 'L2':
                    open(w + '/hold/go-' + tok, 'w').close()
                    signal_ok = True                    # no signal: the hold is released
                else:
                    signal_ok = launch.pin.send(signal.SIGTERM if lid == 'L3' else signal.SIGKILL)
            ended = launch.wait_own(min(time.monotonic() + END_S, launch.deadline)) if signal_ok else False
            t = launch.tree or {}
            pinned = ([t['top']] if t.get('top') else []) + list(t.get('pins') or [])
            rec['alive_after_end'] = [not x.exited(0.2) for x in pinned]
            a = b.snap(p)                              # before any cleanup by this harness
            rec['snapshot_after_end'] = {k: a.get(k) for k in b.SNAP_FIELDS}
            launch.wait_streams(min(time.monotonic() + 1, launch.deadline))
            launch.collect()
    except Exception as e:                             # r1 major 5: collected by owned, then the run stops
        return harness_error(rec, e, launch)
    r = launch.result('hold', d['codex'], w)
    after = b.snap(p)
    steps = b.l_steps(lid, True, reach_ok, signal_ok, ended, launch.exit_code if lid == 'L2' else None,
                      b.identity_same(before, a), b.identity_same(before, after))
    verdict, why = b.judge_l(steps)
    clean = require_clean(r)
    rec.update(launch=launch_view(r), steps=steps, verdict=verdict, reason=why, clean=clean,
               scope_l={'L2': 'the CLI wrapper exits on its own after go (not an app-server end)',
                        'L3': 'SIGTERM to the CLI wrapper during a held command (not to an app-server)',
                        'L4': 'SIGKILL to the CLI wrapper during a held command (not to codex-linux-sandbox itself)'}[lid])
    if next_step(clean) != 'continue':
        return stop_contaminated(rec, lid.lower() + '_final', r)
    return rec, True, None


def k_case(kid, app_fn=None, check_fn=None, dirs_fn=None, ev_fn=None):
    """K1 (placeholder) / K2 (none): the runner's app-server config check (P2), not a CLI observation. The same steps
    and verdicts as the base k_case (start failure: inconclusive, stop app_server_start; else pass / fail from
    policy_check), with the app-server owned and ended here (app_session / strict_end_app, r2 major 2): the base
    k_case, stop_case and end_and_check are not called. dirs_fn / ev_fn / app_fn / check_fn: base case_dirs /
    placeholder / AppServer / policy_check; the tests pass local stand-ins."""
    b = base()
    d = (dirs_fn or b.case_dirs)(kid)
    ev = (ev_fn or b.placeholder)(d) if kid == 'K1' else None
    rec = {'id': kid, 'placeholder': ev, 'vehicle': 'app_server_config_read', 'scope': SCOPE,
           'policy_proxy_limit': PROXY_LIMIT, 'note': 'K1/K2 are the runner path config check, not a CLI observation'}
    s = app_session(d, check_fn, app_fn)
    e = s['end']
    rec.update(environment=s['environment'], checks=s['checks'], ended=e['ended'], forced=e['forced'],
               clean=e['clean'], end=e)
    if not s['started']:
        rec.update(verdict='inconclusive', reason='app_server_start')
        return rec, e['clean'], 'app_server_start'
    rec['verdict'] = 'pass' if s['ok'] else 'fail'
    return rec, e['clean'], None


def main():
    b = base()
    if not b.environment() or not b.pins():
        b.out('summary', result='gate_stopped', vehicle=VEHICLE, scope=SCOPE, policy_proxy_limit=PROXY_LIMIT)
        return 2
    os.makedirs(b.ROOT + '/g1', mode=0o700)
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
            rec, clean, stop = ({'id': cid, 'verdict': 'inconclusive', 'reason': 'harness_error:' + type(e).__name__,
                                 'cleanup': getattr(e, 'cleanup', None)}, False, 'harness_error')
        rec.setdefault('vehicle', VEHICLE)                  # minor 2: the same scope fields on every output path
        rec.setdefault('scope', SCOPE)
        rec.setdefault('policy_proxy_limit', PROXY_LIMIT)
        rec['seconds'] = round(time.monotonic() - started, 1)
        results[cid] = rec.get('verdict')
        b.out('case', **rec)
        tr = b.case_transition(cid, rec.get('verdict'), clean, stop)
        if tr is not None:
            result, reason, rc = tr
            b.out('summary', result=result, at=cid, reason=reason, results=results, vehicle=VEHICLE, scope=SCOPE,
                  policy_proxy_limit=PROXY_LIMIT)
            return rc
    b.out('summary', result=b.conclusion(results), results=results, vehicle=VEHICLE, scope=SCOPE,
          policy_proxy_limit=PROXY_LIMIT)
    return 0


if __name__ == '__main__' and os.environ.get('G1_IMPORT_ONLY') != '1':
    sys.exit(main())
