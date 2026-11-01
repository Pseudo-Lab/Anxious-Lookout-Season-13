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
    for name in ('lib.sh', 'w1-import.sh'):
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
             'applyStages': {'0': 'apply-ns', '1': 'apply-rbac', '2': 'apply-cnp', '3': 'apply-deploy'},
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


def imported(s):
    for name, archive in s['archives'].items():
        s['images'][archive['ref']] = archive
    s['docker']['anxious-dev-runtime:61-5e73bc4'] = IDS['runtime']


def install_fakes():
    for tool, path in (('k3s', '/usr/local/bin/k3s'), ('docker', '/usr/local/bin/docker'),
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
    check('w1: runtime loaded into guest docker', 'docker runtime id ' + IDS['runtime'] in out, out)
    rc, out = run('sh', INSTALL + '/w1-import.sh')
    check('w1: second run skips', rc == 0 and out.count('skip ') == 3, out)
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
            ('render refused', dict(mutate=imported, fail=['render']))]:
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
          and os.path.exists(ev + '/created.json'), out)
    backup = json.load(open(ev + '/backup.json'))
    check('w2: Secret data never in evidence', all('object' not in e for e in backup if e['kind'] == 'Secret')
          and 'SECRETPW' not in open(ev + '/backup.json').read())
    check('w2: values dropped the host-observer keys', "dropped=['bootId', 'criSocket', 'crictl'" in out, out)
    check('w2: allowlist taken from the live api', "allowlist=['00000000-0000-4000-8000-000000000048', "
          "'00000000-0000-4000-8000-000000005804']" in out, out)
    created = json.load(open(ev + '/created.json'))

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
    check('w2: failure at the api image is rc 5 and lists what changed', rc == 5 and 'partial: already changed by this run: '
          'granted 061' in out and 'applied CNP' in out and 'rollback: python3 -I' in out, out)
    ev = evidence(out)
    os.unlink('/mock/fail')
    rc, out = w2('rollback', ev)
    s = state()
    check('w2: rollback after a partial apply restores the CNP and removes the namespace',
          rc == 0 and s['objects'][okey('CiliumNetworkPolicy', 'codex-runner', 'runner-callback-draft')]['spec'] == {'old': True}
          and okey('Namespace', None, 'runner-control') not in s['objects'], out)
    rc, out = w2('rollback', '/tmp/elsewhere')
    check('w2: rollback outside the evidence root is refused', rc == 3, out)

    print('passed %d, failed %d' % (passed, failed))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
