"""#68 G3a / G3b (design g3 r2 §2): the first and the second start of owner …5805's runner on the G2 image (code
0117d137), model-free (no message POST), on the development VM only (S1). Root on the guest:

    python3 -I -B /root/native68/g3-start.py check <sha256>   # read-only: changes nothing, writes no file
    systemd-run … python3 -I -B /root/native68/g3-start.py g3a <sha256>   # once: entry, gen 7 Pod, three reads,
                                                                         # session revoked, natural idle stop watched
    systemd-run … python3 -I -B /root/native68/g3-start.py g3b <sha256>   # once, separate PM go after review accepts
                                                                         # G3a: entry, gen 8 Pod, the same

<sha256> is the reviewed sha256 of /root/native68/g3-expected.json (the G3-2 file; this script reads only newImage
and newPlanSha256 from it).

Base. /root/native68/v6pre-turns.py (sha256 6170fec8…) and /root/native68/v6-turns.py (sha256 d9f1e9a7…) are each
read once, their sha256 checked, and the bytes that were hashed are compiled into modules (the v6-turns.py
load_base pattern). They are libraries here; their own actions are not reachable. Exactly these names of the base
are replaced: RUNNER_REF (= the G2 digest reference: the base precheck's binding image and start_checks' image and
imageID), guard, open_evidence, ACTIONS. From v6-turns.py only these are used: identity, policy_checks,
status_word, PENDING, READS, IDLE_LIMIT, IDLE_POLL. Not used: its wait_idle (it counts lookup errors and keeps
going, and its success test ignores them), act_gen5 / judge_read (they refuse a present `.codex`), its image pins.
Every other base rule (lock, evidence, exit.json, session create/revoke, entry, wait_pod, start_checks, the final
invariants) is the base code unchanged. Evidence: evidence/g3-<action>-<UTC>/ (out.txt, run.json, exit.json).

Sequence: precheck (the base precheck with the binding image = the G2 reference, plus: …5805 plan digest = the
new one; registry idle_stopped with no Pod on gen 6 (g3a) / 7 (g3b); `.codex`, the record and staging absent (g3a)
or equal to G3a's (g3b); the ledgers at their pins) -> session -> entry POST /api/research/codex/runner -> wait_pod
for gen 7 / 8 -> start_checks -> three reads at the container start +5 / +35 / +65 s -> session revoked ->
g3_wait_idle -> host state after the idle stop. No message POST, no DELETE, no stop request.
r1 (review code r0 major 1): a Stop from the entry, wait_pod, start_checks or any read revokes the session and
returns that Stop at once; g3_wait_idle and the after-state are never reached on that path.
r1 (major 2): the record's stat fields RECORD_FIELDS (dev, ino, mode, uid, gid, nlink, size, mtime_ns; ctime not
compared) and its sha256 after G3a are the G3b reference; G3b's precheck, every read and the after-state compare
all of them (the same bytes in a new inode, or a new mtime, differ).

Each read, in this order: the runner Pod (UID, container id, restart count), GET /codex/status, the runner log
(--tail=5000), the host state (stat only, no content read except the record's sha256). The status is read before
the log, so an `available` status means Native had finished its start (ensure_placeholder, check_local, launch)
before the log was read. A read stops (rc 4) on: Pod not found or its identity changed, restarts not 0, a log read
failure, any runner_policy_check line, an unparsed or refused runner_dot_codex_placeholder line, a line sequence
that is not the expected one (exactly, once the status is available; a prefix of it while pending), a different
ino, an excluded ino (131200 / 131192), a host state that does not match the lines, a host lookup failure, or no
explicit available signal at the third read.
Expected lines (backend/runner/app.py 196-197, 236; dot_codex.py 245-246, 370-386):
  g3a: created/start, matched/start, matched/launch; one ino N; no `replaced` (no record existed).
  g3b: matched/start, matched/start, matched/launch; ino = G3a's N; no `created`.
`not_runner_owned` means an unrecorded object was found: stop, no fix on the spot.

rc: the base codes (3 refused before any change; 4 entry/Pod/start/read failed, an idle-wait lookup error or
identity change, or the after-state differs; 7 session; 70 session not revoked; 12 final invariants; 9 unexpected;
130 interrupted twice) plus 11: the natural idle stop was not observed within IDLE_LIMIT (or the stop rows are not
exactly one completed row of the generation). A nonzero rc is never rerun.
"""
import hashlib
import json
import os
import re
import stat
import sys
import time
import types

BASE_PATH = '/root/native68/v6pre-turns.py'
BASE_SHA = '6170fec826400589ef0ac4ed38a4fd070f2abe2625edf6e1c8bb2887f7898e73'
V6_PATH = '/root/native68/v6-turns.py'
V6_SHA = 'd9f1e9a7be87adf13f4aee3d8e10f31cb8ab7839d2984d53ed19b7162ee19c48'
EXPECTED_FILE = '/root/native68/g3-expected.json'
NEW_PREFIX = 'docker.io/library/anxious-dev-runner@sha256:'
LEDGERS = {'isolation-42': '9a1a8173e3aebf45729cfed5b024d6732366886ecc8dd01bcaaaf6dbdfa98d3e',
           'resources-6': '51e7ae651e8dcb79213082362fef2bd6873c11f2000b9567719974c61b6eafeb'}
RECORD, STAGING, NAME = 'dot-codex-placeholder.json', '.placeholder-staging', '.codex'
OTHER_NAMES = ('.git', '.agents', '.aws')
EXCLUDED_INO = (131200, 131192)
RECORD_MAX = 64 * 1024
RECORD_FIELDS = ('dev', 'ino', 'mode', 'uid', 'gid', 'nlink', 'size', 'mtime_ns')  # G3a == G3b (+ sha256)
CODEX_FIELDS = ('dev', 'ino', 'mtime_ns')
LOG_KEYS = frozenset({'event', 'phase', 'stage', 'reason', 'errno', 'when', 'ino', 'replaced', 'staging_left'})
PLACEHOLDER_LINE = re.compile(r'runner_dot_codex_placeholder (\{.*\})\s*$')
SEQUENCES = {'g3a': (('created', 'start'), ('matched', 'start'), ('matched', 'launch')),
             'g3b': (('matched', 'start'), ('matched', 'start'), ('matched', 'launch'))}
GEN = {'g3a': 7, 'g3b': 8}
ORDER = {'g3a': None, 'g3b': 'g3a'}
HEX64 = re.compile(r'[0-9a-f]{64}')

P = None   # the base module (v6pre-turns.py)
V6 = None  # v6-turns.py, for its read helpers
SCRIPT_SHA = None
EXPECTED = {}


# Pure functions (tested in Docker) ----------------------------------------------------------------------
def placeholder_lines(log):
    """Every runner_dot_codex_placeholder line as its JSON object, or 'unparsed' (not one object of the fixed keys
    with fixed-shape values)."""
    found = []
    for line in log.splitlines():
        if 'runner_dot_codex_placeholder' not in line:
            continue
        match = PLACEHOLDER_LINE.search(line)
        value = None
        if match:
            try:
                value = json.loads(match.group(1))
            except ValueError:
                value = None
        ok = isinstance(value, dict) and value and set(value) <= LOG_KEYS and all(
            (isinstance(v, str) and re.fullmatch(r'[a-z_]{1,40}', v)) or (type(v) is int and v >= 0) or v is True
            for v in value.values())
        found.append(value if ok else 'unparsed')
    return found


def judge_lines(lines, mode, available, ino_expected=None):
    """(ino or None, problem or None) for the placeholder lines of one read. Exactly the mode's sequence once the
    status is available, a prefix of it while pending; every line {event, phase, ino} with one ino N that is not
    excluded (and equals G3a's N in g3b)."""
    if any(line == 'unparsed' for line in lines):
        return None, 'an unparsed runner_dot_codex_placeholder line'
    refused = [line for line in lines if line.get('event') == 'refused']
    if refused:
        first = refused[0]
        if first.get('reason') == 'not_runner_owned':
            return None, 'refused not_runner_owned: an unrecorded object was found (no fix on the spot)'
        return None, 'refused %s/%s at %s' % (first.get('stage'), first.get('reason'), first.get('phase'))
    want = SEQUENCES[mode]
    got = tuple((line.get('event'), line.get('phase')) for line in lines)
    if available and got != want:
        return None, 'placeholder lines are %s, want %s' % (json.dumps(got), json.dumps(want))
    if not available and got != want[:len(got)]:
        return None, 'placeholder lines are %s, not a prefix of %s' % (json.dumps(got), json.dumps(want))
    if any(set(line) != {'event', 'phase', 'ino'} for line in lines):
        return None, 'a placeholder line has other keys than event/phase/ino'
    inos = {line['ino'] for line in lines}
    if len(inos) > 1:
        return None, 'placeholder lines carry different ino numbers'
    ino = inos.pop() if inos else None
    if ino is not None and ino in EXCLUDED_INO:
        return None, 'the placeholder ino is %d, an excluded ino' % ino
    if ino is not None and ino_expected is not None and ino != ino_expected:
        return None, 'the placeholder ino differs from G3a\'s'
    return ino, None


def judge_host(host, mode, ino, available, before=None):
    """None or the problem of the host state of one read. `before` (g3b): G3a's recorded state."""
    if 'error' in json.dumps(host, sort_keys=True):
        return 'a host lookup failed'
    codex, rec, staging = host[NAME], host[RECORD], host[STAGING]
    if mode == 'g3b' or available or ino is not None:
        if codex is None or rec is None or staging is None:
            return '.codex, the record or staging is absent'
    if codex is not None:
        if (codex['type'], codex['mode'], codex['uid'], codex['entries']) != ('dir', '0o755', 10001, 0):
            return '.codex is not an empty 0755 directory of uid 10001'
        if codex['ino'] in EXCLUDED_INO:
            return '.codex has an excluded ino'
        if ino is not None and codex['ino'] != ino:
            return '.codex ino differs from the logged ino'
    if rec is not None and ((rec['type'], rec['mode'], rec['uid'], rec['nlink']) != ('file', '0o600', 10001, 1)
                            or rec['size'] > RECORD_MAX):
        return 'the record is not a 0600 regular file of uid 10001 with one link, at most 64 KiB'
    if staging is not None and (staging['type'], staging['mode'], staging['entries']) != ('dir', '0o700', 0):
        return 'staging is not an empty 0700 directory'
    if before is not None:
        if [codex.get(k) for k in CODEX_FIELDS] != [before['codex'].get(k) for k in CODEX_FIELDS]:
            return '.codex dev/ino/mtime differ from G3a'
        moved = [k for k in RECORD_FIELDS + ('sha256',) if k not in rec or k not in before['record']
                 or rec[k] != before['record'][k]]
        if moved:
            return 'the record differs from G3a in %s' % ','.join(moved)
    for name in OTHER_NAMES:
        if host[name] is None:
            return '%s is absent' % name
    return None


def judge_read(read, first, index, mode, ino_expected=None, before=None):
    """('available' | 'pending' | None, ino, problem) for read number `index` (0, 1, 2)."""
    if not read.get('found'):
        return None, None, 'runner Pod not found'
    if first.get('restarts') != 0:
        return None, None, 'the container had restarted before the first read'
    for key, label in (('pod', 'Pod UID'), ('container', 'container id'), ('restarts', 'restart count')):
        if first.get(key) is None or read['ident'].get(key) != first.get(key):
            return None, None, '%s changed or missing' % label
    if read.get('logRc') != 0:
        return None, None, 'runner log read failed'
    if read.get('checks'):
        return None, None, 'runner_policy_check logged (%s)' % ','.join(sorted(set(read['checks'])))
    status = read.get('status') or {}
    if (status.get('available') is True and 'reason' in status and status['reason'] is None
            and status.get('verification') in ('unverified', 'real')):
        state = 'available'
    elif index < 2 and status.get('available') is False and status.get('reason') in V6.PENDING:
        state = 'pending'
    else:
        return None, None, 'no explicit available signal (status %s)' % json.dumps(
            {k: V6.status_word(v) for k, v in sorted(status.items())}, sort_keys=True)
    ino, problem = judge_lines(read.get('lines') or [], mode, state == 'available', ino_expected)
    if problem:
        return None, None, problem
    problem = judge_host(read.get('host') or {}, mode, ino, state == 'available', before)
    if problem:
        return None, None, problem
    return state, ino, None


def idle_step(state, pods, pod_name, ident, generation, state_id):
    """One poll of g3_wait_idle: ('wait' | 'done' | 'fail', why)."""
    if state.get('state') != state_id or state.get('gen') != generation:
        return 'fail', 'the registry left generation %d of this state' % generation
    if state.get('pod') not in (None, ident.get('pod')):
        return 'fail', 'the registry names another Pod'
    if any(p['metadata']['name'] != pod_name for p in pods):
        return 'fail', 'another runner Pod exists'
    own = [p for p in pods if p['metadata']['name'] == pod_name]
    if own:
        now = V6.identity(own[0])
        if any(now.get(k) != ident.get(k) for k in ('pod', 'container', 'restarts')):
            return 'fail', 'the Pod UID, container id or restart count changed'
        return 'wait', None
    if state.get('phase') == 'idle_stopped' and state.get('pod') is None:
        return 'done', None
    return 'wait', None


def stops_ok(stops, generation):
    return (len(stops) == 1 and stops[0].get('gen') == generation and stops[0].get('completed') is True)


def expected_values(doc):
    """(newImage, newPlanSha256) from the G3-2 expected file, or None."""
    try:
        image, plan = doc['newImage'], doc['newPlanSha256']
        if image.startswith(NEW_PREFIX) and HEX64.fullmatch(image[len(NEW_PREFIX):]) and HEX64.fullmatch(plan):
            return image, plan
    except Exception:
        pass
    return None


# Loading --------------------------------------------------------------------------------------------------
def load_pinned(path, sha, name):
    data = open(path, 'rb').read()
    if hashlib.sha256(data).hexdigest() != sha:
        raise SystemExit('%s is not %s; nothing done' % (path, sha[:8]))
    module = types.ModuleType(name)
    module.__file__ = path
    exec(compile(data, path, 'exec'), module.__dict__)
    return module


def load_expected(want_sha):
    if not (isinstance(want_sha, str) and HEX64.fullmatch(want_sha)):
        raise SystemExit('the expected-file sha256 argument is not 64 hex characters; nothing done')
    try:
        info = os.lstat(EXPECTED_FILE)
        data = open(EXPECTED_FILE, 'rb').read()
        doc = json.loads(data)
    except Exception:
        raise SystemExit('g3-expected.json missing or unreadable; nothing done') from None
    if not stat.S_ISREG(info.st_mode) or hashlib.sha256(data).hexdigest() != want_sha:
        raise SystemExit('g3-expected.json is not the reviewed file; nothing done')
    values = expected_values(doc) if isinstance(doc, dict) else None
    if values is None:
        raise SystemExit('g3-expected.json has no valid newImage/newPlanSha256; nothing done')
    return values


# Host state (stat only; the record's sha256, never its content) ----------------------------------------
def look(path, digest=False):
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError:
        return 'error'
    kind = 'dir' if stat.S_ISDIR(info.st_mode) else 'file' if stat.S_ISREG(info.st_mode) else 'other'
    out = {'type': kind, 'mode': oct(stat.S_IMODE(info.st_mode)), 'uid': info.st_uid, 'gid': info.st_gid, 'nlink': info.st_nlink,
           'dev': info.st_dev, 'ino': info.st_ino, 'mtime_ns': info.st_mtime_ns, 'size': info.st_size}
    if kind == 'dir':
        try:
            out['entries'] = len(os.listdir(path))
        except OSError:
            out['entries'] = 'error'
    if digest and kind == 'file':
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                if os.fstat(fd).st_size > RECORD_MAX:
                    raise OSError
                out['sha256'] = hashlib.sha256(os.read(fd, RECORD_MAX + 1)).hexdigest()
            finally:
                os.close(fd)
        except OSError:
            out['sha256'] = 'error'
    return out


def host_state():
    host = {NAME: look(os.path.join(P.WORKSPACE_HOST, NAME)),
            RECORD: look(os.path.join(P.NATIVE, RECORD), digest=True),
            STAGING: look(os.path.join(P.NATIVE, STAGING))}
    for name in OTHER_NAMES:
        host[name] = look(os.path.join(P.WORKSPACE_HOST, name))
    return host


def ledgers_ok():
    try:
        return all(hashlib.sha256(open(P.LEDGERS[b][0], 'rb').read()).hexdigest() == want for b, want in LEDGERS.items())
    except Exception:
        return False


def plan_digest_now(code):
    rows = P.sql('plan', "SELECT json_build_object('plan',plan) FROM runner_bindings.bindings WHERE owner_id='%s';"
                 % P.NEW, code)
    if len(rows) != 1 or not isinstance(rows[0].get('plan'), dict):
        raise P.Stop(code, '…5805 binding lookup unreadable')
    return P.digest(rows[0]['plan'])


# Reads and the idle wait ----------------------------------------------------------------------------------
def read_once():
    pods = [p for p in P.runner_pods() if p['metadata']['name'] == P.POD_NAME]  # a lookup failure stops (rc 4)
    if len(pods) != 1:
        return {'found': False}
    status = P.runner_status()
    rc, log = P.run([P.K3S, 'kubectl', '-n', P.RUNNER_NS, 'logs', P.POD_NAME, '--tail=5000'], 60)
    return {'found': True, 'ident': V6.identity(pods[0]), 'status': status, 'logRc': rc,
            'checks': V6.policy_checks(log) if rc == 0 else [],
            'lines': placeholder_lines(log) if rc == 0 else [], 'host': host_state()}


def public_host(host):
    """The host state for out.txt: types, modes, uids, counts, ino numbers; dev and mtime as recorded numbers."""
    return {k: ('absent' if v is None else v if v == 'error' else
                {f: v.get(f) for f in ('type', 'mode', 'uid', 'nlink', 'entries', 'ino', 'size')})
            for k, v in host.items()}


def readiness(ev, pod, mode, ino_expected=None, before=None):
    """The three reads; raises Stop(4) at the first failing read. Returns (reads, ino, last host state)."""
    first = V6.identity(pod)
    started = P.stamp(first['started']) if first['started'] else None
    if started is None:
        raise P.Stop(4, 'the container start time is unknown')
    reads, ino, read = [], None, None
    for index, offset in enumerate(V6.READS):
        while time.time() < started + offset:
            if P.STOPPING['requested']:
                raise P.Stop(4, 'a stop signal was received before read %d' % (index + 1))
            time.sleep(min(1.0, started + offset - time.time()))
        read = read_once()
        state, got, problem = judge_read(read, first, index, mode, ino_expected, before)
        if got is not None:
            ino = got
        fields = {'read': index + 1, 'at': round(time.time() - started, 1), 'found': read.get('found'),
                  'restarts': (read.get('ident') or {}).get('restarts'), 'logRc': read.get('logRc'),
                  'checks': sorted(set(read.get('checks') or [])),
                  'status': {k: V6.status_word(v) for k, v in sorted((read.get('status') or {}).items())},
                  'lines': [l if l == 'unparsed' else {k: l.get(k) for k in ('event', 'phase', 'ino', 'stage', 'reason')
                                                       if k in l} for l in read.get('lines') or []],
                  'host': public_host(read.get('host') or {}), 'state': state, 'problem': problem}
        reads.append(fields)
        P.record(ev, reads=reads)
        P.say('read %d at +%.1f s: %s' % (index + 1, fields['at'], json.dumps(fields, sort_keys=True)))
        if problem:
            raise P.Stop(4, 'read %d: %s' % (index + 1, problem))
    if reads[-1]['state'] != 'available' or ino is None:
        raise P.Stop(4, 'no explicit available signal with the full line sequence at the last read')
    return reads, ino, read['host'], first


def g3_wait_idle(ev, generation, ident, lookups=None, limit=None, poll=None, clock=time.time, sleep=time.sleep):
    """Read-only: the natural idle stop of `generation` (no DELETE, no stop request). Stops at the first lookup
    error, when it happens (registry, runner Pods, Pod identity, stop rows). Pass: registry idle_stopped on this
    generation with pod null, no runner Pod, exactly one completed stop row of this generation."""
    lookups = lookups or {'registry': P.registry, 'pods': P.runner_pods, 'stops': stop_rows}
    limit = V6.IDLE_LIMIT if limit is None else limit
    poll = V6.IDLE_POLL if poll is None else poll
    deadline, polls = clock() + limit, 0

    def result(ok, why, **extra):
        value = dict({'ok': ok, 'why': why, 'polls': polls, 'generation': generation}, **extra)
        P.record(ev, idleStop=value)
        P.say('idle stop: %s' % json.dumps(value, sort_keys=True))
        return value
    while True:
        polls += 1
        try:
            state = lookups['registry']()
        except Exception:
            return result(False, 'registry lookup failed')
        try:
            pods = lookups['pods']()
        except Exception:
            return result(False, 'runner Pod lookup failed')
        try:
            step, why = idle_step(state, pods, P.POD_NAME, ident, generation, P.STATE)
        except Exception:
            return result(False, 'Pod identity lookup failed')
        if step == 'fail':
            return result(False, why)
        if step == 'done':
            break
        if clock() > deadline:
            return result(False, 'not idle_stopped within %d s' % limit)
        if P.STOPPING['requested']:
            return result(False, 'stop signal')
        sleep(poll)
    try:
        stops = lookups['stops'](generation)
    except Exception:
        return result(False, 'stop row lookup failed')
    if not stops_ok(stops, generation):
        return result(False, 'not exactly one completed stop row of generation %d' % generation,
                      stops=len(stops), completed=[s.get('completed') for s in stops])
    return result(True, None, stops=1, receipt=stops[0].get('receipt'))


def stop_rows(generation):
    return P.sql('stops', "SELECT json_build_object('gen',generation,'receipt',receipt->'version','completed',"
                 "completed_at IS NOT NULL) FROM runner_termination.stops WHERE owner_id='%s' AND state_id='%s' AND "
                 "generation=%d ORDER BY requested_at;" % (P.NEW, P.STATE, generation), 11)


# Guard, evidence, actions ---------------------------------------------------------------------------------
def g3_runs(action):
    return sorted(d for d in os.listdir(P.EVIDENCE) if re.fullmatch(r'g3-%s-\d{8}T\d{6}Z' % action, d))


def run_record(folder):
    return json.load(open(os.path.join(P.EVIDENCE, folder, 'run.json')))


def guard(action):
    """One run per action, in order: g3a, then g3b (separate PM go) only after exactly one g3a that passed."""
    if g3_runs(action):
        raise P.Stop(3, '%s already ran (%s); a second run is a PM decision' % (action, g3_runs(action)[-1]))
    before = ORDER[action]
    if before is not None:
        done = g3_runs(before)
        if len(done) != 1 or not P.completed_ok(done[0]) or run_record(done[0]).get('g3a') != 'pass':
            raise P.Stop(3, '%s needs exactly one %s that passed (rc 0, step rc 0, final invariants true, session '
                            'revoked, g3a pass)' % (action, before))


def open_evidence(action):
    ev = os.path.join(P.EVIDENCE, 'g3-%s-%s' % (action, time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())))
    os.mkdir(ev, 0o700)
    fd = os.open(os.path.join(ev, 'out.txt'), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    P.OUT['stream'] = os.fdopen(fd, 'w')
    P.record(ev, action=action, started=P.now_utc(), base=BASE_SHA, v6=V6_SHA, script=SCRIPT_SHA,
             expected=EXPECTED['sha'], image=P.RUNNER_REF)
    P.say('evidence: %s (base %s, v6 %s, script %s, expected %s)' % (ev, BASE_SHA[:8], V6_SHA[:8], SCRIPT_SHA[:8],
                                                                     EXPECTED['sha'][:8]))
    return ev


def record_reference(rec):
    """The record's reference after G3a: RECORD_FIELDS and sha256 (ctime is not compared, as in E2)."""
    return {k: rec[k] for k in RECORD_FIELDS + ('sha256',)}


def g3a_reference(g3a):
    """(ino, before) from the G3a run record: the logged ino, the recorded .codex dev/ino/mtime and the record's
    RECORD_FIELDS + sha256 (judge_host's `before`), or (None, None) when any of it is missing or malformed."""
    try:
        codex, record, ino = g3a['codex'], g3a['record'], g3a['ino']
        if type(ino) is not int or codex.get('ino') != ino or set(record) != set(RECORD_FIELDS) | {'sha256'}:
            return None, None
        if not HEX64.fullmatch(str(record['sha256'])) or not all(type(record[k]) is int for k in RECORD_FIELDS
                                                                  if k != 'mode'):
            return None, None
        return ino, {'codex': {k: codex[k] for k in CODEX_FIELDS}, 'record': dict(record)}
    except Exception:
        return None, None


def pre(mode):
    """The base precheck for this mode plus the G3 conditions. Returns (ino, before, previous Pod): all None for
    g3a; G3a's ino, recorded state and Pod UID for g3b."""
    gen = GEN[mode] - 1
    ok, state, broker = P.precheck(lambda s, pods: s['phase'] == 'idle_stopped' and s['pod'] is None
                                   and s['gen'] == gen and not pods)
    if not ok:
        raise P.Stop(3, 'precheck refused; nothing changed')
    if plan_digest_now(3) != EXPECTED['plan']:
        raise P.Stop(3, '…5805 plan digest is not the G3-2 new one; nothing changed')
    if not ledgers_ok():
        raise P.Stop(3, 'a ledger is not at its pin; nothing changed')
    host = host_state()
    P.say('host state before the entry: %s' % json.dumps(public_host(host), sort_keys=True))
    if 'error' in json.dumps(host, sort_keys=True) or any(host[n] is None for n in OTHER_NAMES):
        raise P.Stop(3, 'a host lookup failed or .git/.agents/.aws is absent; nothing changed')
    if mode == 'g3a':
        if any(host[n] is not None for n in (NAME, RECORD, STAGING)):
            raise P.Stop(3, '.codex, the record or staging exists before G3a; nothing changed')
        return None, None, None
    g3a = run_record(g3_runs('g3a')[0])
    ino, before = g3a_reference(g3a)
    if ino is None or not g3a.get('pod'):
        raise P.Stop(3, 'the G3a run record has no complete reference; nothing changed')
    if judge_host(host, 'g3b', ino, True, before) is not None:
        raise P.Stop(3, 'the host state is not G3a\'s; nothing changed')
    return ino, before, g3a['pod']


def act(mode):
    def action(ev, verdicts):
        ino_before, before, previous = pre(mode)
        P.record(ev, hostBefore=host_state())
        P.create_session(ev)
        try:
            pod = P.enter_and_wait(ev, GEN[mode], previous)
            ident = V6.identity(pod)
            P.record(ev, pod=pod['metadata']['uid'])
            reads, ino, _, _ = readiness(ev, pod, mode, ino_before, before)
        except P.Stop as stop:
            # r1 (review code r0 major 1): the session cleanup only, then the original Stop at once. No idle wait,
            # no after-state: the failure is reported as it was observed.
            verdicts['%s start' % mode] = 'not passed: %s (observed state only)' % stop.message
            P.revoke_session(ev)
            raise
        P.record(ev, ino=ino)
        verdicts['%s start' % mode] = ('explicit available signal at the third read (+%.1f s); placeholder lines '
                                       '%s with one ino; host state as expected; no refusal, no policy check '
                                       'line; same Pod/container, 0 restarts; no message POST'
                                       % (reads[-1]['at'], ' -> '.join('/'.join(p) for p in SEQUENCES[mode])))
        P.revoke_session(ev)
        idle = g3_wait_idle(ev, GEN[mode], ident)
        verdicts['%s idle stop' % mode] = ('natural idle stop observed: one completed stop row of generation %d (no '
                                           'DELETE sent)' % GEN[mode] if idle['ok'] else 'not observed (%s)' % idle['why'])
        if not idle['ok']:
            timeout = idle['why'].startswith('not idle_stopped within') or idle['why'].startswith('not exactly one')
            raise P.Stop(11 if timeout else 4, 'idle wait: %s' % idle['why'])
        after = host_state()
        P.record(ev, after=after)
        P.say('host state after the idle stop: %s' % json.dumps(public_host(after), sort_keys=True))
        problem = judge_host(after, mode, ino, True, before)
        if problem is None and after[NAME]['ino'] != ino:
            problem = '.codex ino differs from the logged ino'
        if problem:
            verdicts['%s after the idle stop' % mode] = problem
            raise P.Stop(4, 'after the idle stop: %s' % problem)
        if not ledgers_ok():
            raise P.Stop(4, 'a ledger changed (G3 runs no turn)')
        P.record(ev, **{mode: 'pass', 'codex': {k: after[NAME][k] for k in CODEX_FIELDS + ('mode', 'uid')},
                        'record': record_reference(after[RECORD])})
    return action


ACTIONS = {'g3a': act('g3a'), 'g3b': act('g3b')}


def check():
    """Read-only: the base precheck (binding image = the G2 reference), the plan digest, the ledgers, the host
    state, the G3 runs. Writes nothing."""
    ok, state, broker = P.precheck(lambda s, pods: True)
    try:
        plan = plan_digest_now(3) == EXPECTED['plan']
    except P.Stop:
        plan = None
    P.say('…5805 plan digest is the G3-2 new one: %s; ledgers at their pins: %s' % (plan, ledgers_ok()))
    P.say('host state: %s' % json.dumps(public_host(host_state()), sort_keys=True))
    P.say('G3 runs: %s' % {a: [(r, P.exit_rc(r)) for r in g3_runs(a)] for a in ORDER})
    return 0 if ok and plan else 3


def main(argv):
    global P, V6, SCRIPT_SHA
    if len(argv) != 3 or argv[1] not in ('check',) + tuple(ACTIONS):
        print('usage: g3-start.py check|g3a|g3b <sha256 of g3-expected.json>')
        return 2
    if os.geteuid() != 0:
        print('run as root')
        return 2
    SCRIPT_SHA = hashlib.sha256(open(os.path.abspath(__file__), 'rb').read()).hexdigest()
    image, plan = load_expected(argv[2])
    EXPECTED.update(sha=argv[2], image=image, plan=plan)
    P = load_pinned(BASE_PATH, BASE_SHA, 'v6pre_turns')
    V6 = load_pinned(V6_PATH, V6_SHA, 'v6_turns')
    V6.P = P
    P.RUNNER_REF = image
    P.guard, P.open_evidence, P.ACTIONS = guard, open_evidence, ACTIONS
    if argv[1] == 'check':
        try:
            return check()
        except P.Stop as stop:
            P.say('check stopped: %s' % stop.message)
            return stop.code
    return P.main(argv[:2])


if __name__ == '__main__':
    sys.exit(main(sys.argv))
