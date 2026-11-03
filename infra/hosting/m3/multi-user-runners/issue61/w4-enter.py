"""#61 dev-VM gate W4 (entry): start the …5804 runner once on the moved binding (#61 runner/executor images) for
the R1 VM gate. Run once by the user as root in the guest:

    python3 -I /root/issue61/w4-enter.py

1. precheck (read-only; nothing changes): no earlier W4 evidence; runner-controller and api are the recorded
   Deployments, 1/1; NO Pod at all in codex-runner; …5804 exactly at the W3b fence (idle_stopped gen 3 rev 12,
   no Pod, not ambiguous, no active request) with the approved new plan digest and images; no open stop anywhere,
   no unconsumed entry, open admission or unsettled outbox row for …5804; no other owner waiting for a Pod; the
   account approved; no live session of …5804; the API origin is the one the session is bound to; both target
   digest references resolve in k3s to the W1 config IDs. Snapshot of every conversation (id:state:version).
2. session: one auth.sessions row for …5804, valid 1 hour, created through psql (stdin). The session token and
   CSRF token exist only in this process's memory: never written to a file, an argument or the output. Printed:
   a non-secret id (the first 16 hex of the row's token hash).
3. ONE POST /api/research/codex/runner (Host 192.88.99.1 via the gateway 10.0.0.107:80; cookie, x-csrf-token,
   a fresh Idempotency-Key that is printed). Never repeated: a timeout or a lost answer is reported as unknown.
4. the session row is revoked right after the POST (exact token hash and owner), on every path.
5. the Pod (at most 300 s after the POST, read-only polling): registry generation 4 on the same state, the Pod
   runner-…5804 with that registry Pod UID and generation label, phase Running, both containers running with
   0 restarts, init container prepare-shell exited 0. Pod start and container start times are printed; the
   60-minute deadline is Pod start + 60 min.
6. identity and image proof (fail closed; never a tag or prefix match): spec images == the approved manifest
   references; status imageID == the same references (this runtime's observed format); each containerID
   resolved through CRI: imageRef == the known config ID, Pod UID / namespace / container name labels, and its
   sandbox's Pod UID; `crictl inspecti <imageID>` id == the config ID. automountServiceAccountToken false, no
   kube-api-access volume or service-account mount, exactly the broker-audience projected token at
   /run/codex-broker (read-only). Token contents are never read.
7. context, read-only: an early /health of this exact runner, read inside the runner-controller Pod with the
   controller's own client (the runner admits only api/controller), printed sanitized (ownerId, stateId, podUid,
   available, reason, verification, failure, model); the W0 ledger probe text unchanged (mode=ro, metadata only);
   the conversation snapshot again.

Evidence: /root/issue61/evidence/w4-<UTC stamp>/run.json (root 0700 directory). No secret is stored.

rc (never rerun; never a second POST; back checks read-only and PM decides):
  0  entered, Pod proved on the new images, session revoked; early health printed with r1_state=true|unobserved
  3  precheck refused: nothing changed
  4  the session could not be created (no POST was made; a row this run created is removed)
  5  the POST was answered with an error status: the live registry state is printed; session revoked
  6  the POST outcome is unknown (timeout / connection lost / interrupted): never repeated; live state printed
  7  the POST was accepted but the Pod was not proved Running within 300 s: provisioning may still continue;
     no retry, no reclaim; live state printed
  8  the Pod is not the proved instance on the approved images (identity, image, CRI or token mount): the Pod
     is left as it is; PM decides
  9  entered and proved, but the early health is NOT the R1 state (for example available, or another failure):
     the runner is left running; PM decides
  70 the main result was 0 but revoking the session row failed (printed; the row expires within the hour)
  130 interrupted before the POST: no POST; session revoked
Output is fixed messages, identities, digests and sanitized health only.
"""
import calendar
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

OWNER = '00000000-0000-4000-8000-000000005804'
STATE = '5ff583e9-abe1-4ef8-b277-4a5afead6b80'
FENCE = {'state': STATE, 'phase': 'idle_stopped', 'generation': 3, 'revision': 12, 'pod': None, 'active': 0,
         'ambiguous': False}
NEW_GENERATION = 4
PLAN_SHA = '86c691bae85544e319f0528d69fc3c878d59b919c065099b0adb6e9c92a6a481'
RUNNER_REF = 'docker.io/library/anxious-dev-runner@sha256:067c77de5d6b3673ece9b243f7753a9184a007cd44ab0ea1c93dc3044458a5e4'
EXECUTOR_REF = ('docker.io/library/anxious-dev-shell-executor@sha256:'
                '9c87d626cdf6798db5bc5a562cc68af1b13a36c8f90d3738e19c6553e7cf4d12')
RUNNER_ID = 'sha256:4cac37e2173d5aec7b351e05c2977f2e0df76670f3befb91ce54306d2444e8ad'
EXECUTOR_ID = 'sha256:49b449cb31c5b344f876a9d9aee2d97dd3dcae906c3324bc0eaf00678dd6b883'
CONTAINERS = {'runner': (RUNNER_REF, RUNNER_ID), 'shell-executor': (EXECUTOR_REF, EXECUTOR_ID)}
INIT = {'prepare-shell': RUNNER_REF}
POD_NAME = 'runner-' + OWNER.replace('-', '')
NS = 'codex-runner'
CTRL = ('runner-control', 'runner-controller', '41564f26-95e7-4ad1-8af3-d73b93c4669a')
API = ('m2-hosting', 'api', 'fad6799a-6df5-4086-812f-cfae6212adfa')
ORIGIN = 'http://192.88.99.1'
API_HOST = '10.0.0.107'
API_PORT = 80
HOST_HEADER = '192.88.99.1'
PATH = '/api/research/codex/runner'
BROKER_AUDIENCE = 'codex-broker'
EVIDENCE = '/root/issue61/evidence'
K3S = '/usr/local/bin/k3s'
POD_WAIT = max(1, min(300, int(os.environ.get('W4_POD_WAIT_SECONDS', 300))))  # the harness may only shorten it
POST_TIMEOUT = max(1, min(60, int(os.environ.get('W4_POST_TIMEOUT', 60))))  # the harness may only shorten it
DEADLINE_MINUTES = 60
HEALTH_KEYS = ('ownerId', 'stateId', 'podUid', 'available', 'reason', 'verification', 'failure', 'model')
# The W0 ledger probe, unchanged (w0-baseline.sh): mode=ro, one read transaction, metadata only.
LEDGER_PROBE = '''
import json, os, sqlite3, sys
path = os.path.join(os.environ["CODEX_ACCESS_CONTROL_DIR"], "service-access.sqlite3")
if not os.path.isfile(path) or os.path.islink(path):
    print("ledger missing"); sys.exit(0)
try:
    db = sqlite3.connect("file:" + path + "?mode=ro", uri=True, isolation_level=None, timeout=2)
    db.execute("BEGIN")
    rows = db.execute("SELECT payload FROM metadata").fetchall()
    value = json.loads(rows[0][0]) if len(rows) == 1 else None
    if not isinstance(value, dict) or not isinstance(value.get("snapshotFingerprint"), str):
        print("ledger unreadable: metadata"); sys.exit(0)
    rejected = db.execute("SELECT count(*) FROM rejected").fetchone()[0]
    member = db.execute("SELECT 1 FROM rejected WHERE fingerprint=?", (value["snapshotFingerprint"],)).fetchone() is not None
    db.execute("COMMIT")
    print("ledger blocked=%s rejected=%d current_rejected=%s snapshotId=%s"
          % (value.get("blocked"), rejected, member, value.get("snapshotId")))
except sqlite3.Error as error:
    print("ledger unreadable: " + type(error).__name__)
'''
# Read inside the runner-controller Pod with the controller's own code (read-only: a registry SELECT, the Pod
# GET, the runner's GET /health). Prints only the sanitized keys.
HEALTH_PROBE = '''
import json, sys
from uuid import UUID
from ops.runner_controller_service import build
from app.runner_registry import Registry
OWNER, POD, KEYS = UUID(%r), %r, %r
service = build()[0]
with service.observer_engine.begin() as db:
    state = Registry(db).read(OWNER)
with service.observer_pool.bound(OWNER) as runtime:
    obj = service.pod(runtime)
    if state is None or obj is None or obj["metadata"]["uid"] != POD or str(state.pod_uid) != POD:
        print(json.dumps({"probe": "refused"})); sys.exit(3)
    health = service.runner(service.address(runtime, obj), runtime.token, OWNER, str(state.state_id)).call("GET", "/health", None)
print(json.dumps(dict({k: health.get(k) for k in KEYS}, probe="health"), sort_keys=True))
'''


class Stop(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


class Interrupted(BaseException):
    pass


SIGNALLED = []


def interrupted(signum, frame):
    if not SIGNALLED:
        SIGNALLED.append(signum)
        raise Interrupted()


def say(text):
    """Output can never fail the run (a broken stdout goes to /dev/null)."""
    try:
        print(text, flush=True)
    except Exception:
        try:
            null = os.open(os.devnull, os.O_WRONLY)
            os.dup2(null, sys.stdout.fileno())
            os.close(null)
        except Exception:
            pass


def run(cmd, timeout=60, stdin=None):
    try:
        done = subprocess.run(cmd, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=max(1, timeout), check=False)
    except subprocess.TimeoutExpired:
        return 124, ''
    except Exception:
        return 125, ''
    return done.returncode, done.stdout.decode('utf-8', 'replace').strip()


def kubectl(args, code, message, timeout=60, stdin=None):
    rc, out = run([K3S, 'kubectl'] + args, timeout, stdin)
    if rc != 0:
        raise Stop(code, message)
    return out


def psql(tag, body, code, write=False):
    """JSON rows of one statement in its own transaction (READ ONLY unless write), as postgres in postgres-0."""
    text = '%s\n/*w4%s:%s*/ %s\nCOMMIT;\n' % ('BEGIN;' if write else 'BEGIN READ ONLY;', 'w' if write else '', tag, body)
    out = kubectl(['-n', 'm2-hosting', 'exec', '-i', 'postgres-0', '--', 'psql', '-U', 'postgres', '-d', 'hosting',
                   '-Atq', '-v', 'ON_ERROR_STOP=1', '-f', '-'], code, 'database %s failed' % tag, 60, text.encode())
    try:
        found = [json.loads(line) for line in out.splitlines() if line.strip()]
    except ValueError:
        raise Stop(code, 'database %s unreadable' % tag) from None
    if not all(isinstance(row, dict) for row in found):
        raise Stop(code, 'database %s unreadable' % tag)
    return found


def one(tag, body, code, write=False):
    found = psql(tag, body, code, write)
    if len(found) != 1:
        raise Stop(code, 'database %s: expected one row, got %d' % (tag, len(found)))
    return found[0]


def kget(ns, kind, name, code):
    out = kubectl(['-n', ns, 'get', kind, name, '-o', 'json', '--ignore-not-found'], code, '%s lookup failed' % kind)
    try:
        return json.loads(out) if out else None
    except ValueError:
        raise Stop(code, '%s unreadable' % kind) from None


def digest(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


RUN = {}


def record(ev, **items):
    RUN.update(items)
    if not ev:
        return
    path = os.path.join(ev, 'run.json')
    with open(path + '.tmp', 'w') as stream:
        json.dump(RUN, stream, sort_keys=True)
    os.replace(path + '.tmp', path)


# Queries ----------------------------------------------------------------------------------------------
PRE = ("SELECT json_build_object('state',r.state_id,'phase',r.phase,'generation',r.generation,'revision',r.revision,"
       "'pod',r.pod_uid,'active',r.active_requests,'ambiguous',r.ambiguous,'bindingState',b.state_id,'plan',b.plan,"
       "'role',a.role,'approved',a.is_approved,"
       "'allOpenStops',(SELECT count(*) FROM runner_termination.stops WHERE completed_at IS NULL),"
       "'entries',(SELECT count(*) FROM runner_termination.entries e WHERE e.owner_id=r.owner_id AND e.consumed_at IS NULL),"
       "'admissions',(SELECT count(*) FROM runner_runtime.admissions x WHERE x.owner_id=r.owner_id AND x.completed_at IS NULL),"
       "'outbox',(SELECT count(*) FROM runner_runtime.outbox o WHERE o.owner_id=r.owner_id AND NOT o.settled),"
       "'waiting',(SELECT count(*) FROM runner_runtime.registry w WHERE w.phase IN ('provisioning','capacity_wait')),"
       "'liveSessions',(SELECT count(*) FROM auth.sessions s WHERE s.account_id=r.owner_id AND s.expires_at>now())) "
       "FROM runner_runtime.registry r JOIN runner_bindings.bindings b ON b.owner_id=r.owner_id "
       "JOIN auth.accounts a ON a.id=r.owner_id WHERE r.owner_id='%s';" % OWNER)
LIVE = ("SELECT json_build_object('state',r.state_id,'phase',r.phase,'generation',r.generation,'revision',r.revision,"
        "'pod',r.pod_uid,'active',r.active_requests,'ambiguous',r.ambiguous,"
        "'outbox',(SELECT count(*) FROM runner_runtime.outbox o WHERE o.owner_id=r.owner_id AND NOT o.settled),"
        "'openStops',(SELECT count(*) FROM runner_termination.stops s WHERE s.owner_id=r.owner_id AND s.completed_at IS NULL)) "
        "FROM runner_runtime.registry r WHERE r.owner_id='%s';" % OWNER)
CONV = ("SELECT json_build_object('n',count(*),'digest',coalesce(md5(string_agg(id::text||':'||state||':'||version::text,"
        "',' ORDER BY id)),'-')) FROM research.conversations;")


def conversations(code):
    row = one('conv', CONV, code)
    return {'n': row.get('n'), 'digest': row.get('digest')}


# Precheck ---------------------------------------------------------------------------------------------
def precheck():
    if os.geteuid() != 0:
        raise Stop(3, 'precheck: not root')
    if os.path.isdir(EVIDENCE) and any(d.startswith('w4-') for d in os.listdir(EVIDENCE)):
        raise Stop(3, 'precheck: an earlier W4 run left evidence; never rerun, back checks')
    for ns, name, uid in (CTRL, API):
        d = kget(ns, 'deployment', name, 3)
        status = (d or {}).get('status') or {}
        if d is None or d['metadata']['uid'] != uid or d['spec'].get('replicas') != 1 or status.get('readyReplicas') != 1:
            raise Stop(3, 'precheck: %s is not the recorded Deployment %s at 1/1' % (name, uid))
    pods = kubectl(['-n', NS, 'get', 'pods', '-o', 'name'], 3, 'precheck: runner Pod lookup failed').split()
    if pods:
        raise Stop(3, 'precheck: a runner Pod exists in %s (%s)' % (NS, ', '.join(pods)))
    row = one('pre', PRE, 3)
    fence = {k: row.get(k) for k in FENCE}
    if fence != FENCE:
        raise Stop(3, 'precheck: …5804 fence is %s' % json.dumps(fence, sort_keys=True))
    plan = row.get('plan') or {}
    if (row.get('bindingState') != STATE or digest(plan) != PLAN_SHA or plan.get('image') != RUNNER_REF
            or (plan.get('executor') or {}).get('image') != EXECUTOR_REF):
        raise Stop(3, 'precheck: …5804 binding is not the W3b plan %s' % PLAN_SHA)
    blockers = {k: row.get(k) for k in ('allOpenStops', 'entries', 'admissions', 'outbox', 'waiting', 'liveSessions')}
    if any(blockers.values()):
        raise Stop(3, 'precheck: not quiet %s' % json.dumps(blockers, sort_keys=True))
    if row.get('role') not in ('editor', 'admin') or row.get('approved') is not True:
        raise Stop(3, 'precheck: …5804 account not an approved editor')
    origin = kubectl(['-n', 'm2-hosting', 'get', 'configmap', 'hosting-config', '-o',
                      'jsonpath={.data.AUTH_ORIGIN}|{.data.APP_BASE_PATH}'], 3, 'precheck: hosting-config lookup failed')
    if origin != ORIGIN + '|':
        raise Stop(3, 'precheck: API origin/base path is %s, not %s with an empty base path' % (origin, ORIGIN))
    for ref, want in ((RUNNER_REF, RUNNER_ID), (EXECUTOR_REF, EXECUTOR_ID)):
        rc, got = run([K3S, 'crictl', 'inspecti', '-o', 'go-template', '--template', '{{.status.id}}', ref])
        if rc != 0 or got != want:
            raise Stop(3, 'precheck: %s does not resolve to %s' % (ref, want))
    conv = conversations(3)
    say('precheck ok: …5804 %s plan %s; no runner Pod, no open stop, nothing pending or waiting; no live session; '
        'origin %s; targets resolve; conversations n=%s digest=%s'
        % (json.dumps(fence, sort_keys=True), PLAN_SHA[:12], ORIGIN, conv['n'], conv['digest']))
    return fence, conv


# Session ----------------------------------------------------------------------------------------------
SESSION = {}  # 'hash' (token hash), 'created' (bool or None = unknown), 'revoked'


def session_hash(token):
    return hashlib.sha256(('m2-origin-session-v1\0' + ORIGIN + '\0' + token).encode()).hexdigest()


def create_session():
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    for value in (token, csrf):
        if not re.fullmatch(r'[A-Za-z0-9_-]{40,}', value):
            raise Stop(4, 'session: token generation failed')
    SESSION.update(hash=session_hash(token), created=None)
    try:
        row = one('insert', "WITH s AS (INSERT INTO auth.sessions(token_hash,account_id,csrf_token,expires_at) VALUES "
                  "('%s','%s','%s',now()+interval '1 hour') RETURNING token_hash,expires_at) SELECT json_build_object("
                  "'id',left(token_hash,16),'expires',expires_at) FROM s;" % (SESSION['hash'], OWNER, csrf), 4, write=True)
    except Stop:
        raise Stop(4, 'session: insert failed or its result is unknown (checked and removed below); no POST made')
    if row.get('id') != SESSION['hash'][:16]:
        raise Stop(4, 'session: insert answered another row')
    SESSION['created'] = True
    say('session created: id %s, expires %s (1 hour), owner …5804' % (row['id'], row.get('expires')))
    return token, csrf


def revoke_session():
    """Delete exactly this run's row (token hash and owner). True when no such row remains."""
    if not SESSION.get('hash') or SESSION.get('created') is False:
        return True
    gone = False
    try:
        row = one('revoke', "WITH d AS (DELETE FROM auth.sessions WHERE token_hash='%s' AND account_id='%s' RETURNING 1) "
                  "SELECT json_build_object('deleted',count(*)) FROM d;" % (SESSION['hash'], OWNER), 70, write=True)
        left = one('session', "SELECT json_build_object('n',count(*)) FROM auth.sessions WHERE token_hash='%s';"
                   % SESSION['hash'], 70)
        gone = left.get('n') == 0
        say('session %s revoked: deleted %s row(s); remaining %s' % (SESSION['hash'][:16], row.get('deleted'), left.get('n')))
    except Exception:
        say('session %s: revoke not proved (the row expires within the hour)' % SESSION['hash'][:16])
    SESSION['revoked'] = gone
    return gone


# POST -------------------------------------------------------------------------------------------------
def post(token, csrf, key):
    """The single POST. (status, sanitized body) or (None, None) when the outcome is unknown."""
    connection = http.client.HTTPConnection(API_HOST, API_PORT, timeout=POST_TIMEOUT)
    try:
        connection.request('POST', PATH, body=b'{}', headers={
            'Host': HOST_HEADER, 'Cookie': 'anxious_session=' + token, 'x-csrf-token': csrf,
            'Idempotency-Key': key, 'Content-Type': 'application/json', 'Accept': 'application/json'})
        response = connection.getresponse()
        status, raw = response.status, response.read(65536)
    except Exception:
        return None, None
    finally:
        try:
            connection.close()
        except Exception:
            pass
    try:
        body = json.loads(raw)
        safe = {k: body.get(k) for k in ('available', 'reason', 'retryAfterSeconds', 'verification')} \
            if isinstance(body, dict) and 'error' not in body else {'error': ((body or {}).get('error') or {}).get('code')}
    except Exception:
        safe = {'body': 'unreadable'}
    return status, safe


def live(code):
    return one('live', LIVE, code)


# Pod --------------------------------------------------------------------------------------------------
def wait_pod(started):
    """Poll read-only until the Pod is proved Running for registry generation 4, or POD_WAIT has passed."""
    deadline = started + POD_WAIT
    seen = {}
    while True:
        try:
            state = live(7)
            pod = kget(NS, 'pod', POD_NAME, 7)
        except Stop:
            state, pod = {'lookup': 'failed'}, None
        seen = {'registry': state, 'pod': None if pod is None else pod['metadata'].get('uid')}
        if (pod is not None and state.get('generation') == NEW_GENERATION and state.get('state') == STATE
                and state.get('pod') == pod['metadata']['uid']
                and (pod['metadata'].get('labels') or {}).get('anxious.io/generation') == str(NEW_GENERATION)
                and (pod.get('status') or {}).get('phase') == 'Running' and not pod['metadata'].get('deletionTimestamp')):
            statuses = {s.get('name'): s for s in (pod.get('status') or {}).get('containerStatuses', [])}
            if all('running' in ((statuses.get(n) or {}).get('state') or {}) for n in CONTAINERS):
                return pod, state
        if time.monotonic() >= deadline:
            say('pod not proved Running within %d s: %s' % (POD_WAIT, json.dumps(seen, sort_keys=True, default=str)))
            raise Stop(7, 'the POST was accepted but the Pod is not proved Running; provisioning may continue; no retry, '
                          'no reclaim; back checks read-only')
        time.sleep(min(5, max(0.2, deadline - time.monotonic())))


def crictl_json(args):
    rc, out = run([K3S, 'crictl'] + args + ['-o', 'json'])
    if rc != 0:
        return None
    try:
        return json.loads(out)
    except ValueError:
        return None


def prove(pod):
    """Problems with the instance, its images and its token mount (empty list = proved)."""
    problems = []
    uid = pod['metadata']['uid']
    spec, status = pod.get('spec') or {}, pod.get('status') or {}
    labels = pod['metadata'].get('labels') or {}
    if (labels.get('anxious.io/owner'), labels.get('anxious.io/state')) != (OWNER, STATE):
        problems.append('owner/state labels')
    if [c.get('name') for c in spec.get('containers', [])] != list(CONTAINERS):
        problems.append('containers are %s' % [c.get('name') for c in spec.get('containers', [])])
    if [c.get('name') for c in spec.get('initContainers', [])] != list(INIT):
        problems.append('init containers are %s' % [c.get('name') for c in spec.get('initContainers', [])])
    for c in spec.get('containers', []) + spec.get('initContainers', []):
        want = (CONTAINERS.get(c.get('name')) or (INIT.get(c.get('name')),))[0]
        if c.get('image') != want:
            problems.append('%s spec image %s' % (c.get('name'), c.get('image')))
    statuses = {s.get('name'): s for s in status.get('containerStatuses', [])}
    for name, (ref, config) in CONTAINERS.items():
        s = statuses.get(name) or {}
        if s.get('imageID') != ref:
            problems.append('%s imageID %s (format not the observed repo@manifest reference)' % (name, s.get('imageID')))
        if s.get('restartCount') != 0 or 'running' not in (s.get('state') or {}):
            problems.append('%s not running with 0 restarts' % name)
        match = re.fullmatch(r'containerd://([0-9a-f]{64})', s.get('containerID') or '')
        if not match:
            problems.append('%s containerID format %s' % (name, s.get('containerID')))
            continue
        info = crictl_json(['inspect', match.group(1)])
        cri = (info or {}).get('status') or {}
        cri_labels = cri.get('labels') or {}
        if cri.get('imageRef') != config:
            problems.append('%s CRI imageRef %s, want %s' % (name, cri.get('imageRef'), config))
        if (cri_labels.get('io.kubernetes.pod.uid'), cri_labels.get('io.kubernetes.pod.namespace'),
                cri_labels.get('io.kubernetes.container.name')) != (uid, NS, name):
            problems.append('%s CRI container labels are another instance' % name)
        sandbox = ((info or {}).get('info') or {}).get('sandboxID')
        box = (crictl_json(['inspectp', sandbox]) or {}).get('status') if sandbox else None
        if ((box or {}).get('metadata') or {}).get('uid') != uid:
            problems.append('%s sandbox is not this Pod' % name)
        rc, image = run([K3S, 'crictl', 'inspecti', '-o', 'go-template', '--template', '{{.status.id}}', s.get('imageID')])
        if rc != 0 or image != config:
            problems.append('%s inspecti(imageID) %s, want %s' % (name, image or 'none', config))
    inits = {s.get('name'): s for s in status.get('initContainerStatuses', [])}
    for name, ref in INIT.items():
        s = inits.get(name) or {}
        if s.get('imageID') != ref or ((s.get('state') or {}).get('terminated') or {}).get('exitCode') != 0:
            problems.append('init %s not exited 0 on %s' % (name, ref))
    if spec.get('automountServiceAccountToken') is not False:
        problems.append('automountServiceAccountToken is not false')
    volumes = {v.get('name'): v for v in spec.get('volumes', [])}
    if any(n.startswith('kube-api-access') for n in volumes):
        problems.append('a kube-api-access volume exists')
    broker = (volumes.get('broker-token') or {}).get('projected') or {}
    sources = broker.get('sources') or []
    if (len(sources) != 1 or set(sources[0]) != {'serviceAccountToken'}
            or sources[0]['serviceAccountToken'].get('audience') != BROKER_AUDIENCE
            or sources[0]['serviceAccountToken'].get('path') != 'token'):
        problems.append('broker-token is not exactly one %s-audience projected token' % BROKER_AUDIENCE)
    for c in spec.get('containers', []) + spec.get('initContainers', []):
        for m in c.get('volumeMounts', []):
            if m.get('mountPath', '').startswith('/var/run/secrets/kubernetes.io'):
                problems.append('%s mounts a service-account path' % c.get('name'))
            if m.get('name') == 'broker-token' and (c.get('name') != 'runner' or m.get('mountPath') != '/run/codex-broker'
                                                     or m.get('readOnly') is not True):
                problems.append('broker-token mounted other than read-only at runner:/run/codex-broker')
    if not any(m.get('name') == 'broker-token' for c in spec.get('containers', []) if c.get('name') == 'runner'
               for m in c.get('volumeMounts', [])):
        problems.append('runner has no broker-token mount')
    return problems


def times(pod):
    status = pod.get('status') or {}
    started = {s.get('name'): ((s.get('state') or {}).get('running') or {}).get('startedAt')
               for s in status.get('containerStatuses', [])}
    return status.get('startTime'), started


def early_health(pod_uid):
    script = HEALTH_PROBE % (OWNER, pod_uid, HEALTH_KEYS)
    rc, out = run([K3S, 'kubectl', '-n', CTRL[0], 'exec', '-i', 'deploy/' + CTRL[1], '-c', 'controller', '--',
                   'python', '-B', '-'], 90, script.encode())
    lines = [l for l in out.splitlines() if l.startswith('{')]
    try:
        value = json.loads(lines[-1]) if lines else None
    except ValueError:
        value = None
    if rc != 0 or not isinstance(value, dict) or value.get('probe') != 'health':
        return None
    return {k: value.get(k) for k in HEALTH_KEYS}


def ledger():
    rc, out = run([K3S, 'kubectl', '-n', NS, 'exec', POD_NAME, '-c', 'runner', '--', 'python', '-I', '-c', LEDGER_PROBE], 60)
    line = out.splitlines()[-1] if out else ''
    return line if rc == 0 and (line.startswith('ledger ')) else 'ledger probe failed'


# Main -------------------------------------------------------------------------------------------------
def main():
    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    code, message, ev, posted = 0, '', None, False
    try:
        try:
            fence, conv = precheck()
            ev = os.path.join(EVIDENCE, 'w4-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()))
            os.makedirs(EVIDENCE, mode=0o700, exist_ok=True)
            os.mkdir(ev, 0o700)
            say('evidence: %s' % ev)
            record(ev, started=now(), before={'fence': fence, 'conversations': conv})
            token, csrf = create_session()
            record(ev, session={'id': SESSION['hash'][:16]})
            key = str(uuid.uuid4())
            say('POST %s (Idempotency-Key %s) at %s' % (PATH, key, now()))
            posted = True  # from here on, never a second POST
            record(ev, post={'idempotencyKey': key, 'at': now()})
            started = time.monotonic()
            status, body = post(token, csrf, key)
            token = csrf = None
            record(ev, post={'idempotencyKey': key, 'status': status, 'body': body})
            revoke_session()
            record(ev, session={'id': SESSION['hash'][:16], 'revoked': SESSION.get('revoked')})
            if status is None:
                raise Stop(6, 'POST outcome unknown (timeout or connection lost): never repeated; back checks read-only')
            say('POST answered %d %s' % (status, json.dumps(body, sort_keys=True)))
            if status not in (200, 202):
                raise Stop(5, 'POST refused with %d: no runner requested by this run; back checks read-only' % status)
            pod, state = wait_pod(started)
            uid = pod['metadata']['uid']
            start, containers = times(pod)
            deadline = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(
                calendar.timegm(time.strptime(start, '%Y-%m-%dT%H:%M:%SZ')) + DEADLINE_MINUTES * 60)) if start else None
            say('pod %s uid %s Running; registry %s; pod start %s; containers %s; deadline %s'
                % (POD_NAME, uid, json.dumps(state, sort_keys=True), start, json.dumps(containers, sort_keys=True),
                   deadline))
            record(ev, pod={'uid': uid, 'start': start, 'containers': containers, 'deadline': deadline,
                            'registry': state})
            problems = prove(pod)
            if problems:
                record(ev, proof={'ok': False, 'problems': problems})
                raise Stop(8, 'instance/image proof failed: %s; the Pod is left as it is, PM decides' % '; '.join(problems))
            say('proof ok: spec images and status imageIDs are the approved references; each containerID resolves '
                'through CRI to this Pod (labels and sandbox) with imageRef %s / %s; init prepare-shell exited 0; '
                'automount false, only the %s-audience token at runner:/run/codex-broker'
                % (RUNNER_ID[:19], EXECUTOR_ID[:19], BROKER_AUDIENCE))
            record(ev, proof={'ok': True})
            health = early_health(uid)
            r1 = None if health is None else (health.get('available') is False and health.get('failure') == 'auth_revoked'
                                              and (health.get('ownerId'), health.get('stateId'), health.get('podUid'))
                                              == (OWNER, STATE, uid))
            say('early health %s: %s; r1_state=%s' % (now(), json.dumps(health, sort_keys=True) if health else 'not observed',
                                                     'unobserved' if r1 is None else str(r1).lower()))
            record(ev, health={'at': now(), 'value': health, 'r1State': r1})
            line = ledger()
            say('5804 %s (retained-ledger metadata, context only)' % line)
            after = conversations(9)
            say('conversations n=%s digest=%s (%s)' % (after['n'], after['digest'],
                                                      'unchanged' if after == conv else 'CHANGED'))
            record(ev, ledger=line, after={'conversations': after})
            if r1 is False:
                raise Stop(9, 'early health is not the R1 state; the runner is left running; PM decides')
            say('next: back observes read-only until the stop or the deadline %s; no action by this script' % deadline)
        except Stop as stop:
            code, message = stop.code, stop.message
        except Interrupted:
            code, message = (6, 'interrupted after the POST began') if posted else (130, 'interrupted before the POST')
        except Exception:
            code, message = (6, 'unexpected error after the POST began') if posted else (3, 'unexpected error')
    except BaseException:
        if not message:
            code, message = (6 if posted else 130), 'interrupted'
    return finish(code, message, ev, posted)


def finish(code, message, ev, posted):
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, signal.SIG_IGN)
        except Exception:
            pass
    if message:
        say('STOPPED (%d): %s' % (code, message))
    revoked = True
    if SESSION.get('hash') and 'revoked' not in SESSION:
        if SESSION.get('created') is None:
            # The insert's result was unknown: look for the row; if it exists (or the lookup fails) delete it.
            try:
                found = one('session', "SELECT json_build_object('n',count(*)) FROM auth.sessions WHERE "
                            "token_hash='%s';" % SESSION['hash'], 4).get('n')
                SESSION['created'] = {0: False, 1: True}.get(found)
            except Exception:
                SESSION['created'] = None
            say('session insert result was unknown: row %s' % {True: 'found', False: 'absent'}.get(SESSION['created'],
                                                                                              'unknown'))
        revoked = revoke_session()
    elif SESSION.get('hash'):
        revoked = SESSION.get('revoked') is True
    if posted and code in (5, 6, 7):
        try:
            say('live (read-only): %s' % json.dumps(live(code), sort_keys=True, default=str))
            pod = kget(NS, 'pod', POD_NAME, code)
            say('pod: %s' % ('absent' if pod is None else '%s %s' % (pod['metadata'].get('uid'),
                                                                     (pod.get('status') or {}).get('phase'))))
        except Exception:
            say('live state lookup failed')
    if not revoked:
        say('SESSION NOT PROVED REVOKED: id %s' % SESSION.get('hash', '')[:16])
        if code == 0:
            code = 70
    try:
        record(ev, result=code, finished=now(), session={'id': SESSION.get('hash', '')[:16],
                                                          'revoked': SESSION.get('revoked')})
    except Exception:
        pass
    say('result rc=%d' % code)
    return code


if __name__ == '__main__':
    sys.exit(main())
