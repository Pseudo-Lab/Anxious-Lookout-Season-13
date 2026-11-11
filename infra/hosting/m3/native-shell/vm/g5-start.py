"""#68 G5 g5s (deployment design r3 §5; this file r2): the gen 11 model-free start of owner …5805's runner on the G5
image (code 07a04e85, the (a′) fix), after the G5-2 switch and the E3 rename, on the development VM only (S1). Root on
the guest:

    python3 -I -B /root/native68/g5-start.py check <X> <E>   # read-only: changes nothing, writes no file
    systemd-run … python3 -I -B /root/native68/g5-start.py g5s <X> <E>   # once: entry, gen 11 Pod, three reads,
                                                                         # session revoked, natural idle stop watched

<X> is the reviewed sha256 of /root/native68/g5-expected.json (the G5-2 file; only newImage / newPlanSha256 are read,
by g3-start.py's own load_expected). <E> (r2) is the sha256 of the one E3 evidence file
/root/native68/evidence/e3-rename-<UTC>.txt, as E3 printed it (`evidence <sha256> …`), so no revision of this file is
needed after E3. r1 (review deploy-code-r0): `check` = guard + the read-only pre_g5; each read's judged CODEX_HOME view
stored (`codexHomeReads`). r2 (review start-r1 minor): a missing / unreadable G3a or G4b record is rc 3, no traceback.

What it is. A wrapper over the pinned g3-start.py (f61f6e78…), read once, its sha256 checked, the hashed bytes
compiled (the load_pinned pattern; this file's own copy). g3-start.py's main() is never called: this file is the
entry point, accepts only `check` and `g5s` (g3a / g3b and anything else: usage, rc 2, before any file is read),
records its own sha256 (SCRIPT_SHA) and g3-start.py's in the evidence, and gives the base dispatcher
(v6pre-turns.py main: guard -> open_evidence -> ACTIONS[action]) exactly {'g5s': action_g5s}.
Replaced in the loaded g3-start module: EXPECTED_FILE, LEDGERS (the G4b post b2 values), P / V6 (the pinned bases,
loaded with g3-start's own pins), and two read hooks that its readiness() looks up at call time: read_once (adds the
CODEX_HOME view) and judge_read (g3-start's judge with mode 'g3b', then the CODEX_HOME judge). Used unchanged from
g3-start.py: readiness, g3_wait_idle, judge_host, judge_lines, host_state, public_host, look, ledgers_ok,
plan_digest_now, g3a_reference, g3_runs, run_record, record_reference, placeholder_lines, load_expected. Not used:
its pre, act, ACTIONS, guard, open_evidence, check, main.

Mode 'g3b' for every g3-start judge: a later start of the recorded placeholder (presence required at every read, the
G3a reference compared): lines matched/start, matched/start, matched/launch with the G3a ino; no created / replaced.

CODEX_HOME (P.CODEX_HOST), through the no-follow fd walk of codex-home-look r1 (open_home / component_reason,
byte-identical, tested), at the precheck, each of the three reads and after the idle stop: config.toml absent,
rules absent, exactly one config.toml.kept-<UTC> whose identity equals KEPT_REF (the file E3 renamed: rename keeps
dev / ino / type / uid / gid / mode / nlink / size / mtime_ns); any lookup failure is a problem. The count of all
other entries is printed as an observation only (Codex creates state files in CODEX_HOME at a start; deployment
design r2 §5 narrowed, reported to review). Names are compared in memory, never printed.
Not evidence of the (a′) fix (design §0 rule): this start does not call thread/start.

Guard (rc 3): no g5-start-g5s-* run; exactly one g3-g3a-* and one g3-g3b-* run, each completed_ok with its pass flag;
exactly one g4-turn-* (exit rc 14) and one g4b-turn-* (exit rc 4); exactly one g5-switch-* whose run.json result is 0
with no cleanup failure; exactly one E3 evidence file e3-rename-<UTC>.txt whose sha256 is the 4th argument (r2: E3's
printed `evidence <sha256> …`, reviewed with the run line) and whose JSON rc is 0.
Precheck (rc 3, nothing changed): the base precheck with idle_stopped, no Pod, gen 10, no owner Pod (binding image =
the G5 digest reference); …5805 plan digest = the G5-2 new one; the ledgers at their pins; the host = the G3a
reference (judge_host 'g3b'); g4-src exactly as G4 left it and the workspace names exactly G4b's known set; the
CODEX_HOME judge. Previous Pod = the G4b run's recorded pod (the gen 10 Pod).
Then: session -> entry -> wait_pod for gen 11 -> start_checks -> three reads (the hooks above) -> session revoked ->
g3_wait_idle(11) -> after the idle stop: judge_host 'g3b', the .codex ino, g4-src, the names, the CODEX_HOME judge,
the ledgers. A Stop from the entry, Pod, start or a read: the session is revoked and the Stop returned at once.
rc: the base codes (3 refused before any change; 4 entry/Pod/start/read failed, an idle-wait lookup error or identity
change, or the after-state differs; 7 session; 70 session not revoked; 12 final invariants; 9 unexpected; 130) plus 11
(the natural idle stop not observed). A nonzero rc is never rerun.
"""
import hashlib
import json
import os
import re
import stat
import sys
import time
import types

G3_PATH, G3_SHA = '/root/native68/g3-start.py', 'f61f6e787bd9f5affa040b378be3fb0d51ca17114c754c84834397f8dda0a67b'
EXPECTED_FILE = '/root/native68/g5-expected.json'
LEDGERS = {'isolation-42': 'a9cd9e6f84e8e8e5dbe91008e2eac879ac20011bef909e91efb43cd6ce429751', 'resources-6': '51e7ae651e8dcb79213082362fef2bd6873c11f2000b9567719974c61b6eafeb'}
GEN_G5, JUDGE_MODE, ACTION = 11, 'g3b', 'g5s'
# The file E3 renamed (e3-rename.py PIN_FILE = codex-home-look r1 41e88783…); a rename keeps these fields.
KEPT_REF = {'dev': 1802, 'ino': 131205, 'type': 'file', 'uid': 10001, 'gid': 10001, 'mode': 0o600, 'nlink': 1, 'size': 54, 'mtime_ns': 1794342704373636030}
KEPT_NAME = re.compile(r'config\.toml\.kept-\d{8}T\d{6}Z')
# r2 (PM: no r2.1 after E3): the E3 evidence file's sha256 is the 4th argument (E3's printed `evidence <sha256> …`,
# kept on the host in e3-evidence.sha256 and reviewed with the run line), not a constant in this file.
E3_SHA = None
E3_NAME = re.compile(r'e3-rename-\d{8}T\d{6}Z\.txt')
HEX64 = re.compile(r'[0-9a-f]{64}')
# As G4b left them (g4b-turn.py 9b90a66e…: G4SRC, KNOWN_NAMES, G4SRC_REF; tested equal).
G4SRC = 'g4-src'
KNOWN_NAMES = ('.codex', '.git', '.agents', '.aws', 'codex-dir-kept-20261110T090017Z',
               'codex-dir-kept-20261110T170732Z', 'v6pre-write.txt', G4SRC)
G4SRC_REF = {'type': 'dir', 'uid': 10001, 'entries': 0, 'ino': 131208, 'mtime_ns': 1794342715963798350}

G3 = P = V6 = None
SCRIPT_SHA = None
G3_READ_ONCE = G3_JUDGE_READ = None
CODEX_READS, EV_G5 = [], []  # the per-read CODEX_HOME judgments of this run, and its evidence directory


def load_pinned(path, sha, name):
    data = open(path, 'rb').read()
    if hashlib.sha256(data).hexdigest() != sha:
        raise SystemExit('%s is not %s; nothing done' % (path, sha[:8]))
    module = types.ModuleType(name)
    module.__file__ = path
    exec(compile(data, path, 'exec'), module.__dict__)
    return module


# CODEX_HOME (codex-home-look r1's walk, byte-identical) --------------------------------------------------------------
def component_reason(fd, part):
    """Why a path component could not be opened as a no-follow directory: its own no-follow lstat, relative to the
    parent fd (open with O_NOFOLLOW | O_DIRECTORY gives ENOTDIR for a symlink too, so errno alone cannot tell)."""
    try:
        mode = os.lstat(part, dir_fd=fd).st_mode
    except FileNotFoundError:
        return 'path_absent'
    except OSError:
        return 'path_error'
    return 'path_symlink' if stat.S_ISLNK(mode) else 'path_not_dir' if not stat.S_ISDIR(mode) else 'path_error'


def open_home(path):
    """(fd, reason). r1 (review major): walk the fixed absolute path one component at a time from `/`, each opened
    with O_DIRECTORY | O_NOFOLLOW relative to its parent's fd, so a symlink or a non-directory
    anywhere on the path, CODEX_HOME itself included, refuses the observation. The directory fds are never read for
    content; every later lookup is relative to the CODEX_HOME fd, so a path swap after the walk is not followed."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    parts = [p for p in path.split('/') if p]
    if not path.startswith('/') or any(p in ('.', '..') for p in parts):
        return None, 'path_error'
    fd = os.open('/', flags)
    for part in parts:
        try:
            nfd = os.open(part, flags, dir_fd=fd)
        except OSError:
            reason = component_reason(fd, part)
            os.close(fd)
            return None, reason
        os.close(fd)
        fd = nfd
    return fd, None


def identity(info):
    kind = 'file' if stat.S_ISREG(info.st_mode) else 'dir' if stat.S_ISDIR(info.st_mode) else \
        'symlink' if stat.S_ISLNK(info.st_mode) else 'other'
    return {'dev': info.st_dev, 'ino': info.st_ino, 'type': kind, 'uid': info.st_uid, 'gid': info.st_gid,
            'mode': stat.S_IMODE(info.st_mode), 'nlink': info.st_nlink, 'size': info.st_size,
            'mtime_ns': info.st_mtime_ns}


def observe_g5():
    """The CODEX_HOME view: booleans, counts and the kept files' identities (names stay in memory)."""
    try:
        home, reason = open_home(P.CODEX_HOST)
    except OSError:
        return {'ok': False, 'reason': 'path_error'}
    if home is None:
        return {'ok': False, 'reason': reason}
    try:
        names = os.listdir(home)
        view = {'ok': True, 'config': 'config.toml' in names, 'rules': 'rules' in names, 'kept': [],
                'others': len([n for n in names if n not in ('config.toml', 'rules') and not KEPT_NAME.fullmatch(n)])}
        for name in names:
            if KEPT_NAME.fullmatch(name):
                view['kept'].append(identity(os.lstat(name, dir_fd=home)))
        return view
    except OSError:
        return {'ok': False, 'reason': 'lookup_error'}
    finally:
        os.close(home)


def g5_problem(view):
    """None, or the CODEX_HOME problem (fixed text)."""
    if not isinstance(view, dict) or view.get('ok') is not True:
        return 'CODEX_HOME lookup failed (%s)' % (view or {}).get('reason')
    if view['config']:
        return 'CODEX_HOME/config.toml exists'
    if view['rules']:
        return 'CODEX_HOME/rules exists'
    if len(view['kept']) != 1:
        return 'CODEX_HOME has %d config.toml.kept-* files, not 1' % len(view['kept'])
    moved = sorted(k for k, v in KEPT_REF.items() if view['kept'][0].get(k) != v)
    if moved:
        return 'the kept file is not the one E3 renamed (%s)' % ','.join(moved)
    return None


def public_g5(view):
    if not isinstance(view, dict) or view.get('ok') is not True:
        return {'ok': False, 'reason': (view or {}).get('reason')}
    return {'ok': True, 'config': view['config'], 'rules': view['rules'], 'kept': len(view['kept']),
            'keptAsE3': len(view['kept']) == 1 and all(view['kept'][0].get(k) == v for k, v in KEPT_REF.items()),
            'others': view['others']}


# The two read hooks (installed into the g3-start module) ---------------------------------------------------------
def read_once_g5():
    read = G3_READ_ONCE()
    read['g5'] = observe_g5()
    return read


def judge_read_g5(read, first, index, mode, ino_expected=None, before=None):
    """g3-start's judge, then the CODEX_HOME judge on the view read_once_g5 took in this same read. r1 (review
    deploy-code-r0 major 2): that view and its verdict are stored as they were judged (read number, UTC, public
    view, problem), for every read including a failing one, and recorded at once; never replaced by a later look."""
    state, ino, problem = G3_JUDGE_READ(read, first, index, mode, ino_expected, before)
    view = read.get('g5')
    codex_problem = g5_problem(view)
    CODEX_READS.append({'read': index + 1, 'utc': P.now_utc(), 'view': public_g5(view), 'problem': codex_problem})
    if EV_G5:
        P.record(EV_G5[0], codexHomeReads=list(CODEX_READS))
    if problem:
        return state, ino, problem
    if codex_problem:
        return None, None, codex_problem
    return state, ino, None


# Workspace extras (G4b's rules, copied; tested equal) ------------------------------------------------------------
def names_problem_b(names):
    if names is None:
        return 'workspace listing failed'
    if sorted(names) != sorted(KNOWN_NAMES):
        return 'the workspace names are not exactly the known set (%d names)' % len(names)
    return None


def g4src_problem(g4src, host):
    """None when g4-src is exactly the object G4 left: dir, uid 10001, empty, the same ino / mtime_ns, on .codex's
    device."""
    if not isinstance(g4src, dict):
        return 'g4-src absent or unreadable'
    if any(g4src.get(k) != v for k, v in G4SRC_REF.items()):
        return 'g4-src changed (%s)' % ','.join(k for k, v in G4SRC_REF.items() if g4src.get(k) != v)
    codex = host.get('.codex') if isinstance(host, dict) else None
    if not isinstance(codex, dict) or g4src.get('dev') != codex.get('dev'):
        return 'g4-src is not on the workspace device'
    return None


def workspace_problem(host):
    try:
        names = sorted(os.listdir(P.WORKSPACE_HOST))
    except OSError:
        names = None
    return names_problem_b(names) or g4src_problem(G3.look(os.path.join(P.WORKSPACE_HOST, G4SRC)), host)


# Guard, evidence, reference ----------------------------------------------------------------------------------------
def runs(pattern):
    return sorted(d for d in os.listdir(P.EVIDENCE) if re.fullmatch(pattern, d))


def e3_ok():
    """Exactly one e3-rename-<UTC>.txt in the evidence directory, its sha256 = the E3_SHA argument, its JSON rc 0."""
    found = runs(E3_NAME.pattern)
    if E3_SHA is None or len(found) != 1:
        return False
    try:
        data = open(os.path.join(P.EVIDENCE, found[0]), 'rb').read()
        doc = json.loads(data[data.index(b'{'):data.rindex(b'}') + 1])
    except Exception:
        return False
    return hashlib.sha256(data).hexdigest() == E3_SHA and doc.get('rc') == 0


def guard_g5(action):
    if action != ACTION:
        raise P.Stop(3, 'only g5s exists here')
    if runs(r'g5-start-g5s-\d{8}T\d{6}Z'):
        raise P.Stop(3, 'g5s already ran; a second run is a PM decision')
    for mode in ('g3a', 'g3b'):
        done = G3.g3_runs(mode)
        if len(done) != 1 or not P.completed_ok(done[0]) or G3.run_record(done[0]).get(mode) != 'pass':
            raise P.Stop(3, 'g5s needs exactly one %s that passed' % mode)
    for pattern, rc in ((r'g4-turn-\d{8}T\d{6}Z', 14), (r'g4b-turn-\d{8}T\d{6}Z', 4)):
        done = runs(pattern)
        if len(done) != 1 or P.exit_rc(done[0]) != rc:
            raise P.Stop(3, 'g5s needs exactly one %s run that ended rc %d' % (pattern.split('-')[0], rc))
    switched = runs(r'g5-switch-\d{8}T\d{6}Z')
    rec = G3.run_record(switched[0]) if len(switched) == 1 else {}
    if len(switched) != 1 or rec.get('result') != 0 or rec.get('cleanupFailed') != []:
        raise P.Stop(3, 'g5s needs exactly one g5-switch run with result 0 and no cleanup failure')
    if not e3_ok():
        raise P.Stop(3, 'g5s needs exactly one E3 evidence file with the given sha256 and rc 0')


def open_evidence_g5(action):
    ev = os.path.join(P.EVIDENCE, 'g5-start-%s-%s' % (action, time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())))
    os.mkdir(ev, 0o700)
    fd = os.open(os.path.join(ev, 'out.txt'), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    P.OUT['stream'] = os.fdopen(fd, 'w')
    P.record(ev, action=action, started=P.now_utc(), script=SCRIPT_SHA, g3=G3_SHA, base=G3.BASE_SHA, v6=G3.V6_SHA,
             expected=G3.EXPECTED['sha'], image=P.RUNNER_REF)
    P.say('evidence: %s (script %s, g3 %s, base %s, v6 %s, expected %s)' % (
        ev, SCRIPT_SHA[:8], G3_SHA[:8], G3.BASE_SHA[:8], G3.V6_SHA[:8], G3.EXPECTED['sha'][:8]))
    return ev


def reference_g5():
    """(ino, before, previous Pod): the G3a reference and the G4b run's recorded Pod (the gen 10 Pod). r2 (review
    start-r1 minor): a missing or unreadable G3a / G4b record is Stop(3), never an exception."""
    g3a, g4b = G3.g3_runs('g3a'), runs(r'g4b-turn-\d{8}T\d{6}Z')
    try:
        ino, before = G3.g3a_reference(G3.run_record(g3a[0])) if len(g3a) == 1 else (None, None)
        pod = G3.run_record(g4b[0]).get('pod') if len(g4b) == 1 else None
    except (OSError, ValueError, AttributeError):
        ino, pod = None, None
    if ino is None or not pod:
        raise P.Stop(3, 'the G3a / G4b reference is incomplete; nothing changed')
    return ino, before, pod


def pre_g5():
    ok, state, broker = P.precheck(lambda s, pods: s['phase'] == 'idle_stopped' and s['pod'] is None
                                   and s['gen'] == GEN_G5 - 1 and not pods)
    if not ok:
        raise P.Stop(3, 'precheck refused; nothing changed')
    if G3.plan_digest_now(3) != G3.EXPECTED['plan']:
        raise P.Stop(3, '…5805 plan digest is not the G5-2 new one; nothing changed')
    if not G3.ledgers_ok():
        raise P.Stop(3, 'a ledger is not at its pin; nothing changed')
    ino, before, previous = reference_g5()
    host = G3.host_state()
    P.say('host state before the entry: %s' % json.dumps(G3.public_host(host), sort_keys=True))
    view = observe_g5()
    P.say('CODEX_HOME before the entry: %s' % json.dumps(public_g5(view), sort_keys=True))
    problem = (G3.judge_host(host, JUDGE_MODE, ino, True, before) or workspace_problem(host) or g5_problem(view))
    if problem:
        raise P.Stop(3, 'the host is not the reference (%s); nothing changed' % problem)
    return ino, before, previous


def action_g5s(ev, verdicts):
    CODEX_READS.clear()
    EV_G5[:] = [ev]
    ino_before, before, previous = pre_g5()
    P.record(ev, hostBefore=G3.host_state(), codexHomeBefore=public_g5(observe_g5()))
    P.create_session(ev)
    try:
        pod = P.enter_and_wait(ev, GEN_G5, previous)
        ident = V6.identity(pod)
        P.record(ev, pod=pod['metadata']['uid'])
        reads, ino, _, _ = G3.readiness(ev, pod, JUDGE_MODE, ino_before, before)
    except P.Stop as stop:
        verdicts['g5s start'] = 'not passed: %s (observed state only)' % stop.message
        P.revoke_session(ev)
        raise
    P.record(ev, ino=ino)  # codexHomeReads: recorded by judge_read_g5 as each read was judged
    verdicts['g5s start'] = ('explicit available signal at the third read (+%.1f s); placeholder lines %s with the G3a '
                             'ino; CODEX_HOME: no config.toml, no rules, the E3 kept file; no refusal, no policy '
                             'check line; same Pod/container, 0 restarts; no message POST; not evidence of the fix '
                             '(no thread/start)' % (reads[-1]['at'], ' -> '.join(
                                 '/'.join(p) for p in G3.SEQUENCES[JUDGE_MODE])))
    P.revoke_session(ev)
    idle = G3.g3_wait_idle(ev, GEN_G5, ident)
    verdicts['g5s idle stop'] = ('natural idle stop observed: one completed stop row of generation %d (no DELETE '
                                 'sent)' % GEN_G5 if idle['ok'] else 'not observed (%s)' % idle['why'])
    if not idle['ok']:
        timeout = idle['why'].startswith('not idle_stopped within') or idle['why'].startswith('not exactly one')
        raise P.Stop(11 if timeout else 4, 'idle wait: %s' % idle['why'])
    after, view = G3.host_state(), observe_g5()
    P.record(ev, after=after, codexHomeAfter=public_g5(view))
    P.say('host state after the idle stop: %s' % json.dumps(G3.public_host(after), sort_keys=True))
    P.say('CODEX_HOME after the idle stop: %s' % json.dumps(public_g5(view), sort_keys=True))
    problem = G3.judge_host(after, JUDGE_MODE, ino, True, before)
    if problem is None and after[G3.NAME]['ino'] != ino:
        problem = '.codex ino differs from the logged ino'
    problem = problem or workspace_problem(after) or g5_problem(view)
    if problem:
        verdicts['g5s after the idle stop'] = problem
        raise P.Stop(4, 'after the idle stop: %s' % problem)
    if not G3.ledgers_ok():
        raise P.Stop(4, 'a ledger changed (g5s runs no turn)')
    P.record(ev, g5s='pass', codex={k: after[G3.NAME][k] for k in G3.CODEX_FIELDS + ('mode', 'uid')},
             record=G3.record_reference(after[G3.RECORD]))


def check_g5():
    """Read-only. r1 (review deploy-code-r0 major 1): the same decisions as the action, by the same code: guard_g5 and
    pre_g5 (the base precheck with idle_stopped / no Pod / gen 10 / no owner Pod, the plan digest, the ledgers, the G3a
    reference, the workspace, CODEX_HOME). pre_g5 creates no session, no entry and no evidence. rc 0 only when both
    would pass, else 3. Writes nothing."""
    verdicts = {}
    for name, step in (('guard', lambda: guard_g5(ACTION)), ('precheck', pre_g5)):
        try:
            step()
            verdicts[name] = 'would pass'
        except P.Stop as stop:
            verdicts[name] = stop.message
        P.say('%s: %s' % (name, verdicts[name]))
    return 0 if all(v == 'would pass' for v in verdicts.values()) else 3


def wire(image, plan, sha):
    global P, V6, G3_READ_ONCE, G3_JUDGE_READ
    G3.EXPECTED_FILE, G3.LEDGERS = EXPECTED_FILE, dict(LEDGERS)
    G3.EXPECTED.update(sha=sha, image=image, plan=plan)
    P = G3.load_pinned(G3.BASE_PATH, G3.BASE_SHA, 'v6pre_turns')
    V6 = G3.load_pinned(G3.V6_PATH, G3.V6_SHA, 'v6_turns')
    V6.P, G3.P, G3.V6 = P, P, V6
    P.RUNNER_REF = image
    G3_READ_ONCE, G3_JUDGE_READ = G3.read_once, G3.judge_read
    G3.read_once, G3.judge_read = read_once_g5, judge_read_g5
    P.guard, P.open_evidence, P.ACTIONS = guard_g5, open_evidence_g5, {ACTION: action_g5s}


def main(argv):
    global G3, SCRIPT_SHA, E3_SHA
    if len(argv) != 4 or argv[1] not in ('check', ACTION) or not HEX64.fullmatch(argv[3]):
        print('usage: g5-start.py check|g5s <sha256 of g5-expected.json> <sha256 of the E3 evidence file>')
        return 2
    E3_SHA = argv[3]
    if os.geteuid() != 0:
        print('run as root')
        return 2
    SCRIPT_SHA = hashlib.sha256(open(os.path.abspath(__file__), 'rb').read()).hexdigest()
    try:  # a pin or expected-file refusal before the dispatcher: rc 3, nothing done (design r2 §5)
        G3 = load_pinned(G3_PATH, G3_SHA, 'g3_start')
        G3.EXPECTED_FILE = EXPECTED_FILE
        image, plan = G3.load_expected(argv[2])
        wire(image, plan, argv[2])
    except SystemExit as refused:
        print(refused)
        return 3
    if argv[1] == 'check':
        try:
            return check_g5()
        except P.Stop as stop:
            P.say('check stopped: %s' % stop.message)
            return stop.code
    return P.main(argv[:2])


if __name__ == '__main__':
    sys.exit(main(sys.argv))
