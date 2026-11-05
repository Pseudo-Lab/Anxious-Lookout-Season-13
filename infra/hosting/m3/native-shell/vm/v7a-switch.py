"""#68 dev-VM stage V7a: the native-shell switch window (native-shell/README.md section 3, steps 1-5) up to the
canary, on the development VM only. Entry stays frozen to the canary owner …5804 afterwards (until V7b).

    python3 -I /root/native68/v7a-switch.py precheck   # read-only: changes nothing, writes no evidence
    python3 -I /root/native68/v7a-switch.py run        # once, by the user as root, after review cleared it

Inputs: v7a-expected.json next to this script (sha256 EXPECTED_SHA): the canary's tool inputs and the exact tool
reports for the forward swap and for its reverse (the in-run undo), the other four plan digests, and the
SHA-256 of the controller inputs.json as read in the preflight.

run, in this order:
1. precheck (read-only, rc 3): no earlier V7a evidence; host issue16 mutators disabled and inactive, no guest
   container, no runner Pod; node prerequisites (SELinux Enforcing, ptrace_scope 1, seccomp profile digest, SELinux module
   anxious_runner_bwrap stored = version 2 at priority 400); k3s images (the #68 runtime tag with its manifest
   and image ID, the #68 runner digest reference, the old references a rollback needs); api and
   runner-controller are the recorded Deployments (UID), 1/1 ready, each container on its recorded old image (the
   #61 runtime; the api's runner-pool-input init container on the earlier R4 runtime), both allowlists the five
   fixture owners; exactly the five fixture owners in registry and bindings, every one idle_stopped with no
   Pod, no pending work and a completed proof for its generation; the canary's fence and plan digest are the
   expected ones, the other four plan digests too; inputs.json is root 0600, not a link, has `executor`, and has
   the expected SHA-256; /api/health 200.
2. prepare (rc 4; changes no workload): evidence directory (root 0700); backups: both Deployments (JSON), all
   five plans, inputs.json (byte copy), a pg_dump of database hosting; the #68 runtime digest reference in k3s
   (tag, as W1b did), the #68 runtime image in guest Docker (archive SHA-256 checked first); the tool modules in
   that image are the reviewed ones.
3. window (rc 4): runner-controller then api scaled to 0 by UID-preconditioned patch, their Pods gone, 0 client
   sessions on database hosting, every value of step 1 unchanged. Admin DSN as in #61 W3b (root 0600, removed on
   every exit path, never printed).
4. forward report (rc 5): the tool's `report` for the canary equals the approved one. No change ran yet.
5. changes, each recorded before it runs:
   c1 inputs.json without `executor` (temporary file in the same directory, root 0600, fsync, rename; the
      parsed result equals the old one minus `executor`);
   c2 the canary binding: the tool's `apply` (runner image only; the v2 plan keeps its `executor`); after it,
      the canary plan digest is the approved new one and the other four are unchanged;
   c3 both Deployment templates, while at 0, by one JSON Patch each whose `test` ops pin the UID,
      resourceVersion, replicas 0, container names, the old images and the old allowlist: image (api, its
      runner-pool-input init container, controller) -> the #68 runtime digest reference, and
      CODEX_USER_RUNNER_ALLOWLIST -> the canary owner only (both allowlists, README section 3 step 5);
   c4 api to 1: rollout, the Pod runs the #68 runtime (spec image and imageID), /api/health 200;
   c5 before the controller starts: every owner still idle_stopped, no pending work, fences unchanged, no
      runner Pod;
   c6 runner-controller to 1: rollout, the Pod runs the #68 runtime; then OBSERVE seconds of observation: no
      runner Pod appears and no fence changes.
6. postcheck (rc 0): the final state printed: images, allowlists, inputs.json, plans, fences, no runner Pod.
   Entry stays frozen to the canary. The canary's first turn (V6-pre) starts its runner.

Two kinds of failure are kept apart. An unexpected state (a runner Pod appears; a fence, plan or pending-work
check fails in c5, the c6 observation or final; a plan digest is neither old nor new; another plan changed) is
rc 8: nothing is undone, runner-controller is held at 0. Any other failure or an interrupt after c1 began is
undone (nothing is retried): both Deployments to 0 by UID; their templates back by JSON Patch (`test` = the new
values); the canary binding back by the tool (reverse report must equal the approved one, then `apply`);
inputs.json back to the backed-up bytes (same rename).

Reopen (after a complete undo, or after a failure before any change): entry opens to the five owners again
only after the window-start conditions are verified again: host mutators disabled/inactive, no guest container,
no runner Pod; all five owners idle_stopped, no pending work, the same fences as at the window start, the canary
plan old and the other four unchanged; both templates on their old images and the five-owner allowlist, at 0;
inputs.json the original bytes. Then api to 1 (old images, health 200) and only then runner-controller to 1.
Any failure stops there and both Deployments are held at 0.

Holding at 0 (rc 7, 8, 80) is done and proved (replicas 0, Pods gone); the printed line says FREEZE HELD
(verified) or FREEZE NOT CONFIRMED (tell PM now).

rc (never rerun on a nonzero rc; back checks the evidence read-only):
  0  switched and verified; entry frozen to the canary; admin DSN removed; final evidence written
  3  precheck refused: nothing changed
  4  prepare/window/DSN/tool step before any change failed: no workload change (images may have been tagged or
     loaded, printed); reopened after the checks
  5  the forward report was refused or differs: no change; reopened after the checks
  6  a change or check failed: every change undone and verified, reopened after the checks
  7  entry held closed: an undo item or the reopen (its checks, the api, its health or the controller) failed,
     printed per item; both Deployments held at 0 (the freeze line says whether that was proved)
  8  unexpected state (above): runner-controller held at 0, nothing else touched; back checks read-only
  70 the main result was 0, 4, 5 or 6 (printed as "main result") but the admin DSN was not removed or the final
     evidence was not written
  80 a tool container was not proved stopped: both Deployments held at 0, admin DSN removed; manual via PM
A cleanup failure on 7, 8 or 80 keeps that code and is printed (CLEANUP FAILED).
Output is fixed messages, states, UIDs and digests only: no DSN, password, Secret data or command error text.
"""
import base64
import bz2
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

HERE = os.path.dirname(os.path.abspath(__file__))
EXPECTED_FILE = os.path.join(HERE, 'v7a-expected.json')
EXPECTED_SHA = 'e57f0cd8a33a0c58b764187561d3b2f61fe84ba732d9233f4c5dd522ca0f6c6e'
CANARY = '00000000-0000-4000-8000-000000005804'
OWNERS = ('00000000-0000-4000-8000-000000000048', '00000000-0000-4000-8000-000000005801',
          '00000000-0000-4000-8000-000000005802', '00000000-0000-4000-8000-000000005803', CANARY)
ALLOW_OLD = ','.join(OWNERS)
ALLOW_NEW = CANARY
LIB = 'docker.io/library/'
OLD_RUNTIME_REF = LIB + 'anxious-dev-runtime@sha256:eb3a18acd1b2856f6a95c7686c0c0ee4e5194b6237266b3443664f37feb3377c'
# The api's runner-pool-input init container was left on the earlier (R4, 50ac30b) runtime by #61 W2, which
# patched only the api container. Each container returns to its own old image on undo.
OLD_POOL_INPUT_REF = LIB + 'anxious-dev-runtime@sha256:3fa616abd13915089d80fdabdfad1fdd41ca0b6d3459ec3483fec3223a6f0dc4'
OLD_IMAGES = {'api': OLD_RUNTIME_REF, 'runner-pool-input': OLD_POOL_INPUT_REF, 'controller': OLD_RUNTIME_REF}
RUNTIME_TAG = LIB + 'anxious-dev-runtime:68-42a1e8b'
RUNTIME_MANIFEST = 'sha256:f5a84ff5d7bf6f00a79fa3d421a8176e6198436d8266b91370f617475a159db5'
RUNTIME_ID = 'sha256:edf33d2daa1968223d870147311707967fdeceb732d34be2448c926236fe6648'
RUNTIME_REF = LIB + 'anxious-dev-runtime@' + RUNTIME_MANIFEST
RUNNER_REF = LIB + 'anxious-dev-runner@sha256:840679946e8071b5495b9609c6e9f33c0708d9196e712e262361e200d4641801'
OLD_RUNNER_REF = LIB + 'anxious-dev-runner@sha256:067c77de5d6b3673ece9b243f7753a9184a007cd44ab0ea1c93dc3044458a5e4'
OLD_EXECUTOR_REF = LIB + 'anxious-dev-shell-executor@sha256:9c87d626cdf6798db5bc5a562cc68af1b13a36c8f90d3738e19c6553e7cf4d12'
# Reference -> CRI image ID (the V1 chain for #68, the #61 W1 chain for the old ones).
RESOLVE = {RUNNER_REF: 'sha256:ddd7bad4ea352eff5d905be329e7acf32c5ff415b52c8e9ae4a759e6b714f6c0',
           OLD_RUNTIME_REF: 'sha256:80af8c59f978f7a7d59d02f71985f3d456ce2e8011ed2d534395297188830771',
           OLD_POOL_INPUT_REF: 'sha256:90eaaf8bd68e6dfbdf41b7beed398eac972fac3cfa2980c21f4587c11ff9fd22',
           OLD_RUNNER_REF: 'sha256:4cac37e2173d5aec7b351e05c2977f2e0df76670f3befb91ce54306d2444e8ad',
           OLD_EXECUTOR_REF: 'sha256:49b449cb31c5b344f876a9d9aee2d97dd3dcae906c3324bc0eaf00678dd6b883'}
ARCHIVE = '/home/opc/native68-images/anxious-dev-runtime-68-42a1e8b.tar'
ARCHIVE_SHA = '559e0ee399a7dc9fd50adc3588c97f813692a361f9c600a59480550739b94f65'
TOOL_TAG = 'anxious-dev-runtime:68-42a1e8b'
TOOL_IDS = {RUNTIME_MANIFEST, RUNTIME_ID}  # containerd store: manifest digest; classic store: config ID
MODULES = {'/app/ops/runner_binding_image.py': '0a60c7d4f3b8765bb9fb7f204f47917e49d5a5b9c3bc5b0cd48eb8a6ed632d7e',
           '/app/app/runner_bindings.py': '0cc5706f8db9193d1b1ff61dac46533a62a09eb951dfff39f2d2b2d0160e5342'}
SECCOMP_FILE = '/var/lib/kubelet/seccomp/profiles/anxious-runner-userns-v1.json'
SECCOMP_SHA = '9a6594419eae4089e737c787597d2ed4950fd7e1031e371f490d5b2c935dff78'
MODULE_HLL = '/var/lib/selinux/targeted/active/modules/400/anxious_runner_bwrap/hll'
PP2_SHA = '3f4fc25cae5bfdceb86e1026f3ffbc72f1194c1a8e7cb75178e968dd159dbeea'
INPUTS = '/etc/anxious-lookout/issue16/controller/inputs.json'
# (namespace, name, recorded UID, Pod label, containers, init containers)
CTRL = ('runner-control', 'runner-controller', '41564f26-95e7-4ad1-8af3-d73b93c4669a', 'app=runner-controller',
        ('controller',), ())
API = ('m2-hosting', 'api', 'fad6799a-6df5-4086-812f-cfae6212adfa', 'app=hosting-api', ('api',),
       ('runner-pool-input',))
UNITS = ['issue16-controller.service', 'issue16-observer.service', 'issue16-supply.service',
         'issue16-controller.timer', 'issue16-observer.timer', 'issue16-supply.timer', 'issue16-restore.timer']
EVIDENCE = '/root/native68/evidence'
K3S = '/usr/local/bin/k3s'
DOCKER = '/usr/bin/docker'
SYSTEMCTL = '/usr/bin/systemctl'
CURL = '/usr/bin/curl'
SEMODULE = '/usr/sbin/semodule'
HEALTH = ['-s', '-o', '/dev/null', '-w', '%{http_code}', '--max-time', '10',
          '--resolve', '192.88.99.1:80:10.0.0.107', 'http://192.88.99.1/api/health']
POD_CIDR = ipaddress.ip_network('10.42.0.0/16')
SCHEMES = {'postgresql', 'postgres', 'postgresql+psycopg', 'postgresql+psycopg2'}
INPUT_KEYS = {'owner', 'state', 'generation', 'revision', 'oldPlanSha256', 'newImage'}
REPORT_KEYS = {'owner', 'state', 'generation', 'revision', 'oldImage', 'newImage', 'oldPlanSha256', 'newPlanSha256'}
ALLOW_ENV = 'CODEX_USER_RUNNER_ALLOWLIST'
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


# Database (read-only) ---------------------------------------------------------------------------------
def rows(tag, query, code):
    """JSON rows (one json_build_object per line) of a READ ONLY query as postgres in postgres-0."""
    text = 'BEGIN READ ONLY;\n/*v7a:%s*/ %s\nCOMMIT;\n' % (tag, query)
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
    """Every registry row joined to its binding: fence, plan, pending work, current-generation proof."""
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
                  "'admissions',(SELECT count(*) FROM runner_runtime.admissions WHERE completed_at IS NULL));", code)
    by = {row.get('owner'): row for row in found}
    if len(found) != len(OWNERS) or sorted(by) != sorted(OWNERS):
        raise Stop(code, 'registry/bindings are not exactly the five fixture owners')
    if any(not isinstance(row.get('plan'), dict) for row in found):
        raise Stop(code, 'binding plan unreadable')
    if len(totals) != 1 or any(totals[0].get(k) != 0 for k in ('openStops', 'openEntries', 'outbox', 'admissions')):
        raise Stop(code, 'pending runner work exists: %s' % json.dumps(totals[0] if totals else None, sort_keys=True))
    return by


def fence_of(row):
    return {k: row.get(k) for k in ('state', 'phase', 'generation', 'revision', 'pod', 'active', 'ambiguous')}


def clients(code):
    found = rows('clients', "SELECT json_build_object('clients',count(*),'roles',coalesce(string_agg(DISTINCT "
                 "usename::text,',' ORDER BY usename::text),'')) FROM pg_stat_activity WHERE datname='hosting' AND "
                 "backend_type='client backend' AND pid<>pg_backend_pid();", code)
    if len(found) != 1 or not isinstance(found[0].get('clients'), int):
        raise Stop(code, 'session lookup unreadable')
    return found[0]['clients'], found[0].get('roles') or ''


def check_owners(expected, code, where, canary_plan):
    """Every owner stopped and idle with a proof; the canary on `canary_plan` ('old'/'new'), the others unchanged."""
    by = owners_state(code)
    forward = expected['forward']['inputs']
    for owner in OWNERS:
        row = by[owner]
        if row['phase'] != 'idle_stopped' or row['pod'] is not None or row['active'] != 0 or row['ambiguous']:
            raise Stop(code, '%s: …%s fence is %s' % (where, short(owner), json.dumps(fence_of(row), sort_keys=True)))
        if row['bindingState'] != row['state']:
            raise Stop(code, '%s: …%s binding is on another state' % (where, short(owner)))
        pending = {k: row[k] for k in ('outbox', 'entries', 'stops', 'admissions')}
        if any(pending.values()):
            raise Stop(code, '%s: …%s has pending work %s' % (where, short(owner), json.dumps(pending, sort_keys=True)))
        if row['proof'] < 1:
            raise Stop(code, '%s: …%s has no completed proof for its current generation' % (where, short(owner)))
        if owner != CANARY and digest(row['plan']) != expected['otherPlans'][owner]:
            raise Stop(code, '%s: …%s plan digest is not the recorded one' % (where, short(owner)))
    row, plan = by[CANARY], by[CANARY]['plan']
    want = {'state': forward['state'], 'phase': 'idle_stopped', 'generation': forward['generation'],
            'revision': forward['revision'], 'pod': None, 'active': 0, 'ambiguous': False}
    if fence_of(row) != want:
        raise Stop(code, '%s: canary fence is %s' % (where, json.dumps(fence_of(row), sort_keys=True)))
    if canary_plan_state(plan, expected) != canary_plan:
        raise Stop(code, '%s: canary plan is %s, want %s' % (where, canary_plan_state(plan, expected), canary_plan))
    return by


def canary_plan_state(plan, expected):
    got = digest(plan)
    executor = (plan.get('executor') or {}).get('image')
    if got == expected['forward']['inputs']['oldPlanSha256'] and plan.get('image') == OLD_RUNNER_REF \
            and executor == OLD_EXECUTOR_REF:
        return 'old'
    if got == expected['forward']['report']['newPlanSha256'] and plan.get('image') == RUNNER_REF \
            and executor == OLD_EXECUTOR_REF:
        return 'new'
    return 'other'


# Kubernetes and host ----------------------------------------------------------------------------------
def deployment_json(spec, code):
    ns, name = spec[0], spec[1]
    out = kubectl(['-n', ns, 'get', 'deployment', name, '-o', 'json'], code, '%s lookup failed' % name)
    try:
        return json.loads(out)
    except ValueError:
        raise Stop(code, '%s unreadable' % name) from None


def template_of(spec, d, code):
    """(images by container, allowlist value, allowlist env index) of the Deployment's template."""
    try:
        pod = d['spec']['template']['spec']
        containers, inits = pod['containers'], pod.get('initContainers') or []
        if tuple(c['name'] for c in containers) != spec[4] or tuple(c['name'] for c in inits) != spec[5]:
            raise ValueError
        images = {c['name']: c['image'] for c in containers + inits}
        env = containers[0].get('env') or []
        index = [i for i, e in enumerate(env) if e.get('name') == ALLOW_ENV]
        single = [e for e in env if e.get('name') == 'CODEX_USER_RUNNER_ACCOUNT_ID']
        if len(index) != 1 or single or 'value' not in env[index[0]] or 'valueFrom' in env[index[0]]:
            raise ValueError
        return images, env[index[0]]['value'], index[0]
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
        # An installed unit must be disabled; a unit with no unit file must still be inactive.
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


def runtime_tag_manifest():
    rc, out = run([K3S, 'ctr', '-n', 'k8s.io', 'images', 'ls'], 30)
    if rc != 0:
        return None
    found = [line.split()[2] for line in out.splitlines()[1:] if line.split() and line.split()[0] == RUNTIME_TAG]
    return found[0] if len(found) == 1 else ''


def node_prerequisites(code):
    try:
        with open('/sys/fs/selinux/enforce') as stream:
            enforcing = stream.read().strip() == '1'
    except Exception:
        enforcing = False
    if not enforcing:
        raise Stop(code, 'SELinux is not Enforcing')
    rc, scope = run(['/usr/sbin/sysctl', '-n', 'kernel.yama.ptrace_scope'])
    if rc != 0 or scope != '1':
        raise Stop(code, 'kernel.yama.ptrace_scope is %s, not 1' % (scope or 'unreadable'))
    try:
        if os.path.islink(SECCOMP_FILE) or sha_file(SECCOMP_FILE) != SECCOMP_SHA:
            raise ValueError
    except Exception:
        raise Stop(code, 'seccomp profile is not the reviewed one') from None
    rc, listing = run([SEMODULE, '-lfull'], 60)
    entries = [line.split() for line in listing.splitlines() if len(line.split()) >= 2
               and line.split()[1] == 'anxious_runner_bwrap']
    if rc != 0 or [e[0] for e in entries] != ['400'] or 'disabled' in entries[0]:
        raise Stop(code, 'SELinux module anxious_runner_bwrap is not one active entry at priority 400')
    try:
        with open(MODULE_HLL, 'rb') as stream:
            stored = hashlib.sha256(bz2.decompress(stream.read())).hexdigest()
    except Exception:
        raise Stop(code, 'SELinux stored module unreadable') from None
    if stored != PP2_SHA:
        raise Stop(code, 'SELinux stored module is not version 2')


def inputs_file(code, want_sha):
    """Parsed inputs.json after checking it is a root 0600 regular file with the wanted SHA-256."""
    try:
        info = os.lstat(INPUTS)
        with open(INPUTS, 'rb') as stream:
            data = stream.read()
    except Exception:
        raise Stop(code, 'inputs.json unreadable') from None
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
        raise Stop(code, 'inputs.json is not a root 0600 regular file')
    if hashlib.sha256(data).hexdigest() != want_sha:
        raise Stop(code, 'inputs.json SHA-256 is not the expected one')
    try:
        value = json.loads(data)
    except ValueError:
        raise Stop(code, 'inputs.json unreadable') from None
    if not isinstance(value, dict):
        raise Stop(code, 'inputs.json unreadable')
    return value, data


def health():
    rc, got = run([CURL] + HEALTH, 20)
    return rc == 0 and got == '200'


# Expected file and precheck ---------------------------------------------------------------------------
def load_expected():
    try:
        info = os.lstat(EXPECTED_FILE)
        with open(EXPECTED_FILE, 'rb') as stream:
            data = stream.read()
    except Exception:
        raise Stop(3, 'v7a-expected.json missing') from None
    if not stat.S_ISREG(info.st_mode) or hashlib.sha256(data).hexdigest() != EXPECTED_SHA:
        raise Stop(3, 'v7a-expected.json is not the approved file')
    doc = json.loads(data)
    try:
        f_in, f_rep = doc['forward']['inputs'], doc['forward']['report']
        r_in, r_rep = doc['reverse']['inputs'], doc['reverse']['report']
        ok = (doc['canary'] == CANARY and set(f_in) == INPUT_KEYS and set(r_in) == INPUT_KEYS
              and set(f_rep) == REPORT_KEYS and set(r_rep) == REPORT_KEYS
              and f_in['owner'] == CANARY and f_in['newImage'] == RUNNER_REF and r_in['newImage'] == OLD_RUNNER_REF
              and f_rep['oldImage'] == OLD_RUNNER_REF and f_rep['newImage'] == RUNNER_REF
              and r_rep['oldImage'] == RUNNER_REF and r_rep['newImage'] == OLD_RUNNER_REF
              and all(f_rep[k] == f_in[k] and r_in[k] == f_in[k] and r_rep[k] == f_in[k]
                      for k in ('owner', 'state', 'generation', 'revision'))
              and f_rep['oldPlanSha256'] == f_in['oldPlanSha256']
              and r_in['oldPlanSha256'] == f_rep['newPlanSha256'] == r_rep['oldPlanSha256']
              and r_rep['newPlanSha256'] == f_in['oldPlanSha256']
              and sorted(doc['otherPlans']) == sorted(o for o in OWNERS if o != CANARY)
              and isinstance(doc['inputsSha256'], str) and len(doc['inputsSha256']) == 64)
    except Exception:
        ok = False
    if not ok:
        raise Stop(3, 'v7a-expected.json fields disagree')
    return doc


def precheck(expected, readonly):
    if os.geteuid() != 0:
        raise Stop(3, 'precheck: not root')
    # A run directory is v7a-<UTC stamp>; preserved script copies (v7a-switch.py.rN) are not runs.
    runs = [d for d in os.listdir(EVIDENCE) if re.fullmatch(r'v7a-\d{8}T\d{6}Z', d)] if os.path.isdir(EVIDENCE) else []
    if runs:
        raise Stop(3, 'precheck: an earlier V7a run left evidence (%s); never rerun, back checks' % ', '.join(runs))
    host_quiet(3)
    node_prerequisites(3)
    if runtime_tag_manifest() != RUNTIME_MANIFEST or resolve(RUNTIME_TAG) != RUNTIME_ID:
        raise Stop(3, 'precheck: %s is not the V1-imported #68 runtime image' % RUNTIME_TAG)
    runtime_ref = resolve(RUNTIME_REF)
    if runtime_ref not in ('', RUNTIME_ID):
        raise Stop(3, 'precheck: %s resolves to another image' % RUNTIME_REF)
    for ref, want in sorted(RESOLVE.items()):
        if resolve(ref) != want:
            raise Stop(3, 'precheck: %s does not resolve to %s' % (ref, want))
    replicas, templates = {}, {}
    for spec in (CTRL, API):
        uid, want, ready, _, d = deployment(spec, 3)
        if uid != spec[2] or want != 1 or ready != 1:
            raise Stop(3, 'precheck: %s is uid=%s replicas=%d ready=%d (want %s 1/1)' % (spec[1], uid, want, ready, spec[2]))
        images, allow, _ = template_of(spec, d, 3)
        if images != old_images(spec) or allow != ALLOW_OLD:
            raise Stop(3, 'precheck: %s template images/allowlist are not the recorded #61 ones' % spec[1])
        replicas[spec[1]], templates[spec[1]] = want, d
    by = check_owners(expected, 3, 'precheck', 'old')
    value, _ = inputs_file(3, expected['inputsSha256'])
    if 'executor' not in value:
        raise Stop(3, 'precheck: inputs.json has no executor (already switched?)')
    if not health():
        raise Stop(3, 'precheck: /api/health is not 200')
    rc, docker_id = run([DOCKER, 'image', 'inspect', '--format', '{{.Id}}', TOOL_TAG], 30)
    docker_id = docker_id if rc == 0 else ''
    if docker_id and docker_id not in TOOL_IDS:
        raise Stop(3, 'precheck: guest Docker %s is another image' % TOOL_TAG)
    if not docker_id:
        try:
            if os.path.islink(ARCHIVE) or sha_file(ARCHIVE) != ARCHIVE_SHA:
                raise ValueError
        except Exception:
            raise Stop(3, 'precheck: runtime archive is not the V1 one') from None
    count, roles = clients(3)
    say('precheck ok%s: host mutators disabled/inactive, no guest container, no runner Pod; SELinux Enforcing, ptrace_scope 1, seccomp '
        'profile, SELinux module v2 at 400; #68 runtime tag ok, digest reference %s; #68 runner and old references '
        'resolve; %s %s and %s %s 1/1 on their recorded old images, both allowlists the five owners; five fixture owners '
        'idle_stopped with proofs, no pending work; inputs.json as expected (has executor); health 200; guest '
        'Docker tool image %s; sessions now %d (%s)'
        % (' (read-only)' if readonly else '', 'present' if runtime_ref else 'to tag', CTRL[1], CTRL[2], API[1],
           API[2], docker_id or 'to load (archive ok)', count, roles or 'none'))
    for owner in OWNERS:
        say('  …%s fence %s plan %s' % (short(owner), json.dumps(fence_of(by[owner]), sort_keys=True),
                                       digest(by[owner]['plan'])))
    return replicas, templates, by, docker_id


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
def prepare(ev, templates, by, docker_id):
    for spec in (CTRL, API):
        write_new(os.path.join(ev, 'deployment-%s.json' % spec[1]), json.dumps(templates[spec[1]], sort_keys=True).encode())
    write_new(os.path.join(ev, 'before-plans.json'), json.dumps({o: by[o]['plan'] for o in OWNERS}, sort_keys=True).encode())
    _, data = inputs_file(4, RUN['inputsSha256'])
    write_new(os.path.join(ev, 'private', 'inputs.json.before'), data)
    dump = os.path.join(ev, 'private', 'hosting.pgdump')
    fd = os.open(dump, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        done = subprocess.run([K3S, 'kubectl', '-n', 'm2-hosting', 'exec', 'postgres-0', '--', 'pg_dump', '-U',
                               'postgres', '-d', 'hosting', '-Fc'], stdout=fd, stderr=subprocess.DEVNULL, timeout=300)
        os.fsync(fd)
    except Exception:
        raise Stop(4, 'prepare: pg_dump failed') from None
    finally:
        os.close(fd)
    size = os.path.getsize(dump)
    if done.returncode != 0 or size < 1024:
        raise Stop(4, 'prepare: pg_dump failed')
    record(dbBackup={'bytes': size, 'sha256': sha_file(dump)})
    say('backups: both Deployments, five plans, inputs.json (%s), pg_dump of hosting (%d bytes, sha256 %s)'
        % (RUN['inputsSha256'], size, RUN['dbBackup']['sha256']))
    if resolve(RUNTIME_REF) != RUNTIME_ID:
        change('runtimeDigestReference', 'pending')
        must([K3S, 'ctr', '-n', 'k8s.io', 'images', 'tag', RUNTIME_TAG, RUNTIME_REF], 4,
             'prepare: tag of the #68 runtime digest reference failed', 30)
        if resolve(RUNTIME_REF) != RUNTIME_ID:
            raise Stop(4, 'prepare: %s does not resolve to %s after the tag' % (RUNTIME_REF, RUNTIME_ID))
        change('runtimeDigestReference', 'done')
        say('tagged %s (resolves to %s)' % (RUNTIME_REF, RUNTIME_ID))
    if not docker_id:
        if os.path.islink(ARCHIVE) or sha_file(ARCHIVE) != ARCHIVE_SHA:
            raise Stop(4, 'prepare: runtime archive changed')
        change('guestDockerLoad', 'pending')
        must([DOCKER, 'load', '-q', '-i', ARCHIVE], 4, 'prepare: docker load failed', 600)
        change('guestDockerLoad', 'done')
        say('loaded %s into guest Docker' % TOOL_TAG)
    image = must([DOCKER, 'image', 'inspect', '--format', '{{.Id}}', TOOL_TAG], 4, 'prepare: tool image lookup failed')
    if image not in TOOL_IDS:
        raise Stop(4, 'prepare: guest Docker %s is %s, not the #68 runtime image' % (TOOL_TAG, image or 'none'))
    name = 'native68-v7a-modules-%d' % os.getpid()
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
    record(toolImage=image)
    say('tool image %s, modules ok' % image)
    host_quiet(4)
    return image


# Window -----------------------------------------------------------------------------------------------
SCALED = []  # Deployments this run scaled


def scale(spec, replicas, code):
    ns, name, uid = spec[0], spec[1], spec[2]
    patch = {'metadata': {'uid': uid}, 'spec': {'replicas': replicas}}
    kubectl(['-n', ns, 'patch', 'deployment', name, '--type=merge', '-p', json.dumps(patch)], code,
            '%s scale to %d failed' % (name, replicas))


def gone(spec, code):
    ns, name, label = spec[0], spec[1], spec[3]
    deadline = time.monotonic() + WAIT
    while pods(ns, label, code):
        if time.monotonic() >= deadline:
            raise Stop(code, '%s Pods still present after %d s' % (name, WAIT))
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
    name = 'native68-v7a-%s-%s-%d' % (direction, action, os.getpid())
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
    want = {'owner': CANARY, 'stage': 'binding-image-' + action, 'result': 'verified'}
    lines = [l for l in out.splitlines() if l.startswith('{')]
    try:
        confirmed = rc == 0 and bool(lines) and json.loads(lines[-1]) == want
    except ValueError:
        confirmed = False
    return rc, confirmed


def report(image, ev, expected, direction, code):
    """The tool's report for `direction` must equal the approved one."""
    private_dir(os.path.join(ev, 'report-' + direction))
    write_new(os.path.join(ev, 'private', 'inputs-%s.json' % direction),
              json.dumps(expected[direction]['inputs']).encode())
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
    want = expected[direction]['report']
    if got != want:
        diff = sorted(k for k in set(got) | set(want) if got.get(k) != want.get(k))
        raise Stop(code, '%s report differs from the approved one in %s' % (direction, ','.join(diff)))
    record(**{'report-' + direction: hashlib.sha256(data).hexdigest()})
    say('%s report: equals the approved report (file sha256 %s)' % (direction, hashlib.sha256(data).hexdigest()))


def apply(image, ev, expected, direction, want_state, code):
    no_clients(code, '%s apply' % direction)
    change('binding-' + direction, 'pending')
    rc, confirmed = tool(image, ev, 'apply', direction)
    by = owners_state(8)
    state = canary_plan_state(by[CANARY]['plan'], expected)
    others = all(digest(by[o]['plan']) == expected['otherPlans'][o] for o in OWNERS if o != CANARY)
    change('binding-' + direction, 'done' if confirmed and state == want_state and others else 'uncertain')
    say('%s apply: tool rc %d, confirmed %s; canary plan now %s; other plans %s'
        % (direction, rc, confirmed, state, 'unchanged' if others else 'CHANGED'))
    if not others or state == 'other':
        raise Stop(8, '%s apply: a plan digest is neither old nor new, or another plan changed' % direction)
    if not (confirmed and state == want_state):
        raise Stop(code, '%s apply did not complete (canary plan %s)' % (direction, state))


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


# Changes ----------------------------------------------------------------------------------------------
def write_inputs(data, code, where):
    """Replace inputs.json by `data`: temporary file in the same directory (root 0600, fsync), rename, fsync dir."""
    folder = os.path.dirname(INPUTS)
    temp = os.path.join(folder, '.inputs.json.v7a-%d' % os.getpid())
    try:
        write_new(temp, data)
        os.chown(temp, 0, 0)
        os.rename(temp, INPUTS)
        dirfd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    except Exception:
        try:
            os.unlink(temp)
        except Exception:
            pass
        raise Stop(code, '%s: inputs.json write failed' % where) from None


def switch_inputs(code):
    old, _ = inputs_file(code, RUN['inputsSha256'])
    new = {k: v for k, v in old.items() if k != 'executor'}
    data = (json.dumps(new, indent=2, sort_keys=True) + '\n').encode()
    change('inputs', 'pending')
    write_inputs(data, code, 'c1')
    got, raw = inputs_file(code, hashlib.sha256(data).hexdigest())
    if got != new or 'executor' in got or set(old) - set(got) != {'executor'}:
        change('inputs', 'uncertain')
        raise Stop(code, 'c1: inputs.json after the write is not the old one minus executor')
    change('inputs', 'done')
    record(inputsAfterSha256=hashlib.sha256(raw).hexdigest())
    say('c1 inputs.json: executor removed, every other key unchanged (sha256 %s)' % hashlib.sha256(raw).hexdigest())


def old_images(spec):
    return {name: OLD_IMAGES[name] for name in spec[4] + spec[5]}


def new_images(spec):
    return {name: RUNTIME_REF for name in spec[4] + spec[5]}


def manifests(images):
    return {name: ref.split('@', 1)[1] for name, ref in images.items()}


def patch_template(spec, old, new, old_allow, new_allow, code, where):
    """One JSON Patch: tests pin UID, resourceVersion, replicas 0, names, images and allowlist; then replace.
    `old` and `new` map each container name to its image."""
    _, want, _, current, d = deployment(spec, code)
    images, allow, index = template_of(spec, d, code)
    if want != 0 or current != 0:
        raise Stop(code, '%s: %s is not at 0' % (where, spec[1]))
    if images != old or allow != old_allow:
        raise Stop(code, '%s: %s template is not the expected one' % (where, spec[1]))
    ops = [{'op': 'test', 'path': '/metadata/uid', 'value': spec[2]},
           {'op': 'test', 'path': '/metadata/resourceVersion', 'value': d['metadata']['resourceVersion']},
           {'op': 'test', 'path': '/spec/replicas', 'value': 0}]
    for kind, names in (('containers', spec[4]), ('initContainers', spec[5])):
        for i, name in enumerate(names):
            base = '/spec/template/spec/%s/%d' % (kind, i)
            ops += [{'op': 'test', 'path': base + '/name', 'value': name},
                    {'op': 'test', 'path': base + '/image', 'value': old[name]},
                    {'op': 'replace', 'path': base + '/image', 'value': new[name]}]
    env = '/spec/template/spec/containers/0/env/%d' % index
    ops += [{'op': 'test', 'path': env + '/name', 'value': ALLOW_ENV},
            {'op': 'test', 'path': env + '/value', 'value': old_allow},
            {'op': 'replace', 'path': env + '/value', 'value': new_allow}]
    change('template-' + spec[1] + '-' + where, 'pending')
    rc, _ = run([K3S, 'kubectl', '-n', spec[0], 'patch', 'deployment', spec[1], '--type=json', '-p', json.dumps(ops)])
    _, _, _, _, after = deployment(spec, code)
    images, allow, _ = template_of(spec, after, code)
    ok = images == new and allow == new_allow and after['metadata']['uid'] == spec[2]
    change('template-' + spec[1] + '-' + where, 'done' if ok else ('uncertain' if rc == 0 else 'failed'))
    if not ok:
        raise Stop(code, '%s: %s template patch failed' % (where, spec[1]))
    say('%s %s: images -> %s, %s -> %s' % (where, spec[1], ', '.join('%s %s' % (n, r.split('@')[1][:19])
                                                                      for n, r in sorted(new.items())),
                                           ALLOW_ENV, 'canary only' if new_allow == ALLOW_NEW else 'five owners'))


def start(spec, want_manifests, code, where):
    """Scale to 1 by UID; rollout; one ready Pod whose containers run `want_manifests` (name -> digest)."""
    scale(spec, 1, code)
    if run([K3S, 'kubectl', '-n', spec[0], 'rollout', 'status', 'deployment/' + spec[1], '--timeout=180s'], 210)[0]:
        raise Stop(code, '%s: %s rollout did not finish' % (where, spec[1]))
    uid, want, ready, _, _ = deployment(spec, code)
    if uid != spec[2] or want != 1 or ready != 1:
        raise Stop(code, '%s: %s is not 1/1' % (where, spec[1]))
    out = kubectl(['-n', spec[0], 'get', 'pods', '-l', spec[3], '-o', 'json'], code, '%s Pod lookup failed' % spec[1])
    try:
        items = [p for p in json.loads(out)['items'] if not p['metadata'].get('deletionTimestamp')]
        statuses = items[0]['status'].get('containerStatuses', []) + items[0]['status'].get('initContainerStatuses', [])
        ids = {s['name']: s.get('imageID', '').rsplit('@', 1)[-1] for s in statuses}
        ok = len(items) == 1 and ids == want_manifests
    except Exception:
        ok = False
    if not ok:
        raise Stop(code, '%s: %s Pod does not run the expected images' % (where, spec[1]))
    say('%s %s: 1/1, Pod runs %s' % (where, spec[1], ', '.join('%s %s' % (n, m[:19])
                                                               for n, m in sorted(want_manifests.items()))))


def no_runner_pods(code, where):
    found = pods('codex-runner', None, code)
    if found:
        raise Stop(8, '%s: runner Pod(s) present: %s' % (where, ' '.join(found)))


def observe(expected, before):
    deadline = time.monotonic() + OBSERVE
    while True:
        no_runner_pods(8, 'c6 observation')
        by = owners_state(8)
        if any(fence_of(by[o]) != fence_of(before[o]) for o in OWNERS):
            raise Stop(8, 'c6 observation: a fence changed')
        if time.monotonic() >= deadline:
            break
        time.sleep(10)
    say('c6 observation: %d s, no runner Pod, every fence unchanged' % OBSERVE)


# Undo -------------------------------------------------------------------------------------------------
def guarded(failed, item, fn, *args, **kwargs):
    try:
        if fn(*args, **kwargs) is False:
            failed.append(item)
    except BaseException:
        failed.append(item)


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


def freeze_line(proved, specs):
    names = ' and '.join(spec[1] for spec in specs)
    return ('FREEZE HELD: %s at 0 (verified)' % names) if proved else ('FREEZE NOT CONFIRMED: %s could not be '
                                                                        'proved at 0; tell PM now' % names)


def reopen_checks(expected, where):
    """Before entry is open to the five owners again: every window-start condition holds again (raises Stop 7).
    Host mutators disabled/inactive, no guest container, no runner Pod; all five owners idle_stopped with no
    pending work, the same fences as at the window start, the canary plan old and the others unchanged; both
    templates on their old images and the five-owner allowlist, at 0; inputs.json the original bytes."""
    host_quiet(7)
    by = check_owners(expected, 7, where, 'old')
    if any(fence_of(by[o]) != RUN['before'][o] for o in OWNERS):
        raise Stop(7, '%s: a fence differs from the window start' % where)
    for spec in (API, CTRL):
        _, want, _, current, d = deployment(spec, 7)
        images, allow, _ = template_of(spec, d, 7)
        if images != old_images(spec) or allow != ALLOW_OLD or (want, current) != (0, 0):
            raise Stop(7, '%s: %s is not on its old template at 0' % (where, spec[1]))
    value, _ = inputs_file(7, RUN['inputsSha256'])
    if 'executor' not in value:
        raise Stop(7, '%s: inputs.json is not the original' % where)


def reopen(expected, where):
    """Verify, then api, then (only if the api is 1/1 on its old images and healthy) runner-controller back to 1.
    Returns (True, '') or (False, why); never continues past a failed step (finish() then holds both at 0)."""
    try:
        stopped(7, where)
        reopen_checks(expected, where)
    except BaseException as error:
        return False, '%s refused: %s' % (where, message_of(error))
    for spec in (API, CTRL):
        try:
            start(spec, manifests(old_images(spec)), 7, where)
            if spec is API and not health():
                raise Stop(7, '%s: /api/health is not 200' % where)
        except BaseException as error:
            return False, '%s: %s' % (where, message_of(error))
    return True, ''


def undo(image, ev, expected):
    """Every change back, then reopen after the checks. Returns the failed items (empty = undone and reopened)."""
    failed = []
    changes = RUN.get('changes', {})
    guarded(failed, 'stop both Deployments', stopped, 7, 'undo')
    if failed:
        return failed  # nothing else without the window
    for spec in (API, CTRL):
        if changes.get('template-%s-c3' % spec[1]):
            def back(spec=spec):
                _, _, _, _, d = deployment(spec, 7)
                images, allow, _ = template_of(spec, d, 7)
                if images == old_images(spec) and allow == ALLOW_OLD:
                    return True  # the forward patch never applied
                patch_template(spec, new_images(spec), old_images(spec), ALLOW_NEW, ALLOW_OLD, 7, 'undo')
            guarded(failed, 'template ' + spec[1], back)
    if changes.get('binding-forward'):
        def binding():
            by = owners_state(7)
            state = canary_plan_state(by[CANARY]['plan'], expected)
            if state == 'old':
                return True  # reopen_checks still verifies every fence and plan
            if state != 'new':
                return False
            report(image, ev, expected, 'reverse', 7)
            apply(image, ev, expected, 'reverse', 'old', 7)
        guarded(failed, 'binding', binding)
    if changes.get('inputs'):
        def inputs():
            with open(os.path.join(ev, 'private', 'inputs.json.before'), 'rb') as stream:
                data = stream.read()
            if hashlib.sha256(data).hexdigest() != RUN['inputsSha256']:
                return False
            change('inputs-undo', 'pending')
            write_inputs(data, 7, 'undo')
            inputs_file(7, RUN['inputsSha256'])
            change('inputs-undo', 'done')
        guarded(failed, 'inputs.json', inputs)
    if failed:
        return failed  # any item not back: no reopen, both held at 0 by finish()
    ok, why = reopen(expected, 'undo reopen')
    return [] if ok else [why]


# Main -------------------------------------------------------------------------------------------------
def main(argv):
    if argv[1:] not in (['precheck'], ['run']):
        say('usage: v7a-switch.py precheck|run')
        return 2
    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    if argv[1] == 'precheck':
        try:
            precheck(load_expected(), True)
        except Stop as stop:
            say('STOPPED (%d): %s' % (stop.code, stop.message))
            say('result rc=%d' % stop.code)
            return stop.code
        say('result rc=0 (read-only)')
        return 0
    code, message, replicas, dsn, image, changing = 0, '', {}, None, None, False
    expected = None
    try:
        try:
            expected = load_expected()
            replicas, templates, before, docker_id = precheck(expected, False)
            ev = os.path.join(EVIDENCE, 'v7a-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()))
            private_dir(ev)
            private_dir(os.path.join(ev, 'private'))
            EV.append(ev)
            say('evidence: %s' % ev)
            record(started=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), expectedSha256=EXPECTED_SHA,
                   inputsSha256=expected['inputsSha256'], replicas=replicas,
                   before={o: fence_of(before[o]) for o in OWNERS})
            image = prepare(ev, templates, before, docker_id)
            stopped(4, 'window')
            host_quiet(4)
            check_owners(expected, 4, 'window', 'old')
            say('window ok: %s and %s at 0 with the same UIDs, their Pods gone; 0 client sessions on hosting'
                % (CTRL[1], API[1]))
            dsn = os.path.join(ev, 'private', 'admin-dsn')
            admin_dsn(dsn)
            report(image, ev, expected, 'forward', 5)
            changing = True
            switch_inputs(6)
            apply(image, ev, expected, 'forward', 'new', 6)
            for spec in (API, CTRL):
                patch_template(spec, old_images(spec), new_images(spec), ALLOW_OLD, ALLOW_NEW, 6, 'c3')
            start(API, manifests(new_images(API)), 6, 'c4')
            if not health():
                raise Stop(6, 'c4: /api/health is not 200')
            no_runner_pods(8, 'c5')
            check_owners(expected, 8, 'c5', 'new')
            start(CTRL, manifests(new_images(CTRL)), 6, 'c6')
            observe(expected, before)
            final(expected)
        except Stop as stop:
            code, message = stop.code, stop.message
        except Interrupted:
            code, message = (6 if changing else 4), 'interrupted'
        except Exception:
            code, message = (6 if changing else (4 if SCALED or EV else 3)), 'unexpected error'
    except BaseException:
        if not message:
            code, message = (6 if changing else 4), 'interrupted'
    return finish(code, message, replicas, dsn, image, changing, expected)


def final(expected):
    """Everything was verified step by step; a difference now is an outside change: rc 8, never an undo."""
    for spec in (API, CTRL):
        _, want, ready, _, d = deployment(spec, 8)
        images, allow, _ = template_of(spec, d, 8)
        say('final %s: %d/%d, images %s, %s = %s' % (spec[1], ready, want, ','.join(sorted(set(images.values()))),
                                                     ALLOW_ENV, allow))
        if images != new_images(spec) or allow != ALLOW_NEW or (want, ready) != (1, 1):
            raise Stop(8, 'final: %s is not as switched' % spec[1])
    value, _ = inputs_file(8, RUN['inputsAfterSha256'])
    by = check_owners(expected, 8, 'final', 'new')
    if any(fence_of(by[o]) != RUN['before'][o] for o in OWNERS):
        raise Stop(8, 'final: a fence differs from the window start')
    no_runner_pods(8, 'final')
    say('final inputs.json: keys %s (no executor)' % ','.join(sorted(value)))
    for owner in OWNERS:
        say('final …%s fence %s plan %s' % (short(owner), json.dumps(fence_of(by[owner]), sort_keys=True),
                                           digest(by[owner]['plan'])))


def finish(code, message, replicas, dsn, image, changing, expected):
    """Cleanup nothing can skip. Order: tool containers proved gone (else rc 80, both held at 0) -> rc 8: the
    controller held at 0 -> after a change: undo and reopen (else rc 7, both held at 0) -> before any change:
    reopen after the checks (else rc 7, both held at 0) -> admin DSN removed and proved absent -> final evidence.
    A DSN or evidence failure turns 0/4/5/6 into 70 (the main result is printed); 7/8/80 stay and print it."""
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, signal.SIG_IGN)
        except Exception:
            pass
    if message:
        say('STOPPED (%d): %s' % (code, message))
    main_code, cleanup, stuck = code, [], []
    for name in CONTAINERS:
        guarded(stuck, name, proved_gone, name)
    if stuck or code == 80:
        code = 80
        say('a tool container was not proved stopped (%s): the DB writers stay stopped'
            % (', '.join(stuck) or 'see above'))
        say(freeze_line(hold((CTRL, API)), (CTRL, API)))
    elif code == 8:
        say('unexpected state: runner-controller is held at 0, nothing else is touched; back checks read-only')
        say(freeze_line(hold((CTRL,)), (CTRL,)))
    elif changing and code != 0:
        try:
            undone = undo(image, EV[0], expected)
        except BaseException:
            undone = ['undo']
        if undone:
            code = 7
            say('UNDO INCOMPLETE: %s' % '; '.join(undone))
            say(freeze_line(hold((CTRL, API)), (CTRL, API)))
        else:
            code = 6
            say('undo: every change back and verified; reopened after the checks (old images, both allowlists five '
                'owners, canary binding old, inputs.json original, fences as at the window start, 1/1, health 200)')
    elif code != 0 and SCALED:
        ok, why = (False, 'expected file not loaded') if expected is None else reopen(expected, 'reopen')
        if ok:
            say('reopened after the checks: both Deployments 1/1 on their old images, health 200')
        else:
            code = 7
            say('REOPEN FAILED: %s' % why)
            say(freeze_line(hold((CTRL, API)), (CTRL, API)))
    result = code  # the outcome before cleanup: printed as "main result" if a cleanup failure turns it into 70

    def promoted(code):
        return 70 if code in (0, 4, 5, 6) else code
    if dsn:
        guarded(cleanup, 'admin DSN not removed', remove_dsn, dsn)
        say('admin DSN %s' % ('STILL PRESENT' if os.path.lexists(dsn) else 'absent'))
    if cleanup:
        code = promoted(code)
    try:
        if not EV:
            raise OSError
        record(result=code, outcome=result, stopCode=main_code, cleanupFailed=cleanup,
               finished=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
    except BaseException:
        if EV:
            cleanup.append('final evidence not written')
            code = promoted(code)
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
