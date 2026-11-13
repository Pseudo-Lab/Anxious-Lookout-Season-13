"""#68 dev-VM stage V6a: the development broker leaves mock OAuth and is bound to a separate real ChatGPT
account (user decisions 2026-11-06). Development VM only; production keeps its broker and account.

    python3 -I /root/native68/v6a-broker.py precheck   # read-only: changes nothing, writes no file
    python3 -I /root/native68/v6a-broker.py mock-off   # once, after review: mock removed, no credential
    (the user logs in: see "login" below)
    python3 -I /root/native68/v6a-broker.py bind       # once, after review and the login
    python3 -I /root/native68/v6a-broker.py restore    # only if PM decides to go back (after `codex logout`)

Entry stays closed for the whole stage: there is no live session in auth.sessions (the fixture owners are
synthetic and only reviewed scripts create sessions), no runner Pod, both allowlists stay the V7a value (…5804
only), and every fence stays the V7a one. Each action checks this at its start and at its end.

mock-off (rc 3 precheck refused, nothing changed; 4 failed before the patch; 5 the patch or the new Pod failed):
1. precheck: StatefulSet codex-broker is the recorded object (UID, generation 1) with exactly the mock
   layout: init seed-synthetic-auth, containers broker + mock-oauth, broker env 4 =
   CODEX_REFRESH_TOKEN_URL_OVERRIDE (the loopback mock), volumes tmp + mock-oauth, broker image #60; its Pod
   2/2 Running; /data/codex/auth.json present (the synthetic file), /data/mock-auth.json.r1 absent; no
   VolumeSnapshot or LVM snapshot of the broker volume and no snapshot LV with the broker LV as origin; the
   common entry-closed checks.
2. evidence directory v6a-<UTC> (root 0700) with private/ (0700): the StatefulSet JSON before (0600).
3. inside the broker container: mv -n /data/codex/auth.json /data/mock-auth.json.r1 (the synthetic file kept
   on the same volume; the seed init would otherwise not write again, but it is removed in 4 anyway).
4. one JSON Patch whose test ops pin UID, resourceVersion and every name and value it removes: remove broker
   env 4, container mock-oauth, volume mock-oauth and initContainers. The ConfigMap object stays.
5. rollout: a new Pod (other UID), 1/1, containers exactly [broker] on the same image, no init container, no
   override env, /healthz 200, /data/codex/auth.json absent, /data/mock-auth.json.r1 present.
There is no in-run undo: a failure leaves the broker without credentials, which nothing uses while entry is
closed. `restore` is the way back.

login (the user, interactive, after mock-off rc 0; agents never see the account or a token):
    sudo /usr/local/bin/k3s kubectl -n codex-broker exec -it codex-broker-0 -c broker -- codex login --device-auth
The broker container has CODEX_HOME=HOME=/data/codex, so auth.json is written there (0600, UID 10001).

bind (rc 3 refused, nothing changed; 4 the account id could not be read; 5 the broker patch or its Pod
failed; 6 inputs.json could not be written; checks print booleans only):
1. precheck: the post-mock-off StatefulSet (same UID, no mock, BROKER_ACCOUNT_ID = the synthetic id that
   inputs.json also holds) and Pod; /data/codex/auth.json present, 0600, UID 10001 (stat only); entry closed.
2. the account id: inside the broker container, the broker's own reader (`ops.access_snapshot.selected_file`,
   the function Credentials uses) parses auth.json and prints the account id only. This script keeps it in
   memory: never printed, never a command argument, never in evidence. A refusal prints the fixed reason.
   Printed: "account id is UUID-shaped True/False", "differs from the synthetic id True/False".
3. the broker: BROKER_ACCOUNT_ID -> the account id by one JSON Patch (test = UID, resourceVersion, name and
   the synthetic value) passed in a root 0600 file under private/, removed in the same run (also on failure);
   rollout; the new Pod's env equals the account id and the reader in the new Pod still returns it (booleans).
4. inputs.json: the original bytes copied to private/ (0600), then providerAccountId -> the account id
   (temporary file in the same directory, root 0600, fsync, rename); every other key unchanged (boolean).
   The runner-controller does not read inputs.json; the host preparer reads it when stage V6b runs it.
No backup taken after step 2 holds the new id. A failure stops; nothing is undone in the run.

Temporary files that may hold the account id (the patch file, the inputs.json temporary file) are removed in
the same run on every exit path the process can handle: success, failure, SIGINT, SIGTERM and SIGHUP (the
first handled signal sets all three to ignored and raises inside the run, so the cleanup in `finally` runs
and no later signal cuts it). Only files this run created are removed. A file is registered right after its
O_EXCL create succeeds, with the three signals blocked between the two. An existing path is never this run's:
it is left as it is and the run stops with "TEMPORARY PATH ALREADY EXISTS" (rc 72). If a removal fails, the
run prints "TEMPORARY FILE NOT REMOVED: <path>" and exits 71. SIGKILL and power loss are not covered: back
then checks read-only for `private/.patch-*` and `inputs.json.v6a-tmp` and reports to PM.

restore (only on a PM decision; rc 3 refused; 5 failed): works from whatever state an earlier mock-off, bind
or restore left. It reads the state first and does only the steps still missing, in this order:
  a. /data/codex/auth.json present and NOT the synthetic file (inode, size and mtime differ from the identity
     mock-off recorded) -> refused: a real login is present. The user removes it with `codex logout` first.
     That command removes the stored credentials; whether it also invalidates the session server-side is not
     shown here, so V9 does not rely on it for revocation.
  b. /data/mock-auth.json.r1 present, auth.json absent -> mv -n back; the result must have the recorded
     identity.
  c. StatefulSet without the mock -> the template from the mock-off backup (test = UID, resourceVersion);
     rollout. StatefulSet already back on the mock layout -> no patch, only the Pod check.
  d. inputs.json not the V7a file and a bind backup exists -> the original bytes back (temporary file in the
     same directory, rename) -> must be the V7a file.
  The states it accepts are exactly the ones mock-off, bind and restore can leave: (mock layout, auth.json
  moved away), (mock-free, auth.json absent: after mock-off, or after a logout), (mock-free, synthetic
  auth.json back: restore stopped after b), (mock layout, synthetic auth.json: restore stopped after c), each
  with inputs.json either the V7a file or the bound one. Anything else is refused and printed.

Output is fixed messages, UIDs, states and booleans: no account id, token, Secret data or command error text.
"""
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import time

CANARY = '00000000-0000-4000-8000-000000005804'
OWNERS = ('00000000-0000-4000-8000-000000000048', '00000000-0000-4000-8000-000000005801',
          '00000000-0000-4000-8000-000000005802', '00000000-0000-4000-8000-000000005803', CANARY)
K3S = '/usr/local/bin/k3s'
EVIDENCE = '/root/native68/evidence'
INPUTS = '/etc/anxious-lookout/issue16/controller/inputs.json'
INPUTS_V7A = 'd2fe5debd1c5784a9d37025fe73d95e452ae46e4aecc1d84cd5fbd2e2ab98665'
NS, STS, POD = 'codex-broker', 'codex-broker', 'codex-broker-0'
STS_UID = 'a4016104-57c6-4dc1-b3d5-b271ba029958'
PVC = 'data-codex-broker-0'
PV = 'pvc-57329fcd-9f8b-433b-a8ce-a3c9472b3f34'
BROKER_IMAGE = 'docker.io/library/anxious-dev-runner@sha256:90eb212b24598ec852889df1a4e6f9f916ec73f257dcb2cf739e0f9b695d0d3c'
OVERRIDE = ('CODEX_REFRESH_TOKEN_URL_OVERRIDE', 'http://127.0.0.1:18080/oauth/token')
BROKER_ENV = ['BROKER_ACCOUNT_ID', 'BROKER_RUNNER_NAMESPACE', 'CODEX_HOME', 'HOME']
AUTH, KEPT = '/data/codex/auth.json', '/data/mock-auth.json.r1'
RUNTIME_REF = 'docker.io/library/anxious-dev-runtime@sha256:f5a84ff5d7bf6f00a79fa3d421a8176e6198436d8266b91370f617475a159db5'
# (namespace, name, UID, container carrying the allowlist)
DEPLOYMENTS = (('m2-hosting', 'api', 'fad6799a-6df5-4086-812f-cfae6212adfa', 'api'),
               ('runner-control', 'runner-controller', '41564f26-95e7-4ad1-8af3-d73b93c4669a', 'controller'))
ALLOW_ENV = 'CODEX_USER_RUNNER_ALLOWLIST'
ID_RULE = re.compile(r'[A-Za-z0-9_-]{1,200}')
UUID_RULE = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
# Runs inside the broker container (UID 10001): the broker's own reader; prints the account id only.
READER = ("import sys\nsys.path.insert(0, '/app')\nfrom ops.access_snapshot import selected_file\n"
          "try:\n    _, account, _ = selected_file('/data/codex/auth.json')\n"
          "except Exception as failure:\n    print('REFUSED ' + str(getattr(failure, 'reason', 'unreadable'))[:40]); sys.exit(2)\n"
          "print('ACCOUNT ' + account)\n")


class Stop(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


class Interrupted(BaseException):
    """SIGINT/SIGTERM/SIGHUP, raised inside the run so every `finally` cleanup runs."""


SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


def ignore_signals():
    for signum in SIGNALS:
        signal.signal(signum, signal.SIG_IGN)


def interrupted(signum, frame):
    ignore_signals()  # one handled signal is enough; no later one (of any of the three) can cut the cleanup
    raise Interrupted()


TEMPORARY = []  # files THIS run created (O_EXCL succeeded) that may hold the account id


def create_temporary(path):
    """O_EXCL-create a root 0600 file and register it, with the three signals blocked between the two, so a
    file is registered exactly when this run created it. An existing path is not this run's: it is left as it
    is and the run stops (rc 72)."""
    old = signal.pthread_sigmask(signal.SIG_BLOCK, SIGNALS)
    try:
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            raise Stop(72, 'TEMPORARY PATH ALREADY EXISTS (not created by this run, left as it is): %s (tell PM)'
                       % path) from None
        except OSError:
            raise Stop(72, 'temporary file not created: %s' % path) from None
        TEMPORARY.append(path)
        return fd
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, old)


def remove_temporary():
    """Remove every file this run created and still owns; the paths that could not be removed."""
    old = signal.pthread_sigmask(signal.SIG_BLOCK, SIGNALS)
    try:
        left = []
        while TEMPORARY:
            path = TEMPORARY.pop()
            try:
                os.unlink(path)
            except OSError:
                pass
            if os.path.lexists(path):
                left.append(path)
        return left
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, old)


def say(text):
    print(text, flush=True)


def run(cmd, timeout=60, stdin=None):
    """(rc, stdout) of a fixed command; stderr is discarded (it could echo a patch); 124 timeout, 125 not started."""
    try:
        done = subprocess.run(cmd, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=max(1, timeout), check=False)
    except subprocess.TimeoutExpired:
        return 124, ''
    except Exception:
        return 125, ''
    return done.returncode, done.stdout.decode('utf-8', 'replace').strip()


def kubectl(args, code, message, timeout=60):
    rc, out = run([K3S, 'kubectl'] + args, timeout)
    if rc != 0:
        raise Stop(code, message)
    return out


def kjson(args, code, message):
    try:
        return json.loads(kubectl(args + ['-o', 'json'], code, message))
    except ValueError:
        raise Stop(code, message) from None


def in_broker(script, code, message, timeout=60):
    """rc and stdout of a fixed shell line inside the broker container."""
    return run([K3S, 'kubectl', '-n', NS, 'exec', POD, '-c', 'broker', '--', 'sh', '-c', script], timeout)


def rows(tag, query, code):
    text = 'BEGIN READ ONLY;\n/*v6a:%s*/ %s\nCOMMIT;\n' % (tag, query)
    rc, out = run([K3S, 'kubectl', '-n', 'm2-hosting', 'exec', '-i', 'postgres-0', '--', 'psql', '-X', '-U',
                   'postgres', '-d', 'hosting', '-Atq', '-v', 'ON_ERROR_STOP=1', '-f', '-'], 60, text.encode())
    if rc != 0:
        raise Stop(code, 'database lookup failed (%s)' % tag)
    try:
        return [json.loads(line) for line in out.splitlines() if line.strip() not in ('', 'BEGIN', 'COMMIT')]
    except ValueError:
        raise Stop(code, 'database lookup unreadable (%s)' % tag) from None


# Entry closed ---------------------------------------------------------------------------------------
def entry_closed(code, where, fences=None):
    """No live session, no runner Pod, both allowlists …5804 only on the #68 runtime, five owners idle_stopped
    with no pending work. Returns the fences so the end of an action can compare them with its start."""
    live = rows('sessions', "SELECT json_build_object('n',count(*)) FROM auth.sessions WHERE expires_at>now();", code)
    if live != [{'n': 0}]:
        raise Stop(code, '%s: a live session exists (entry is not closed)' % where)
    pods = kubectl(['-n', 'codex-runner', 'get', 'pods', '-o', 'name'], code, '%s: runner Pod lookup failed' % where)
    if pods.strip():
        raise Stop(code, '%s: a runner Pod exists' % where)
    for ns, name, uid, container in DEPLOYMENTS:
        d = kjson(['-n', ns, 'get', 'deployment', name], code, '%s: %s lookup failed' % (where, name))
        try:
            c = [x for x in d['spec']['template']['spec']['containers'] if x['name'] == container][0]
            allow = [e.get('value') for e in c.get('env', []) if e['name'] == ALLOW_ENV]
            ok = (d['metadata']['uid'] == uid and allow == [CANARY] and c['image'] == RUNTIME_REF
                  and d['spec']['replicas'] == 1 and (d.get('status') or {}).get('readyReplicas') == 1)
        except Exception:
            ok = False
        if not ok:
            raise Stop(code, '%s: %s is not the V7a state (UID, 1/1, #68 runtime, allowlist …5804 only)' % (where, name))
    found = rows('fences', "SELECT json_build_object('owner',r.owner_id,'state',r.state_id,'phase',r.phase,"
                 "'generation',r.generation,'revision',r.revision,'pod',r.pod_uid,'active',r.active_requests,"
                 "'ambiguous',r.ambiguous) FROM runner_runtime.registry r ORDER BY r.owner_id;", code)
    pending = rows('pending', "SELECT json_build_object('stops',(SELECT count(*) FROM runner_termination.stops WHERE "
                   "completed_at IS NULL),'entries',(SELECT count(*) FROM runner_termination.entries WHERE consumed_at "
                   "IS NULL),'outbox',(SELECT count(*) FROM runner_runtime.outbox WHERE NOT settled),'admissions',"
                   "(SELECT count(*) FROM runner_runtime.admissions WHERE completed_at IS NULL));", code)
    if sorted(r['owner'] for r in found) != sorted(OWNERS) or pending != [{'stops': 0, 'entries': 0, 'outbox': 0,
                                                                            'admissions': 0}]:
        raise Stop(code, '%s: registry is not exactly the five owners, or pending runner work exists' % where)
    if any(r['phase'] != 'idle_stopped' or r['pod'] is not None or r['active'] != 0 or r['ambiguous'] for r in found):
        raise Stop(code, '%s: an owner is not idle_stopped without a Pod' % where)
    if fences is not None and found != fences:
        raise Stop(code, '%s: a fence changed during the action' % where)
    return found


def snapshots_clear(code):
    """No Kubernetes or LVM snapshot of the broker volume (a real token must not land in one)."""
    for kind in ('volumesnapshots.snapshot.storage.k8s.io', 'lvmsnapshots.local.openebs.io'):
        items = kjson(['get', kind, '-A'], code, 'snapshot lookup failed (%s)' % kind).get('items', [])
        for item in items:
            text = json.dumps(item.get('spec', {})) + json.dumps(item.get('metadata', {}).get('labels', {}))
            if PVC in text or PV in text or item.get('metadata', {}).get('namespace') == NS:
                raise Stop(code, 'a snapshot of the broker volume exists (%s)' % kind)
    rc, out = run(['/usr/sbin/lvs', '--noheadings', '-o', 'lv_name,origin'])
    if rc != 0:
        raise Stop(code, 'lvs failed')
    if any(len(line.split()) == 2 and line.split()[1] == PV for line in out.splitlines()):
        raise Stop(code, 'an LVM snapshot of the broker LV exists')
    if not any(line.split()[:1] == [PV] for line in out.splitlines()):
        raise Stop(code, 'the broker LV is not where it was recorded')


# Broker ------------------------------------------------------------------------------------------------
def statefulset(code):
    d = kjson(['-n', NS, 'get', 'statefulset', STS], code, 'StatefulSet lookup failed')
    if d['metadata']['uid'] != STS_UID or d['spec'].get('replicas') != 1:
        raise Stop(code, 'StatefulSet is not the recorded object')
    return d


def layout(d):
    t = d['spec']['template']['spec']
    return {'init': [c['name'] for c in t.get('initContainers') or []],
            'containers': [c['name'] for c in t['containers']],
            'images': sorted({c['image'] for c in (t.get('initContainers') or []) + t['containers']}),
            'env': [(e['name'], e.get('value')) for e in t['containers'][0].get('env', [])],
            'volumes': [v['name'] for v in t.get('volumes') or []]}


def mocked(d):
    v = layout(d)
    return (v['init'] == ['seed-synthetic-auth'] and v['containers'] == ['broker', 'mock-oauth']
            and v['images'] == [BROKER_IMAGE] and [n for n, _ in v['env']] == BROKER_ENV + [OVERRIDE[0]]
            and v['env'][4] == OVERRIDE and v['volumes'] == ['tmp', 'mock-oauth'])


def unmocked(d):
    v = layout(d)
    return (v['init'] == [] and v['containers'] == ['broker'] and v['images'] == [BROKER_IMAGE]
            and [n for n, _ in v['env']] == BROKER_ENV and v['volumes'] == ['tmp'])


def broker_pod(code, containers):
    p = kjson(['-n', NS, 'get', 'pod', POD], code, 'broker Pod lookup failed')
    st = p.get('status') or {}
    ready = [c.get('ready') for c in st.get('containerStatuses') or []]
    if st.get('phase') != 'Running' or [c['name'] for c in p['spec']['containers']] != containers \
            or ready != [True] * len(containers):
        raise Stop(code, 'broker Pod is not Running and ready with %s' % containers)
    return p['metadata']['uid']


def present(path, code):
    rc, out = in_broker("if [ -e %s ]; then stat -c '%%u %%a %%h %%F' %s; else echo absent; fi" % (path, path), code, '')
    if rc != 0:
        raise Stop(code, 'broker file check failed')
    return out


def healthz(code):
    rc, out = in_broker("python3 -I -c \"import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=5).status)\"", code, '')
    return rc == 0 and out == '200'


def rollout(code, old_uid, containers):
    if run([K3S, 'kubectl', '-n', NS, 'rollout', 'status', 'statefulset/' + STS, '--timeout=300s'], 330)[0] != 0:
        raise Stop(code, 'broker rollout did not finish')
    deadline = time.time() + 120
    while True:
        try:
            uid = broker_pod(code, containers)
            if uid != old_uid and healthz(code):
                return uid
        except Stop:
            pass
        if time.time() > deadline:
            raise Stop(code, 'broker Pod not proved new, ready and healthy')
        time.sleep(5)


def patch(ops, code, private):
    """kubectl patch --patch-file with a root 0600 file under private/, removed in the same run."""
    path = os.path.join(private, '.patch-%d' % os.getpid())
    fd = create_temporary(path)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(ops, stream)
        return run([K3S, 'kubectl', '-n', NS, 'patch', 'statefulset', STS, '--type=json', '--patch-file', path])[0]
    finally:
        left = remove_temporary()
        if left:
            raise Stop(71, 'TEMPORARY FILE NOT REMOVED: %s (tell PM)' % ', '.join(left))


def identity(path, code):
    """'<inode> <size> <mtime>' of a file in the broker container, or 'absent'. Not secret."""
    rc, out = in_broker("if [ -e %s ]; then stat -c '%%i %%s %%Y' %s; else echo absent; fi" % (path, path), code, '')
    if rc != 0 or not (out == 'absent' or re.fullmatch(r'\d+ \d+ \d+', out)):
        raise Stop(code, 'broker file identity check failed')
    return out


def read_account(code):
    rc, out = run([K3S, 'kubectl', '-n', NS, 'exec', '-i', POD, '-c', 'broker', '--', 'python3', '-I', '-'],
                  60, READER.encode())
    if out.startswith('REFUSED '):
        raise Stop(code, 'auth.json refused by the broker reader: %s' % re.sub(r'[^a-z_]', '', out[8:])[:40])
    if rc != 0 or not out.startswith('ACCOUNT ') or '\n' in out:
        raise Stop(code, 'account id not read')
    account = out[8:]
    if not ID_RULE.fullmatch(account):
        raise Stop(code, 'account id has an unexpected form')
    return account


# Evidence ----------------------------------------------------------------------------------------------
def write_new(path, data, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def runs():
    return sorted(d for d in os.listdir(EVIDENCE) if re.fullmatch(r'v6a-\d{8}T\d{6}Z', d))


def record(ev, **items):
    path = os.path.join(ev, 'run.json')
    value = json.load(open(path)) if os.path.exists(path) else {}
    value.update(items)
    with open(path + '.tmp', 'w') as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(path + '.tmp', path)
    return value


def replace_inputs(data):
    """Write inputs.json via a root 0600 temporary file in the same directory and rename it. The temporary file
    is removed on every handled exit path (it may hold the account id). True when the rename happened."""
    temp = INPUTS + '.v6a-tmp'
    fd = create_temporary(temp)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        old = signal.pthread_sigmask(signal.SIG_BLOCK, SIGNALS)
        try:
            os.replace(temp, INPUTS)
            TEMPORARY.remove(temp)  # renamed: the path is no longer this run's temporary file
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, old)
        return True
    except OSError:
        return False
    finally:
        left = remove_temporary()
        if left:
            raise Stop(71, 'TEMPORARY FILE NOT REMOVED: %s (tell PM)' % ', '.join(left))


def inputs_bytes(code):
    info = os.lstat(INPUTS)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
        raise Stop(code, 'inputs.json is not a root 0600 regular file')
    with open(INPUTS, 'rb') as stream:
        return stream.read()


# Actions -----------------------------------------------------------------------------------------------
def precheck_mock(code=3):
    if os.geteuid() != 0:
        raise Stop(code, 'run as root')
    if runs():
        raise Stop(code, 'an earlier v6a evidence directory exists')
    d = statefulset(code)
    if not mocked(d):
        raise Stop(code, 'StatefulSet is not the recorded mock layout')
    uid = broker_pod(code, ['broker', 'mock-oauth'])
    if present(AUTH, code) != '10001 600 1 regular file' or present(KEPT, code) != 'absent':
        raise Stop(code, 'broker files are not as recorded (synthetic auth.json present, no kept copy)')
    if hashlib.sha256(inputs_bytes(code)).hexdigest() != INPUTS_V7A:
        raise Stop(code, 'inputs.json is not the V7a file')
    snapshots_clear(code)
    fences = entry_closed(code, 'precheck')
    say('precheck ok: broker StatefulSet %s (rv %s) mock layout, Pod %s 2/2; synthetic auth.json present; no '
        'snapshot of the broker volume; inputs.json is the V7a file; entry closed (no session, no runner Pod, '
        'allowlists …5804 only, five owners idle_stopped)' % (STS_UID, d['metadata']['resourceVersion'], uid))
    return d, uid, fences


def mock_off():
    d, old_uid, fences = precheck_mock()
    ev = os.path.join(EVIDENCE, 'v6a-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()))
    os.mkdir(ev, 0o700)
    private = os.path.join(ev, 'private')
    os.mkdir(private, 0o700)
    write_new(os.path.join(private, 'statefulset-before.json'), json.dumps(d, sort_keys=True).encode())
    record(ev, action='mock-off', started=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), oldPod=old_uid,
           resourceVersion=d['metadata']['resourceVersion'])
    say('evidence: %s' % ev)
    synthetic = identity(AUTH, 4)
    record(ev, syntheticIdentity=synthetic, kept='pending')
    rc, _ = in_broker('mv -n %s %s' % (AUTH, KEPT), 4, '')
    if rc != 0 or present(AUTH, 4) != 'absent' or present(KEPT, 4) != '10001 600 1 regular file' \
            or identity(KEPT, 4) != synthetic:
        record(ev, kept='failed')
        raise Stop(4, 'synthetic auth.json not moved to %s with its identity' % KEPT)
    record(ev, kept='done')
    say('synthetic auth.json kept as %s' % KEPT)
    base = '/spec/template/spec'
    ops = [{'op': 'test', 'path': '/metadata/uid', 'value': STS_UID},
           {'op': 'test', 'path': '/metadata/resourceVersion', 'value': d['metadata']['resourceVersion']},
           {'op': 'test', 'path': base + '/initContainers/0/name', 'value': 'seed-synthetic-auth'},
           {'op': 'test', 'path': base + '/containers/0/name', 'value': 'broker'},
           {'op': 'test', 'path': base + '/containers/0/env/4/name', 'value': OVERRIDE[0]},
           {'op': 'test', 'path': base + '/containers/0/env/4/value', 'value': OVERRIDE[1]},
           {'op': 'test', 'path': base + '/containers/1/name', 'value': 'mock-oauth'},
           {'op': 'test', 'path': base + '/volumes/1/name', 'value': 'mock-oauth'},
           {'op': 'remove', 'path': base + '/containers/0/env/4'},
           {'op': 'remove', 'path': base + '/containers/1'},
           {'op': 'remove', 'path': base + '/volumes/1'},
           {'op': 'remove', 'path': base + '/initContainers'}]
    record(ev, patch='pending')
    rc = patch(ops, 5, private)
    after = statefulset(5)
    record(ev, patch='done' if unmocked(after) else ('uncertain' if rc == 0 else 'failed'))
    if not unmocked(after):
        raise Stop(5, 'StatefulSet patch failed (template not the mock-free layout)')
    new_uid = rollout(5, old_uid, ['broker'])
    if present(AUTH, 5) != 'absent' or present(KEPT, 5) != '10001 600 1 regular file':
        raise Stop(5, 'new broker Pod: auth.json present or kept copy missing')
    entry_closed(5, 'end', fences)
    record(ev, newPod=new_uid, result=0, finished=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
    say('mock off: StatefulSet template has only the broker container (no init, no override env, no mock '
        'volume), new Pod %s 1/1, /healthz 200, no auth.json, synthetic copy kept; entry still closed' % new_uid)
    say('next: the user logs in (codex login --device-auth in the broker container), then bind')


def bind():
    if os.geteuid() != 0:
        raise Stop(3, 'run as root')
    found = runs()
    if len(found) != 1:
        raise Stop(3, 'expected exactly one v6a evidence directory (mock-off)')
    ev = os.path.join(EVIDENCE, found[0])
    state = json.load(open(os.path.join(ev, 'run.json')))
    if state.get('action') != 'mock-off' or state.get('result') != 0 or 'bind' in state:
        raise Stop(3, 'mock-off did not finish with rc 0, or bind already ran')
    private = os.path.join(ev, 'private')
    d = statefulset(3)
    env = dict(layout(d)['env'])
    original = inputs_bytes(3)
    synthetic = json.loads(original).get('providerAccountId')
    if not unmocked(d) or env.get('BROKER_ACCOUNT_ID') != synthetic or hashlib.sha256(original).hexdigest() != INPUTS_V7A:
        raise Stop(3, 'StatefulSet or inputs.json is not the post-mock-off state')
    uid = broker_pod(3, ['broker'])
    if uid != state.get('newPod') or present(AUTH, 3) != '10001 600 1 regular file':
        raise Stop(3, 'broker Pod is not the mock-off one, or auth.json is not a 0600 UID 10001 file (log in first)')
    fences = entry_closed(3, 'precheck')
    say('precheck ok: mock-free broker Pod %s, auth.json present (stat only), inputs.json the V7a file, entry closed'
        % uid)
    record(ev, bind='started', bindStarted=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
    account = read_account(4)
    say('account id is UUID-shaped %s; differs from the synthetic id %s' % (bool(UUID_RULE.fullmatch(account)),
                                                                           account != synthetic))
    if account == synthetic:
        raise Stop(4, 'the logged-in account id equals the synthetic one (not a real login)')
    base = '/spec/template/spec/containers/0/env/0'
    ops = [{'op': 'test', 'path': '/metadata/uid', 'value': STS_UID},
           {'op': 'test', 'path': '/metadata/resourceVersion', 'value': d['metadata']['resourceVersion']},
           {'op': 'test', 'path': base + '/name', 'value': 'BROKER_ACCOUNT_ID'},
           {'op': 'test', 'path': base + '/value', 'value': synthetic},
           {'op': 'replace', 'path': base + '/value', 'value': account}]
    record(ev, brokerAccount='pending')
    rc = patch(ops, 5, private)
    after = statefulset(5)
    ok = unmocked(after) and dict(layout(after)['env']).get('BROKER_ACCOUNT_ID') == account
    record(ev, brokerAccount='done' if ok else ('uncertain' if rc == 0 else 'failed'))
    if not ok:
        raise Stop(5, 'broker account patch failed')
    new_uid = rollout(5, uid, ['broker'])
    again = read_account(5)
    say('broker: new Pod %s; env BROKER_ACCOUNT_ID equals the account id True; reader in the new Pod returns the '
        'same id %s' % (new_uid, again == account))
    if again != account:
        raise Stop(5, 'the new broker Pod reads another account id')
    write_new(os.path.join(private, 'inputs.json.before-bind'), original)
    value = json.loads(original)
    value['providerAccountId'] = account
    data = (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()
    record(ev, inputs='pending')
    if not replace_inputs(data):
        record(ev, inputs='failed')
        raise Stop(6, 'inputs.json not written (the temporary file was removed)')
    now = json.loads(inputs_bytes(6))
    same = {k: v for k, v in now.items() if k != 'providerAccountId'} == \
        {k: v for k, v in json.loads(original).items() if k != 'providerAccountId'}
    ok = now.get('providerAccountId') == account and same and sorted(now) == sorted(json.loads(original))
    record(ev, inputs='done' if ok else 'failed')
    say('inputs.json: providerAccountId equals the account id %s; every other key unchanged %s' %
        (now.get('providerAccountId') == account, same))
    if not ok:
        raise Stop(6, 'inputs.json is not the expected result')
    entry_closed(6, 'end', fences)
    record(ev, bind='done', bindPod=new_uid, bindResult=0, bindFinished=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
    say('bound: broker and inputs.json carry the logged-in account id; entry still closed; next stage V6b')


def restore():
    """Does only the steps still missing; see the module docstring for the accepted states."""
    if os.geteuid() != 0:
        raise Stop(3, 'run as root')
    found = runs()
    if len(found) != 1:
        raise Stop(3, 'expected exactly one v6a evidence directory')
    ev = os.path.join(EVIDENCE, found[0])
    private = os.path.join(ev, 'private')
    state = json.load(open(os.path.join(ev, 'run.json')))
    before = json.load(open(os.path.join(private, 'statefulset-before.json')))
    synthetic = state.get('syntheticIdentity')
    d = statefulset(3)
    layout_now = 'mock' if mocked(d) else ('mock-free' if unmocked(d) else 'other')
    auth, kept = identity(AUTH, 3), identity(KEPT, 3)
    auth_kind = 'absent' if auth == 'absent' else ('synthetic' if auth == synthetic else 'other')
    inputs_now = hashlib.sha256(inputs_bytes(3)).hexdigest()
    backup = os.path.join(private, 'inputs.json.before-bind')
    say('restore: state found: StatefulSet %s, auth.json %s, kept copy %s, inputs.json %s' % (
        layout_now, auth_kind, 'present' if kept != 'absent' else 'absent',
        'V7a file' if inputs_now == INPUTS_V7A else 'changed'))
    if layout_now == 'other' or synthetic is None:
        raise Stop(3, 'not a state mock-off, bind or restore can leave (StatefulSet layout, or no recorded identity)')
    if auth_kind == 'other':
        raise Stop(3, 'a real login is present: the user runs `codex logout` in the broker container first')
    if not ((auth_kind == 'absent' and kept == synthetic) or (auth_kind == 'synthetic' and kept == 'absent')):
        raise Stop(3, 'auth.json and the kept copy are not one synthetic file in one of the two places')
    if inputs_now != INPUTS_V7A and not os.path.exists(backup):
        raise Stop(3, 'inputs.json changed but no bind backup exists')
    fences = entry_closed(3, 'precheck')
    if auth_kind == 'absent':
        rc, _ = in_broker('mv -n %s %s' % (KEPT, AUTH), 5, '')
        if rc != 0 or identity(AUTH, 5) != synthetic or identity(KEPT, 5) != 'absent':
            raise Stop(5, 'synthetic auth.json not moved back with its identity')
        record(ev, restoreMovedBack=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
        say('restore: synthetic auth.json moved back (identity as recorded)')
    if layout_now == 'mock-free':
        old_uid = broker_pod(5, ['broker'])
        ops = [{'op': 'test', 'path': '/metadata/uid', 'value': STS_UID},
               {'op': 'test', 'path': '/metadata/resourceVersion', 'value': d['metadata']['resourceVersion']},
               {'op': 'replace', 'path': '/spec/template', 'value': before['spec']['template']}]
        patch(ops, 5, private)
        if not mocked(statefulset(5)):
            raise Stop(5, 'StatefulSet template not restored')
        record(ev, restorePatched=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
        uid = rollout(5, old_uid, ['broker', 'mock-oauth'])
    else:
        if run([K3S, 'kubectl', '-n', NS, 'rollout', 'status', 'statefulset/' + STS, '--timeout=300s'], 330)[0] != 0:
            raise Stop(5, 'broker rollout did not finish')
        uid = broker_pod(5, ['broker', 'mock-oauth'])
        if not healthz(5):
            raise Stop(5, 'broker /healthz is not 200')
    if identity(AUTH, 5) != synthetic:
        raise Stop(5, 'auth.json is not the synthetic file after the rollout')
    if inputs_now != INPUTS_V7A:
        with open(backup, 'rb') as stream:
            data = stream.read()
        if hashlib.sha256(data).hexdigest() != INPUTS_V7A or not replace_inputs(data):
            raise Stop(5, 'inputs.json not restored')
        if hashlib.sha256(inputs_bytes(5)).hexdigest() != INPUTS_V7A:
            raise Stop(5, 'inputs.json not restored')
    entry_closed(5, 'end', fences)
    record(ev, restored=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), restoredPod=uid)
    say('restored: mock layout, Pod %s 2/2, synthetic auth.json in place, inputs.json the V7a file' % uid)


def main(argv):
    if len(argv) != 2 or argv[1] not in ('precheck', 'mock-off', 'bind', 'restore'):
        say('usage: v6a-broker.py precheck|mock-off|bind|restore')
        return 2
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, interrupted)
    code = 0
    try:
        {'precheck': precheck_mock, 'mock-off': mock_off, 'bind': bind, 'restore': restore}[argv[1]]()
    except Stop as stop:
        say('STOP rc %d: %s' % (stop.code, stop.message))
        code = stop.code
    except Interrupted:
        say('STOP rc 130: interrupted by a signal (state printed above; back checks read-only)')
        code = 130
    finally:
        ignore_signals()
        left = remove_temporary()
        if left:
            say('TEMPORARY FILE NOT REMOVED: %s (tell PM)' % ', '.join(left))
            code = 71
    if code == 0:
        say('result rc=0')
    return code


if __name__ == '__main__':
    sys.exit(main(sys.argv))
