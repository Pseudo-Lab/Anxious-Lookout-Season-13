#!/usr/bin/env python3
"""#61 W5: the in-cluster controller's projected SA token rotates, and the controller's Kube calls succeed with
the rotated token. Read-only on the cluster and the database. Run as root on the dev VM:

    sudo -n python3 -I /root/issue61/w5-rotation.py

1. Precheck: no earlier w5-* evidence; host issue16 units inactive and their 24 h kube-token files (stat only,
   never read) older than 24 h; runner-controller Deployment/Pod at the recorded UIDs, ready, with only the
   default kube-api-access token (SA token 3607 s, kube-root-ca, namespace) and no hostPath.
2. Claims: inside the controller container a fixed reader decodes only the JWT payload of the mounted token and
   prints non-secret claims (iat, nbf, exp, warnafter, jti, sub, aud, namespace, Pod UID, SA, node) plus the
   projected volume's ..data link. The token and its signature never leave that process. Read every POLL seconds
   until jti changes, at most WAIT seconds.
3. Proof after the change: a fresh process in the controller container runs the controller's own code (build(),
   observer_pool.bound(…5804) = PVC GETs, runtime.namespace() = Namespace GET, service.pod() = Pod GET,
   NodeEvidence.identity() = Node GET). Every request re-reads the token file (runner_kube.py:208-214), and the
   jti is read before and after, so the calls are tied to the rotated token. The resident process uses the same
   per-request read (runner_controller_service.py:408-411); its restarts and reconciliation_required lines after
   the change are checked.

Limits: k3s extends projected tokens (exp far beyond 3607 s), so the old token stays valid until the Pod goes.
A success after rotation therefore proves the rotated token works; it does not prove the old one was refused.
With no runner Pod, the resident loop makes no Kube call besides its watch; the fresh-process proof covers that.

rc: 0 rotated and proved; 3 precheck refused; 4 a proof call failed, or the resident controller restarted or logged
reconciliation_required after the change; 5 not rotated within WAIT; 6 rotated again during the proof (no retry);
7 claims unreadable or malformed; 8 controller Pod changed during the window; 70 unexpected error; 130 interrupted
(SIGINT, SIGTERM or SIGHUP; the record is still written).
Evidence: /root/issue61/evidence/w5-<stamp>/run.json (claims and outcomes only).
"""
import calendar
import json
import os
import re
import signal
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
UUID_RE = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
LINK_RE = re.compile(r'\.\.[0-9]{4}_[0-9]{2}_[0-9]{2}_[0-9]{2}_[0-9]{2}_[0-9]{2}\.[0-9]{1,12}')
CLAIM_KEYS = ('aud', 'dataLink', 'exp', 'iat', 'jti', 'namespace', 'nbf', 'node', 'pod', 'serviceaccount', 'sub', 'warnafter')
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
    body = TOKEN.read_bytes().strip().split(b".")[1]
    return json.loads(base64.urlsafe_b64decode(body + b"=" * (-len(body) %% 4))).get("jti")
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
    out["result"] = "failed"
    out["failedStep"] = step
    out["error"] = type(error).__name__
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


def run(args, timeout=60, stdin=None):
    try:
        done = subprocess.run(args, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout)
        return done.returncode, done.stdout.decode('utf-8', 'replace')
    except subprocess.TimeoutExpired:
        return 124, ''
    except OSError:
        return 125, ''


def kube_json(args, code, what):
    rc, out = run([K3S, 'kubectl'] + args + ['-o', 'json'])
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
        units[unit] = {'active': active, 'enabled': enabled or '-'}
        if active not in ('inactive', 'failed'):
            raise Stop(3, 'host unit %s is %s' % (unit, active or 'unreadable'))
    tokens, now = {}, time.time()
    for path in HOST_TOKENS:
        try:
            info = os.lstat(path)  # metadata only; the bytes are never opened
        except FileNotFoundError:
            tokens[path] = None
            continue
        except OSError:
            raise Stop(3, 'host token %s not stat-able' % path)
        age = int(now - info.st_mtime)
        tokens[path] = {'mtime': stamp(info.st_mtime), 'ageHours': round(age / 3600.0, 1)}
        if age < 24 * 3600:
            raise Stop(3, 'host token %s is younger than 24 h (mtime %s): a live host token exists' % (path, stamp(info.st_mtime)))
    return units, tokens


def controller():
    deploy = kube_json(['-n', NS, 'get', 'deployment', DEPLOY], 3, 'controller Deployment')
    status = deploy.get('status') or {}
    if deploy['metadata'].get('uid') != DEPLOY_UID:
        raise Stop(3, 'controller Deployment UID %s is not the recorded %s' % (deploy['metadata'].get('uid'), DEPLOY_UID))
    if deploy['spec'].get('replicas') != 1 or status.get('readyReplicas') != 1:
        raise Stop(3, 'controller Deployment not 1/1 ready')
    items = kube_json(['-n', NS, 'get', 'pods', '-l', 'app=' + DEPLOY], 3, 'controller Pod').get('items', [])
    if len(items) != 1:
        raise Stop(3, 'expected one controller Pod, found %d' % len(items))
    return pod_shape(items[0], 3)


def pod_shape(pod, code):
    meta, spec, status = pod['metadata'], pod['spec'], pod.get('status') or {}
    if meta.get('uid') != POD_UID:
        raise Stop(code, 'controller Pod UID %s is not the recorded %s' % (meta.get('uid'), POD_UID))
    if meta.get('deletionTimestamp') or status.get('phase') != 'Running':
        raise Stop(code, 'controller Pod not Running or deleting')
    cs = {c.get('name'): c for c in status.get('containerStatuses', [])}
    ctrl = cs.get('controller') or {}
    if not ctrl.get('ready') or not (ctrl.get('state') or {}).get('running'):
        raise Stop(code, 'controller container not running and ready')
    problems = []
    if spec.get('serviceAccountName') != SA:
        problems.append('serviceAccountName %s' % spec.get('serviceAccountName'))
    if spec.get('automountServiceAccountToken') is False:
        problems.append('automount disabled')
    volumes = {v['name']: v for v in spec.get('volumes', [])}
    if any('hostPath' in v for v in volumes.values()):
        problems.append('a hostPath volume')
    access = [n for n in volumes if n.startswith('kube-api-access-')]
    tokens = [n for n, v in volumes.items()
              if any('serviceAccountToken' in s for s in (v.get('projected') or {}).get('sources', []))]
    if len(access) != 1 or tokens != access:
        problems.append('token volumes %s' % sorted(tokens))
    else:
        sources = volumes[access[0]]['projected'].get('sources', [])
        shape = sorted((list(s)[0], json.dumps(s[list(s)[0]], sort_keys=True)) for s in sources)
        want = sorted([('serviceAccountToken', json.dumps({'expirationSeconds': 3607, 'path': 'token'}, sort_keys=True)),
                       ('configMap', json.dumps({'items': [{'key': 'ca.crt', 'path': 'ca.crt'}], 'name': 'kube-root-ca.crt'}, sort_keys=True)),
                       ('downwardAPI', json.dumps({'items': [{'fieldRef': {'apiVersion': 'v1', 'fieldPath': 'metadata.namespace'},
                                                              'path': 'namespace'}]}, sort_keys=True))])
        if shape != want:
            problems.append('kube-api-access sources differ')
    others = sorted(set(volumes) - set(access))
    if others != ['db', 'tmp'] or 'secret' not in volumes.get('db', {}) or 'emptyDir' not in volumes.get('tmp', {}):
        problems.append('volumes %s' % others)
    containers = [c for c in spec.get('containers', []) if c.get('name') == 'controller']
    mounts = {m['mountPath']: m for m in containers[0].get('volumeMounts', [])} if containers else {}
    token_mount = mounts.get(TOKEN_DIR) or {}
    if len(spec.get('containers', [])) != 1 or token_mount.get('name') not in access or token_mount.get('readOnly') is not True:
        problems.append('token mount')
    if problems:
        raise Stop(code, 'controller Pod shape: ' + '; '.join(problems))
    return {'name': meta['name'], 'uid': meta['uid'], 'restarts': ctrl.get('restartCount'),
            'containerID': ctrl.get('containerID'), 'started': ctrl['state']['running'].get('startedAt')}


def live_pod():
    items = kube_json(['-n', NS, 'get', 'pods', '-l', 'app=' + DEPLOY], 8, 'controller Pod').get('items', [])
    if len(items) != 1:
        raise Stop(8, 'controller Pod set changed (%d Pods)' % len(items))
    return pod_shape(items[0], 8)


# Claims and proof -------------------------------------------------------------------------------------
def claims(pod_name):
    rc, out = run([K3S, 'kubectl', '-n', NS, 'exec', pod_name, '-c', 'controller', '--', 'python', '-I', '-c', READER])
    lines = [l for l in out.splitlines() if l.startswith('{')]
    if rc != 0 or len(lines) != 1:
        raise Stop(7, 'claims read failed (rc %d)' % rc)
    try:
        value = json.loads(lines[0])
    except ValueError:
        raise Stop(7, 'claims output unreadable')
    if value.get('error'):
        raise Stop(7, 'claims reader: token unreadable')
    bad = [k for k in CLAIM_KEYS if k not in value] + [k for k in value if k not in CLAIM_KEYS]
    ints = ('iat', 'nbf', 'exp', 'warnafter')
    for key in ints:
        if key in value and not (type(value[key]) is int and 1600000000 <= value[key] <= 4000000000):
            bad.append(key)
    if not (isinstance(value.get('jti'), str) and UUID_RE.fullmatch(value['jti'])):
        bad.append('jti')
    if value.get('sub') != SUB or value.get('namespace') != NS or value.get('serviceaccount') != SA:
        bad.append('identity')
    if value.get('pod') != POD_UID:
        bad.append('pod')
    if not (isinstance(value.get('aud'), list) and value['aud']
            and all(isinstance(a, str) and 0 < len(a) <= 200 and not any(c.isspace() for c in a) for a in value['aud'])):
        bad.append('aud')
    if not (isinstance(value.get('node'), str) and re.fullmatch(r'[a-z0-9.-]{1,253}', value['node'])):
        bad.append('node')
    if value.get('dataLink') is not None and not (isinstance(value['dataLink'], str) and LINK_RE.fullmatch(value['dataLink'])):
        bad.append('dataLink')
    if bad:
        raise Stop(7, 'claims not as expected: ' + ', '.join(sorted(set(bad))))  # field names only, never values
    return {k: value[k] for k in CLAIM_KEYS}


def brief(c):
    return 'iat %s exp %s warnafter %s jti %s link %s' % (stamp(c['iat']), stamp(c['exp']), stamp(c['warnafter']),
                                                       c['jti'], c['dataLink'])


def proof(pod_name):
    rc, out = run([K3S, 'kubectl', '-n', NS, 'exec', '-i', pod_name, '-c', 'controller', '--', 'python', '-B', '-'],
                  120, PROOF.encode())
    lines = [l for l in out.splitlines() if l.startswith('{')]
    try:
        value = json.loads(lines[-1]) if lines else None
    except ValueError:
        value = None
    keys = {'jtiBefore', 'jtiAfter', 'result', 'namespace', 'pod', 'node', 'failedStep', 'error'}
    if rc != 0 or not isinstance(value, dict) or not set(value) <= keys:
        raise Stop(4, 'proof process failed (rc %d)' % rc)
    for key in ('jtiBefore', 'jtiAfter'):
        if not (isinstance(value.get(key), str) and UUID_RE.fullmatch(value[key])):
            raise Stop(4, 'proof process: %s unreadable' % key)
    if value.get('error') is not None and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', str(value['error'])):
        value['error'] = 'unnamed'
    if value.get('failedStep') is not None and value['failedStep'] not in ('build', 'bound', 'namespace', 'pod', 'node'):
        value['failedStep'] = 'unknown'
    if value.get('pod') is not None and not (isinstance(value['pod'], str) and UUID_RE.fullmatch(value['pod'])):
        value['pod'] = 'unreadable'
    return value


def resident_after(pod_name, since):
    rc, out = run([K3S, 'kubectl', '-n', NS, 'logs', pod_name, '-c', 'controller', '--since-time=' + since])
    if rc != 0:
        raise Stop(4, 'resident controller log unreadable (rc %d)' % rc)
    bad = sum(1 for l in out.splitlines() if '"result": "reconciliation_required"' in l)
    lines = sum(1 for l in out.splitlines() if '"stage": "controller"' in l)
    return {'outcomeLines': lines, 'reconciliationRequired': bad}


# Main -------------------------------------------------------------------------------------------------
def main():
    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGHUP, interrupted)  # a dropped ssh session still leaves run.json
    record = {'started': stamp(), 'series': []}
    ev, code, message = None, 0, ''
    try:
        try:
            if os.path.isdir(EVIDENCE) and any(n.startswith('w5-') for n in os.listdir(EVIDENCE)):
                raise Stop(3, 'earlier w5-* evidence exists; this window is one-shot')
            units, host_tokens = host_quiet()
            pod = controller()
            record.update(units=units, hostTokens=host_tokens, pod=pod)
            say('precheck ok: host issue16 units inactive (%s); host 24 h tokens %s; controller Pod %s uid %s restarts %s, '
                'only kube-api-access (3607 s) at %s'
                % (', '.join(sorted(set(u['enabled'] for u in units.values()))),
                   json.dumps(host_tokens, sort_keys=True), pod['name'], pod['uid'], pod['restarts'], TOKEN_DIR))
            ev = os.path.join(EVIDENCE, 'w5-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()))
            os.makedirs(EVIDENCE, mode=0o700, exist_ok=True)
            os.mkdir(ev, 0o700)
            say('evidence: ' + ev)
            first = claims(pod['name'])
            record['series'].append({'at': stamp(), 'claims': first})
            age = int(time.time()) - first['iat']
            say('claims %s: %s (age %d s; kubelet normally refreshes at about 80%% of 3607 s, i.e. near %s; '
                'expectation only)' % (stamp(), brief(first), age, stamp(first['iat'] + 2886)))
            deadline = time.time() + WAIT
            current = first
            while True:
                time.sleep(POLL)
                if live_pod() != pod:
                    raise Stop(8, 'controller Pod or container changed during the window')
                current = claims(pod['name'])
                record['series'].append({'at': stamp(), 'claims': current})
                if current['jti'] != first['jti']:
                    break
                if time.time() >= deadline:
                    raise Stop(5, 'token not rotated within %d s (last %s); nothing retried' % (WAIT, brief(current)))
            changed = stamp()
            record['rotatedSeenAt'] = changed
            say('rotated (seen %s): %s' % (changed, brief(current)))
            if not (current['iat'] > first['iat'] and current['exp'] >= first['exp']):
                raise Stop(7, 'rotated claims do not advance (iat/exp)')
            result = proof(pod['name'])
            record['proof'] = result
            say('proof (fresh process, controller code and SA): %s' % json.dumps(result, sort_keys=True))
            if result['jtiBefore'] != current['jti'] or result['jtiAfter'] != current['jti']:
                raise Stop(6, 'token changed around the proof calls (before %s after %s, rotated %s); no retry'
                           % (result['jtiBefore'], result['jtiAfter'], current['jti']))
            if result.get('result') != 'ok':
                raise Stop(4, 'a controller Kube call with the rotated token failed at %s (%s)'
                           % (result.get('failedStep'), result.get('error')))
            after = live_pod()
            if after != pod:
                raise Stop(8, 'controller Pod or container changed during the window')
            resident = resident_after(pod['name'], first_seen(record))
            record['resident'] = resident
            say('resident controller: same container, restarts %s; since the first read %d outcome lines, %d '
                'reconciliation_required' % (after['restarts'], resident['outcomeLines'], resident['reconciliationRequired']))
            if resident['reconciliationRequired']:
                raise Stop(4, 'the resident controller logged reconciliation_required after the first read')
            message = ('W5 ok: token rotated (jti %s -> %s, iat %s -> %s); Namespace, Pod and Node GETs and PVC lookups '
                       'succeeded with the rotated token; resident controller unchanged'
                       % (first['jti'], current['jti'], stamp(first['iat']), stamp(current['iat'])))
        except Stop as stop:
            code, message = stop.code, stop.message
        except Interrupted:
            code, message = 130, 'interrupted; nothing was changed'
    except BaseException as error:  # a second signal or anything unexpected: still record
        code, message = 130 if isinstance(error, (Interrupted, KeyboardInterrupt)) else 70, \
            'stopped: %s' % type(error).__name__
    record.update(finished=stamp(), result=code, message=message)
    if ev:
        try:
            fd = os.open(os.path.join(ev, 'run.json'), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as stream:
                json.dump(record, stream, sort_keys=True, indent=1)
        except Exception:
            say('evidence record could not be written')
    say(('STOPPED (%d): ' % code if code else '') + message)
    say('result rc=%d' % code)
    return code


def first_seen(record):
    return record['series'][0]['at']


if __name__ == '__main__':
    sys.exit(main())
