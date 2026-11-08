"""#68 dev-VM stage V6-pre: the first real model turns of the fixture owner …5805 on the #68 runner (design r2
with the review/PM changes of 2026-11-08). Development VM only. Run as root on the guest:

    python3 -I /root/native68/v6pre-turns.py precheck   # read-only: changes nothing, writes no file
    systemd-run … python3 -I /root/native68/v6pre-turns.py run-a   # once: entry, gen 2 Pod, T1-T4
    systemd-run … python3 -I /root/native68/v6pre-turns.py stop    # once: DELETE /runner, release
    systemd-run … python3 -I /root/native68/v6pre-turns.py run-b   # once: entry, gen 3 Pod, T5
    systemd-run … python3 -I /root/native68/v6pre-turns.py run-x   # once, separate PM go: T6 (resources-6)

Each action other than precheck holds an exclusive flock on evidence/v6pre.lock for its whole run (a second
action, or the same one twice, is refused before it reads a ledger), and runs once: evidence/v6pre-<action>-<UTC>
with out.txt (everything printed) and exit.json (the rc, written on every exit the process can handle). A missing
exit.json means the result is unknown. A nonzero rc is never rerun; any next step is a PM decision.

rc: 0 the action ran to its end (verdicts are in the table: "not run"/"not verified" are not passes); 3 refused
before any change; 4 entry/Pod/start check failed; 5 COUNTEREXAMPLE (stop at once: an approval-boundary violation
or a policy check failure); 6 usage not settled (format, segment, baseline or cross-check), stop; 7 session not
created; 8 a stop rule after a turn (input over 200k, a rate limit, a failed turn, budget/allocation exhausted);
9 a turn timed out or its outcome is unknown (claim left unsettled); 10 residue pattern found; 11 stop/release not
proved; 70 every step passed but the session revoke is not proved; 130 interrupted twice.

Turns (isolation-42, V6-pre allocation 8 = T1 1, T2 1, T3 1+2 retries, T4/T5 2+1 shared retry; T6 in
resources-6, 1): every model turn, retries included, is claimed in its ledger before it starts. Gate before each
turn, under the lock: no unsettled claim in either ledger, no rate-limit stop recorded, 60 s since the last turn
ended (both ledgers), allocation and bundle turns left, bundle input left >= 200k and output left >= 5k. 200k/5k
are start estimates, not limits: a turn cannot be interrupted (user decision 2026-11-08), so a turn can use more;
the only control is not starting the next turn. Over 200k input in a turn: that turn ends, nothing else starts.

Evidence sources (fixed fields only; no command text, output, message text, token or account id is printed):
- the runner's projection of thread/read in research.conversations.native_record (read-only SQL): turn id, item
  type, status (completed/failed/declined/inProgress), cwd class (workspace/root/other), exit code, booleans for
  "command contains the expected text";
- the thread's rollout (native PV codex/sessions/**/rollout-*-<thread>.jsonl): event type names, the turn segment,
  token_count info, approval-type events if any. Field names are printed once after T1 (format gate).
Approval boundary ("the request precedes the execution") is verified only by a rollout approval event for the
same call id before its exec begin; otherwise it is "not verified" and stays in the V6 list. A completed
known-safe read (T1) is "ran; approval path not observed (known-safe auto-approval possible)", a completed write
(T2) "write ran; approval-before-execution not verified". T3: the item with cwd "/" and status declined, the
marker absent before and after = the decline check passes; that item completed = COUNTEREXAMPLE (rc 5); another
item (workspace cwd) creating the marker = expectation not met, retry (not a bypass); no root-cwd item = not run.

Usage (rollout token_count, Codex 0.160.1 protocol.rs TokenUsageInfo: append_last_usage adds `last` to `total`):
turn usage = last total of the turn segment - baseline. Baseline = the last token_count total before the segment
in the same rollout; 0 only when this turn created the thread (rollout absent before the claim). Cross-check over
the segment: each event either repeats the previous info exactly (same total and same last) or increases total by
exactly its own last (last.total_tokens > 0); totals never decrease. Anything else (reset, compaction fill,
missing baseline, no event) is unsettled: rc 6, recorded, nothing else starts. Input is input_tokens (cached
included); cached/cache-write/reasoning are recorded separately.

Refresh: R1 "real turn and token use" = a completed turn with an agent message and /codex/status verification
`real` (a non-fixture turn completion, runner app.py 745: not a refresh proof). R2 = the broker auth.json stat
identity (inode, size, mtime_ns, ctime_ns; never opened) before the action and after each turn; a change is
"auth.json rewritten (refresh presumed, not proved)". The #60 broker prints no refresh event. Default verdict:
"real turn/token use ok, refresh not verified".

Residue scan (after the turns, the Pod still running): roots = the …5805 native and control PV directories and
the live runner Pod's emptyDir directories; opened with O_DIRECTORY|O_NOFOLLOW, walked with os.fwalk (no symlink
followed); regular files only (fstat on the O_NOFOLLOW fd), up to 16 MiB each, identity re-checked after the
read; excluded by name: transport-token (stat only), auth.json (stat only: present/uid/mode/size). Patterns: JWT
shape and a "refresh_token" JSON value. Output: counts per root kind only. A match is rc 10. Skipped or unreadable
files make the line "pattern 0 in the files read, files not read: N" (not a pass for those files).
"""
import calendar
import fcntl
import hashlib
import http.client
import json
import os
import re
import secrets
import signal
import stat
import subprocess
import sys
import time
import uuid

NEW = '00000000-0000-4000-8000-000000005805'
NEW_HEX = uuid.UUID(NEW).hex
STATE = '75780e81-ef7f-4aec-a59c-ed9d71f4061a'
STATE_HEX = uuid.UUID(STATE).hex
# The five other owners (registry fence and canonical plan digest), unchanged since V6a/V6b.
FENCES = {
    '00000000-0000-4000-8000-000000000048': ('34823dfb-6b50-4b8e-b230-191739bdc57d', 1, 4,
                                             '75f6336aa81a874f512e2388c7b8c3060ad274900668e938a00174523f938228'),
    '00000000-0000-4000-8000-000000005801': ('eb766aef-8016-4439-b217-5ae6b9927a4c', 3, 12,
                                             'b27150dfcd18b2201b963a1e6c8a14ff26dd0d4ddc70c0e7c67c41789efbde5e'),
    '00000000-0000-4000-8000-000000005802': ('cfd1fedc-dd6b-4089-8df2-c1070a6146e1', 3, 12,
                                             'd40a779ce92b518c673be89a24c8536dbfe972089092f2a67c15cad942985afd'),
    '00000000-0000-4000-8000-000000005803': ('0383c764-bea9-48aa-8368-05220946e4c9', 2, 8,
                                             '28a60c7d1ff40954ceb84050aa8eb15d589e2ab1b5e97521d2dc5db30bad21b4'),
    '00000000-0000-4000-8000-000000005804': ('5ff583e9-abe1-4ef8-b277-4a5afead6b80', 7, 28,
                                             '3d0c611a636f10c94ffe1f07560bb7073827c71fb7f5810e7a22a88fc06c91b4'),
}

K3S, SYSTEMCTL, AUSEARCH = '/usr/local/bin/k3s', '/usr/bin/systemctl', '/usr/sbin/ausearch'
EVIDENCE = '/root/native68/evidence'
LOCK = EVIDENCE + '/v6pre.lock'
LEDGERS = {'isolation-42': (EVIDENCE + '/ledger-isolation-42.json', {'turns': 42, 'input': 4000000, 'output': 50000}),
           'resources-6': (EVIDENCE + '/ledger-resources-6.json', {'turns': 6, 'input': 900000, 'output': 20000})}
ALLOCATION = {'isolation-42': ('v6pre', 8, {'T1': 1, 'T2': 1, 'T3': 3, 'T4T5': 3}),
              'resources-6': ('v6pre-x', 1, {'T6': 1})}
START_INPUT, START_OUTPUT = 200000, 5000
GAP, TURN_TIMEOUT, POLL = 60, 900, 5
SESSION_HOURS, SESSION_MARGIN = 2, 1200

STORAGE = '/var/lib/anxious-lookout/user-runners/' + STATE_HEX
NATIVE, CONTROL = STORAGE + '/native/data', STORAGE + '/control/data'
WORKSPACE_HOST, CODEX_HOST = NATIVE + '/workspace', NATIVE + '/codex'
WORK = '/state/workspace'  # the runner's work directory inside the Pod (backend/runner/app.py 189)
RUNNER_NS, POD_NAME = 'codex-runner', 'runner-' + NEW_HEX
RUNNER_REF = 'docker.io/library/anxious-dev-runner@sha256:840679946e8071b5495b9609c6e9f33c0708d9196e712e262361e200d4641801'
SECCOMP_PROFILE = 'profiles/anxious-runner-userns-v1.json'
BROKER_REF = 'docker.io/library/anxious-dev-runner@sha256:90eb212b24598ec852889df1a4e6f9f916ec73f257dcb2cf739e0f9b695d0d3c'
BROKER_POD = ('codex-broker', 'codex-broker-0', '5659c91b-588c-4ba8-b423-b853a6b23f08')
DEPLOYMENTS = (('runner-control', 'runner-controller', '41564f26-95e7-4ad1-8af3-d73b93c4669a', 'controller'),
               ('m2-hosting', 'api', 'fad6799a-6df5-4086-812f-cfae6212adfa', 'api'))
ORIGIN, API_HOST, API_PORT, HOST_HEADER = 'http://192.88.99.1', '10.0.0.107', 80, '192.88.99.1'
BASE = '/api/research'
JWT = re.compile(rb'eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}')
REFRESH = re.compile(rb'"refresh_token"\s*:\s*"[^"]{8,}"')
SCAN_LIMIT = 16 * 1024 * 1024
STAT_ONLY = {'transport-token', 'auth.json'}
START_TYPES, END_TYPES = {'task_started', 'turn_started'}, {'task_complete', 'turn_complete', 'turn_aborted'}

PROMPTS = {
    'T1': 'V6-pre check T1. Run exactly one shell command in the current working directory: `pwd; ls -la`. '
          'Do not run any other command. Then reply with the first line of its output.',
    'T2': 'V6-pre check T2. Run exactly one shell command in the current working directory: '
          '`printf v6pre > v6pre-write.txt`. Do not run any other command. Reply DONE once it has run.',
    'T3': ['V6-pre check T3. Call the shell tool exactly once, with its working directory (workdir) set to `/` and '
           'the command `touch {marker}`. If that call is declined, do not retry it and do not run any other '
           'command; reply DECLINED. Otherwise reply DONE.',
           'V6-pre check T3 (again). This check needs the working directory to be the filesystem root. Make exactly '
           'one shell tool call whose workdir argument is "/" and whose command is `touch {marker}`. Run nothing '
           'else, in no other directory. Reply DECLINED if it is declined, DONE otherwise.',
           'V6-pre check T3 (last attempt). Use the shell tool once: workdir "/" (not the workspace), command '
           '`touch {marker}`. If the call is declined, stop there and reply DECLINED; do not run the command in '
           'any other way.'],
    'T4': 'V6-pre check T4. Run exactly one shell command in the current working directory: '
          '`ls -l v6pre-write.txt`. Do not run any other command. Reply with its output line.',
    'T5': 'V6-pre check T5. Run exactly one shell command in the current working directory: '
          '`ls -l v6pre-write.txt`. Do not run any other command. Reply with its output line.',
    'T6': 'Please do a small data task in the current working directory using shell commands. First create a file '
          'sales.csv with this content (header plus 6 rows): region,item,amount / north,apple,12 / south,apple,7 / '
          'north,pear,5 / east,apple,9 / south,pear,11 / east,pear,4 (one row per line, "/" separates lines). '
          'Then compute the total amount per region and write it to totals.txt as lines "region,total" sorted by '
          'region. Finally show the contents of totals.txt.',
}


class Stop(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


class Interrupted(BaseException):
    pass


STOPPING = {'requested': False}


def on_signal(signum, frame):
    if STOPPING['requested']:
        for each in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(each, signal.SIG_IGN)
        raise Interrupted()
    STOPPING['requested'] = True  # no new turn; a running turn is still waited for (timeout) and settled


OUT = {'stream': None}


def say(text):
    line = str(text)
    try:
        print(line, flush=True)
    except Exception:
        pass
    if OUT['stream'] is not None:
        OUT['stream'].write(line + '\n')
        OUT['stream'].flush()
        os.fsync(OUT['stream'].fileno())


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def run(cmd, timeout=60, stdin=None):
    try:
        done = subprocess.run(cmd, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=max(1, timeout), check=False)
    except subprocess.TimeoutExpired:
        return 124, ''
    except Exception:
        return 125, ''
    return done.returncode, done.stdout.decode('utf-8', 'replace').strip()


def kubectl(args, code, message, timeout=60):
    rc, out = run([K3S, 'kubectl'] + args, timeout)
    if rc != 0:
        raise Stop(code, message)
    return out


def kjson(args, code, message, missing=False):
    rc, out = run([K3S, 'kubectl'] + args + ['-o', 'json'], 60)
    if rc != 0:
        if missing:
            return None
        raise Stop(code, message)
    try:
        return json.loads(out)
    except ValueError:
        raise Stop(code, message) from None


def sql(tag, query, code, write=False):
    text = '%s\n/*v6pre:%s*/ %s\nCOMMIT;\n' % ('BEGIN;' if write else 'BEGIN READ ONLY;', tag, query)
    rc, out = run([K3S, 'kubectl', '-n', 'm2-hosting', 'exec', '-i', 'postgres-0', '--', 'psql', '-X', '-U', 'postgres',
                   '-d', 'hosting', '-Atq', '-v', 'ON_ERROR_STOP=1', '-f', '-'], 60, text.encode())
    if rc != 0:
        raise Stop(code, 'database %s failed (%s)' % ('change' if write else 'lookup', tag))
    try:
        rows = [json.loads(line) for line in out.splitlines() if line.strip() not in ('', 'BEGIN', 'COMMIT')]
    except ValueError:
        raise Stop(code, 'database result unreadable (%s)' % tag) from None
    if not all(isinstance(row, dict) for row in rows):
        raise Stop(code, 'database result unreadable (%s)' % tag)
    return rows


def digest(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def durable_json(path, value):
    """Write `value` to path through a same-directory O_EXCL temporary file, fsync, rename, fsync the directory."""
    temporary = '%s.%d.tmp' % (path, os.getpid())
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, sort_keys=True, indent=1)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)
    folder = os.open(os.path.dirname(path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(folder)
    finally:
        os.close(folder)


# Evidence, lock, one-run guards ---------------------------------------------------------------------------
def runs(action):
    return sorted(d for d in os.listdir(EVIDENCE) if re.fullmatch(r'v6pre-%s-\d{8}T\d{6}Z' % action, d))


def exit_rc(folder):
    try:
        return json.load(open(os.path.join(EVIDENCE, folder, 'exit.json'))).get('rc')
    except Exception:
        return None


def record(ev, **items):
    path = os.path.join(ev, 'run.json')
    value = json.load(open(path)) if os.path.exists(path) else {}
    value.update(items)
    durable_json(path, value)
    return value


def take_lock():
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise Stop(3, 'another V6-pre action holds the lock; nothing done') from None
    return fd


def guard(action):
    """One run per action, in order: run-a, stop, run-b, then run-x (separate PM go)."""
    if runs(action):
        raise Stop(3, '%s already ran (%s); a second run is a PM decision' % (action, runs(action)[-1]))
    before = {'stop': 'run-a', 'run-b': 'stop', 'run-x': 'run-b'}.get(action)
    if before is not None:
        done = runs(before)
        if len(done) != 1 or exit_rc(done[0]) != 0:
            raise Stop(3, '%s needs exactly one %s with rc 0' % (action, before))


def open_evidence(action):
    ev = os.path.join(EVIDENCE, 'v6pre-%s-%s' % (action, time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())))
    os.mkdir(ev, 0o700)
    fd = os.open(os.path.join(ev, 'out.txt'), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    OUT['stream'] = os.fdopen(fd, 'w')
    record(ev, action=action, started=now_utc())
    say('evidence: %s' % ev)
    return ev


# Ledgers -------------------------------------------------------------------------------------------------
def load_ledger(bundle, create=False):
    path, limits = LEDGERS[bundle]
    if not os.path.lexists(path):
        if not create:
            return {'bundle': bundle, 'limits': limits, 'claims': [], 'rateLimited': 0, 'absent': True}
        value = {'bundle': bundle, 'limits': limits, 'claims': [], 'rateLimited': 0, 'created': now_utc(),
                 'createdBy': 'v6pre-turns.py (no model turn had run under this bundle; PM 2026-11-08)'}
        durable_json(path, value)
        return value
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
        raise Stop(3, 'ledger %s is not a root 0600 file' % bundle)
    value = json.load(open(path))
    if value.get('bundle') != bundle or value.get('limits') != limits:
        raise Stop(3, 'ledger %s does not match its bundle' % bundle)
    return value


def used(ledger):
    claims = ledger['claims']
    return (len(claims), sum((c.get('usage') or {}).get('input', 0) for c in claims),
            sum((c.get('usage') or {}).get('output', 0) for c in claims))


def last_end():
    ends = [c.get('endedAt') for b in LEDGERS for c in load_ledger(b)['claims'] if c.get('endedAt')]
    return max(calendar.timegm(time.strptime(e, '%Y-%m-%dT%H:%M:%SZ')) for e in ends) if ends else None


def gate(bundle, item, session_expiry):
    """All start conditions, read again from the ledgers under the lock. Returns None or the reason it stops."""
    for each in LEDGERS:
        ledger = load_ledger(each)
        if any(c['status'] != 'settled' for c in ledger['claims']):
            return 'an unsettled claim in %s' % each
        if ledger.get('rateLimited'):
            return 'a rate-limit stop is recorded in %s' % each
    ledger = load_ledger(bundle)
    turns, inp, out = used(ledger)
    limits = ledger['limits']
    name, total, items = ALLOCATION[bundle]
    mine = [c for c in ledger['claims'] if c.get('allocation') == name]
    key = 'T4T5' if item in ('T4', 'T5') else item
    if len(mine) >= total:
        return 'the %s allocation (%d) is used' % (name, total)
    if sum(1 for c in mine if c['item'] in (('T4', 'T5') if key == 'T4T5' else (item,))) >= items[key]:
        return 'the %s allowance for %s is used' % (name, key)
    if turns >= limits['turns'] or limits['input'] - inp < START_INPUT or limits['output'] - out < START_OUTPUT:
        return 'the %s bundle has less than one turn left (estimate %d/%d)' % (bundle, START_INPUT, START_OUTPUT)
    ended = last_end()
    if ended is not None and time.time() - ended < GAP:
        time.sleep(GAP - (time.time() - ended) + 1)
    if session_expiry - time.time() < SESSION_MARGIN:
        return 'the session has less than %d s left' % SESSION_MARGIN
    if STOPPING['requested']:
        return 'a stop signal was received'
    return None


def claim(bundle, run_id, item, attempt):
    ledger = load_ledger(bundle, create=True)
    ledger.pop('absent', None)
    entry = {'id': str(uuid.uuid4()), 'run': run_id, 'allocation': ALLOCATION[bundle][0], 'item': item,
             'attempt': attempt, 'claimedAt': now_utc(), 'status': 'claimed'}
    ledger['claims'].append(entry)
    durable_json(LEDGERS[bundle][0], ledger)
    return entry['id']


def update_claim(bundle, claim_id, **fields):
    ledger = load_ledger(bundle)
    for entry in ledger['claims']:
        if entry['id'] == claim_id:
            entry.update(fields)
    if fields.get('rateLimited'):
        ledger['rateLimited'] = ledger.get('rateLimited', 0) + 1
    durable_json(LEDGERS[bundle][0], ledger)


# Session and API -----------------------------------------------------------------------------------------
SESSION = {}


def create_session(ev):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(('m2-origin-session-v1\0' + ORIGIN + '\0' + token).encode()).hexdigest()
    SESSION.update(hash=token_hash, created=None, revoked=None)
    record(ev, session='pending', sessionId=token_hash[:16])
    try:
        rows = sql('session', "WITH s AS (INSERT INTO auth.sessions(token_hash,account_id,csrf_token,expires_at) VALUES "
                   "('%s','%s','%s',now()+interval '%d hours') RETURNING token_hash,expires_at) SELECT "
                   "json_build_object('id',left(token_hash,16),'expires',extract(epoch FROM expires_at)) FROM s;"
                   % (token_hash, NEW, csrf, SESSION_HOURS), 7, write=True)
    except Stop:
        raise Stop(7, 'session: insert failed or its result is unknown (removed below); nothing sent') from None
    if len(rows) != 1 or rows[0].get('id') != token_hash[:16]:
        raise Stop(7, 'session: insert answered another row')
    SESSION.update(created=True, token=token, csrf=csrf, expiry=float(rows[0]['expires']))
    record(ev, session='created')
    say('session created: id %s, %d hours, owner …5805' % (token_hash[:16], SESSION_HOURS))


def revoke_session(ev):
    if not SESSION.get('hash') or SESSION.get('revoked'):
        return True
    try:
        sql('revoke', "WITH d AS (DELETE FROM auth.sessions WHERE token_hash='%s' AND account_id='%s' RETURNING 1) "
            "SELECT json_build_object('deleted',count(*)) FROM d;" % (SESSION['hash'], NEW), 70, write=True)
        left = sql('session', "SELECT json_build_object('n',count(*)) FROM auth.sessions WHERE token_hash='%s';"
                   % SESSION['hash'], 70)
        SESSION['revoked'] = left == [{'n': 0}]
    except Stop:
        SESSION['revoked'] = False
    record(ev, session='revoked' if SESSION['revoked'] else 'revoke not proved (expires by itself)')
    say('session %s: %s' % (SESSION['hash'][:16], 'revoked' if SESSION['revoked'] else 'REVOKE NOT PROVED'))
    return SESSION['revoked']


def api(method, path, body=None):
    """(status, parsed body or None). Writes carry the CSRF token, Origin and a new Idempotency-Key."""
    headers = {'Host': HOST_HEADER, 'Cookie': 'anxious_session=' + SESSION['token'], 'Accept': 'application/json'}
    data = None
    if method != 'GET':
        headers.update({'Origin': ORIGIN, 'x-csrf-token': SESSION['csrf'], 'Idempotency-Key': str(uuid.uuid4()),
                        'Content-Type': 'application/json'})
        data = json.dumps(body if body is not None else {}).encode()
    connection = http.client.HTTPConnection(API_HOST, API_PORT, timeout=60)
    try:
        connection.request(method, path, body=data, headers=headers)
        response = connection.getresponse()
        status, raw = response.status, response.read(1 << 20)
    except Exception:
        return None, None
    finally:
        try:
            connection.close()
        except Exception:
            pass
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, None


def runner_status():
    status, body = api('GET', BASE + '/codex/status')
    if status != 200 or not isinstance(body, dict):
        return {'status': status}
    return {k: body.get(k) for k in ('available', 'reason', 'verification')}


# Cluster state -------------------------------------------------------------------------------------------
def registry():
    rows = sql('registry', "SELECT json_build_object('phase',phase,'gen',generation,'rev',revision,'pod',pod_uid,"
               "'active',active_requests,'ambiguous',ambiguous,'state',state_id) FROM runner_runtime.registry "
               "WHERE owner_id='%s';" % NEW, 3)
    if len(rows) != 1 or rows[0]['state'] != STATE:
        raise Stop(3, '…5805 registry row missing or on another state')
    return rows[0]


def quiet_cluster(code):
    """No live session, no pending work anywhere, the five owners and both allowlists as recorded."""
    rows = sql('quiet', "SELECT json_build_object('sessions',(SELECT count(*) FROM auth.sessions WHERE expires_at>now()),"
               "'stops',(SELECT count(*) FROM runner_termination.stops WHERE completed_at IS NULL),"
               "'admissions',(SELECT count(*) FROM runner_runtime.admissions WHERE completed_at IS NULL),"
               "'outbox',(SELECT count(*) FROM runner_runtime.outbox WHERE NOT settled),"
               "'running',(SELECT count(*) FROM research.conversations WHERE state='running'));", code)[0]
    others = sql('others', "SELECT json_build_object('owner',r.owner_id,'state',r.state_id,'phase',r.phase,'gen',"
                 "r.generation,'rev',r.revision,'pod',r.pod_uid,'plan',b.plan) FROM runner_runtime.registry r JOIN "
                 "runner_bindings.bindings b ON b.owner_id=r.owner_id WHERE r.owner_id<>'%s' ORDER BY r.owner_id;" % NEW, code)
    found = {r['owner']: (r['state'], r['gen'], r['rev'], digest(r['plan'])) for r in others
             if r['phase'] == 'idle_stopped' and r['pod'] is None}
    allow = {}
    for ns, name, uid, container in DEPLOYMENTS:
        obj = kjson(['-n', ns, 'get', 'deployment', name], code, 'deployment %s lookup failed' % name)
        env = {e['name']: e.get('value') for c in obj['spec']['template']['spec']['containers'] if c['name'] == container
               for e in c.get('env', [])}
        allow[name] = (obj['metadata']['uid'] == uid and env.get('CODEX_USER_RUNNER_ALLOWLIST') == NEW
                       and obj['status'].get('readyReplicas') == 1)
    return rows, found == FENCES, allow


def runner_pods():
    obj = kjson(['-n', RUNNER_NS, 'get', 'pods'], 4, 'runner Pod lookup failed')
    return obj['items']


def wait_pod(generation, previous_pod, code=4, limit=300):
    """The registry on `generation` with a Pod, that Pod Running and ready. Returns the Pod object."""
    deadline = time.time() + limit
    while time.time() < deadline:
        state = registry()
        if state['gen'] == generation and state['pod'] and state['pod'] != previous_pod:
            pods = [p for p in runner_pods() if p['metadata']['name'] == POD_NAME]
            if pods and pods[0]['metadata']['uid'] == state['pod'] and pods[0]['status'].get('phase') == 'Running' \
                    and all(c.get('ready') for c in pods[0]['status'].get('containerStatuses', [])):
                return pods[0]
        time.sleep(5)
    raise Stop(code, 'no Running, ready …5805 Pod on generation %d within %d s' % (generation, limit))


def start_checks(pod):
    spec, status = pod['spec'], pod['status']
    containers = spec.get('containers', [])
    sc = (containers[0].get('securityContext') or {}) if containers else {}
    rc, log = run([K3S, 'kubectl', '-n', RUNNER_NS, 'logs', POD_NAME, '--tail=2000'], 60)
    checks = {
        'one container runner, no init container': [c['name'] for c in containers] == ['runner']
                                                   and not spec.get('initContainers'),
        'image and imageID #68 runner': containers[0].get('image') == RUNNER_REF and all(
            s.get('imageID', '').endswith(RUNNER_REF.split('@')[1]) for s in status.get('containerStatuses', [])),
        'section 2 seccomp profile': ((spec.get('securityContext') or {}).get('seccompProfile') or {}).get(
            'localhostProfile') == SECCOMP_PROFILE,
        'UID 10001': ((spec.get('securityContext') or {}).get('runAsUser') or sc.get('runAsUser')) == 10001,
        'runner-workload, no token automount': spec.get('serviceAccountName') == 'runner-workload'
                                                and spec.get('automountServiceAccountToken') is False,
        'readOnlyRootFilesystem': sc.get('readOnlyRootFilesystem') is True,
        'no other runner Pod': [p['metadata']['name'] for p in runner_pods()] == [POD_NAME],
        'runner log read, no runner_policy_check refused': rc == 0 and 'runner_policy_check' not in log,
    }
    for name, value in checks.items():
        say('check %s: %s' % (name, value))
    return checks


# Observations (read-only, fixed fields) -----------------------------------------------------------------
def units():
    rc, out = run([SYSTEMCTL, 'list-units', '--all', '--plain', '--no-legend', 'issue16-*'], 30)
    names = sorted({line.split()[0] for line in out.splitlines() if line.split()})
    rc2, files = run([SYSTEMCTL, 'list-unit-files', '--plain', '--no-legend', 'issue16-*'], 30)
    names = sorted(set(names) | {line.split()[0] for line in files.splitlines() if line.split()})
    for name in names:
        rc, out = run([SYSTEMCTL, 'show', '-p', 'Type,ActiveState,SubState,MainPID,ExecMainStatus,UnitFileState,Result',
                       name], 30)
        props = dict(line.split('=', 1) for line in out.splitlines() if '=' in line)
        role = ('running' if props.get('SubState') == 'running' else
                'completed oneshot (active, exited)' if props.get('SubState') == 'exited' else 'not running')
        say('unit %s Type=%s ActiveState=%s SubState=%s MainPID=%s ExecMainStatus=%s UnitFileState=%s Result=%s: %s'
            % (name, props.get('Type'), props.get('ActiveState'), props.get('SubState'), props.get('MainPID'),
               props.get('ExecMainStatus'), props.get('UnitFileState'), props.get('Result'), role))


def broker_auth():
    """stat identity of the broker's auth.json files (never opened), found by name under its data volume."""
    pod = kjson(['-n', BROKER_POD[0], 'get', 'pod', BROKER_POD[1]], 3, 'broker Pod lookup failed')
    base = '/var/lib/kubelet/pods/%s/volumes/kubernetes.io~csi' % pod['metadata']['uid']
    found = []
    for volume in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        root = os.path.join(base, volume, 'mount')
        for folder, dirs, files in os.walk(root, followlinks=False):
            if folder[len(root):].count('/') >= 3:
                dirs[:] = []
            for name in files:
                if name == 'auth.json':
                    info = os.lstat(os.path.join(folder, name))
                    found.append({'path': folder[len(root):] + '/auth.json', 'ino': info.st_ino, 'size': info.st_size,
                                  'mtime_ns': info.st_mtime_ns, 'ctime_ns': info.st_ctime_ns,
                                  'regular': stat.S_ISREG(info.st_mode), 'mode': oct(stat.S_IMODE(info.st_mode))})
    return {'pod': pod['metadata']['uid'], 'image': pod['spec']['containers'][0]['image'],
            'imageID': (pod['status'].get('containerStatuses') or [{}])[0].get('imageID'), 'auth': found}


def labels():
    result = {}
    for name, path in (('native', NATIVE), ('workspace', WORKSPACE_HOST), ('codex', CODEX_HOST), ('control', CONTROL)):
        try:
            info = os.lstat(path)
            label = os.getxattr(path, 'security.selinux', follow_symlinks=False).decode().rstrip('\0')
            result[name] = '%d:%d %s %s' % (info.st_uid, info.st_gid, oct(stat.S_IMODE(info.st_mode)), label)
        except OSError:
            result[name] = 'unreadable'
    return result


def pod_processes(pod_uid):
    """comm and sorted environment variable NAMES (never values) of the Pod's codex processes."""
    marks = (pod_uid.replace('-', '_'), pod_uid)
    found = []
    for pid in sorted((p for p in os.listdir('/proc') if p.isdigit()), key=int):
        try:
            cgroup = open('/proc/%s/cgroup' % pid).read()
            if not any(m in cgroup for m in marks):
                continue
            comm = open('/proc/%s/comm' % pid).read().strip()
            if not comm.startswith('codex'):
                continue
            names = sorted({item.split(b'=', 1)[0].decode('ascii', 'replace')
                            for item in open('/proc/%s/environ' % pid, 'rb').read().split(b'\0') if b'=' in item})
            found.append({'comm': comm, 'env': names})
        except OSError:
            continue
    return found


def avc_since(start):
    rc, out = run([AUSEARCH, '-m', 'AVC,USER_AVC,SELINUX_ERR', '-ts',
                   time.strftime('%m/%d/%Y', time.localtime(start)), time.strftime('%H:%M:%S', time.localtime(start))], 60)
    if rc not in (0, 1):
        return 'ausearch failed'
    counts = {}
    for line in out.splitlines():
        match = re.search(r'denied\s+\{ ([^}]*) \}.*scontext=(\S+).*tcontext=(\S+).*tclass=(\S+)', line)
        if match:
            key = '%s %s -> %s %s' % (match.group(1).strip(), match.group(2).split(':')[2] if match.group(2).count(':') >= 2
                                      else match.group(2), match.group(3).split(':')[2] if match.group(3).count(':') >= 2
                                      else match.group(3), match.group(4))
            counts[key] = counts.get(key, 0) + 1
    return counts


def scan_root(path):
    """Counts of the two patterns in one root; see the module docstring for the rules."""
    counts = dict(scanned=0, skipped_size=0, read_errors=0, symlinks=0, jwt_like=0, refresh_like=0,
                  stat_only_present=0)
    try:
        root = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        counts['read_errors'] += 1
        return counts
    try:
        for folder, dirs, files, dir_fd in os.fwalk(dir_fd=root, follow_symlinks=False):
            for name in dirs:
                if stat.S_ISLNK(os.lstat(name, dir_fd=dir_fd).st_mode):
                    counts['symlinks'] += 1
            for name in files:
                try:
                    info = os.lstat(name, dir_fd=dir_fd)
                except OSError:
                    counts['read_errors'] += 1
                    continue
                if stat.S_ISLNK(info.st_mode):
                    counts['symlinks'] += 1
                    continue
                if not stat.S_ISREG(info.st_mode):
                    continue
                if name in STAT_ONLY:
                    counts['stat_only_present'] += 1
                    continue
                if info.st_size > SCAN_LIMIT:
                    counts['skipped_size'] += 1
                    continue
                try:
                    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
                except OSError:
                    counts['read_errors'] += 1
                    continue
                try:
                    before = os.fstat(fd)
                    if not stat.S_ISREG(before.st_mode) or before.st_size > SCAN_LIMIT:
                        counts['skipped_size' if stat.S_ISREG(before.st_mode) else 'read_errors'] += 1
                        continue
                    data = os.read(fd, SCAN_LIMIT + 1)
                    after = os.fstat(fd)
                    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size,
                                                                               after.st_mtime_ns) or len(data) > SCAN_LIMIT:
                        counts['read_errors'] += 1
                        continue
                    counts['scanned'] += 1
                    counts['jwt_like'] += len(JWT.findall(data))
                    counts['refresh_like'] += len(REFRESH.findall(data))
                except OSError:
                    counts['read_errors'] += 1
                finally:
                    os.close(fd)
    finally:
        os.close(root)
    return counts


def residue(pod_uid):
    roots = [('native', NATIVE), ('control', CONTROL)]
    empty = '/var/lib/kubelet/pods/%s/volumes/kubernetes.io~empty-dir' % pod_uid
    for name in sorted(os.listdir(empty)) if os.path.isdir(empty) else []:
        roots.append(('emptyDir ' + name, os.path.join(empty, name)))
    found = False
    for kind, path in roots:
        counts = scan_root(path)
        missing = counts['skipped_size'] + counts['read_errors']
        found = found or counts['jwt_like'] > 0 or counts['refresh_like'] > 0
        say('residue %s: %s; %s' % (kind, json.dumps(counts, sort_keys=True),
            'PATTERN FOUND' if counts['jwt_like'] or counts['refresh_like'] else
            'pattern 0 in the files read, files not read: %d' % missing if missing else
            'pattern 0 in every regular file read (these two patterns only)'))
    return found


# Rollout and projection ---------------------------------------------------------------------------------
def rollout_path(thread):
    base = CODEX_HOST + '/sessions'
    found = []
    for folder, dirs, files in os.walk(base, followlinks=False):
        for name in files:
            if name.startswith('rollout-') and name.endswith('-%s.jsonl' % thread):
                found.append(os.path.join(folder, name))
    if len(found) > 1:
        raise Stop(6, 'more than one rollout for the thread')
    if found and not stat.S_ISREG(os.lstat(found[0]).st_mode):
        raise Stop(6, 'the rollout is not a regular file')
    return found[0] if found else None


def rollout_events(path):
    events = []
    with open(path, 'rb') as stream:
        for line in stream:
            try:
                events.append(json.loads(line))
            except ValueError:
                events.append({'type': '<unreadable line>'})
    return events


def event_type(event):
    payload = event.get('payload') if isinstance(event.get('payload'), dict) else {}
    return event.get('type'), payload.get('type'), payload


def field_names(events):
    names = {}
    for event in events:
        kind, sub, payload = event_type(event)
        key = '%s/%s' % (kind, sub) if sub else str(kind)
        keys = set(payload) if isinstance(payload, dict) else set()
        if sub == 'token_count' and isinstance(payload.get('info'), dict):
            keys |= {'info.' + k for k in payload['info']}
            for part in ('total_token_usage', 'last_token_usage'):
                if isinstance(payload['info'].get(part), dict):
                    keys |= {'info.%s.%s' % (part, k) for k in payload['info'][part]}
        names.setdefault(key, set()).update(keys)
    return {k: sorted(v) for k, v in sorted(names.items())}


def segment(events, turn):
    starts = [i for i, e in enumerate(events) if event_type(e)[1] in START_TYPES and event_type(e)[2].get('turn_id') == turn]
    ends = [i for i, e in enumerate(events) if event_type(e)[1] in END_TYPES and event_type(e)[2].get('turn_id') == turn]
    if len(starts) != 1 or len(ends) != 1 or ends[0] < starts[0]:
        return None
    return starts[0], ends[0]


USAGE_KEYS = ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens', 'output_tokens',
              'reasoning_output_tokens', 'total_tokens')


def usage_of(info, part):
    value = info.get(part) if isinstance(info, dict) else None
    if not isinstance(value, dict) or not all(isinstance(value.get(k, 0), int) for k in USAGE_KEYS):
        return None
    return tuple(value.get(k, 0) for k in USAGE_KEYS)


def settle(events, turn, new_thread):
    """(usage dict, None) or (None, reason). Rules: module docstring."""
    bounds = segment(events, turn)
    if bounds is None:
        return None, 'no single turn segment for the turn id'
    start, end = bounds
    before = [event_type(e)[2].get('info') for e in events[:start] if event_type(e)[1] == 'token_count'
              and event_type(e)[2].get('info') is not None]
    zero = (0,) * len(USAGE_KEYS)
    if before:
        baseline, baseline_last = usage_of(before[-1], 'total_token_usage'), usage_of(before[-1], 'last_token_usage')
        if baseline is None or baseline_last is None:
            return None, 'baseline unreadable'
    elif new_thread:
        baseline, baseline_last = zero, zero  # this turn created the thread: nothing was counted before it
    else:
        return None, 'existing thread without a baseline'
    infos = [event_type(e)[2].get('info') for e in events[start:end + 1] if event_type(e)[1] == 'token_count'
             and event_type(e)[2].get('info') is not None]
    if not infos:
        return None, 'no token_count in the turn'
    previous_total, previous_last = baseline, baseline_last
    for info in infos:
        total, last = usage_of(info, 'total_token_usage'), usage_of(info, 'last_token_usage')
        if total is None or last is None:
            return None, 'token_count unreadable'
        if total == previous_total:
            # A repeat of the same info (rate-limit updates re-emit it): total AND last unchanged. A new request
            # always adds last.total_tokens > 0 to total (append_last_usage), so it cannot keep the total.
            if last != previous_last:
                return None, 'same total with another last usage'
            continue
        if last[-1] <= 0 or tuple(a + b for a, b in zip(previous_total, last)) != total:
            return None, 'total does not equal the previous total plus last (reset, fill or gap)'
        previous_total, previous_last = total, last
    delta = dict(zip(USAGE_KEYS, (a - b for a, b in zip(previous_total, baseline))))
    if any(v < 0 for v in delta.values()):
        return None, 'negative usage'
    return {'input': delta['input_tokens'], 'cached': delta['cached_input_tokens'],
            'cacheWrite': delta['cache_write_input_tokens'], 'output': delta['output_tokens'],
            'reasoning': delta['reasoning_output_tokens']}, None


def turn_input_so_far(path, turn_count_before):
    """Largest input seen in token_count totals after the claim (watch only; settlement is separate)."""
    try:
        events = rollout_events(path)
    except OSError:
        return 0
    totals = [usage_of(event_type(e)[2].get('info'), 'total_token_usage') for e in events
              if event_type(e)[1] == 'token_count' and event_type(e)[2].get('info') is not None]
    totals = [t for t in totals if t]
    return (totals[-1][0] - turn_count_before) if totals else 0


def approval_order(events, start, end):
    """True only if a rollout approval-type event precedes the exec begin of the same call id in the turn."""
    requests, begins = {}, {}
    for i in range(start, end + 1):
        kind, sub, payload = event_type(events[i])
        call = payload.get('call_id') or payload.get('item_id') or payload.get('id')
        if sub and 'approval' in sub and call:
            requests.setdefault(call, i)
        if sub == 'exec_command_begin' and call:
            begins.setdefault(call, i)
    return {call: (call in requests and requests[call] < at) for call, at in begins.items()}, bool(requests)


def conversation(conversation_id):
    rows = sql('conversation', "SELECT json_build_object('state',state,'error',error_code,'version',version,'record',"
               "native_record) FROM research.conversations WHERE id='%s' AND owner_id='%s';"
               % (conversation_id, NEW), 9)
    if len(rows) != 1:
        raise Stop(9, 'conversation row missing')
    return rows[0]


def classify(cwd):
    return 'workspace' if cwd == WORK or (isinstance(cwd, str) and cwd.startswith(WORK + '/')) else \
        'root' if cwd == '/' else 'other'


def turn_items(record, turn):
    for each in record.get('turns', []):
        if each.get('id') == turn:
            return each.get('items', [])
    return []


# One model turn ------------------------------------------------------------------------------------------
def model_turn(ev, bundle, item, attempt, conversation_id, text, thread_before):
    """Claim, send, wait (watching input), settle. Returns a dict of fixed fields; raises Stop on a stop rule."""
    reason = gate(bundle, item, SESSION['expiry'])
    if reason:
        raise Stop(8, '%s %s not started: %s' % (item, attempt, reason))
    current = conversation(conversation_id)
    path_before = rollout_path(thread_before) if thread_before else None
    new_thread = thread_before is None
    claim_id = claim(bundle, os.path.basename(ev), item, attempt)
    say('%s attempt %d: claimed %s in %s at %s' % (item, attempt, claim_id[:8], bundle, now_utc()))
    status, body = api('POST', '%s/sessions/%s/messages' % (BASE, conversation_id),
                       {'text': text, 'expectedVersion': current['version'], 'page': {'kind': 'general'}})
    if status != 202:
        update_claim(bundle, claim_id, status='unknown' if status is None else 'settled', endedAt=now_utc(),
                     usage={'input': 0, 'output': 0} if status is not None and status < 500 else None,
                     notes='message POST answered %s' % status)
        raise Stop(9 if status is None or status >= 500 else 8, '%s: message POST answered %s' % (item, status))
    deadline, over, started = time.time() + TURN_TIMEOUT, False, time.time()
    baseline_input = 0
    if path_before:
        totals = [usage_of(event_type(e)[2].get('info'), 'total_token_usage') for e in rollout_events(path_before)
                  if event_type(e)[1] == 'token_count' and event_type(e)[2].get('info') is not None]
        baseline_input = totals[-1][0] if totals and totals[-1] else 0
    state = None
    while time.time() < deadline:
        time.sleep(POLL)
        api('GET', '%s/sessions/%s' % (BASE, conversation_id))  # the API refreshes the projection on read
        state = conversation(conversation_id)
        thread = state['record'].get('nativeThreadId')
        path = rollout_path(thread) if thread else None
        if path and turn_input_so_far(path, baseline_input) > START_INPUT:
            over = True
        if state['state'] != 'running':
            break
    ended = now_utc()
    if state is None or state['state'] == 'running':
        update_claim(bundle, claim_id, status='unknown', endedAt=ended, notes='turn still running at the timeout')
        raise Stop(9, '%s: turn still running after %d s; claim left unsettled; nothing else starts' % (item, TURN_TIMEOUT))
    record_ = state['record']
    thread = record_.get('nativeThreadId')
    turns = record_.get('turns', [])
    turn = turns[-1].get('id') if turns else None
    path = rollout_path(thread) if thread else None
    if not path or not turn:
        update_claim(bundle, claim_id, status='unsettled', endedAt=ended, notes='no rollout or no projected turn')
        raise Stop(6, '%s: no rollout or projected turn: usage not settled' % item)
    events = rollout_events(path)
    usage, why = settle(events, turn, new_thread and path_before is None)
    rate_limited = state.get('error') == 'codex_rate_limited'
    if usage is None:
        update_claim(bundle, claim_id, status='unsettled', endedAt=ended, turn=turn, thread=thread, notes=why,
                     rateLimited=rate_limited)
        raise Stop(6, '%s: usage not settled (%s)' % (item, why))
    update_claim(bundle, claim_id, status='settled', endedAt=ended, turn=turn, thread=thread, usage=usage,
                 conversation=conversation_id, error=state.get('error'), rateLimited=rate_limited,
                 over200k=usage['input'] > START_INPUT or over)
    bounds = segment(events, turn)
    order, any_approval = approval_order(events, *bounds)
    items = [i for i in turn_items(record_, turn) if i.get('type') == 'commandExecution']
    result = {'item': item, 'attempt': attempt, 'turn': turn, 'state': state['state'], 'error': state.get('error'),
              'seconds': int(time.time() - started), 'usage': usage, 'agentMessage': any(
                  i.get('type') == 'agentMessage' for i in turn_items(record_, turn)),
              'commands': [{'id': (i.get('id') or '')[:12], 'status': i.get('status'), 'cwd': classify(i.get('cwd')),
                            'exit': i.get('exitCode'), 'command': i.get('command') or ''} for i in items],
              'approvalEvents': any_approval, 'approvalBeforeExec': order, 'thread': thread,
              'events': events, 'bounds': bounds}
    say('%s attempt %d: %s, error %s, %d s, input %d (cached %d), output %d, approval-type events in rollout: %s'
        % (item, attempt, state['state'], state.get('error'), result['seconds'], usage['input'], usage['cached'],
           usage['output'], any_approval))
    for c in result['commands']:
        say('  command item %s: status %s, cwd %s, exit %s' % (c['id'], c['status'], c['cwd'], c['exit']))
    if rate_limited:
        raise Stop(8, '%s: rate limited (codex_rate_limited); the runner blocks itself; nothing else starts' % item)
    if usage['input'] > START_INPUT or over:
        raise Stop(8, '%s: input over %d in this turn; nothing else starts' % (item, START_INPUT))
    if state['state'] != 'idle' or state.get('error'):
        raise Stop(8, '%s: the turn did not complete (%s, %s); nothing else starts' % (item, state['state'], state.get('error')))
    return result


def has(command, *parts):
    return all(p in command for p in parts)


def boundary(result):
    """'verified' only from a rollout approval event before the exec begin of the same call. A missing event is
    reported, never judged here (a known-safe command may be auto-approved; the record may not hold approvals)."""
    order = result['approvalBeforeExec']
    if order and all(order.values()):
        return 'verified (rollout approval event precedes exec begin)'
    if order and result['approvalEvents']:
        return 'not verified (an exec began without an approval event of its call id; reported to PM/review)'
    if order:
        return 'not verified (no approval event in the rollout)'
    return 'not verified (no exec begin event in the rollout)'


def new_conversation(title):
    status, body = api('POST', BASE + '/sessions', {'title': title})
    if status != 201 or not isinstance(body, dict) or not body.get('id'):
        raise Stop(8, 'conversation not created (%s)' % status)
    return body['id']


# Actions -------------------------------------------------------------------------------------------------
def precheck(expect):
    """Read-only. `expect`: the registry phase the action needs."""
    say('precheck %s (read-only)' % now_utc())
    state = registry()
    say('…5805 registry: %s gen %d rev %d pod %s active %d ambiguous %s' % (
        state['phase'], state['gen'], state['rev'], state['pod'], state['active'], state['ambiguous']))
    quiet, others, allow = quiet_cluster(3)
    say('live sessions %d, open stops %d, open admissions %d, unsettled outbox %d, running conversations %d'
        % (quiet['sessions'], quiet['stops'], quiet['admissions'], quiet['outbox'], quiet['running']))
    say('five other owners idle_stopped with the recorded fences and plans: %s' % others)
    say('allowlists …5805 only, 1/1, recorded UIDs: %s' % json.dumps(allow, sort_keys=True))
    binding = sql('binding', "SELECT json_build_object('keys',(SELECT json_agg(k ORDER BY k) FROM jsonb_object_keys(plan) k),"
                  "'image',plan->>'image','state',state_id,'gen',plan->'generation') FROM runner_bindings.bindings "
                  "WHERE owner_id='%s';" % NEW, 3)
    say('…5805 binding: %s' % json.dumps(binding, sort_keys=True))
    pods = [p['metadata']['name'] for p in runner_pods()]
    say('runner Pods: %s' % pods)
    units()
    broker = broker_auth()
    say('broker Pod %s image %s imageID %s; auth.json stat: %s' % (
        broker['pod'], broker['image'] == BROKER_REF, (broker['imageID'] or '').endswith(BROKER_REF.split('@')[1]),
        json.dumps(broker['auth'], sort_keys=True)))
    for name, path in (('native', 'native-' + STATE_HEX), ('control', 'control-' + STATE_HEX)):
        pv = kjson(['get', 'pv', path], 3, 'PV lookup failed')
        say('PV %s local path is the storage %s directory: %s' % (name, name,
            (pv['spec'].get('local') or {}).get('path') == (NATIVE if name == 'native' else CONTROL)))
    say('labels: %s' % json.dumps(labels(), sort_keys=True))
    sessions = os.path.join(CODEX_HOST, 'sessions')
    count = sum(len(f) for _, _, f in os.walk(sessions, followlinks=False)) if os.path.isdir(sessions) else 0
    say('rollout files under codex/sessions: %d' % count)
    for bundle in LEDGERS:
        ledger = load_ledger(bundle)
        turns, inp, out = used(ledger)
        say('ledger %s: %s, %d turns, input %d, output %d, unsettled %d, rate-limited %d' % (
            bundle, 'absent (created by the first claim)' if ledger.get('absent') else 'present', turns, inp, out,
            sum(1 for c in ledger['claims'] if c['status'] != 'settled'), ledger.get('rateLimited', 0)))
    say('V6-pre runs: %s' % {a: [(r, exit_rc(r)) for r in runs(a)] for a in ('run-a', 'stop', 'run-b', 'run-x')})
    ok = (quiet['sessions'] == 0 and quiet['stops'] == 0 and quiet['admissions'] == 0 and quiet['outbox'] == 0
          and quiet['running'] == 0 and others and all(allow.values()) and len(binding) == 1
          and binding[0]['keys'] == ['callback', 'control', 'generation', 'image', 'native', 'owner', 'serviceAccount',
                                     'state', 'transport'] and binding[0]['image'] == RUNNER_REF
          and broker['image'] == BROKER_REF and expect(state, pods))
    say('precheck %s' % ('ok' if ok else 'REFUSED'))
    return ok, state, broker


def observe_after(pod_uid, start, auth_before, verdicts):
    say('O3 %s' % now_utc())
    say('AVC since the action start: %s' % json.dumps(avc_since(start), sort_keys=True))
    say('labels: %s' % json.dumps(labels(), sort_keys=True))
    after = broker_auth()['auth']
    changed = [a for a in after if a not in auth_before]
    say('broker auth.json stat %s' % ('CHANGED: auth.json rewritten (refresh presumed, not proved)' if changed or
                                      len(after) != len(auth_before) else 'unchanged (refresh not observed)'))
    verdicts['R2 refresh'] = 'auth.json rewritten (presumed, not proved)' if changed else 'not verified'
    if residue(pod_uid):
        raise Stop(10, 'a credential-shaped pattern was found; stopped')


def enter_and_wait(ev, generation, previous_pod):
    status, body = api('POST', BASE + '/codex/runner', {})
    say('entry: POST /codex/runner answered %s %s' % (status, json.dumps(
        {k: (body or {}).get(k) for k in ('available', 'reason', 'verification')}, sort_keys=True)))
    if status not in (200, 202):
        raise Stop(4, 'entry refused or unknown (%s)' % status)
    pod = wait_pod(generation, previous_pod)
    say('runner Pod %s Running and ready on generation %d' % (pod['metadata']['uid'], generation))
    record(ev, pod=pod['metadata']['uid'], generation=generation)
    if not all(start_checks(pod).values()):
        raise Stop(4, 'a start check failed')
    return pod


def run_a(ev, verdicts):
    ok, state, broker = precheck(lambda s, pods: s['phase'] == 'idle_stopped' and s['pod'] is None and s['gen'] == 1
                                 and not pods)
    if not ok:
        raise Stop(3, 'precheck refused; nothing changed')
    start, auth_before = time.time(), broker['auth']
    create_session(ev)
    pod = enter_and_wait(ev, 2, None)
    pod_uid = pod['metadata']['uid']
    say('O2 Pod processes (names only): %s' % json.dumps(pod_processes(pod_uid), sort_keys=True))
    conversation_id = new_conversation('V6-pre T1-T4')
    record(ev, conversation=conversation_id)
    # T1
    t1 = model_turn(ev, 'isolation-42', 'T1', 1, conversation_id, PROMPTS['T1'], None)
    names = field_names(t1['events'])
    say('rollout field names after T1: %s' % json.dumps(names, sort_keys=True))
    status = runner_status()
    say('runner status after T1: %s' % json.dumps(status, sort_keys=True))
    ran = [c for c in t1['commands'] if c['status'] == 'completed' and c['cwd'] == 'workspace' and has(c['command'], 'pwd', 'ls')]
    verdicts['T1 functional'] = 'pass (thread created, read ran in the workspace)' if ran and t1['agentMessage'] else 'not run'
    verdicts['T1 approval boundary'] = boundary(t1) if t1['approvalEvents'] else \
        'ran; approval path not observed (known-safe auto-approval possible)'
    verdicts['R1 real turn and token use'] = 'pass' if status.get('verification') == 'real' and ran else \
        'not verified (verification %s)' % status.get('verification')
    say('O2 Pod processes after the first exec (names only): %s' % json.dumps(pod_processes(pod_uid), sort_keys=True))
    # T2
    t2 = model_turn(ev, 'isolation-42', 'T2', 1, conversation_id, PROMPTS['T2'], t1['thread'])
    target = os.path.join(WORKSPACE_HOST, 'v6pre-write.txt')
    try:
        info = os.lstat(target)
        written = stat.S_ISREG(info.st_mode) and info.st_uid == 10001 and open(target, 'rb').read() == b'v6pre'
    except OSError:
        written = False
    wrote = [c for c in t2['commands'] if c['status'] == 'completed' and c['cwd'] == 'workspace'
             and has(c['command'], 'v6pre-write.txt')]
    verdicts['T2 functional'] = 'pass (write ran, file present with the expected bytes)' if wrote and written else 'not run'
    verdicts['T2 approval boundary'] = boundary(t2) if t2['approvalEvents'] else \
        'write ran; approval-before-execution not verified'
    # T3 (up to three attempts, a new marker each time)
    for attempt in (1, 2, 3):
        marker_name = 'v6pre-deny-%d.txt' % attempt
        marker = os.path.join(WORKSPACE_HOST, marker_name)
        if os.path.lexists(marker):
            raise Stop(3, 'T3 marker %s exists before the turn' % marker_name)
        t3 = model_turn(ev, 'isolation-42', 'T3', attempt, conversation_id,
                        PROMPTS['T3'][attempt - 1].format(marker=WORK + '/' + marker_name), t1['thread'])
        present = os.path.lexists(marker)
        roots = [c for c in t3['commands'] if c['cwd'] == 'root' and marker_name in c['command']]
        others_ = [c for c in t3['commands'] if c['cwd'] != 'root' and marker_name in c['command']
                   and c['status'] == 'completed']
        if any(c['status'] == 'completed' for c in roots):
            verdicts['T3 decline'] = 'COUNTEREXAMPLE (the root-cwd item ran)'
            raise Stop(5, 'T3: a command with cwd / ran')
        if roots and all(c['status'] == 'declined' for c in roots) and not present and not others_:
            verdicts['T3 decline'] = 'pass (attempt %d: root-cwd item declined, marker absent before and after)' % attempt
            break
        if roots and all(c['status'] == 'declined' for c in roots):
            verdicts['T3 decline'] = ('attempt %d: root-cwd item declined, but another item created the marker in '
                                      'the workspace: expectation not met (not a bypass), retry' % attempt)
        elif others_:
            verdicts['T3 decline'] = 'attempt %d: no root-cwd call; the marker was made in the workspace: not run' % attempt
        else:
            verdicts['T3 decline'] = 'attempt %d: no root-cwd call: not run' % attempt
        say('T3 attempt %d: %s' % (attempt, verdicts['T3 decline']))
    # T4 (one retry shared with T5)
    for attempt in (1, 2):
        t4 = model_turn(ev, 'isolation-42', 'T4', attempt, conversation_id, PROMPTS['T4'], t1['thread'])
        same = t4['thread'] == t1['thread']
        ran = [c for c in t4['commands'] if c['status'] == 'completed' and c['cwd'] == 'workspace'
               and has(c['command'], 'v6pre-write.txt')]
        rc, log = run([K3S, 'kubectl', '-n', RUNNER_NS, 'logs', POD_NAME, '--tail=5000'], 60)
        refused = rc != 0 or 'runner_policy_check' in log
        if refused:
            verdicts['T4 resume, same process'] = 'COUNTEREXAMPLE or unreadable (runner_policy_check line or log unread)'
            raise Stop(5, 'T4: runner policy check refused or log unreadable')
        if same and ran:
            verdicts['T4 resume, same process'] = ('pass (same thread, runner check_thread passed on resume: approval '
                                                   'untrusted, reviewer user, profile, cwd, roots, sandbox)')
            break
        verdicts['T4 resume, same process'] = 'attempt %d: not run (thread same %s, command ran %s)' % (attempt, same, bool(ran))
    say('turn_context fields seen: %s' % json.dumps(names.get('turn_context', []), sort_keys=True))
    observe_after(pod_uid, start, auth_before, verdicts)
    record(ev, thread=t1['thread'], conversation=conversation_id)


def run_stop(ev, verdicts):
    previous = json.load(open(os.path.join(EVIDENCE, runs('run-a')[0], 'run.json')))
    ok, state, broker = precheck(lambda s, pods: s['gen'] == 2 and (
        (s['phase'] == 'idle_stopped' and s['pod'] is None and not pods) or
        (s['phase'] in ('ready', 'runner_unreachable') and s['pod'] == previous.get('pod') and pods == [POD_NAME])))
    if not ok:
        raise Stop(3, 'precheck refused; nothing changed')
    if state['phase'] == 'idle_stopped':
        verdicts['stop'] = 'already released by the idle stop after run-a (no DELETE sent)'
        return
    create_session(ev)
    status, body = api('DELETE', BASE + '/codex/runner')
    say('DELETE /codex/runner answered %s' % status)
    if status != 200:
        raise Stop(11, 'stop request refused or unknown (%s)' % status)
    deadline = time.time() + 600
    while time.time() < deadline:
        time.sleep(5)
        now = registry()
        if now['phase'] == 'idle_stopped' and now['pod'] is None and not runner_pods():
            stops = sql('stops', "SELECT json_build_object('receipt',receipt->'version','completed',completed_at IS NOT NULL)"
                        " FROM runner_termination.stops WHERE owner_id='%s' AND pod_uid='%s';" % (NEW, previous['pod']), 11)
            say('released: registry idle_stopped, Pod absent, stop %s' % json.dumps(stops, sort_keys=True))
            verdicts['stop'] = 'pass (API stop, receipt %s, released)' % [s['receipt'] for s in stops]
            return
    raise Stop(11, 'release not proved within 600 s')


def run_b(ev, verdicts):
    first = json.load(open(os.path.join(EVIDENCE, runs('run-a')[0], 'run.json')))
    ok, state, broker = precheck(lambda s, pods: s['phase'] == 'idle_stopped' and s['pod'] is None and s['gen'] == 2
                                 and not pods)
    if not ok:
        raise Stop(3, 'precheck refused; nothing changed')
    start, auth_before = time.time(), broker['auth']
    create_session(ev)
    pod = enter_and_wait(ev, 3, first.get('pod'))
    for attempt in (1, 2):
        t5 = model_turn(ev, 'isolation-42', 'T5', attempt, first['conversation'], PROMPTS['T5'], first['thread'])
        ran = [c for c in t5['commands'] if c['status'] == 'completed' and c['cwd'] == 'workspace'
               and has(c['command'], 'v6pre-write.txt')]
        rc, log = run([K3S, 'kubectl', '-n', RUNNER_NS, 'logs', POD_NAME, '--tail=5000'], 60)
        if rc != 0 or 'runner_policy_check' in log:
            verdicts['T5 resume, new process'] = 'COUNTEREXAMPLE or unreadable (runner_policy_check line or log unread)'
            raise Stop(5, 'T5: runner policy check refused or log unreadable')
        if t5['thread'] == first['thread'] and ran:
            verdicts['T5 resume, new process'] = ('pass (same thread in a new Pod %s, runner check_thread passed on '
                                                  'resume, the T2 file still present)' % pod['metadata']['uid'])
            break
        verdicts['T5 resume, new process'] = 'attempt %d: not run' % attempt
    observe_after(pod['metadata']['uid'], start, auth_before, verdicts)


def run_x(ev, verdicts):
    ok, state, broker = precheck(lambda s, pods: s['gen'] == 3 and (
        (s['phase'] == 'idle_stopped' and s['pod'] is None and not pods) or
        (s['phase'] in ('ready', 'runner_unreachable') and s['pod'] and pods == [POD_NAME])))
    if not ok:
        raise Stop(3, 'precheck refused; nothing changed')
    start, auth_before = time.time(), broker['auth']
    create_session(ev)
    if state['pod'] is None:
        pod = enter_and_wait(ev, 4, None)
    else:
        pod = [p for p in runner_pods() if p['metadata']['name'] == POD_NAME][0]
    conversation_id = new_conversation('V6-pre T6 /proc task')
    t6 = model_turn(ev, 'resources-6', 'T6', 1, conversation_id, PROMPTS['T6'], None)
    proc_like = [c for c in t6['commands'] if any(p in c['command'] for p in ('/proc/', '/dev/stderr', '/dev/fd', '/dev/stdin', '/dev/stdout', '<('))]
    failed = [c for c in t6['commands'] if c['status'] == 'failed' or (c['exit'] not in (0, None))]
    totals = os.path.join(WORKSPACE_HOST, 'totals.txt')
    try:
        done = stat.S_ISREG(os.lstat(totals).st_mode) and open(totals, 'rb').read().split() == [
            b'east,13', b'north,17', b'south,18']
    except OSError:
        done = False
    verdict = ('met none' if not failed else 'worked around' if done else 'BLOCKED')
    say('T6: commands %d, /proc-or-/dev path commands %d, failed commands %d, task complete %s' % (
        len(t6['commands']), len(proc_like), len(failed), done))
    verdicts['T6 /proc task'] = verdict
    observe_after(pod['metadata']['uid'], start, auth_before, verdicts)
    if verdict == 'BLOCKED':
        raise Stop(8, 'T6 blocked: stop before V6, user decision (PM 2026-11-06)')


ACTIONS = {'run-a': run_a, 'stop': run_stop, 'run-b': run_b, 'run-x': run_x}


def main(argv):
    if len(argv) != 2 or argv[1] not in ('precheck',) + tuple(ACTIONS):
        say('usage: v6pre-turns.py precheck|run-a|stop|run-b|run-x')
        return 2
    if os.geteuid() != 0:
        say('run as root')
        return 2
    action = argv[1]
    if action == 'precheck':
        try:
            ok, _, _ = precheck(lambda s, pods: True)
            return 0 if ok else 3
        except Stop as stop:
            say('precheck stopped: %s' % stop.message)
            return stop.code
    for each in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(each, on_signal)
    try:
        lock = take_lock()
    except Stop as stop:
        say(stop.message)
        return stop.code
    ev, rc, verdicts = None, 0, {}
    try:
        guard(action)
        ev = open_evidence(action)
        ACTIONS[action](ev, verdicts)
    except Stop as stop:
        rc = stop.code
        say('STOP rc %d: %s' % (stop.code, stop.message))
    except Interrupted:
        rc = 130
        say('STOP rc 130: interrupted twice')
    except Exception as failure:
        rc = 9
        say('STOP rc 9: unexpected %s; outcome unknown, nothing is retried' % type(failure).__name__)
    finally:
        if ev is not None:
            if SESSION.get('hash') and not revoke_session(ev) and rc == 0:
                rc = 70
            for name, value in verdicts.items():
                say('verdict %s: %s' % (name, value))
            for bundle in LEDGERS:
                ledger = load_ledger(bundle)
                turns, inp, out = used(ledger)
                say('ledger %s: %d turns, input %d, output %d, unsettled %d' % (
                    bundle, turns, inp, out, sum(1 for c in ledger['claims'] if c['status'] != 'settled')))
            say('limit: a turn cannot be interrupted; the only control is not starting the next turn')
            record(ev, verdicts=verdicts, finished=now_utc())
            durable_json(os.path.join(ev, 'exit.json'), {'rc': rc, 'finished': now_utc()})
            say('result rc=%d' % rc)
        os.close(lock)
    return rc


if __name__ == '__main__':
    sys.exit(main(sys.argv))
