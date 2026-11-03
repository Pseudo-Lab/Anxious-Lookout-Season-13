"""#61 dev-VM gate W3b: move the bindings of …0048 and …5804 to the #61 runner and shell-executor images with
the reviewed `ops.runner_binding_image` tool. Run once by the user as root in the guest:

    python3 -I /root/issue61/w3b-binding.py

Inputs: w3b-expected.json next to this script (review-approved, sha256 EXPECTED_SHA). Per owner it holds the
tool inputs (owner, state, generation, revision, oldPlanSha256, newImage, executorImage) and the exact report
the tool must print for them.

1. precheck (read-only; nothing changes): expected file hash and consistency; no earlier W3b evidence; host units
   stopped and no guest container; the tool image (guest Docker, by verified Id) and its two module hashes; the
   target digest references resolve in k3s to the W1 image IDs; api and runner-controller are the recorded
   Deployments (UID), 1/1 ready; no runner Pod; per owner the exact registry fence, no pending work (outbox,
   entries, stops, admissions), a completed proof for the current state/generation, binding on the same state,
   plan digest == oldPlanSha256 and plan images == the report's old images.
2. window (proved, not inferred): runner-controller then api scaled to 0 by UID-preconditioned patch; their Pods
   gone; both Deployments the same UIDs with replicas 0; NO client session left on database `hosting`
   (pg_stat_activity, any role, our own psql excluded); host units still stopped, no guest container, no runner
   Pod; every per-owner value of step 1 unchanged.
3. admin DSN: Secret m2-hosting/hosting-ops `database-url`, host replaced by the postgres-0 Pod IP, written
   O_EXCL root 0600 in the run's private directory (0700). Never printed; removed on every exit path.
4. report, both owners, before any write: the tool's `report` per owner (separate container, own report
   directory); each must equal the approved report exactly. Any refusal or difference: no apply at all (rc 5).
5. apply, …0048 then …5804, one tool transaction each: before each, no client session on `hosting`; the
   tool's `apply` with that owner's report mounted read-only. After each, the plan digest is read: the next
   owner runs only if this one is confirmed (tool rc 0) AND the digest is the approved new one.
6. postcheck: per owner the plan digest (old / new / other), new plans carry the target images, registry fences
   unchanged.
7. restore (always, once a Deployment was touched; after every tool container is proved gone): api then
   runner-controller back to their recorded replicas by UID, rollout status, ready with the same UID,
   /api/health 200, admin DSN removed and absent.

Evidence: /root/issue61/evidence/w3b-<UTC stamp>/ (root 0700): run.json (milestones, per-owner outcome),
before-plans.json, the tool reports, private/ (inputs; the admin DSN only while the run lasts).

rc (never rerun on a nonzero rc; back checks read-only, the evidence and plan digests tell what happened):
  0  both bindings moved (tool confirmed, digests approved new), fences unchanged, restored
  3  precheck refused: nothing changed
  4  window not proved, or the admin DSN could not be written: no tool ran, no binding changed; restored
  5  a guest report was refused or differs from the approved one: no apply ran; restored
  6  the apply phase began but no binding changed (…0048's apply did not commit, or a check stopped the phase
     before it; the STOPPED line names it); restored
  7  partial: …0048 moved, …5804 still old (not attempted, or its apply did not commit); restored
  (After the apply phase began, rc 0/6/7/8/9 come from the live plan digests and tool confirmations, so they
  describe the bindings even when a check or an interrupt stopped the phase; the STOPPED line says why.)
  8  a plan digest is neither old nor approved new or unreadable, or a fence changed: back checks
  9  both moved but a tool apply was not confirmed (timeout / interrupt / lost answer); restored
  70 the main result was 0 but a restore item failed (printed)
  80 a tool container was not proved stopped: Deployments LEFT AT 0, admin DSN removed; manual recovery via PM
  130 interrupted before any apply: no binding changed; restored
A main-path code is kept when restore also fails; "RESTORE FAILED: …" is always printed.
Output is fixed messages, states, UIDs and digests only: no DSN, password, Secret data or command error text.
"""
import base64
import hashlib
import ipaddress
import json
import os
import signal
import stat
import subprocess
import sys
import time
from urllib.parse import urlsplit, urlunsplit

HERE = os.path.dirname(os.path.abspath(__file__))
EXPECTED_FILE = os.path.join(HERE, 'w3b-expected.json')
EXPECTED_SHA = 'cc1049a79b2e9deca166c7314b30c00c067f383609e07172b061a9881b51aa7d'
OWNERS = ('00000000-0000-4000-8000-000000000048', '00000000-0000-4000-8000-000000005804')  # apply order
RUNNER_REF = 'docker.io/library/anxious-dev-runner@sha256:067c77de5d6b3673ece9b243f7753a9184a007cd44ab0ea1c93dc3044458a5e4'
EXECUTOR_REF = ('docker.io/library/anxious-dev-shell-executor@sha256:'
                '9c87d626cdf6798db5bc5a562cc68af1b13a36c8f90d3738e19c6553e7cf4d12')
TARGET_IDS = {RUNNER_REF: 'sha256:4cac37e2173d5aec7b351e05c2977f2e0df76670f3befb91ce54306d2444e8ad',
              EXECUTOR_REF: 'sha256:49b449cb31c5b344f876a9d9aee2d97dd3dcae906c3324bc0eaf00678dd6b883'}
TOOL_TAG = 'anxious-dev-runtime:61-5e73bc4'
# Guest Docker (containerd store) reports the manifest digest as Id; a classic store reports the config ID. Both
# name exactly the W1-imported runtime image (W2's established check). The tool runs by this verified Id.
TOOL_IDS = {'sha256:eb3a18acd1b2856f6a95c7686c0c0ee4e5194b6237266b3443664f37feb3377c',
            'sha256:80af8c59f978f7a7d59d02f71985f3d456ce2e8011ed2d534395297188830771'}
MODULES = {'/app/ops/runner_binding_image.py': '77cfc4dad550efdce270d7f7f852f1e4730a65b3eed550d4a2ca147528273778',
           '/app/app/runner_bindings.py': '0cc5706f8db9193d1b1ff61dac46533a62a09eb951dfff39f2d2b2d0160e5342'}
# (namespace, name, recorded UID, Pod label); runner-controller is scaled first and restored last.
CTRL = ('runner-control', 'runner-controller', '41564f26-95e7-4ad1-8af3-d73b93c4669a', 'app=runner-controller')
API = ('m2-hosting', 'api', 'fad6799a-6df5-4086-812f-cfae6212adfa', 'app=hosting-api')
UNITS = ['issue16-controller.service', 'issue16-observer.service', 'issue16-supply.service',
         'issue16-controller.timer', 'issue16-observer.timer', 'issue16-supply.timer', 'issue16-restore.timer']
EVIDENCE = '/root/issue61/evidence'
K3S = '/usr/local/bin/k3s'
DOCKER = '/usr/bin/docker'
SYSTEMCTL = '/usr/bin/systemctl'
CURL = '/usr/bin/curl'
HEALTH = ['-s', '-o', '/dev/null', '-w', '%{http_code}', '--max-time', '10',
          '--resolve', '192.88.99.1:80:10.0.0.107', 'http://192.88.99.1/api/health']
POD_CIDR = ipaddress.ip_network('10.42.0.0/16')
SCHEMES = {'postgresql', 'postgres', 'postgresql+psycopg', 'postgresql+psycopg2'}
REPORT_KEYS = {'owner', 'state', 'generation', 'revision', 'oldImage', 'newImage', 'oldPlanSha256', 'newPlanSha256',
               'oldExecutorImage', 'newExecutorImage'}
INPUT_KEYS = {'owner', 'state', 'generation', 'revision', 'oldPlanSha256', 'newImage', 'executorImage'}
# The harness may only shorten these.
WAIT = max(1, min(120, int(os.environ.get('W3B_WAIT_SECONDS', 120))))
TOOL_TIMEOUT = max(1, min(300, int(os.environ.get('W3B_TOOL_TIMEOUT', 300))))


class Stop(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


class Interrupted(BaseException):
    """SIGINT/SIGTERM; a BaseException so command wrappers do not turn it into a command failure."""


def interrupted(signum, frame):
    raise Interrupted()


def say(text):
    print(text, flush=True)


def short(owner):
    return owner[-4:]


def run(cmd, timeout=60, stdin=None):
    """(rc, stdout) of a fixed command; 124 on timeout, 125 if it could not start. Output is never echoed raw."""
    try:
        done = subprocess.run(cmd, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=max(1, timeout), check=False)
    except subprocess.TimeoutExpired:
        return 124, ''
    except Exception:
        return 125, ''
    return done.returncode, done.stdout.decode('utf-8', 'replace').strip()


def must(cmd, code, message, timeout=60, stdin=None):
    rc, out = run(cmd, timeout, stdin)
    if rc != 0:
        raise Stop(code, message)
    return out


def kubectl(args, code, message, timeout=60):
    return must([K3S, 'kubectl'] + args, code, message, timeout)


def rows(tag, query, code):
    """JSON rows (one json_build_object per line) of a READ ONLY query as postgres in postgres-0."""
    text = 'BEGIN READ ONLY;\n/*w3b:%s*/ %s\nCOMMIT;\n' % (tag, query)
    out = must([K3S, 'kubectl', '-n', 'm2-hosting', 'exec', '-i', 'postgres-0', '--', 'psql', '-U', 'postgres', '-d',
                'hosting', '-Atq', '-v', 'ON_ERROR_STOP=1', '-f', '-'], code, 'database lookup failed (%s)' % tag,
               60, text.encode())
    try:
        parsed = [json.loads(line) for line in out.splitlines() if line.strip()]
    except ValueError:
        raise Stop(code, 'database lookup unreadable (%s)' % tag) from None
    if not all(isinstance(row, dict) for row in parsed):
        raise Stop(code, 'database lookup unreadable (%s)' % tag)
    return parsed


def digest(plan):
    """ops.runner_binding_image.plan_digest: sha256 of the sorted, compact JSON."""
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


OWNER_LIST = ','.join("'%s'" % o for o in OWNERS)


def owners_state(code):
    """Per owner: registry fence, binding state and plan, pending work and the current-generation proof."""
    found = rows('state', "SELECT json_build_object('owner',r.owner_id,'state',r.state_id,'phase',r.phase,"
                 "'generation',r.generation,'revision',r.revision,'pod',r.pod_uid,'active',r.active_requests,"
                 "'ambiguous',r.ambiguous,'bindingState',b.state_id,'plan',b.plan,"
                 "'outbox',(SELECT count(*) FROM runner_runtime.outbox o WHERE o.owner_id=r.owner_id AND NOT o.settled),"
                 "'entries',(SELECT count(*) FROM runner_termination.entries e WHERE e.owner_id=r.owner_id "
                 "AND e.consumed_at IS NULL),"
                 "'stops',(SELECT count(*) FROM runner_termination.stops s WHERE s.owner_id=r.owner_id "
                 "AND s.completed_at IS NULL),"
                 "'admissions',(SELECT count(*) FROM runner_runtime.admissions a WHERE a.owner_id=r.owner_id "
                 "AND a.completed_at IS NULL),"
                 "'proof',(SELECT count(*) FROM runner_termination.stops s WHERE s.owner_id=r.owner_id "
                 "AND s.state_id=r.state_id AND s.generation=r.generation AND s.completed_at IS NOT NULL "
                 "AND s.proof IS NOT NULL)) FROM runner_runtime.registry r JOIN runner_bindings.bindings b "
                 "ON b.owner_id=r.owner_id WHERE r.owner_id IN (%s) ORDER BY r.owner_id;" % OWNER_LIST, code)
    by = {row.get('owner'): row for row in found}
    if sorted(by) != sorted(OWNERS) or len(found) != len(OWNERS):
        raise Stop(code, 'registry/binding rows for the two owners not found exactly once')
    for row in found:
        if not isinstance(row.get('plan'), dict):
            raise Stop(code, 'binding plan unreadable')
    return by


def fence_of(row):
    return {k: row.get(k) for k in ('state', 'phase', 'generation', 'revision', 'pod', 'active', 'ambiguous')}


def clients(code):
    """Client sessions on database hosting other than this query's own psql."""
    found = rows('clients', "SELECT json_build_object('clients',count(*),'roles',coalesce(string_agg(DISTINCT "
                 "usename::text,',' ORDER BY usename::text),'')) FROM pg_stat_activity WHERE datname='hosting' AND "
                 "backend_type='client backend' AND pid<>pg_backend_pid();", code)
    if len(found) != 1 or not isinstance(found[0].get('clients'), int):
        raise Stop(code, 'session lookup unreadable')
    return found[0]['clients'], found[0].get('roles') or ''


def deployment(spec, code):
    ns, name, uid, _ = spec
    out = kubectl(['-n', ns, 'get', 'deployment', name, '-o', 'json'], code, '%s lookup failed' % name)
    try:
        d = json.loads(out)
        status = d.get('status') or {}
        return (d['metadata']['uid'], int(d['spec']['replicas']), int(status.get('readyReplicas') or 0),
                int(status.get('replicas') or 0))
    except Exception:
        raise Stop(code, '%s unreadable' % name) from None


def pods(ns, label, code):
    args = ['-n', ns, 'get', 'pods', '-o', 'name'] + (['-l', label] if label else [])
    return kubectl(args, code, 'pod lookup failed (%s)' % ns).split()


def host_quiet(code):
    for unit in UNITS:
        _, active = run([SYSTEMCTL, 'is-active', unit])  # rc 3 is normal for inactive units
        if active not in ('inactive', 'failed'):
            raise Stop(code, 'host unit %s is %s' % (unit, active or 'unreadable'))
    rc, names = run([DOCKER, 'ps', '-a', '-q'])
    if rc != 0:
        raise Stop(code, 'docker ps failed')
    if names:
        raise Stop(code, 'a guest container exists')
    if pods('codex-runner', None, code):
        raise Stop(code, 'a runner Pod exists')


# Precheck ---------------------------------------------------------------------------------------------
def load_expected():
    try:
        info = os.lstat(EXPECTED_FILE)
        with open(EXPECTED_FILE, 'rb') as stream:
            data = stream.read()
    except Exception:
        raise Stop(3, 'w3b-expected.json missing') from None
    if not stat.S_ISREG(info.st_mode) or hashlib.sha256(data).hexdigest() != EXPECTED_SHA:
        raise Stop(3, 'w3b-expected.json is not the approved file')
    doc = json.loads(data)
    if sorted(doc) != sorted(OWNERS):
        raise Stop(3, 'w3b-expected.json owners differ')
    expected = {}
    for owner in OWNERS:
        inputs, report = doc[owner]['inputs'], doc[owner]['expectedToolReport']
        if set(inputs) != INPUT_KEYS or set(report) != REPORT_KEYS:
            raise Stop(3, 'w3b-expected.json fields differ')
        if (inputs['owner'] != owner or inputs['newImage'] != RUNNER_REF or inputs['executorImage'] != EXECUTOR_REF
                or report['newImage'] != RUNNER_REF or report['newExecutorImage'] != EXECUTOR_REF
                or any(report[k] != inputs[k] for k in ('owner', 'state', 'generation', 'revision', 'oldPlanSha256'))):
            raise Stop(3, 'w3b-expected.json inputs and report disagree')
        expected[owner] = (inputs, report)
    return expected


def check_owners(expected, code, where):
    by = owners_state(code)
    for owner in OWNERS:
        inputs, report = expected[owner]
        row, plan = by[owner], by[owner]['plan']
        want = {'state': inputs['state'], 'phase': 'idle_stopped', 'generation': inputs['generation'],
                'revision': inputs['revision'], 'pod': None, 'active': 0, 'ambiguous': False}
        if fence_of(row) != want:
            raise Stop(code, '%s: …%s fence is %s' % (where, short(owner), json.dumps(fence_of(row), sort_keys=True)))
        if row['bindingState'] != inputs['state']:
            raise Stop(code, '%s: …%s binding is on another state' % (where, short(owner)))
        pending = {k: row[k] for k in ('outbox', 'entries', 'stops', 'admissions')}
        if any(pending.values()):
            raise Stop(code, '%s: …%s has pending work %s' % (where, short(owner), json.dumps(pending, sort_keys=True)))
        if row['proof'] < 1:
            raise Stop(code, '%s: …%s has no completed proof for its current generation' % (where, short(owner)))
        if digest(plan) != inputs['oldPlanSha256']:
            raise Stop(code, '%s: …%s plan digest %s is not the approved old one' % (where, short(owner), digest(plan)))
        if plan.get('image') != report['oldImage'] or (plan.get('executor') or {}).get('image') != report['oldExecutorImage']:
            raise Stop(code, '%s: …%s plan images are not the approved old ones' % (where, short(owner)))
    return by


def precheck(expected):
    if os.geteuid() != 0:
        raise Stop(3, 'precheck: not root')
    if os.path.isdir(EVIDENCE) and any(d.startswith('w3b-') for d in os.listdir(EVIDENCE)):
        raise Stop(3, 'precheck: an earlier W3b run left evidence; never rerun, back checks')
    host_quiet(3)
    image = must([DOCKER, 'image', 'inspect', '--format', '{{.Id}}', TOOL_TAG], 3, 'precheck: tool image lookup failed')
    if image not in TOOL_IDS:
        raise Stop(3, 'precheck: guest Docker %s is %s, not the W1 runtime image' % (TOOL_TAG, image or 'none'))
    out = must([DOCKER, 'run', '--rm', '--name', 'issue61-w3b-modules-%d' % os.getpid(), '--pull=never', '--network',
                'none', '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--user', '0:0',
                '--entrypoint', 'sha256sum', image] + sorted(MODULES), 3, 'precheck: module hash run failed', 60)
    seen = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2:
            seen[parts[1]] = parts[0]
    if seen != MODULES:
        raise Stop(3, 'precheck: tool image modules are not the reviewed ones')
    for ref, want in sorted(TARGET_IDS.items()):
        rc, got = run([K3S, 'crictl', 'inspecti', '-o', 'go-template', '--template', '{{.status.id}}', ref])
        if rc not in (0, 1) or got != want:
            raise Stop(3, 'precheck: %s does not resolve to %s' % (ref, want))
    replicas = {}
    for spec in (CTRL, API):
        uid, want, ready, _ = deployment(spec, 3)
        if uid != spec[2] or want != 1 or ready != 1:
            raise Stop(3, 'precheck: %s is uid=%s replicas=%d ready=%d (want %s 1/1)' % (spec[1], uid, want, ready, spec[2]))
        replicas[spec[1]] = want
    by = check_owners(expected, 3, 'precheck')
    count, roles = clients(3)
    host_quiet(3)  # also after the module-hash container
    say('precheck ok: tool image %s, modules ok; targets resolve; %s %s and %s %s 1/1; sessions now %d (%s)'
        % (image, CTRL[1], CTRL[2], API[1], API[2], count, roles or 'none'))
    for owner in OWNERS:
        say('  …%s fence %s plan %s' % (short(owner), json.dumps(fence_of(by[owner]), sort_keys=True),
                                       digest(by[owner]['plan'])))
    return image, replicas, by


# Evidence ---------------------------------------------------------------------------------------------
RUN = {}


def record(ev, **items):
    RUN.update(items)
    path = os.path.join(ev, 'run.json')
    temp = path + '.tmp'
    with open(temp, 'w') as stream:
        json.dump(RUN, stream, sort_keys=True)
    os.replace(temp, path)


def private_dir(path):
    os.mkdir(path, 0o700)
    os.chmod(path, 0o700)
    return path


def write_new(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        if os.write(fd, data) != len(data):
            raise OSError
        os.fsync(fd)
    finally:
        os.close(fd)


# Window -----------------------------------------------------------------------------------------------
TOUCHED = []  # Deployments this run patched (restore covers exactly these)


def scale(spec, replicas, code):
    ns, name, uid, _ = spec
    patch = {'metadata': {'uid': uid}, 'spec': {'replicas': replicas}}
    kubectl(['-n', ns, 'patch', 'deployment', name, '--type=merge', '-p', json.dumps(patch)], code,
            '%s scale to %d failed' % (name, replicas))


def gone(spec, code):
    ns, name, _, label = spec
    deadline = time.monotonic() + WAIT
    while True:
        left = pods(ns, label, code)
        if not left:
            return
        if time.monotonic() >= deadline:
            raise Stop(code, 'window: %s Pods still present after %d s' % (name, WAIT))
        time.sleep(min(3, max(0.1, deadline - time.monotonic())))


def window(expected, before):
    for spec in (CTRL, API):
        TOUCHED.append(spec)
        scale(spec, 0, 4)
        gone(spec, 4)
    for spec in (CTRL, API):
        uid, want, _, current = deployment(spec, 4)
        if uid != spec[2] or want != 0 or current != 0:
            raise Stop(4, 'window: %s is uid=%s replicas=%d/%d' % (spec[1], uid, want, current))
    count, roles = clients(4)
    if count:
        raise Stop(4, 'window: %d client session(s) on hosting remain (%s)' % (count, roles))
    host_quiet(4)
    by = check_owners(expected, 4, 'window')
    if any(fence_of(by[o]) != fence_of(before[o]) for o in OWNERS):
        raise Stop(4, 'window: a fence changed')
    say('window ok: %s and %s at 0 with the same UIDs, their Pods gone; 0 client sessions on hosting; '
        'host units stopped, no guest container, no runner Pod; fences and plans unchanged' % (CTRL[1], API[1]))


def admin_dsn(path):
    ip = kubectl(['-n', 'm2-hosting', 'get', 'pod', 'postgres-0', '-o', 'jsonpath={.status.podIP}'], 4,
                 'admin DSN: postgres Pod lookup failed')
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        raise Stop(4, 'admin DSN: postgres Pod IP invalid') from None
    if address.version != 4 or address not in POD_CIDR:
        raise Stop(4, 'admin DSN: postgres Pod IP invalid')
    encoded = kubectl(['-n', 'm2-hosting', 'get', 'secret', 'hosting-ops', '-o',
                       'go-template={{index .data "database-url"}}'], 4, 'admin DSN: Secret lookup failed')
    try:
        url = urlsplit(base64.b64decode(encoded, validate=True).decode('ascii').strip())
        port = url.port
    except Exception:
        raise Stop(4, 'admin DSN: invalid') from None
    database = url.path[1:] if url.path.startswith('/') else ''
    if (url.scheme not in SCHEMES or '@' not in url.netloc or not url.username or not url.password
            or not url.hostname or database != 'hosting' or url.fragment):
        raise Stop(4, 'admin DSN: invalid')
    netloc = '%s@%s%s' % (url.netloc.rsplit('@', 1)[0], ip, '' if port is None else ':%d' % port)
    try:
        write_new(path, urlunsplit((url.scheme, netloc, url.path, url.query, '')).encode('ascii'))
    except Exception:
        raise Stop(4, 'admin DSN: file not written') from None
    say('admin DSN written (root 0600, host = postgres-0 Pod IP)')


# Tool -------------------------------------------------------------------------------------------------
CONTAINERS = []  # names of tool containers started by this run


def tool(image, ev, action, owner):
    """One tool container: (rc, confirmed). rc 124 = CLI timeout (container state proved separately)."""
    name = 'issue61-w3b-%s-%s-%d' % (action, short(owner), os.getpid())
    reports = os.path.join(ev, 'report-' + short(owner))
    CONTAINERS.append(name)
    rc, out = run([DOCKER, 'run', '--rm', '--name', name, '--pull=never', '--user', '0:0', '--read-only',
                   '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--network', 'host',
                   '--mount', 'type=bind,src=%s,dst=/private,readonly' % os.path.join(ev, 'private'),
                   '--mount', 'type=bind,src=%s,dst=/reports%s' % (reports, ',readonly' if action == 'apply' else ''),
                   '-e', 'ADMIN_DATABASE_URL_FILE=/private/admin-dsn', '--entrypoint', 'python', image,
                   '-B', '-m', 'ops.runner_binding_image', action,
                   '--inputs', '/private/inputs-%s.json' % short(owner), '--report', '/reports/binding-image.json'],
                  TOOL_TIMEOUT)
    want = {'owner': owner, 'stage': 'binding-image-' + action, 'result': 'verified'}
    lines = [l for l in out.splitlines() if l.startswith('{')]
    try:
        confirmed = rc == 0 and bool(lines) and json.loads(lines[-1]) == want
    except ValueError:
        confirmed = False
    return rc, confirmed


def reports(image, ev, expected):
    for owner in OWNERS:
        inputs, want = expected[owner]
        private_dir(os.path.join(ev, 'report-' + short(owner)))
        write_new(os.path.join(ev, 'private', 'inputs-%s.json' % short(owner)), json.dumps(inputs).encode())
    for owner in OWNERS:
        rc, confirmed = tool(image, ev, 'report', owner)
        if not proved_gone(CONTAINERS[-1]):
            raise Stop(80, 'report container not proved stopped')
        path = os.path.join(ev, 'report-' + short(owner), 'binding-image.json')
        if rc != 0 or not confirmed:
            raise Stop(5, 'report: the tool refused or failed for …%s (rc %d); no apply ran' % (short(owner), rc))
        try:
            with open(path, 'rb') as stream:
                data = stream.read()
            got = json.loads(data)
        except Exception:
            raise Stop(5, 'report: …%s report unreadable; no apply ran' % short(owner)) from None
        sha = hashlib.sha256(data).hexdigest()
        if got != expected[owner][1]:
            diff = sorted(k for k in set(got) | set(expected[owner][1]) if got.get(k) != expected[owner][1].get(k))
            raise Stop(5, 'report: …%s differs from the approved report in %s; no apply ran'
                          % (short(owner), ','.join(diff)))
        RUN.setdefault('reports', {})[owner] = sha
        say('report …%s: equals the approved report (file sha256 %s)' % (short(owner), sha))


def plan_state(expected):
    """Per owner 'old' | 'new' | 'other' | 'unreadable' from the live plan digest, with the fences."""
    try:
        by = owners_state(8)
    except Stop:
        return {o: 'unreadable' for o in OWNERS}, None
    result = {}
    for owner in OWNERS:
        inputs, report = expected[owner]
        plan, got = by[owner]['plan'], digest(by[owner]['plan'])
        if got == inputs['oldPlanSha256']:
            result[owner] = 'old'
        elif (got == report['newPlanSha256'] and plan.get('image') == RUNNER_REF
              and (plan.get('executor') or {}).get('image') == EXECUTOR_REF):
            result[owner] = 'new'
        else:
            result[owner] = 'other'
    return result, by


def apply_all(image, ev, expected, outcome):
    record(ev, applyStarted=True)
    for owner in OWNERS:
        count, roles = clients(8)
        if count:
            raise Stop(8, 'apply: %d client session(s) on hosting appeared before …%s (%s)' % (count, short(owner), roles))
        outcome[owner] = {'attempted': True, 'confirmed': False}
        record(ev, outcome=outcome)
        rc, confirmed = tool(image, ev, 'apply', owner)
        outcome[owner]['confirmed'] = confirmed
        record(ev, outcome=outcome)
        if not proved_gone(CONTAINERS[-1]):
            raise Stop(80, 'apply container not proved stopped')
        states, _ = plan_state(expected)
        say('apply …%s: tool rc %d, confirmed %s; plan now %s' % (short(owner), rc, confirmed, states[owner]))
        if not (confirmed and states[owner] == 'new'):
            return  # never the next owner after an unconfirmed or uncommitted apply


# Container proof --------------------------------------------------------------------------------------
def present(name):
    """True if the container exists (any state), False if gone, None if the daemon lookup failed."""
    rc, out = run([DOCKER, 'ps', '-a', '--filter', 'name=^%s$' % name, '--format', '{{.State}}'], 30)
    if rc != 0:
        return None
    return out != ''


def proved_gone(name):
    for _ in range(3):
        state = present(name)
        if state is False:
            return True
        if state is None:
            return False
        run([DOCKER, 'stop', '-t', '30', name], 60)
        run([DOCKER, 'rm', '-f', name], 30)
        time.sleep(1)
    return present(name) is False


# Restore ----------------------------------------------------------------------------------------------
def remove_dsn(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except Exception:
        return False
    return not os.path.lexists(path)


def restore(replicas, dsn):
    failed = []
    for spec in (API, CTRL):
        if spec not in TOUCHED:
            continue
        try:
            scale(spec, replicas[spec[1]], 70)
        except Stop:
            failed.append('scale ' + spec[1])
    for spec in (API, CTRL):
        if spec not in TOUCHED:
            continue
        ns, name = spec[0], spec[1]
        if run([K3S, 'kubectl', '-n', ns, 'rollout', 'status', 'deployment/' + name, '--timeout=180s'], 210)[0] != 0:
            failed.append('rollout ' + name)
        try:
            uid, want, ready, _ = deployment(spec, 70)
            if uid != spec[2] or want != replicas[name] or ready != replicas[name]:
                failed.append('readiness ' + name)
        except Stop:
            failed.append('readiness ' + name)
    if API in TOUCHED:
        rc, code = run([CURL] + HEALTH, 20)
        if rc != 0 or code != '200':
            failed.append('health')
    if dsn and not remove_dsn(dsn):
        failed.append('admin DSN still present')
    say('restore: %s; admin DSN %s' % (', '.join('%s replicas=%d' % (s[1], replicas[s[1]]) for s in (API, CTRL)
                                                 if s in TOUCHED) or 'nothing was scaled',
                                       'present' if dsn and os.path.lexists(dsn) else 'absent'))
    return failed


def settle(expected, outcome):
    """Final code from the live plan digests and the per-owner tool confirmations."""
    states, by = plan_state(expected)
    say('plans: %s' % ', '.join('…%s %s' % (short(o), states[o]) for o in OWNERS))
    if by is not None:
        for owner in OWNERS:
            say('  …%s fence %s' % (short(owner), json.dumps(fence_of(by[owner]), sort_keys=True)))
    if any(states[o] in ('other', 'unreadable') for o in OWNERS):
        return 8, 'a plan digest is neither the approved old nor new one, or unreadable: back checks read-only'
    if by is not None:
        for owner in OWNERS:
            inputs = expected[owner][0]
            want = {'state': inputs['state'], 'phase': 'idle_stopped', 'generation': inputs['generation'],
                    'revision': inputs['revision'], 'pod': None, 'active': 0, 'ambiguous': False}
            if fence_of(by[owner]) != want:
                return 8, '…%s fence changed: back checks read-only' % short(owner)
    first, second = (states[o] for o in OWNERS)
    if (first, second) == ('new', 'new'):
        if all((outcome.get(o) or {}).get('confirmed') for o in OWNERS):
            return 0, 'both bindings moved to %s / %s' % (RUNNER_REF.split('@')[1][:19], EXECUTOR_REF.split('@')[1][:19])
        return 9, 'both bindings moved but a tool apply was not confirmed: back checks read-only'
    if (first, second) == ('new', 'old'):
        return 7, 'partial: …0048 moved, …5804 still on its old plan; do not rerun, back drafts the next step'
    if (first, second) == ('old', 'old'):
        return 6, 'no binding changed'
    return 8, 'unexpected plan combination: back checks read-only'


def main():
    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    code, message, replicas, dsn, ev, applying = 0, '', {}, None, None, False
    outcome = {}
    try:
        expected = load_expected()
        image, replicas, before = precheck(expected)
        ev = os.path.join(EVIDENCE, 'w3b-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()))
        os.makedirs(EVIDENCE, mode=0o700, exist_ok=True)
        private_dir(ev)
        say('evidence: %s' % ev)
        record(ev, started=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), expectedSha256=EXPECTED_SHA,
               toolImage=image, replicas=replicas, before={o: fence_of(before[o]) for o in OWNERS})
        write_new(os.path.join(ev, 'before-plans.json'), json.dumps({o: before[o]['plan'] for o in OWNERS},
                                                                   sort_keys=True).encode())
        window(expected, before)
        record(ev, window=True)
        private_dir(os.path.join(ev, 'private'))
        dsn = os.path.join(ev, 'private', 'admin-dsn')
        admin_dsn(dsn)
        reports(image, ev, expected)
        record(ev, reportsMatched=True)
        applying = True
        apply_all(image, ev, expected, outcome)
    except Stop as stop:
        code, message = stop.code, stop.message
    except Interrupted:
        code, message = (9, 'interrupted during apply') if applying else (130, 'interrupted before any apply')
    except Exception:
        code, message = (8, 'unexpected error during apply') if applying else ((3, 'unexpected error') if not TOUCHED
                                                                               else (4, 'unexpected error'))
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if message:
        say('STOPPED (%d): %s' % (code, message))
    if any(not proved_gone(name) for name in CONTAINERS) or code == 80:
        removed = remove_dsn(dsn) if dsn else True
        say('MANUAL RECOVERY NEEDED: a tool container was not proved stopped; %s and %s LEFT AT 0; admin DSN %s; '
            'tell PM before any further step' % (CTRL[1], API[1], 'removed' if removed else 'STILL PRESENT'))
        if ev:
            record(ev, result=80)
        say('result rc=80')
        return 80
    if applying:
        code, message = settle(expected, outcome)
        say(message)
    elif TOUCHED and code != 3:
        states, _ = plan_state(expected)
        say('plans: %s (no apply ran)' % ', '.join('…%s %s' % (short(o), states[o]) for o in OWNERS))
        if any(states[o] != 'old' for o in OWNERS):
            code = 8
            say('a plan changed although no apply ran: back checks read-only')
    if TOUCHED or dsn:
        failed = restore(replicas, dsn)
        if failed:
            say('RESTORE FAILED: %s' % ', '.join(failed))
            if code == 0:
                code = 70
    if ev:
        record(ev, outcome=outcome, result=code, finished=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
    say('result rc=%d' % code)
    return code


if __name__ == '__main__':
    sys.exit(main())
