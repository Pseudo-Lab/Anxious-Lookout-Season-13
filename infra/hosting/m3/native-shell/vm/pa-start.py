"""#68 P-A start (P-A design r3 §3.1 / §4; D-P2 r1): the gen 14 model-free start of owner …5805's runner on the P-A
image, with the exec-observer validation (V4's precondition, D-V2), on the development VM only (S1). Root on the guest:

    python3 -I -B /root/native68/pa-start.py check <X> <G5T> <G5R>   # read-only: changes nothing, writes no file
    systemd-run … python3 -I -B /root/native68/pa-start.py pas2 <X> <G5T> <G5R>   # once, after review and PM's go

<X> is the reviewed sha256 of /root/native68/pa-expected.json; <G5T> / <G5R> are the sha256 of the accepted g5t and
g5r run.json (one reviewed read-only look, run by PM). Anything else is rc 2 before any file is read.

What it is: a wrapper over the pinned g5-start.py r2 (2856cd29…, its main never called) and the pinned pa-observe.py.
Initialisation order (P-A r3 §3.1): the PA expected path is set on both modules, then g3-start's load_expected reads
it, then g5-start's wire runs with the PA image / plan; the names wire copies into the base dispatcher (ACTION, the
guard, the evidence opener, the action) are set before wire, as g5-restart.py does; g3-start's ledgers_ok is replaced
after wire (wire re-installs only LEDGERS / EXPECTED_FILE); a self-check then asserts that the dispatcher holds this
file's guard, opener and action and that ledgers_ok is ledgers_pa.

The action is g5-start's action_g5s unchanged, with three hooks:
- g3-start.readiness (after the Pod is Running and ready): the observer precheck is done in the precheck; here the
  observer starts (READY, clock check, R1 snapshot), K4 runs, then the inherited three reads, then CODEX_HOME / the
  .codex reference and /tmp are observed, K3 and K2 run once each (pinned bytes, timeouts), and the same are observed
  again: a CODEX_HOME / .codex change is rc 5 (counterexample);
- g3-start.g3_wait_idle: after the natural idle stop the observer is stopped and validated;
- the action itself in try/finally: the observer is always stopped (its own child only).
The start's own rc and priority are g5-start's. When the start passed but the observer is not validated: rc 16
"observer not validated" (no V6 go; D-P2 r1: design (b) next, no skipping, no retry). A runner process in another
time namespace than the host, or a failed lookup of it, makes the observer not validated (rc 16): the runner's
monotonic clock would not be the observer's (P-A r3 §0, review round 1).

/tmp verdict of a pass: pa-observe.TMP_VERDICT, recorded with pa-observe.TMP_UNVERIFIED. M1 was a partial
pre-measurement, not a pass; the VM K2 is the first measurement of namespaces, launcher chain and success-path files.

r5 (design (b) r1 1c921ee0…, after the pas run ended rc 16): the unit is `pas2`, generation 15, evidence
pa-start-pas2-<UTC>; the failed run pa-start-pas-20261111T123436Z stays as it is and is required exactly as preserved
(FAILED_PINS: its six files, exit rc 16; guard rc 3 otherwise); the previous Pod is the failed run's (from its pinned
run.json); every other gate is carried unchanged (the expected image / plan, the ledgers, CODEX_HOME / .codex, the
other owners, the session, the natural idle stop, the final invariants). K3 / K2 run through the observer's bounded
capture (kIo: outcome, rc, timing, stdout / stderr with truncation flags); the K4 record is persisted (k4); the
observer validates the anchored exec sessions (pa-observe r5) and its anchor detail is recorded (observer.anchor).

r4 (the r3 check stopped at the observer precheck with no message, PM diagnosis pa-diag baa6784e…): the precheck
keeps every bpftrace call's rc and stderr; a refusal prints them to the log (rc 3, nothing done, no evidence directory
yet), a pass is recorded in run.json (observerPrecheck). The rc 3 meaning is unchanged.
"""
import hashlib
import json
import os
import sys
import time
import types

G5S_PATH, G5S_SHA = '/root/native68/g5-start.py', '2856cd2962e0a95dbdc27440946ce9e6b671fab515dfddab8bc73608581d3920'
PAO_PATH, PAO_SHA = '/root/native68/pa-observe.py', '16024ba51a197ff121f322ec79575ae18582177817476a58a1179fbd4529cf3f'
EXPECTED_PATH = '/root/native68/pa-expected.json'
GEN, ACTION = 15, 'pas2'  # r5 (design (b) r1 §6): the new unit; the failed pas run stays as it is
RES6_PIN = '51e7ae651e8dcb79213082362fef2bd6873c11f2000b9567719974c61b6eafeb'
G5T_RUN, G5R_RUN = r'g5-turn-\d{8}T\d{6}Z', r'g5-restart-g5r-\d{8}T\d{6}Z'
SWITCH_RUN, PAS_RUN = r'pa-switch-\d{8}T\d{6}Z', r'pa-start-pas-\d{8}T\d{6}Z'
PAS2_RUN = r'pa-start-pas2-\d{8}T\d{6}Z'
# The failed pas run (rc 16), preserved: its directory and all six files at these pins (look cde7d864…).
FAILED_RUN = 'pa-start-pas-20261111T123436Z'
FAILED_PINS = {
    'exit.json': '72dfc1a79b30556923f7f2dc9f636a85c7299cbf021130e8aecb070260399910',
    'k4-pids.json': '3507403a21467ed1c36f4580519d099dac7167b303b4278a05c0771a26293687',
    'observer.err': 'a2ec3d4074abc856eb968b8430ebecb32889ec207e53c66e48ba8337dfe43aff',
    'observer.out': 'd139723f605053bd5ad9ed253d052703322fad2a79df8f72ae8870025437f455',
    'out.txt': '6d735563928f00e52512ca163265b0f0330100a8c27e09b28af7fadbe74d33a9',
    'run.json': 'c7753896b09586a0ad47ef8960cb0ac920c0fcba272beec8d5353f593f80944e',
}
EMPTYDIR = '/var/lib/kubelet/pods/%s/volumes/kubernetes.io~empty-dir/tmp'  # the Pod's /tmp on the node

G5S = OBS = None
ARGS = {}
STATE = {'observer': None, 'validated': None, 'reasons': [], 'facts': {}}


def load_pinned(path, sha, name):
    data = open(path, 'rb').read()
    if sha is None or hashlib.sha256(data).hexdigest() != sha:
        raise SystemExit('%s is not the pinned file; nothing done' % path)
    module = types.ModuleType(name)
    module.__file__ = path
    exec(compile(data, path, 'exec'), module.__dict__)
    return module


def canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def pinned_record(pattern, want):
    """(name, run.json dict, exit rc) of the one run matching pattern whose run.json has the argument's sha256."""
    found = G5S.runs(pattern)
    if len(found) != 1:
        return None, None, None
    path = os.path.join(G5S.P.EVIDENCE, found[0], 'run.json')
    try:
        data = open(path, 'rb').read()
    except OSError:
        return found[0], None, None
    if hashlib.sha256(data).hexdigest() != want:
        return found[0], None, None
    try:
        return found[0], json.loads(data), G5S.P.exit_rc(found[0])
    except ValueError:
        return found[0], None, None


def ledgers_pa():
    """The post-G5 verifier (P-A r3 §3.1), used by the inherited pre_g5 and after the idle stop. Never re-pinned from
    the current files: g5t / g5r at their argument hashes, exit rc 0, g5t's ledger rule held; isolation-42 = g5t's
    ledgersAfter = its ledgersBefore plus only g5t's own v6 / G5 / settled claim; resources-6 at its raw pin and
    = g5t's ledgersBefore."""
    P = G5S.P
    tname, trec, trc = pinned_record(G5T_RUN, ARGS.get('g5t'))
    rname, rrec, rrc = pinned_record(G5R_RUN, ARGS.get('g5r'))
    if not (isinstance(trec, dict) and isinstance(rrec, dict) and trc == 0 and rrc == 0):
        return False
    if trec.get('ledgerCheck') != "only this run's claim":
        return False
    try:
        raw = {b: open(P.LEDGERS[b][0], 'rb').read() for b in ('isolation-42', 'resources-6')}
        iso, res = json.loads(raw['isolation-42']), json.loads(raw['resources-6'])
    except (OSError, ValueError, KeyError):
        return False
    before, after, claim_id = trec.get('ledgersBefore') or {}, trec.get('ledgersAfter') or {}, trec.get('g5Claim')
    if hashlib.sha256(raw['resources-6']).hexdigest() != RES6_PIN or not isinstance(iso, dict):
        return False
    if canonical(res) != (before.get('resources-6') or {}).get('canonical'):
        return False
    if canonical(iso) != (after.get('isolation-42') or {}).get('canonical'):
        return False
    mine = [c for c in iso.get('claims', []) if isinstance(c, dict) and c.get('id') == claim_id]
    if not claim_id or len(mine) != 1 or (mine[0].get('allocation'), mine[0].get('item'), mine[0].get('status'),
                                         mine[0].get('run')) != ('v6', 'G5', 'settled', tname):
        return False
    rest = dict(iso, claims=[c for c in iso['claims'] if not (isinstance(c, dict) and c.get('id') == claim_id)])
    if canonical(rest) != (before.get('isolation-42') or {}).get('canonical'):
        return False
    STATE['facts']['refs'] = {'g5t': {'name': tname, 'sha256': ARGS['g5t'],
                                      'digest': (after.get('isolation-42') or {}).get('canonical')},
                              'g5r': {'name': rname, 'sha256': ARGS['g5r']}}
    return True


def guard_pa(action):
    P, G3 = G5S.P, G5S.G3
    if action != ACTION:
        raise P.Stop(3, 'only pas2 exists here')
    if G5S.runs(PAS2_RUN):
        raise P.Stop(3, 'pas2 already ran; a second run is a PM decision (no pas3 without a reviewed decision)')
    if failed_problems():
        raise P.Stop(3, 'pas2 needs the failed pas run exactly as preserved: %s' % '; '.join(failed_problems()))
    switched = G5S.runs(SWITCH_RUN)
    rec = G3.run_record(switched[0]) if len(switched) == 1 else {}
    if len(switched) != 1 or rec.get('result') != 0 or rec.get('cleanupFailed') != []:
        raise P.Stop(3, 'pas2 needs exactly one pa-switch run with result 0 and no cleanup failure')
    for pattern, key in ((G5T_RUN, 'g5t'), (G5R_RUN, 'g5r')):
        name, record, rc = pinned_record(pattern, ARGS.get(key))
        if record is None or rc != 0:
            raise P.Stop(3, 'pas2 needs the accepted %s run.json at its argument hash with exit rc 0' % key)


def failed_problems():
    """r5 §6: exactly one pa-start-pas-* directory, the failed run, its six files at FAILED_PINS and no other file;
    exit rc 16. Returns the problems (empty when preserved)."""
    P = G5S.P
    found = G5S.runs(PAS_RUN)
    if found != [FAILED_RUN]:
        return ['the pas runs are %s, not exactly %s' % (found, FAILED_RUN)]
    problems, folder = [], os.path.join(P.EVIDENCE, FAILED_RUN)
    try:
        names = sorted(os.listdir(folder))
    except OSError as error:
        return ['the failed run is unreadable (errno %d)' % error.errno]
    if names != sorted(FAILED_PINS):
        problems.append('the failed run holds %s' % names)
    for name in sorted(FAILED_PINS):
        try:
            data = open(os.path.join(folder, name), 'rb').read()
        except OSError:
            problems.append('%s unreadable' % name)
            continue
        if hashlib.sha256(data).hexdigest() != FAILED_PINS[name]:
            problems.append('%s is not at its pin' % name)
    if not problems and P.exit_rc(FAILED_RUN) != 16:
        problems.append('the failed run exit rc is not 16')
    return problems


def failed_pod():
    """The failed pas run's recorded Pod (its gen 14 Pod), read from its pinned run.json."""
    data = open(os.path.join(G5S.P.EVIDENCE, FAILED_RUN, 'run.json'), 'rb').read()
    if hashlib.sha256(data).hexdigest() != FAILED_PINS['run.json']:
        return None
    return json.loads(data).get('pod')


def reference_pa():
    """(ino, before, previous Pod): the G3a reference and, r5, the failed pas run's recorded Pod (gen 14), read from its
    pinned run.json (r4: the g5r run's gen 13 Pod)."""
    G3, P = G5S.G3, G5S.P
    g3a = G3.g3_runs('g3a')
    try:
        ino, before = G3.g3a_reference(G3.run_record(g3a[0])) if len(g3a) == 1 else (None, None)
    except (OSError, ValueError, AttributeError):
        ino, before = None, None
    try:
        pod = failed_pod()
    except (OSError, ValueError):
        pod = None
    if ino is None or not pod:
        raise P.Stop(3, 'the G3a / failed-run Pod reference is incomplete; nothing changed')
    return ino, before, pod


def open_evidence_pa(action):
    P, G3 = G5S.P, G5S.G3
    ev = os.path.join(P.EVIDENCE, 'pa-start-%s-%s' % (action, time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())))
    os.mkdir(ev, 0o700)
    fd = os.open(os.path.join(ev, 'out.txt'), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    P.OUT['stream'] = os.fdopen(fd, 'w')
    P.record(ev, action=action, started=P.now_utc(), script=G5S.SCRIPT_SHA, g5s=G5S_SHA, observe=PAO_SHA,
             g3=G5S.G3_SHA, base=G3.BASE_SHA, v6=G3.V6_SHA, expected=G3.EXPECTED['sha'], image=P.RUNNER_REF,
             args={'g5t': ARGS['g5t'], 'g5r': ARGS['g5r']}, k2Command=OBS.K2_COMMAND,
             k2Sha256=hashlib.sha256(OBS.K2_SCRIPT.encode()).hexdigest(),
             observerPrecheck=STATE['facts'].get('observerPrecheck'), failedRun={'name': FAILED_RUN, 'pins': FAILED_PINS})
    P.say('evidence: %s (script %s, g5s %s, observe %s)' % (ev, G5S.SCRIPT_SHA[:8], G5S_SHA[:8], PAO_SHA[:8]))
    return ev


def protected_view():
    """CODEX_HOME (g5-start's judge view) and the .codex reference fields (g3-start host state)."""
    G3 = G5S.G3
    host = G3.host_state()
    return {'codexHome': G5S.public_g5(G5S.observe_g5()),
            'codex': {k: (host.get(G3.NAME) or {}).get(k) for k in G3.CODEX_FIELDS}}


def readiness_pa(ev, pod, mode, ino_expected=None, before=None):
    """The inherited three reads, bracketed by the observer (P-A r3 §4.4 / §4.5, D-P2 r1)."""
    P = G5S.P
    ident = G5S.V6.identity(pod)
    try:
        cg_path, cg_id = OBS.resolve_cgroup(ident.get('container') or '')
        obs = STATE['observer'] = OBS.Observer(ev, cg_id)
        obs.start()
        r1 = OBS.snapshot_r1(cg_path)
        pidns = OBS.container_pidns(r1)  # r5 §2c: one pid ns for all R1 processes, identity-checked, else not validated
        time_ns = OBS.time_namespace(r1)  # review round 1: the runner's clock is the observer's (rc 16 if not)
        container_ns = (r1[0][2], r1[0][3])
        STATE['facts'].update(cgroup=cg_path, cgroupId=cg_id, r1=r1, containerNs=container_ns, timeNs=time_ns,
                              pidns=pidns)
        P.record(ev, observerStart={'cgroupId': cg_id, 'clock': obs.clock_window, 'r1': r1, 'timeNs': time_ns,
                                    'pidns': pidns})
        STATE['facts']['k4'] = obs.run_k4()
        P.record(ev, k4=STATE['facts']['k4'])  # r5 §5: the driver identity and the statuses persisted too
    except OBS.ObserverError as error:
        STATE['validated'], STATE['reasons'] = False, [error.message]
        P.say('observer: %s' % error.message)
        result = G5S.G3_READINESS(ev, pod, mode, ino_expected, before)
        return result
    result = G5S.G3_READINESS(ev, pod, mode, ino_expected, before)
    name = pod['metadata']['name']
    tmp_root = EMPTYDIR % ident['pod']
    view_before, tmp_before = protected_view(), OBS.tmp_walk(tmp_root)
    # r5 §3: each K through the bounded capture (outcome, rc, timing, stdout / stderr), never the base run()
    capture = lambda argv, timeout: OBS.capture_k([P.K3S, 'kubectl', '-n', P.RUNNER_NS, 'exec', name, '-c', 'runner',
                                                   '--'] + argv, timeout)
    obs = STATE['observer']
    obs.run_window('K3', OBS.K3_COMMAND, OBS.T_K3, capture)
    k2 = obs.run_window('K2', OBS.K2_COMMAND, OBS.T_K2, capture)
    P.record(ev, kIo=obs.kio)
    match = OBS.K2_T.search((k2.get('stdout') or {}).get('text') or '')
    t_name = match.group(1) if match else None
    view_after, tmp_after = protected_view(), OBS.tmp_walk(tmp_root)
    r1_after = []
    try:
        r1_after = OBS.snapshot_r1(STATE['facts']['cgroup'])
    except (OBS.ObserverError, OSError, ValueError):
        pass
    tmp_problems, note = OBS.tmp_judge(tmp_before, tmp_after, t_name)
    STATE['facts'].update(t=t_name, tmpProblems=tmp_problems, daemon=note, r1After=r1_after)
    P.record(ev, k=obs.windows, kT=t_name, tmpBefore=tmp_before, tmpAfter=tmp_after, tmpProblems=tmp_problems,
             daemonPath=note, protectedBefore=view_before, protectedAfter=view_after)
    if view_after != view_before:
        raise P.Stop(5, 'COUNTEREXAMPLE: CODEX_HOME or the .codex reference changed around K3 / K2')
    return result


def wait_idle_pa(ev, generation, ident, *args, **kwargs):
    idle = G5S.G3_WAIT_IDLE(ev, generation, ident, *args, **kwargs)
    finish_observer(ev)
    return idle


def finish_observer(ev):
    """Stop our own observer (idempotent) and validate once."""
    P, obs = G5S.P, STATE['observer']
    if obs is None or STATE.get('finished'):
        return
    STATE['finished'] = True
    try:
        obs.stop()
    except OBS.ObserverError as error:
        STATE['reasons'].append(error.message)
    summary = obs.summary()
    facts = STATE['facts']
    anchor = {}
    if STATE['validated'] is False:
        validated, reasons = False, list(STATE['reasons'])
    else:
        validated, reasons = OBS.validate(summary, obs.lines, facts.get('r1', []), obs.driver_id, facts.get('k4'),
                                          obs.windows, facts.get('containerNs'), facts.get('tmpProblems', []),
                                          facts.get('r1After', []), kio=obs.kio, cg=facts.get('cgroupId'),
                                          detail=anchor)
        reasons = STATE['reasons'] + reasons
        validated = validated and not STATE['reasons']
    STATE['validated'], STATE['reasons'] = validated, reasons
    verdict = {'state': 'validated' if validated else 'not validated', 'reasons': reasons, 'summary': summary,
               'tmpVerdict': OBS.TMP_VERDICT if validated else None, 'tmpUnverified': list(OBS.TMP_UNVERIFIED),
               'daemonPath': facts.get('daemon'), 'refs': facts.get('refs'), 'anchor': anchor}
    P.record(ev, observer=verdict)
    P.say('observer: %s' % json.dumps({k: verdict[k] for k in ('state', 'reasons', 'tmpVerdict')}, sort_keys=True,
                                      ensure_ascii=False))


def action_pa(ev, verdicts):
    P = G5S.P
    try:
        G5S.action_g5s(ev, verdicts)
    finally:
        finish_observer(ev)
    verdicts['observer'] = ('validated: K4 / K3 / K2 as expected; %s' % OBS.TMP_VERDICT if STATE['validated']
                            else 'not validated: %s' % '; '.join(STATE['reasons']))
    if not STATE['validated']:
        raise P.Stop(16, 'observer not validated (no V6 go; D-P2 r1: design (b) next, no retry)')


def main(argv):
    global G5S, OBS
    hexes = argv[2:] if len(argv) == 5 else []
    if len(argv) != 5 or argv[1] not in ('check', ACTION) or not all(
            len(h) == 64 and all(c in '0123456789abcdef' for c in h) for h in hexes):
        print('usage: pa-start.py check|pas2 <sha256 of pa-expected.json> <g5t run.json sha256> <g5r run.json sha256>')
        return 2
    if os.geteuid() != 0:
        print('run as root')
        return 2
    ARGS.update(g5t=argv[3], g5r=argv[4])
    own = hashlib.sha256(open(os.path.abspath(__file__), 'rb').read()).hexdigest()
    try:  # a pin or expected-file refusal before the dispatcher: rc 3, nothing done
        OBS = load_pinned(PAO_PATH, PAO_SHA, 'pa_observe')
        G5S = load_pinned(G5S_PATH, G5S_SHA, 'g5_start')
        G5S.G3 = G5S.load_pinned(G5S.G3_PATH, G5S.G3_SHA, 'g3_start')
        G5S.EXPECTED_FILE = G5S.G3.EXPECTED_FILE = EXPECTED_PATH          # 1. the PA path, before any load
        image, plan = G5S.G3.load_expected(argv[2])                         # 2. reads the PA file
        G5S.GEN_G5, G5S.ACTION, G5S.SCRIPT_SHA = GEN, ACTION, own           # names wire copies into the dispatcher
        G5S.guard_g5, G5S.open_evidence_g5 = guard_pa, open_evidence_pa
        G5S.reference_g5 = reference_pa
        G5S.wire(image, plan, argv[2])                                      # 3. wire with the PA image / plan
        G5S.G3_READINESS, G5S.G3_WAIT_IDLE = G5S.G3.readiness, G5S.G3.g3_wait_idle
        G5S.G3.readiness, G5S.G3.g3_wait_idle = readiness_pa, wait_idle_pa  # 4. final replacements, after wire
        G5S.G3.ledgers_ok = ledgers_pa
        G5S.P.ACTIONS = {ACTION: action_pa}
        assert (G5S.P.guard is guard_pa and G5S.P.open_evidence is open_evidence_pa and G5S.G3.ledgers_ok is ledgers_pa
                and G5S.G3.EXPECTED['sha'] == argv[2] and G5S.P.RUNNER_REF == image
                and G5S.G3.EXPECTED_FILE == EXPECTED_PATH), 'pa-start wiring self-check'
    except (SystemExit, AssertionError) as refused:
        print(refused)
        return 3
    P = G5S.P
    try:
        STATE['facts']['observerPrecheck'] = OBS.precheck()  # r4: its own capture, stderr kept
    except OBS.ObserverError as error:
        print('observer precheck: %s; nothing done' % error.message)
        for entry in error.detail or []:  # r4: each call's rc and stderr, in order (no evidence directory yet)
            print('observer precheck call: %s' % json.dumps(entry, sort_keys=True, ensure_ascii=False))
        return 3
    for entry in STATE['facts']['observerPrecheck']['calls']:  # r4: a pass shows the same (warnings included)
        print('observer precheck call: %s' % json.dumps(entry, sort_keys=True, ensure_ascii=False))
    if argv[1] == 'check':
        try:
            return G5S.check_g5()
        except P.Stop as stop:
            P.say('check stopped: %s' % stop.message)
            return stop.code
    return P.main(argv[:2])


if __name__ == '__main__':
    sys.exit(main(sys.argv))
