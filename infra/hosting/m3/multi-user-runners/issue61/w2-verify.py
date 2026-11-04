"""#61 dev-VM gate W2 certification, read-only: re-checks a finished W2 apply against its evidence directory.

    python3 -I /root/issue61/w2-verify.py /root/issue61/evidence/w2-<stamp>

Why: the W2 run w2-20261102T151325Z ended rc 8 only because its postcheck asked `kubectl auth can-i create
pods/exec`, which kubectl reads as "create the Pod named exec" (TYPE/NAME), so the controller's legitimate
right to create Pods answered yes. This script repeats W2's postcheck with the subresources asked through
--subresource (the same CHECKS as w2-apply.py; the harness compares the two lists) and adds the evidence and
UID checks W3 relies on. It only reads: kubectl get / auth can-i / logs and the evidence files. No change.

Checks:
  evidence  no rollback marker; changes.json has no pending, uncertain or creation-uncertain attempt; the last
            step created Deployment runner-control/runner-controller; the eight created objects are recorded
  uids      each created object is live with its recorded UID; each object that existed before W2 is live with
            its backup UID (Secrets are read by UID only, never their data)
  api       container api on the 61-5e73bc4 runtime reference; desired >= 1; observed = generation;
            ready = updated = desired
  controller  the Deployment W2 created (UID), exactly one container `controller` on the runtime reference, no
            init containers, serviceAccountName runner-controller (the identity the can-i checks impersonate),
            Ready 1/1, observed = generation
  can-i     the CHECKS list (controller may create Pods; host roles may not; no exec/attach/portforward/
            ephemeralcontainers for the controller)
  log       the controller's JSON outcome lines (shown, not a gate)

rc: 0 certified; 3 could not verify (usage, not root, evidence unreadable, or any lookup failed); 4 verified and
something does not match (listed). Fixed messages only: no DSN, token or Secret data.
"""
import json
import os
import re
import subprocess
import sys

K = ['/usr/local/bin/k3s', 'kubectl']
EVIDENCE = '/root/issue61/evidence'
RUNTIME_REF = 'docker.io/library/anxious-dev-runtime@sha256:eb3a18acd1b2856f6a95c7686c0c0ee4e5194b6237266b3443664f37feb3377c'
CONTROLLER = ('Deployment', 'runner-control', 'runner-controller')
CREATED = {'Namespace -/runner-control', 'Secret runner-control/runner-controller-db',
           'ServiceAccount runner-control/runner-controller', 'Role codex-runner/runner-controller',
           'RoleBinding codex-runner/runner-controller', 'ClusterRole -/runner-controller',
           'ClusterRoleBinding -/runner-controller', 'Deployment runner-control/runner-controller'}
CONTROLLER_SA = 'runner-control:runner-controller'
# Same list as w2-apply.py CHECKS (name, verb, resource, subresource, service account, wanted answer).
CHECKS = (
    ('controller creates pods', 'create', 'pods', None, CONTROLLER_SA, 'yes'),
    ('host controller creates pods', 'create', 'pods', None, 'codex-runner:runner-pool-controller', 'no'),
    ('host observer deletes pods', 'delete', 'pods', None, 'codex-runner:runner-pool-observer', 'no'),
    ('controller execs pods (create)', 'create', 'pods', 'exec', CONTROLLER_SA, 'no'),
    ('controller execs pods (get)', 'get', 'pods', 'exec', CONTROLLER_SA, 'no'),
    ('controller attaches pods (create)', 'create', 'pods', 'attach', CONTROLLER_SA, 'no'),
    ('controller attaches pods (get)', 'get', 'pods', 'attach', CONTROLLER_SA, 'no'),
    ('controller port-forwards pods (create)', 'create', 'pods', 'portforward', CONTROLLER_SA, 'no'),
    ('controller port-forwards pods (get)', 'get', 'pods', 'portforward', CONTROLLER_SA, 'no'),
    ('controller edits ephemeral containers', 'update', 'pods', 'ephemeralcontainers', CONTROLLER_SA, 'no'),
)
OUTCOME = re.compile(r'\{"owner": "[0-9a-f-]{36}", "stage": "controller", "result": "[a-z_]{1,40}"\}')


class Unverifiable(Exception):
    pass


def say(text):
    print(text, flush=True)


def kubectl(args, ok=(0,)):
    try:
        done = subprocess.run(K + args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60)
    except Exception:
        raise Unverifiable('lookup failed: kubectl %s' % args[0]) from None
    if done.returncode not in ok:
        raise Unverifiable('lookup failed: kubectl %s %s' % (args[0], ' '.join(a for a in args[1:3] if not a.startswith('-'))))
    return done.stdout.decode()


def parse(label):
    kind, rest = label.split(' ', 1)
    ns, name = rest.split('/', 1)
    return kind, (None if ns == '-' else ns), name


def live_uid(kind, ns, name):
    """The live UID, or None when the API answered that the object does not exist."""
    out = kubectl(['get', kind, name, '-o', 'jsonpath={.metadata.uid}', '--ignore-not-found'] + (['-n', ns] if ns else []))
    return out.strip() or None


def get(kind, ns, name):
    out = kubectl(['get', kind, name, '-o', 'json', '--ignore-not-found'] + (['-n', ns] if ns else [])).strip()
    return json.loads(out) if out else None


def can_i(verb, resource, subresource, ns, sa):
    if '/' in resource:
        raise Unverifiable('can-i resource %s names an object, not a subresource' % resource)
    args = ['auth', 'can-i', verb, resource] + (['--subresource=' + subresource] if subresource else [])
    return kubectl(args + ['-n', ns, '--as=system:serviceaccount:' + sa], ok=(0, 1)).strip()


def rollout(label, obj, bad):
    if obj is None:
        bad.append(label + ' missing')
        return
    status, spec, meta = obj.get('status') or {}, obj.get('spec') or {}, obj['metadata']
    want = spec.get('replicas', 1)
    if not isinstance(want, int) or want < 1:
        bad.append(label + ' desired replicas %r' % want)
    say('%s uid=%s generation=%s observed=%s desired=%s ready=%s updated=%s' % (
        label, meta.get('uid'), meta.get('generation'), status.get('observedGeneration'), want,
        status.get('readyReplicas'), status.get('updatedReplicas')))
    if status.get('observedGeneration') != meta.get('generation') or status.get('readyReplicas') != want \
            or status.get('updatedReplicas') != want:
        bad.append(label + ' not rolled out')


def verify(ev):
    bad = []
    for marker in ('rollback-started.json', 'rolled-back.json'):
        if os.path.exists(os.path.join(ev, marker)):
            bad.append('evidence has ' + marker)
    try:
        with open(os.path.join(ev, 'changes.json')) as stream:
            changes = json.load(stream)
        with open(os.path.join(ev, 'backup.json')) as stream:
            backup = json.load(stream)
        created, done = changes['created'], changes['done']
        clean = not changes.get('pending') and not changes['uncertain'] and not changes['creationUncertain']
    except Exception:
        raise Unverifiable('evidence unreadable') from None
    say('evidence %s: done %d, pending %s, uncertain %s, creationUncertain %s' % (
        ev, len(done), changes.get('pending'), changes['uncertain'], changes['creationUncertain']))
    if not clean:
        bad.append('changes.json has a pending or uncertain attempt')
    if not done or done[-1] != 'created Deployment runner-control/runner-controller':
        bad.append('W2 did not finish with the controller Deployment')
    if set(created) != CREATED:
        bad.append('created set differs: %s' % sorted(set(created) ^ CREATED))

    for label in sorted(created):
        uid = live_uid(*parse(label))
        same = uid == created[label]
        say('created %s live=%s recorded=%s %s' % (label, uid, created[label], 'same' if same else 'DIFFERS'))
        if not same:
            bad.append('created UID ' + label)
    same = 0
    for entry in backup:
        if entry['absent']:
            continue
        want = entry.get('uid') if entry['kind'] == 'Secret' else entry['object']['metadata']['uid']
        label = '%s %s/%s' % (entry['kind'], entry['namespace'] or '-', entry['name'])
        uid = live_uid(entry['kind'], entry['namespace'], entry['name'])
        if uid == want and want:
            same += 1
        else:
            say('pre-existing %s live=%s backup=%s DIFFERS' % (label, uid, want))
            bad.append('backup UID ' + label)
    say('pre-existing objects with their backup UID: %d of %d' % (same, sum(not e['absent'] for e in backup)))

    api = get('Deployment', 'm2-hosting', 'api')
    rollout('api', api, bad)
    containers = (api or {}).get('spec', {}).get('template', {}).get('spec', {}).get('containers', [])
    image = next((c.get('image') for c in containers if c.get('name') == 'api'), None)
    say('api container api image %s' % image)
    if image != RUNTIME_REF:
        bad.append('api image')
    controller = get(*CONTROLLER)
    rollout('controller', controller, bad)
    if controller is not None:
        pod = controller['spec']['template']['spec']
        names = [c.get('name') for c in pod.get('containers', [])]
        image = pod['containers'][0].get('image') if names == ['controller'] else None
        account = pod.get('serviceAccountName')
        say('controller containers %s image %s serviceAccountName %s initContainers %d' % (
            names, image, account, len(pod.get('initContainers') or [])))
        # The can-i checks impersonate runner-control:runner-controller; they say something about the running
        # workload only if that is its identity and it runs the verified image.
        if names != ['controller'] or pod.get('initContainers') or image != RUNTIME_REF:
            bad.append('controller does not run exactly the controller container on the runtime reference')
        if account != 'runner-controller':
            bad.append('controller service account %s' % account)
        if controller['spec'].get('replicas', 1) != 1 \
                or controller['metadata']['uid'] != created.get('Deployment runner-control/runner-controller'):
            bad.append('controller is not the single-replica Deployment W2 created')

    for name, verb, resource, sub, sa, want in CHECKS:
        got = can_i(verb, resource, sub, 'codex-runner', sa)
        say('can-i %s: %s (want %s)' % (name, got, want))
        if got != want:
            bad.append('can-i ' + name)

    logs = kubectl(['-n', 'runner-control', 'logs', 'deployment/runner-controller', '--tail=200'])
    shown = [line for line in logs.splitlines() if OUTCOME.fullmatch(line)]
    say('controller log lines (JSON outcome lines only, %d of %d):' % (len(shown), len(logs.splitlines())))
    for line in shown:
        say('  ' + line)
    return bad


def main(argv):
    if len(argv) != 2 or os.path.dirname(os.path.abspath(argv[1])) != EVIDENCE \
            or not os.path.basename(argv[1]).startswith('w2-'):
        say('usage: w2-verify.py %s/w2-<stamp>' % EVIDENCE)
        say('result rc=3')
        return 3
    if os.geteuid() != 0:
        say('not root')
        say('result rc=3')
        return 3
    try:
        bad = verify(os.path.abspath(argv[1]))
    except Unverifiable as failure:
        say('not verified: %s' % failure)
        say('result rc=3')
        return 3
    except Exception:
        say('not verified: unexpected error (no detail printed)')
        say('result rc=3')
        return 3
    if bad:
        say('mismatch: ' + ', '.join(bad))
        say('result rc=4')
        return 4
    say('W2 certified')
    say('result rc=0')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
