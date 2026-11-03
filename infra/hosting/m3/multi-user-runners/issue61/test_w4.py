"""Mock harness for the #61 W4 scripts (w4-enter.py, w4-observe.py). Docker only, never on a real node:
    docker run --rm -v "$PWD/infra/hosting/m3:/m3:ro" python:3.9-alpine \
      python3 -I /m3/multi-user-runners/issue61/test_w4.py
mock/fake_w4.py stands in for k3s kubectl/crictl. The API POST is served here by a local HTTP server that checks,
like the API, that the cookie's session row exists (origin-bound hash), the CSRF header matches it, and the
Idempotency-Key is a UUID. The installed w4-enter.py is the branch file with only API_HOST/API_PORT pointed at it.
"""
import copy
import hashlib
import http.server
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid

SRC = '/m3/multi-user-runners/issue61'
INSTALL = '/root/issue61'
OWNER = '00000000-0000-4000-8000-000000005804'
STATE = '5ff583e9-abe1-4ef8-b277-4a5afead6b80'
POD_NAME = 'runner-' + OWNER.replace('-', '')
RUNNER = 'docker.io/library/anxious-dev-runner@sha256:067c77de5d6b3673ece9b243f7753a9184a007cd44ab0ea1c93dc3044458a5e4'
EXECUTOR = 'docker.io/library/anxious-dev-shell-executor@sha256:9c87d626cdf6798db5bc5a562cc68af1b13a36c8f90d3738e19c6553e7cf4d12'
RUNNER_ID = 'sha256:4cac37e2173d5aec7b351e05c2977f2e0df76670f3befb91ce54306d2444e8ad'
EXECUTOR_ID = 'sha256:49b449cb31c5b344f876a9d9aee2d97dd3dcae906c3324bc0eaf00678dd6b883'
POD_UID = '7c0d2b8e-1111-4222-8333-444455556666'
CID = {'runner': 'a' * 64, 'shell-executor': 'b' * 64}
SANDBOX = 'c' * 64
PLANS = json.load(open(SRC + '/mock/w3b-plans.json'))
passed = failed = 0
POSTS = []  # (headers, body) of every POST the server saw
PORT = 18080


def check(name, condition, detail=''):
    global passed, failed
    if condition:
        passed += 1
        print('ok   ' + name)
    else:
        failed += 1
        print('FAIL ' + name + (' (' + detail[-1500:] + ')' if detail else ''))


def moved_plan():
    plan = copy.deepcopy(PLANS[OWNER])
    plan['image'] = RUNNER
    plan['executor'] = dict(plan['executor'], image=EXECUTOR)
    return plan


def pod_template():
    return {
        'metadata': {'name': POD_NAME, 'namespace': 'codex-runner', 'uid': POD_UID,
                     'labels': {'anxious.io/owner': OWNER, 'anxious.io/state': STATE, 'anxious.io/generation': '4',
                                'anxious.io/managed': 'user-runner'}},
        'spec': {'serviceAccountName': 'runner-workload', 'automountServiceAccountToken': False,
                 'initContainers': [{'name': 'prepare-shell', 'image': RUNNER,
                                     'volumeMounts': [{'name': 'native', 'mountPath': '/state'}]}],
                 'containers': [
                     {'name': 'runner', 'image': RUNNER, 'volumeMounts': [
                         {'name': 'native', 'mountPath': '/state'}, {'name': 'control', 'mountPath': '/control'},
                         {'name': 'broker-token', 'mountPath': '/run/codex-broker', 'readOnly': True},
                         {'name': 'tmp', 'mountPath': '/tmp'}]},
                     {'name': 'shell-executor', 'image': EXECUTOR, 'volumeMounts': [
                         {'name': 'native', 'mountPath': '/workspace', 'subPath': 'shell-work'}]}],
                 'volumes': [{'name': 'native', 'persistentVolumeClaim': {'claimName': 'native-x'}},
                             {'name': 'control', 'persistentVolumeClaim': {'claimName': 'control-x'}},
                             {'name': 'broker-token', 'projected': {'defaultMode': 292, 'sources': [
                                 {'serviceAccountToken': {'audience': 'codex-broker', 'expirationSeconds': 600,
                                                          'path': 'token'}}]}},
                             {'name': 'tmp', 'emptyDir': {'medium': 'Memory'}}]},
        'status': {'phase': 'Running', 'startTime': '2026-11-04T01:00:00Z',
                   'initContainerStatuses': [{'name': 'prepare-shell', 'imageID': RUNNER,
                                              'state': {'terminated': {'exitCode': 0}}}],
                   'containerStatuses': [
                       {'name': n, 'imageID': ref, 'containerID': 'containerd://' + CID[n], 'restartCount': 0,
                        'image': ref.split('@')[0] + ':61-5e73bc4',
                        'state': {'running': {'startedAt': '2026-11-04T01:00:0%dZ' % i}}}
                       for i, (n, ref) in enumerate((('runner', RUNNER), ('shell-executor', EXECUTOR)), start=2)]}}


def base_state():
    return {
        'deployments': {'runner-control/runner-controller': {'uid': '41564f26-95e7-4ad1-8af3-d73b93c4669a', 'replicas': 1,
                                                             'ready': 1},
                        'm2-hosting/api': {'uid': 'fad6799a-6df5-4086-812f-cfae6212adfa', 'replicas': 1, 'ready': 1}},
        'db': {'state': STATE, 'phase': 'idle_stopped', 'generation': 3, 'revision': 12, 'pod': None, 'active': 0,
               'ambiguous': False, 'bindingState': STATE, 'plan': moved_plan(), 'role': 'editor', 'approved': True,
               'allOpenStops': 0, 'entries': 0, 'admissions': 0, 'outbox': 0, 'waiting': 0, 'liveSessions': 0},
        'conv': {'n': 0, 'digest': '-'}, 'sessions': {}, 'fail': [], 'postMode': '202', 'podAfter': 2,
        'podTemplate': pod_template(),
        'images': {RUNNER: RUNNER_ID, EXECUTOR: EXECUTOR_ID},
        # The shapes observed on the guest (2026-11-03T16:15Z): inspect imageRef = repo@manifest reference,
        # ps --id imageRef = config ID, both in the same sandbox.
        'cri': {CID[n]: {'status': {'imageRef': ref, 'labels': {'io.kubernetes.pod.uid': POD_UID,
                                                                'io.kubernetes.pod.namespace': 'codex-runner',
                                                                'io.kubernetes.container.name': n}},
                         'info': {'sandboxID': SANDBOX}}
                for n, ref in (('runner', RUNNER), ('shell-executor', EXECUTOR))},
        'ps': {CID[n]: [{'id': CID[n], 'imageRef': cfg, 'podSandboxId': SANDBOX}]
               for n, cfg in (('runner', RUNNER_ID), ('shell-executor', EXECUTOR_ID))},
        'sandboxes': {SANDBOX: {'status': {'metadata': {'uid': POD_UID, 'name': POD_NAME, 'namespace': 'codex-runner'}}}},
        'health': {'ownerId': OWNER, 'stateId': STATE, 'podUid': POD_UID, 'available': False, 'reason': 'auth_revoked',
                   'verification': 'unverified', 'failure': 'auth_revoked', 'model': 'gpt-6.1-sol'},
    }


def state():
    return json.load(open('/mock/w4.json'))


def put(st):
    json.dump(st, open('/mock/w4.json', 'w'))


class Api(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('content-length', 0)))
        POSTS.append((dict(self.headers), body))
        st = state()
        mode = st.get('postMode')
        if mode == 'slow':
            time.sleep(8)
        if mode == 'timeout':
            time.sleep(6)
            return
        cookie = self.headers.get('cookie', '')
        token = cookie[len('anxious_session='):] if cookie.startswith('anxious_session=') else ''
        token_hash = hashlib.sha256(('m2-origin-session-v1\0http://192.88.99.1\0' + token).encode()).hexdigest()
        row = st['sessions'].get(token_hash)
        try:
            uuid.UUID(self.headers.get('idempotency-key', ''))
            key_ok = True
        except ValueError:
            key_ok = False
        # As backend/app/user_runners.py: a session (401), then Origin == settings.origin, then CSRF (403).
        if row is None or row['owner'] != OWNER:
            self.reply(401, {'error': {'code': 'unauthenticated', 'message': 'x'}})
            return
        if self.headers.get('origin') != 'http://192.88.99.1':
            self.reply(403, {'error': {'code': 'origin_not_allowed', 'message': 'x'}})
            return
        if row['csrf'] != self.headers.get('x-csrf-token'):
            self.reply(403, {'error': {'code': 'csrf_invalid', 'message': 'x'}})
            return
        ok = (self.path == '/api/research/codex/runner' and self.headers.get('host') == '192.88.99.1' and body == b'{}'
              and key_ok)
        if mode == '403' or not ok:
            self.reply(403 if mode == '403' else 422, {'error': {'code': 'forbidden', 'message': 'x'}})
            return
        st['entered'] = True
        st['db'].update(phase='provisioning', generation=4, revision=13)
        put(st)
        if mode == 'lost':
            self.connection.close()
            return
        self.reply(202, {'available': False, 'reason': 'provisioning', 'retryAfterSeconds': 2, 'verification': 'unverified',
                         'model': 'gpt-6.1-sol'})

    def reply(self, status, value):
        data = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('content-type', 'application/json')
        self.send_header('content-length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def install():
    for tool, path in (('k3s', '/usr/local/bin/k3s'),):
        with open(path, 'w') as stream:
            stream.write('#!/bin/sh\nexec python3 -I %s/mock/fake_w4.py %s "$@"\n' % (SRC, tool))
        os.chmod(path, 0o755)
    server = http.server.ThreadingHTTPServer(('127.0.0.1', PORT), Api)
    threading.Thread(target=server.serve_forever, daemon=True).start()


def setup(mutate=None, fail=(), mode='202'):
    for path in ('/mock', INSTALL):
        shutil.rmtree(path, ignore_errors=True)
        os.makedirs(path, mode=0o700)
    text = open(SRC + '/w4-enter.py').read()
    pointed = text.replace("API_HOST = '10.0.0.107'\n", "API_HOST = '127.0.0.1'\n").replace(
        'API_PORT = 80\n', 'API_PORT = %d\n' % PORT)
    assert pointed.count('127.0.0.1') == 1 and len(pointed.splitlines()) == len(text.splitlines())
    open(INSTALL + '/w4-enter.py', 'w').write(pointed)
    shutil.copy(SRC + '/w4-observe.py', INSTALL)
    st = base_state()
    st['fail'], st['postMode'] = list(fail), mode
    if mutate:
        mutate(st)
    put(st)
    del POSTS[:]


def enter(env=None, signal_after=None):
    environ = dict(os.environ, W4_POD_WAIT_SECONDS='4', W4_POST_TIMEOUT='3')
    environ.update(env or {})
    proc = subprocess.Popen(['python3', '-I', INSTALL + '/w4-enter.py'], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, env=environ)
    if signal_after:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not POSTS:
            time.sleep(0.05)
        time.sleep(signal_after)
        proc.send_signal(signal.SIGTERM)
    out, _ = proc.communicate(timeout=300)
    return proc.returncode, out.decode()


def evidence():
    root = INSTALL + '/evidence'
    dirs = sorted(d for d in os.listdir(root) if d.startswith('w4-')) if os.path.isdir(root) else []
    return os.path.join(root, dirs[-1]) if dirs else None


def run_json():
    ev = evidence()
    return json.load(open(os.path.join(ev, 'run.json'))) if ev else None


def secrets_seen():
    """The token and CSRF the server received (empty if no POST)."""
    found = set()
    for headers, _ in POSTS:
        cookie = headers.get('Cookie') or headers.get('cookie') or ''
        found.add(cookie.split('=', 1)[-1])
        found.add(headers.get('x-csrf-token') or headers.get('X-Csrf-Token') or '')
    return {s for s in found if s}


def common(name, rc, out, want, posts):
    st = state()
    check(name, rc == want, 'rc %s want %s; %s' % (rc, want, out))
    check(name + ': POSTs %d' % posts, len(POSTS) == posts, str(len(POSTS)))
    check(name + ': no session row left', not st['sessions'], str(st['sessions']))
    leaked = [s for s in secrets_seen() if s in out or s in open('/mock/w4-calls.log').read()
              or (evidence() and s in open(os.path.join(evidence(), 'run.json')).read())]
    check(name + ': token/CSRF never in output, argv or evidence', not leaked)
    return st


def observe(*args, env=None):
    environ = dict(os.environ, W4_POLL_SECONDS='1')
    environ.update(env or {})
    done = subprocess.run(['python3', '-I', INSTALL + '/w4-observe.py'] + list(args), stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, timeout=300, env=environ)
    return done.returncode, done.stdout.decode()


def released(st, when=None, **over):
    receipt = {'version': 3, 'podUid': POD_UID, 'ownerId': OWNER, 'stateId': STATE, 'generation': 4, 'stopId': 's4',
               'unavailable': {'reason': 'auth_revoked', 'clean': False}}
    receipt.update(over.pop('receipt', {}))
    # 2026-11-04T01:31:00Z, inside the entry deadline 02:00:00Z.
    stop = {'stopId': 's4', 'owner': OWNER, 'stateId': STATE, 'generation': 4, 'pod': POD_UID,
            'completed': '2026-11-04T01:31:00.20196+00:00', 'completedEpoch': 1793755860.20196, 'receipt': receipt,
            'proof': {'podUid': POD_UID, 'evidence': 'pod-deleted', 'verifiedAt': '2026-11-04T01:31:00.210210+00:00'}}
    if when is not None:  # a completion at epoch `when` (for deadlines relative to the real clock)
        iso = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(when))
        stop.update(completed=iso + '.5+00:00', completedEpoch=when,
                    proof={'podUid': POD_UID, 'evidence': 'pod-deleted', 'verifiedAt': iso + '.600000+00:00'})
    stop.update(over.pop('stop', {}))
    st['stopGen4'] = stop
    st['db'].update(phase='idle_stopped', pod=None, revision=15)
    st['pod'] = None
    st['entered'] = False
    for k, v in over.items():
        st[k] = v


def main():
    install()

    # The mock API enforces the route's order: no session 401, wrong/missing Origin 403, bad CSRF 403 ----------
    import http.client as hc
    setup()
    st = state()
    token = 'T' * 43
    token_hash = hashlib.sha256(('m2-origin-session-v1\0http://192.88.99.1\0' + token).encode()).hexdigest()
    st['sessions'][token_hash] = {'owner': OWNER, 'csrf': 'C' * 43}
    put(st)
    for name, extra, want in (('no Origin', {}, 403), ('another Origin', {'Origin': 'http://evil'}, 403),
                              ('a bad CSRF', {'Origin': 'http://192.88.99.1', 'x-csrf-token': 'x'}, 403)):
        c = hc.HTTPConnection('127.0.0.1', PORT, timeout=5)
        headers = {'Host': '192.88.99.1', 'Cookie': 'anxious_session=' + token, 'x-csrf-token': 'C' * 43,
                   'Idempotency-Key': str(uuid.uuid4()), 'Content-Type': 'application/json'}
        headers.update(extra)
        c.request('POST', '/api/research/codex/runner', body=b'{}', headers=headers)
        status = c.getresponse().status
        c.close()
        check('mock API: %s is refused %d' % (name, want), status == want, str(status))
    c = hc.HTTPConnection('127.0.0.1', PORT, timeout=5)
    c.request('POST', '/api/research/codex/runner', body=b'{}', headers={'Host': '192.88.99.1', 'Origin': 'http://192.88.99.1'})
    status = c.getresponse().status
    c.close()
    check('mock API: no session is 401', status == 401, str(status))

    # Happy path -----------------------------------------------------------------------------------------
    setup()
    rc, out = enter()
    st = common('happy', rc, out, 0, 1)
    headers = POSTS[0][0] if POSTS else {}
    key = headers.get('Idempotency-Key') or headers.get('idempotency-key')
    check('happy: one POST with Host, cookie, CSRF and a UUID Idempotency-Key, printed', key and key in out, out)
    check('happy: the POST carries Origin http://192.88.99.1 (the route requires it)',
          (headers.get('Origin') or headers.get('origin')) == 'http://192.88.99.1', str(headers.get('Origin')))
    check('happy: the session row was revoked (exact hash, owner)', len(st.get('revoked', [])) == 1, out)
    rj = run_json()
    check('happy: evidence has the Pod UID, start, deadline = start + 60 min and r1State true',
          rj and (rj.get('pod') or {}).get('uid') == POD_UID and (rj.get('pod') or {}).get('deadline') == '2026-11-04T02:00:00Z'
          and (rj.get('health') or {}).get('r1State') is True and rj.get('result') == 0
          and (rj.get('session') or {}).get('revoked') is True, json.dumps(rj))
    check('happy: prints the proof, the sanitized health with r1_state=true, the ledger line, conversations unchanged',
          'proof ok' in out and 'r1_state=true' in out and '5804 ledger blocked=auth_revoked' in out
          and 'unchanged' in out, out)
    check('happy: read-only queries are READ ONLY, writes only insert/revoke',
          all(q.startswith('BEGIN READ ONLY;') or re.search(r'/\*w4w:(insert|revoke)\*/', q) for q in st['queries']))
    check('happy: the health probe reads this Pod only', POD_UID in (st.get('healthProbes') or [''])[0])
    check('happy: evidence directory is 0700', oct(os.stat(evidence()).st_mode & 0o777) == '0o700')
    rc, out = enter()
    check('rerun refuses at precheck (evidence exists), no POST', rc == 3 and 'never rerun' in out and len(POSTS) == 1, out)

    # Observation (on a well-formed entry record, independent of the entry script's result) -----------------
    ev = INSTALL + '/evidence/w4-observe-fixture'
    os.makedirs(ev, mode=0o700, exist_ok=True)
    json.dump({'result': 0, 'proof': {'ok': True}, 'post': {'idempotencyKey': str(uuid.uuid4()), 'status': 202},
               'pod': {'uid': POD_UID, 'start': '2026-11-04T01:00:00Z', 'deadline': '2026-11-04T02:00:00Z'},
               'before': {'conversations': {'n': 0, 'digest': '-'}}, 'session': {'id': 'x', 'revoked': True}},
              open(os.path.join(ev, 'run.json'), 'w'))
    st = state(); st['pod'] = pod_template(); st['entered'] = False; st['db'].update(generation=4, revision=14,
                                                                                     phase='ready', pod=POD_UID)
    st['conv'] = {'n': 0, 'digest': '-'}; st['fail'] = []; put(st)
    rc, out = observe(ev)
    check('observe: running, no stop yet: pending (rc 5)', rc == 5 and 'pending' in out, out)
    st = state(); released(st); put(st)
    rc, out = observe(ev)
    check('observe: v3 auth_revoked, pod-deleted, released, Pod gone, conversations same: R1 done (rc 0)',
          rc == 0 and 'R1 done' in out, out)
    check('observe: only READ ONLY queries', all(q.startswith('BEGIN READ ONLY;') for q in state()['queries']
                                                 if '/*w4o:' in q))
    for name, over in (('a v1 clean drain receipt', {'receipt': {'version': 1}}),
                       ('a v4 operator receipt', {'receipt': {'version': 4}}),
                       ('another unavailable reason', {'receipt': {'unavailable': {'reason': 'auth_expired', 'clean': False}}}),
                       ('the proof for another Pod', {'stop': {'proof': {'podUid': 'other', 'evidence': 'pod-deleted'}}}),
                       ('a stop for another Pod', {'stop': {'pod': 'other'}})):
        st = state(); released(st, **copy.deepcopy(over)); put(st)
        rc, out = observe(ev)
        check('observe: %s is R1 not done (rc 4)' % name, rc == 4 and 'R1 not done' in out, out)
    st = state(); released(st); st['pod'] = pod_template(); put(st)
    rc, out = observe(ev)
    check('observe: Pod still there is not done', rc == 4 and '(the W4 Pod)' in out, out)
    st = state(); released(st); st['conv'] = {'n': 1, 'digest': 'x'}; put(st)
    rc, out = observe(ev)
    check('observe: changed conversations are not done', rc == 4 and 'conversations changed' in out, out)
    st = state(); st['conv'] = {'n': 0, 'digest': '-'}; put(st)
    for name, over, why in (
            ('a stop/receipt stopId mismatch', {'receipt': {'stopId': 'other'}}, 'receipt linkage'),
            ('a receipt for another state', {'receipt': {'stateId': 'other'}}, 'receipt linkage'),
            ('a receipt for another generation', {'receipt': {'generation': 3}}, 'receipt linkage'),
            ('a stop row for another state', {'stop': {'stateId': 'other'}}, 'is not this owner/state/Pod'),
            ('a completion after the deadline', {'stop': {'completed': '2026-11-04T03:00:00+00:00',
                                                          'completedEpoch': 1793761200.0}}, 'kept as evidence'),
            ('a proof verified after the deadline', {'stop': {'proof': {
                'podUid': POD_UID, 'evidence': 'pod-deleted', 'verifiedAt': '2026-11-04T03:00:00.000001+00:00'}}},
             'not at or before the deadline'),
            ('a proof time without UTC offset', {'stop': {'proof': {
                'podUid': POD_UID, 'evidence': 'pod-deleted', 'verifiedAt': '2026-11-04T01:31:00'}}},
             'not at or before the deadline')):
        st = state(); released(st, **copy.deepcopy(over)); put(st)
        rc, out = observe(ev)
        check('observe: %s is R1 not done (rc 4) for that reason only' % name,
              rc == 4 and why in out and 'conversations changed' not in out, out)
    st = state(); released(st); st['pod'] = dict(pod_template(), metadata=dict(pod_template()['metadata'], uid='other-live'))
    put(st)
    rc, out = observe(ev)
    check('observe: a replacement Pod by name is not done and named as a REPLACEMENT',
          rc == 4 and 'REPLACEMENT, uid other-live' in out, out)
    st = state(); released(st); st['db']['allOpenStops'] = 1; put(st)
    rc, out = observe(ev)
    check('observe: an open stop is not done', rc == 4 and 'open stop' in out, out)
    st = state(); st['db']['allOpenStops'] = 0; released(st); put(st)
    good = json.load(open(os.path.join(ev, 'run.json')))
    for name, change in (('entry proof failed (result 8)', {'result': 8, 'proof': {'ok': False, 'problems': ['x']}}),
                         ('entry proof missing (result 6)', {'result': 6, 'proof': None}),
                         ('entry early health not R1 (result 9)', {'result': 9}),
                         ('entry POST status 403', {'post': dict(good['post'], status=403)}),
                         ('entry without Idempotency-Key', {'post': dict(good['post'], idempotencyKey=None)}),
                         ('entry deadline not Pod start + 60 min', {'pod': dict(good['pod'], deadline='2026-11-04T03:00:00Z')})):
        bad_entry = dict(good, **change)
        json.dump(bad_entry, open(os.path.join(ev, 'run.json'), 'w'))
        rc, out = observe(ev)
        check('observe: %s never certifies, even with a matching stop (rc 6, diagnostic)' % name,
              rc == 6 and 'DOES NOT certify' in out and 'R1 done' not in out, out)
    json.dump(good, open(os.path.join(ev, 'run.json'), 'w'))
    rc, out = observe(ev)
    check('observe: the restored good entry certifies again (control)', rc == 0 and 'R1 done' in out, out)
    st = state(); st['stopGen4'] = None; st['conv'] = {'n': 0, 'digest': '-'}; put(st)
    rj = json.load(open(os.path.join(ev, 'run.json')))
    rj['pod']['deadline'] = '2026-01-01T00:00:00Z'
    rj['pod']['start'] = '2025-12-31T23:00:00Z'
    json.dump(rj, open(os.path.join(ev, 'run.json'), 'w'))
    rc, out = observe(ev, '--wait')
    check('observe: --wait past the deadline ends at once: R1 not done at the deadline (rc 5)',
          rc == 5 and 'at the deadline' in out and 'nothing is extended' in out, out)
    soon = int(time.time()) + 120
    rj['pod']['deadline'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(soon))
    rj['pod']['start'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(soon - 3600))
    json.dump(rj, open(os.path.join(ev, 'run.json'), 'w'))
    st = state(); st['stopGen4'] = None; put(st)

    def later():
        time.sleep(2.5)
        s = state(); released(s, when=time.time()); put(s)
    threading.Thread(target=later).start()
    start = time.monotonic()
    rc, out = observe(ev, '--wait')
    check('observe: --wait polls until the release, then R1 done', rc == 0 and time.monotonic() - start < 30, out)
    soon = int(time.time()) + 4
    rj['pod']['deadline'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(soon))
    rj['pod']['start'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(soon - 3600))
    json.dump(rj, open(os.path.join(ev, 'run.json'), 'w'))
    st = state(); st['stopGen4'] = None; st['fail'] = ['slow-db']; put(st)
    start = time.monotonic()
    rc, out = observe(ev, '--wait')
    took = time.monotonic() - start
    check('observe: --wait with slow lookups ends at the deadline, bounded (no extension)',
          rc == 5 and 'at the deadline' in out and took < 25, '%.1f s %s' % (took, out))
    st = state(); st['fail'] = []; put(st)
    rc, out = observe('/nonexistent')
    check('observe: unreadable evidence is rc 3', rc == 3, out)
    st = state(); st['fail'] = ['db-fail-state']; put(st)
    rc, out = observe(ev)
    check('observe: a failed lookup is rc 3, nothing concluded', rc == 3, out)

    # Precheck refusals: rc 3, no session, no POST, no evidence -----------------------------------------
    def pre(name, mutate):
        setup(mutate)
        rc, out = enter()
        st = common('precheck: ' + name, rc, out, 3, 0)
        check('precheck: %s: no session created, no evidence' % name,
              not [q for q in st.get('queries', []) if '/*w4w:' in q] and evidence() is None, out)
    pre('controller is another Deployment', lambda s: s['deployments']['runner-control/runner-controller'].update(uid='x'))
    pre('api not ready', lambda s: s['deployments']['m2-hosting/api'].update(ready=0))
    pre('another runner Pod exists', lambda s: s.update(otherPods=['pod/runner-other']))
    for field, value in (('revision', 13), ('generation', 4), ('phase', 'ready'), ('pod', 'p'), ('active', 1),
                         ('ambiguous', True), ('state', 'other'), ('bindingState', 'other'), ('allOpenStops', 1),
                         ('entries', 1), ('admissions', 1), ('outbox', 1), ('waiting', 1), ('liveSessions', 1),
                         ('role', 'viewer'), ('approved', False)):
        pre('…5804 %s=%s' % (field, value), lambda s, f=field, v=value: s['db'].update({f: v}))
    pre('plan is the old one', lambda s: s['db'].update(plan=PLANS[OWNER]))
    pre('API origin differs', lambda s: s.update(origin='https://m2.invalid|'))
    pre('runner target not resolving', lambda s: s['images'].pop(RUNNER))
    pre('database fails', lambda s: s['fail'].append('db-fail-pre'))

    # Session ----------------------------------------------------------------------------------------------
    setup(fail=['db-fail-insert'])
    rc, out = enter()
    common('session: insert fails', rc, out, 4, 0)
    setup(fail=['insert-lost'])
    rc, out = enter()
    st = common('session: insert committed but its answer lost', rc, out, 4, 0)
    check('session: the lost row is found and removed', 'row found' in out and len(st.get('revoked', [])) == 1, out)
    setup(fail=['revoke-fail'])
    rc, out = enter()
    st = state()
    check('session: revoke failing after a good run is rc 70 and says so',
          rc == 70 and 'SESSION NOT PROVED REVOKED' in out and len(POSTS) == 1, out)

    # POST outcomes ------------------------------------------------------------------------------------------
    setup(mode='403')
    rc, out = enter()
    st = common('post: refused 403', rc, out, 5, 1)
    check('post: refused: no Pod wait, live state printed', 'live (read-only)' in out and 'pod: absent' in out, out)
    setup(mode='timeout')
    rc, out = enter()
    common('post: timeout is unknown, never repeated', rc, out, 6, 1)
    setup(mode='lost')
    rc, out = enter()
    st = common('post: accepted but the answer lost', rc, out, 6, 1)
    check('post: lost answer: live state shows the entered generation 4', '"generation": 4' in out, out)
    setup(fail=['no-pod'])
    rc, out = enter()
    st = common('pod: never Running within the wait', rc, out, 7, 1)
    check('pod: rc 7 says provisioning may continue, no retry/reclaim', 'no retry' in out and 'live (read-only)' in out, out)

    # Instance / image proof (rc 8, Pod left as is) ------------------------------------------------------
    def bad(name, mutate):
        setup(mutate)
        rc, out = enter()
        st = common('proof: ' + name, rc, out, 8, 1)
        check('proof: %s: no health probe run, Pod left' % name, not st.get('healthProbes') and st.get('pod'), out)

    def status_of(s, n):
        return [c for c in s['podTemplate']['status']['containerStatuses'] if c['name'] == n][0]
    bad('imageID reported as the config ID', lambda s: status_of(s, 'runner').update(imageID=RUNNER_ID))
    bad('imageID by tag', lambda s: status_of(s, 'shell-executor').update(imageID=EXECUTOR.split('@')[0] + ':61-5e73bc4'))
    bad('spec executor image is the old one', lambda s: s['podTemplate']['spec']['containers'][1].update(
        image='docker.io/library/anxious-dev-shell-executor@sha256:' + '5' * 64))
    bad('init container on another image', lambda s: s['podTemplate']['spec']['initContainers'][0].update(image=EXECUTOR))
    bad('CRI inspect imageRef is the config ID, not the manifest reference',
        lambda s: s['cri'][CID['runner']]['status'].update(imageRef=RUNNER_ID))
    bad('CRI inspect imageRef is another reference', lambda s: s['cri'][CID['runner']]['status'].update(
        imageRef=RUNNER.split('@')[0] + '@sha256:' + '0' * 64))
    bad('CRI ps lists no container for the ID', lambda s: s['ps'].update({CID['runner']: []}))
    bad('CRI ps lists two containers', lambda s: s['ps'][CID['runner']].append(dict(s['ps'][CID['runner']][0])))
    bad('CRI ps container in another sandbox', lambda s: s['ps'][CID['runner']][0].update(podSandboxId='d' * 64))
    bad('CRI container belongs to another Pod', lambda s: s['cri'][CID['runner']]['status']['labels'].update(
        {'io.kubernetes.pod.uid': 'other'}))
    bad('sandbox is another Pod', lambda s: s['sandboxes'][SANDBOX]['status']['metadata'].update(uid='other'))
    bad('containerID not containerd', lambda s: status_of(s, 'runner').update(containerID='docker://' + 'a' * 64))
    bad('executor CRI ps imageRef is another config', lambda s: s['ps'][CID['shell-executor']][0].update(
        imageRef='sha256:' + '1' * 64))
    bad('a container restarted', lambda s: status_of(s, 'runner').update(restartCount=1))
    bad('init exited 1', lambda s: s['podTemplate']['status']['initContainerStatuses'][0].update(
        state={'terminated': {'exitCode': 1}}))
    bad('automount true', lambda s: s['podTemplate']['spec'].update(automountServiceAccountToken=True))
    bad('kube-api-access volume', lambda s: s['podTemplate']['spec']['volumes'].append({'name': 'kube-api-access-x'}))
    bad('broker token for another audience', lambda s: s['podTemplate']['spec']['volumes'][2]['projected']['sources'][0][
        'serviceAccountToken'].update(audience='kubernetes'))
    bad('broker token writable', lambda s: s['podTemplate']['spec']['containers'][0]['volumeMounts'][2].update(readOnly=False))
    bad('a third container', lambda s: s['podTemplate']['spec']['containers'].append({'name': 'extra', 'image': RUNNER}))

    # Health -------------------------------------------------------------------------------------------------
    setup(fail=['health-fail'])
    rc, out = enter()
    common('health: not observed is rc 0 with r1_state=unobserved', rc, out, 0, 1)
    check('health: unobserved printed', 'r1_state=unobserved' in out, out)
    for name, over in (('available', {'available': True, 'reason': None, 'failure': None}),
                       ('another failure', {'failure': 'auth_expired'}),
                       ('another Pod', {'podUid': 'other'})):
        setup(lambda s, o=over: s['health'].update(o))
        rc, out = enter()
        st = common('health: %s is not the R1 state (rc 9)' % name, rc, out, 9, 1)
        check('health: %s: runner left running' % name, st.get('pod') and 'r1_state=false' in out, out)

    # Interrupt and lost stdout ------------------------------------------------------------------------------
    setup(mode='slow')
    rc, out = enter(env={'W4_POST_TIMEOUT': '20'}, signal_after=1)
    common('interrupt during the POST', rc, out, 6, 1)
    setup()
    environ = dict(os.environ, W4_POD_WAIT_SECONDS='4', W4_POST_TIMEOUT='3')
    proc = subprocess.Popen(['python3', '-I', INSTALL + '/w4-enter.py'], stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, env=environ)
    for raw in proc.stdout:
        if b'session created' in raw:
            break
    proc.stdout.close()
    rc = proc.wait(timeout=120)
    st = state()
    check('stdout closed after the session line: run completes (rc 0), session revoked',
          rc == 0 and not st['sessions'] and len(POSTS) == 1, 'rc %s' % rc)

    print('passed %d, failed %d' % (passed, failed))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
