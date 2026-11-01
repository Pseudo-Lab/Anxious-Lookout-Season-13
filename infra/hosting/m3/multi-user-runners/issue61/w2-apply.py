"""#61 dev-VM gate W2: install runner-controller next to the existing hosting stack, or roll it back.

Run once by the user as root in the guest, after review cleared this file's SHA-256 with the placeholders filled:
    python3 -I /root/issue61/w2-apply.py apply
    python3 -I /root/issue61/w2-apply.py rollback /root/issue61/evidence/w2-<stamp>

apply, in this order (every lookup, the render and every backup finish before the first change):
  precheck   the W0 conditions: host issue16 units inactive, no runner-control namespace, the three fixtures
             as recorded, no owner waiting for a Pod, the W1 images imported with the host image IDs
  render     ops.runner_rollout from the 5e73bc4 runtime image (guest Docker, no network, read-only root):
             values = /root/r4-rollout/values.json reduced to the #61 keys, the W1 image references and the
             live api allowlist; baseline = the live api Deployment. Nothing is applied from api-candidate.json.
  backup     every object W2 applies (all of resources.json, the api Deployment, CNP runner-callback-draft,
             Secret runner-controller-db): its JSON, or "absent"
  1 grants   backend/db/061_runner_controller_grants.sql (idempotent GRANTs) through psql in postgres-0
  2 ns       Namespace runner-control
  3 secret   runner-controller-db from the guest's controller/observer DSN files, host rewritten to
             postgres.m2-hosting.svc.cluster.local:5432; DSNs never printed, temp files removed
  4 rbac     every other resources.json object except the Deployment (host roles lose Pod verbs / become empty)
  5 cnp      user-runner-e2e/40-runner-network.json (8080 ingress from runner-control instead of host)
  6 api      api Deployment image -> the 5e73bc4 runtime reference (rollout status)
  7 deploy   Deployment runner-controller (rollout status), can-i checks, created UIDs recorded
After step 7 runner-controller starts its loop: …0048/…5802 take the dead-runner path on their own (back's
read-only postcheck records it). …5804 is left for W3 (R2).

rollback (restores what W2 changed; never claims to undo a reclaim):
  scale runner-controller to 0 and wait for its Pod; list open v2/v3/v4 stops (recorded, not finished here);
  replace every object that existed before W2 with its backup (server fields stripped); delete only objects
  that were absent before W2 and whose live UID equals the UID recorded after creation, each with a UID
  precondition; Namespace runner-control last, only if nothing but default children remain in it.
  The 061 grants stay (rollout README). Already failed conversations and deleted Pods stay so.

rc: 0 done and verified; 2 placeholders left; 3 precheck/render/backup refused, nothing changed; 5 a change
failed or was ambiguous (the output lists what this run changed; run rollback with the printed directory);
8 changed but a postcheck failed. Fixed messages only: no DSN, token, Secret data or command output with them.
"""
import base64
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit, urlunsplit

RUNTIME_REF = '@@RUNTIME_REF@@'    # docker.io/library/anxious-dev-runtime@sha256:<W1 import digest>
RUNNER_REF = '@@RUNNER_REF@@'      # docker.io/library/anxious-dev-runner@sha256:<W1 import digest>
EXECUTOR_REF = '@@EXECUTOR_REF@@'  # docker.io/library/anxious-dev-shell-executor@sha256:<W1 import digest>
RUNTIME_TAG = 'anxious-dev-runtime:61-5e73bc4'
RUNTIME_ID = 'sha256:80af8c59f978f7a7d59d02f71985f3d456ce2e8011ed2d534395297188830771'
MODULE_SHA256 = 'a7d2a7d1c7ab17421cfd6b2e03ad9eab40d4a00df48b72f5011899dcb870ac6c'  # ops/runner_rollout.py @ 5e73bc4
HERE = os.path.dirname(os.path.abspath(__file__))
K3S = '/usr/local/bin/k3s'
VALUES = '/root/r4-rollout/values.json'
DSN = {'controller-url': '/etc/anxious-lookout/issue16/controller/database-url',
       'observer-url': '/etc/anxious-lookout/issue16/observer/database-url'}
DB_HOST = 'postgres.m2-hosting.svc.cluster.local'
EVIDENCE = '/root/issue61/evidence'
UNITS = ['issue16-controller.timer', 'issue16-controller.service', 'issue16-observer.timer', 'issue16-observer.service']
KEYS = {'version', 'runtimeImage', 'runnerImage', 'provisionerImageId', 'postImagesStorageClass', 'api', 'hostApi',
        'namespaceUid', 'podCIDR', 'node', 'providerAccountId', 'allowlist', 'maxPods', 'idleSeconds',
        'nativeSizeGiB', 'controlSizeGiB', 'executor'}
FIXTURES = [('00000000-0000-4000-8000-000000000048', '3bb0fe46-af68-4d3a-9c67-35bce5f44cf7', 'failed'),
            ('00000000-0000-4000-8000-000000005802', '4107e603-0c33-4fb3-9fc3-911212a716fb', 'runner-dead'),
            ('00000000-0000-4000-8000-000000005804', 'b9bbc857-cd36-4a7b-89b5-1ca7c7d3b61b', 'running')]
IMAGES = {'docker.io/library/anxious-dev-runtime:61-5e73bc4': RUNTIME_ID,
          'docker.io/library/anxious-dev-runner:61-5e73bc4':
              'sha256:4cac37e2173d5aec7b351e05c2977f2e0df76670f3befb91ce54306d2444e8ad',
          'docker.io/library/anxious-dev-shell-executor:61-5e73bc4':
              'sha256:49b449cb31c5b344f876a9d9aee2d97dd3dcae906c3324bc0eaf00678dd6b883'}
CNP = ('CiliumNetworkPolicy', 'codex-runner', 'runner-callback-draft')
API = ('Deployment', 'm2-hosting', 'api')
DB_SECRET = ('Secret', 'runner-control', 'runner-controller-db')
# REST paths for the UID-preconditioned deletes of rollback (only kinds W2 can create).
PATHS = {'Namespace': '/api/v1/namespaces/{name}',
         'ServiceAccount': '/api/v1/namespaces/{ns}/serviceaccounts/{name}',
         'Secret': '/api/v1/namespaces/{ns}/secrets/{name}',
         'Deployment': '/apis/apps/v1/namespaces/{ns}/deployments/{name}',
         'Role': '/apis/rbac.authorization.k8s.io/v1/namespaces/{ns}/roles/{name}',
         'RoleBinding': '/apis/rbac.authorization.k8s.io/v1/namespaces/{ns}/rolebindings/{name}',
         'ClusterRole': '/apis/rbac.authorization.k8s.io/v1/clusterroles/{name}',
         'ClusterRoleBinding': '/apis/rbac.authorization.k8s.io/v1/clusterrolebindings/{name}',
         'ResourceQuota': '/api/v1/namespaces/{ns}/resourcequotas/{name}'}
DEFAULT_CHILDREN = {'serviceaccount/default', 'configmap/kube-root-ca.crt'}


class Stop(Exception):
    def __init__(self, rc, message):
        super().__init__(message)
        self.rc, self.message = rc, message


def say(text):
    print(text, flush=True)


def run(args, stage, *, rc=3, timeout=60, stdin=None, ok=(0,)):
    """Run a fixed command; on failure raise Stop with a fixed stage text (output is never echoed)."""
    try:
        done = subprocess.run(args, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=timeout, check=False)
    except Exception:
        raise Stop(rc, stage + ' failed') from None
    if done.returncode not in ok:
        raise Stop(rc, stage + ' failed')
    return done.stdout.decode('utf-8', 'replace')


def kubectl(args, stage, **kw):
    return run([K3S, 'kubectl'] + args, stage, **kw)


def get(kind, ns, name, stage, rc=3):
    """The object as JSON, or None when it does not exist (any other failure stops)."""
    args = ['get', kind, name, '-o', 'json', '--ignore-not-found'] + (['-n', ns] if ns else [])
    out = kubectl(args, stage, rc=rc).strip()
    return json.loads(out) if out else None


def sql(text, stage, rc=3):
    return run([K3S, 'kubectl', '-n', 'm2-hosting', 'exec', '-i', 'postgres-0', '--', 'psql', '-U', 'postgres',
                '-d', 'hosting', '-Atq', '-v', 'ON_ERROR_STOP=1', '-f', '-'], stage, rc=rc, stdin=text.encode())


def key(obj):
    return (obj['kind'], obj['metadata'].get('namespace'), obj['metadata']['name'])


def label(k):
    return '%s %s/%s' % (k[0], k[1] or '-', k[2])


# Precheck ---------------------------------------------------------------------------------------------
def precheck():
    if os.geteuid() != 0:
        raise Stop(3, 'precheck: not root')
    for unit in UNITS:
        out = run(['systemctl', 'show', '-p', 'LoadState', '-p', 'ActiveState', unit], 'systemctl show ' + unit)
        state = dict(line.split('=', 1) for line in out.split() if '=' in line)
        if state.get('LoadState') != 'loaded' or state.get('ActiveState') not in {'inactive', 'failed'}:
            raise Stop(3, 'precheck: %s is %s %s' % (unit, state.get('LoadState'), state.get('ActiveState')))
    if get('Namespace', None, 'runner-control', 'namespace lookup') is not None:
        raise Stop(3, 'precheck: namespace runner-control exists')
    for owner, uid, want in FIXTURES:
        name = 'runner-' + owner.replace('-', '')
        pod = get('Pod', 'codex-runner', name, 'pod lookup')
        if pod is None:
            raise Stop(3, 'precheck: %s missing' % name)
        phase = pod['status'].get('phase')
        runner = [s for s in pod['status'].get('containerStatuses', []) if s.get('name') == 'runner']
        state = next(iter((runner[0].get('state') or {}) if runner else {}), None)
        expected = {'failed': ('Failed', None), 'runner-dead': ('Running', 'terminated'),
                    'running': ('Running', 'running')}[want]
        seen = (pod['metadata']['uid'], phase, state if want != 'failed' else None,
                bool(pod['metadata'].get('deletionTimestamp')))
        if seen != (uid, expected[0], expected[1], False):
            raise Stop(3, 'precheck: %s observed uid=%s phase=%s runner=%s deleting=%s' % ((name,) + seen))
    waiting = sql("SELECT count(*) FROM runner_runtime.registry WHERE phase IN ('provisioning','capacity_wait');",
                  'waiting lookup').strip()
    if waiting != '0':
        raise Stop(3, 'precheck: owners waiting for a Pod: %s' % waiting)
    for ref, image_id in IMAGES.items():
        got = run([K3S, 'crictl', 'inspecti', '-o', 'go-template', '--template', '{{.status.id}}', ref],
                  'image lookup').strip()
        if got != image_id:
            raise Stop(3, 'precheck: %s has id %s, want %s' % (ref, got or 'none', image_id))
    got = run(['docker', 'image', 'inspect', '--format', '{{.Id}}', RUNTIME_TAG], 'docker image lookup').strip()
    if got != RUNTIME_ID:
        raise Stop(3, 'precheck: docker %s has id %s' % (RUNTIME_TAG, got or 'none'))
    rows = [row.split() for row in run([K3S, 'ctr', '-n', 'k8s.io', 'images', 'ls'], 'ctr images ls').splitlines()]
    for ref, tag in ((RUNTIME_REF, 'runtime'), (RUNNER_REF, 'runner'), (EXECUTOR_REF, 'shell-executor')):
        # The digest reference resolves only to the manifest W1 imported under the 61-5e73bc4 tag.
        digest = ref.split('@', 1)[1]
        if not any(row[:1] == ['docker.io/library/anxious-dev-%s:61-5e73bc4' % tag] and digest in row for row in rows):
            raise Stop(3, 'precheck: %s is not the imported %s manifest' % (ref, tag))
    for name in ('40-runner-network.json', '061_runner_controller_grants.sql', 'worker.py', 'storage-root.sh'):
        path = os.path.join(HERE, name)
        if not os.path.isfile(path) or os.path.islink(path):
            raise Stop(3, 'precheck: %s missing next to this script' % name)


# Render -----------------------------------------------------------------------------------------------
def values(api):
    with open(VALUES) as stream:
        old = json.load(stream)
    env = {e['name']: e.get('value') for e in api['spec']['template']['spec']['containers'][0].get('env', [])}
    allow = env.get('CODEX_USER_RUNNER_ALLOWLIST')
    if not isinstance(allow, str) or not allow:
        raise Stop(3, 'render: live api allowlist missing')
    new = {k: old[k] for k in KEYS - {'executor'} if k in old}
    missing = sorted(KEYS - {'executor'} - set(new))
    if missing:
        raise Stop(3, 'render: values lack ' + ','.join(missing))
    new.update(runtimeImage=RUNTIME_REF, runnerImage=RUNNER_REF, allowlist=sorted(allow.split(',')))
    executor = old.get('executor') or {}
    new['executor'] = {'image': EXECUTOR_REF, 'wallSeconds': executor.get('wallSeconds', 30),
                       'outputBytes': executor.get('outputBytes', 65536)}
    ns = get('Namespace', None, 'codex-runner', 'codex-runner lookup')
    if ns is None or ns['metadata']['uid'] != new['namespaceUid']:
        raise Stop(3, 'render: namespaceUid differs from the live codex-runner namespace')
    say('render: values keys dropped=%s allowlist=%s' % (sorted(set(old) - KEYS), new['allowlist']))
    return new


def render(work, api):
    inputs = os.path.join(work, 'in')
    os.mkdir(inputs, 0o700)
    os.mkdir(os.path.join(inputs, 'templates'), 0o700)
    for name in ('worker.py', 'storage-root.sh'):
        shutil.copyfile(os.path.join(HERE, name), os.path.join(inputs, 'templates', name))
    for name, value in (('values.json', values(api)), ('api.json', api)):
        with open(os.path.join(inputs, name), 'x') as stream:
            json.dump(value, stream)
    parent = os.path.join(work, 'out-parent')
    os.mkdir(parent, 0o700)
    run(['docker', 'run', '--rm', '--pull=never', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
         '--security-opt', 'no-new-privileges', '--user', '0:0', '--tmpfs', '/tmp',
         '-v', inputs + ':/in:ro,Z', '-v', parent + ':/out-parent:Z', '--entrypoint', 'python', RUNTIME_TAG,
         '-B', '-m', 'ops.runner_rollout', '--values', '/in/values.json', '--deployment', '/in/api.json',
         '--templates', '/in/templates', '--output', '/out-parent/out', '--expected-module-sha256', MODULE_SHA256],
        'render', timeout=120)
    with open(os.path.join(parent, 'out', 'resources.json')) as stream:
        resources = json.load(stream)
    items = resources['items'] if resources.get('kind') == 'List' else resources
    kinds = {}
    for obj in items:
        kinds[obj['kind']] = kinds.get(obj['kind'], 0) + 1
    say('render: resources.json %d objects %s' % (len(items), json.dumps(kinds, sort_keys=True)))
    return items


# Backup -----------------------------------------------------------------------------------------------
def backup(ev, items):
    rendered = [o for o in items if key(o) == ('Secret', 'm2-hosting', 'hosting-runner-pool-v2')]
    live = get('Secret', 'm2-hosting', 'hosting-runner-pool-v2', 'pool secret lookup')
    try:
        same = live is not None and len(rendered) == 1 and json.loads(rendered[0]['stringData']['pool.json']) == \
            json.loads(base64.b64decode(live['data']['pool.json']).decode())
    except Exception:
        same = False
    if not same:
        # Rollback cannot restore Secret data (never kept in evidence), so W2 only re-applies an identical one.
        raise Stop(3, 'backup: rendered hosting-runner-pool-v2 differs from the live Secret (or is missing)')
    targets = [key(obj) for obj in items] + [API, CNP, DB_SECRET]
    if len(set(targets)) != len(targets):
        raise Stop(3, 'backup: duplicate object in the apply set')
    record = []
    for kind, ns, name in targets:
        live = get(kind, ns, name, 'backup lookup ' + label((kind, ns, name)))
        entry = {'kind': kind, 'namespace': ns, 'name': name, 'absent': live is None}
        if live is not None:
            if kind == 'Secret':
                entry['uid'] = live['metadata']['uid']  # Secret data is never written to evidence.
                entry['secretKeys'] = sorted((live.get('data') or {}).keys())
            else:
                entry['object'] = live
        record.append(entry)
    with open(os.path.join(ev, 'backup.json'), 'x') as stream:
        json.dump(record, stream, indent=1)
    for e in record:
        if e['kind'] in {'Role', 'ClusterRole'} and e['name'] in {'runner-pool-controller', 'runner-pool-observer'}:
            rules = [] if e['absent'] else e['object'].get('rules') or []
            say('backup: %s rules before=%s' % (label((e['kind'], e['namespace'], e['name'])),
                                                 json.dumps([[r.get('resources'), r.get('verbs')] for r in rules])))
    say('backup: %d objects recorded, %d absent before W2' % (len(record), sum(e['absent'] for e in record)))
    return record


# Apply ------------------------------------------------------------------------------------------------
class Changes:
    def __init__(self, ev):
        self.ev, self.done = ev, []

    def mark(self, text):
        self.done.append(text)
        with open(os.path.join(self.ev, 'changed.txt'), 'a') as stream:
            stream.write(text + '\n')


def apply_objects(objs, stage):
    payload = json.dumps({'apiVersion': 'v1', 'kind': 'List', 'items': objs}).encode()
    kubectl(['apply', '-f', '-'], stage, rc=5, stdin=payload, timeout=120)


def db_secret(changes):
    tmp = tempfile.mkdtemp(prefix='issue61-w2-', dir='/run')
    try:
        files = []
        for name, path in DSN.items():
            with open(path) as stream:
                url = urlsplit(stream.read().strip())
            if url.scheme not in {'postgresql', 'postgresql+psycopg', 'postgres'} or not url.username or not url.path:
                raise Stop(5, 'secret: %s is not a PostgreSQL URL' % name)
            netloc = url.netloc.rsplit('@', 1)[0] + '@' + DB_HOST + ':5432'
            target = os.path.join(tmp, name)
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as stream:
                stream.write(urlunsplit((url.scheme, netloc, url.path, url.query, '')))
            files += ['--from-file=%s=%s' % (name, target)]
        kubectl(['-n', 'runner-control', 'create', 'secret', 'generic', 'runner-controller-db'] + files,
                'secret create', rc=5)
        changes.mark('created Secret runner-control/runner-controller-db')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def can_i(verb, resource, ns, sa):
    out = run([K3S, 'kubectl', 'auth', 'can-i', verb, resource, '-n', ns, '--as=system:serviceaccount:' + sa],
              'can-i', rc=8, ok=(0, 1)).strip()
    return out


def created_uids(ev, record, rc=8):
    """Record the live UID of every object that was absent before W2 (None: not created yet).

    Called after each change step and on failure, so a rollback after a partial apply still knows exactly
    which objects this run created; rollback deletes nothing else."""
    uids = {}
    for e in record:
        if e['absent']:
            live = get(e['kind'], e['namespace'], e['name'], 'created lookup', rc=rc)
            uids[label((e['kind'], e['namespace'], e['name']))] = None if live is None else live['metadata']['uid']
    path = os.path.join(ev, 'created.json')
    with open(path + '.tmp', 'w') as stream:
        json.dump(uids, stream, indent=1)
    os.replace(path + '.tmp', path)
    return uids


def apply(ev):
    work = os.path.join(ev, 'private')
    os.mkdir(work, 0o700)
    api = get(*API, 'api lookup')
    if api is None:
        raise Stop(3, 'precheck: api Deployment missing')
    old_image = api['spec']['template']['spec']['containers'][0]['image']
    say('api before: uid=%s generation=%s image=%s' % (api['metadata']['uid'], api['metadata']['generation'], old_image))
    items = render(work, api)
    by_kind = {}
    for obj in items:
        by_kind.setdefault(obj['kind'], []).append(obj)
    if [o['metadata']['name'] for o in by_kind.get('Namespace', [])] != ['runner-control'] or \
            [key(o) for o in by_kind.get('Deployment', [])] != [('Deployment', 'runner-control', 'runner-controller')]:
        raise Stop(3, 'render: unexpected Namespace/Deployment set')
    with open(os.path.join(HERE, '40-runner-network.json')) as stream:
        cnp = json.load(stream)
    cnp_items = cnp['items'] if cnp.get('kind') == 'List' else [cnp]
    if [key(o) for o in cnp_items] != [CNP]:
        raise Stop(3, 'render: 40-runner-network.json is not exactly CNP runner-callback-draft')
    record = backup(ev, items)
    say('evidence: ' + ev)

    changes = Changes(ev)
    try:
        with open(os.path.join(HERE, '061_runner_controller_grants.sql')) as stream:
            sql(stream.read(), 'grants 061', rc=5)
        changes.mark('granted 061 (stays on rollback)')
        apply_objects(by_kind['Namespace'], 'namespace')
        changes.mark('applied Namespace runner-control')
        created_uids(ev, record, rc=5)
        db_secret(changes)
        created_uids(ev, record, rc=5)
        rest = [o for o in items if o['kind'] not in {'Namespace', 'Deployment'}]
        apply_objects(rest, 'rbac and pool objects')
        changes.mark('applied %d RBAC/SA/pool objects' % len(rest))
        created_uids(ev, record, rc=5)
        apply_objects(cnp_items, 'cnp')
        changes.mark('applied CNP codex-runner/runner-callback-draft')
        kubectl(['-n', 'm2-hosting', 'set', 'image', 'deployment/api', 'api=' + RUNTIME_REF], 'api image', rc=5)
        changes.mark('set api image %s -> %s' % (old_image, RUNTIME_REF))
        kubectl(['-n', 'm2-hosting', 'rollout', 'status', 'deployment/api', '--timeout=180s'], 'api rollout',
                rc=5, timeout=200)
        apply_objects(by_kind['Deployment'], 'controller deployment')
        changes.mark('applied Deployment runner-control/runner-controller')
        created_uids(ev, record, rc=5)
        kubectl(['-n', 'runner-control', 'rollout', 'status', 'deployment/runner-controller', '--timeout=180s'],
                'controller rollout', rc=5, timeout=200)
    except Exception as failure:
        try:
            created_uids(ev, record, rc=5)
        except Exception:
            say('partial: could not re-read the created objects; rollback deletes only UIDs recorded before')
        say('partial: already changed by this run: ' + ('; '.join(changes.done) or 'nothing'))
        say('rollback: python3 -I %s rollback %s' % (os.path.abspath(__file__), ev))
        if isinstance(failure, Stop):
            raise
        raise Stop(5, 'unexpected error during the changes (no detail printed)') from None

    uids = created_uids(ev, record)
    checks = {
        'controller creates pods': (can_i('create', 'pods', 'codex-runner', 'runner-control:runner-controller'), 'yes'),
        'host controller creates pods': (can_i('create', 'pods', 'codex-runner', 'codex-runner:runner-pool-controller'), 'no'),
        'host observer deletes pods': (can_i('delete', 'pods', 'codex-runner', 'codex-runner:runner-pool-observer'), 'no'),
        'controller execs pods': (can_i('create', 'pods/exec', 'codex-runner', 'runner-control:runner-controller'), 'no'),
    }
    bad = [name for name, (got, want) in checks.items() if got != want]
    for name, (got, want) in checks.items():
        say('can-i %s: %s (want %s)' % (name, got, want))
    now = get(*API, 'api lookup', rc=8)
    image = now['spec']['template']['spec']['containers'][0]['image']
    say('api after: generation=%s image=%s' % (now['metadata']['generation'], image))
    if image != RUNTIME_REF:
        bad.append('api image')
    if None in uids.values():
        bad.append('created object missing')
    logs = run([K3S, 'kubectl', '-n', 'runner-control', 'logs', 'deployment/runner-controller', '--tail=20'],
               'controller logs', rc=8)
    shown = [line for line in logs.splitlines() if re.fullmatch(r'\{"owner": "[0-9a-f-]{36}", "stage": "controller", '
                                                                 r'"result": "[a-z_]{1,40}"\}', line)]
    say('controller log lines (JSON outcome lines only, %d of %d):' % (len(shown), len(logs.splitlines())))
    for line in shown:
        say('  ' + line)
    if bad:
        raise Stop(8, 'postcheck failed: ' + ', '.join(bad))


# Rollback ---------------------------------------------------------------------------------------------
def strip(obj):
    obj = json.loads(json.dumps(obj))
    meta = obj['metadata']
    for field in ('uid', 'resourceVersion', 'creationTimestamp', 'managedFields', 'generation', 'selfLink'):
        meta.pop(field, None)
    obj.pop('status', None)
    return obj


def delete_exact(kind, ns, name, uid, stage):
    path = PATHS[kind].format(ns=ns, name=name)
    body = json.dumps({'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {'uid': uid}}).encode()
    with tempfile.NamedTemporaryFile('wb', dir='/run', prefix='issue61-w2-del-', delete=False) as stream:
        stream.write(body)
        options = stream.name
    try:
        kubectl(['delete', '--raw', path, '-f', options], stage, rc=5)
    finally:
        os.unlink(options)


def rollback(ev):
    if os.geteuid() != 0:
        raise Stop(3, 'rollback: not root')
    try:
        with open(os.path.join(ev, 'backup.json')) as stream:
            record = json.load(stream)
        created = {}
        if os.path.exists(os.path.join(ev, 'created.json')):
            with open(os.path.join(ev, 'created.json')) as stream:
                created = json.load(stream)
    except Exception:
        raise Stop(3, 'rollback: evidence directory unreadable') from None
    done = []
    try:
        if get('Deployment', 'runner-control', 'runner-controller', 'controller lookup', rc=5) is not None:
            kubectl(['-n', 'runner-control', 'scale', 'deployment/runner-controller', '--replicas=0'], 'scale 0', rc=5)
            kubectl(['-n', 'runner-control', 'wait', '--for=delete', 'pod', '-l', 'app=runner-controller',
                     '--timeout=120s'], 'controller pod gone', rc=5, timeout=140, ok=(0, 1))
            pods = kubectl(['-n', 'runner-control', 'get', 'pods', '-l', 'app=runner-controller', '-o', 'name'],
                           'controller pods', rc=5).split()
            if pods:
                raise Stop(5, 'rollback: runner-controller Pod still present')
            done.append('runner-controller scaled to 0')
        stops = sql("SELECT owner_id||'|'||generation||'|'||pod_uid||'|v'||(receipt->>'version') FROM "
                    "runner_termination.stops WHERE completed_at IS NULL AND receipt->>'version' IN ('2','3','4') "
                    "ORDER BY owner_id;", 'open stops', rc=5).split()
        say('rollback: open v2/v3/v4 stops left for the next roll-forward (not finished here): %s'
            % (', '.join(stops) or 'none'))
        for e in record:
            if e['absent'] or e['kind'] == 'Secret':
                continue  # Existing Secrets are never rewritten by W2 except through resources.json apply.
            payload = json.dumps(strip(e['object'])).encode()
            kubectl(['replace', '-f', '-'], 'restore ' + label((e['kind'], e['namespace'], e['name'])), rc=5,
                    stdin=payload, timeout=120)
            done.append('restored ' + label((e['kind'], e['namespace'], e['name'])))
        if any(k == API for k in [(e['kind'], e['namespace'], e['name']) for e in record if not e['absent']]):
            kubectl(['-n', 'm2-hosting', 'rollout', 'status', 'deployment/api', '--timeout=180s'], 'api rollout',
                    rc=5, timeout=200)
        say('rollback: Secret m2-hosting/hosting-runner-pool-v2 left as is: W2 re-applied it only because the '
            'render was identical to the live data')
        later = []
        for e in record:
            if not e['absent']:
                continue
            k = (e['kind'], e['namespace'], e['name'])
            if k[0] == 'Namespace':
                later.append(e)
                continue
            live = get(k[0], k[1], k[2], 'lookup ' + label(k), rc=5)
            if live is None:
                continue
            if created.get(label(k)) != live['metadata']['uid'] or k[0] not in PATHS:
                say('rollback: %s not deleted (live UID %s, recorded %s)' % (label(k), live['metadata']['uid'],
                                                                             created.get(label(k))))
                continue
            delete_exact(k[0], k[1], k[2], live['metadata']['uid'], 'delete ' + label(k))
            done.append('deleted ' + label(k))
        for e in later:
            live = get('Namespace', None, e['name'], 'namespace lookup', rc=5)
            if live is None:
                continue
            kinds = kubectl(['api-resources', '--verbs=list', '--namespaced', '-o', 'name'], 'api-resources',
                            rc=5).split()
            kinds = [kind for kind in kinds if kind not in {'events', 'events.events.k8s.io'}]
            for _ in range(10):  # The deleted Deployment's ReplicaSet and Pods go by garbage collection.
                left = set(kubectl(['get', ','.join(kinds), '-n', e['name'], '-o', 'name', '--ignore-not-found'],
                                   'namespace contents', rc=5, timeout=120).split())
                extra = sorted(item for item in left if item not in DEFAULT_CHILDREN)
                if not extra:
                    break
                time.sleep(9)
            if extra or created.get(label(('Namespace', None, e['name']))) != live['metadata']['uid']:
                raise Stop(5, 'rollback: namespace %s not deleted; unexpected contents %s or UID differs'
                           % (e['name'], extra))
            delete_exact('Namespace', None, e['name'], live['metadata']['uid'], 'delete namespace')
            done.append('deleted Namespace ' + e['name'])
    except Stop:
        say('partial: rollback changed: ' + ('; '.join(done) or 'nothing'))
        raise
    for line in done:
        say('rollback: ' + line)
    say('rollback: not reverted: 061 grants (rollout README); conversations already failed and Pods already '
        'deleted by runner-controller stay so')
    api = get(*API, 'api lookup', rc=8)
    before = [e for e in record if (e['kind'], e['namespace'], e['name']) == API][0]['object']
    if api['spec']['template']['spec']['containers'][0]['image'] != \
            before['spec']['template']['spec']['containers'][0]['image']:
        raise Stop(8, 'postcheck failed: api image not restored')


def main(argv):
    try:
        if '@@' in RUNTIME_REF + RUNNER_REF + EXECUTOR_REF:
            say('placeholders not filled')
            return 2
        for ref in (RUNTIME_REF, RUNNER_REF, EXECUTOR_REF):
            if not re.fullmatch(r'docker\.io/library/anxious-dev-[a-z-]+@sha256:[0-9a-f]{64}', ref):
                raise Stop(3, 'precheck: bad image reference')
        if argv[1:] == ['apply']:
            precheck()
            stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            os.makedirs(EVIDENCE, mode=0o700, exist_ok=True)
            ev = os.path.join(EVIDENCE, 'w2-' + stamp)
            os.mkdir(ev, 0o700)
            apply(ev)
        elif len(argv) == 3 and argv[1] == 'rollback' and os.path.dirname(os.path.abspath(argv[2])) == EVIDENCE:
            rollback(os.path.abspath(argv[2]))
        else:
            say('usage: w2-apply.py apply | rollback %s/w2-<stamp>' % EVIDENCE)
            return 3
    except Stop as stop:
        say(stop.message)
        say('result rc=%d' % stop.rc)
        return stop.rc
    except Exception:
        say('unexpected error (no detail printed)')
        say('result rc=5')
        return 5
    say('result rc=0')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
