"""#90 stage C switch (chat-stream/DEV-VM.md P2), development VM only: in one window, the runner binding of owner …5805
moves to the #90 runner image (IMAGE_SHA 159e271c, tag 90-159e271) and the api Deployment's `api` container moves to
the #90 runtime image (same source). Both stream flags stay off; nothing else in either Deployment changes.

    python3 -I -B /root/issue90/i90-switch.py survey            # read-only: prints facts and the expected candidate
    python3 -I -B /root/issue90/i90-switch.py precheck <sha256>  # read-only: the full precheck against the file
    python3 -I -B /root/issue90/i90-switch.py run <sha256>       # once, as a systemd unit, after review and PM's go

**This file is native-shell/vm/pa-switch.py (sha256 00d14fb9…, approved for P-A) with these changes** (listed in full
so review can check them against that file):
- this docstring; the paths (/root/issue90/…), the evidence name and earlier-run regex (i90-switch-<UTC>), the SQL tag
  (i90), the container names (issue90-i90-*), the texts that named pa-expected.json, gen 13 / rev 52 and the P-A build;
- PINS filled after the earlier stages' output, never typed (placeholders refuse every mode but `survey`):
  GENERATION / REVISION (29 / 116, P0 look), OLD_REF (…5805's current plan.image, from `survey`), NEW_TAG / NEW_ID
  (the runner tag and host id, stage A build), API_TAG / API_ID (the runtime tag and host id, stage A build), and the
  LEDGERS hashes (from `survey`, which prints them);
- **the api image step** (new): the expected file adds `newApiImage` (the runtime digest reference, stage B). In the
  window, after the binding apply, `api_image()` changes only `spec.template.spec.containers[0].image` of api (named
  `api`) from the recorded image to newApiImage by a JSON patch that first tests the Deployment UID, the container
  name and the old image (kubectl patch --type=json). The init container `runner-pool-input`, env, replicas and every
  other field are not touched. `wanted()` gives the api template the checks expect: the recorded one before the
  change, the recorded one with newApiImage after it;
- **the undo** reverts both: the binding branch as before, and the api image back to the recorded image when the
  template is the new one (`api_state()`: old / new / other; other = FREEZE). 'undo not needed' only when both are old.
Everything else is pa-switch.py: the survey, i90-expected.json written by back from the survey output only and
cleared by review by hash, the precheck (rc 3), prepare (rc 4), the UID-preconditioned controller/api window, the
forward report (rc 5), apply, reopen, FREEZE (rc 7/8/80), the admin DSN handling, rc 70, and the read-only output rules.
"""
import base64
import hashlib
import ipaddress
import json
import os
import re
import signal
import stat
import subprocess
import sys
import time
from urllib.parse import urlsplit, urlunsplit

EXPECTED_FILE = '/root/issue90/i90-expected.json'
VERSION = 'i90-expected-r0'
OWNER = '00000000-0000-4000-8000-000000005805'
STATE = '75780e81-ef7f-4aec-a59c-ed9d71f4061a'
GENERATION, REVISION = 29, 116
OTHERS = ('00000000-0000-4000-8000-000000000048', '00000000-0000-4000-8000-000000005801',
          '00000000-0000-4000-8000-000000005802', '00000000-0000-4000-8000-000000005803',
          '00000000-0000-4000-8000-000000005804')
OWNERS = OTHERS + (OWNER,)
LIB = 'docker.io/library/'
PIN = 'PIN'  # a placeholder: every mode but `survey` refuses while any pin below is still PIN
OLD_REF = PIN  # …5805's current plan.image (full digest reference, printed by `survey`)
NEW_TAG = LIB + 'anxious-dev-runner:90-159e271'
NEW_ID = PIN  # the runner host image id (stage A build output)
NEW_PREFIX = LIB + 'anxious-dev-runner@sha256:'
API_TAG = LIB + 'anxious-dev-runtime:90-159e271'
API_ID = PIN  # the runtime host image id (stage A build output)
API_PREFIX = LIB + 'anxious-dev-runtime@sha256:'
API_CONTAINER = 'api'
TOOL_TAG = 'anxious-dev-runtime:68-42a1e8b'
MODULES = {'/app/ops/runner_binding_image.py': '0a60c7d4f3b8765bb9fb7f204f47917e49d5a5b9c3bc5b0cd48eb8a6ed632d7e',
           '/app/app/runner_bindings.py': '0cc5706f8db9193d1b1ff61dac46533a62a09eb951dfff39f2d2b2d0160e5342'}
LEDGERS = {'/root/native68/evidence/ledger-isolation-42.json': PIN,  # sha256 printed by `survey`
           '/root/native68/evidence/ledger-resources-6.json': PIN}
# (namespace, name, recorded UID, Pod label, containers, init containers)
CTRL = ('runner-control', 'runner-controller', '41564f26-95e7-4ad1-8af3-d73b93c4669a', 'app=runner-controller',
        ('controller',), ())
API = ('m2-hosting', 'api', 'fad6799a-6df5-4086-812f-cfae6212adfa', 'app=hosting-api', ('api',),
       ('runner-pool-input',))
UNITS = ['issue16-controller.service', 'issue16-observer.service', 'issue16-supply.service',
         'issue16-controller.timer', 'issue16-observer.timer', 'issue16-supply.timer', 'issue16-restore.timer']
EVIDENCE = '/root/issue90/evidence'
K3S = '/usr/local/bin/k3s'
DOCKER = '/usr/bin/docker'
SYSTEMCTL = '/usr/bin/systemctl'
CURL = '/usr/bin/curl'
HEALTH = ['-s', '-o', '/dev/null', '-w', '%{http_code}', '--max-time', '10',
          '--resolve', '192.88.99.1:80:10.0.0.107', 'http://192.88.99.1/api/health']
POD_CIDR = ipaddress.ip_network('10.42.0.0/16')
SCHEMES = {'postgresql', 'postgres', 'postgresql+psycopg', 'postgresql+psycopg2'}
ALLOW_ENV = 'CODEX_USER_RUNNER_ALLOWLIST'
HEX64 = re.compile(r'[0-9a-f]{64}')
WAIT = 120
TOOL_TIMEOUT = 300
OBSERVE = 90


class Stop(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


class Interrupted(BaseException):
    """SIGINT/SIGTERM; a BaseException so command wrappers do not turn it into a command failure."""


SIGNALLED = []


def interrupted(signum, frame):
    if not SIGNALLED:
        SIGNALLED.append(signum)
        raise Interrupted()


def say(text):
    try:
        print(text, flush=True)
    except Exception:
        try:
            null = os.open(os.devnull, os.O_WRONLY)
            os.dup2(null, sys.stdout.fileno())
            os.close(null)
        except Exception:
            pass


def utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


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


def sha_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def digest(plan):
    """ops.runner_binding_image.plan_digest: sha256 of the sorted, compact JSON."""
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


# Pure decisions (tested in Docker) ---------------------------------------------------------------------
def derive(expected):
    """The tool inputs and reports for 'forward' and 'reverse' (ops.runner_binding_image L49-57, main L72-74)."""
    fence = {'owner': OWNER, 'state': STATE, 'generation': GENERATION, 'revision': REVISION}
    old, new, image = expected['oldPlanSha256'], expected['newPlanSha256'], expected['newImage']
    return {'forward': {'inputs': dict(fence, oldPlanSha256=old, newImage=image),
                        'report': dict(fence, oldImage=OLD_REF, newImage=image, oldPlanSha256=old, newPlanSha256=new)},
            'reverse': {'inputs': dict(fence, oldPlanSha256=new, newImage=OLD_REF),
                        'report': dict(fence, oldImage=image, newImage=OLD_REF, oldPlanSha256=new, newPlanSha256=old)}}


def plan_state(plan, expected):
    """'old' / 'new' / 'other' for …5805's plan: digest and image both, as recorded."""
    if not isinstance(plan, dict):
        return 'other'
    got = digest(plan)
    if got == expected['oldPlanSha256'] and plan.get('image') == OLD_REF:
        return 'old'
    if got == expected['newPlanSha256'] and plan.get('image') == expected['newImage']:
        return 'new'
    return 'other'


def undo_branch(lookup_ok, fence_ok, others_ok, state):
    """The in-window undo branch (design g3 r2 §2 step 6; G5 design §3): 'reverse' (one reverse apply), 'not needed' or
    'freeze'. A failed lookup, a moved fence or another owner off its pin is a freeze whatever the plan says."""
    if lookup_ok is not True or fence_ok is not True or others_ok is not True:
        return 'freeze'
    return {'new': 'reverse', 'old': 'not needed'}.get(state, 'freeze')


def undo_actions(binding_branch, api):
    """#90: what the in-window undo does, from the binding branch and the api template state ('old'/'new'/'other', or
    None when its lookup failed): 'freeze', or the list of reversals (binding, api); [] means 'undo not needed'."""
    if binding_branch == 'freeze' or api not in ('old', 'new'):
        return 'freeze'
    return (['binding'] if binding_branch == 'reverse' else []) + (['api'] if api == 'new' else [])


def expected_problem(doc):
    """None, or why the expected file is refused (shape and cross-field rules)."""
    try:
        if set(doc) != {'version', 'newImage', 'oldImageId', 'oldPlanSha256', 'newPlanSha256', 'others',
                        'deployments', 'toolImage', 'newApiImage'} or doc['version'] != VERSION:
            return 'keys or version'
        api_new = doc['newApiImage']
        if not (isinstance(api_new, str) and api_new.startswith(API_PREFIX)
                and HEX64.fullmatch(api_new[len(API_PREFIX):])):
            return 'newApiImage is not a digest reference of anxious-dev-runtime'
        if not isinstance(doc.get('deployments'), dict) or not isinstance(doc['deployments'].get(API[1]), dict) \
                or doc['deployments'][API[1]].get('images', {}).get(API_CONTAINER) == api_new:
            return 'newApiImage is the recorded api image'
        image = doc['newImage']
        if not (isinstance(image, str) and image.startswith(NEW_PREFIX) and HEX64.fullmatch(image[len(NEW_PREFIX):])
                and image != OLD_REF):
            return 'newImage is not a digest reference of anxious-dev-runner other than the old one'
        for key in ('oldImageId', 'toolImage'):
            if not (isinstance(doc[key], str) and doc[key].startswith('sha256:') and HEX64.fullmatch(doc[key][7:])):
                return '%s is not a sha256 id' % key
        if doc['oldImageId'] == NEW_ID:
            return 'oldImageId is the new image id'
        for key in ('oldPlanSha256', 'newPlanSha256'):
            if not (isinstance(doc[key], str) and HEX64.fullmatch(doc[key])):
                return '%s is not a sha256' % key
        if doc['oldPlanSha256'] == doc['newPlanSha256']:
            return 'old and new plan digests are equal'
        others = doc['others']
        if not isinstance(others, dict) or sorted(others) != sorted(OTHERS):
            return 'others are not exactly the five other owners'
        for owner, row in others.items():
            if (not isinstance(row, dict) or set(row) != {'state', 'generation', 'revision', 'planSha256', 'image'}
                    or not isinstance(row['state'], str) or type(row['generation']) is not int
                    or type(row['revision']) is not int or not HEX64.fullmatch(str(row['planSha256']))
                    or not isinstance(row['image'], str) or row['image'] == image):
                return 'others row %s' % short(owner)
        deployments = doc['deployments']
        if not isinstance(deployments, dict) or sorted(deployments) != sorted([CTRL[1], API[1]]):
            return 'deployments are not exactly api and runner-controller'
        for spec in (CTRL, API):
            row = deployments[spec[1]]
            if (not isinstance(row, dict) or set(row) != {'uid', 'images', 'allow'} or row['uid'] != spec[2]
                    or row['allow'] != OWNER or not isinstance(row['images'], dict)
                    or sorted(row['images']) != sorted(spec[4] + spec[5])
                    or not all(isinstance(v, str) and '@sha256:' in v for v in row['images'].values())):
                return 'deployment %s' % spec[1]
    except Exception:
        return 'unreadable'
    return None


def pins_missing():
    """The names of the pins still at the placeholder (filled after the earlier stages' output, never typed)."""
    pins = {'OLD_REF': OLD_REF, 'NEW_ID': NEW_ID, 'API_ID': API_ID}
    pins.update({'LEDGERS ' + os.path.basename(path): want for path, want in LEDGERS.items()})
    return sorted(name for name, value in pins.items() if value == PIN)


def load_expected(want_sha, code=3):
    if pins_missing():
        raise Stop(3, 'pins are still placeholders: %s' % ', '.join(pins_missing()))
    if not (isinstance(want_sha, str) and HEX64.fullmatch(want_sha)):
        raise Stop(2, 'the expected-file sha256 argument is not 64 hex characters')
    try:
        info = os.lstat(EXPECTED_FILE)
        with open(EXPECTED_FILE, 'rb') as stream:
            data = stream.read()
    except Exception:
        raise Stop(code, 'i90-expected.json missing or unreadable') from None
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o022:
        raise Stop(code, 'i90-expected.json is not a root regular file without group/other write')
    if hashlib.sha256(data).hexdigest() != want_sha:
        raise Stop(code, 'i90-expected.json is not the reviewed file')
    try:
        doc = json.loads(data)
    except ValueError:
        raise Stop(code, 'i90-expected.json is not JSON') from None
    problem = expected_problem(doc) if isinstance(doc, dict) else 'not an object'
    if problem:
        raise Stop(code, 'i90-expected.json refused: %s' % problem)
    return doc


# Database (read-only) ---------------------------------------------------------------------------------
def rows(tag, query, code):
    """JSON rows (one json_build_object per line) of a READ ONLY query as postgres in postgres-0."""
    text = 'BEGIN READ ONLY;\n/*i90:%s*/ %s\nCOMMIT;\n' % (tag, query)
    out = must([K3S, 'kubectl', '-n', 'm2-hosting', 'exec', '-i', 'postgres-0', '--', 'psql', '-X', '-U', 'postgres',
                '-d', 'hosting', '-Atq', '-v', 'ON_ERROR_STOP=1', '-f', '-'], code,
               'database lookup failed (%s)' % tag, 60, text.encode())
    try:
        parsed = [json.loads(line) for line in out.splitlines() if line.strip() not in ('', 'BEGIN', 'COMMIT')]
    except ValueError:
        raise Stop(code, 'database lookup unreadable (%s)' % tag) from None
    if not all(isinstance(row, dict) for row in parsed):
        raise Stop(code, 'database lookup unreadable (%s)' % tag)
    return parsed


def owners_state(code):
    """Every registry row joined to its binding: fence, plan, pending work, current-generation proof; the totals of
    pending work, live sessions and running conversations must all be 0."""
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
                 "AND s.proof IS NOT NULL)) FROM runner_runtime.registry r FULL JOIN runner_bindings.bindings b "
                 "ON b.owner_id=r.owner_id ORDER BY 1::text;", code)
    totals = rows('totals', "SELECT json_build_object('openStops',(SELECT count(*) FROM runner_termination.stops "
                  "WHERE completed_at IS NULL),'openEntries',(SELECT count(*) FROM runner_termination.entries WHERE "
                  "consumed_at IS NULL),'outbox',(SELECT count(*) FROM runner_runtime.outbox WHERE NOT settled),"
                  "'admissions',(SELECT count(*) FROM runner_runtime.admissions WHERE completed_at IS NULL),"
                  "'sessions',(SELECT count(*) FROM auth.sessions WHERE expires_at>now()),"
                  "'running',(SELECT count(*) FROM research.conversations WHERE state='running'));", code)
    by = {row.get('owner'): row for row in found}
    if len(found) != len(OWNERS) or sorted(by) != sorted(OWNERS):
        raise Stop(code, 'registry/bindings are not exactly the six owners')
    if any(not isinstance(row.get('plan'), dict) for row in found):
        raise Stop(code, 'binding plan unreadable')
    keys = ('openStops', 'openEntries', 'outbox', 'admissions', 'sessions', 'running')
    if len(totals) != 1 or any(totals[0].get(k) != 0 for k in keys):
        raise Stop(code, 'pending work, a live session or a running conversation exists: %s'
                   % json.dumps({k: (totals[0] if totals else {}).get(k) for k in keys}, sort_keys=True))
    return by


def fence_of(row):
    return {k: row.get(k) for k in ('state', 'phase', 'generation', 'revision', 'pod', 'active', 'ambiguous')}


def idle(row):
    return row['phase'] == 'idle_stopped' and row['pod'] is None and row['active'] == 0 and row['ambiguous'] is False


def owner_fence_ok(row):
    return fence_of(row) == {'state': STATE, 'phase': 'idle_stopped', 'generation': GENERATION, 'revision': REVISION,
                             'pod': None, 'active': 0, 'ambiguous': False}


def others_ok(by, expected):
    for owner in OTHERS:
        row, pin = by[owner], expected['others'][owner]
        if (row['state'], row['generation'], row['revision']) != (pin['state'], pin['generation'], pin['revision']):
            return False
        if digest(row['plan']) != pin['planSha256'] or row['plan'].get('image') != pin['image']:
            return False
    return True


def clients(code):
    found = rows('clients', "SELECT json_build_object('clients',count(*),'roles',coalesce(string_agg(DISTINCT "
                 "usename::text,',' ORDER BY usename::text),'')) FROM pg_stat_activity WHERE datname='hosting' AND "
                 "backend_type='client backend' AND pid<>pg_backend_pid();", code)
    if len(found) != 1 or not isinstance(found[0].get('clients'), int):
        raise Stop(code, 'session lookup unreadable')
    return found[0]['clients'], found[0].get('roles') or ''


def check_owners(expected, code, where, want):
    """Every owner stopped and idle with a proof and no pending work; the others at their pins; …5805 on gen 29 /
    rev 116 with plan `want` ('old'/'new')."""
    by = owners_state(code)
    for owner in OWNERS:
        row = by[owner]
        if not idle(row):
            raise Stop(code, '%s: …%s fence is %s' % (where, short(owner), json.dumps(fence_of(row), sort_keys=True)))
        if row['bindingState'] != row['state']:
            raise Stop(code, '%s: …%s binding is on another state' % (where, short(owner)))
        pending = {k: row[k] for k in ('outbox', 'entries', 'stops', 'admissions')}
        if any(pending.values()):
            raise Stop(code, '%s: …%s has pending work %s' % (where, short(owner), json.dumps(pending, sort_keys=True)))
        if row['proof'] < 1:
            raise Stop(code, '%s: …%s has no completed proof for its current generation' % (where, short(owner)))
    if not others_ok(by, expected):
        raise Stop(code, '%s: another owner is not at its pin (fence, plan digest or image)' % where)
    if not owner_fence_ok(by[OWNER]):
        raise Stop(code, '%s: …5805 fence is %s' % (where, json.dumps(fence_of(by[OWNER]), sort_keys=True)))
    got = plan_state(by[OWNER]['plan'], expected)
    if got != want:
        raise Stop(code, '%s: …5805 plan is %s, want %s' % (where, got, want))
    return by


# Kubernetes and host ----------------------------------------------------------------------------------
def deployment_json(spec, code):
    out = kubectl(['-n', spec[0], 'get', 'deployment', spec[1], '-o', 'json'], code, '%s lookup failed' % spec[1])
    try:
        return json.loads(out)
    except ValueError:
        raise Stop(code, '%s unreadable' % spec[1]) from None


def template_of(spec, d, code):
    """{'uid', 'images', 'allow'} of the Deployment and its template (the expected file's shape)."""
    try:
        pod = d['spec']['template']['spec']
        containers, inits = pod['containers'], pod.get('initContainers') or []
        if tuple(c['name'] for c in containers) != spec[4] or tuple(c['name'] for c in inits) != spec[5]:
            raise ValueError
        env = containers[0].get('env') or []
        found = [e for e in env if e.get('name') == ALLOW_ENV]
        if len(found) != 1 or 'value' not in found[0] or 'valueFrom' in found[0]:
            raise ValueError
        return {'uid': d['metadata']['uid'], 'images': {c['name']: c['image'] for c in containers + inits},
                'allow': found[0]['value']}
    except Exception:
        raise Stop(code, '%s template is not the expected shape' % spec[1]) from None


def deployment(spec, code):
    d = deployment_json(spec, code)
    try:
        status = d.get('status') or {}
        return (d['metadata']['uid'], int(d['spec']['replicas']), int(status.get('readyReplicas') or 0),
                int(status.get('replicas') or 0), d)
    except Exception:
        raise Stop(code, '%s unreadable' % spec[1]) from None


def pods(ns, label, code):
    args = ['-n', ns, 'get', 'pods', '-o', 'name'] + (['-l', label] if label else [])
    return kubectl(args, code, 'pod lookup failed (%s)' % ns).split()


def host_quiet(code):
    for unit in UNITS:
        _, active = run([SYSTEMCTL, 'is-active', unit])  # rc 3 is normal for inactive units
        rc, listed = run([SYSTEMCTL, 'list-unit-files', '--no-legend', unit])
        if rc not in (0, 1):
            raise Stop(code, 'host unit %s lookup failed' % unit)
        enabled = run([SYSTEMCTL, 'is-enabled', unit])[1] if listed else 'absent'
        if active not in ('inactive', 'failed') or enabled not in ('disabled', 'absent'):
            raise Stop(code, 'host unit %s is %s/%s' % (unit, enabled or 'unreadable', active or 'unreadable'))
    rc, names = run([DOCKER, 'ps', '-a', '-q'])
    if rc != 0:
        raise Stop(code, 'docker ps failed')
    if names:
        raise Stop(code, 'a guest container exists')
    if pods('codex-runner', None, code):
        raise Stop(code, 'a runner Pod exists')


def resolve(ref):
    rc, got = run([K3S, 'crictl', 'inspecti', '-o', 'go-template', '--template', '{{.status.id}}', ref], 30)
    return got if rc == 0 else ''


def tag_digest(code, tag=NEW_TAG):
    """The digest column (3) of `tag` in `ctr images ls` (as v1-import.sh / V7a read it)."""
    rc, out = run([K3S, 'ctr', '-n', 'k8s.io', 'images', 'ls'], 30)
    if rc != 0:
        raise Stop(code, 'ctr images ls failed')
    found = [line.split()[2] for line in out.splitlines()[1:] if len(line.split()) > 2 and line.split()[0] == tag]
    if len(found) != 1 or not found[0].startswith('sha256:') or not HEX64.fullmatch(found[0][7:]):
        raise Stop(code, '%s is not one imported image with a digest' % tag)
    return found[0]


def images_ok(expected, code):
    if resolve(NEW_TAG) != NEW_ID:
        raise Stop(code, '%s does not resolve to the #90 runner build host id' % NEW_TAG)
    if NEW_PREFIX + tag_digest(code)[7:] != expected['newImage']:
        raise Stop(code, 'the new digest reference is not the recorded one')
    if resolve(expected['newImage']) != NEW_ID:
        raise Stop(code, 'the new digest reference does not resolve to the #90 runner build host id')
    if resolve(OLD_REF) != expected['oldImageId']:
        raise Stop(code, 'the old runner reference does not resolve to its recorded id')
    # #90: the runtime image the api container moves to (stage B), and the image it moves back to on undo.
    if resolve(API_TAG) != API_ID:
        raise Stop(code, '%s does not resolve to the #90 runtime build host id' % API_TAG)
    if API_PREFIX + tag_digest(code, API_TAG)[7:] != expected['newApiImage']:
        raise Stop(code, 'the new runtime digest reference is not the recorded one')
    if resolve(expected['newApiImage']) != API_ID:
        raise Stop(code, 'the new runtime digest reference does not resolve to the #90 runtime build host id')
    if not resolve(expected['deployments'][API[1]]['images'][API_CONTAINER]):
        raise Stop(code, 'the recorded api image does not resolve (the undo could not use it)')


def health():
    rc, got = run([CURL] + HEALTH, 20)
    return rc == 0 and got == '200'


def tool_image():
    rc, got = run([DOCKER, 'image', 'inspect', '--format', '{{.Id}}', TOOL_TAG], 30)
    return got if rc == 0 else ''


def ledgers_ok():
    try:
        return all(not os.path.islink(p) and sha_file(p) == want for p, want in LEDGERS.items())
    except Exception:
        return False


# Survey (read-only; no expected file) -----------------------------------------------------------------
def survey():
    """Facts and the expected candidate. Refuses (rc 3) at the first failed lookup; judges nothing else."""
    if os.geteuid() != 0:
        raise Stop(3, 'survey: not root')
    found = tag_digest(3)
    image = NEW_PREFIX + found[7:]
    api_found = tag_digest(3, API_TAG)
    api_image_ref = API_PREFIX + api_found[7:]
    by = owners_state(3)
    current = by[OWNER]['plan'].get('image')
    # OLD_REF is pinned from this output: the old id is read from the plan's own image, not from the pin.
    ids = {'tag': resolve(NEW_TAG), 'digestReference': resolve(image), 'old': resolve(current),
           'apiTag': resolve(API_TAG), 'apiDigestReference': resolve(api_image_ref)}
    say('survey %s (read-only): %s digest %s; %s digest %s; ids %s; build host ids runner %s runtime %s'
        % (utc(), NEW_TAG, found, API_TAG, api_found, json.dumps(ids, sort_keys=True), NEW_ID, API_ID))
    say('  …5805 current plan.image (the OLD_REF pin): %s' % current)
    for path in sorted(LEDGERS):
        try:
            say('  ledger %s sha256 %s (the LEDGERS pin)' % (path, 'symlink' if os.path.islink(path) else sha_file(path)))
        except Exception:
            say('  ledger %s unreadable' % path)
    for owner in OWNERS:
        row = by[owner]
        say('  …%s fence %s plan %s image %s pending %s proof %d' % (
            short(owner), json.dumps(fence_of(row), sort_keys=True), digest(row['plan']), row['plan'].get('image'),
            json.dumps({k: row[k] for k in ('outbox', 'entries', 'stops', 'admissions')}, sort_keys=True), row['proof']))
    plan = by[OWNER]['plan']
    deployments = {}
    for spec in (CTRL, API):
        uid, want, ready, current, d = deployment(spec, 3)
        deployments[spec[1]] = template_of(spec, d, 3)
        say('  %s uid %s replicas %d/%d ready %d template %s' % (spec[1], uid, current, want, ready,
                                                                json.dumps(deployments[spec[1]], sort_keys=True)))
    count, roles = clients(3)
    say('  client sessions on hosting %d (%s); /api/health 200 %s; ledgers at their pins %s; tool image %s'
        % (count, roles or 'none', health(), ledgers_ok(), tool_image() or 'absent'))
    try:
        host_quiet(3)
        say('  host units disabled/inactive, no guest container, no runner Pod: True')
    except Stop as stop:
        say('  host quiet: False (%s)' % stop.message)
    candidate = {'version': VERSION, 'newImage': image, 'oldImageId': ids['old'], 'oldPlanSha256': digest(plan),
                 'newPlanSha256': digest(dict(plan, image=image)), 'toolImage': tool_image(), 'newApiImage': api_image_ref,
                 'others': {o: {'state': by[o]['state'], 'generation': by[o]['generation'],
                                'revision': by[o]['revision'], 'planSha256': digest(by[o]['plan']),
                                'image': by[o]['plan'].get('image')} for o in OTHERS},
                 'deployments': deployments}
    say('  …5805 plan image is the OLD_REF pin: %s; pins still placeholders: %s; candidate problem: %s'
        % (plan.get('image') == OLD_REF, pins_missing() or 'none', expected_problem(candidate)))
    derived = derive(candidate)
    for direction in ('forward', 'reverse'):
        say('  derived %s report %s' % (direction, json.dumps(derived[direction]['report'], sort_keys=True)))
    say('expected candidate: %s' % json.dumps(candidate, sort_keys=True, separators=(',', ':')))


# Precheck ---------------------------------------------------------------------------------------------
def precheck(expected, readonly):
    if os.geteuid() != 0:
        raise Stop(3, 'precheck: not root')
    previous = [d for d in os.listdir(EVIDENCE) if re.fullmatch(r'i90-switch-\d{8}T\d{6}Z', d)] \
        if os.path.isdir(EVIDENCE) else None
    if previous is None or previous:
        raise Stop(3, 'precheck: an earlier i90-switch run left evidence, or the evidence directory is missing; '
                      'never rerun, back checks')
    host_quiet(3)
    images_ok(expected, 3)
    templates = {}
    for spec in (CTRL, API):
        uid, want, ready, _, d = deployment(spec, 3)
        if uid != spec[2] or want != 1 or ready != 1:
            raise Stop(3, 'precheck: %s is uid=%s replicas=%d ready=%d (want %s 1/1)' % (spec[1], uid, want, ready,
                                                                                          spec[2]))
        if template_of(spec, d, 3) != expected['deployments'][spec[1]]:
            raise Stop(3, 'precheck: %s template is not the recorded one' % spec[1])
        templates[spec[1]] = d
    by = check_owners(expected, 3, 'precheck', 'old')
    if not health():
        raise Stop(3, 'precheck: /api/health is not 200')
    if tool_image() != expected['toolImage']:
        raise Stop(3, 'precheck: guest Docker %s is absent or another image' % TOOL_TAG)
    if not ledgers_ok():
        raise Stop(3, 'precheck: a ledger is not at its pin')
    count, roles = clients(3)
    say('precheck ok%s %s: host units disabled/inactive, no guest container, no runner Pod; %s and the new digest '
        'reference resolve to %s, the old reference to its recorded id; %s and %s 1/1 with the recorded UID, '
        'images and allowlist; six owners idle_stopped with proofs, no pending work, no live session, no running '
        'conversation; the others at their pins; …5805 gen 29 rev 116 on the old plan; health 200; tool image ok; '
        'ledgers at their pins; client sessions now %d (%s)'
        % (' (read-only)' if readonly else '', utc(), NEW_TAG, NEW_ID, CTRL[1], API[1], count, roles or 'none'))
    for owner in OWNERS:
        say('  …%s fence %s plan %s' % (short(owner), json.dumps(fence_of(by[owner]), sort_keys=True),
                                       digest(by[owner]['plan'])))
    return templates, by


# Evidence ---------------------------------------------------------------------------------------------
RUN = {}
EV = []


def record(**items):
    RUN.update(items)
    if not EV:
        return
    path = os.path.join(EV[0], 'run.json')
    temp = path + '.tmp'
    with open(temp, 'w') as stream:
        json.dump(RUN, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
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


def change(item, state):
    RUN.setdefault('changes', {})[item] = state
    record()


# Prepare ----------------------------------------------------------------------------------------------
def prepare(ev, templates, by, expected):
    for spec in (CTRL, API):
        write_new(os.path.join(ev, 'deployment-%s.json' % spec[1]), json.dumps(templates[spec[1]], sort_keys=True).encode())
    write_new(os.path.join(ev, 'before-plans.json'), json.dumps({o: by[o]['plan'] for o in OWNERS}, sort_keys=True).encode())
    image = expected['toolImage']
    name = 'issue90-i90-modules-%d' % os.getpid()
    CONTAINERS.append(name)
    rc, out = run([DOCKER, 'run', '--rm', '--name', name, '--pull=never', '--network', 'none', '--read-only',
                   '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--user', '0:0',
                   '--entrypoint', 'sha256sum', image] + sorted(MODULES), 60)
    if not proved_gone(name):
        raise Stop(80, 'prepare: module hash container not proved stopped')
    seen = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2:
            seen[parts[1]] = parts[0]
    if rc != 0 or seen != MODULES:
        raise Stop(4, 'prepare: tool image modules are not the reviewed ones')
    say('backups: both Deployments and six plans (evidence directory); tool image %s, modules ok' % image)
    host_quiet(4)
    return image


# Window -----------------------------------------------------------------------------------------------
SCALED = []  # Deployments this run scaled


def scale(spec, replicas, code):
    patch = {'metadata': {'uid': spec[2]}, 'spec': {'replicas': replicas}}
    kubectl(['-n', spec[0], 'patch', 'deployment', spec[1], '--type=merge', '-p', json.dumps(patch)], code,
            '%s scale to %d failed' % (spec[1], replicas))


def gone(spec, code):
    deadline = time.monotonic() + WAIT
    while pods(spec[0], spec[3], code):
        if time.monotonic() >= deadline:
            raise Stop(code, '%s Pods still present after %d s' % (spec[1], WAIT))
        time.sleep(3)


def no_clients(code, where):
    deadline = time.monotonic() + WAIT
    while True:
        count, roles = clients(code)
        if not count:
            return
        if time.monotonic() >= deadline:
            raise Stop(code, '%s: %d client session(s) on hosting remain (%s)' % (where, count, roles))
        time.sleep(3)


def stopped(code, where):
    """Both Deployments at 0 (same UIDs), their Pods gone, no client session on hosting."""
    for spec in (CTRL, API):
        if spec not in SCALED:
            SCALED.append(spec)
        scale(spec, 0, code)
        gone(spec, code)
    for spec in (CTRL, API):
        uid, want, _, current, _ = deployment(spec, code)
        if uid != spec[2] or want != 0 or current != 0:
            raise Stop(code, '%s: %s is uid=%s replicas=%d/%d' % (where, spec[1], uid, want, current))
    no_clients(code, where)


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
CONTAINERS = []


def tool(image, ev, action, direction):
    """One tool container for `direction` ('forward'/'reverse'): (rc, confirmed)."""
    name = 'issue90-i90-%s-%s-%d' % (direction, action, os.getpid())
    reports = os.path.join(ev, 'report-' + direction)
    CONTAINERS.append(name)
    rc, out = run([DOCKER, 'run', '--rm', '--name', name, '--pull=never', '--user', '0:0', '--read-only',
                   '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--network', 'host',
                   '--mount', 'type=bind,src=%s,dst=/private,readonly' % os.path.join(ev, 'private'),
                   '--mount', 'type=bind,src=%s,dst=/reports%s' % (reports, ',readonly' if action == 'apply' else ''),
                   '-e', 'ADMIN_DATABASE_URL_FILE=/private/admin-dsn', '--entrypoint', 'python', image,
                   '-B', '-m', 'ops.runner_binding_image', action,
                   '--inputs', '/private/inputs-%s.json' % direction, '--report', '/reports/binding-image.json'],
                  TOOL_TIMEOUT)
    if not proved_gone(name):
        raise Stop(80, '%s %s container not proved stopped' % (direction, action))
    want = {'owner': OWNER, 'stage': 'binding-image-' + action, 'result': 'verified'}
    lines = [l for l in out.splitlines() if l.startswith('{')]
    try:
        confirmed = rc == 0 and bool(lines) and json.loads(lines[-1]) == want
    except ValueError:
        confirmed = False
    return rc, confirmed


def report(image, ev, expected, direction, code):
    """The tool's report for `direction` must equal the derived one."""
    derived = derive(expected)[direction]
    private_dir(os.path.join(ev, 'report-' + direction))
    write_new(os.path.join(ev, 'private', 'inputs-%s.json' % direction), json.dumps(derived['inputs']).encode())
    rc, confirmed = tool(image, ev, 'report', direction)
    if rc != 0 or not confirmed:
        raise Stop(code, '%s report: the tool refused or failed (rc %d)' % (direction, rc))
    path = os.path.join(ev, 'report-' + direction, 'binding-image.json')
    try:
        with open(path, 'rb') as stream:
            data = stream.read()
        got = json.loads(data)
    except Exception:
        raise Stop(code, '%s report unreadable' % direction) from None
    if got != derived['report']:
        diff = sorted(k for k in set(got) | set(derived['report']) if got.get(k) != derived['report'].get(k))
        raise Stop(code, '%s report differs from the derived one in %s' % (direction, ','.join(diff)))
    record(**{'report-' + direction: hashlib.sha256(data).hexdigest()})
    say('%s report: equals the derived report (file sha256 %s)' % (direction, hashlib.sha256(data).hexdigest()))


def apply(image, ev, expected, direction, want_state, code):
    no_clients(code, '%s apply' % direction)
    change('binding-' + direction, 'pending')
    rc, confirmed = tool(image, ev, 'apply', direction)
    by = owners_state(8)
    state = plan_state(by[OWNER]['plan'], expected)
    others = others_ok(by, expected)
    change('binding-' + direction, 'done' if confirmed and state == want_state and others else 'uncertain')
    say('%s apply: tool rc %d, confirmed %s; …5805 plan now %s; other plans %s'
        % (direction, rc, confirmed, state, 'unchanged' if others else 'CHANGED'))
    if not others or state == 'other' or not owner_fence_ok(by[OWNER]):
        raise Stop(8, '%s apply: a plan digest is neither old nor new, another plan changed or the fence moved'
                   % direction)
    if not (confirmed and state == want_state):
        raise Stop(code, '%s apply did not complete (…5805 plan %s)' % (direction, state))


# Container proof --------------------------------------------------------------------------------------
def present(name):
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


# API image (#90) --------------------------------------------------------------------------------------
def wanted(spec, expected, api):
    """The template the checks expect: as recorded, except api's `api` container on newApiImage when api='new'."""
    row = expected['deployments'][spec[1]]
    if spec is not API or api == 'old':
        return row
    return dict(row, images=dict(row['images'], **{API_CONTAINER: expected['newApiImage']}))


def api_state(expected, code):
    """'old' / 'new' / 'other' for the api Deployment template (UID, images incl. the init container, allowlist)."""
    _, _, _, _, d = deployment(API, code)
    got = template_of(API, d, code)
    for state in ('old', 'new'):
        if got == wanted(API, expected, state):
            return state
    return 'other'


def api_patch(expected, direction):
    """The JSON patch (kubectl --type=json) that moves only containers[0].image of api, after testing the UID, the
    container name and the current image. 'forward': recorded -> newApiImage; 'reverse': back."""
    old = expected['deployments'][API[1]]['images'][API_CONTAINER]
    new = expected['newApiImage']
    source, target = (old, new) if direction == 'forward' else (new, old)
    path = '/spec/template/spec/containers/0'
    return [{'op': 'test', 'path': '/metadata/uid', 'value': API[2]},
            {'op': 'test', 'path': path + '/name', 'value': API_CONTAINER},
            {'op': 'test', 'path': path + '/image', 'value': source},
            {'op': 'replace', 'path': path + '/image', 'value': target}]


def api_image(expected, direction, code):
    """Inside the window (api at 0): one image-only patch, then the template must be exactly the wanted one."""
    want = 'new' if direction == 'forward' else 'old'
    _, replicas, _, current, _ = deployment(API, code)
    if (replicas, current) != (0, 0):
        raise Stop(code, 'api image %s: api is not at 0' % direction)
    change('api-image-' + direction, 'pending')
    rc, _ = run([K3S, 'kubectl', '-n', API[0], 'patch', 'deployment', API[1], '--type=json', '-p',
                 json.dumps(api_patch(expected, direction))], 60)
    state = api_state(expected, 8)
    change('api-image-' + direction, 'done' if rc == 0 and state == want else 'uncertain')
    say('api image %s: patch rc %d; api template now %s' % (direction, rc, state))
    if state == 'other':
        raise Stop(8, 'api image %s: the api template is neither the recorded nor the new one' % direction)
    if rc != 0 or state != want:
        raise Stop(code, 'api image %s did not complete (template %s)' % (direction, state))


# Reopen and its checks --------------------------------------------------------------------------------
def start(spec, expected, code, where, api='old'):
    """Scale to 1 by UID; rollout; one ready Pod whose containers run the wanted images (spec and imageID)."""
    images = wanted(spec, expected, api)['images']
    want = {name: ref.split('@', 1)[1] for name, ref in images.items()}
    scale(spec, 1, code)
    if run([K3S, 'kubectl', '-n', spec[0], 'rollout', 'status', 'deployment/' + spec[1], '--timeout=180s'], 210)[0]:
        raise Stop(code, '%s: %s rollout did not finish' % (where, spec[1]))
    uid, replicas, ready, _, d = deployment(spec, code)
    if uid != spec[2] or replicas != 1 or ready != 1 or template_of(spec, d, code) != wanted(spec, expected, api):
        raise Stop(code, '%s: %s is not 1/1 on its wanted template' % (where, spec[1]))
    out = kubectl(['-n', spec[0], 'get', 'pods', '-l', spec[3], '-o', 'json'], code, '%s Pod lookup failed' % spec[1])
    try:
        items = [p for p in json.loads(out)['items'] if not p['metadata'].get('deletionTimestamp')]
        statuses = items[0]['status'].get('containerStatuses', []) + items[0]['status'].get('initContainerStatuses', [])
        ids = {s['name']: s.get('imageID', '').rsplit('@', 1)[-1] for s in statuses}
        ok = len(items) == 1 and ids == want
    except Exception:
        ok = False
    if not ok:
        raise Stop(code, '%s: %s Pod does not run the recorded images' % (where, spec[1]))
    say('%s %s: 1/1, Pod runs its recorded images' % (where, spec[1]))


def no_runner_pods(code, where):
    found = pods('codex-runner', None, code)
    if found:
        raise Stop(8, '%s: runner Pod(s) present: %d' % (where, len(found)))


def observe(expected):
    deadline = time.monotonic() + OBSERVE
    while True:
        no_runner_pods(8, 'observation')
        by = owners_state(8)
        if any(fence_of(by[o]) != RUN['before'][o] for o in OWNERS) or not others_ok(by, expected) \
                or plan_state(by[OWNER]['plan'], expected) != 'new':
            raise Stop(8, 'observation: a fence or plan changed')
        if time.monotonic() >= deadline:
            break
        time.sleep(10)
    say('observation: %d s, no runner Pod, every fence and plan unchanged' % OBSERVE)


def reopen_checks(expected, where):
    """Before entry opens again: every window-start condition holds (raises Stop 7). Host units and containers
    quiet, no runner Pod; six owners idle_stopped, no pending work, the same fences as at the window start,
    …5805 old and the others at their pins; both templates as recorded, at 0."""
    host_quiet(7)
    by = check_owners(expected, 7, where, 'old')
    if any(fence_of(by[o]) != RUN['before'][o] for o in OWNERS):
        raise Stop(7, '%s: a fence differs from the window start' % where)
    for spec in (API, CTRL):
        _, want, _, current, d = deployment(spec, 7)
        if template_of(spec, d, 7) != expected['deployments'][spec[1]] or (want, current) != (0, 0):
            raise Stop(7, '%s: %s is not on its recorded template at 0' % (where, spec[1]))


def reopen(expected, where):
    """Verify, then api, then (only if the api is 1/1 on its images and healthy) runner-controller back to 1.
    Returns (True, '') or (False, why); never continues past a failed step (finish() then holds both at 0)."""
    try:
        stopped(7, where)
        reopen_checks(expected, where)
    except BaseException as error:
        return False, '%s refused: %s' % (where, message_of(error))
    for spec in (API, CTRL):
        try:
            start(spec, expected, 7, where)
            if spec is API and not health():
                raise Stop(7, '%s: /api/health is not 200' % where)
        except BaseException as error:
            return False, '%s: %s' % (where, message_of(error))
    say('window end %s (reopened after the checks)' % utc())
    return True, ''


# Undo and freeze --------------------------------------------------------------------------------------
def message_of(error):
    return error.message if isinstance(error, Stop) else type(error).__name__


def hold(specs):
    """Scale `specs` to 0 by UID and prove it: replicas 0 and their Pods gone. True only if proved for every one."""
    proved = True
    for spec in specs:
        try:
            if spec not in SCALED:
                SCALED.append(spec)
            scale(spec, 0, 7)
            gone(spec, 7)
            uid, want, _, current, _ = deployment(spec, 7)
            proved = proved and uid == spec[2] and want == 0 and current == 0
        except BaseException:
            proved = False
    return proved


def freeze_line(proved):
    return ('FREEZE HELD: runner-controller and api at 0 (verified); window end: none, entry stays closed'
            if proved else 'FREEZE NOT CONFIRMED: runner-controller and api could not be proved at 0; tell PM now')


def undo(image, ev, expected):
    """The in-window undo (three branches). Returns 'undo ok', 'undo not needed', 'undo failed' or 'freeze'."""
    try:
        stopped(7, 'undo')
    except BaseException:
        return 'freeze'  # the window could not be proved closed again: no tool call
    try:
        by = owners_state(7)
        lookup, fence, others, state = True, owner_fence_ok(by[OWNER]), others_ok(by, expected), \
            plan_state(by[OWNER]['plan'], expected)
    except BaseException:
        lookup, fence, others, state = False, None, None, None
    branch = undo_branch(lookup, fence, others, state)
    try:
        api = api_state(expected, 7)
    except BaseException:
        api = None
    actions = undo_actions(branch, api)
    record(undoBranch={'lookup': lookup, 'fence': fence, 'others': others, 'plan': state, 'branch': branch,
                       'api': api, 'actions': actions})
    say('undo branch: …5805 plan %s, fence ok %s, others at their pins %s, lookup ok %s -> %s; api template %s; '
        'actions %s' % (state, fence, others, lookup, branch, api, actions))
    if actions == 'freeze':
        return 'freeze'
    if not actions:
        return 'undo not needed'
    try:
        if 'binding' in actions:
            report(image, ev, expected, 'reverse', 7)
            apply(image, ev, expected, 'reverse', 'old', 7)
        if 'api' in actions:
            api_image(expected, 'reverse', 7)
        check_owners(expected, 7, 'undo verification', 'old')
        if api_state(expected, 7) != 'old':
            raise Stop(7, 'undo verification: the api template is not the recorded one')
    except BaseException:
        return 'undo failed'
    return 'undo ok'


# Main -------------------------------------------------------------------------------------------------
def main(argv):
    if len(argv) == 2 and argv[1] == 'survey':
        try:
            survey()
        except Stop as stop:
            say('STOPPED (%d): %s' % (stop.code, stop.message))
            say('result rc=%d' % stop.code)
            return stop.code
        say('result rc=0 (survey, read-only)')
        return 0
    if len(argv) != 3 or argv[1] not in ('precheck', 'run'):
        say('usage: i90-switch.py survey | precheck <sha256> | run <sha256>')
        return 2
    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    if argv[1] == 'precheck':
        try:
            precheck(load_expected(argv[2]), True)
        except Stop as stop:
            say('STOPPED (%d): %s' % (stop.code, stop.message))
            say('result rc=%d' % stop.code)
            return stop.code
        say('result rc=0 (read-only)')
        return 0
    code, message, dsn, image, changing, expected = 0, '', None, None, False, None
    try:
        try:
            expected = load_expected(argv[2])
            templates, before = precheck(expected, False)
            ev = os.path.join(EVIDENCE, 'i90-switch-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()))
            private_dir(ev)
            private_dir(os.path.join(ev, 'private'))
            EV.append(ev)
            say('evidence: %s' % ev)
            record(started=utc(), expectedSha256=argv[2], before={o: fence_of(before[o]) for o in OWNERS},
                   derived=derive(expected))
            image = prepare(ev, templates, before, expected)
            say('window start %s' % utc())
            record(windowStart=utc())
            stopped(4, 'window')
            host_quiet(4)
            window = check_owners(expected, 4, 'window', 'old')
            if any(fence_of(window[o]) != RUN['before'][o] for o in OWNERS):
                raise Stop(4, 'window: a fence differs from the precheck')
            say('window ok: %s and %s at 0 with the same UIDs, their Pods gone; 0 client sessions on hosting'
                % (CTRL[1], API[1]))
            dsn = os.path.join(ev, 'private', 'admin-dsn')
            admin_dsn(dsn)
            report(image, ev, expected, 'forward', 5)
            changing = True
            apply(image, ev, expected, 'forward', 'new', 6)
            api_image(expected, 'forward', 6)  # #90: only the api container's image, still inside the window
            start(API, expected, 6, 'reopen', 'new')
            if not health():
                raise Stop(6, 'reopen: /api/health is not 200')
            no_runner_pods(8, 'before the controller')
            c5 = check_owners(expected, 8, 'before the controller', 'new')
            if any(fence_of(c5[o]) != RUN['before'][o] for o in OWNERS):
                raise Stop(8, 'before the controller: a fence differs from the window start')
            start(CTRL, expected, 6, 'reopen')
            observe(expected)
            final(expected)
            changing = False  # the reopen has ended: a later failure is not an in-window undo
            say('window end %s (switched)' % utc())
            record(windowEnd=utc())
        except Stop as stop:
            code, message = stop.code, stop.message
        except Interrupted:
            code, message = (6 if changing else 4), 'interrupted'
        except Exception:
            code, message = (6 if changing else (4 if SCALED or EV else 3)), 'unexpected error'
    except BaseException:
        if not message:
            code, message = (6 if changing else 4), 'interrupted'
    return finish(code, message, dsn, image, changing, expected)


def final(expected):
    """Everything was verified step by step; a difference now is an outside change: rc 8, never an undo."""
    for spec in (API, CTRL):
        _, want, ready, _, d = deployment(spec, 8)
        if template_of(spec, d, 8) != wanted(spec, expected, 'new') or (want, ready) != (1, 1):
            raise Stop(8, 'final: %s is not as wanted at 1/1' % spec[1])
    by = check_owners(expected, 8, 'final', 'new')
    if any(fence_of(by[o]) != RUN['before'][o] for o in OWNERS):
        raise Stop(8, 'final: a fence differs from the window start')
    no_runner_pods(8, 'final')
    for owner in OWNERS:
        say('final …%s fence %s plan %s image %s' % (short(owner), json.dumps(fence_of(by[owner]), sort_keys=True),
                                                    digest(by[owner]['plan']), by[owner]['plan'].get('image')))


def route(code, changing, stuck):
    """What finish() does first: 'freeze' (80 or 8), 'undo' (a failure after apply began), 'reopen' (a failure
    after the window was entered, before any change) or 'none'."""
    if stuck or code in (80, 8):
        return 'freeze'
    if changing and code != 0:
        return 'undo'
    if code != 0 and SCALED:
        return 'reopen'
    return 'none'


def finish(code, message, dsn, image, changing, expected):
    """Cleanup nothing can skip: tool containers proved gone (else 80) -> freeze / undo / reopen -> the admin DSN
    removed and proved absent -> final evidence. A DSN or evidence failure turns 0/4/5/6/9 into 70 (the main
    result is printed); 7/8/80 stay and print it."""
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, signal.SIG_IGN)
        except Exception:
            pass
    if message:
        say('STOPPED (%d): %s' % (code, message))
    main_code, cleanup, stuck, outcome = code, [], [], None
    for name in CONTAINERS:
        try:
            if not proved_gone(name):
                stuck.append(name)
        except BaseException:
            stuck.append(name)
    step = route(code, changing, stuck)
    if step == 'freeze':
        code = 80 if stuck or code == 80 else 8
        say('a tool container was not proved stopped: the DB writers stay stopped' if code == 80 else
            'unexpected state: nothing is undone; runner-controller and api are held at 0')
        say(freeze_line(hold((CTRL, API))))
    elif step == 'undo':
        try:
            outcome = undo(image, EV[0], expected)
        except BaseException:
            outcome = 'undo failed'
        say('undo result: %s' % (outcome + (', state = before' if outcome == 'undo not needed' else '')))
        if outcome in ('undo ok', 'undo not needed'):
            ok, why = reopen(expected, 'undo reopen')
            if ok:
                code = 6 if outcome == 'undo ok' else 9
            else:
                code = 7
                say('REOPEN FAILED: %s' % why)
                say(freeze_line(hold((CTRL, API))))
        else:
            code = 8
            say(freeze_line(hold((CTRL, API))))
    elif step == 'reopen':
        ok, why = (False, 'expected file not loaded') if expected is None else reopen(expected, 'reopen')
        if not ok:
            code = 7
            say('REOPEN FAILED: %s' % why)
            say(freeze_line(hold((CTRL, API))))
    result = code
    if dsn:
        if not remove_dsn(dsn):
            cleanup.append('admin DSN not removed')
        say('admin DSN %s' % ('STILL PRESENT' if os.path.lexists(dsn) else 'absent'))
    try:
        if not EV:
            raise OSError
        record(result=code, outcome=result, stopCode=main_code, undo=outcome, cleanupFailed=cleanup, finished=utc())
    except BaseException:
        if EV:
            cleanup.append('final evidence not written')
    if cleanup and code in (0, 4, 5, 6, 9):
        code = 70
    if cleanup:
        say('CLEANUP FAILED: %s' % ', '.join(cleanup))
        if code != result:
            say('main result rc=%d' % result)
    say('result rc=%d' % code)
    return code


def remove_dsn(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except Exception:
        return False
    return not os.path.lexists(path)


if __name__ == '__main__':
    sys.exit(main(sys.argv))
