"""#68 dev-VM stage V6 (user decision 2026-11-10, #68 issuecomment-6763432576): the gen 5 start observation (no
model turn) and the T5 retry, built on the installed V6-pre script. Development VM only. Root on the guest:

    python3 -I -B /root/native68/v6-turns.py check   # read-only: changes nothing, writes no file
    systemd-run … python3 -I -B /root/native68/v6-turns.py gen5   # once: entry, gen 5 Pod, three reads, NO message
                                                                  # POST, session revoked, natural idle stop watched
    systemd-run … python3 -I -B /root/native68/v6-turns.py t5     # once, separate PM go: entry, gen 6 Pod, three
                                                                  # reads, then the T5 retry (one model turn)

Base. /root/native68/v6pre-turns.py (revision 2, sha256 6170fec8…) is read once, its sha256 checked, and the bytes
that were hashed are compiled into a module; it is a library here. Its own actions (run-a/stop/run-b/run-x) are not
reachable. Exactly these names of it are replaced: ALLOCATION, used, gate, guard, open_evidence, ACTIONS. Every other
rule (claim before the POST, 60 s between turns, one rate limit stops everything, input over 200k in a turn stops,
non-202 POST leaves the claim unknown and stops, settlement from the rollout, residue scan, session revoke, final
invariants, exit.json) is the base code unchanged. Evidence: evidence/v6-<action>-<UTC>/ (out.txt, run.json,
exit.json); the lock is the same file (evidence/v6pre.lock), so no V6-pre action, V6 action or v6-settle.py runs at the
same time. A nonzero rc is never rerun; a missing exit.json means the result is unknown.

U2 gate exception (one claim only). The base gate refuses any turn while any claim of either ledger is not settled.
Here a claim that is not settled is let through only when ALL of these hold, else the gate refuses as before:
- the resources-6 ledger has a `settlements` list of exactly one row, the isolation-42 ledger has no `settlements`;
- that row has exactly the fields of v6-settle.py with exactly its values (kind assumed-deduction-risk-acceptance,
  bundle resources-6, claim 27c8a751-a43a-4281-a163-e0bebc2fece9, claimSha256 f9055176…, ledgerSha256Before
  e83369a4…, assumedDeduction exactly {input 200000, output 5000} as ints, actualUsage "unknown", the decision URL,
  decidedOn 2026-11-10, the fixed reason and recordedBy texts, tool = the sha256 of the reviewed v6-settle.py, a
  canonical UUID id and a UTC second recordedAt);
- that claim is present exactly once and its canonical JSON sha256 is still f9055176… (status unknown, no usage:
  any later change to it, a usage added included, ends the exception);
- the claim let through is that one id in resources-6 only; that id in any other ledger refuses, and an unsettled
  claim of another ledger is never let through (round 1 review). Any other unknown/unsettled claim, any rate limit,
  the allocation and the token checks still refuse. A new allocation alone does not lift the unknown gate.
T5 after a stopped turn: when model_turn stops (non-202, unsettled, a limit, a timeout), a best-effort read-only
observation (protected names, policy check names) is recorded under `afterFailedTurn`; the original rc is kept. The
other post-turn data (PV file comparison, exec pairs, residue scan) exist only for a turn that returned.
Assumed deduction. `used()` adds 200,000/5,000 to resources-6 exactly once, only while the exception holds; the
claim has no usage (pinned), so it is not counted twice. The resulting resources-6 remainder is an ASSUMED figure:
the actual usage of that claim is unknown. 200,000/5,000 are also the base script's start estimates (v6pre-turns.py
L418); they are not limits: a turn cannot be interrupted, L1120 only records an input overrun and L1176 stops the
next turn. This round adds one stop: output over 5,000 in a turn stops (rc 8) like the input rule.

Allocation (user decision: 16 new V6 turns in isolation-42; PM 2026-11-10: T5 counts in the 16; the V6-pre
remainder (T5 retry 1, reserve 1) stays unused and is no permission to run). isolation-42 `v6` 16 = T5 1, V6-1 1,
V6-2 4, V6-3 1, V6-4 1, V6-5 1, V6-6 1, V6-7 1, V6-8 1, V6-10 1, X2 (extra 2, fileChange) 1, and 2 reserve no item can
use (a use needs a new table: review + PM go, beyond 16 a user decision). resources-6 `v6-x` 1 = T6 1 (the retry).
Every claim is one execution: at most 8 + 16 = 24 isolation-42 claims and 1 + 1 = 2 resources-6 claims. This round
implements only gen5 and t5; the V6 item prompts/verdicts and the T6 retry action are the next round.
Three budgets, each checked before every turn (the first one exhausted stops; nothing is retried automatically; more
tokens are a user decision): turns (the allocation item and total, and the bundle's turn limit); input (bundle limit
minus used >= 200,000 start estimate); output (bundle limit minus used >= 5,000 start estimate). After every turn:
input over 200,000, output over 5,000, a rate limit or a failed turn stops.

Reads (gen5 and before the T5 POST). After the Pod is Running and ready: three reads at the container start time
+5 s, +35 s, +65 s (run-x: the first refusal 0.3 s after start, then every 30 s). Each read: the runner Pod (UID,
container id, restart count), the runner log (rc and the fixed check names of runner_policy_check lines), GET
/codex/status (available, reason, verification) and the four workspace names. A read stops (rc 4, no message POST)
on: Pod not found, the Pod UID, container id or restart count changed (not the same log sequence), a restart count
other than 0, a log read failure, any runner_policy_check line, a name lookup failure, `.codex` present, a status
reason outside the pending set, or a still pending status at the third read. A pass needs the EXPLICIT signal
/codex/status available true, reason null, verification unverified/real at the third read: the API takes it from
the runner's /health, which is available only after the runner's Native started, and Native runs check_local and
check_policy before it starts (backend/runner/app.py 193, 239, 246; 1017-1029). Log silence is never a pass. The
signal holds for that moment only; check_local runs again on every turn.
"""
import hashlib
import json
import os
import re
import stat
import sys
import time
import types
import uuid

BASE_PATH = '/root/native68/v6pre-turns.py'
BASE_SHA = '6170fec826400589ef0ac4ed38a4fd070f2abe2625edf6e1c8bb2887f7898e73'
SETTLE_SHA = '1d96c6f666779c589dd81df8690358476c76b75450cc892111c996314b757e48'
DECISION = 'https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/68#issuecomment-6763432576'
REASON = ('message POST answered 503, usage unknown; assumed deduction 200000 input / 5000 output accepted as a risk '
          'by user decision 2026-11-10; actual usage unknown (not a measured value, not an enforced limit)')
SETTLED_BUNDLE = 'resources-6'
SETTLED_CLAIM = '27c8a751-a43a-4281-a163-e0bebc2fece9'
SETTLED_CLAIM_SHA = 'f9055176dca046a3907970eb8ebfdbb01a2249f9616e214c5057b1f90af60ff9'
LEDGER_SHA_BEFORE = 'e83369a42ec690ae604b041d02055e8d03e780d342e459c105aef51126620592'
DEDUCTION = {'input': 200000, 'output': 5000}
FIXED_ROW = {'kind': 'assumed-deduction-risk-acceptance', 'bundle': SETTLED_BUNDLE, 'claim': SETTLED_CLAIM,
             'claimSha256': SETTLED_CLAIM_SHA, 'ledgerSha256Before': LEDGER_SHA_BEFORE, 'actualUsage': 'unknown',
             'reason': REASON, 'decision': DECISION, 'decidedOn': '2026-11-10',
             'recordedBy': 'v6-settle.py (run by PM)', 'tool': SETTLE_SHA}
ROW_KEYS = set(FIXED_ROW) | {'id', 'assumedDeduction', 'recordedAt'}

ALLOCATION = {'isolation-42': ('v6', 16, {'T5': 1, 'V6-1': 1, 'V6-2': 4, 'V6-3': 1, 'V6-4': 1, 'V6-5': 1, 'V6-6': 1,
                                          'V6-7': 1, 'V6-8': 1, 'V6-10': 1, 'X2': 1}),  # 2 reserve: no item
              'resources-6': ('v6-x', 1, {'T6': 1})}
START_INPUT, START_OUTPUT = 200000, 5000

PROTECTED = ('.git', '.agents', '.codex', '.aws')
READS = (5, 35, 65)
PENDING = {'not_configured', 'runner_unreachable', 'provisioning'}  # not yet started; only at reads 1-2
STATUS_WORD = re.compile(r'[a-z_]{1,40}')
CHECK_NAME = re.compile(r'[a-z_.]{1,64}')
POLICY_LINE = re.compile(r'runner_policy_check (\{.*\})')
IDLE_LIMIT, IDLE_POLL = 3600, 30
ORDER = {'gen5': None, 't5': 'gen5'}

P = None  # the base module, set by load_base()


# Pure functions (tested in Docker without the base) --------------------------------------------------------
def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def row_problem(row):
    if not isinstance(row, dict):
        return 'the settlement row is not an object'
    if set(row) != ROW_KEYS:
        return 'the settlement row fields are not exactly the expected fields'
    for key, value in FIXED_ROW.items():
        if type(row[key]) is not type(value) or row[key] != value:
            return 'settlement row field %s differs' % key
    deduction = row['assumedDeduction']
    if not (isinstance(deduction, dict) and set(deduction) == set(DEDUCTION)
            and all(type(deduction[k]) is int and deduction[k] == DEDUCTION[k] for k in DEDUCTION)):
        return 'settlement row field assumedDeduction differs'
    try:
        if not isinstance(row['id'], str) or str(uuid.UUID(row['id'])) != row['id'] or uuid.UUID(row['id']).version != 4:
            return 'settlement row id is not a canonical UUID4'
    except ValueError:
        return 'settlement row id is not a canonical UUID4'
    if not (isinstance(row['recordedAt'], str) and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', row['recordedAt'])):
        return 'settlement row recordedAt is not a UTC second'
    return None


def settlement_state(ledgers):
    """(set of claim ids the exception lets through, problem or None). `ledgers`: {bundle: parsed ledger}.
    No settlements anywhere: (empty, None) and the base rule applies (every unsettled claim refuses)."""
    for bundle, ledger in ledgers.items():
        if bundle != SETTLED_BUNDLE and 'settlements' in ledger:
            return set(), 'a settlements key in %s' % bundle
        if bundle != SETTLED_BUNDLE and any(isinstance(c, dict) and c.get('id') == SETTLED_CLAIM
                                            for c in ledger.get('claims', [])):
            return set(), 'the settled claim id appears in %s' % bundle
    ledger = ledgers.get(SETTLED_BUNDLE)
    if ledger is None or 'settlements' not in ledger:
        return set(), None
    rows = ledger['settlements']
    if not isinstance(rows, list) or len(rows) != 1:
        return set(), 'settlements in %s is not a list of exactly one row' % SETTLED_BUNDLE
    problem = row_problem(rows[0])
    if problem:
        return set(), problem
    matches = [c for c in ledger.get('claims', []) if isinstance(c, dict) and c.get('id') == SETTLED_CLAIM]
    if len(matches) != 1:
        return set(), 'the settled claim is not present exactly once'
    if canonical_sha(matches[0]) != SETTLED_CLAIM_SHA:
        return set(), 'the settled claim differs from its pinned record'
    return {SETTLED_CLAIM}, None


def unsettled_problem(ledgers):
    """The ledger part of the gate: None or the reason it refuses."""
    excepted, problem = settlement_state(ledgers)
    if problem:
        return 'settlement record not accepted: %s' % problem
    for bundle, ledger in ledgers.items():
        # The exception covers the one resources-6 claim only: an unsettled claim of any other bundle refuses even
        # with the same id (review round 1 major).
        allowed = excepted if bundle == SETTLED_BUNDLE else set()
        if any(not isinstance(c, dict) or (c.get('status') != 'settled' and c.get('id') not in allowed)
               for c in ledger['claims']):
            return 'an unsettled claim in %s' % bundle
        if ledger.get('rateLimited'):
            return 'a rate-limit stop is recorded in %s' % bundle
    return None


def used(ledger):
    """(turns, input, output) of one ledger: every claim is a turn; recorded usage summed; plus the assumed deduction
    once for resources-6 while the exception holds (the settled claim has no usage, pinned by its sha256)."""
    claims = ledger['claims']
    turns = len(claims)
    inp = sum((c.get('usage') or {}).get('input', 0) for c in claims)
    out = sum((c.get('usage') or {}).get('output', 0) for c in claims)
    if ledger.get('bundle') == SETTLED_BUNDLE:
        excepted, problem = settlement_state({SETTLED_BUNDLE: ledger})
        if excepted and not problem:
            inp, out = inp + DEDUCTION['input'], out + DEDUCTION['output']
    return turns, inp, out


def allocation_problem(ledger, bundle, item, allocation=ALLOCATION):
    """The three budgets for one more turn of `item`: None or the reason it refuses."""
    turns, inp, out = used(ledger)
    limits = ledger['limits']
    name, total, items = allocation[bundle]
    mine = [c for c in ledger['claims'] if c.get('allocation') == name]
    if len(mine) >= total:
        return 'the %s allocation (%d) is used' % (name, total)
    if item not in items:
        return 'the %s allocation has no allowance for %s' % (name, item)
    if sum(1 for c in mine if c.get('item') == item) >= items[item]:
        return 'the %s allowance for %s (%d) is used' % (name, item, items[item])
    if turns >= limits['turns']:
        return 'the %s bundle turn limit is reached' % bundle
    if limits['input'] - inp < START_INPUT:
        return 'the %s bundle has less than %d input left' % (bundle, START_INPUT)
    if limits['output'] - out < START_OUTPUT:
        return 'the %s bundle has less than %d output left' % (bundle, START_OUTPUT)
    return None


def policy_checks(log):
    """The fixed check names of runner_policy_check lines (native_policy.refuse); 'unparsed' for any other form."""
    found = []
    for line in log.splitlines():
        if 'runner_policy_check' not in line:
            continue
        name = 'unparsed'
        match = POLICY_LINE.search(line)
        if match:
            try:
                value = json.loads(match.group(1))
            except ValueError:
                value = None
            if isinstance(value, dict) and isinstance(value.get('check'), str) and CHECK_NAME.fullmatch(value['check']):
                name = value['check']
        found.append(name)
    return found


def identity(pod):
    statuses = (pod.get('status') or {}).get('containerStatuses') or []
    one = statuses[0] if len(statuses) == 1 else {}
    running = (one.get('state') or {}).get('running') or {}
    return {'pod': (pod.get('metadata') or {}).get('uid'), 'container': one.get('containerID'),
            'restarts': one.get('restartCount'), 'started': running.get('startedAt')}


def status_word(value):
    return value if value is None or isinstance(value, (bool, int)) or (isinstance(value, str) and STATUS_WORD.fullmatch(value)) \
        else 'other'


def judge_read(read, first, index):
    """('available' | 'pending' | None, problem or None) for read number `index` (0, 1, 2)."""
    if not read.get('found'):
        return None, 'runner Pod not found'
    if first.get('restarts') != 0:
        return None, 'the container had restarted before the first read'
    for key, label in (('pod', 'Pod UID'), ('container', 'container id'), ('restarts', 'restart count')):
        if first.get(key) is None or read['ident'].get(key) != first.get(key):
            return None, '%s changed or missing' % label
    if read.get('logRc') != 0:
        return None, 'runner log read failed'
    if read.get('checks'):
        return None, 'runner_policy_check logged (%s)' % ','.join(sorted(set(read['checks'])))
    names = read.get('names') or {}
    if set(names) != set(PROTECTED) or any(v == 'error' for v in names.values()):
        return None, 'workspace name lookup failed'
    if names['.codex'] is not None:
        return None, '.codex present in the workspace'
    status = read.get('status') or {}
    if (status.get('available') is True and 'reason' in status and status['reason'] is None
            and status.get('verification') in ('unverified', 'real')):
        return 'available', None
    if index < len(READS) - 1 and status.get('available') is False and status.get('reason') in PENDING:
        return 'pending', None
    return None, 'no explicit available signal (status %s)' % json.dumps(
        {k: status_word(v) for k, v in sorted(status.items())}, sort_keys=True)


def names_change(before, after):
    """Per protected name: (present before, present after, same identity). 'error' entries compare as unknown."""
    result = {}
    for name in PROTECTED:
        b, a = before.get(name), after.get(name)
        result[name] = {'before': 'error' if b == 'error' else b is not None, 'after': 'error' if a == 'error' else a is not None,
                        'same': None if 'error' in (a, b) else a == b}
    return result


# Base and overrides ----------------------------------------------------------------------------------------
def load_base(path=BASE_PATH, sha=BASE_SHA):
    data = open(path, 'rb').read()
    if hashlib.sha256(data).hexdigest() != sha:
        raise SystemExit('base %s is not %s; nothing done' % (path, sha[:8]))
    module = types.ModuleType('v6pre_turns')
    module.__file__ = path
    exec(compile(data, path, 'exec'), module.__dict__)
    return module


def ledgers_now():
    return {b: P.load_ledger(b) for b in P.LEDGERS}


def gate(bundle, item, session_expiry):
    """The base gate with the one-claim exception and the V6 allocation, read again under the lock."""
    ledgers = ledgers_now()
    reason = unsettled_problem(ledgers) or allocation_problem(ledgers[bundle], bundle, item)
    if reason:
        return reason
    ended = P.last_end()
    if ended is not None and time.time() - ended < P.GAP:
        time.sleep(P.GAP - (time.time() - ended) + 1)
    if session_expiry - time.time() < P.SESSION_MARGIN:
        return 'the session has less than %d s left' % P.SESSION_MARGIN
    if P.STOPPING['requested']:
        return 'a stop signal was received'
    return None


def v6_runs(action):
    return sorted(d for d in os.listdir(P.EVIDENCE) if re.fullmatch(r'v6-%s-\d{8}T\d{6}Z' % re.escape(action), d))


def run_record(folder):
    return json.load(open(os.path.join(P.EVIDENCE, folder, 'run.json')))


def guard(action):
    """One run per action, in order: gen5, then t5 (separate PM go) only after exactly one gen5 that passed."""
    if v6_runs(action):
        raise P.Stop(3, '%s already ran (%s); a second run is a PM decision' % (action, v6_runs(action)[-1]))
    before = ORDER[action]
    if before is not None:
        done = v6_runs(before)
        if len(done) != 1 or not P.completed_ok(done[0]) or run_record(done[0]).get('gen5') != 'pass':
            raise P.Stop(3, '%s needs exactly one %s that passed (rc 0, step rc 0, final invariants true, session '
                            'revoked, gen5 pass)' % (action, before))


def open_evidence(action):
    ev = os.path.join(P.EVIDENCE, 'v6-%s-%s' % (action, time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())))
    os.mkdir(ev, 0o700)
    fd = os.open(os.path.join(ev, 'out.txt'), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    P.OUT['stream'] = os.fdopen(fd, 'w')
    P.record(ev, action=action, started=P.now_utc(), base=BASE_SHA, script=SCRIPT_SHA)
    P.say('evidence: %s (base %s, script %s)' % (ev, BASE_SHA[:8], SCRIPT_SHA[:8]))
    return ev


# Observation steps -----------------------------------------------------------------------------------------
def protected_names():
    """Per name: None when absent (ENOENT only), 'error' when the lookup failed, else [type, inode, mtime_ns]."""
    result = {}
    for name in PROTECTED:
        try:
            info = os.lstat(os.path.join(P.WORKSPACE_HOST, name))
        except FileNotFoundError:
            result[name] = None
            continue
        except OSError:
            result[name] = 'error'
            continue
        kind = 'dir' if stat.S_ISDIR(info.st_mode) else 'file' if stat.S_ISREG(info.st_mode) else \
            'link' if stat.S_ISLNK(info.st_mode) else 'other'
        result[name] = [kind, info.st_ino, info.st_mtime_ns]
    return result


def say_names(label, names):
    P.say('%s: %s' % (label, json.dumps({k: ('absent' if v is None else v if v == 'error' else v[0])
                                         for k, v in names.items()}, sort_keys=True)))


def read_once():
    pods = [p for p in P.runner_pods() if p['metadata']['name'] == P.POD_NAME]  # a lookup failure stops (rc 4)
    if len(pods) != 1:
        return {'found': False}
    rc, log = P.run([P.K3S, 'kubectl', '-n', P.RUNNER_NS, 'logs', P.POD_NAME, '--tail=5000'], 60)
    return {'found': True, 'ident': identity(pods[0]), 'logRc': rc, 'checks': policy_checks(log) if rc == 0 else [],
            'status': P.runner_status(), 'names': protected_names()}


def readiness(ev, pod, key):
    """The three reads; raises Stop(4) at the first failing read (no message POST). Returns the fixed fields."""
    first = identity(pod)
    started = P.stamp(first['started']) if first['started'] else None
    if started is None:
        raise P.Stop(4, 'the container start time is unknown; no POST')
    reads = []
    for index, offset in enumerate(READS):
        while time.time() < started + offset:
            if P.STOPPING['requested']:
                raise P.Stop(4, 'a stop signal was received before read %d; no POST' % (index + 1))
            time.sleep(min(1.0, started + offset - time.time()))
        read = read_once()
        state, problem = judge_read(read, first, index)
        fields = {'read': index + 1, 'at': round(time.time() - started, 1), 'found': read.get('found'),
                  'sameIdentity': bool(read.get('found')) and all(read['ident'].get(k) == first.get(k)
                                                                  for k in ('pod', 'container', 'restarts')),
                  'restarts': (read.get('ident') or {}).get('restarts'), 'logRc': read.get('logRc'),
                  'checks': sorted(set(read.get('checks') or [])), 'status': {k: status_word(v) for k, v in
                                                                             sorted((read.get('status') or {}).items())},
                  'names': {k: ('absent' if v is None else v if v == 'error' else v[0])
                            for k, v in (read.get('names') or {}).items()},
                  'state': state, 'problem': problem}
        reads.append(fields)
        P.record(ev, **{key: reads})
        P.say('read %d at +%.1f s: %s' % (index + 1, fields['at'], json.dumps(fields, sort_keys=True)))
        if problem:
            raise P.Stop(4, 'read %d: %s; no message POST' % (index + 1, problem))
    if reads[-1]['state'] != 'available':
        raise P.Stop(4, 'no explicit available signal at the last read; no message POST')
    return reads, read['names']


def wait_idle(ev, generation):
    """Read-only: wait for the natural idle stop (no DELETE). Fixed fields; ok only with exactly one completed stop
    row of this generation and the registry idle_stopped on it, Pod absent."""
    deadline, failures, state = time.time() + IDLE_LIMIT, 0, None
    while True:
        try:
            state, pods = P.registry(), P.runner_pods()
            if state['phase'] == 'idle_stopped' and state['pod'] is None and not pods:
                break
        except P.Stop:
            failures += 1
        if time.time() > deadline or P.STOPPING['requested']:
            result = {'ok': False, 'why': 'stop signal' if P.STOPPING['requested'] else 'not idle_stopped within %d s'
                      % IDLE_LIMIT, 'lookupFailures': failures}
            P.record(ev, idleStop=result)
            return result
        time.sleep(IDLE_POLL)
    try:
        stops = P.sql('stops', "SELECT json_build_object('gen',generation,'receipt',receipt->'version','completed',"
                      "completed_at IS NOT NULL,'requested',requested_at,'completedAt',completed_at) FROM "
                      "runner_termination.stops WHERE owner_id='%s' AND generation=%d ORDER BY requested_at;"
                      % (P.NEW, generation), 11)
    except P.Stop:
        result = {'ok': False, 'why': 'stop row lookup failed', 'lookupFailures': failures + 1}
        P.record(ev, idleStop=result)
        return result
    ok = state['gen'] == generation and len(stops) == 1 and stops[0].get('completed') is True
    result = {'ok': ok, 'gen': state['gen'], 'stops': len(stops), 'completed': [s.get('completed') for s in stops],
              'receipt': [s.get('receipt') for s in stops], 'requested': [s.get('requested') for s in stops],
              'completedAt': [s.get('completedAt') for s in stops], 'lookupFailures': failures}
    P.record(ev, idleStop=result)
    P.say('idle stop: %s' % json.dumps(result, sort_keys=True))
    return result


# Actions ---------------------------------------------------------------------------------------------------
def act_gen5(ev, verdicts):
    ok, state, broker = P.precheck(lambda s, pods: s['phase'] == 'idle_stopped' and s['pod'] is None and s['gen'] == 4
                                   and not pods)
    if not ok:
        raise P.Stop(3, 'precheck refused; nothing changed')
    names0 = protected_names()
    say_names('workspace names before the entry', names0)
    if names0['.codex'] is not None or 'error' in names0.values():
        raise P.Stop(3, '.codex present or a name lookup failed before the entry; nothing changed')
    P.record(ev, namesBefore=names0)
    P.create_session(ev)
    failure, pod_uid = None, None
    try:
        pod = P.enter_and_wait(ev, 5, None)
        pod_uid = pod['metadata']['uid']
        reads, names = readiness(ev, pod, 'reads')
        verdicts['gen5 start'] = ('explicit available signal at the third read (+%.1f s), no runner_policy_check line '
                                  'in three reads, same Pod/container, 0 restarts, .codex absent; no message POST'
                                  % reads[-1]['at'])
    except P.Stop as stop:
        failure = stop
        verdicts['gen5 start'] = 'not passed: %s (observed state only; not a check_local result)' % stop.message
    P.revoke_session(ev)
    idle = wait_idle(ev, 5)
    names1 = protected_names()
    change = names_change(names0, names1)
    P.record(ev, namesAfter=names1, namesChange=change, gen5Pod=pod_uid)
    P.say('workspace names after the idle stop vs before the entry: %s' % json.dumps(change, sort_keys=True))
    verdicts['gen5 idle stop'] = ('natural idle stop observed: one completed stop row of generation 5 (no DELETE sent)'
                                  if idle['ok'] else 'not observed (%s)' % idle.get('why', 'stop rows %s' % idle.get('stops')))
    if failure is not None:
        raise failure
    if names1['.codex'] is not None or 'error' in names1.values():
        verdicts['gen5 start'] += '; but .codex present or a name lookup failed after the idle stop'
        raise P.Stop(4, '.codex present or a name lookup failed after the idle stop')
    if not idle['ok']:
        raise P.Stop(11, 'the natural idle stop of generation 5 was not observed')
    P.record(ev, gen5='pass')


def after_failed_turn(ev, names_before, verdicts):
    """Read-only, best effort, after model_turn stopped (503/unknown/unsettled/over a limit/timeout): the protected
    names and the policy check names in the runner log, recorded under separate keys. Never changes the rc; a failure
    here is recorded as 'not completed'. (Interrupted, the second signal, skips it.)"""
    try:
        rc, log = P.run([P.K3S, 'kubectl', '-n', P.RUNNER_NS, 'logs', P.POD_NAME, '--tail=5000'], 60)
        checks = sorted(set(policy_checks(log))) if rc == 0 else None
        names_after = protected_names()
        change = names_change(names_before, names_after)
        P.record(ev, afterFailedTurn={'logRc': rc, 'checks': checks, 'namesAfter': names_after, 'namesChange': change})
        P.say('after the stopped turn (read-only): policy check names %s (log rc %d); workspace names before/after %s'
              % (checks, rc, json.dumps(change, sort_keys=True)))
        verdicts['T5 after the stopped turn'] = ('.codex present (writer not observed)' if names_after['.codex'] not in
                                                 (None, 'error') else 'name lookup failed' if 'error' in
                                                 names_after.values() else '.codex absent') + \
            '; policy check names %s' % checks
    except Exception as failure:
        try:
            P.say('after the stopped turn: observation not completed (%s)' % type(failure).__name__)
            verdicts['T5 after the stopped turn'] = 'observation not completed'
        except Exception:
            pass


def act_t5(ev, verdicts):
    gen5 = run_record(v6_runs('gen5')[0])
    first = run_record(P.runs('run-a')[0])
    ok, state, broker = P.precheck(lambda s, pods: s['phase'] == 'idle_stopped' and s['pod'] is None and s['gen'] == 5
                                   and not pods)
    if not ok:
        raise P.Stop(3, 'precheck refused; nothing changed')
    ledgers = ledgers_now()
    reason = unsettled_problem(ledgers) or allocation_problem(ledgers['isolation-42'], 'isolation-42', 'T5')
    if reason:
        raise P.Stop(3, 'T5 would not pass the gate (%s); nothing changed' % reason)
    names0 = protected_names()
    say_names('workspace names before the entry', names0)
    if names0['.codex'] is not None or 'error' in names0.values():
        raise P.Stop(3, '.codex present or a name lookup failed before the entry; nothing changed')
    target = os.path.join(P.WORKSPACE_HOST, 'v6pre-write.txt')

    def kept(value):
        return bool(value) and value['uid'] == 10001 and value['size'] == 5 and value['lines'] == [b'v6pre']
    entry_state = P.file_state(target)
    if not kept(entry_state):
        raise P.Stop(3, 'T5: the T2 file is not as expected before the action; nothing changed')
    start, auth_before = time.time(), broker['auth']
    P.create_session(ev)
    pod = P.enter_and_wait(ev, 6, gen5.get('gen5Pod'))
    before = P.file_state(target)
    if not kept(before):
        raise P.Stop(4, 'T5: the T2 file content or owner changed during the Pod start; no turn started')
    reads, names_before = readiness(ev, pod, 'reads')
    P.record(ev, namesBeforeTurn=names_before)
    name = 'T5 resume, new process'
    try:
        t5 = P.model_turn(ev, 'isolation-42', 'T5', 1, first['conversation'], P.PROMPTS['T5'], first['thread'])
    except P.Stop:
        after_failed_turn(ev, names_before, verdicts)
        raise  # the original rc and message are kept
    after = P.file_state(target)
    unchanged = after == before
    rc, log = P.run([P.K3S, 'kubectl', '-n', P.RUNNER_NS, 'logs', P.POD_NAME, '--tail=5000'], 60)
    checks = sorted(set(policy_checks(log))) if rc == 0 else None
    names_after = protected_names()
    change = names_change(names_before, names_after)
    P.record(ev, namesAfterTurn=names_after, namesChange=change, checksAfterTurn=checks)
    P.say('T5: policy check names in the log after the turn %s (log rc %d); workspace names before/after the turn %s'
          % (checks, rc, json.dumps(change, sort_keys=True)))
    verdicts['T5 protected names'] = ('.codex absent before and after the turn; the other three %s' % (
        'kept (same type, inode, mtime)' if all(change[n]['same'] for n in PROTECTED if n != '.codex')
        else 'changed or unknown (see namesChange)')) if names_after['.codex'] is None else \
        ('.codex PRESENT after the turn (writer not observed; the next Pod start refuses; removal is a user decision)'
         if names_after['.codex'] != 'error' else 'name lookup failed after the turn (unknown)')
    if t5['usage']['output'] > START_OUTPUT:
        verdicts[name] = 'output over %d in the turn: stopped' % START_OUTPUT
        raise P.Stop(8, 'T5: output over %d in this turn; nothing else starts' % START_OUTPUT)
    if checks is None or checks:
        verdicts[name] = 'policy refusal logged after the turn or log unread (checks %s)' % checks
        raise P.Stop(5, 'T5: runner policy check refused or log unreadable')
    if names_after['.codex'] is not None or 'error' in names_after.values():
        raise P.Stop(8, 'T5: .codex present or a name lookup failed after the turn; nothing else starts')
    kind = t5['calls']['kind']
    listed = [p for p in t5['calls']['pairs'] if P.LS_LINE.search(p['text'])]
    same = t5['thread'] == first['thread']
    P.say('T5: same thread %s, pairs whose result shows an ls -l line of the T2 file with size 5: %d, T2 file identical '
          'before and after %s' % (same, len(listed), unchanged))
    if not unchanged:
        verdicts[name] = 'the T2 file changed during a listing turn: not met (cause not verified)'
        raise P.Stop(8, 'T5: the T2 file changed; nothing else starts')
    if kind == 'pairs' and same and listed:
        verdicts[name] = ('limited functional observation, exit-0 check not met (inner exit code and cwd not recorded): '
                          'same thread in a new Pod %s (gen 6); an exec call with a nested exec_command whose result '
                          'shows an ls -l line naming the T2 file with size 5; the T2 file identical on the PV between '
                          'the Pod start and after the turn; the whole turn record complete; policy inferred, not '
                          'observed' % pod['metadata']['uid'])
    elif kind in ('unextractable', 'partial'):
        verdicts[name] = '%s (not verified)' % ('call present, nested result not extractable' if kind == 'unextractable'
                                                else 'the turn record is incomplete')
        raise P.Stop(13, 'T5: format gate (%s); nothing else starts' % kind)
    else:
        verdicts[name] = ('no tool call on every record path (not run)' if kind == 'none' else
                          'exec pairs present, the expected listing not shown (thread same %s): not met' % same)
    P.observe_after(pod['metadata']['uid'], start, auth_before, verdicts)


ACTIONS = {'gen5': act_gen5, 't5': act_t5}


def check():
    """Read-only: the base precheck, the settlement record, the three budgets per bundle, the allocation table,
    the workspace names, the V6 runs. Writes nothing."""
    ok, state, broker = P.precheck(lambda s, pods: True)
    ledgers = ledgers_now()
    excepted, problem = settlement_state(ledgers)
    rows = ledgers[SETTLED_BUNDLE].get('settlements')
    P.say('settlement rows in resources-6: %s; accepted %s%s' % (
        'absent' if rows is None else len(rows) if isinstance(rows, list) else 'not a list', bool(excepted),
        '' if problem is None else ' (%s)' % problem))
    for bundle, ledger in ledgers.items():
        turns, inp, out = used(ledger)
        plain = (sum((c.get('usage') or {}).get('input', 0) for c in ledger['claims']),
                 sum((c.get('usage') or {}).get('output', 0) for c in ledger['claims']))
        counted = (inp - plain[0]) // DEDUCTION['input'] if inp - plain[0] else 0
        limits = ledger['limits']
        assumed = bundle == SETTLED_BUNDLE and excepted
        P.say('%s: turns %d/%d, input recorded %d + assumed deduction counted %d time(s) -> left %d%s, output recorded '
              '%d -> left %d%s' % (bundle, turns, limits['turns'], plain[0], counted, limits['input'] - inp,
                                   ' (assumed: actual use of claim %s unknown)' % SETTLED_CLAIM[:8] if assumed else '',
                                   plain[1], limits['output'] - out, ' (assumed)' if assumed else ''))
        if bundle == SETTLED_BUNDLE and counted != (1 if excepted else 0):
            P.say('ASSUMED DEDUCTION COUNT WRONG for %s' % bundle)
            ok = False
        name, total, items = ALLOCATION[bundle]
        mine = [c for c in ledger['claims'] if c.get('allocation') == name]
        P.say('allocation %s/%s: %d of %d used; per item %s; next-turn gate (T5/T6): %s' % (
            bundle, name, len(mine), total, json.dumps({i: '%d/%d' % (sum(1 for c in mine if c.get('item') == i), n)
                                                       for i, n in items.items()}, sort_keys=True),
            unsettled_problem(ledgers) or allocation_problem(ledger, bundle, 'T5' if bundle == 'isolation-42' else 'T6')
            or 'would pass'))
    say_names('workspace names', protected_names())
    P.say('V6 runs: %s' % {a: [(r, P.exit_rc(r)) for r in v6_runs(a)] for a in ORDER})
    return 0 if ok else 3


def main(argv):
    global P, SCRIPT_SHA
    if len(argv) != 2 or argv[1] not in ('check',) + tuple(ACTIONS):
        print('usage: v6-turns.py check|gen5|t5')
        return 2
    if os.geteuid() != 0:
        print('run as root')
        return 2
    SCRIPT_SHA = hashlib.sha256(open(os.path.abspath(__file__), 'rb').read()).hexdigest()
    P = load_base()
    P.ALLOCATION, P.used, P.gate = ALLOCATION, used, gate
    P.guard, P.open_evidence, P.ACTIONS = guard, open_evidence, ACTIONS
    if argv[1] == 'check':
        try:
            return check()
        except P.Stop as stop:
            P.say('check stopped: %s' % stop.message)
            return stop.code
    return P.main(argv)


SCRIPT_SHA = None

if __name__ == '__main__':
    sys.exit(main(sys.argv))
