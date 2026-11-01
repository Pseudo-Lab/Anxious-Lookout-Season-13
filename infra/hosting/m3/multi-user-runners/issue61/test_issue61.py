"""Mock harness for the #61 dev-VM gate scripts W1 and W2. Docker only, never on a real node:
    docker run --rm -v "$PWD/infra/hosting/m3:/m3:ro" -v "$PWD/backend/db:/db:ro" python:3.9-alpine \
      python3 -I /m3/multi-user-runners/issue61/test_issue61.py
Stand-ins (mock/fake.py) replace k3s kubectl/ctr/crictl, docker and systemctl. Python 3.9 as on the guest.
"""
import base64
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys

SRC = '/m3/multi-user-runners/issue61'
INSTALL = '/root/issue61'
REFS = {'runtime': 'docker.io/library/anxious-dev-runtime@sha256:' + '1'*64,
        'runner': 'docker.io/library/anxious-dev-runner@sha256:' + '2'*64,
        'shell-executor': 'docker.io/library/anxious-dev-shell-executor@sha256:' + '3'*64}
IDS = {'runtime': 'sha256:80af8c59f978f7a7d59d02f71985f3d456ce2e8011ed2d534395297188830771',
       'runner': 'sha256:4cac37e2173d5aec7b351e05c2977f2e0df76670f3befb91ce54306d2444e8ad',
       'shell-executor': 'sha256:49b449cb31c5b344f876a9d9aee2d97dd3dcae906c3324bc0eaf00678dd6b883'}
FIXTURES = [('00000000-0000-4000-8000-000000000048', '3bb0fe46-af68-4d3a-9c67-35bce5f44cf7', 'Failed', 'terminated'),
            ('00000000-0000-4000-8000-000000005802', '4107e603-0c33-4fb3-9fc3-911212a716fb', 'Running', 'terminated'),
            ('00000000-0000-4000-8000-000000005804', 'b9bbc857-cd36-4a7b-89b5-1ca7c7d3b61b', 'Running', 'running')]
NEW = {('Namespace', None, 'runner-control'), ('ServiceAccount', 'runner-control', 'runner-controller'),
       ('Role', 'codex-runner', 'runner-controller'), ('RoleBinding', 'codex-runner', 'runner-controller'),
       ('ClusterRole', None, 'runner-controller'), ('ClusterRoleBinding', None, 'runner-controller'),
       ('Deployment', 'runner-control', 'runner-controller')}
passed = failed = 0


def check(name, condition, detail=''):
    global passed, failed
    if condition:
        passed += 1
        print('ok   ' + name)
    else:
        failed += 1
        print('FAIL ' + name + (' (' + detail + ')' if detail else ''))


def okey(kind, ns, name):
    kind = kind.lower()
    return '%s|%s|%s' % (kind, '' if kind in {'namespace', 'node', 'clusterrole', 'clusterrolebinding'} else ns, name)


def setup(*, units=None, waiting=0, mutate=None, fail=()):
    for path in ('/mock', INSTALL, '/root/r4-rollout', '/etc/anxious-lookout/issue16', '/home/opc/issue61-images'):
        shutil.rmtree(path, ignore_errors=True)
    for path in ('/mock', INSTALL, '/root/r4-rollout', '/home/opc/issue61-images'):
        os.makedirs(path, mode=0o700)
    shutil.copy(SRC + '/mock/resources.json', '/mock/resources.json')
    for name in ('lib.sh', 'w0-lib.sh', 'w1-import.sh', 'w1b-tag.sh', 'w1c-finalize-ns.py', 'w3-reclaim.py'):
        shutil.copy(os.path.join(SRC, name), INSTALL)
    with open(os.path.join(SRC, 'w2-apply.py')) as stream:
        text = stream.read()
    for tag, ref in REFS.items():
        text = text.replace('@@%s_REF@@' % {'runtime': 'RUNTIME', 'runner': 'RUNNER', 'shell-executor': 'EXECUTOR'}[tag], ref)
    with open(INSTALL + '/w2-apply.py', 'w') as stream:
        stream.write(text)
    shutil.copy('/m3/user-runner-e2e/40-runner-network.json', INSTALL)
    shutil.copy('/db/061_runner_controller_grants.sql', INSTALL)
    for name in ('worker.py', 'storage-root.sh'):
        shutil.copy('/m3/multi-user-runners/rollout/' + name, INSTALL)
    archives, sums = {}, []
    for tag in REFS:
        name = 'anxious-dev-%s-61-5e73bc4.tar' % tag
        with open('/home/opc/issue61-images/' + name, 'w') as stream:
            stream.write('archive ' + tag)
        sums.append('%s  %s' % (hashlib.sha256(('archive ' + tag).encode()).hexdigest(), name))
        archives[name] = {'ref': 'docker.io/library/anxious-dev-%s:61-5e73bc4' % tag, 'id': IDS[tag],
                          'digest': REFS[tag].split('@')[1]}
    with open(INSTALL + '/archives.sha256', 'w') as stream:
        stream.write('\n'.join(sums) + '\n')
    os.makedirs('/etc/anxious-lookout/issue16/controller')
    os.makedirs('/etc/anxious-lookout/issue16/observer')
    for role in ('controller', 'observer'):
        with open('/etc/anxious-lookout/issue16/%s/database-url' % role, 'w') as stream:
            stream.write('postgresql+psycopg://%s:SECRETPW@10.42.0.83:5432/hosting\n' % role)
    rendered = json.load(open('/mock/resources.json'))['items']
    values = {'version': 1, 'runtimeImage': 'old', 'runnerImage': 'old', 'provisionerImageId': 'sha256:' + '9'*64,
              'postImagesStorageClass': 'm2-postgres-retain', 'api': 'https://10.43.0.1:443',
              'hostApi': 'https://127.0.0.1:6443', 'namespaceUid': '52fe679d-33b1-4e84-b377-443ff61fb38c',
              'podCIDR': '10.42.0.0/16', 'node': 'anxious-lookout', 'providerAccountId': 'user-abc',
              'allowlist': ['00000000-0000-4000-8000-000000000048'], 'maxPods': 3, 'idleSeconds': 1800,
              'nativeSizeGiB': 5, 'controlSizeGiB': 1, 'bootId': 'x', 'criSocket': '/run/x', 'crictl': '/usr/x',
              'hostPidNamespace': 'pid:[1]', 'hostCgroupNamespace': 'cgroup:[1]', 'nodeUid': 'x', 'kubectl': 'x',
              'sourceUid': 'x', 'executor': {'image': 'old', 'wallSeconds': 30, 'outputBytes': 65536}}
    with open('/root/r4-rollout/values.json', 'w') as stream:
        json.dump(values, stream)
    objects = {}
    for obj in rendered:
        k = (obj['kind'], obj['metadata'].get('namespace'), obj['metadata']['name'])
        if k in NEW:
            continue
        obj = copy.deepcopy(obj)
        obj['metadata']['uid'] = 'uid-' + obj['metadata']['name']
        if obj['kind'] == 'Secret':
            obj['data'] = {key: base64.b64encode(value.encode()).decode() for key, value in obj.pop('stringData').items()}
        if obj['metadata']['name'] in {'runner-pool-controller', 'runner-pool-observer'} and 'rules' in obj:
            obj['rules'] = obj['rules'] + [{'apiGroups': [''], 'resources': ['pods'], 'verbs': ['create', 'delete']}]
        objects[okey(*k)] = obj
    objects[okey('Namespace', None, 'codex-runner')] = {'kind': 'Namespace', 'metadata': {
        'name': 'codex-runner', 'uid': '52fe679d-33b1-4e84-b377-443ff61fb38c'}}
    objects[okey('Deployment', 'm2-hosting', 'api')] = {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': {
        'name': 'api', 'namespace': 'm2-hosting', 'uid': 'uid-api', 'generation': 10, 'resourceVersion': '1'},
        'spec': {'template': {'spec': {'containers': [{'name': 'api', 'image': 'docker.io/library/old-runtime@sha256:' + 'f'*64,
            'env': [{'name': 'CODEX_USER_RUNNER_ALLOWLIST', 'value': '00000000-0000-4000-8000-000000005804,'
                     '00000000-0000-4000-8000-000000000048'}]}]}}}, 'status': {'replicas': 1}}
    objects[okey('CiliumNetworkPolicy', 'codex-runner', 'runner-callback-draft')] = {
        'apiVersion': 'cilium.io/v2', 'kind': 'CiliumNetworkPolicy', 'metadata': {
            'name': 'runner-callback-draft', 'namespace': 'codex-runner', 'uid': 'uid-cnp'}, 'spec': {'old': True}}
    for owner, uid, phase, runner in FIXTURES:
        name = 'runner-' + owner.replace('-', '')
        objects[okey('Pod', 'codex-runner', name)] = {'kind': 'Pod', 'metadata': {'name': name, 'namespace': 'codex-runner',
            'uid': uid}, 'status': {'phase': phase, 'containerStatuses': [{'name': 'runner', 'state': {runner: {}}}]}}
    state = {'objects': objects, 'images': {}, 'archives': archives, 'docker': {}, 'waiting': waiting,
             'units': units or {}, 'openStops': [],
             'applyStages': {'0': 'apply-rbac', '1': 'apply-cnp'},
             'cani': {'create pods runner-control:runner-controller': 'yes'}}
    if mutate:
        mutate(state)
    with open('/mock/state.json', 'w') as stream:
        json.dump(state, stream)
    with open('/mock/fail', 'w') as stream:
        stream.write('\n'.join(fail))


def state():
    return json.load(open('/mock/state.json'))


def run(*args):
    done = subprocess.run(list(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
    return done.returncode, done.stdout.decode()


def imported(s, tagged=True):
    """W1 done (tag references); with tagged, W1b done too (digest references resolve)."""
    for name, archive in s['archives'].items():
        s['images'][archive['ref']] = archive
        if tagged:
            digest_ref = archive['ref'].split(':61-')[0] + '@' + archive['digest']
            s['images'][digest_ref] = dict(archive, ref=digest_ref)
    s['docker']['anxious-dev-runtime:61-5e73bc4'] = REFS['runtime'].split('@')[1]  # containerd image store


def install_fakes():
    for tool, path in (('k3s', '/usr/local/bin/k3s'), ('kubectl', '/usr/local/bin/kubectl'), ('docker', '/usr/local/bin/docker'),
                       ('systemctl', '/usr/local/bin/systemctl')):
        with open(path, 'w') as stream:
            stream.write('#!/bin/sh\nexec python3 -I %s/mock/fake.py %s "$@"\n' % (SRC, tool))
        os.chmod(path, 0o755)


def w2(*args):
    return run('python3', '-I', INSTALL + '/w2-apply.py', *args)


def evidence(out):
    line = [l for l in out.splitlines() if l.startswith('evidence: ')]
    return line[0].split(': ', 1)[1] if line else None


def main():
    if subprocess.run(['apk', 'add', '-q', 'coreutils'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        print('apk failed'); return 1
    install_fakes()

    # W1 --------------------------------------------------------------------------------------------
    setup()
    rc, out = run('sh', INSTALL + '/w1-import.sh')
    check('w1: imports all three and verifies the chain', rc == 0 and out.count('imported ') == 3, out)
    check('w1: chain lines name archive, ref and host id', out.count('chain ') == 3 and 'MISMATCH' not in out, out)
    check('w1: runtime loaded into guest docker (containerd store: Id = manifest digest)',
          'docker runtime id ' + REFS['runtime'].split('@')[1] in out, out)
    rc, out = run('sh', INSTALL + '/w1-import.sh')
    check('w1: second run skips', rc == 0 and out.count('skip ') == 3, out)
    setup(mutate=lambda s: s.__setitem__('dockerStore', 'classic'))
    rc, out = run('sh', INSTALL + '/w1-import.sh')
    check('w1: classic Docker store (Id = config ID) also passes', rc == 0 and 'docker runtime id ' + IDS['runtime'] in out, out)
    setup(mutate=lambda s: s['docker'].__setitem__('anxious-dev-runtime:61-5e73bc4', 'sha256:' + 'd'*64))
    rc, out = run('sh', INSTALL + '/w1-import.sh')
    check('w1: a foreign Docker image id triggers the load; the result is accepted only as the manifest', rc == 0 and 'docker loaded runtime' in out, out)
    setup()
    with open('/home/opc/issue61-images/anxious-dev-runner-61-5e73bc4.tar', 'a') as stream:
        stream.write('tampered')
    rc, out = run('sh', INSTALL + '/w1-import.sh')
    check('w1: checksum mismatch refuses before any import', rc == 3 and not state()['images'], out)
    setup(fail=['import'])
    rc, out = run('sh', INSTALL + '/w1-import.sh')
    check('w1: failed import is rc 5', rc == 5, out)

    # W2 precheck -----------------------------------------------------------------------------------
    with open(SRC + '/w2-apply.py') as stream:
        raw = stream.read()
    setup(mutate=imported)
    with open(INSTALL + '/w2-apply.py', 'w') as stream:
        stream.write(raw)
    rc, out = w2('apply')
    check('w2: placeholders left is rc 2', rc == 2, out)
    for name, kwargs in [
            ('host observer timer active', dict(units={'issue16-observer.timer': 'loaded active'}, mutate=imported)),
            ('owner waiting', dict(waiting=1, mutate=imported)),
            ('images not imported', dict()),
            ('runner-control exists', dict(mutate=lambda s: (imported(s), s['objects'].__setitem__(
                okey('Namespace', None, 'runner-control'), {'kind': 'Namespace', 'metadata': {'name': 'runner-control', 'uid': 'x'}})))),
            ('…5804 not running', dict(mutate=lambda s: (imported(s), s['objects'][okey('Pod', 'codex-runner',
                'runner-00000000000040008000000000005804')]['status'].__setitem__('phase', 'Failed')))),
            ('pool secret differs', dict(mutate=lambda s: (imported(s), s['objects'][okey('Secret', 'm2-hosting',
                'hosting-runner-pool-v2')]['data'].__setitem__('pool.json', base64.b64encode(b'{"version": 1}').decode())))),
            ('render refused', dict(mutate=imported, fail=['render'])),
            ('digest references not tagged (W1b not run)', dict(mutate=lambda s: imported(s, tagged=False))),
            ('docker holds another runtime image', dict(mutate=lambda s: (imported(s), s['docker'].__setitem__(
                'anxious-dev-runtime:61-5e73bc4', 'sha256:' + 'd'*64))))]:
        setup(**kwargs)
        before = json.dumps(state()['objects'], sort_keys=True)
        rc, out = w2('apply')
        s = state()
        check('w2: %s refuses with nothing changed' % name,
              rc == 3 and json.dumps(s['objects'], sort_keys=True) == before
              and not any('GRANT' in q for q in s.get('queries', [])), out)

    # W2 apply --------------------------------------------------------------------------------------
    setup(mutate=imported)
    rc, out = w2('apply')
    s = state()
    ev = evidence(out)
    check('w2: apply succeeds', rc == 0, out)
    check('w2: the renderer runs by the verified Docker Id, not by tag',
          any(l.startswith('docker run') and (' ' + REFS['runtime'].split('@')[1] + ' ') in l
              for l in open('/mock/calls.log').read().splitlines()))
    check('w2: api image is the runtime ref',
          s['objects'][okey('Deployment', 'm2-hosting', 'api')]['spec']['template']['spec']['containers'][0]['image'] == REFS['runtime'])
    check('w2: controller Deployment and namespace exist', okey('Deployment', 'runner-control', 'runner-controller') in s['objects']
          and okey('Namespace', None, 'runner-control') in s['objects'])
    check('w2: DSNs point at the service, temp files gone',
          s.get('dsnHosts') == {'controller-url': 'postgres.m2-hosting.svc.cluster.local:5432',
                                'observer-url': 'postgres.m2-hosting.svc.cluster.local:5432'}
          and not any(os.path.exists(p) for p in s.get('dsnTemp', [])), json.dumps(s.get('dsnHosts')))
    check('w2: grants sent once', sum('GRANT' in q for q in s.get('queries', [])) == 1)
    check('w2: no DSN password or traceback in output', 'SECRETPW' not in out and 'SECRET@' not in out, out)
    check('w2: only JSON outcome log lines shown', '"result": "deleting"' in out)
    check('w2: host role rules recorded before', 'rules before=' in out, out)
    check('w2: backup and created UIDs recorded', ev is not None and os.path.exists(ev + '/backup.json')
          and os.path.exists(ev + '/changes.json'), out)
    backup = json.load(open(ev + '/backup.json'))
    check('w2: Secret data never in evidence', all('object' not in e for e in backup if e['kind'] == 'Secret')
          and 'SECRETPW' not in open(ev + '/backup.json').read())
    check('w2: values dropped the host-observer keys', "dropped=['bootId', 'criSocket', 'crictl'" in out, out)
    check('w2: allowlist taken from the live api', "allowlist=['00000000-0000-4000-8000-000000000048', "
          "'00000000-0000-4000-8000-000000005804']" in out, out)
    changes = json.load(open(ev + '/changes.json'))
    check('w2: created UIDs come from the answers and match the live objects',
          set(changes['created']) == {'Namespace -/runner-control', 'ServiceAccount runner-control/runner-controller',
              'Role codex-runner/runner-controller', 'RoleBinding codex-runner/runner-controller',
              'ClusterRole -/runner-controller', 'ClusterRoleBinding -/runner-controller',
              'Deployment runner-control/runner-controller', 'Secret runner-control/runner-controller-db'}
          and changes['creationUncertain'] == [] and not changes['uncertain'], json.dumps(changes))
    check('w2: grants sent in one transaction', any(q.startswith('BEGIN;') and q.rstrip().endswith('COMMIT;')
                                                   for q in s.get('queries', [])))

    # W2 rollback after a full apply ----------------------------------------------------------------
    s = state()
    s['openStops'] = ['00000000-0000-4000-8000-000000000048|1|3bb0fe46-af68-4d3a-9c67-35bce5f44cf7|v2']
    s['controllerPods'] = ['runner-controller-abc']
    json.dump(s, open('/mock/state.json', 'w'))
    rc, out = w2('rollback', ev)
    s = state()
    check('w2 rollback: succeeds', rc == 0, out)
    check('w2 rollback: controller scaled to 0 first', s.get('scaled') is True)
    check('w2 rollback: open v2 stop listed, not finished', '|v2' in out and 'not finished here' in out, out)
    check('w2 rollback: api image restored',
          s['objects'][okey('Deployment', 'm2-hosting', 'api')]['spec']['template']['spec']['containers'][0]['image']
          == 'docker.io/library/old-runtime@sha256:' + 'f'*64)
    check('w2 rollback: host role rules restored', any(r.get('verbs') == ['create', 'delete'] for r in
          s['objects'][okey('Role', 'codex-runner', 'runner-pool-controller')].get('rules', [])))
    check('w2 rollback: created objects deleted by UID, namespace last',
          all(okey(*k) not in s['objects'] for k in NEW) and s['deleted'][-1] == 'namespace||runner-control', json.dumps(s.get('deleted')))
    check('w2 rollback: says what is not reverted', '061 grants' in out and 'stay so' in out, out)

    # Rollback refuses a replaced object and a namespace with foreign content -----------------------
    setup(mutate=imported)
    rc, out = w2('apply')
    ev = evidence(out)
    s = state()
    s['objects'][okey('ClusterRole', None, 'runner-controller')]['metadata']['uid'] = 'someone-else'
    s['objects'][okey('ConfigMap', 'runner-control', 'foreign')] = {'kind': 'ConfigMap', 'metadata': {
        'name': 'foreign', 'namespace': 'runner-control', 'uid': 'f'}}
    json.dump(s, open('/mock/state.json', 'w'))
    rc, out = w2('rollback', ev)
    s = state()
    check('w2 rollback: object with another UID is reported, not deleted',
          okey('ClusterRole', None, 'runner-controller') in s['objects'] and 'not deleted (live UID someone-else' in out, out)
    check('w2 rollback: namespace with foreign content is not deleted (rc 5)',
          rc == 5 and okey('Namespace', None, 'runner-control') in s['objects'] and 'configmap/foreign' in out, out)

    # Partial failure -------------------------------------------------------------------------------
    setup(mutate=imported, fail=['set-image'])
    rc, out = w2('apply')
    check('w2: failure at the api image is rc 5 and lists what changed', rc == 5 and 'partial: done: granted 061' in out
          and 'updated CNP' in out and 'uncertain (may have been applied): set api image' in out
          and 'rollback: python3 -I' in out, out)
    ev = evidence(out)
    os.unlink('/mock/fail')
    rc, out = w2('rollback', ev)
    s = state()
    check('w2: rollback after a partial apply restores the CNP and removes the namespace',
          rc == 0 and s['objects'][okey('CiliumNetworkPolicy', 'codex-runner', 'runner-callback-draft')]['spec'] == {'old': True}
          and okey('Namespace', None, 'runner-control') not in s['objects'], out)
    rc, out = w2('rollback', '/tmp/elsewhere')
    check('w2: rollback outside the evidence root is refused', rc == 3, out)


    # Review round 1 counterexamples ------------------------------------------------------------------
    for name, change in [
            ('controller Deployment replaced after apply',
             lambda s: s['objects'][okey('Deployment', 'runner-control', 'runner-controller')]['metadata'].__setitem__('uid', 'foreign')),
            ('pre-existing CNP replaced after apply',
             lambda s: s['objects'][okey('CiliumNetworkPolicy', 'codex-runner', 'runner-callback-draft')]['metadata'].__setitem__('uid', 'foreign'))]:
        setup(mutate=imported)
        rc, out = w2('apply')
        ev = evidence(out)
        s = state()
        change(s)
        s['controllerPods'] = ['runner-controller-abc']
        json.dump(s, open('/mock/state.json', 'w'))
        before = json.dumps(state()['objects'], sort_keys=True)
        rc, out = w2('rollback', ev)
        s = state()
        check('w2 rollback: %s refuses before any change (rc 3, not scaled)' % name,
              rc == 3 and not s.get('scaled') and json.dumps(s['objects'], sort_keys=True) == before
              and 'refused before any change' in out, out)

    def foreign(s):
        imported(s)
        s['foreignBeforeCreate'] = {okey('ClusterRole', None, 'runner-controller'): {
            'kind': 'ClusterRole', 'metadata': {'name': 'runner-controller'}, 'rules': []}}
    setup(mutate=foreign)
    rc, out = w2('apply')
    s = state()
    ev = evidence(out)
    changes = json.load(open(ev + '/changes.json'))
    check('w2: a name created by another actor after the last lookup is never modified or adopted (create-only)',
          rc == 5 and s['objects'][okey('ClusterRole', None, 'runner-controller')]['rules'] == []
          and 'ClusterRole -/runner-controller' not in changes['created']
          and 'ClusterRole -/runner-controller' in changes['creationUncertain'], out)
    rc, out = w2('rollback', ev)
    s = state()
    check('w2 rollback: the foreign ClusterRole is reported and never deleted',
          okey('ClusterRole', None, 'runner-controller') in s['objects'] and 'creation uncertain' in out, out)

    setup(mutate=imported, fail=['kill-create-deployment'])
    rc, out = w2('apply')
    ev = evidence(out)
    changes = json.load(open(ev + '/changes.json'))
    check('w2: an interrupted run leaves its attempt pending in changes.json',
          rc != 0 and changes['pending'] == {'text': 'created Deployment runner-control/runner-controller',
                                             'objects': ['Deployment runner-control/runner-controller']}, json.dumps(changes))
    os.unlink('/mock/fail')
    before = json.dumps(state()['objects'], sort_keys=True)
    rc, out = w2('rollback', ev)
    check('w2 rollback after an interrupted controller create refuses before any change',
          rc == 3 and 'interrupted attempt treated as uncertain' in out
          and json.dumps(state()['objects'], sort_keys=True) == before, out)

    setup(mutate=imported, fail=['kill-grants'])
    rc, out = w2('apply')
    ev = evidence(out)
    changes = json.load(open(ev + '/changes.json'))
    check('w2: interrupted after the GRANT commit: pending, not "nothing"',
          rc != 0 and changes['pending']['text'].startswith('granted 061') and changes['done'] == [], json.dumps(changes))
    os.unlink('/mock/fail')
    rc, out = w2('rollback', ev)
    check('w2 rollback after an interrupted GRANT reports it and changes nothing else',
          rc == 0 and 'interrupted attempt treated as uncertain: granted 061' in out, out)

    for stage, text in [('grants-lost', 'granted 061'), ('create-namespace-lost', 'created Namespace runner-control'),
                        ('secret-lost', 'created Secret runner-control/runner-controller-db'),
                        ('set-image-lost', 'set api image')]:
        setup(mutate=imported, fail=[stage])
        rc, out = w2('apply')
        ev = evidence(out)
        changes = json.load(open(ev + '/changes.json'))
        check('w2: lost answer at %s is reported as uncertain, not as nothing' % stage,
              rc == 5 and any(u.startswith(text) for u in changes['uncertain'])
              and 'uncertain (may have been applied): ' + text in out, out)
        if stage in {'create-namespace-lost', 'secret-lost'}:
            os.unlink('/mock/fail')
            rc, out = w2('rollback', ev)
            s = state()
            check('w2 rollback after %s: the uncertain object is reported, never deleted' % stage,
                  rc == 5 and okey('Namespace', None, 'runner-control') in s['objects']
                  and ('creation uncertain' in out or 'not deleted' in out), out)

    # W0 helper: ctr_has rc 0/1/2 and check_imports ----------------------------------------------------
    for case, mutate, want_rc, want in [('imported', imported, 0, 'imported docker.io'),
                                        ('absent', None, 1, 'not imported docker.io'),
                                        ('lookup failed', None, 2, 'LOOKUP FAILED: containerd image list')]:
        setup(mutate=mutate, fail=['ctr-ls'] if case == 'lookup failed' else [])
        rc, out = run('sh', '-c', '. %s/lib.sh; ctr_has docker.io/library/anxious-dev-runner:61-5e73bc4; echo "rc=$?"' % INSTALL)
        check('w0 helper: ctr_has %s is rc %d' % (case, want_rc), out.strip().endswith('rc=%d' % want_rc), out)
        rc, out = run('sh', '-c', 'lookups=0; note() { echo "$1"; }; fail_lookup() { echo "LOOKUP FAILED: $1"; '
                      'lookups=$((lookups+1)); }; . %s/lib.sh; . %s/w0-lib.sh; check_imports; echo "lookups=$lookups"'
                      % (INSTALL, INSTALL))
        check('w0 check_imports: %s' % case, want in out and out.strip().endswith(
            'lookups=%d' % (3 if case == 'lookup failed' else 0)), out)

    # W3 -----------------------------------------------------------------------------------------------
    import time as _time
    O48, O02, O04 = (f[0] for f in FIXTURES)
    POD = 'b9bbc857-cd36-4a7b-89b5-1ca7c7d3b61b'
    NAME = 'runner-' + O04.replace('-', '')

    def w3_state(*, running=0, reg48='idle_stopped', release=None, reclaim=None, gets=1, slow=0, w2_clean=True):
        def mutate(st):
            st['objects'][okey('Deployment', 'runner-control', 'runner-controller')] = {'kind': 'Deployment', 'metadata': {
                'name': 'runner-controller', 'namespace': 'runner-control', 'uid': 'ctrl-uid'}, 'status': {'readyReplicas': 1}}
            reg = [{'owner': O48, 'phase': reg48, 'generation': 2, 'revision': 4, 'pod': None},
                   {'owner': O02, 'phase': 'idle_stopped', 'generation': 4, 'revision': 12, 'pod': None},
                   {'owner': O04, 'phase': 'runner_unreachable', 'generation': 3, 'revision': 10, 'pod': POD}]
            done = {'phase': 'idle_stopped', 'pod': None, 'completed': '2026-11-01T15:00:00+00:00', 'receipt': '4',
                    'proofPod': POD, 'evidence': 'pod-deleted', 'boot': 'b', 'drain': '503', 'reason': 'auth_revoked'}
            st['sql'] = {'registry': ['\n'.join(json.dumps(r) for r in reg)], 'stops': [''],
                         'running': [json.dumps({'running': running})],
                         'release': [x if isinstance(x, str) else json.dumps(x) for x in (release or [dict(done, phase='stopping', pod=POD,
                                                                                    completed=None), done])]}
            st['reclaim'] = reclaim or {'rc': 0, 'line': json.dumps({'stage': 'reclaim', 'result': 'accepted', 'owner': O04,
                                                                       'podUid': POD, 'stopId': 'stop-1'})}
            st['podGetsBeforeGone'] = {NAME: gets}
            st['slowPodGet'] = slow
        setup(mutate=mutate)
        os.makedirs('/root/issue61/evidence/w2-x', mode=0o700)
        json.dump({'pending': None, 'done': ['x'], 'uncertain': [] if w2_clean else ['y'],
                   'created': {'Deployment runner-control/runner-controller': 'ctrl-uid'}, 'creationUncertain': []},
                  open('/root/issue61/evidence/w2-x/changes.json', 'w'))

    def w3(*args, wait=None):
        env = dict(os.environ, **({'W3_WAIT_SECONDS': str(wait)} if wait else {}))
        start = _time.monotonic()
        done = subprocess.run(['python3', '-I', INSTALL + '/w3-reclaim.py'] + list(args), stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=300, env=env)
        return done.returncode, done.stdout.decode(), _time.monotonic() - start

    w3_state()
    rc, out, _ = w3('--expected-running', '0', wait=30)
    check('w3: happy path reclaims once and proves the release', rc == 0 and state()['reclaimCalls'] == 1
          and '"proofPod": "%s"' % POD in out, out)
    for args in ([], ['--expected-running'], ['--expected-running', 'x'], ['--expected-running', '-1'],
                 ['--expected', '0'], ['--expected-running', '0', 'extra']):
        w3_state()
        rc, out, _ = w3(*args)
        check('w3: bad count input %s refuses before anything runs' % args, rc == 3 and not state().get('reclaimCalls'), out)
    w3_state(running=1)
    rc, out, _ = w3('--expected-running', '0')
    check('w3: running count changed since the go refuses before reclaim, prints both',
          rc == 3 and not state().get('reclaimCalls') and 'go was for 0' in out and 'new go with 1' in out, out)
    w3_state(running=1)
    rc, out, _ = w3('--expected-running', '1', wait=30)
    check('w3: a go given for the current count proceeds', rc == 0 and state()['reclaimCalls'] == 1, out)
    w3_state(reg48='runner_unreachable')
    rc, out, _ = w3('--expected-running', '0')
    check('w3: …0048 not released refuses before reclaim', rc == 3 and not state().get('reclaimCalls'), out)
    w3_state(w2_clean=False)
    rc, out, _ = w3('--expected-running', '0')
    check('w3: W2 with an uncertain attempt refuses', rc == 3 and not state().get('reclaimCalls'), out)
    w3_state(reclaim={'rc': 3, 'line': json.dumps({'stage': 'reclaim', 'result': 'refused', 'code': 'runner_available',
                                                     'observed': {'health': {'available': True}}})})
    rc, out, _ = w3('--expected-running', '0')
    check('w3: refusal before a stop is opened is rc 4 "nothing changed"', rc == 4 and 'nothing changed' in out, out)
    w3_state(reclaim={'rc': 3, 'line': json.dumps({'stage': 'reclaim', 'result': 'refused', 'code': 'drain_accepted',
                     'observed': {'stopId': 'stop-1', 'stopOpenedByThisRun': True, 'receiptVersion': 1}})},
             release=[{'phase': 'idle_stopped', 'pod': None, 'completed': 'x', 'receipt': '1', 'proofPod': POD,
                       'evidence': 'pod-deleted'}])
    rc, out, took = w3('--expected-running', '0', wait=20)
    check('w3: drain_accepted + completed v1 is rc 7 with normal release confirmed=True, without waiting',
          rc == 7 and 'nothing changed' not in out and 'stopOpenedByThisRun=True' in out
          and 'normal release confirmed=True' in out and took < 10, '%s took %.1f' % (out, took))
    drain_refused = {'rc': 3, 'line': json.dumps({'stage': 'reclaim', 'result': 'refused', 'code': 'drain_accepted',
                     'observed': {'stopId': 'stop-1', 'stopOpenedByThisRun': True, 'receiptVersion': 1}})}
    w3_state(reclaim=drain_refused, release=[{'phase': 'stopping', 'pod': POD, 'completed': None, 'receipt': '1',
                                              'proofPod': None, 'evidence': None}], gets=99)
    rc, out, _ = w3('--expected-running', '0', wait=3)
    check('w3: drain_accepted + uncompleted v1 is rc 7 with normal release confirmed=False',
          rc == 7 and 'normal release confirmed=False' in out, out)
    w3_state(release=[{'phase': 'idle_stopped', 'pod': None, 'completed': 'x', 'receipt': '1', 'proofPod': POD,
                       'evidence': 'pod-deleted'}])
    rc, out, _ = w3('--expected-running', '0', wait=3)
    check('w3: an accepted R2 whose stop completed with a v1 receipt is not an R2 success (rc 6)', rc == 6, out)
    w3_state(reclaim={'rc': 1, 'line': json.dumps({'stage': 'reclaim', 'result': 'failed'})})
    rc, out, _ = w3('--expected-running', '0')
    check('w3: CLI failure is rc 5, do not rerun', rc == 5 and 'do not rerun' in out, out)
    w3_state(reclaim={'rc': 0, 'line': json.dumps({'stage': 'reclaim', 'result': 'accepted', 'owner': O04,
                                                     'podUid': 'other', 'stopId': 's'})})
    rc, out, _ = w3('--expected-running', '0')
    check('w3: accepted for another Pod is rc 5', rc == 5, out)
    incomplete = {'phase': 'idle_stopped', 'pod': None, 'completed': None, 'receipt': '4', 'proofPod': POD,
                  'evidence': 'pod-deleted'}
    w3_state(release=[incomplete])
    rc, out, took = w3('--expected-running', '0', wait=3)
    check('w3: an uncompleted stop never passes (rc 6)', rc == 6 and took < 20, '%s took %.1f' % (out, took))
    w3_state(release=['FAIL'])
    rc, out, took = w3('--expected-running', '0', wait=3)
    check('w3: a release lookup that fails is a failed lookup, not a crash (rc 6)', rc == 6 and 'unexpected' not in out, out)
    w3_state(release=['"not-an-object"'])
    rc, out, took = w3('--expected-running', '0', wait=3)
    check('w3: failing release lookups end at the deadline (rc 6)', rc == 6 and took < 20, '%s took %.1f' % (out, took))
    w3_state(slow=2, gets=99)
    rc, out, took = w3('--expected-running', '0', wait=4)
    check('w3: slow lookups are capped by the wall-clock deadline', rc == 6 and took < 15, '%s took %.1f' % (out, took))

    # W1b ----------------------------------------------------------------------------------------------
    REAL = {'runtime': ('sha256:eb3a18acd1b2856f6a95c7686c0c0ee4e5194b6237266b3443664f37feb3377c', IDS['runtime']),
            'runner': ('sha256:067c77de5d6b3673ece9b243f7753a9184a007cd44ab0ea1c93dc3044458a5e4', IDS['runner']),
            'shell-executor': ('sha256:9c87d626cdf6798db5bc5a562cc68af1b13a36c8f90d3738e19c6553e7cf4d12', IDS['shell-executor'])}

    def w1_done(s, executor_tagged=True, wrong=None):
        for tag, (digest, image_id) in REAL.items():
            ref = 'docker.io/library/anxious-dev-%s:61-5e73bc4' % tag
            s['images'][ref] = {'ref': ref, 'id': image_id, 'digest': 'sha256:' + 'b'*64 if tag == wrong else digest}
        if executor_tagged:  # As on the guest: the identical executor manifest was tagged in #60.
            d, i = REAL['shell-executor']
            s['images']['docker.io/library/anxious-dev-shell-executor@' + d] = {'ref': 'x', 'id': i, 'digest': d}
    setup(mutate=w1_done)
    rc, out = run('sh', INSTALL + '/w1b-tag.sh')
    s = state()
    check('w1b: tags runtime and runner, skips the executor that already resolves, all resolve to host IDs',
          rc == 0 and out.count('tagged ') == 2 and out.count('skip ') == 1 and out.count(' -> sha256:') == 3
          and all(('docker.io/library/anxious-dev-%s@%s' % (t, d)) in s['images'] for t, (d, _) in REAL.items()), out)
    rc, out = run('sh', INSTALL + '/w1b-tag.sh')
    check('w1b: second run skips all three', rc == 0 and out.count('skip ') == 3, out)
    setup(mutate=lambda s: w1_done(s, wrong='runner'))
    rc, out = run('sh', INSTALL + '/w1b-tag.sh')
    check('w1b: a source tag with another manifest refuses before any tag', rc == 3 and 'tagged docker' not in out
          and not any('@sha256:eb3a18ac' in k for k in state()['images']), out)
    def api_on_new(s):
        w1_done(s)
        s['objects'][okey('Deployment', 'm2-hosting', 'api')]['spec']['template']['spec']['containers'][0]['image'] = \
            'docker.io/library/anxious-dev-runtime@' + REAL['runtime'][0]
    setup(mutate=api_on_new)
    rc, out = run('sh', INSTALL + '/w1b-tag.sh')
    check('w1b: refuses while the api Deployment still points at the new digest (roll W2 back first)',
          rc == 3 and 'roll W2 back first' in out and not any('@' + REAL['runtime'][0] in k for k in state()['images']), out)
    setup(mutate=w1_done, fail=['tag'])
    rc, out = run('sh', INSTALL + '/w1b-tag.sh')
    check('w1b: a failed tag is rc 5', rc == 5, out)

    # W2 rollback marker and W3 evidence selection ------------------------------------------------------
    setup(mutate=imported, fail=['set-image'])
    rc, out = w2('apply')
    ev = evidence(out)
    os.unlink('/mock/fail')
    rc, out = w2('rollback', ev)
    check('w2 rollback writes the rolled-back marker only after success', rc == 0 and os.path.exists(ev + '/rolled-back.json')
          and os.path.exists(ev + '/rollback-started.json'), out)
    setup(mutate=imported, fail=['set-image'])
    rc, out = w2('apply')
    ev2 = evidence(out)
    with open('/mock/fail', 'w') as stream:
        stream.write('replace')
    rc, out = w2('rollback', ev2)
    check('w2: a failed rollback leaves rollback-started without the certified marker',
          rc == 5 and os.path.exists(ev2 + '/rollback-started.json') and not os.path.exists(ev2 + '/rolled-back.json'), out)
    mode = oct(os.stat(ev2 + '/changes.json').st_mode & 0o777)
    check('w2 evidence files are 0600', mode == '0o600', mode)

    for name, flag, want in [('accepted but Terminating', 'nsTerminating', 'still Terminating'),
                             ('absence lookup failing', 'failNsGetAfterDelete', 'absence lookup failed')]:
        setup(mutate=imported, fail=['set-image'])
        rc, out = w2('apply')
        ev3 = evidence(out)
        os.unlink('/mock/fail')
        st = state(); st[flag] = True; json.dump(st, open('/mock/state.json', 'w'))
        env_rc = subprocess.run(['python3', '-I', INSTALL + '/w2-apply.py', 'rollback', ev3], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, env=dict(os.environ, W2_NS_WAIT_SECONDS='2'), timeout=120)
        out = env_rc.stdout.decode()
        check('w2 rollback: namespace %s is rc 8 and not certified' % name,
              env_rc.returncode == 8 and want in out and not os.path.exists(ev3 + '/rolled-back.json')
              and os.path.exists(ev3 + '/rollback-started.json'), out)

    def two_runs(first):
        w3_state()
        os.makedirs('/root/issue61/evidence/w2-a', mode=0o700)
        json.dump(first, open('/root/issue61/evidence/w2-a/changes.json', 'w'))
    partial = {'pending': None, 'done': ['x'], 'uncertain': [], 'created': {'Namespace -/runner-control': 'n'},
               'creationUncertain': []}
    two_runs(partial)
    rc, out, _ = w3('--expected-running', '0', wait=30)
    check('w3: picks the clean W2 run over an earlier one that never created the controller (no marker)',
          rc == 0 and 'live: w2-x' in out, out)
    two_runs({'pending': None, 'done': ['x'], 'uncertain': [],
              'created': {'Deployment runner-control/runner-controller': 'ctrl-uid'}, 'creationUncertain': []})
    rc, out, _ = w3('--expected-running', '0')
    check('w3: two live-looking W2 runs refuse', rc == 3 and not state().get('reclaimCalls'), out)
    two_runs({'pending': None, 'done': ['x'], 'uncertain': [],
              'created': {'Deployment runner-control/runner-controller': 'ctrl-uid'}, 'creationUncertain': []})
    open('/root/issue61/evidence/w2-a/rolled-back.json', 'w').write('{}')
    rc, out, _ = w3('--expected-running', '0', wait=30)
    check('w3: a rolled-back W2 run is never selected', rc == 0 and 'live: w2-x' in out, out)
    for name, flags in [('pending', {'pending': {'text': 't', 'objects': []}}), ('uncertain', {'uncertain': ['set api image']}),
                        ('creationUncertain', {'creationUncertain': ['Deployment runner-control/runner-controller']})]:
        two_runs(dict(partial, **flags))
        rc, out, _ = w3('--expected-running', '0')
        check('w3: another W2 run with %s refuses before reclaim even when a clean run exists' % name,
              rc == 3 and 'pending/uncertain' in out and not state().get('reclaimCalls'), out)
    two_runs(partial)
    open('/root/issue61/evidence/w2-a/rollback-started.json', 'w').write('{}')
    rc, out, _ = w3('--expected-running', '0')
    check('w3: a started but uncertified rollback refuses before reclaim',
          rc == 3 and 'uncertified rollback' in out and not state().get('reclaimCalls'), out)

    # W1c ----------------------------------------------------------------------------------------------
    NSUID = '54a486c3-0750-411b-82bb-70a39b3c80b4'
    METRICS_MSG = ('Discovery failed for some groups, 1 failing: unable to retrieve the complete list of server APIs: '
                   'metrics.k8s.io/v1beta1: stale GroupVersion discovery: metrics.k8s.io/v1beta1')

    def w1c_state(**over):
        def mutate(st):
            conditions = [{'type': 'NamespaceDeletionDiscoveryFailure', 'status': 'True', 'message': METRICS_MSG},
                          {'type': 'NamespaceContentRemaining', 'status': 'False'},
                          {'type': 'NamespaceDeletionContentFailure', 'status': 'False'},
                          {'type': 'NamespaceFinalizersRemaining', 'status': 'False'}]
            ns = {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': 'runner-control', 'uid': NSUID,
                  'resourceVersion': '42', 'deletionTimestamp': '2026-11-01T15:57:11Z'},
                  'spec': {'finalizers': ['kubernetes']}, 'status': {'phase': 'Terminating', 'conditions': conditions}}
            for kind in over.get('drop', []):
                conditions[:] = [c for c in conditions if c['type'] != kind]
            for kind, value in over.get('cond', {}).items():
                [c for c in conditions if c['type'] == kind][0].update(value)
            ns['metadata'].update(over.get('meta', {}))
            ns['status'].update(over.get('status', {}))
            st['objects'][okey('Namespace', None, 'runner-control')] = ns
            st['objects'][okey('Namespace', None, 'shell-pilot')] = {'kind': 'Namespace', 'metadata': {
                'name': 'shell-pilot', 'uid': 'sp', 'resourceVersion': '7'}, 'status': {'phase': 'Terminating'}}
            base = '/api/v1/namespaces/runner-control/'
            st['raw'] = {'/api/v1': {'resources': [{'name': 'pods', 'namespaced': True, 'verbs': ['list']},
                                                   {'name': 'pods/log', 'namespaced': True, 'verbs': ['get']},
                                                   {'name': 'nodes', 'namespaced': False, 'verbs': ['list']},
                                                   {'name': 'secrets', 'namespaced': True, 'verbs': ['list']}]},
                         '/apis': {'groups': [{'versions': [{'groupVersion': 'apps/v1'}]},
                                              {'versions': [{'groupVersion': 'metrics.k8s.io/v1beta1'}]}]},
                         '/apis/apps/v1': {'resources': [{'name': 'deployments', 'namespaced': True, 'verbs': ['list']}]},
                         base + 'pods?limit=500': {'metadata': {}, 'items': []},
                         base + 'secrets?limit=500': {'metadata': {}, 'items': []},
                         '/apis/apps/v1/namespaces/runner-control/deployments?limit=500': {'metadata': {}, 'items': []}}
            for path, value in over.get('raw', {}).items():
                if value is None:
                    st['raw'].pop(path, None)
                else:
                    st['raw'][path] = value
            for flag in over.get('flags', []):
                st[flag] = True
        setup(mutate=mutate)

    def w1c(wait=None):
        env = dict(os.environ, **({'W1C_WAIT_SECONDS': str(wait)} if wait else {}))
        done = subprocess.run(['python3', '-I', INSTALL + '/w1c-finalize-ns.py'], stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=120, env=env)
        return done.returncode, done.stdout.decode()

    w1c_state()
    rc, out = w1c()
    s = state()
    check('w1c: finalizes exactly runner-control, proves it gone, leaves shell-pilot',
          rc == 0 and s.get('finalizeCalls') == ['/api/v1/namespaces/runner-control/finalize']
          and okey('Namespace', None, 'runner-control') not in s['objects'] and okey('Namespace', None, 'shell-pilot') in s['objects']
          and 'stores no objects): metrics.k8s.io/v1beta1' in out and '3 namespaced resource types listed empty' in out, out)
    for name, over in [
            ('another UID', dict(meta={'uid': 'other'})),
            ('not Terminating', dict(status={'phase': 'Active'})),
            ('no deletionTimestamp', dict(meta={'deletionTimestamp': None})),
            ('ContentRemaining True', dict(cond={'NamespaceContentRemaining': {'status': 'True'}})),
            ('ContentRemaining missing', dict(drop=['NamespaceContentRemaining'])),
            ('FinalizersRemaining missing', dict(drop=['NamespaceFinalizersRemaining'])),
            ('discovery failure names another group', dict(cond={'NamespaceDeletionDiscoveryFailure': {
                'message': METRICS_MSG.replace('1 failing', '2 failing') + ', example.io/v1'}})),
            ('apps/v1 discovery fails', dict(raw={'/apis/apps/v1': None})),
            ('a list fails', dict(raw={'/api/v1/namespaces/runner-control/secrets?limit=500': None})),
            ('an object remains', dict(raw={'/apis/apps/v1/namespaces/runner-control/deployments?limit=500':
                                            {'metadata': {}, 'items': [{'metadata': {'name': 'runner-controller'}}]}})),
            ('/apis without groups', dict(raw={'/apis': {}})),
            ('apps/v1 without resources', dict(raw={'/apis/apps/v1': {}})),
            ('a list without items', dict(raw={'/api/v1/namespaces/runner-control/secrets?limit=500': {}})),
            ('an empty first page with a continuation', dict(raw={
                '/api/v1/namespaces/runner-control/secrets?limit=500': {'metadata': {'continue': 'next'}, 'items': []}})),
            ('a malformed resource entry', dict(raw={'/apis/apps/v1': {'resources': [{'name': 'deployments'}]}}))]:
        w1c_state(**over)
        rc, out = w1c()
        s = state()
        check('w1c: %s refuses before any write (rc 3)' % name,
              rc == 3 and not s.get('finalizeCalls') and okey('Namespace', None, 'runner-control') in s['objects'], out)
    w1c_state(flags=['rvChanged'])
    rc, out = w1c()
    check('w1c: a namespace changed between read and finalize is refused by the API (rc 5)',
          rc == 5 and okey('Namespace', None, 'runner-control') in state()['objects'], out)
    w1c_state(flags=['finalizeKeeps'])
    rc, out = w1c(wait=2)
    check('w1c: finalize answered but the namespace stays is rc 8, not done', rc == 8 and 'still present' in out, out)
    w1c_state(flags=['finalizeKeeps', 'failNsGetAfterFinalize'])
    rc, out = w1c(wait=2)
    check('w1c: a failed absence lookup is rc 8, not done', rc == 8 and 'lookup failed' in out, out)

    # Rollback postcheck producer: a failed lookup never reads as absent ---------------------------------
    for case in ('ok', 'failing'):
        setup(mutate=imported)
        os.makedirs('/root/issue61/evidence/w2-20261101T150903Z', mode=0o700)
        json.dump([{'kind': 'Namespace', 'namespace': None, 'name': 'runner-control', 'absent': True}],
                  open('/root/issue61/evidence/w2-20261101T150903Z/backup.json', 'w'))
        if case == 'failing':
            st = state(); st['failAllGets'] = True; json.dump(st, open('/mock/state.json', 'w'))
        done = subprocess.run(['python3', '-I', SRC + '/postcheck-w2-rollback.py'], stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=60)
        out = done.stdout.decode()
        if case == 'ok':
            check('postcheck producer: lookups that answer give "absent" only from the API answer',
                  'absent-before Namespace -/runner-control now absent' in out, out)
        else:
            check('postcheck producer: failed lookups exit 3 and never print absent',
                  done.returncode == 3 and 'LOOKUP FAILED' in out and 'now absent' not in out
                  and 'namespace runner-control absent' not in out, out)

    # rollout/ns-contents.py: proof of "only the defaults" ------------------------------------------------
    def ns_raw(**over):
        base = '/api/v1/namespaces/runner-control/'
        raw = {'/api/v1': {'resources': [{'name': 'serviceaccounts', 'namespaced': True, 'verbs': ['list']},
                                         {'name': 'configmaps', 'namespaced': True, 'verbs': ['list']},
                                         {'name': 'events', 'namespaced': True, 'verbs': ['list']},
                                         {'name': 'persistentvolumeclaims', 'namespaced': True, 'verbs': ['list']}]},
               '/apis': {'groups': [{'versions': [{'groupVersion': 'networking.k8s.io/v1'}]},
                                    {'versions': [{'groupVersion': 'metrics.k8s.io/v1beta1'}]}]},
               '/apis/networking.k8s.io/v1': {'resources': [{'name': 'networkpolicies', 'namespaced': True, 'verbs': ['list']}]},
               base + 'serviceaccounts?limit=500': {'metadata': {}, 'items': [{'metadata': {'name': 'default'}}]},
               base + 'configmaps?limit=500': {'metadata': {}, 'items': [{'metadata': {'name': 'kube-root-ca.crt'}}]},
               base + 'persistentvolumeclaims?limit=500': {'metadata': {}, 'items': []},
               '/apis/networking.k8s.io/v1/namespaces/runner-control/networkpolicies?limit=500': {'metadata': {}, 'items': []}}
        raw.update(over)
        return raw

    def ns_contents(raw):
        setup(mutate=lambda st: st.__setitem__('raw', {k: v for k, v in raw.items() if v is not None}))
        done = subprocess.run(['python3', '-I', '/m3/multi-user-runners/rollout/ns-contents.py', 'runner-control'],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        return done.returncode, done.stdout.decode()

    rc, out = ns_contents(ns_raw())
    check('ns-contents: only the defaults is rc 0 (events ignored, metrics skipped)',
          rc == 0 and 'only the defaults' in out and 'metrics.k8s.io/v1beta1' in out, out)
    base = '/api/v1/namespaces/runner-control/'
    for name, over, want in [
            ('a leftover PVC', {base + 'persistentvolumeclaims?limit=500': {'metadata': {}, 'items': [{'metadata': {'name': 'x'}}]}}, 4),
            ('another ConfigMap', {base + 'configmaps?limit=500': {'metadata': {}, 'items': [
                {'metadata': {'name': 'kube-root-ca.crt'}}, {'metadata': {'name': 'other'}}]}}, 4),
            ('a list failing', {'/apis/networking.k8s.io/v1/namespaces/runner-control/networkpolicies?limit=500': None}, 3),
            ('another group failing discovery', {'/apis/networking.k8s.io/v1': None}, 3),
            ('a continuation token', {base + 'persistentvolumeclaims?limit=500': {'metadata': {'continue': 'n'}, 'items': []}}, 3),
            ('a list without metadata', {base + 'persistentvolumeclaims?limit=500': {'items': []}}, 3),
            ('/apis without groups', {'/apis': {}}, 3)]:
        rc, out = ns_contents(ns_raw(**over))
        check('ns-contents: %s is rc %d' % (name, want), rc == want, out)

    print('passed %d, failed %d' % (passed, failed))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
