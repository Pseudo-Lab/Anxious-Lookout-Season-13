"""Mock harness for the #61 W6 scripts (w6-enter.py, w6-reboot.py, w6-observe.py). Docker only, never on a node:
    docker run --rm --network none --hostname simple -v "$PWD/infra/hosting/m3:/m3:ro" python:3.9-alpine \
      python3 -I /m3/multi-user-runners/issue61/test_w6.py

mock/fake_w6.py stands in for k3s (kubectl/crictl), losetup, systemctl, journalctl and docker. The API POST is
served here (session row, Origin, CSRF, Idempotency-Key checked as in W4). A sitecustomize answers os.path.ismount
and os.statvfs for the listed storage targets only (no real loop mounts in a container) and can make os.fsync fail
for a named file. The installed scripts are the branch files with only these constants pointed at the mock:
API_HOST/API_PORT, BOOT_ID, MOUNTINFO, PROC, and the PINNED hash values (the harness's own files).
"""
import copy
import hashlib
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid

SRC = '/m3/multi-user-runners/issue61'
INSTALL = '/root/issue61'
EVID = INSTALL + '/evidence'
OWNER = '00000000-0000-4000-8000-000000005804'
STATE = '5ff583e9-abe1-4ef8-b277-4a5afead6b80'
STATE_HEX = STATE.replace('-', '')
POD_NAME = 'runner-' + OWNER.replace('-', '')
RUNNER = 'docker.io/library/anxious-dev-runner@sha256:067c77de5d6b3673ece9b243f7753a9184a007cd44ab0ea1c93dc3044458a5e4'
EXECUTOR = 'docker.io/library/anxious-dev-shell-executor@sha256:9c87d626cdf6798db5bc5a562cc68af1b13a36c8f90d3738e19c6553e7cf4d12'
RUNNER_ID = 'sha256:4cac37e2173d5aec7b351e05c2977f2e0df76670f3befb91ce54306d2444e8ad'
EXECUTOR_ID = 'sha256:49b449cb31c5b344f876a9d9aee2d97dd3dcae906c3324bc0eaf00678dd6b883'
POD5 = '7c0d2b8e-5555-4222-8333-444455556666'
POD6 = '7c0d2b8e-6666-4222-8333-444455556666'
B0 = 'dea6d273-2127-4d43-af0c-e94cb6da2c85'
B1 = 'b1b1b1b1-2127-4d43-af0c-e94cb6da2c85'
NODE_UID = '3a59eb5e-0800-4445-af68-9ae0bf4ed457'
ROOT = '/var/lib/anxious-lookout/issue16-d4/provision-output'
STORAGE = '/var/lib/anxious-lookout/user-runners'
OWNERS = {
    '00000000000040008000000000000048': '34823dfb6b504b8eb230191739bdc57d',
    '00000000000040008000000000005801': 'eb766aef80164439b2175ae6b9927a4c',
    '00000000000040008000000000005802': 'cfd1fedcdd6b40898df2c1070a6146e1',
    '00000000000040008000000000005803': '0383c764bea948aa836805220946e4c9',
    '00000000000040008000000000005804': STATE_HEX,
}
GIB = 1024 ** 3
SIZES = {'native': 5 * GIB, 'control': 1 * GIB}
PINNED = {
    '/usr/local/lib/anxious-lookout/issue16/restore.sh': '3e519a67f11297d5911d72b06db1dd14b430be5c7b342a4d8ca129fa255b4c51',
    '/usr/local/lib/anxious-lookout/issue16/storage-root.sh': '2528d9284779886a0ac26a574baf3240d6b5742209765ba05899691522c429d5',
    '/usr/local/lib/anxious-lookout/issue16/worker.py': '77244e29f5ecb7f0ccdfa53c0bf62bf8fce3b21b86371fb58964944db4ce8afe',
    '/usr/local/lib/anxious-lookout/issue16/stop-restore.sh': 'ef15935ca6326d4a02bb4951e2b0a1fd047dac91cd05f8655deb3f630eeb49e7',
    '/etc/systemd/system/issue16-restore.service': '98d2e2a9eb060e8c4552e5ab8f9d6850bf36dc9366cd0b8c693ec4662a2c119a',
    '/etc/systemd/system/issue16-storage-root.service': '760d70691d9835b6c4d66871238ec060dc16f19be6cd493548641a9587056b8f',
    '/etc/systemd/system/k3s.service.d/issue16.conf': '35bb3a30b5d0d7265d8b853048bfb39b67121341fc14ffa17195254828688d00',
    '/etc/systemd/system/k3s.service': 'adb161a79a851f710cb20acac0721e02c65b67c815fa86f6075a3be7b5997d96',
}
PASS_LINE = 'PASS bounded restore inventory; no formatting'
SUDO_SECRET = 'SECRET-ARG-NEVER-PRINT'
PLANS = json.load(open(SRC + '/mock/w3b-plans.json'))
PORT = 18086
passed = failed = 0
POSTS = []


def check(name, condition, detail=''):
    global passed, failed
    if condition:
        passed += 1
        print('ok   ' + name)
    else:
        failed += 1
        print('FAIL ' + name + (' (' + str(detail)[-1800:] + ')' if detail else ''))


# Fixtures ---------------------------------------------------------------------------------------------
def moved_plan():
    plan = copy.deepcopy(PLANS[OWNER])
    plan['image'] = RUNNER
    plan['executor'] = dict(plan['executor'], image=EXECUTOR)
    return plan


def pod_template():
    return {
        'metadata': {'name': POD_NAME, 'namespace': 'codex-runner', 'uid': 'set-by-fake',
                     'labels': {'anxious.io/owner': OWNER, 'anxious.io/state': STATE, 'anxious.io/generation': '0'}},
        'spec': {'automountServiceAccountToken': False,
                 'initContainers': [{'name': 'prepare-shell', 'image': RUNNER}],
                 'containers': [
                     {'name': 'runner', 'image': RUNNER, 'volumeMounts': [
                         {'name': 'native', 'mountPath': '/state'}, {'name': 'control', 'mountPath': '/control'},
                         {'name': 'broker-token', 'mountPath': '/run/codex-broker', 'readOnly': True}]},
                     {'name': 'shell-executor', 'image': EXECUTOR, 'volumeMounts': [
                         {'name': 'native', 'mountPath': '/workspace', 'subPath': 'shell-work'}]}],
                 'volumes': [{'name': 'native', 'persistentVolumeClaim': {'claimName': 'native-' + STATE_HEX}},
                             {'name': 'control', 'persistentVolumeClaim': {'claimName': 'control-' + STATE_HEX}},
                             {'name': 'broker-token', 'projected': {'sources': [
                                 {'serviceAccountToken': {'audience': 'codex-broker', 'expirationSeconds': 600,
                                                          'path': 'token'}}]}}]},
        'status': {'phase': 'Running', 'startTime': 'set-by-fake',
                   'initContainerStatuses': [{'name': 'prepare-shell', 'imageID': RUNNER,
                                              'state': {'terminated': {'exitCode': 0}}}],
                   'containerStatuses': [
                       {'name': n, 'imageID': ref, 'containerID': 'set', 'restartCount': 0,
                        'state': {'running': {'startedAt': '2026-11-04T06:00:00Z'}}}
                       for n, ref in (('runner', RUNNER), ('shell-executor', EXECUTOR))]}}


VOL = {'native': ('4b122f91-b409-4d6e-b049-cde699620897', '02a00d97-063a-416f-91fe-364a43ec4204', '5Gi'),
       'control': ('2a7c0559-c161-4ea6-99c7-103d2e6d51f5', '2e72dd83-832a-4300-8107-062f92b04266', '1Gi')}


def pvcs():
    pvc, pv = {}, {}
    for kind, (pvc_uid, pv_uid, cap) in VOL.items():
        name = '%s-%s' % (kind, STATE_HEX)
        pvc[name] = {'metadata': {'uid': pvc_uid}, 'spec': {'volumeName': name},
                     'status': {'phase': 'Bound', 'capacity': {'storage': cap}}}
        pv[name] = {'metadata': {'uid': pv_uid}, 'status': {'phase': 'Bound'},
                    'spec': {'capacity': {'storage': cap}, 'claimRef': {'uid': pvc_uid},
                             'local': {'path': '%s/%s/%s/data' % (STORAGE, STATE_HEX, kind)}}}
    return pvc, pv


def other_pod(ns, name):
    return {'metadata': {'namespace': ns, 'name': name, 'uid': str(uuid.uuid4())}, 'status': {'phase': 'Running'}}


def base_state():
    pvc, pv = pvcs()
    db = {'state': STATE, 'phase': 'idle_stopped', 'generation': 4, 'revision': 16, 'pod': None, 'active': 0,
          'ambiguous': False, 'allOpenStops': 0, 'stops': 4, 'running': 0, 'conv': {'n': 0, 'digest': '-'},
          'stop5': None,
          'pre': {'bindingState': STATE, 'plan': moved_plan(), 'role': 'editor', 'approved': True, 'allOpenStops': 0,
                  'entries': 0, 'admissions': 0, 'outbox': 0, 'waiting': 0, 'liveSessions': 0}}
    return {
        'workloads': {
            'deployment/runner-control/runner-controller': {'uid': '41564f26-95e7-4ad1-8af3-d73b93c4669a', 'replicas': 1, 'ready': 1},
            'deployment/m2-hosting/api': {'uid': 'fad6799a-6df5-4086-812f-cfae6212adfa', 'replicas': 1, 'ready': 1},
            'deployment/m2-hosting/web': {'uid': 'd35eafcc-6d75-43c8-b093-fee29ec9b6d4', 'replicas': 1, 'ready': 1},
            'statefulset/m2-hosting/postgres': {'uid': 'a5ce2d32-8688-4d9a-a003-66c71982681b', 'replicas': 1, 'ready': 1},
            'statefulset/codex-broker/codex-broker': {'uid': 'a4016104-57c6-4dc1-b3d5-b271ba029958', 'replicas': 1, 'ready': 1}},
        'db': db, 'sessions': {}, 'fail': [], 'postMode': '202', 'podAfter': 2, 'podTemplate': pod_template(),
        'podUids': {'5': POD5, '6': POD6}, 'cids': {'5': {'runner': '5' * 64, 'shell-executor': '6' * 64},
                                                     '6': {'runner': '7' * 64, 'shell-executor': '8' * 64}},
        'sandbox': {'5': 'a' * 64, '6': 'b' * 64}, 'cri': {}, 'ps': {}, 'sandboxes': {},
        'images': {RUNNER: RUNNER_ID, EXECUTOR: EXECUTOR_ID},
        'health': {'ownerId': OWNER, 'stateId': STATE, 'podUid': 'CURRENT', 'available': False, 'reason': 'auth_revoked',
                   'verification': 'unverified', 'failure': 'auth_revoked', 'model': 'gpt-6.1-sol'},
        'pvc': pvc, 'pv': pv,
        'node': {'metadata': {'uid': NODE_UID, 'labels': {'kubernetes.io/hostname': 'anxious-lookout'},
                              'annotations': {'k3s.io/hostname': 'simple'}},
                 'status': {'nodeInfo': {'bootID': B0}, 'conditions': [{'type': 'Ready', 'status': 'True'}]}},
        'namespaces': ['codex-runner', 'kube-system', 'm2-hosting', 'shell-full', 'shell-pilot'],
        'otherPods': [other_pod('m2-hosting', 'api-x'), other_pod('kube-system', 'coredns-x')],
        'controllerPod': {'metadata': {'name': 'runner-controller-x', 'uid': 'e66fa6fa-3256-4dcb-a03b-2bc5923345bd'},
                          'status': {'containerStatuses': [{'name': 'controller', 'containerID': 'containerd://' + 'd' * 64,
                                                            'restartCount': 0}]}},
        'units': {
            'k3s.service': {'ActiveState': 'active', 'SubState': 'running', 'UnitFileState': 'enabled'},
            'docker.service': {'ActiveState': 'active', 'SubState': 'running', 'UnitFileState': 'enabled'},
            'issue16-restore.service': {'ActiveState': 'active', 'SubState': 'exited', 'Result': 'success',
                                        'ExecMainStatus': '0', 'ExecMainExitTimestampMonotonic': '9000000'},
            'issue16-storage-root.service': {'ActiveState': 'active', 'SubState': 'exited', 'Result': 'success',
                                             'ExecMainStatus': '0', 'ExecMainExitTimestampMonotonic': '8000000'},
            'issue16-controller.service': {'ActiveState': 'inactive'}, 'issue16-controller.timer': {'ActiveState': 'inactive'},
            'issue16-observer.service': {'ActiveState': 'failed'}, 'issue16-observer.timer': {'ActiveState': 'inactive'},
            'issue16-supply.service': {'ActiveState': 'failed'}, 'issue16-supply.timer': {'ActiveState': 'inactive'},
            'issue16-restore.timer': {'ActiveState': 'inactive'}},
        'journal': {'restore': [{'MESSAGE': PASS_LINE, '_BOOT_ID': B0.replace('-', '')}], 'sudo': [], 'sshd': []},
        'bootHex': B0.replace('-', ''), 'restoreImage': True, 'k3sJournalBytes': 4000,
        'loops': [], 'controllerLogs': [],
    }


def state():
    return json.load(open('/mock/w6.json'))


def put(st):
    with open('/mock/w6.json.tmp', 'w') as stream:
        json.dump(st, stream)
    os.replace('/mock/w6.json.tmp', '/mock/w6.json')


def edit(fn):
    st = state()
    fn(st)
    put(st)


MOUNTS = {}


def put_mounts():
    json.dump(MOUNTS, open('/mock/mounts.json', 'w'))


def build_fs(st):
    """Provision outputs, state dirs with sparse backing files, mountinfo, losetup view, ismount/statvfs answers."""
    for path in (ROOT, STORAGE):
        shutil.rmtree(path, ignore_errors=True)
        os.makedirs(path, mode=0o700)
        os.chmod(path, 0o700)
    lines = ['92 70 252:0 %s %s rw,relatime shared:1 - xfs /dev/mapper/root rw' % (STORAGE, STORAGE)]
    loops, ismount, statvfs = [], [], {}
    n = 0
    for owner_hex, state_hex in OWNERS.items():
        out = os.path.join(ROOT, owner_hex)
        os.mkdir(out, 0o700)
        os.chown(out, 10001, 10001)
        open(os.path.join(ROOT, '.lock-' + owner_hex), 'w').close()
        owner = str(uuid.UUID(hex=owner_hex))
        st_uuid = str(uuid.UUID(hex=state_hex))
        manifest = os.path.join(out, 'storage-restore.json')
        with open(manifest, 'w') as stream:
            json.dump({'version': 1, 'owner': owner, 'state': st_uuid, 'path': os.path.join(STORAGE, state_hex),
                       'layout': 'bounded-ext4'}, stream)
        os.chmod(manifest, 0o600)
        runtime = os.path.join(out, 'runtime.json')
        with open(runtime, 'w') as stream:
            json.dump({'plan': {'owner': owner, 'state': st_uuid}}, stream)
        os.chmod(runtime, 0o600)
        os.chown(runtime, 10001, 10001)
        token = os.path.join(out, 'transport-token')
        with open(token, 'w') as stream:
            stream.write('TRANSPORT-TOKEN-NEVER-READ')
        os.chmod(token, 0o600)
        os.chown(token, 10001, 10001)
        sdir = os.path.join(STORAGE, state_hex)
        os.mkdir(sdir, 0o700)
        for kind, size in SIZES.items():
            backing = os.path.join(sdir, kind + '.ext4')
            fd = os.open(backing, os.O_WRONLY | os.O_CREAT, 0o600)
            os.ftruncate(fd, size)
            os.close(fd)
            target = os.path.join(sdir, kind)
            os.mkdir(target, 0o755)
            info = os.stat(backing)
            majmin = '7:%d' % n
            lines.append('%d 70 %s / %s rw,nosuid,nodev,relatime shared:%d - ext4 /dev/loop%d rw' % (900 + n, majmin, target, 400 + n, n))
            lines.append('%d 92 %s / %s rw,nosuid,nodev,relatime shared:%d - ext4 /dev/loop%d rw' % (1900 + n, majmin, target, 400 + n, n))
            loops.append({'name': '/dev/loop%d' % n, 'maj:min': '  %s  ' % majmin, 'back-ino': info.st_ino,
                          'back-maj:min': '   %d:%d  ' % (os.major(info.st_dev), os.minor(info.st_dev)),
                          'back-file': '/%s/%s.ext4' % (state_hex, kind)})
            ismount.append(target)
            statvfs[target] = [4096, size // 4096 - 2000, 1000]
            n += 1
    open('/mock/mountinfo', 'w').write('\n'.join(lines) + '\n')
    st['loops'] = loops
    MOUNTS.clear()
    MOUNTS.update({'ismount': ismount, 'statvfs': statvfs, 'fsyncFail': []})
    put_mounts()


def build_host():
    for path in PINNED:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as stream:
            stream.write('harness copy of ' + path + '\n')
    os.makedirs('/var/lib/rancher/k3s/agent/containerd', exist_ok=True)
    with open('/var/lib/rancher/k3s/agent/containerd/containerd.log', 'w') as stream:
        stream.write('containerd line\n' * 500)
    shutil.rmtree('/mock/proc', ignore_errors=True)
    for proc in ('1', 'self'):
        os.makedirs('/mock/proc/%s/ns' % proc)
        for kind, ino in (('pid', 4026531836), ('cgroup', 4026531835), ('mnt', 4026531841)):
            os.symlink('%s:[%d]' % (kind, ino), '/mock/proc/%s/ns/%s' % (proc, kind))
    open('/mock/proc/1/cmdline', 'wb').write(b'/sbin/init\0')
    open('/mock/boot_id', 'w').write(B0 + '\n')


SITE = r'''
import json, os
_PATH = '/mock/mounts.json'
_ismount, _statvfs, _fsync = os.path.ismount, os.statvfs, os.fsync
def _cfg():
    try:
        with open(_PATH) as s:
            return json.load(s)
    except Exception:
        return None
class _V:
    def __init__(self, v):
        self.f_frsize, self.f_bsize, self.f_blocks, self.f_bavail = v[0], v[0], v[1], v[2]
def ismount(path):
    c = _cfg()
    p = os.fspath(path)
    if c is not None and p.startswith('/var/lib/anxious-lookout/'):
        return p in c.get('ismount', [])
    return _ismount(path)
def statvfs(path):
    c = _cfg()
    p = os.fspath(path)
    if c is not None and p in c.get('statvfs', {}):
        return _V(c['statvfs'][p])
    return _statvfs(path)
def fsync(fd):
    c = _cfg()
    if c is not None and c.get('fsyncFail'):
        try:
            name = os.readlink('/proc/self/fd/%d' % fd)
        except OSError:
            name = ''
        if any(n in name for n in c['fsyncFail']):
            raise OSError(28, 'No space left on device')
    return _fsync(fd)
os.path.ismount, os.statvfs, os.fsync = ismount, statvfs, fsync
'''


class Api(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('content-length', 0)))
        POSTS.append((dict(self.headers), body))
        st = state()
        mode = st.get('postMode')
        cookie = self.headers.get('cookie', '')
        token = cookie[len('anxious_session='):] if cookie.startswith('anxious_session=') else ''
        token_hash = hashlib.sha256(('m2-origin-session-v1\0http://192.88.99.1\0' + token).encode()).hexdigest()
        row = st['sessions'].get(token_hash)
        if row is None or row['owner'] != OWNER or self.headers.get('origin') != 'http://192.88.99.1' \
                or row['csrf'] != self.headers.get('x-csrf-token'):
            self.reply(403, {'error': {'code': 'forbidden'}})
            return
        if mode == '403':
            self.reply(403, {'error': {'code': 'forbidden'}})
            return
        st['entered'] = True
        st['db'].update(phase='provisioning', generation=st['db']['generation'] + 1, revision=st['db']['revision'] + 1)
        put(st)
        if mode == 'lost':
            self.connection.close()
            return
        self.reply(202, {'available': False, 'reason': 'provisioning', 'retryAfterSeconds': 2})

    def reply(self, status, value):
        data = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('content-type', 'application/json')
        self.send_header('content-length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def install_tools():
    for tool, path in (('k3s', '/usr/local/bin/k3s'), ('losetup', '/usr/sbin/losetup'), ('systemctl', '/usr/bin/systemctl'),
                       ('journalctl', '/usr/bin/journalctl'), ('docker', '/usr/bin/docker')):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.lexists(path):
            os.unlink(path)
        with open(path, 'w') as stream:
            stream.write('#!/bin/sh\nFAKE_NAME=%s exec python3 -I %s/mock/fake_w6.py "$@"\n' % (tool, SRC))
        os.chmod(path, 0o755)
    site = [p for p in sys.path if p.endswith('site-packages')][0]
    open(os.path.join(site, 'sitecustomize.py'), 'w').write(SITE)
    server = http.server.ThreadingHTTPServer(('127.0.0.1', PORT), Api)
    threading.Thread(target=server.serve_forever, daemon=True).start()


def install_scripts():
    shutil.rmtree(INSTALL, ignore_errors=True)
    os.makedirs(INSTALL, mode=0o700)
    common = (("BOOT_ID = '/proc/sys/kernel/random/boot_id'\n", "BOOT_ID = '/mock/boot_id'\n"),
              ("MOUNTINFO = '/proc/self/mountinfo'\n", "MOUNTINFO = '/mock/mountinfo'\n"))
    for name, extra in (('w6-enter.py', (("API_HOST = '10.0.0.107'\n", "API_HOST = '127.0.0.1'\n"),
                                         ('API_PORT = 80\n', 'API_PORT = %d\n' % PORT))),
                        ('w6-reboot.py', (("PROC = '/proc'\n", "PROC = '/mock/proc'\n"),)),
                        ('w6-observe.py', ())):
        text = open(os.path.join(SRC, name)).read()
        pointed = text
        for old, new in common + extra:
            assert pointed.count(old) == 1, (name, old)
            pointed = pointed.replace(old, new)
        if name == 'w6-reboot.py':
            for path, digest in PINNED.items():
                assert pointed.count(digest) == 1, digest
                pointed = pointed.replace(digest, hashlib.sha256(open(path, 'rb').read()).hexdigest())
        assert len(pointed.splitlines()) == len(text.splitlines())
        open(os.path.join(INSTALL, name), 'w').write(pointed)


def fresh(mutate=None):
    shutil.rmtree('/mock', ignore_errors=True)
    os.makedirs('/mock', mode=0o700)
    build_host()
    install_scripts()
    st = base_state()
    build_fs(st)
    if mutate:
        mutate(st)
    put(st)
    del POSTS[:]


ENV = {'W6_POD_WAIT_SECONDS': '4', 'W6_POST_TIMEOUT': '3', 'W6_HEALTH_WAIT_SECONDS': '2', 'W6_HEALTH_POLL_SECONDS': '0.2',
       'W6_OBSERVE_WAIT_SECONDS': '6', 'W6_OBSERVE_POLL_SECONDS': '0.2'}


def run(script, env=None):
    environ = dict(os.environ, **ENV)
    environ.update(env or {})
    done = subprocess.run(['python3', '-I', os.path.join(INSTALL, script)], stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, timeout=300, env=environ)
    return done.returncode, done.stdout.decode()


def dirs(kind):
    if not os.path.isdir(EVID):
        return []
    return sorted(os.path.join(EVID, d) for d in os.listdir(EVID) if d.startswith('w6-%s-' % kind))


def rj(kind):
    found = dirs(kind)
    return json.load(open(os.path.join(found[-1], 'run.json'))) if found else None


def rewrite(kind, fn):
    path = os.path.join(dirs(kind)[-1], 'run.json')
    value = json.load(open(path))
    fn(value)
    json.dump(value, open(path, 'w'), sort_keys=True)


def secrets_seen():
    found = set()
    for headers, _ in POSTS:
        cookie = headers.get('Cookie') or headers.get('cookie') or ''
        found.add(cookie.split('=', 1)[-1])
        found.add(headers.get('x-csrf-token') or headers.get('X-Csrf-Token') or '')
    return {s for s in found if s}


def no_leak(name, out):
    blobs = out + open('/mock/w6-calls.log').read() if os.path.exists('/mock/w6-calls.log') else out
    for d in dirs(''):
        for f in os.listdir(d):
            if f.endswith('.json'):
                blobs += open(os.path.join(d, f)).read()
    leaked = [s for s in secrets_seen() if s in blobs]
    check(name + ': token/CSRF never in output, argv or evidence', not leaked)
    check(name + ': transport-token never read or printed', 'TRANSPORT-TOKEN-NEVER-READ' not in blobs)


def enter1_ok():
    fresh()
    rc, out = run('w6-enter.py')
    assert rc == 0, out
    return out


def reboot_ok(after_enter=None):
    enter1_ok()
    if after_enter:
        after_enter()
    rc, out = run('w6-reboot.py')
    assert rc == 0, out
    return out


def simulate_reboot(st, timeline=True, receipt=None, proof=None, stop=None):
    """The guest came back: B1, restore PASS in this boot, the gen-5 Pod Failed; the controller then writes the
    stop (v2 terminal), deletes the Pod and completes the proof on B1 after a few observe reads."""
    open('/mock/boot_id', 'w').write(B1 + '\n')
    st['node']['status']['nodeInfo']['bootID'] = B1
    st['bootHex'] = B1.replace('-', '')
    st['journal']['restore'] = [{'MESSAGE': PASS_LINE, '_BOOT_ID': B0.replace('-', '')},
                                {'MESSAGE': PASS_LINE, '_BOOT_ID': B1.replace('-', '')}]
    st['journal']['sudo'] = [{'MESSAGE': 'opc : PWD=/home/opc ; USER=root ; COMMAND=/usr/bin/python3 -I /root/issue61/w6-observe.py',
                              '__REALTIME_TIMESTAMP': '1793780000000000'},
                             {'MESSAGE': 'opc : USER=root ; COMMAND=/bin/echo %s kubectl delete pod x' % SUDO_SECRET,
                              '__REALTIME_TIMESTAMP': '1793780100000000'}]
    st['journal']['sshd'] = [{'MESSAGE': 'Accepted publickey for opc from 10.0.2.2 port 5 ssh2: ED25519 SHA256:%s' % SUDO_SECRET,
                              '__REALTIME_TIMESTAMP': '1793780050000000'}]
    st['controllerLogs'] = [json.dumps({'owner': OWNER, 'stage': 'controller', 'result': r})
                            for r in ('deleting', 'awaiting_pod_deletion', 'released')]
    pod = st['pod']
    pod['status']['phase'] = 'Failed'
    for s in pod['status']['containerStatuses']:
        s['state'] = {'terminated': {'exitCode': 255, 'reason': 'Unknown'}}
    st['db'].update(phase='runner_unreachable', ambiguous=False)
    intent = json.load(open(os.path.join(dirs('reboot')[-1], 'run.json')))['intent']['epoch']
    base = {'stopId': '11111111-2222-4333-8444-555555555555', 'owner': OWNER, 'state': STATE, 'generation': 5, 'pod': POD5,
            'requestedEpoch': intent + 60, 'nodeName': None, 'nodeUid': None, 'bootId': None, 'receipt': None,
            'proof': None, 'completed': False, 'completedAt': None}
    base.update(stop or {})
    rec = {'stopId': base['stopId'], 'ownerId': OWNER, 'stateId': STATE, 'podUid': POD5, 'generation': 5, 'version': 2,
           'terminal': {'podPhase': 'Failed', 'containers': {'runner': {'exitCode': 255, 'reason': 'Unknown'}}}}
    rec.update(receipt or {})
    prf = {'podUid': POD5, 'nodeName': 'anxious-lookout', 'nodeUid': NODE_UID, 'bootId': B1,
           'verifiedAt': '2026-11-04T07:00:00+00:00', 'evidence': 'pod-deleted'}
    prf.update(proof or {})
    opened = dict(base, receipt=rec, nodeName='anxious-lookout', nodeUid=NODE_UID, bootId=B1)
    done = dict(opened, proof=prf, completed=True, completedAt='2026-11-04T07:00:00+00:00', bootId=prf['bootId'])
    st['timeline'] = [{'after': 2, 'db': {'stop5': opened, 'stops': 5, 'allOpenStops': 1, 'phase': 'stopping'}},
                      {'after': 3, 'db': {'stop5': done, 'allOpenStops': 0, 'phase': 'idle_stopped', 'pod': None,
                                          'revision': 21}, 'pod': None}] if timeline else []


def observe_ok():
    reboot_ok()
    edit(simulate_reboot)
    rc, out = run('w6-observe.py')
    assert rc == 0, out
    return out


# Tests ------------------------------------------------------------------------------------------------
def test_enter1():
    fresh()
    rc, out = run('w6-enter.py')
    st = state()
    check('enter1 happy: rc 0', rc == 0, out)
    check('enter1 happy: one POST', len(POSTS) == 1, str(len(POSTS)))
    check('enter1 happy: session row revoked', not st['sessions'] and len(st.get('revoked', [])) == 1)
    r = rj('enter1')
    check('enter1 happy: record mode enter1, gen 5, Pod POD5, proof ok, health auth_revoked, storage recorded',
          r and r['mode'] == 'enter1' and r['newGeneration'] == 5 and r['pod']['uid'] == POD5 and r['proof']['ok']
          and r['health']['class'] == 'auth_revoked' and r['before']['storage']['hostMounts']['native']['loop'].startswith('7:')
          and r['before']['storage']['volumes']['native']['pvUid'] == VOL['native'][1] and r['result'] == 0, json.dumps(r))
    bound = min(r['post']['epoch'], r['pod']['startEpoch']) + 1200
    check('enter1 happy: reboot bound = min(POST, Pod start) + 1200 s, printed as NEXT',
          abs(r['rebootBy']['epoch'] - bound) < 1e-6 and r['rebootBy']['at'] in out and 'NEXT (step 2' in out, out)
    check('enter1 happy: evidence dir 0700 and run.json 0600',
          oct(os.stat(dirs('enter1')[-1]).st_mode & 0o777) == '0o700'
          and oct(os.stat(dirs('enter1')[-1] + '/run.json').st_mode & 0o777) == '0o600')
    check('enter1 happy: read-only queries are READ ONLY; writes only insert/revoke',
          all(q.startswith('BEGIN READ ONLY;') or re.search(r'/\*w6w:(insert|revoke)\*/', q) for q in st['queries']))
    no_leak('enter1 happy', out)
    rc, out = run('w6-enter.py')
    check('enter1 rerun after success: rc 3 (re-entry needs reboot and observe), no POST',
          rc == 3 and len(POSTS) == 1 and 'exactly one enter1, reboot and observe' in out, out)

    def fence(st):
        st['db']['revision'] = 15
    fresh(fence)
    rc, out = run('w6-enter.py')
    check('enter1: fence gen 4 rev 15 (not the agreed 16) is refused rc 3, no evidence, no POST',
          rc == 3 and not dirs('') and not POSTS and 'fence' in out, out)
    fresh()
    os.makedirs(EVID + '/w6-foo', mode=0o700)
    rc, out = run('w6-enter.py')
    check('enter1: an unexpected w6-* entry is refused rc 3', rc == 3 and 'unexpected W6 evidence entry' in out, out)

    def nc_then_revoked(st):
        nc = {'ownerId': OWNER, 'stateId': STATE, 'podUid': 'CURRENT', 'available': False, 'reason': 'not_configured',
              'failure': None}
        st['healthSeq'] = ['fail', nc, nc, dict(st['health'])]
    fresh(nc_then_revoked)
    rc, out = run('w6-enter.py', {'W6_HEALTH_WAIT_SECONDS': '10'})
    r = rj('enter1')
    check('enter1: probe failure, not_configured x2, then auth_revoked: rc 0 with the counts recorded separately',
          rc == 0 and r['health']['counts'] == {'probe_failed': 1, 'not_configured': 2, 'auth_revoked': 1}, out)
    for name, health, want, text in (
            ('usable', {'available': True, 'reason': None, 'failure': None}, 0, 'class usable'),
            ('another concrete failure', {'available': False, 'reason': 'login_failed', 'failure': 'login_failed'}, 9,
             'concrete failure other than auth_revoked'),
            ('still not_configured', {'available': False, 'reason': 'not_configured', 'failure': None}, 10,
             'not_configured'),
            ('another Pod UID', {'podUid': '00000000-0000-4000-8000-00000000dead'}, 8, 'another owner, state or Pod')):
        fresh(lambda st, h=health: st['health'].update(h))
        rc, out = run('w6-enter.py')
        check('enter1 health %s: rc %d' % (name, want), rc == want and text in out, out)
    fresh(lambda st: st['fail'].append('health-fail'))
    rc, out = run('w6-enter.py')
    check('enter1 health never observed (probe failures only): rc 11, never success',
          rc == 11 and 'never observed' in out and rj('enter1')['health']['class'] == 'unobserved', out)
    fresh(lambda st: st['pvc']['native-' + STATE_HEX]['metadata'].update(uid='00000000-0000-4000-8000-0000000000aa'))
    rc, out = run('w6-enter.py')
    check('enter1: a PVC with another UID is refused rc 3 before any POST', rc == 3 and not POSTS and 'PVC/PV' in out, out)
    fresh(lambda st: st['loops'][8].update({'back-ino': 1}))
    rc, out = run('w6-enter.py')
    check('enter1: the native loop backs another inode: rc 3 before any POST',
          rc == 3 and not POSTS and 'backs another file' in out, out)
    fresh(lambda st: st['podTemplate']['spec']['volumes'][0]['persistentVolumeClaim'].update(claimName='native-other'))
    rc, out = run('w6-enter.py')
    check('enter1: the Pod mounts another PVC: rc 8, left as it is', rc == 8 and 'volume native is not PVC' in out, out)
    fresh(lambda st: st.update(postMode='lost'))
    rc, out = run('w6-enter.py')
    check('enter1: the POST answer is lost: rc 6, never repeated, evidence kept',
          rc == 6 and len(POSTS) == 1 and len(dirs('enter1')) == 1 and rj('enter1')['result'] == 6, out)
    rc, out = run('w6-enter.py')
    check('enter1 after a lost POST answer: a second run is refused rc 3 (the attempt is never reused)',
          rc == 3 and len(POSTS) == 1, out)
    fresh(lambda st: st['fail'].append('insert-lost'))
    rc, out = run('w6-enter.py')
    check('enter1: the session insert result is lost: rc 4, no POST, the row found and removed',
          rc == 4 and not POSTS and not state()['sessions'], out)


def test_reboot():
    out = enter1_ok()
    rc, out = run('w6-reboot.py')
    st = state()
    r = rj('reboot')
    check('reboot happy: rc 0 and exactly one systemctl reboot call', rc == 0 and st.get('rebootCalls') == 1, out)
    check('reboot happy: says requested, not success; ssh will drop', 'reboot requested' in out and 'not its success' in out, out)
    check('reboot happy: intent (B0, Pod) recorded before the call, call rc recorded',
          r['intent']['bootId'] == B0 and r['intent']['podUid'] == POD5 and r['rebootCall']['rc'] == 0, json.dumps(r))
    ev = dirs('reboot')[-1]
    j, c = r['captured']['journal'], r['captured']['containerd']
    check('reboot happy: journal and containerd copies are 0600 files whose size/sha256 are recorded',
          all(oct(os.stat(os.path.join(ev, f)).st_mode & 0o777) == '0o600' for f in ('k3s-journal.jsonl', 'containerd.log'))
          and j['bytes'] == os.path.getsize(ev + '/k3s-journal.jsonl')
          and j['sha256'] == hashlib.sha256(open(ev + '/k3s-journal.jsonl', 'rb').read()).hexdigest()
          and c['sha256'] == hashlib.sha256(open(ev + '/containerd.log', 'rb').read()).hexdigest()
          and j['overToolCap'] is False, json.dumps(r['captured']))
    check('reboot happy: namespace IDs, hostname, B0, Node mapping, paid namespaces present-but-empty recorded',
          r['captured']['identity']['namespaces']['1/pid'] == 'pid:[4026531836]' and r['captured']['identity']['hostname'] == 'simple'
          and r['before']['node']['k3sHostname'] == 'simple' and r['before']['overview']['paidNamespacesPresent'] == ['shell-full', 'shell-pilot']
          and 'no Pod in them' in out, json.dumps(r['before']['overview']))
    check('reboot happy: the journal content is never printed', 'k3s line' not in out)
    rc, out = run('w6-reboot.py')
    check('reboot rerun: rc 3 and still one reboot call', rc == 3 and state().get('rebootCalls') == 1, out)

    def big(st):
        st['k3sJournalBytes'] = 16000500
    enter1_ok()
    edit(big)
    rc, out = run('w6-reboot.py')
    check('reboot: a journal above the 16,000,000-byte cap is kept whole and recorded as over cap, not empty',
          rc == 0 and rj('reboot')['captured']['journal']['overToolCap'] is True and 'OVER the 16000000-byte' in out, out)

    fresh()
    rc, out = run('w6-reboot.py')
    check('reboot without an enter1 record: rc 3, no evidence, no call',
          rc == 3 and not dirs('reboot') and not state().get('rebootCalls'), out)

    def shift(delta_post, delta_start):
        def fn(v):
            v['post']['epoch'] += delta_post
            v['pod']['startEpoch'] += delta_start
            v['rebootBy']['epoch'] = min(v['post']['epoch'], v['pod']['startEpoch']) + 1200
        return fn
    for name, fn, text in (('too late (POST and Pod start 1300 s ago)', shift(-1300, -1300), 'too late'),
                           ('Pod start in the future', shift(0, 100), 'in the future'),
                           ('Pod started before the POST', shift(0, -60), 'started before the POST'),
                           ('bound differs from the recorded one', lambda v: v['rebootBy'].update(epoch=v['rebootBy']['epoch'] + 30),
                            'differs from the one enter1 recorded')):
        enter1_ok()
        rewrite('enter1', fn)
        rc, out = run('w6-reboot.py')
        check('reboot time fence: %s -> rc 3, no evidence, no call' % name,
              rc == 3 and text in out and not dirs('reboot') and not state().get('rebootCalls'), out)
    cases = (
        ('clock skew 30 s', lambda st: st.update(dbSkew=30), 'clocks differ'),
        ('registry phase stopping', lambda st: st['db'].update(phase='stopping'), 'registry phase stopping'),
        ('ambiguous while ready', lambda st: st['db'].update(ambiguous=True), 'ambiguous outside runner_unreachable'),
        ('an open stop elsewhere', lambda st: st['db'].update(allOpenStops=1), 'allOpenStops=1'),
        ('a running conversation', lambda st: st['db'].update(running=1), 'running=1'),
        ('a Pod in shell-pilot', lambda st: st['otherPods'].append(other_pod('shell-pilot', 'pilot')), 'shell-pilot'),
        ('a Pod in an unknown namespace', lambda st: st['otherPods'].append(other_pod('rogue', 'x')), 'unknown namespace'),
        ('restore image missing', lambda st: st.update(restoreImage=False), 'restore image missing'),
        ('restore unit failed', lambda st: st['units']['issue16-restore.service'].update(Result='exit-code'),
         'issue16-restore.service not active/exited/success'),
        ('host supply service running', lambda st: st['units']['issue16-supply.service'].update(ActiveState='active'),
         'issue16-supply.service is running'),
        ('Node bootID differs from /proc', lambda st: st['node']['status']['nodeInfo'].update(bootID=B1),
         'Node bootID differs'),
        ('a workload not ready', lambda st: st['workloads']['deployment/m2-hosting/web'].update(ready=0), 'web'),
    )
    for name, fn, text in cases:
        enter1_ok()
        edit(fn)
        rc, out = run('w6-reboot.py')
        check('reboot precheck: %s -> rc 3, no call' % name, rc == 3 and text in out and not state().get('rebootCalls'), out)
    enter1_ok()
    edit(lambda st: st['db'].update(phase='runner_unreachable', ambiguous=True))
    rc, out = run('w6-reboot.py')
    check('reboot: runner_unreachable with ambiguous=true (as W4) is allowed', rc == 0, out)

    enter1_ok()
    os.makedirs('/mock/proc/4242')
    open('/mock/proc/4242/cmdline', 'wb').write(b'/usr/local/bin/codex\0login\0--device-auth\0')
    rc, out = run('w6-reboot.py')
    check('reboot: a codex login process -> rc 3', rc == 3 and 'codex-login' in out and 'device-auth' in out, out)
    enter1_ok()
    open('/usr/local/lib/anxious-lookout/issue16/worker.py', 'a').write('# changed\n')
    rc, out = run('w6-reboot.py')
    check('reboot: a pinned restore file changed -> rc 3', rc == 3 and 'worker.py changed' in out, out)

    def fs_case(name, action, text):
        enter1_ok()
        action()
        rc, out = run('w6-reboot.py')
        check('reboot restore mirror: %s -> rc 3, nothing mounted, no call' % name,
              rc == 3 and text in out and not state().get('rebootCalls'), out)
    out5804 = os.path.join(ROOT, '00000000000040008000000000005804')
    fs_case('a stray entry in the storage root', lambda: os.mkdir(os.path.join(STORAGE, 'ffffffffffffffffffffffffffffffff')),
            'stray or missing storage entry')

    def runtime_owner():
        path = os.path.join(out5804, 'runtime.json')
        json.dump({'plan': {'owner': OWNER, 'state': '00000000-0000-4000-8000-0000000000ff'}}, open(path, 'w'))
    fs_case('runtime.json names another state', runtime_owner, 'runtime.json of')
    fs_case('storage-restore.json hard-linked (nlink 2)',
            lambda: os.link(os.path.join(out5804, 'storage-restore.json'), '/mock/hardlink'), 'manifest file')

    def runtime_symlink():
        path = os.path.join(out5804, 'runtime.json')
        os.rename(path, path + '.real')
        os.symlink(path + '.real', path)
    fs_case('runtime.json is a symlink', runtime_symlink, 'runtime path')

    def manifest_extra():
        path = os.path.join(out5804, 'storage-restore.json')
        value = json.load(open(path))
        value['extra'] = 1
        json.dump(value, open(path, 'w'))
    fs_case('storage-restore.json with an extra key', manifest_extra, 'storage-restore.json of')

    def runtime_uid():
        os.chown(os.path.join(out5804, 'runtime.json'), 0, 0)
    fs_case('runtime.json owned by root, not 10001', runtime_uid, 'runtime file')

    def backing_size():
        fd = os.open(os.path.join(STORAGE, STATE_HEX, 'control.ext4'), os.O_WRONLY)
        os.ftruncate(fd, GIB + 4096)
        os.close(fd)
    fs_case('a backing file of the wrong size', backing_size, 'control backing/target')

    def unmounted():
        MOUNTS['ismount'].remove(os.path.join(STORAGE, STATE_HEX, 'native'))
        put_mounts()
    fs_case('a target not mounted', unmounted, 'not mounted')

    def not_shared():
        text = open('/mock/mountinfo').read().replace('shared:1 - xfs', '- xfs')
        open('/mock/mountinfo', 'w').write(text)
    fs_case('the storage root not a shared mount', not_shared, 'not a shared mount')
    fs_case('a loop device backing another inode', lambda: edit(lambda st: st['loops'][0].update({'back-ino': 7})),
            'backs another file')

    def evidence_case(name, action, text):
        enter1_ok()
        action()
        rc, out = run('w6-reboot.py')
        check('reboot evidence: %s -> rc 4, NO reboot call' % name,
              rc == 4 and text in out and not state().get('rebootCalls') and 'intent' not in (rj('reboot') or {}), out)

    def small_disk():
        MOUNTS['statvfs'][EVID] = [4096, 10 ** 6, 1000]
        put_mounts()
    evidence_case('free space below 2 GiB', small_disk, 'bytes free')
    evidence_case('journal export fails', lambda: edit(lambda st: st['fail'].append('journal-fail')),
                  'journal export failed')

    def fsync_fail():
        MOUNTS['fsyncFail'] = ['k3s-journal.jsonl']
        put_mounts()
    evidence_case('fsync of the journal fails (ENOSPC)', fsync_fail, 'saving the evidence failed')
    evidence_case('containerd log missing', lambda: os.unlink('/var/lib/rancher/k3s/agent/containerd/containerd.log'),
                  'containerd log is missing')

    enter1_ok()
    edit(lambda st: st.update(finalChange={'pod': '00000000-0000-4000-8000-0000000000ee'}))
    rc, out = run('w6-reboot.py')
    check('reboot: the registry Pod changes at the final look -> rc 5, NO call',
          rc == 5 and 'final look' in out and not state().get('rebootCalls'), out)
    import signal as sig

    def signalled(when):
        environ = dict(os.environ, **ENV)
        proc = subprocess.Popen(['python3', '-I', INSTALL + '/w6-reboot.py'], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, env=environ)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not when():
            time.sleep(0.05)
        time.sleep(0.3)
        proc.send_signal(sig.SIGTERM)
        out, _ = proc.communicate(timeout=120)
        return proc.returncode, out.decode()
    enter1_ok()
    edit(lambda st: st.update(journalSleep=4))
    rc, out = signalled(lambda: bool(dirs('reboot')))
    check('reboot: SIGTERM during the evidence export -> rc 130, NO reboot call, no intent',
          rc == 130 and not state().get('rebootCalls') and 'intent' not in (rj('reboot') or {}), out)
    enter1_ok()
    edit(lambda st: st.update(rebootSleep=3))
    rc, out = signalled(lambda: bool(state().get('rebootCalls')))
    check('reboot: SIGTERM during the systemctl call is ignored -> rc 0, one call',
          rc == 0 and state().get('rebootCalls') == 1 and rj('reboot')['rebootCall']['rc'] == 0, out)

    enter1_ok()
    edit(lambda st: st.update(rebootRc=1))
    rc, out = run('w6-reboot.py')
    check('reboot: systemctl reboot returns 1 -> rc 7, called once, never repeated',
          rc == 7 and state().get('rebootCalls') == 1 and rj('reboot')['rebootCall']['rc'] == 1, out)


def test_observe():
    reboot_ok()
    edit(simulate_reboot)
    rc, out = run('w6-observe.py')
    r = rj('observe')
    check('observe happy: rc 0 with G2 and G3', rc == 0 and 'G2 at' in out and 'G3 at' in out, out)
    check('observe happy: the Pod was seen Failed, then absent',
          'Failed [runner:terminated' in out and 'pod absent' in out, out)
    ev1 = os.path.join(dirs('enter1')[-1], 'run.json')
    ev2 = os.path.join(dirs('reboot')[-1], 'run.json')
    check('observe happy: the chain carries the enter1/reboot record hashes; proved B1, stop, gen-5 fence',
          r['chain']['enter1Sha256'] == hashlib.sha256(open(ev1, 'rb').read()).hexdigest()
          and r['chain']['rebootSha256'] == hashlib.sha256(open(ev2, 'rb').read()).hexdigest()
          and r['proved']['bootId'] == B1 and r['proved']['stop']['pod'] == POD5
          and r['proved']['fence'] == {'state': STATE, 'phase': 'idle_stopped', 'generation': 5, 'revision': 21,
                                       'pod': None, 'active': 0, 'ambiguous': False} and r['pass'] is True, json.dumps(r))
    n = r['noInput']
    check('observe happy: no-input counts (2 sudo commands, 1 risky, 1 ssh login, controller outcomes)',
          n['sudo']['commands'] == 2 and len(n['sudo']['riskyAt']) == 1 and len(n['sshd']['acceptedAt']) == 1
          and n['controller']['outcomes5804'] == {'deleting': 1, 'awaiting_pod_deletion': 1, 'released': 1}, json.dumps(n))
    ev = dirs('observe')[-1]
    check('observe happy: raw sudo/sshd/controller logs kept 0600 privately; their contents never printed',
          all(oct(os.stat(os.path.join(ev, f)).st_mode & 0o777) == '0o600'
              for f in ('sudo-journal.jsonl', 'sshd-journal.jsonl', 'controller.log'))
          and SUDO_SECRET not in out and SUDO_SECRET not in open(ev + '/run.json').read()
          and 'absence from these logs is not proof of absence' in out, out)
    rc, out = run('w6-observe.py')
    check('observe rerun after a pass: rc 3', rc == 3, out)

    reboot_ok()
    rc, out = run('w6-observe.py')
    check('observe before the reboot (boot still B0): rc 3, nothing written',
          rc == 3 and 'still B0' in out and not dirs('observe'), out)

    def case(name, fn, want, text, sim=None):
        reboot_ok()
        edit(lambda st: (simulate_reboot(st, **(sim or {})), fn(st)))
        rc, out = run('w6-observe.py')
        check('observe: %s -> rc %d' % (name, want), rc == want and text in out, out)
    case('only an old-boot restore PASS line',
         lambda st: st['journal'].update(restore=[{'MESSAGE': PASS_LINE, '_BOOT_ID': B0.replace('-', '')}]), 5,
         'no restore PASS line in this boot')
    case('restore unit did not run in this boot',
         lambda st: st['units']['issue16-restore.service'].update(ExecMainExitTimestampMonotonic='0'), 5,
         'issue16-restore.service not active/exited/success in this boot')
    case('Node still on the old bootID', lambda st: st['node']['status']['nodeInfo'].update(bootID=B0), 5,
         'Node bootID is not B1')
    case('a loop mount not linked after the reboot', lambda st: st['loops'][3].update({'back-ino': 3}), 5,
         'loop backs another file')
    case('a v3 receipt', lambda st: None, 6, 'receipt version 3', {'receipt': {'version': 3}})
    case('a v4 receipt', lambda st: None, 6, 'receipt version 4', {'receipt': {'version': 4}})
    case('a v2 receipt with an extra key', lambda st: None, 6, 'v2 receipt does not match', {'receipt': {'extra': 1}})
    case('a v2 receipt for another Pod', lambda st: None, 6, 'v2 receipt does not match',
         {'receipt': {'podUid': POD6}})
    case('a proof on B0', lambda st: None, 6, 'the proof is on B0', {'proof': {'bootId': B0}})
    case('a proof on another Node UID', lambda st: None, 6, 'not pod-deleted on the recorded Node',
         {'proof': {'nodeUid': '00000000-0000-4000-8000-0000000000cc'}})
    case('a stop for another Pod UID', lambda st: None, 6, 'another owner/state/Pod', {'stop': {'pod': POD6}})
    case('a stop requested before the intent', lambda st: None, 6, 'requested before the reboot intent',
         {'stop': {'requestedEpoch': 1000}})
    case('two new stops', lambda st: st['timeline'][0]['db'].update(stops=6), 6, '2 new stops')
    case('a replacement runner Pod', lambda st: st['pod']['metadata'].update(uid=POD6), 6, 'replacement runner Pod')
    case('the registry moved to gen 6', lambda st: st['timeline'][1]['db'].update(generation=6), 6, 'registry moved')
    case('the stop never completes', lambda st: st['timeline'].pop(), 5, 'gen-5 stop not completed yet')

    def running(st):
        st['pod']['status']['phase'] = 'Running'
        for s in st['pod']['status']['containerStatuses']:
            s['state'] = {'running': {}}
    case('the Pod stays Running and no stop appears', lambda st: (running(st), st.update(timeline=[])), 5,
         'no gen-5 stop yet')
    case('k3s never comes back', lambda st: st['units']['k3s.service'].update(ActiveState='activating'), 5,
         'k3s activating')

    reboot_ok()
    edit(simulate_reboot)
    rewrite('reboot', lambda v: v.pop('intent'))
    rc, out = run('w6-observe.py')
    check('observe: a reboot record without an intent -> rc 3', rc == 3 and 'no consistent intent' in out, out)
    reboot_ok()
    edit(simulate_reboot)
    rewrite('reboot', lambda v: v.update(rebootCall={'rc': 1}))
    rc, out = run('w6-observe.py')
    check('observe: a reboot record with a failed call -> rc 3', rc == 3 and 'failed reboot call' in out, out)
    reboot_ok()
    edit(simulate_reboot)
    os.unlink(os.path.join(dirs('reboot')[-1], 'run.json'))
    rc, out = run('w6-observe.py')
    check('observe: a partial reboot record (run.json missing) -> rc 3', rc == 3 and 'missing, partial' in out, out)
    reboot_ok()
    edit(simulate_reboot)
    MOUNTS['fsyncFail'] = ['sudo-journal.jsonl']
    put_mounts()
    rc, out = run('w6-observe.py')
    MOUNTS['fsyncFail'] = []
    put_mounts()
    check('observe: proved but the no-input evidence cannot be saved -> rc 9', rc == 9, out)


def test_enter2():
    observe_ok()
    rc, out = run('w6-enter.py')
    r = rj('enter2')
    check('enter2 happy: rc 0, mode enter2, gen 6, Pod POD6, chain verified',
          rc == 0 and r and r['mode'] == 'enter2' and r['newGeneration'] == 6 and r['pod']['uid'] == POD6
          and r['chain']['b1'] == B1 and 'chain ok' in out, out)
    check('enter2 happy: two POSTs over the whole W6 run (one per entry), W7 deadline printed',
          len(POSTS) == 2 and 'W7 (read-only) by Pod start + 60 min' in out, out)
    no_leak('enter2 happy', out)
    rc, out = run('w6-enter.py')
    check('enter2: a third entry is refused rc 3', rc == 3 and 'never a third entry' in out, out)

    def case(name, action, text):
        observe_ok()
        action()
        before = len(POSTS)
        rc, out = run('w6-enter.py')
        check('enter2: %s -> rc 3, no POST' % name, rc == 3 and text in out and len(POSTS) == before, out)
    case('the enter1 record changed after observe', lambda: rewrite('enter1', lambda v: v.update(note='x')),
         'differ from the hashes')
    case('the observe record is not a pass', lambda: rewrite('observe', lambda v: v.update(result=5)), 'not a pass')
    case('another boot than B1', lambda: open('/mock/boot_id', 'w').write('c1c1c1c1-2127-4d43-af0c-e94cb6da2c85\n'),
         'not the B1')
    case('the database stop proof is on B0', lambda: edit(lambda st: st['db']['stop5']['proof'].update(bootId=B0)),
         'not the one w6-observe proved')
    case('the fence revision differs from the observed one', lambda: edit(lambda st: st['db'].update(revision=22)),
         'fence is')
    case('a second observe record', lambda: os.makedirs(EVID + '/w6-observe-20991231T000000Z'), 'exactly one enter1')


def main():
    install_tools()
    fresh()
    for name, fn in (('enter1', test_enter1), ('reboot', test_reboot), ('observe', test_observe), ('enter2', test_enter2)):
        try:
            fn()
        except Exception as error:  # a broken fixture is a failure, not a crash of the harness
            check('%s block ran to the end' % name, False, '%s: %s' % (type(error).__name__, error))
    print('%d passed, %d failed' % (passed, failed))
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
