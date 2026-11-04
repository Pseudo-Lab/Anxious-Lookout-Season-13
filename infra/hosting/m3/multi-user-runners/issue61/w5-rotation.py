#!/usr/bin/env python3
"""#61 W5: the in-cluster controller's projected SA token rotates, and the controller's own code makes Kube calls
that succeed with the rotated token. Read-only on the cluster and the database. Run as root on the dev VM:

    sudo -n python3 -I /root/issue61/w5-rotation.py              # the window (at most 75 min)
    sudo -n python3 -I /root/issue61/w5-rotation.py --preflight  # one validated claims read; writes nothing

1. Precheck: no earlier w5-* evidence; host issue16 units inactive; the two known host kube-token paths are
   lstat-ed only (never opened): each must be absent or a regular file whose mtime is older than 24 h (a symlink
   or other file type is refused); runner-controller Deployment/Pod at the recorded UIDs, ready, with only the
   default kube-api-access token (SA token 3607 s, kube-root-ca, namespace) mounted read-only and no hostPath.
2. Claims: inside the controller container a fixed reader decodes only the JWT payload of the mounted token. The
   guest validates every value before anything is printed or recorded (iat, nbf, exp, warnafter, jti, sub, aud,
   namespace, Pod UID, SA, node, ..data link); the token and its signature never leave the reader process.
   Read every POLL seconds until jti changes. The window is WAIT seconds on a monotonic clock; a rotation first
   seen after it is not accepted and no proof starts.
3. Proof (after-window grace: it may finish after WAIT, but only starts for a rotation seen inside it): a fresh
   process in the controller container runs the controller's own code: build(), observer_pool.bound(…5804)
   (PVC GETs), runtime.namespace() (Namespace GET), service.pod() (Pod GET; absent = null), NodeEvidence.identity()
   (Node GET). Every request re-reads the token file (runner_kube.py:208-214); the jti is read before and after,
   and both must be the rotated jti. Success needs namespace ok, a Pod result (UUID or null) and a node name;
   anything else is refused without echoing values.
4. Resident identity: the resident controller keeps its container ID and restart count through the window and
   logs no reconciliation_required after the first read.

What success means (and does not): the controller's code succeeds with the rotated jti, and the resident process
stayed the same. It does NOT show that the resident watch re-established after the rotation (Wake.watch swallows
errors and logs nothing), nor that the old token is refused: k3s extends projected tokens (exp far beyond 3607 s),
so the old token stays valid until the Pod goes. The host check is metadata age of two known paths plus inactive
units, not proof that no host token exists anywhere (the paths' tokens were issued with --duration=24h per
rollout/README.md "PM requests 24h tokens"; mtime is not the JWT's own expiry).

rc: 0 rotated and proved; 3 precheck refused; 4 a proof call failed or its output was not as expected, or the
resident controller logged reconciliation_required; 5 not rotated within WAIT (or first seen after it); 6 rotated
again around the proof (no retry); 7 claims unreadable or malformed; 8 controller Pod or container changed;
9 everything else passed but run.json could not be written; 70 unexpected error; 130 interrupted (SIGINT,
SIGTERM or SIGHUP; run.json is still written). Preflight: 0 printed, 3, 7, 8 as above.
Evidence: /root/issue61/evidence/w5-<stamp>/run.json (validated claims and outcomes only).
"""
import json
import os
import re
import signal
import stat
import subprocess
import sys
import time

K3S = '/usr/local/bin/k3s'
SYSTEMCTL = '/usr/bin/systemctl'
EVIDENCE = '/root/issue61/evidence'
NS, DEPLOY = 'runner-control', 'runner-controller'
DEPLOY_UID = '41564f26-95e7-4ad1-8af3-d73b93c4669a'
POD_UID = 'e66fa6fa-3256-4dcb-a03b-2bc5923345bd'
SA = 'runner-controller'
SUB = 'system:serviceaccount:%s:%s' % (NS, SA)
TOKEN_DIR = '/var/run/secrets/kubernetes.io/serviceaccount'
OWNER = '00000000-0000-4000-8000-000000005804'
UNITS = ['issue16-controller.service', 'issue16-observer.service', 'issue16-supply.service',
         'issue16-controller.timer', 'issue16-observer.timer', 'issue16-supply.timer', 'issue16-restore.timer']
HOST_TOKENS = ['/etc/anxious-lookout/issue16/controller/kube-token', '/etc/anxious-lookout/issue16/observer/kube-token']
WAIT = max(1, min(75 * 60, int(os.environ.get('W5_WAIT_SECONDS', 75 * 60))))  # the harness may only shorten it
POLL = max(1, min(60, int(os.environ.get('W5_POLL_SECONDS', 60))))
CALL_TIMEOUT, MIN_TIMEOUT, PROOF_TIMEOUT = 60, 5, 120
UUID_RE = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
LINK_RE = re.compile(r'\.\.[0-9]{4}_[0-9]{2}_[0-9]{2}_[0-9]{2}_[0-9]{2}_[0-9]{2}\.[0-9]{1,12}')
NODE_RE = re.compile(r'[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?')
NAME_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,63}')
CLAIM_KEYS = ('aud', 'dataLink', 'exp', 'iat', 'jti', 'namespace', 'nbf', 'node', 'pod', 'serviceaccount', 'sub', 'warnafter')
STEPS = ('build', 'bound', 'namespace', 'pod', 'node')
LIMITS = ('success = the controller code succeeds with the rotated jti and the resident process stayed the same; '
          'NOT shown: resident watch reconnection after the rotation (Wake.watch logs nothing), refusal of the old '
          'token (k3s extends projected tokens), absence of host tokens beyond the two known paths')
READER = r'''
import base64, json, os, sys
d = %r
try:
    link = os.readlink(d + "/..data")
except OSError:
    link = None
try:
    parts = open(d + "/token", "rb").read().strip().split(b".")
    body = parts[1] if len(parts) == 3 else b""
    claims = json.loads(base64.urlsafe_b64decode(body + b"=" * (-len(body) %% 4)))
    assert isinstance(claims, dict)
except Exception:
    print(json.dumps({"error": "unreadable"})); sys.exit(0)
k = claims.get("kubernetes.io") if isinstance(claims.get("kubernetes.io"), dict) else {}
sub = lambda key: (k.get(key) if isinstance(k.get(key), dict) else {})
print(json.dumps({"iat": claims.get("iat"), "nbf": claims.get("nbf"), "exp": claims.get("exp"), "jti": claims.get("jti"),
                  "sub": claims.get("sub"), "aud": claims.get("aud"), "warnafter": k.get("warnafter"),
                  "namespace": k.get("namespace"), "pod": sub("pod").get("uid"),
                  "serviceaccount": sub("serviceaccount").get("name"), "node": sub("node").get("name"),
                  "dataLink": link}, sort_keys=True))
''' % TOKEN_DIR
PROOF = r'''
import base64, json, sys
from pathlib import Path
from uuid import UUID
TOKEN = Path(%r + "/token")
def jti():
    try:
        body = TOKEN.read_bytes().strip().split(b".")[1]
        return json.loads(base64.urlsafe_b64decode(body + b"=" * (-len(body) %% 4))).get("jti")
    except Exception:
        return None
out = {"jtiBefore": jti()}
step = "build"
try:
    from ops.runner_controller_service import build
    service = build()[0]
    step = "bound"
    with service.observer_pool.bound(UUID(%r)) as runtime:
        step = "namespace"
        runtime.namespace()
        out["namespace"] = "ok"
        step = "pod"
        obj = service.pod(runtime)
        out["pod"] = None if obj is None else obj["metadata"]["uid"]
        step = "node"
        node = service.node.identity(runtime.pods.client, service.now())
        out["node"] = node["nodeName"]
    out["result"] = "ok"
except Exception as error:
    out = {"jtiBefore": out["jtiBefore"], "result": "failed", "failedStep": step, "error": type(error).__name__}
out["jtiAfter"] = jti()
print(json.dumps(out, sort_keys=True))
''' % (TOKEN_DIR, OWNER)


class Stop(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


class Interrupted(BaseException):
    pass


SIGNALLED = []


def interrupted(signum, frame):
    if not SIGNALLED:
        SIGNALLED.append(signum)
        raise Interrupted()


def say(text):
    """Output can never fail the run (a broken stdout goes to /dev/null)."""
    try:
        print(text, flush=True)
    except Exception:
        try:
            fd = os.open(os.devnull, os.O_WRONLY)
            os.dup2(fd, 1)
        except Exception:
            pass


def stamp(epoch=None):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() if epoch is None else epoch))


class Window:
    """Monotonic window. Calls inside it get timeouts bounded by the remaining time (at least MIN_TIMEOUT)."""
    def __init__(self, seconds):
        self.end = time.monotonic() + seconds

    def remaining(self):
        return self.end - time.monotonic()

    def timeout(self):
        return max(MIN_TIMEOUT, min(CALL_TIMEOUT, int(self.remaining()) + 1))


def run(args, timeout=CALL_TIMEOUT, stdin=None):
    try:
        done = subprocess.run(args, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout)
        return done.returncode, done.stdout.decode('utf-8', 'replace')
    except subprocess.TimeoutExpired:
        return 124, ''
    except OSError:
        return 125, ''


def kube_json(args, code, what, timeout=CALL_TIMEOUT):
    rc, out = run([K3S, 'kubectl'] + args + ['-o', 'json'], timeout)
    if rc != 0:
        raise Stop(code, what + ' lookup failed (rc %d)' % rc)
    try:
        return json.loads(out)
    except ValueError:
        raise Stop(code, what + ' unreadable')


# Precheck ---------------------------------------------------------------------------------------------
def host_quiet():
    units = {}
    for unit in UNITS:
        _, active = run([SYSTEMCTL, 'is-active', unit])  # rc 3 is normal for inactive units
        _, enabled = run([SYSTEMCTL, 'is-enabled', unit])
        active, enabled = active.strip(), enabled.strip()
        if active not in ('inactive', 'failed'):
            raise Stop(3, 'host unit %s is not inactive' % unit)
        units[unit] = {'active': active, 'enabled': enabled if re.fullmatch(r'[a-z-]{1,20}', enabled) else 'unreadable'}
    tokens, now = {}, time.time()
    for path in HOST_TOKENS:
        try:
            info = os.lstat(path)  # metadata only; the bytes are never opened
        except FileNotFoundError:
            tokens[path] = None
            continue
        except OSError:
            raise Stop(3, 'host token path %s not stat-able' % path)
        if not stat.S_ISREG(info.st_mode):
            raise Stop(3, 'host token path %s is not a regular file (symlink or other type refused)' % path)
        age = int(now - info.st_mtime)
        tokens[path] = {'mtime': stamp(info.st_mtime), 'ageHours': round(age / 3600.0, 1)}
        if age < 24 * 3600:
            raise Stop(3, 'host token path %s has mtime %s, younger than 24 h' % (path, stamp(info.st_mtime)))
    return units, tokens


def controller():
    deploy = kube_json(['-n', NS, 'get', 'deployment', DEPLOY], 3, 'controller Deployment')
    status = deploy.get('status') or {}
    if deploy.get('metadata', {}).get('uid') != DEPLOY_UID:
        raise Stop(3, 'controller Deployment UID is not the recorded %s' % DEPLOY_UID)
    if (deploy.get('spec') or {}).get('replicas') != 1 or status.get('readyReplicas') != 1:
        raise Stop(3, 'controller Deployment not 1/1 ready')
    return live_pod(3)


def live_pod(code, timeout=CALL_TIMEOUT):
    items = kube_json(['-n', NS, 'get', 'pods', '-l', 'app=' + DEPLOY], code, 'controller Pod', timeout).get('items', [])
    if len(items) != 1:
        raise Stop(code, 'expected one controller Pod, found %d' % len(items))
    return pod_shape(items[0], code)


def pod_shape(pod, code):
    meta, spec, status = pod.get('metadata') or {}, pod.get('spec') or {}, pod.get('status') or {}
    if meta.get('uid') != POD_UID:
        raise Stop(code, 'controller Pod UID is not the recorded %s' % POD_UID)
    if meta.get('deletionTimestamp') or status.get('phase') != 'Running':
        raise Stop(code, 'controller Pod not Running or deleting')
    cs = {c.get('name'): c for c in status.get('containerStatuses', [])}
    ctrl = cs.get('controller') or {}
    if not ctrl.get('ready') or not (ctrl.get('state') or {}).get('running'):
        raise Stop(code, 'controller container not running and ready')
    problems = []
    if spec.get('serviceAccountName') != SA:
        problems.append('serviceAccountName')
    if spec.get('automountServiceAccountToken') is False:
        problems.append('automount disabled')
    volumes = {v.get('name'): v for v in spec.get('volumes', [])}
    if any('hostPath' in v for v in volumes.values()):
        problems.append('a hostPath volume')
    access = [n for n in volumes if isinstance(n, str) and n.startswith('kube-api-access-')]
    tokens = [n for n, v in volumes.items()
              if any('serviceAccountToken' in s for s in (v.get('projected') or {}).get('sources', []))]
    if len(access) != 1 or tokens != access:
        problems.append('token volumes')
    else:
        sources = volumes[access[0]]['projected'].get('sources', [])
        shape = sorted((list(s)[0], json.dumps(s[list(s)[0]], sort_keys=True)) for s in sources)
        want = sorted([('serviceAccountToken', json.dumps({'expirationSeconds': 3607, 'path': 'token'}, sort_keys=True)),
                       ('configMap', json.dumps({'items': [{'key': 'ca.crt', 'path': 'ca.crt'}], 'name': 'kube-root-ca.crt'}, sort_keys=True)),
                       ('downwardAPI', json.dumps({'items': [{'fieldRef': {'apiVersion': 'v1', 'fieldPath': 'metadata.namespace'},
                                                              'path': 'namespace'}]}, sort_keys=True))])
        if shape != want:
            problems.append('kube-api-access sources differ')
    others = sorted(n for n in volumes if n not in access)
    if others != ['db', 'tmp'] or 'secret' not in volumes.get('db', {}) or 'emptyDir' not in volumes.get('tmp', {}):
        problems.append('other volumes')
    containers = [c for c in spec.get('containers', []) if c.get('name') == 'controller']
    mounts = {m.get('mountPath'): m for m in containers[0].get('volumeMounts', [])} if containers else {}
    token_mount = mounts.get(TOKEN_DIR) or {}
    if len(spec.get('containers', [])) != 1 or token_mount.get('name') not in access or token_mount.get('readOnly') is not True:
        problems.append('token mount')
    if problems:
        raise Stop(code, 'controller Pod shape: ' + '; '.join(problems))  # fixed problem names only
    cid = ctrl.get('containerID')
    started = ctrl['state']['running'].get('startedAt')
    if not (isinstance(cid, str) and re.fullmatch(r'[a-z]{1,20}://[0-9a-f]{64}', cid)) or \
            not (isinstance(started, str) and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', started)) or \
            type(ctrl.get('restartCount')) is not int or not (isinstance(meta.get('name'), str) and NODE_RE.fullmatch(meta['name'])):
        raise Stop(code, 'controller Pod status fields not as expected')
    return {'name': meta['name'], 'uid': meta['uid'], 'restarts': ctrl['restartCount'], 'containerID': cid, 'started': started}


# Claims and proof -------------------------------------------------------------------------------------
def claims(pod_name, timeout=CALL_TIMEOUT):
    rc, out = run([K3S, 'kubectl', '-n', NS, 'exec', pod_name, '-c', 'controller', '--', 'python', '-I', '-c', READER], timeout)
    lines = [l for l in out.splitlines() if l.startswith('{')]
    if rc != 0 or len(lines) != 1:
        raise Stop(7, 'claims read failed (rc %d)' % rc)
    try:
        value = json.loads(lines[0])
    except ValueError:
        raise Stop(7, 'claims output unreadable')
    if not isinstance(value, dict):
        raise Stop(7, 'claims output unreadable')
    if 'error' in value:
        raise Stop(7, 'claims reader: token unreadable')
    bad = [k for k in CLAIM_KEYS if k not in value]
    unexpected = sum(1 for k in value if k not in CLAIM_KEYS)  # counted, never named
    for key in ('iat', 'nbf', 'exp', 'warnafter'):
        if key in value and not (type(value[key]) is int and 1600000000 <= value[key] <= 4000000000):
            bad.append(key)
    if not (isinstance(value.get('jti'), str) and UUID_RE.fullmatch(value['jti'])):
        bad.append('jti')
    if value.get('sub') != SUB or value.get('namespace') != NS or value.get('serviceaccount') != SA:
        bad.append('identity')
    if value.get('pod') != POD_UID:
        bad.append('pod')
    if not (isinstance(value.get('aud'), list) and 0 < len(value['aud']) <= 8
            and all(isinstance(a, str) and re.fullmatch(r'[A-Za-z0-9:/._-]{1,200}', a) for a in value['aud'])):
        bad.append('aud')
    if not (isinstance(value.get('node'), str) and NODE_RE.fullmatch(value['node'])):
        bad.append('node')
    if value.get('dataLink') is not None and not (isinstance(value['dataLink'], str) and LINK_RE.fullmatch(value['dataLink'])):
        bad.append('dataLink')
    if bad or unexpected:
        raise Stop(7, 'claims not as expected: %s%s' % (', '.join(sorted(set(bad))) or '-',
                                                       '; %d unexpected key(s)' % unexpected if unexpected else ''))
    return {k: value[k] for k in CLAIM_KEYS}


def brief(c):
    return 'iat %s exp %s warnafter %s jti %s link %s' % (stamp(c['iat']), stamp(c['exp']), stamp(c['warnafter']),
                                                       c['jti'], c['dataLink'])


def proof(pod_name, rotated):
    """The proof process's output, validated in full before anything of it is printed or recorded."""
    rc, out = run([K3S, 'kubectl', '-n', NS, 'exec', '-i', pod_name, '-c', 'controller', '--', 'python', '-B', '-'],
                  PROOF_TIMEOUT, PROOF.encode())
    lines = [l for l in out.splitlines() if l.startswith('{')]
    try:
        value = json.loads(lines[-1]) if rc == 0 and lines else None
    except ValueError:
        value = None
    if not isinstance(value, dict):
        raise Stop(4, 'proof process failed (rc %d)' % rc)
    tokens_ok = all(isinstance(value.get(k), str) and UUID_RE.fullmatch(value[k]) for k in ('jtiBefore', 'jtiAfter'))
    if value.get('result') == 'ok' and set(value) == {'jtiBefore', 'jtiAfter', 'result', 'namespace', 'pod', 'node'} \
            and tokens_ok and value['namespace'] == 'ok' \
            and (value['pod'] is None or (isinstance(value['pod'], str) and UUID_RE.fullmatch(value['pod']))) \
            and isinstance(value['node'], str) and NODE_RE.fullmatch(value['node']):
        checked = {k: value[k] for k in ('jtiBefore', 'jtiAfter', 'result', 'namespace', 'pod', 'node')}
    elif value.get('result') == 'failed' and set(value) == {'jtiBefore', 'jtiAfter', 'result', 'failedStep', 'error'} \
            and value['failedStep'] in STEPS and isinstance(value['error'], str) and NAME_RE.fullmatch(value['error']) \
            and all(value[k] is None or (isinstance(value[k], str) and UUID_RE.fullmatch(value[k])) for k in ('jtiBefore', 'jtiAfter')):
        checked = {k: value[k] for k in ('jtiBefore', 'jtiAfter', 'result', 'failedStep', 'error')}
    else:
        raise Stop(4, 'proof output not as expected (refused; values not shown)')
    if checked['jtiBefore'] != rotated or checked['jtiAfter'] != rotated:
        raise Stop(6, 'token changed around the proof calls (before %s, after %s, rotated %s); no retry'
                   % (checked['jtiBefore'], checked['jtiAfter'], rotated))
    return checked


def resident_after(pod_name, since):
    rc, out = run([K3S, 'kubectl', '-n', NS, 'logs', pod_name, '-c', 'controller', '--since-time=' + since])
    if rc != 0:
        raise Stop(4, 'resident controller log unreadable (rc %d)' % rc)
    bad = sum(1 for l in out.splitlines() if '"result": "reconciliation_required"' in l)
    lines = sum(1 for l in out.splitlines() if '"stage": "controller"' in l)
    return {'outcomeLines': lines, 'reconciliationRequired': bad}


# Modes ------------------------------------------------------------------------------------------------
def preflight():
    """One validated claims read, bound by the controller Pod identity before and after. Writes nothing."""
    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGHUP, interrupted)
    try:
        pod = controller()
        first = claims(pod['name'])
        if live_pod(8) != pod:
            raise Stop(8, 'controller Pod or container changed during the read')
        age = int(time.time()) - first['iat']
        say('preflight %s: Pod %s uid %s restarts %s; %s; age %d s (kubelet normally refreshes near 80%% of 3607 s, '
            'i.e. about %s; expectation only)' % (stamp(), pod['name'], pod['uid'], pod['restarts'], brief(first), age,
                                                  stamp(first['iat'] + 2886)))
        code = 0
    except Stop as stop:
        say('STOPPED (%d): %s' % (stop.code, stop.message))
        code = stop.code
    except Interrupted:
        say('STOPPED (130): interrupted')
        code = 130
    say('preflight rc=%d (nothing written)' % code)
    return code


def window():
    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGHUP, interrupted)  # a dropped ssh session still leaves run.json
    record = {'started': stamp(), 'series': [], 'limits': LIMITS, 'waitSeconds': WAIT}
    ev, code, message = None, 0, ''
    try:
        try:
            if os.path.isdir(EVIDENCE) and any(n.startswith('w5-') for n in os.listdir(EVIDENCE)):
                raise Stop(3, 'earlier w5-* evidence exists; this window is one-shot')
            units, host_tokens = host_quiet()
            pod = controller()
            record.update(units=units, hostTokens=host_tokens, pod=pod)
            say('precheck ok: host issue16 units inactive (enabled states: %s); known host token paths (metadata only) %s; '
                'controller Pod %s uid %s restarts %s, only kube-api-access (3607 s) at %s'
                % (', '.join(sorted(set(u['enabled'] for u in units.values()))), json.dumps(host_tokens, sort_keys=True),
                   pod['name'], pod['uid'], pod['restarts'], TOKEN_DIR))
            ev = os.path.join(EVIDENCE, 'w5-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()))
            os.makedirs(EVIDENCE, mode=0o700, exist_ok=True)
            os.mkdir(ev, 0o700)
            say('evidence: ' + ev)
            span = Window(WAIT)
            first = claims(pod['name'], span.timeout())
            record['series'].append({'at': stamp(), 'claims': first})
            age = int(time.time()) - first['iat']
            say('claims %s: %s (age %d s; kubelet normally refreshes near 80%% of 3607 s, i.e. about %s; expectation only; '
                'window %d s)' % (stamp(), brief(first), age, stamp(first['iat'] + 2886), WAIT))
            current = first
            while True:
                left = span.remaining()
                if left <= 0:
                    raise Stop(5, 'token not rotated within %d s (last %s); nothing retried' % (WAIT, brief(current)))
                time.sleep(min(POLL, left))
                if span.remaining() <= 0:
                    raise Stop(5, 'token not rotated within %d s (last %s); nothing retried' % (WAIT, brief(current)))
                if live_pod(8, span.timeout()) != pod:
                    raise Stop(8, 'controller Pod or container changed during the window')
                current = claims(pod['name'], span.timeout())
                seen_inside = span.remaining() >= 0  # judged when the read returned, before any proof
                record['series'].append({'at': stamp(), 'claims': current, 'insideWindow': seen_inside})
                if not seen_inside:
                    raise Stop(5, 'window of %d s ended during the last read%s; no proof started'
                               % (WAIT, ' (a rotation seen then is outside the window)' if current['jti'] != first['jti'] else ''))
                if current['jti'] != first['jti']:
                    break
            record['rotatedSeenAt'] = stamp()
            say('rotated (seen %s, inside the window): %s' % (record['rotatedSeenAt'], brief(current)))
            if not (current['iat'] > first['iat'] and current['exp'] >= first['exp']):
                raise Stop(7, 'rotated claims do not advance (iat/exp)')
            say('proof starts (after-window grace applies to the proof and the final record only)')
            result = proof(pod['name'], current['jti'])
            record['proof'] = result
            say('proof (fresh process, controller code and SA, validated): %s' % json.dumps(result, sort_keys=True))
            if result['result'] != 'ok':
                raise Stop(4, 'a controller Kube call with the rotated token failed at %s (%s)' % (result['failedStep'], result['error']))
            if result['node'] != current['node']:
                say('note: proof node %s differs from the token claim node %s (recorded, not a gate)' % (result['node'], current['node']))
            after = live_pod(8)
            if after != pod:
                raise Stop(8, 'controller Pod or container changed during the window')
            resident = resident_after(pod['name'], record['series'][0]['at'])
            record['resident'] = dict(resident, sameContainer=True, restarts=after['restarts'])
            say('resident identity: same container, restarts %s; since the first read %d outcome lines, %d '
                'reconciliation_required (resident watch reconnection is not shown by this)'
                % (after['restarts'], resident['outcomeLines'], resident['reconciliationRequired']))
            if resident['reconciliationRequired']:
                raise Stop(4, 'the resident controller logged reconciliation_required after the first read')
            message = ('W5 ok: token rotated (jti %s -> %s, iat %s -> %s); the controller code (PVC, Namespace, Pod, Node '
                       'GETs) succeeded with the rotated jti; resident controller identity unchanged. %s'
                       % (first['jti'], current['jti'], stamp(first['iat']), stamp(current['iat']), LIMITS))
        except Stop as stop:
            code, message = stop.code, stop.message
        except Interrupted:
            code, message = 130, 'interrupted; nothing was changed'
    except BaseException as error:  # a second signal or anything unexpected: still record
        code = 130 if isinstance(error, (Interrupted, KeyboardInterrupt)) else 70
        message = 'stopped: %s' % type(error).__name__
    record.update(finished=stamp(), result=code, message=message)
    if ev:
        try:
            fd = os.open(os.path.join(ev, 'run.json'), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as stream:
                json.dump(record, stream, sort_keys=True, indent=1)
                stream.flush()
                os.fsync(stream.fileno())
            say('evidence written: %s/run.json' % ev)
        except Exception as error:
            say('EVIDENCE NOT WRITTEN (%s): stdout above is the only record' % type(error).__name__)
            if code == 0:
                code, message = 9, 'everything passed but run.json could not be written'
    say(('STOPPED (%d): ' % code if code else '') + message)
    say('result rc=%d' % code)
    return code


def main(argv):
    if argv == ['--preflight']:
        return preflight()
    if argv:
        say('usage: w5-rotation.py [--preflight]')
        return 2
    return window()


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
