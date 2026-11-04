"""Mock harness for the #61 W5 script (w5-rotation.py). Docker only, never on a real node:
    docker run --rm -v "$PWD/infra/hosting/m3:/m3:ro" python:3.9-alpine \
      python3 -I /m3/multi-user-runners/issue61/test_w5.py
mock/fake_w5.py stands in for k3s kubectl and systemctl; the claims reader runs for real against a planted JWT.
"""
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import time

SRC = '/m3/multi-user-runners/issue61'
INSTALL = '/root/issue61'
EVIDENCE = INSTALL + '/evidence'
POD_UID = 'e66fa6fa-3256-4dcb-a03b-2bc5923345bd'
DEPLOY_UID = '41564f26-95e7-4ad1-8af3-d73b93c4669a'
SIG = 'SECRET-SIGNATURE-NEVER-PRINT'
HOST_TOKENS = ['/etc/anxious-lookout/issue16/controller/kube-token', '/etc/anxious-lookout/issue16/observer/kube-token']
JTI1 = '11111111-2222-4333-8444-555555555555'
JTI2 = '66666666-7777-4888-9999-000000000000'
passed = failed = 0


def check(name, condition, detail=''):
    global passed, failed
    if condition:
        passed += 1
        print('ok   ' + name)
    else:
        failed += 1
        print('FAIL ' + name + (' (' + detail[-1500:] + ')' if detail else ''))


def claims(jti, iat, extra=None):
    value = {'aud': ['https://kubernetes.default.svc.cluster.local', 'k3s'], 'exp': iat + 31536000, 'iat': iat,
             'nbf': iat, 'jti': jti, 'iss': 'https://kubernetes.default.svc.cluster.local',
             'sub': 'system:serviceaccount:runner-control:runner-controller',
             'kubernetes.io': {'namespace': 'runner-control', 'node': {'name': 'devvm', 'uid': 'n'},
                               'pod': {'name': 'runner-controller-ddc7d7dc9-bd6zs', 'uid': POD_UID},
                               'serviceaccount': {'name': 'runner-controller', 'uid': 's'}, 'warnafter': iat + 3607}}
    value.update(extra or {})
    return value


def pod(uid=POD_UID, restarts=0, cid='containerd://' + 'a' * 64, volumes=None, sa='runner-controller', mount_ro=True,
        automount=None, containers=None, ready=True):
    access = {'name': 'kube-api-access-vff4b', 'projected': {'defaultMode': 420, 'sources': [
        {'serviceAccountToken': {'expirationSeconds': 3607, 'path': 'token'}},
        {'configMap': {'name': 'kube-root-ca.crt', 'items': [{'key': 'ca.crt', 'path': 'ca.crt'}]}},
        {'downwardAPI': {'items': [{'fieldRef': {'apiVersion': 'v1', 'fieldPath': 'metadata.namespace'}, 'path': 'namespace'}]}}]}}
    spec = {'serviceAccountName': sa,
            'volumes': volumes if volumes is not None else [
                {'name': 'db', 'secret': {'secretName': 'runner-controller-db', 'defaultMode': 288}},
                {'name': 'tmp', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '16Mi'}}, access],
            'containers': containers or [{'name': 'controller', 'volumeMounts': [
                {'name': 'db', 'mountPath': '/run/runner-db', 'readOnly': True}, {'name': 'tmp', 'mountPath': '/tmp'},
                {'name': 'kube-api-access-vff4b', 'mountPath': '/var/run/secrets/kubernetes.io/serviceaccount', 'readOnly': mount_ro}]}]}
    if automount is not None:
        spec['automountServiceAccountToken'] = automount
    return {'metadata': {'name': 'runner-controller-ddc7d7dc9-bd6zs', 'uid': uid}, 'spec': spec,
            'status': {'phase': 'Running', 'containerStatuses': [
                {'name': 'controller', 'ready': ready, 'restartCount': restarts, 'containerID': cid,
                 'state': {'running': {'startedAt': '2026-11-03T15:49:57Z'}}}]}}


def deploy(uid=DEPLOY_UID, ready=1):
    return {'metadata': {'uid': uid}, 'spec': {'replicas': 1}, 'status': {'readyReplicas': ready}}


def setup(tokens=None, pods=None, proof=None, proof_jti=None, logs='', fail=(), units=None, host_age_hours=(240, 240),
          deploy_obj=None):
    shutil.rmtree(EVIDENCE, ignore_errors=True)
    shutil.rmtree('/var/run/secrets', ignore_errors=True)
    os.makedirs('/var/run/secrets/kubernetes.io/serviceaccount')
    for path, age in zip(HOST_TOKENS, host_age_hours):
        if os.path.lexists(path):
            os.remove(path)
        if age is None:
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as stream:
            stream.write('HOSTTOKEN-NEVER-READ')
        os.chmod(path, 0)  # unreadable even for this harness's assertion: the script must only lstat it
        then = time.time() - age * 3600
        os.utime(path, (then, then))
    now = int(time.time())
    state = {'tokens': tokens if tokens is not None else [claims(JTI1, now - 2000), claims(JTI1, now - 2000),
                                                          claims(JTI2, now - 5)],
             'pods': pods or [[pod()]], 'deploy': deploy_obj or deploy(),
             'proof': proof if proof is not None else {'result': 'ok', 'namespace': 'ok', 'pod': None, 'node': 'devvm'},
             'logs': logs, 'fail': list(fail), 'units': units or {}}
    if proof_jti is not None:
        state['proofJti'] = proof_jti
    with open('/mock/w5.json', 'w') as stream:
        json.dump(state, stream)
    if os.path.exists('/mock/w5-calls.log'):
        os.remove('/mock/w5-calls.log')


def run(wait=20):
    env = dict(os.environ, W5_WAIT_SECONDS=str(wait), W5_POLL_SECONDS='1')
    done = subprocess.run(['python3', '-I', INSTALL + '/w5-rotation.py'], stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, timeout=300, env=env)
    return done.returncode, done.stdout.decode()


def record():
    dirs = [d for d in os.listdir(EVIDENCE) if d.startswith('w5-')] if os.path.isdir(EVIDENCE) else []
    if len(dirs) != 1:
        return None
    path = os.path.join(EVIDENCE, dirs[0], 'run.json')
    return json.load(open(path)) if os.path.exists(path) else None


def calls():
    if not os.path.exists('/mock/w5-calls.log'):
        return []
    return [json.loads(l) for l in open('/mock/w5-calls.log')]


def main():
    os.makedirs(INSTALL, exist_ok=True)
    os.makedirs('/mock', exist_ok=True)
    shutil.copy(SRC + '/w5-rotation.py', INSTALL + '/w5-rotation.py')
    for path, name in (('/usr/local/bin/k3s', 'k3s'), ('/usr/bin/systemctl', 'systemctl')):
        with open(path, 'w') as stream:
            stream.write('#!/bin/sh\nFAKE_NAME=%s exec python3 -I %s/mock/fake_w5.py "$@"\n' % (name, SRC))
        os.chmod(path, 0o755)
    source = open(INSTALL + '/w5-rotation.py').read()

    verbs = re.findall(r"'-n', NS, '([a-z]+)'", source)
    check('static: kubectl verbs are get/exec/logs only', verbs and set(verbs) <= {'get', 'exec', 'logs'}, str(verbs))
    check('static: host tokens only lstat-ed', 'os.lstat(path)' in source and 'open(path' not in source)
    check('static: the reader decodes the payload only', 'parts[1]' in source and 'parts[2]' not in source
          and 'split(b".")[1]' in source)

    # Happy path.
    setup()
    rc, out = run()
    r = record()
    check('happy: rc 0', rc == 0, out)
    check('happy: no secret, no host token', SIG not in out and 'HOSTTOKEN' not in out and 'eyJ' not in out
          and SIG not in json.dumps(r), out)
    check('happy: rotation reported with both jti', ('jti %s -> %s' % (JTI1, JTI2)) in out, out)
    check('happy: record', r and r['result'] == 0 and len(r['series']) == 3 and r['proof']['result'] == 'ok'
          and r['series'][0]['claims']['jti'] == JTI1 and r['series'][-1]['claims']['jti'] == JTI2
          and r['resident'] == {'outcomeLines': 0, 'reconciliationRequired': 0}, json.dumps(r))
    check('happy: record keys are the claim allow-list only',
          r and set(r['series'][0]['claims']) == {'aud', 'dataLink', 'exp', 'iat', 'jti', 'namespace', 'nbf', 'node',
                                                   'pod', 'serviceaccount', 'sub', 'warnafter'}, json.dumps(r))
    check('happy: dataLink recorded', r and re.fullmatch(r'\.\.2026_11_04_02_\d\d_00\.\d+', r['series'][-1]['claims']['dataLink'] or ''), json.dumps(r))
    check('happy: host tokens reported with age', '"ageHours": 240.0' in out, out)
    check('happy: evidence file 0600 in a 0700 dir', r is not None and oct(os.stat(os.path.join(EVIDENCE, os.listdir(EVIDENCE)[0])).st_mode & 0o777) == '0o700'
          and oct(os.stat(os.path.join(EVIDENCE, os.listdir(EVIDENCE)[0], 'run.json')).st_mode & 0o777) == '0o600')
    kinds = [c[5] if c[0] == 'k3s' and len(c) > 5 and c[4] == 'get' else c[4] if c[0] == 'k3s' and len(c) > 4 else c[0]
             for c in calls()]
    check('happy: calls are lookups, reads, one proof, logs',
          set(kinds) <= {'systemctl', 'deployment', 'pods', 'exec', 'logs'} and kinds.count('logs') == 1
          and sum(1 for c in calls() if '-B' in c) == 1 and kinds.count('deployment') == 1, json.dumps(kinds))

    # One-shot: earlier evidence.
    os.makedirs(EVIDENCE + '/w5-20260101T000000Z', exist_ok=True)
    with open('/mock/w5-calls.log', 'w'):
        pass
    rc, out = run()
    check('one-shot: rc 3 with earlier w5 evidence', rc == 3 and 'one-shot' in out and len(calls()) == 0, out)

    # Precheck refusals.
    for name, kwargs, word in (
            ('unit active', {'units': {'issue16-observer.timer': {'active': 'active'}}}, 'issue16-observer.timer'),
            ('fresh host token', {'host_age_hours': (240, 3)}, 'younger than 24 h'),
            ('deployment uid', {'deploy_obj': deploy(uid='00000000-0000-0000-0000-000000000000')}, 'Deployment UID'),
            ('deployment not ready', {'deploy_obj': deploy(ready=0)}, 'not 1/1'),
            ('two pods', {'pods': [[pod(), pod()]]}, 'expected one'),
            ('pod uid', {'pods': [[pod(uid='00000000-0000-0000-0000-000000000001')]]}, 'Pod UID'),
            ('not ready', {'pods': [[pod(ready=False)]]}, 'not running and ready'),
            ('sa', {'pods': [[pod(sa='default')]]}, 'serviceAccountName'),
            ('automount off', {'pods': [[pod(automount=False)]]}, 'automount'),
            ('hostPath', {'pods': [[pod(volumes=[{'name': 'db', 'secret': {}}, {'name': 'tmp', 'emptyDir': {}},
                                                 {'name': 'host', 'hostPath': {'path': '/etc/anxious-lookout'}},
                                                 pod()['spec']['volumes'][2]])]]}, 'hostPath'),
            ('extra token volume', {'pods': [[pod(volumes=pod()['spec']['volumes'] + [{'name': 'runner-kube', 'projected': {
                'sources': [{'serviceAccountToken': {'path': 'token', 'expirationSeconds': 86400}}]}}])]]}, 'token volumes'),
            ('mount not read-only', {'pods': [[pod(mount_ro=False)]]}, 'token mount')):
        setup(**kwargs)
        rc, out = run()
        check('precheck %s: rc 3, no exec' % name, rc == 3 and word in out and not any('exec' in c for c in calls()), out)
    setup(host_age_hours=(None, None))
    rc, out = run()
    check('host tokens absent: allowed', rc == 0 and '"/etc/anxious-lookout/issue16/controller/kube-token": null' in out, out)

    # Not rotated within the bound.
    now = int(time.time())
    setup(tokens=[claims(JTI1, now - 100)])
    rc, out = run(wait=3)
    r = record()
    check('no rotation: rc 5, no proof', rc == 5 and 'not rotated within 3 s' in out and r and 'proof' not in r
          and not any('-B' in c for c in calls()), out)

    # Claims problems -> rc 7, values never echoed.
    for name, token, word in (
            ('garbage token', 'garbage', 'token unreadable'),
            ('wrong sub', claims(JTI1, now, {'sub': 'system:serviceaccount:kube-system:admin'}), 'identity'),
            ('wrong pod', claims(JTI1, now, {'kubernetes.io': dict(claims(JTI1, now)['kubernetes.io'], pod={'uid': 'x'})}), 'pod'),
            ('jti not uuid', claims('evil value with spaces', now), 'jti'),
            ('aud with whitespace', claims(JTI1, now, {'aud': ['a b']}), 'aud'),
            ('iat as string', claims(JTI1, now, {'iat': str(now)}), 'iat')):
        setup(tokens=[token])
        rc, out = run()
        check('claims %s: rc 7' % name, rc == 7 and word in out and 'evil value' not in out and SIG not in out, out)
    setup(tokens=[claims(JTI1, now), claims(JTI2, now - 10)])
    rc, out = run()
    check('rotation that does not advance iat: rc 7', rc == 7 and 'do not advance' in out, out)
    setup(fail=['reader'])
    rc, out = run()
    check('reader exec failure: rc 7', rc == 7 and 'claims read failed' in out, out)

    # Proof outcomes.
    setup(proof={'result': 'failed', 'failedStep': 'namespace', 'error': 'Refused'})
    rc, out = run()
    check('proof call failed: rc 4 names the step', rc == 4 and 'failed at namespace (Refused)' in out, out)
    setup(proof_jti=[JTI2, '99999999-9999-4999-8999-999999999999'])
    rc, out = run()
    check('rotated again during the proof: rc 6, no retry', rc == 6 and 'no retry' in out
          and sum(1 for c in calls() if '-B' in c) == 1, out)
    setup(proof_jti=[JTI1, JTI1])
    rc, out = run()
    check('proof ran with the old token: rc 6', rc == 6, out)
    setup(fail=['proof'])
    rc, out = run()
    check('proof process failed: rc 4', rc == 4 and 'proof process failed' in out, out)
    setup(proof={'result': 'failed', 'failedStep': 'pod', 'error': 'x; rm -rf /'})
    rc, out = run()
    check('proof error text sanitized', rc == 4 and 'rm -rf' not in out and '(unnamed)' in out, out)

    # Resident controller.
    setup(logs='{"owner": "o", "stage": "controller", "result": "running"}\n'
               '{"owner": "o", "stage": "controller", "result": "reconciliation_required"}\n')
    rc, out = run()
    check('resident reconciliation_required: rc 4', rc == 4 and '1 reconciliation_required' in out, out)
    setup(pods=[[pod()], [pod()], [pod(restarts=1)]])
    rc, out = run()
    check('controller restarted during the window: rc 8', rc == 8, out)
    setup(pods=[[pod()], [pod(cid='containerd://' + 'b' * 64)]])
    rc, out = run()
    check('controller container replaced: rc 8', rc == 8, out)
    setup(fail=['logs'])
    rc, out = run()
    check('resident log unreadable: rc 4', rc == 4 and 'log unreadable' in out, out)

    # A dropped ssh session (SIGHUP) mid-wait: rc 130, the record is still written.
    setup(tokens=[claims(JTI1, int(time.time()) - 100)])
    env = dict(os.environ, W5_WAIT_SECONDS='60', W5_POLL_SECONDS='1')
    proc = subprocess.Popen(['python3', '-I', INSTALL + '/w5-rotation.py'], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, env=env)
    time.sleep(4)
    proc.send_signal(1)
    out = proc.communicate(timeout=60)[0].decode()
    r = record()
    check('SIGHUP: rc 130 and run.json written', proc.returncode == 130 and r and r['result'] == 130
          and len(r['series']) >= 1, out)

    # Mutations the harness must catch.
    def mutated(old, new, label, kwargs, expect):
        assert old in source, old
        with open(INSTALL + '/w5-rotation.py', 'w') as stream:
            stream.write(source.replace(old, new, 1))
        setup(**kwargs)
        got, out = run()
        check('mutation %s caught' % label, expect(got, out), out)
        shutil.copy(SRC + '/w5-rotation.py', INSTALL + '/w5-rotation.py')

    mutated("if result['jtiBefore'] != current['jti'] or", "if False and (result['jtiBefore'] != current['jti']) or False and",
            'proof-jti check removed', {'proof_jti': [JTI1, JTI1]}, lambda got, out: got == 0)
    mutated("        if age < 24 * 3600:", "        if False:", 'host-token age gate removed',
            {'host_age_hours': (240, 3)}, lambda got, out: got == 0)
    mutated('"sub": claims.get("sub"),', '"sub": claims.get("sub"), "sig": parts[2].decode(),', 'reader leaks the signature',
            {}, lambda got, out: got == 7 and SIG not in out)

    print('%d passed, %d failed' % (passed, failed))
    sys.exit(1 if failed else 0)


main()
