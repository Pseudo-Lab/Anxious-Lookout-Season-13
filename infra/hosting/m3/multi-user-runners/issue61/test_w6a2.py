"""Mock harness for the #61 W6 attempt-2 scripts (w6a2-enter.py, w6a2-reboot.py, w6a2-observe.py), with attempt 1's
approved scripts installed beside them. Docker only, never on a node:
    docker run --rm --network none --hostname simple -v "$PWD/infra/hosting/m3:/m3:ro" \
      -v "$PWD/backend/ops:/ops:ro" python:3.9-alpine python3 -I /m3/multi-user-runners/issue61/test_w6a2.py

mock/fake_w6a2.py stands in for k3s (kubectl/crictl), losetup, systemctl, journalctl and docker. The API POST is
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
POD6 = '7c0d2b8e-5555-4222-8333-444455556666'
POD7 = '7c0d2b8e-6666-4222-8333-444455556666'
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
    db = {'state': STATE, 'phase': 'idle_stopped', 'generation': 5, 'revision': 20, 'pod': None, 'active': 0,
          'ambiguous': False, 'allOpenStops': 0, 'stops': 5, 'running': 0, 'conv': {'n': 0, 'digest': '-'},
          'stop6': None,
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
        'podUids': {'6': POD6, '7': POD7}, 'cids': {'6': {'runner': '5' * 64, 'shell-executor': '6' * 64},
                                                     '7': {'runner': '7' * 64, 'shell-executor': '8' * 64}},
        'sandbox': {'6': 'a' * 64, '7': 'b' * 64}, 'cri': {}, 'ps': {}, 'sandboxes': {},
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
    MOUNTS.update({'ismount': ismount, 'statvfs': statvfs, 'fsyncFail': [], 'fsyncFailIfContains': []})
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
    if c is not None and c.get('fsyncFailIfContains'):
        try:
            name = os.readlink('/proc/self/fd/%d' % fd)
            with open(name) as s:
                text = s.read()
        except (OSError, UnicodeDecodeError):
            text = ''
        if any(m in text for m in c['fsyncFailIfContains']):
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
        if st.get('convChangeOnPost'):
            st['db']['conv'] = {'n': 1, 'digest': 'changed'}
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
            stream.write('#!/bin/sh\nFAKE_NAME=%s exec python3 -I %s/mock/fake_w6a2.py "$@"\n' % (tool, SRC))
        os.chmod(path, 0o755)
    site = [p for p in sys.path if p.endswith('site-packages')][0]
    open(os.path.join(site, 'sitecustomize.py'), 'w').write(SITE)
    server = http.server.ThreadingHTTPServer(('127.0.0.1', PORT), Api)
    threading.Thread(target=server.serve_forever, daemon=True).start()


def install_scripts():
    """Attempt 2's scripts, and attempt 1's approved scripts beside them (as on the guest), both pointed at the mock."""
    shutil.rmtree(INSTALL, ignore_errors=True)
    os.makedirs(INSTALL, mode=0o700)
    common = (("BOOT_ID = '/proc/sys/kernel/random/boot_id'\n", "BOOT_ID = '/mock/boot_id'\n"),
              ("MOUNTINFO = '/proc/self/mountinfo'\n", "MOUNTINFO = '/mock/mountinfo'\n"))
    for attempt in ('w6a2', 'w6'):
        for name, extra in (('%s-enter.py' % attempt, (("API_HOST = '10.0.0.107'\n", "API_HOST = '127.0.0.1'\n"),
                                                     ('API_PORT = 80\n', 'API_PORT = %d\n' % PORT))),
                            ('%s-reboot.py' % attempt, (("PROC = '/proc'\n", "PROC = '/mock/proc'\n"),)),
                            ('%s-observe.py' % attempt, ())):
            text = open(os.path.join(SRC, name)).read()
            pointed = text
            for old, new in common + extra:
                assert pointed.count(old) == 1, (name, old)
                pointed = pointed.replace(old, new)
            if name.endswith('-reboot.py'):
                for path, digest in PINNED.items():
                    assert pointed.count(digest) == 1, digest
                    pointed = pointed.replace(digest, hashlib.sha256(open(path, 'rb').read()).hexdigest())
            assert len(pointed.splitlines()) == len(text.splitlines())
            open(os.path.join(INSTALL, name), 'w').write(pointed)


# Attempt 1 as it ended (2026-11-04T10:34Z read-only diagnosis): its one record, its lock file, the gen-5 stop.
A1_DIR = 'w6-enter1-20261104T095411Z'
A1_POD = 'c00de0a9-d28c-446f-a596-db0f629476fa'
A1_LOCK_MTIME = 1793786050
A1_POST = 1793786051.5


def attempt1(st):
    os.makedirs(EVID, mode=0o700, exist_ok=True)
    os.mkdir(os.path.join(EVID, A1_DIR), 0o700)
    record = {'mode': 'enter1', 'newGeneration': 5, 'result': 0, 'proof': {'ok': True},
              'post': {'epoch': A1_POST, 'at': '2026-11-04T09:54:11Z'},
              'pod': {'uid': A1_POD, 'startEpoch': 1793786051, 'start': '2026-11-04T09:54:11Z'},
              'rebootBy': {'epoch': 1793787251, 'at': '2026-11-04T10:14:11Z'},
              'health': {'class': 'auth_revoked'}, 'bootId': B0}
    path = os.path.join(EVID, A1_DIR, 'run.json')
    with open(path, 'w') as stream:
        json.dump(record, stream, sort_keys=True)
    os.chmod(path, 0o600)
    fd = os.open(INSTALL + '/w6.lock', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    os.utime(INSTALL + '/w6.lock', (A1_LOCK_MTIME, A1_LOCK_MTIME))
    st['db']['base'] = {
        'stopId': '3b15f417-38a3-4409-8bfb-f9afaf2c800c', 'owner': OWNER, 'state': STATE, 'generation': 5, 'pod': A1_POD,
        'nodeName': 'anxious-lookout', 'nodeUid': NODE_UID, 'bootId': B0, 'completed': True,
        'receipt': {'stopId': '3b15f417-38a3-4409-8bfb-f9afaf2c800c', 'ownerId': OWNER, 'stateId': STATE, 'podUid': A1_POD,
                    'generation': 5, 'version': 3, 'unavailable': {'clean': False, 'reason': 'auth_revoked'}},
        'proof': {'podUid': A1_POD, 'nodeName': 'anxious-lookout', 'nodeUid': NODE_UID, 'bootId': B0,
                  'verifiedAt': '2026-11-04T10:24:37.154735+00:00', 'evidence': 'pod-deleted'}}


def a1_snapshot():
    """Attempt 1's files as bytes and metadata: they must never change."""
    out = {}
    for path in (os.path.join(EVID, A1_DIR), os.path.join(EVID, A1_DIR, 'run.json'), INSTALL + '/w6.lock'):
        if os.path.lexists(path):
            info = os.lstat(path)
            data = open(path, 'rb').read() if os.path.isfile(path) else sorted(os.listdir(path)).__repr__().encode()
            out[path] = (info.st_mode, info.st_mtime_ns, info.st_size, hashlib.sha256(data).hexdigest())
    return out


def fresh(mutate=None, a1=True):
    shutil.rmtree('/mock', ignore_errors=True)
    os.makedirs('/mock', mode=0o700)
    build_host()
    install_scripts()
    st = base_state()
    build_fs(st)
    if a1:
        attempt1(st)
    if mutate:
        mutate(st)
    put(st)
    del POSTS[:]


ENV = {'W6_POD_WAIT_SECONDS': '20', 'W6_POST_TIMEOUT': '3', 'W6_HEALTH_WAIT_SECONDS': '2', 'W6_HEALTH_POLL_SECONDS': '0.2',
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
    prefix = 'w6a2-' + (kind + '-' if kind else '')  # '' = every attempt-2 record (attempt 1's harness matched none)
    return sorted(os.path.join(EVID, d) for d in os.listdir(EVID) if d.startswith(prefix))


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
    rc, out = run('w6a2-enter.py')
    assert rc == 0, out
    return out


def reboot_ok(after_enter=None):
    enter1_ok()
    if after_enter:
        after_enter()
    rc, out = run('w6a2-reboot.py')
    assert rc == 0, out
    return out


def stamp(epoch, digits=6):
    """An ISO 8601 timestamp with an explicit offset, as the controller (6 digits) or PostgreSQL (5 here) writes."""
    frac = ('%.*f' % (digits, epoch % 1))[1:]
    return time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(epoch)) + frac + '+00:00'


def simulate_reboot(st, timeline=True, receipt=None, proof=None, stop=None, completed_over=None):
    """The guest came back: B1, restore PASS in this boot, the gen-6 Pod Failed; the controller then writes the
    stop (v2 terminal), deletes the Pod and completes the proof on B1 after a few observe reads."""
    open('/mock/boot_id', 'w').write(B1 + '\n')
    st['node']['status']['nodeInfo']['bootID'] = B1
    st['bootHex'] = B1.replace('-', '')
    st['journal']['restore'] = [{'MESSAGE': PASS_LINE, '_BOOT_ID': B0.replace('-', '')},
                                {'MESSAGE': PASS_LINE, '_BOOT_ID': B1.replace('-', '')}]
    st['journal']['sudo'] = [{'MESSAGE': 'opc : PWD=/home/opc ; USER=root ; COMMAND=/usr/bin/python3 -I /root/issue61/w6a2-observe.py',
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
    base = {'stopId': '11111111-2222-4333-8444-555555555555', 'owner': OWNER, 'state': STATE, 'generation': 6, 'pod': POD6,
            'requestedEpoch': intent + 0.1, 'nodeName': None, 'nodeUid': None, 'bootId': None, 'receipt': None,
            'proof': None, 'completed': False, 'completedAt': None}
    base.update(stop or {})
    rec = {'stopId': base['stopId'], 'ownerId': OWNER, 'stateId': STATE, 'podUid': POD6, 'generation': 6, 'version': 2,
           'terminal': {'podPhase': 'Failed', 'containers': {'runner': {'exitCode': 255, 'reason': 'Unknown'}}}}
    rec.update(receipt or {})
    prf = {'podUid': POD6, 'nodeName': 'anxious-lookout', 'nodeUid': NODE_UID, 'bootId': B1,
           'verifiedAt': stamp(intent + 0.2), 'evidence': 'pod-deleted'}
    prf.update(proof or {})
    opened = dict(base, receipt=rec, nodeName='anxious-lookout', nodeUid=NODE_UID, bootId=B1)
    done = dict(opened, proof=prf, completed=True, completedAt=stamp(intent + 0.3, 5), completedEpoch=intent + 0.3,
                bootId=prf['bootId'])
    done.update(completed_over or {})
    st['timeline'] = [{'after': 2, 'db': {'stop6': opened, 'stops': 6, 'allOpenStops': 1, 'phase': 'stopping'}},
                      {'after': 3, 'db': {'stop6': done, 'allOpenStops': 0, 'phase': 'idle_stopped', 'pod': None,
                                          'revision': 25}, 'pod': None}] if timeline else []


def observe_ok():
    reboot_ok()
    edit(simulate_reboot)
    rc, out = run('w6a2-observe.py')
    assert rc == 0, out
    return out


# Tests ------------------------------------------------------------------------------------------------
def test_enter1():
    fresh()
    rc, out = run('w6a2-enter.py')
    st = state()
    check('enter1 happy: rc 0', rc == 0, out)
    check('enter1 happy: one POST', len(POSTS) == 1, str(len(POSTS)))
    check('enter1 happy: session row revoked', not st['sessions'] and len(st.get('revoked', [])) == 1)
    r = rj('enter1')
    check('enter1 happy: record mode enter1, gen 6, Pod POD6, proof ok, health auth_revoked, storage recorded',
          r and r['mode'] == 'enter1' and r['newGeneration'] == 6 and r['pod']['uid'] == POD6 and r['proof']['ok']
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
    rc, out = run('w6a2-enter.py')
    check('enter1 rerun after success: rc 3 (re-entry needs reboot and observe), no POST',
          rc == 3 and len(POSTS) == 1 and 'exactly one enter1, reboot and observe' in out, out)

    def fence(st):
        st['db']['revision'] = 19
    fresh(fence)
    rc, out = run('w6a2-enter.py')
    check('enter1: fence gen 5 rev 19 (not the diagnosed 20) is refused rc 3, no evidence, no POST',
          rc == 3 and not dirs('') and not POSTS and 'fence' in out, out)
    fresh()
    os.makedirs(EVID + '/w6a2-foo', mode=0o700)
    rc, out = run('w6a2-enter.py')
    check('enter1: an unexpected w6a2-* entry is refused rc 3', rc == 3 and 'unexpected W6 evidence entry' in out, out)

    def nc_then_revoked(st):
        nc = {'ownerId': OWNER, 'stateId': STATE, 'podUid': 'CURRENT', 'available': False, 'reason': 'not_configured',
              'failure': None}
        st['healthSeq'] = ['fail', nc, nc, dict(st['health'])]
    fresh(nc_then_revoked)
    rc, out = run('w6a2-enter.py', {'W6_HEALTH_WAIT_SECONDS': '10'})
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
        rc, out = run('w6a2-enter.py')
        check('enter1 health %s: rc %d' % (name, want), rc == want and text in out, out)
    fresh(lambda st: st['fail'].append('health-fail'))
    rc, out = run('w6a2-enter.py')
    check('enter1 health never observed (probe failures only): rc 11, never success',
          rc == 11 and 'not observed inside' in out and rj('enter1')['health']['class'] == 'unobserved', out)
    fresh(lambda st: st['pvc']['native-' + STATE_HEX]['metadata'].update(uid='00000000-0000-4000-8000-0000000000aa'))
    rc, out = run('w6a2-enter.py')
    check('enter1: a PVC with another UID is refused rc 3 before any POST', rc == 3 and not POSTS and 'PVC/PV' in out, out)
    fresh(lambda st: st['loops'][8].update({'back-ino': 1}))
    rc, out = run('w6a2-enter.py')
    check('enter1: the native loop backs another inode: rc 3 before any POST',
          rc == 3 and not POSTS and 'backs another file' in out, out)
    fresh(lambda st: st['podTemplate']['spec']['volumes'][0]['persistentVolumeClaim'].update(claimName='native-other'))
    rc, out = run('w6a2-enter.py')
    check('enter1: the Pod mounts another PVC: rc 8, left as it is', rc == 8 and 'volume native is not PVC' in out, out)
    fresh(lambda st: st.update(postMode='lost'))
    rc, out = run('w6a2-enter.py')
    check('enter1: the POST answer is lost: rc 6, never repeated, evidence kept',
          rc == 6 and len(POSTS) == 1 and len(dirs('enter1')) == 1 and rj('enter1')['result'] == 6, out)
    rc, out = run('w6a2-enter.py')
    check('enter1 after a lost POST answer: a second run is refused rc 3 (the attempt is never reused)',
          rc == 3 and len(POSTS) == 1, out)
    fresh(lambda st: st['fail'].append('insert-lost'))
    rc, out = run('w6a2-enter.py')
    check('enter1: the session insert result is lost: rc 4, no POST, the row found and removed',
          rc == 4 and not POSTS and not state()['sessions'], out)


def test_reboot():
    out = enter1_ok()
    rc, out = run('w6a2-reboot.py')
    st = state()
    r = rj('reboot')
    check('reboot happy: rc 0 and exactly one systemctl reboot call', rc == 0 and st.get('rebootCalls') == 1, out)
    check('reboot happy: says requested, not success; ssh will drop', 'reboot requested' in out and 'not its success' in out, out)
    check('reboot happy: intent (B0, Pod) recorded before the call, call rc recorded',
          r['intent']['bootId'] == B0 and r['intent']['podUid'] == POD6 and r['rebootCall']['rc'] == 0, json.dumps(r))
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
    rc, out = run('w6a2-reboot.py')
    check('reboot rerun: rc 3 and still one reboot call', rc == 3 and state().get('rebootCalls') == 1, out)

    def big(st):
        st['k3sJournalBytes'] = 16000500
    enter1_ok()
    edit(big)
    rc, out = run('w6a2-reboot.py')
    check('reboot: a journal above the 16,000,000-byte cap is kept whole and recorded as over cap, not empty',
          rc == 0 and rj('reboot')['captured']['journal']['overToolCap'] is True and 'OVER the 16000000-byte' in out, out)

    fresh()
    rc, out = run('w6a2-reboot.py')
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
        rc, out = run('w6a2-reboot.py')
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
        rc, out = run('w6a2-reboot.py')
        check('reboot precheck: %s -> rc 3, no call' % name, rc == 3 and text in out and not state().get('rebootCalls'), out)
    enter1_ok()
    edit(lambda st: st['db'].update(phase='runner_unreachable', ambiguous=True))
    rc, out = run('w6a2-reboot.py')
    check('reboot: runner_unreachable with ambiguous=true (as W4) is allowed', rc == 0, out)

    enter1_ok()
    os.makedirs('/mock/proc/4242')
    open('/mock/proc/4242/cmdline', 'wb').write(b'/usr/local/bin/codex\0login\0--device-auth\0')
    rc, out = run('w6a2-reboot.py')
    check('reboot: a codex login process -> rc 3', rc == 3 and 'codex-login' in out and 'device-auth' in out, out)
    enter1_ok()
    open('/usr/local/lib/anxious-lookout/issue16/worker.py', 'a').write('# changed\n')
    rc, out = run('w6a2-reboot.py')
    check('reboot: a pinned restore file changed -> rc 3', rc == 3 and 'worker.py changed' in out, out)

    def fs_case(name, action, text):
        enter1_ok()
        action()
        rc, out = run('w6a2-reboot.py')
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
        rc, out = run('w6a2-reboot.py')
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
    rc, out = run('w6a2-reboot.py')
    check('reboot: the registry Pod changes at the final look -> rc 5, NO call',
          rc == 5 and 'final look' in out and not state().get('rebootCalls'), out)
    import signal as sig

    def signalled(when):
        environ = dict(os.environ, **ENV)
        proc = subprocess.Popen(['python3', '-I', INSTALL + '/w6a2-reboot.py'], stdout=subprocess.PIPE,
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
    rc, out = run('w6a2-reboot.py')
    check('reboot: systemctl reboot returns 1 -> rc 7, called once, never repeated',
          rc == 7 and state().get('rebootCalls') == 1 and rj('reboot')['rebootCall']['rc'] == 1, out)


def test_observe():
    reboot_ok()
    edit(simulate_reboot)
    rc, out = run('w6a2-observe.py')
    r = rj('observe')
    check('observe happy: rc 0 with G2 and G3', rc == 0 and 'G2 at' in out and 'G3 at' in out, out)
    check('observe happy: the Pod was seen Failed, then absent',
          'Failed [runner:terminated' in out and 'pod absent' in out, out)
    ev1 = os.path.join(dirs('enter1')[-1], 'run.json')
    ev2 = os.path.join(dirs('reboot')[-1], 'run.json')
    check('observe happy: the chain carries the enter1/reboot record hashes; proved B1, stop, gen-6 fence',
          r['chain']['enter1Sha256'] == hashlib.sha256(open(ev1, 'rb').read()).hexdigest()
          and r['chain']['rebootSha256'] == hashlib.sha256(open(ev2, 'rb').read()).hexdigest()
          and r['proved']['bootId'] == B1 and r['proved']['stop']['pod'] == POD6
          and r['proved']['fence'] == {'state': STATE, 'phase': 'idle_stopped', 'generation': 6, 'revision': 25,
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
    rc, out = run('w6a2-observe.py')
    check('observe rerun after a pass: rc 3', rc == 3, out)

    reboot_ok()
    rc, out = run('w6a2-observe.py')
    check('observe before the reboot (boot still B0): rc 3, nothing written',
          rc == 3 and 'still B0' in out and not dirs('observe'), out)

    def case(name, fn, want, text, sim=None):
        reboot_ok()
        edit(lambda st: (simulate_reboot(st, **(sim or {})), fn(st)))
        rc, out = run('w6a2-observe.py')
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
         {'receipt': {'podUid': POD7}})
    case('a proof on B0', lambda st: None, 6, 'the proof is on B0', {'proof': {'bootId': B0}})
    case('a proof on another Node UID', lambda st: None, 6, 'not pod-deleted on the recorded Node',
         {'proof': {'nodeUid': '00000000-0000-4000-8000-0000000000cc'}})
    case('a stop for another Pod UID', lambda st: None, 6, 'another owner/state/Pod', {'stop': {'pod': POD7}})
    case('a stop requested before the intent', lambda st: None, 6, 'requested before the reboot intent',
         {'stop': {'requestedEpoch': 1000}})
    case('two new stops', lambda st: st['timeline'][0]['db'].update(stops=7), 6, '2 new stops')

    # Round 1 (a2): G3 needs the owner's stop total to be exactly the integer 6 (5 before the reboot + 1).
    def stops_at(value, steps=(0, 1)):
        return lambda st: [st['timeline'][i]['db'].update(stops=value) for i in steps]
    case('the total stays 5 while the gen-6 stop completes (review counterexample: final read only)',
         stops_at(5, (1,)), 6, 'an earlier stop is gone')
    case('the total is 5 with the gen-6 stop from its first read', stops_at(5), 6, 'an earlier stop is gone')
    case('the total drops to 4', stops_at(4), 6, 'fewer than the 5 before the reboot')
    case('the total drops to 0', stops_at(0), 6, 'fewer than the 5 before the reboot')
    case('the total is 8', stops_at(8), 6, '3 new stops')
    case('the total is null', stops_at(None), 5, 'the owner stop count is unreadable (None)')
    case('the total is missing from the row', stops_at('__absent__'), 5, 'the owner stop count is unreadable (None)')
    case('the total is the string "6"', stops_at('6'), 5, "the owner stop count is unreadable ('6')")
    case('the total is the float 6.0', stops_at(6.0), 5, 'the owner stop count is unreadable (6.0)')
    case('the total is true', stops_at(True), 5, 'the owner stop count is unreadable (True)')
    case('the total is exactly the integer 6', stops_at(6), 0, 'W6 G2+G3 ok')
    case('a replacement runner Pod', lambda st: st['pod']['metadata'].update(uid=POD7), 6, 'replacement runner Pod')
    case('the registry moved to gen 7', lambda st: st['timeline'][1]['db'].update(generation=7), 6, 'registry moved')
    case('the stop never completes', lambda st: st['timeline'].pop(), 5, 'gen-6 stop not completed yet')

    def running(st):
        st['pod']['status']['phase'] = 'Running'
        for s in st['pod']['status']['containerStatuses']:
            s['state'] = {'running': {}}
    case('the Pod stays Running and no stop appears', lambda st: (running(st), st.update(timeline=[])), 5,
         'no gen-6 stop yet')
    case('k3s never comes back', lambda st: st['units']['k3s.service'].update(ActiveState='activating'), 5,
         'k3s activating')

    reboot_ok()
    edit(simulate_reboot)
    rewrite('reboot', lambda v: v.pop('intent'))
    rc, out = run('w6a2-observe.py')
    check('observe: a reboot record without an intent -> rc 3', rc == 3 and 'no consistent intent' in out, out)
    reboot_ok()
    edit(simulate_reboot)
    rewrite('reboot', lambda v: v.update(rebootCall={'rc': 1}))
    rc, out = run('w6a2-observe.py')
    check('observe: a reboot record with a failed call -> rc 3', rc == 3 and 'failed reboot call' in out, out)
    reboot_ok()
    edit(simulate_reboot)
    os.unlink(os.path.join(dirs('reboot')[-1], 'run.json'))
    rc, out = run('w6a2-observe.py')
    check('observe: a partial reboot record (run.json missing) -> rc 3', rc == 3 and 'missing, partial' in out, out)
    reboot_ok()
    edit(simulate_reboot)
    MOUNTS['fsyncFail'] = ['sudo-journal.jsonl']
    put_mounts()
    rc, out = run('w6a2-observe.py')
    MOUNTS['fsyncFail'] = []
    put_mounts()
    check('observe: proved but the no-input evidence cannot be saved -> rc 9', rc == 9, out)


def test_enter2():
    observe_ok()
    rc, out = run('w6a2-enter.py')
    r = rj('enter2')
    check('enter2 happy: rc 0, mode enter2, gen 7, Pod POD7, chain verified',
          rc == 0 and r and r['mode'] == 'enter2' and r['newGeneration'] == 7 and r['pod']['uid'] == POD7
          and r['chain']['b1'] == B1 and 'chain ok' in out, out)
    check('enter2 happy: two POSTs over the whole W6 run (one per entry), W7 deadline printed',
          len(POSTS) == 2 and 'W7 (read-only) by Pod start + 60 min' in out, out)
    no_leak('enter2 happy', out)
    rc, out = run('w6a2-enter.py')
    check('enter2: a third entry is refused rc 3', rc == 3 and 'never a third entry' in out, out)

    def case(name, action, text):
        observe_ok()
        action()
        before = len(POSTS)
        rc, out = run('w6a2-enter.py')
        check('enter2: %s -> rc 3, no POST' % name, rc == 3 and text in out and len(POSTS) == before, out)
    case('the enter1 record changed after observe', lambda: rewrite('enter1', lambda v: v.update(note='x')),
         'differ from the hashes')
    case('the observe record is not a pass', lambda: rewrite('observe', lambda v: v.update(result=5)), 'not a pass')
    case('another boot than B1', lambda: open('/mock/boot_id', 'w').write('c1c1c1c1-2127-4d43-af0c-e94cb6da2c85\n'),
         'not the B1')
    case('the database stop proof is on B0', lambda: edit(lambda st: st['db']['stop6']['proof'].update(bootId=B0)),
         'not the one w6a2-observe proved')
    case('the fence revision differs from the observed one', lambda: edit(lambda st: st['db'].update(revision=22)),
         'fence is')
    case('a second observe record', lambda: os.makedirs(EVID + '/w6a2-observe-20991231T000000Z'), 'exactly one enter1')




def test_round2():
    import fcntl
    import signal as sig

    def hold():
        fd = os.open(INSTALL + '/w6a2.lock', os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd

    # Blocker 2: one W6 step at a time; the loser is refused before any side effect ----------------------
    fresh()
    fd = hold()
    rc, out = run('w6a2-enter.py')
    os.close(fd)
    check('lock: enter while another W6 step holds the lock -> rc 3, no evidence, no session, no POST',
          rc == 3 and 'lock held' in out and not dirs('') and not POSTS and not state().get('queries'), out)
    fresh(lambda st: st.update(podAfter=3))
    environ = dict(os.environ, **ENV)
    procs = [subprocess.Popen(['python3', '-I', INSTALL + '/w6a2-enter.py'], stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, env=environ) for _ in range(2)]
    results = [(p.wait(timeout=120), p.stdout.read().decode()) for p in procs]
    check('lock: two enter1 processes started together -> exactly one POST and one enter1 record; the other rc 3',
          sorted(r for r, _ in results) == [0, 3] and len(POSTS) == 1 and len(dirs('enter1')) == 1,
          json.dumps(results))
    enter1_ok()
    fd = hold()
    rc, out = run('w6a2-reboot.py')
    os.close(fd)
    check('lock: reboot while the lock is held -> rc 3, no evidence, no call',
          rc == 3 and 'lock held' in out and not dirs('reboot') and not state().get('rebootCalls'), out)
    reboot_ok()
    edit(simulate_reboot)
    fd = hold()
    rc, out = run('w6a2-observe.py')
    os.close(fd)
    check('lock: observe while the lock is held -> rc 3, nothing written', rc == 3 and not dirs('observe'), out)

    # Major 3: conversations ------------------------------------------------------------------------------
    fresh(lambda st: st.update(convChangeOnPost=True))
    rc, out = run('w6a2-enter.py')
    check('conversations: a changed snapshot after the POST -> rc 12, no NEXT', rc == 12 and 'NEXT' not in out, out)
    enter1_ok()
    edit(lambda st: st['db'].update(conv={'n': 1, 'digest': 'x'}))
    rc, out = run('w6a2-reboot.py')
    check('conversations: reboot refuses a snapshot that differs from enter1 before/after -> rc 3, no call',
          rc == 3 and 'conversation snapshot differs from enter1' in out and not state().get('rebootCalls'), out)
    observe_ok()
    edit(lambda st: st['db'].update(conv={'n': 1, 'digest': 'x'}))
    before = len(POSTS)
    rc, out = run('w6a2-enter.py')
    check('conversations: enter2 refuses a snapshot that differs from the chain -> rc 3, no POST',
          rc == 3 and 'differs from the one the W6 chain recorded' in out and len(POSTS) == before, out)

    # Major 6: the success verdict must be stored -----------------------------------------------------------
    def marker(text):
        MOUNTS['fsyncFailIfContains'] = [text] if text else []
        put_mounts()
    fresh()
    marker('"finished"')
    rc, out = run('w6a2-enter.py')
    marker(None)
    check('record: enter1 passes but its final record cannot be written -> rc 72, session still revoked',
          rc == 72 and not state()['sessions'] and len(dirs('enter1')) == 1, out)
    enter1_ok()
    marker('"finished"')
    rc, out = run('w6a2-reboot.py')
    marker(None)
    check('record: reboot requested but the final record cannot be written -> rc 8, exactly one call',
          rc == 8 and state().get('rebootCalls') == 1, out)
    enter1_ok()
    marker('"rebootCall"')
    rc, out = run('w6a2-reboot.py')
    marker(None)
    check('record: reboot requested but the call result cannot be recorded -> rc 8, exactly one call, not repeated',
          rc == 8 and state().get('rebootCalls') == 1 and 'NOT repeated' in out, out)

    # Question 7: the #64 conditions again after the export ------------------------------------------------
    def during_export(action, name, text):
        enter1_ok()
        edit(lambda st: st.update(journalSleep=3))
        proc = subprocess.Popen(['python3', '-I', INSTALL + '/w6a2-reboot.py'], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, env=dict(os.environ, **ENV))
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not dirs('reboot'):
            time.sleep(0.05)
        action()
        out = proc.communicate(timeout=120)[0].decode()
        check('final look: %s during the export -> rc 5, NO call' % name,
              proc.returncode == 5 and text in out and not state().get('rebootCalls'), out)

    def login():
        os.makedirs('/mock/proc/4343')
        open('/mock/proc/4343/cmdline', 'wb').write(b'codex\0login\0')
    during_export(login, 'a codex login process appears', 'codex-login')
    during_export(lambda: edit(lambda st: st['otherPods'].append(other_pod('shell-pilot', 'p'))),
                  'a Pod appears in shell-pilot', 'shell-pilot')

    # Major 5: results after the window are not accepted -------------------------------------------------
    def usable_late(st):
        st['health'].update(available=True, reason=None, failure=None)
        st['healthSleep'] = 1.5
    fresh(usable_late)
    rc, out = run('w6a2-enter.py', {'W6_HEALTH_WAIT_SECONDS': '1'})
    r = rj('enter1')
    check('deadline: a usable health that returns after the 1 s window is not accepted -> rc 11',
          rc == 11 and r['health']['counts'].get('late') == 1 and 'NEXT' not in out, out)
    fresh(lambda st: st.update(podGetSleep=1.5))
    rc, out = run('w6a2-enter.py', {'W6_POD_WAIT_SECONDS': '1'})
    check('deadline: a Pod proved by a read that returned after the 1 s window -> rc 7', rc == 7, out)

    def immediate(st):
        simulate_reboot(st)
        for step in st['timeline']:
            step['after'] = 1
        st['nodeSleep'] = 2
    reboot_ok()
    edit(immediate)
    rc, out = run('w6a2-observe.py', {'W6_OBSERVE_WAIT_SECONDS': '1'})
    check('deadline: observe whose every condition held only after the 1 s window -> rc 5, not 0',
          rc == 5 and 'returned after the window' in out, out)
    reboot_ok()
    edit(immediate)
    rc, out = run('w6a2-observe.py', {'W6_OBSERVE_WAIT_SECONDS': '20'})
    check('deadline: the same with a 20 s window -> rc 0 (boundary)', rc == 0, out)

    # Blocker 1: three-way Pod lookups -------------------------------------------------------------------
    def ocase(name, fn, want, text, env=None, sim=None):
        reboot_ok()
        edit(lambda st: (simulate_reboot(st, **(sim or {})), fn(st)))
        rc, out = run('w6a2-observe.py', env)
        check('observe lookup: %s -> rc %d' % (name, want), rc == want and text in out, out)

    def stale(st, uid=POD6):
        pod = json.loads(json.dumps(st['pod']))
        pod['metadata']['uid'] = uid
        return pod
    ocase('completed proof, the single GET fails (rc 1, e.g. permission) and a same-name Pod is still listed',
          lambda st: (st['fail'].append('pod-get-fail'), st.update(listExtra=[stale(st)])), 5, 'GET failed')
    ocase('the single GET answers invalid JSON', lambda st: st['fail'].append('pod-get-badjson'), 5, 'GET failed')
    ocase('the single GET times out', lambda st: st.update(podGetSleep=7), 5, 'GET failed',
          {'W6_OBSERVE_WAIT_SECONDS': '6'})
    ocase('the Pod list fails', lambda st: st['fail'].append('pods-list-fail'), 5, 'list failed')
    ocase('the GET says absent but the list still has the same name and UID',
          lambda st: st.update(listExtra=[stale(st)]), 5, 'GET says absent but the list')
    ocase('the list has the same name with another UID', lambda st: st.update(listExtra=[stale(st, POD7)]), 6,
          'in the list')

    # Major 4: terminal content and the time chain ---------------------------------------------------------
    for name, sim, want, text in (
            ('terminal {podAbsent: false}', {'receipt': {'terminal': {'podAbsent': False}}}, 6, 'not terminal evidence'),
            ('terminal of a Running Pod with a running runner',
             {'receipt': {'terminal': {'podPhase': 'Running', 'containers': {'runner': {'state': 'running'}}}}}, 6,
             'not terminal evidence'),
            ('terminal with a non-integer exit code',
             {'receipt': {'terminal': {'podPhase': 'Running', 'containers': {'runner': {'exitCode': 'x', 'reason': 'r'}}}}},
             6, 'not terminal evidence'),
            ('terminal Failed with empty containers (a Pod without containerStatuses)',
             {'receipt': {'terminal': {'podPhase': 'Failed', 'containers': {}}}}, 0, 'G3 at'),
            ('terminal Succeeded with empty containers', {'receipt': {'terminal': {'podPhase': 'Succeeded', 'containers': {}}}},
             0, 'G3 at'),
            ('terminal Running with empty containers', {'receipt': {'terminal': {'podPhase': 'Running', 'containers': {}}}},
             6, 'not terminal evidence'),
            ('terminal {podAbsent: true} (what the controller writes for a vanished Pod)',
             {'receipt': {'terminal': {'podAbsent': True}}}, 0, 'G3 at'),
            ('terminal Running but the runner container terminated',
             {'receipt': {'terminal': {'podPhase': 'Running', 'containers': {
                 'runner': {'exitCode': 255, 'reason': 'Unknown'}, 'shell-executor': {'state': 'running'}}}}}, 0, 'G3 at'),
            ('proof verifiedAt null', {'proof': {'verifiedAt': None}}, 6, 'times are not a valid chain'),
            ('proof verifiedAt before the stop was requested', {'proof': {'verifiedAt': '2020-01-01T00:00:00+00:00'}}, 6,
             'times are not a valid chain'),
            ('completion in the future', {'completed_over': {'completedEpoch': time.time() + 3600}}, 6,
             'times are not a valid chain')):
        ocase(name, lambda st: None, want, text, sim=sim)


def test_terminal_source():
    """terminal_ok accepts every terminal the controller's own terminal_evidence() returns for a dead Pod, and
    rejects what it returns None for (the function is taken from backend/ops by AST, mounted read-only at /ops)."""
    import ast
    import importlib.util
    path = '/ops/runner_controller_service.py'
    check('terminal source: backend/ops is mounted (the harness command mounts it read-only)', os.path.exists(path))
    if not os.path.exists(path):
        return
    tree = ast.parse(open(path).read())
    keep = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name == 'terminal_evidence')
            or (isinstance(n, ast.Assign) and any(getattr(t, 'id', '') == 'TERMINAL_PHASES' for t in n.targets))]
    space = {}
    exec(compile(ast.Module(body=keep, type_ignores=[]), path, 'exec'), space)
    spec = importlib.util.spec_from_file_location('w6observe', INSTALL + '/w6a2-observe.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    run_t = {'name': 'runner', 'state': {'terminated': {'exitCode': 255, 'reason': 'Unknown'}}}
    run_r = {'name': 'runner', 'state': {'running': {}}}
    exe_t = {'name': 'shell-executor', 'state': {'terminated': {'exitCode': 0, 'reason': 'Completed'}}}
    exe_w = {'name': 'shell-executor', 'state': {'waiting': {'reason': 'x'}}}
    dead = [None, {'status': {'phase': 'Failed'}}, {'status': {'phase': 'Succeeded'}},
            {'status': {'phase': 'Failed', 'containerStatuses': [run_t, exe_t]}},
            {'status': {'phase': 'Running', 'containerStatuses': [run_t, exe_w]}},
            {'status': {'phase': 'Running', 'containerStatuses': [run_t]}},
            {'status': {'phase': 'Failed', 'containerStatuses': [run_r, exe_w]}},
            {'status': {'containerStatuses': [{'name': 'runner', 'state': {'terminated': {'reason': 'Unknown'}}}]}}]
    alive = [{'status': {'phase': 'Running'}}, {'status': {'phase': 'Running', 'containerStatuses': [run_r, exe_w]}},
             {'status': {'phase': 'Pending'}}]
    outputs = [space['terminal_evidence'](p) for p in dead]
    check('terminal source: every controller terminal for a dead Pod is accepted by the observer',
          all(o is not None and module.terminal_ok(o) for o in outputs), json.dumps(outputs))
    check('terminal source: the controller gives None for a live Pod (nothing to accept)',
          all(space['terminal_evidence'](p) is None for p in alive))
    check('terminal source: a live-looking shape forged into a receipt is rejected',
          not module.terminal_ok({'podPhase': 'Running', 'containers': {}})
          and not module.terminal_ok({'podPhase': 'Running', 'containers': {'runner': {'state': 'running'}}}))


# Attempt 2: separation from attempt 1 ---------------------------------------------------------------------
def w6_names():
    return sorted(d for d in os.listdir(EVID) if d.startswith('w6-')) if os.path.isdir(EVID) else []


def test_attempt2():
    # The pinned baseline: refused, never adapted.
    def base(**over):
        return lambda st: st['db']['base'].update(over)
    for name, mutate, text in (
            ('the gen-5 stop is another stop', base(stopId='3b15f417-0000-4000-8000-000000000000'), 'stopId'),
            ('the gen-5 stop has a v2 receipt', lambda st: st['db']['base']['receipt'].update(version=2), 'version'),
            ('the gen-5 stop is clean', lambda st: st['db']['base']['receipt']['unavailable'].update(clean=True), 'clean'),
            ('the gen-5 stop has another reason', lambda st: st['db']['base']['receipt']['unavailable'].update(reason='login_failed'), 'reason'),
            ('the gen-5 stop is not completed', base(completed=False), 'completed'),
            ('the gen-5 proof is on another boot', lambda st: st['db']['base']['proof'].update(bootId=B1), 'proofBoot'),
            ('the gen-5 proof is for another Pod', lambda st: st['db']['base']['proof'].update(podUid=POD6), 'proofPod'),
            ('the gen-5 stop has no proof', base(proof=None), 'proofEvidence'),
            ('a sixth stop for the owner', lambda st: st['db'].update(stops=6), 'stops'),
            ('no gen-5 stop at all', lambda st: st['db'].update(base=None), 'stopId'),
            ('the registry is at gen 6 already', lambda st: st['db'].update(generation=6), 'fence'),
            ('the registry is at revision 21', lambda st: st['db'].update(revision=21), 'fence')):
        fresh(mutate)
        rc, out = run('w6a2-enter.py')
        check('a2 baseline: %s -> enter1 rc 3, no session, no POST, no evidence' % name,
              rc == 3 and text in out and not POSTS and not dirs('') and not state()['sessions']
              and not state().get('fakeErrors'), out)
    fresh()
    rc, out = run('w6a2-enter.py')
    check('a2 baseline: the diagnosed state passes (gen 5 rev 20, stop 3b15f417 v3 auth_revoked clean=false on B0, '
          '5 stops) -> rc 0, gen 6', rc == 0 and rj('enter1')['newGeneration'] == 6 and rj('enter1')['attempt'] == 'a2'
          and not state().get('fakeErrors'), out)
    queries = '\n'.join(state()['queries'])
    check('a2 baseline: the stop queries name gen 5 (baseline) and gen 6 only',
          set(re.findall(r'generation=(\d+)', queries)) <= {'5', '6'} and 'generation=5' in queries, queries[-400:])

    # Attempt 1's records must be exactly its one enter1 record.
    def remove_a1():
        shutil.rmtree(os.path.join(EVID, A1_DIR))
    for name, action, text in (
            ('missing', remove_a1, 'attempt-1 records are none'),
            ('joined by a reboot record', lambda: os.mkdir(os.path.join(EVID, 'w6-reboot-20261104T103311Z'), 0o700),
             'attempt-1 records are'),
            ('joined by a stray entry', lambda: os.mkdir(os.path.join(EVID, 'w6-x'), 0o700), 'attempt-1 records are')):
        fresh()
        action()
        rc, out = run('w6a2-enter.py')
        check('a2: attempt-1 record %s -> enter1 rc 3, no POST, nothing written' % name,
              rc == 3 and text in out and not POSTS and not dirs(''), out)
        enter1_ok_after(action)
        rc, out = run('w6a2-reboot.py')
        check('a2: attempt-1 record %s after enter1 -> reboot rc 3, no call' % name,
              rc == 3 and text in out and not state().get('rebootCalls') and not dirs('reboot'), out)
    for name, change in (('another Pod', {'pod': {'uid': POD6, 'startEpoch': 1793786051}}), ('result 6', {'result': 6}),
                         ('gen 4', {'newGeneration': 4})):
        fresh()
        path = os.path.join(EVID, A1_DIR, 'run.json')
        value = json.load(open(path))
        value.update(change)
        json.dump(value, open(path, 'w'))
        rc, out = run('w6a2-enter.py')
        check('a2: the attempt-1 record says %s -> enter1 rc 3, no POST' % name,
              rc == 3 and 'attempt-1 record is not' in out and not POSTS, out)

    # Attempt 1's scripts run by mistake: refused before any session, POST or reboot, and they consume nothing.
    fresh()
    rc, out = run('w6-enter.py')
    check('a1 script by mistake: w6-enter.py on the a2 baseline -> rc 3, no POST, no record of either attempt',
          rc == 3 and not POSTS and w6_names() == [A1_DIR] and not dirs('') and not state()['sessions'], out)
    rc, out = run('w6-reboot.py')
    check('a1 script by mistake: w6-reboot.py with only the a1 record -> rc 3, no call',
          rc == 3 and not state().get('rebootCalls') and w6_names() == [A1_DIR], out)
    rc, out = run('w6a2-enter.py')
    check('a1 script by mistake: a2 enter1 still runs afterwards (rc 0, one POST)', rc == 0 and len(POSTS) == 1, out)
    rc, out = run('w6-enter.py')
    check('a1 script by mistake: w6-enter.py with a1 + a2 records -> rc 3, no second POST',
          rc == 3 and len(POSTS) == 1 and w6_names() == [A1_DIR], out)
    rc, out = run('w6-reboot.py')
    check('a1 script by mistake: w6-reboot.py while the gen-6 Pod runs -> rc 3, no call, no a1 reboot record',
          rc == 3 and not state().get('rebootCalls') and w6_names() == [A1_DIR], out)
    rc, out = run('w6a2-reboot.py')
    check('a1 script by mistake: a2 reboot still runs afterwards (rc 0, exactly one call)',
          rc == 0 and state().get('rebootCalls') == 1, out)
    edit(simulate_reboot)
    rc, out = run('w6-observe.py')
    check('a1 script by mistake: w6-observe.py after the reboot -> rc 3, no a1 observe record',
          rc == 3 and w6_names() == [A1_DIR] and not dirs('observe'), out)
    rc, out = run('w6a2-observe.py')
    check('a1 script by mistake: a2 observe still passes afterwards', rc == 0 and rj('observe')['pass'] is True, out)
    rc, out = run('w6-enter.py')
    check('a1 script by mistake: w6-enter.py after the a2 observe -> rc 3, no POST', rc == 3 and len(POSTS) == 1, out)
    check('a1 scripts by mistake: still exactly one reboot call', state().get('rebootCalls') == 1)

    # Attempt 1's files are never written by attempt 2.
    fresh()
    before = a1_snapshot()
    for script in ('w6a2-enter.py', 'w6a2-reboot.py'):
        rc, out = run(script)
        assert rc == 0, out
    edit(simulate_reboot)
    for script in ('w6a2-observe.py', 'w6a2-enter.py'):
        rc, out = run(script)
        assert rc == 0, out
    check('a2 full chain: the attempt-1 record directory, run.json and w6.lock are byte-, mode- and mtime-identical',
          a1_snapshot() == before and len(before) == 3, json.dumps([before, a1_snapshot()]))
    check('a2 full chain: no new w6-* entry; four w6a2 records', w6_names() == [A1_DIR]
          and [len(dirs(k)) for k in ('enter1', 'reboot', 'observe', 'enter2')] == [1, 1, 1, 1])
    check('a2 full chain: the a2 lock is its own file', os.path.isfile(INSTALL + '/w6a2.lock'))
    check('a2 full chain: every record carries attempt a2',
          all(rj(k).get('attempt') == 'a2' for k in ('enter1', 'reboot', 'observe', 'enter2')))
    check('a2 full chain: no query the fake refused', not state().get('fakeErrors'), state().get('fakeErrors'))

    # Attempt 1's lock held does not block attempt 2, and attempt 2's lock held blocks every a2 step.
    import fcntl
    fresh()
    fd = os.open(INSTALL + '/w6.lock', os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    rc, out = run('w6a2-enter.py')
    os.close(fd)
    check('a2 lock: a held attempt-1 lock is not attempt 2\'s lock (enter1 rc 0)', rc == 0, out)


def enter1_ok_after(action):
    fresh()
    rc, out = run('w6a2-enter.py')
    assert rc == 0, out
    action()


# The chained steps 1+2 command, exactly as the README gives it -----------------------------------------------
def chain_command():
    text = open(SRC + '/README.md').read()
    block = re.search(r'<!-- w6a2-chain -->\n```\n(.*?)\n```', text, re.S).group(1)
    lines = [l for l in block.splitlines() if l.strip()]
    assert len(lines) == 1, lines
    match = re.fullmatch(r"! ssh -i ~/\.ssh/anxious-back-devvm -o IdentitiesOnly=yes -p 22107 opc@127\.0\.0\.1 '([^']*)'",
                         lines[0])
    return match.group(1)


def run_chain(remote, env=None, interrupt_after=None):
    environ = dict(os.environ, **ENV)
    environ.update(env or {})
    proc = subprocess.Popen(['sh', '-c', remote], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=environ)
    if interrupt_after:
        time.sleep(interrupt_after)
        for pid in os.listdir('/proc'):
            try:
                cmd = open('/proc/%s/cmdline' % pid, 'rb').read().split(b'\0')
            except OSError:
                continue
            if cmd[:3] == [b'python3', b'-I', (INSTALL + '/w6a2-enter.py').encode()]:
                os.kill(int(pid), 2)
    out = proc.communicate(timeout=300)[0].decode()
    return proc.returncode, out


def test_chain():
    sudo = '/usr/local/bin/sudo'
    with open(sudo, 'w') as stream:
        stream.write('#!/bin/sh\n[ "$1" = -n ] || exit 64\nshift\nexec "$@"\n')
    os.chmod(sudo, 0o755)
    remote = chain_command()
    shipped = {name: hashlib.sha256(open(os.path.join(SRC, name), 'rb').read()).hexdigest()
               for name in ('w6a2-enter.py', 'w6a2-reboot.py')}
    check('chain: the README command checks the branch hashes of both scripts, enter then reboot',
          all(remote.count(h) == 1 for h in shipped.values())
          and remote.index('w6a2-enter.py') < remote.index('w6a2-reboot.py')
          and remote.index('sha256sum -c') < remote.index('python3'), remote)

    def pointed(text):
        for name, digest in shipped.items():
            text = text.replace(digest, hashlib.sha256(open(os.path.join(INSTALL, name), 'rb').read()).hexdigest())
        return text
    fresh()
    rc, out = run_chain(pointed(remote))
    check('chain happy: hashes OK, enter rc 0, reboot rc 0, remote rc 0, exactly one call',
          rc == 0 and 'w6a2-enter.py: OK' in out and 'w6a2-reboot.py: OK' in out and 'enter rc=0' in out
          and 'reboot rc=0' in out and out.rstrip().endswith('remote rc=0') and state().get('rebootCalls') == 1, out)
    for name, target in (('enter', 'w6a2-enter.py'), ('reboot', 'w6a2-reboot.py')):
        fresh()
        cleared = pointed(remote)  # the hashes of the copies as installed, before the change
        with open(os.path.join(INSTALL, target), 'a') as stream:
            stream.write('# changed\n')
        rc, out = run_chain(cleared)
        check('chain: the installed %s script differs from the cleared hash -> nothing runs, rc nonzero' % name,
              rc != 0 and 'enter rc=' not in out and not POSTS and not dirs('') and not state().get('rebootCalls')
              and 'remote rc=%d' % rc in out, out)
    for name, mutate, marker_text, want in (
            ('a refused precheck', lambda st: st['db'].update(revision=21), None, 3),
            ('a concrete health failure', lambda st: st['health'].update(reason='login_failed', failure='login_failed'), None, 9),
            ('a failed session revoke', lambda st: st['fail'].append('revoke-fail'), None, 70),
            ('an unwritten final record', None, '"finished"', 72)):
        fresh(mutate)
        if marker_text:
            MOUNTS['fsyncFailIfContains'] = [marker_text]
            put_mounts()
        rc, out = run_chain(pointed(remote))
        MOUNTS['fsyncFailIfContains'] = []
        put_mounts()
        check('chain: enter returns %d (%s) -> reboot never runs, remote rc %d' % (want, name, want),
              rc == want and 'enter rc=%d' % want in out and 'reboot rc=' not in out and not dirs('reboot')
              and not state().get('rebootCalls') and out.rstrip().endswith('remote rc=%d' % want), out)
    fresh(lambda st: st.update(healthSleep=3))
    rc, out = run_chain(pointed(remote), {'W6_HEALTH_WAIT_SECONDS': '20'}, interrupt_after=4)
    check('chain: enter interrupted (SIGINT) -> nonzero, reboot never runs, the code is the remote rc',
          rc != 0 and 'enter rc=%d' % rc in out and 'reboot rc=' not in out and not state().get('rebootCalls'), out)
    fresh()
    edit(lambda st: st.update(finalChange={'pod': '00000000-0000-4000-8000-0000000000ee'}))
    rc, out = run_chain(pointed(remote))
    check('chain: enter rc 0, reboot refuses at the final look -> remote rc 5, no call',
          rc == 5 and 'enter rc=0' in out and 'reboot rc=5' in out and not state().get('rebootCalls'), out)
    fresh()
    edit(lambda st: st.update(dbSkew=30))
    rc, out = run_chain(pointed(remote))
    check('chain: the reboot script keeps its own precheck (clock skew) -> remote rc 3, no call',
          rc == 3 and 'enter rc=0' in out and 'reboot rc=3' in out and not state().get('rebootCalls'), out)
def main():
    install_tools()
    fresh()
    for name, fn in (('enter1', test_enter1), ('reboot', test_reboot), ('observe', test_observe), ('enter2', test_enter2),
                     ('round2', test_round2), ('terminal source', test_terminal_source), ('attempt 2', test_attempt2),
                     ('chain', test_chain)):
        try:
            fn()
        except Exception as error:  # a broken fixture is a failure, not a crash of the harness
            check('%s block ran to the end' % name, False, '%s: %s' % (type(error).__name__, error))
    print('%d passed, %d failed' % (passed, failed))
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
