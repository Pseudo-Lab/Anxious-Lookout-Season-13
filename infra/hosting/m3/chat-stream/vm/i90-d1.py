"""#90 stage D1 (chat-stream/DEV-VM.md P2 "Regression checks"), development VM only: the #90 API and runner with
both stream flags off, no Codex turn. Run as root on the guest:

    python3 -I -B /root/issue90/i90-d1.py check   # read-only: changes nothing, writes no file
    systemd-run … python3 -I -B /root/issue90/i90-d1.py run     # once, after review and PM's go

`run`: a one-hour browser session for …5805 (created in auth.sessions, its token only in memory, revoked at the end
on every exit the process can handle) → /api/health, /api/version → /codex/status shape → GET /sessions and the
three newest conversations' GET /sessions/{id} (shape, item count, items digest) → GET …/events and POST
…/interrupt answer 404 stream_disabled → POST /codex/runner (the existing entry) → generation 30 Pod Running and
ready on the stage C runner image → /codex/status at +5/+35/+65 s from the container start, available true and
reason null at the last read (an explicit signal; a UID / containerID / restartCount change stops the reads) →
DELETE /codex/runner → idle_stopped with no Pod, released by one completed stop with drain receipt 1 → session
revoked → final invariants (the five other owners' fence and plan as in i90-expected.json, both Deployments as
after stage C, no runner Pod, nothing pending, no live session).

The stage C facts are read from /root/issue90/i90-expected.json, accepted only at its reviewed sha256 (EXPECTED_SHA),
so no image or fence is typed here. Evidence: /root/issue90/evidence/i90-d1-<UTC>/ (out.txt, run.json, exit.json);
an flock on evidence/i90-d.lock; one run (a second run is refused, any rerun is a PM decision). The session token
and CSRF value are never printed or written; only the first 16 hex characters of the token hash are.

rc: 0 every check passed; 3 refused before any change; 4 a regression check failed before the entry (the runner
was not started); 5 the runner start was not proved, by a failed check or any other exception after the entry (the
stop still runs); 6 stop/release not proved; 7 session not created; 8 every step passed but the final invariants
failed or could not be checked; 9 every step passed but an evidence write failed (out.txt / run.json); 70 every step
passed but the session revoke is not proved; 130 interrupted twice (after the entry: no stop is sent, the runner is
left to its idle stop). exit.json holds rc, stepRc, sessionRevoked, finalInvariants.

Cleanup (review 518c088): the session revoke runs in a `finally` once a session hash exists and never raises; an
evidence write failure (say / record) is remembered, never raised into a cleanup step.
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
import subprocess
import sys
import time
import uuid

OWNER = '00000000-0000-4000-8000-000000005805'
OWNER_HEX = uuid.UUID(OWNER).hex
STATE = '75780e81-ef7f-4aec-a59c-ed9d71f4061a'
GENERATION, REVISION = 29, 116  # …5805 after stage C (C-run 2026-11-14, after-survey 7a228683…)
SOURCE = '159e271c33853e93740c213db331103dd9359622'  # IMAGE_SHA: /api/version must name it
EXPECTED_FILE = '/root/issue90/i90-expected.json'
EXPECTED_SHA = '2476bdb2535e7733a58494fbac63057a02d8724b6b9f599b94fbfb07efa31b9f'  # i90-expected.json, from sha256sum
K3S = '/usr/local/bin/k3s'
EVIDENCE = '/root/issue90/evidence'
LOCK = EVIDENCE + '/i90-d.lock'
RUNNER_NS, POD_NAME = 'codex-runner', 'runner-' + OWNER_HEX
DEPLOYMENTS = (('runner-control', 'runner-controller', '41564f26-95e7-4ad1-8af3-d73b93c4669a', 'controller'),
               ('m2-hosting', 'api', 'fad6799a-6df5-4086-812f-cfae6212adfa', 'api'))
ORIGIN, API_HOST, API_PORT, HOST_HEADER = 'http://192.88.99.1', '10.0.0.107', 80, '192.88.99.1'
BASE = '/api/research'
STATUS_KEYS = {'available', 'reason', 'verification', 'model', 'retryAfterSeconds'}
SUMMARY_KEYS = {'id', 'title', 'state', 'createdAt', 'updatedAt'}
STREAM_FLAGS = ('RESEARCH_STREAM_ENABLE', 'CODEX_RUNNER_STREAM_TEXT', 'CODEX_RUNNER_STREAM_TRACE')
READS = (5, 35, 65)  # seconds after the runner container start (runner-ready rule, #68 V6)
SESSION = {}
OUT = {'stream': None}
STOPPING = {'requested': False}


class Stop(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


class Interrupted(BaseException):
    pass


def on_signal(signum, frame):
    if STOPPING['requested']:
        for each in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(each, signal.SIG_IGN)
        raise Interrupted()
    STOPPING['requested'] = True


def say(text):
    line = str(text)
    try:
        print(line, flush=True)
    except Exception:
        pass
    if OUT['stream'] is not None and not OUT.get('failed'):
        try:  # an evidence write failure never stops a cleanup step (review 518c088 major); it is reported in rc
            OUT['stream'].write(line + '\n')
            OUT['stream'].flush()
            os.fsync(OUT['stream'].fileno())
        except Exception:
            OUT['failed'] = True


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


def kjson(args, code, message):
    rc, out = run([K3S, 'kubectl'] + args + ['-o', 'json'], 60)
    if rc != 0:
        raise Stop(code, message)
    try:
        return json.loads(out)
    except ValueError:
        raise Stop(code, message) from None


def sql(tag, query, code, write=False):
    text = '%s\n/*i90d1:%s*/ %s\nCOMMIT;\n' % ('BEGIN;' if write else 'BEGIN READ ONLY;', tag, query)
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


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def durable_json(path, value):
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


def record(ev, **items):
    path = os.path.join(ev, 'run.json')
    value = json.load(open(path)) if os.path.exists(path) else {}
    value.update(items)
    durable_json(path, value)


# Facts from stage C ---------------------------------------------------------------------------------------
def expected():
    """i90-expected.json at its reviewed sha256 (root regular file, no group/other write), else rc 3."""
    if not re.fullmatch(r'[0-9a-f]{64}', EXPECTED_SHA):
        raise Stop(3, 'EXPECTED_SHA is still a placeholder')
    try:
        info = os.lstat(EXPECTED_FILE)
        data = open(EXPECTED_FILE, 'rb').read()
        doc = json.loads(data)
    except Exception:
        raise Stop(3, 'i90-expected.json missing or unreadable') from None
    if not (os.path.isfile(EXPECTED_FILE) and not os.path.islink(EXPECTED_FILE) and info.st_uid == 0
            and not info.st_mode & 0o022 and hashlib.sha256(data).hexdigest() == EXPECTED_SHA):
        raise Stop(3, 'i90-expected.json is not the reviewed root file')
    return doc


def registry():
    rows = sql('registry', "SELECT json_build_object('phase',phase,'gen',generation,'rev',revision,'pod',pod_uid,"
               "'active',active_requests,'ambiguous',ambiguous,'state',state_id) FROM runner_runtime.registry "
               "WHERE owner_id='%s';" % OWNER, 3)
    if len(rows) != 1 or rows[0]['state'] != STATE:
        raise Stop(3, '…5805 registry row missing or on another state')
    return rows[0]


def runner_pods(code=3):
    return kjson(['-n', RUNNER_NS, 'get', 'pods'], code, 'runner Pod lookup failed')['items']


def deployments_as_after_c(doc, code):
    """Both Deployments: recorded UID, 1/1 ready, images as after stage C, allowlist …5805, no stream flag set."""
    want = {'runner-controller': doc['deployments']['runner-controller']['images'],
            'api': dict(doc['deployments']['api']['images'], api=doc['newApiImage'])}
    result = {}
    for ns, name, uid, container in DEPLOYMENTS:
        obj = kjson(['-n', ns, 'get', 'deployment', name], code, 'deployment %s lookup failed' % name)
        spec = obj['spec']['template']['spec']
        images = {c['name']: c.get('image') for c in spec.get('containers', []) + spec.get('initContainers', [])}
        env = {e['name']: e.get('value') for c in spec.get('containers', []) if c['name'] == container
               for e in c.get('env', [])}
        result[name] = (obj['metadata']['uid'] == uid and obj['spec'].get('replicas') == 1
                        and obj['status'].get('readyReplicas') == 1 and images == want[name]
                        and env.get('CODEX_USER_RUNNER_ALLOWLIST') == OWNER
                        and not any(flag in env for flag in STREAM_FLAGS))
    return result


def others_unchanged(doc, code):
    rows = sql('others', "SELECT json_build_object('owner',r.owner_id,'state',r.state_id,'phase',r.phase,'gen',"
               "r.generation,'rev',r.revision,'pod',r.pod_uid,'plan',b.plan) FROM runner_runtime.registry r JOIN "
               "runner_bindings.bindings b ON b.owner_id=r.owner_id WHERE r.owner_id<>'%s' ORDER BY r.owner_id;"
               % OWNER, code)
    found = {r['owner']: {'state': r['state'], 'generation': r['gen'], 'revision': r['rev'], 'planSha256': digest(r['plan']),
                          'image': r['plan'].get('image')} for r in rows if r['phase'] == 'idle_stopped' and r['pod'] is None}
    return found == doc['others']


def pending(code):
    return sql('pending', "SELECT json_build_object('sessions',(SELECT count(*) FROM auth.sessions WHERE "
               "expires_at>now()),'stops',(SELECT count(*) FROM runner_termination.stops WHERE completed_at IS NULL),"
               "'admissions',(SELECT count(*) FROM runner_runtime.admissions WHERE completed_at IS NULL),"
               "'outbox',(SELECT count(*) FROM runner_runtime.outbox WHERE NOT settled),"
               "'running',(SELECT count(*) FROM research.conversations WHERE state='running'));", code)[0]


def binding_image(code):
    rows = sql('binding', "SELECT json_build_object('image',plan->>'image') FROM runner_bindings.bindings WHERE "
               "owner_id='%s';" % OWNER, code)
    return rows[0]['image'] if len(rows) == 1 else None


# HTTP ---------------------------------------------------------------------------------------------------
def call(method, path, body=None, session=True):
    """(status, parsed JSON or None, content type). Writes carry CSRF, Origin and a new Idempotency-Key."""
    headers = {'Host': HOST_HEADER, 'Accept': 'application/json'}
    if session:
        headers['Cookie'] = 'anxious_session=' + SESSION['token']
    data = None
    if method != 'GET':
        headers.update({'Origin': ORIGIN, 'Idempotency-Key': str(uuid.uuid4()), 'Content-Type': 'application/json'})
        if session:
            headers['x-csrf-token'] = SESSION['csrf']
        data = json.dumps(body if body is not None else {}).encode()
    connection = http.client.HTTPConnection(API_HOST, API_PORT, timeout=60)
    try:
        connection.request(method, path, body=data, headers=headers)
        response = connection.getresponse()
        status, kind, raw = response.status, response.getheader('content-type', ''), response.read(1 << 20)
    except Exception:
        return None, None, ''
    finally:
        try:
            connection.close()
        except Exception:
            pass
    try:
        return status, json.loads(raw), kind
    except ValueError:
        return status, None, kind



def error_code(body):
    if isinstance(body, dict):
        error = body.get('error')
        return error.get('code') if isinstance(error, dict) else error
    return None


def health_and_version():
    status, body, _ = call('GET', '/api/health', session=False)
    v_status, v_body, _ = call('GET', '/api/version', session=False)
    ok = status == 200 and v_status == 200 and isinstance(v_body, dict) and v_body.get('sha') == SOURCE
    say('health %s; version %s sha %s: %s' % (status, v_status, (v_body or {}).get('sha') if isinstance(v_body, dict)
                                               else None, ok))
    return ok


def stream_off_without_session():
    """Both stream endpoints refuse before any login check while RESEARCH_STREAM_ENABLE is off."""
    sid = str(uuid.uuid4())
    e_status, e_body, _ = call('GET', BASE + '/sessions/%s/events' % sid, session=False)
    i_status, i_body, _ = call('POST', BASE + '/sessions/%s/interrupt' % sid, {'requestId': str(uuid.uuid4())},
                               session=False)
    ok = (e_status, error_code(e_body), i_status, error_code(i_body)) == (404, 'stream_disabled', 404, 'stream_disabled')
    say('events %s %s, interrupt %s %s (no session): %s' % (e_status, error_code(e_body), i_status, error_code(i_body), ok))
    return ok


# Session ------------------------------------------------------------------------------------------------
def create_session(ev):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(('m2-origin-session-v1\0' + ORIGIN + '\0' + token).encode()).hexdigest()
    SESSION.update(hash=token_hash, created=None, revoked=None)
    record(ev, session='pending', sessionId=token_hash[:16])
    try:
        rows = sql('session', "WITH s AS (INSERT INTO auth.sessions(token_hash,account_id,csrf_token,expires_at) VALUES "
                   "('%s','%s','%s',now()+interval '1 hour') RETURNING token_hash) SELECT "
                   "json_build_object('id',left(token_hash,16)) FROM s;" % (token_hash, OWNER, csrf), 7, write=True)
    except Stop:
        raise Stop(7, 'session: insert failed or its result is unknown (removed below)') from None
    if len(rows) != 1 or rows[0].get('id') != token_hash[:16]:
        raise Stop(7, 'session: insert answered another row')
    SESSION.update(created=True, token=token, csrf=csrf)
    record(ev, session='created')
    say('session created: id %s, 1 hour, owner …5805' % token_hash[:16])


def revoke_session(ev):
    """Never raises: called from main's finally once a session hash exists (also after a failed insert, whose
    result is unknown). The evidence record and the report line are best effort; the DELETE and its check are not
    skipped by their failure (review 518c088 major)."""
    if not SESSION.get('hash'):
        return None
    if SESSION.get('revoked'):
        return True
    try:
        sql('revoke', "WITH d AS (DELETE FROM auth.sessions WHERE token_hash='%s' AND account_id='%s' RETURNING 1) "
            "SELECT json_build_object('deleted',count(*)) FROM d;" % (SESSION['hash'], OWNER), 70, write=True)
        left = sql('session', "SELECT json_build_object('n',count(*)) FROM auth.sessions WHERE token_hash='%s';"
                   % SESSION['hash'], 70)
        SESSION['revoked'] = left == [{'n': 0}]
    except Exception:
        SESSION['revoked'] = False
    try:
        record(ev, session='revoked' if SESSION['revoked'] else 'revoke not proved (expires by itself)')
    except Exception:
        OUT['failed'] = True
    say('session %s: %s' % (SESSION['hash'][:16], 'revoked' if SESSION['revoked'] else 'REVOKE NOT PROVED'))
    return SESSION['revoked']


# Steps --------------------------------------------------------------------------------------------------
def precheck(doc):
    state = registry()
    fence = (state['phase'], state['gen'], state['rev'], state['pod'], state['active'], state['ambiguous'])
    say('…5805 registry %s' % json.dumps(state, sort_keys=True))
    if fence != ('idle_stopped', GENERATION, REVISION, None, 0, False):
        raise Stop(3, '…5805 is not idle_stopped on generation %d / revision %d with no Pod' % (GENERATION, REVISION))
    if binding_image(3) != doc['newImage']:
        raise Stop(3, '…5805 binding image is not the stage C runner digest reference')
    if runner_pods():
        raise Stop(3, 'a runner Pod exists')
    if not others_unchanged(doc, 3):
        raise Stop(3, 'another owner differs from i90-expected.json')
    deployments = deployments_as_after_c(doc, 3)
    say('deployments as after stage C (UID, 1/1, images, allowlist, no stream flag): %s' % json.dumps(deployments))
    if not all(deployments.values()):
        raise Stop(3, 'a Deployment is not as after stage C')
    quiet = pending(3)
    say('pending %s' % json.dumps(quiet, sort_keys=True))
    if quiet != {'sessions': 0, 'stops': 0, 'admissions': 0, 'outbox': 0, 'running': 0}:
        raise Stop(3, 'a live session or pending work exists')
    if not health_and_version():
        raise Stop(3, '/api/health or /api/version is not as expected')
    if not stream_off_without_session():
        raise Stop(3, 'a stream endpoint does not answer 404 stream_disabled')


def regression(ev, verdicts):
    status, body, _ = call('GET', BASE + '/codex/status')
    shape = status == 200 and isinstance(body, dict) and set(body) <= STATUS_KEYS and \
        {'available', 'reason', 'verification', 'model'} <= set(body) and body.get('available') is False
    say('codex/status %s %s: shape %s' % (status, json.dumps(body, sort_keys=True) if isinstance(body, dict) else None, shape))
    verdicts['codex/status shape (runner stopped)'] = shape
    status, body, _ = call('GET', BASE + '/sessions?limit=3')
    items = body.get('items') if status == 200 and isinstance(body, dict) else None
    listing = isinstance(items, list) and all(isinstance(i, dict) and SUMMARY_KEYS <= set(i) for i in items) \
        and set(body) == {'items', 'nextCursor'}
    say('sessions %s: %d items, shape %s' % (status, len(items) if isinstance(items, list) else -1, listing))
    verdicts['GET /sessions shape'] = listing
    details = []
    for item in (items or [])[:3]:
        d_status, d_body, _ = call('GET', BASE + '/sessions/%s' % item.get('id'))
        ok = d_status == 200 and isinstance(d_body, dict) and isinstance(d_body.get('items'), list) \
            and 'error' in d_body and SUMMARY_KEYS <= set(d_body) and d_body.get('state') == item.get('state')
        entry = {'id': item.get('id'), 'status': d_status, 'state': (d_body or {}).get('state') if isinstance(d_body, dict)
                 else None, 'items': len(d_body['items']) if ok else None, 'itemsSha256': digest(d_body['items']) if ok
                 else None, 'shape': ok}
        details.append(entry)
        say('session detail %s' % json.dumps(entry, sort_keys=True))
    verdicts['GET /sessions/{id} shape'] = bool(details) and all(d['shape'] for d in details)
    record(ev, details=details)
    if details:
        sid = details[0]['id']
        e_status, e_body, _ = call('GET', BASE + '/sessions/%s/events' % sid)
        i_status, i_body, _ = call('POST', BASE + '/sessions/%s/interrupt' % sid, {'requestId': str(uuid.uuid4())})
        ok = (e_status, error_code(e_body), i_status, error_code(i_body)) == (404, 'stream_disabled', 404, 'stream_disabled')
        say('events %s %s, interrupt %s %s (with session): %s' % (e_status, error_code(e_body), i_status,
                                                                 error_code(i_body), ok))
        verdicts['events / interrupt 404 stream_disabled (session)'] = ok
    if not all(verdicts.values()):
        raise Stop(4, 'a regression check failed: %s' % ', '.join(k for k, v in verdicts.items() if not v))


def container_state(pod):
    statuses = pod['status'].get('containerStatuses') or [{}]
    started = ((statuses[0].get('state') or {}).get('running') or {}).get('startedAt')
    return pod['metadata']['uid'], statuses[0].get('containerID'), statuses[0].get('restartCount'), started


def wait_pod(doc, limit=300):
    deadline = time.time() + limit
    while time.time() < deadline:
        state = registry()
        if state['gen'] == GENERATION + 1 and state['pod']:
            pods = [p for p in runner_pods(5) if p['metadata']['name'] == POD_NAME]
            if pods and pods[0]['metadata']['uid'] == state['pod'] and pods[0]['status'].get('phase') == 'Running' \
                    and all(c.get('ready') for c in pods[0]['status'].get('containerStatuses', [])):
                return pods[0]
        time.sleep(5)
    raise Stop(5, 'no Running, ready …5805 Pod on generation %d within %d s' % (GENERATION + 1, limit))


def start_runner(ev, doc, verdicts):
    status, body, _ = call('POST', BASE + '/codex/runner', {})
    say('entry: POST /codex/runner answered %s %s' % (status, json.dumps(body, sort_keys=True) if isinstance(body, dict)
                                                       else None))
    if status not in (200, 202):
        raise Stop(5, 'entry refused or unknown (%s)' % status)
    pod = wait_pod(doc)
    uid, cid, restarts, started = container_state(pod)
    images = [c.get('image') for c in pod['spec'].get('containers', [])]
    say('runner Pod %s Running and ready, generation %d, images %s, container started %s'
        % (uid, GENERATION + 1, images, started))
    record(ev, pod=uid, generation=GENERATION + 1)
    verdicts['runner Pod on the stage C runner image'] = images == [doc['newImage']] and len(runner_pods(5)) == 1
    try:
        t0 = calendar.timegm(time.strptime(started, '%Y-%m-%dT%H:%M:%SZ'))
    except Exception:
        raise Stop(5, 'container start time unreadable')
    last = None
    for offset in READS:
        time.sleep(max(0.0, t0 + offset - time.time()))
        pods = [p for p in runner_pods(5) if p['metadata']['name'] == POD_NAME]
        if not pods or container_state(pods[0])[:3] != (uid, cid, restarts):
            raise Stop(5, 'the runner Pod, container or restart count changed between reads')
        s_status, s_body, _ = call('GET', BASE + '/codex/status')
        last = (s_status, {k: (s_body or {}).get(k) for k in ('available', 'reason', 'verification')}
                if isinstance(s_body, dict) else None)
        say('status read +%d s: %s %s' % (offset, s_status, json.dumps(last[1], sort_keys=True)))
    ready = last is not None and last[0] == 200 and last[1] and last[1].get('available') is True \
        and last[1].get('reason') is None
    verdicts['runner available at the last read (explicit signal)'] = bool(ready)
    if not all(verdicts.values()):
        raise Stop(5, 'the runner start was not proved')
    return uid


def stop_runner(ev, verdicts):
    """DELETE /codex/runner and wait for the release. The stop row is looked up by generation (the entry's), so a
    stop sent before a Pod existed is found too."""
    sent = time.time()
    status, body, _ = call('DELETE', BASE + '/codex/runner')
    say('DELETE /codex/runner answered %s %s' % (status, json.dumps(body) if isinstance(body, dict) else None))
    if status != 200 or not isinstance(body, dict) or body.get('terminationRequested') is not True:
        raise Stop(6, 'stop request refused or unknown (%s)' % status)
    deadline = time.time() + 600
    while time.time() < deadline:
        time.sleep(5)
        state = registry()
        if state['phase'] == 'idle_stopped' and state['pod'] is None and not runner_pods(6):
            stops = sql('stops', "SELECT json_build_object('gen',generation,'receipt',receipt->'version','clean',"
                        "receipt->'clean','completed',completed_at IS NOT NULL,'requested',extract(epoch FROM "
                        "requested_at),'completedAt',extract(epoch FROM completed_at)) FROM runner_termination.stops "
                        "WHERE owner_id='%s' AND generation=%d ORDER BY requested_at;" % (OWNER, GENERATION + 1), 6)
            say('released: idle_stopped, no Pod, %.0f s after the DELETE; stops %s' % (time.time() - sent,
                                                                                    json.dumps(stops, sort_keys=True)))
            ok = len(stops) == 1 and stops[0]['gen'] == GENERATION + 1 and stops[0]['receipt'] == 1 \
                and stops[0]['completed'] is True
            verdicts['released by one completed stop with drain receipt 1'] = ok
            record(ev, stops=stops)
            if not ok:
                raise Stop(6, 'released, but not by exactly one completed stop with drain receipt 1')
            return
    raise Stop(6, 'release not proved within 600 s')


def final_check(doc):
    try:
        state = registry()
        deployments = deployments_as_after_c(doc, 8)
        ok = (state['phase'], state['gen'], state['pod']) == ('idle_stopped', GENERATION + 1, None) \
            and not runner_pods(8) and others_unchanged(doc, 8) and all(deployments.values()) \
            and binding_image(8) == doc['newImage'] \
            and pending(8) == {'sessions': 0, 'stops': 0, 'admissions': 0, 'outbox': 0, 'running': 0}
    except Exception:
        ok = False
    say('final invariants (…5805 idle_stopped gen %d, no Pod, others and Deployments as after C, binding unchanged, '
        'nothing pending, no live session): %s' % (GENERATION + 1, ok))
    return ok


def guard():
    if [d for d in os.listdir(EVIDENCE) if re.fullmatch(r'i90-d1-\d{8}T\d{6}Z', d)]:
        raise Stop(3, 'i90-d1 already ran; a second run is a PM decision')


def take_lock():
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise Stop(3, 'another i90 stage D action holds the lock; nothing done') from None
    return fd


def main(argv):
    if len(argv) != 2 or argv[1] not in ('check', 'run'):
        print('usage: i90-d1.py check|run')
        return 2
    if os.geteuid() != 0:
        print('not root; nothing done\nresult rc=3')
        return 3
    if argv[1] == 'check':
        try:
            precheck(expected())
        except Stop as stop:
            say('STOPPED (%d): %s' % (stop.code, stop.message))
            say('result rc=%d' % stop.code)
            return stop.code
        say('result rc=0 (read-only)')
        return 0
    for each in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(each, on_signal)
    ev, code, verdicts, final, step, doc, phase = None, 0, {}, None, 0, None, 'before'
    revoked = None
    try:
        try:
            doc = expected()
            lock = take_lock()  # noqa: F841 (held until exit)
            guard()
            precheck(doc)
            ev = os.path.join(EVIDENCE, 'i90-d1-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()))
            os.mkdir(ev, 0o700)
            fd = os.open(os.path.join(ev, 'out.txt'), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            OUT['stream'] = os.fdopen(fd, 'w')
            record(ev, action='run', started=now_utc(), expectedSha256=EXPECTED_SHA)
            say('evidence: %s' % ev)
            phase = 'session'
            create_session(ev)
            regression(ev, verdicts)
            # From the entry on, every start failure the process can handle (a Stop or any other exception) still
            # sends the stop (review 518c088 major). Only a second interrupt does not: the runner is then left to its
            # idle stop (CODEX_USER_RUNNER_IDLE_SECONDS) and the result is 130; report it.
            phase = 'entry'
            try:
                start_runner(ev, doc, verdicts)
            except Stop as stop:
                step = stop.code
                say('STOPPED (%d): %s; the stop is still sent' % (stop.code, stop.message))
            except Exception as error:
                step = 5
                say('STOPPED (5): unexpected %s in the start check; the stop is still sent' % type(error).__name__)
            stop_runner(ev, verdicts)
            if step:
                raise Stop(step, '')
        except Stop as stop:
            code = stop.code
            if stop.message:
                say('STOPPED (%d): %s' % (stop.code, stop.message))
        except Interrupted:
            code = 130
            say('STOPPED (130): interrupted twice%s' % ('; the runner is left to its idle stop' if phase == 'entry'
                                                        else ''))
        except Exception as error:
            code = {'before': 3, 'session': 4, 'entry': 6}[phase]
            say('STOPPED (%d): unexpected %s' % (code, type(error).__name__))
    finally:
        # Independent of every report and evidence write above (review 518c088 major); never raises.
        revoked = revoke_session(ev)
    step_rc = code
    if ev and code in (0, 4, 5, 6):
        final = final_check(doc)
    if code == 0 and final is not True:
        code = 8
    if code == 0 and revoked is False:
        code = 70
    if code == 0 and OUT.get('failed'):
        code = 9
    for name, value in verdicts.items():
        say('verdict %s: %s' % (name, value))
    if ev:
        try:
            durable_json(os.path.join(ev, 'exit.json'), {'rc': code, 'stepRc': step_rc, 'sessionRevoked': revoked,
                                                        'finalInvariants': final, 'finished': now_utc()})
        except Exception:
            say('exit.json not written: the result is unknown on disk')
    say('result rc=%d' % code)
    return code


if __name__ == '__main__':
    sys.exit(main(sys.argv))
