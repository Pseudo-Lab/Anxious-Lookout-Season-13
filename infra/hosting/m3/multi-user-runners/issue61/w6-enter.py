"""#61 dev-VM gate W6, steps 1 and 4 (entry): start the …5804 runner once, before the guest reboot (enter1,
gen 4 -> 5) and once after it (enter2, gen 5 -> 6). Run by the user as root in the guest:

    python3 -I /root/issue61/w6-enter.py

Derived from the reviewed w4-enter.py (same session, POST, Pod, image/CRI/token-mount proof). Changes for W6:

mode (from the evidence directory, never chosen by an argument):
  enter1  no w6-* evidence at all. The fence is EXACTLY state 5ff583e9…, idle_stopped, gen 4, rev 16, no Pod,
          0 active, not ambiguous (the W4 end state; anything else is a new decision -> rc 3).
  enter2  exactly one w6-enter1 (result 0), one w6-reboot, one passing w6-observe, no w6-enter2. The run.json
          sha256 values that w6-observe recorded for enter1 and reboot must equal the files on disk; the current
          boot ID must be the B1 that w6-observe proved; the fence must be EXACTLY the gen-5 release that
          w6-observe recorded (same state, idle_stopped, gen 5, its revision, no Pod), and the gen-5 stop it proved
          must still be the completed v2 terminal stop with boot B1. Anything else -> rc 3.
  Each run creates its evidence directory before the session and the POST, so an attempt is never reused,
  whatever its outcome. There is never a third run.

storage (G4): the Pod's native/control volumes are the recorded PVCs; each PVC is Bound to the recorded PV (UID,
local path, capacity, claimRef UID); on the host each target is an ext4 mount of a loop device whose backing file
is the state's <kind>.ext4 (losetup BACK-INO and BACK-MAJ:MIN equal stat of the file). Checked before the POST
(enter1: baseline; enter2: after the reboot's restore) and recorded.

health (bounded): after the Pod proof, /health of this exact runner is read every 5 s for at most 300 s, inside
the runner-controller Pod with the controller's own client. Every read must name this owner, state and Pod UID
(otherwise rc 8). Recorded separately: usable (available true), a concrete failure (available false with a
failure), still not_configured, probe failure. auth_revoked is the environment's expected failure and is not a
W6 failure. Unobserved is never success.

timing (enter1): step 2 (w6-reboot.py) must start before min(POST time, Pod startTime) + 1200 s; printed.

rc (never rerun; never a second POST; back checks read-only and PM decides):
  0  entered; Pod and storage proved; session revoked; health settled usable or auth_revoked
  3  precheck refused: nothing changed (no evidence directory is created)
  4  the session could not be created (no POST; a row this run created is removed)
  5  the POST was answered with an error status; session revoked
  6  the POST outcome is unknown (timeout / connection lost / interrupted): never repeated
  7  the POST was accepted but the Pod was not proved Running within 300 s; no retry, no reclaim
  8  the Pod, its images, token mount, volumes or a health identity is not the proved instance; left as it is
  9  health settled on a concrete failure other than auth_revoked; the runner is left running; PM decides
  10 health still not_configured after 300 s; the runner is left running; PM decides
  11 health never observed within 300 s (probe failures only); the runner is left running; PM decides
  70 the main result was 0 but revoking the session row failed (the row expires within the hour)
  130 interrupted before the POST: no POST; session revoked
Evidence: /root/issue61/evidence/w6-enter{1,2}-<UTC stamp>/run.json (root 0700 dir, 0600 file, fsynced). No secret
is stored. Output is fixed messages, identities, digests and sanitized health only.
"""
import calendar
import hashlib
import http.client
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import time
import uuid

OWNER = '00000000-0000-4000-8000-000000005804'
STATE = '5ff583e9-abe1-4ef8-b277-4a5afead6b80'
STATE_HEX = STATE.replace('-', '')
FENCE1 = {'state': STATE, 'phase': 'idle_stopped', 'generation': 4, 'revision': 16, 'pod': None, 'active': 0,
          'ambiguous': False}
PLAN_SHA = '86c691bae85544e319f0528d69fc3c878d59b919c065099b0adb6e9c92a6a481'
RUNNER_REF = 'docker.io/library/anxious-dev-runner@sha256:067c77de5d6b3673ece9b243f7753a9184a007cd44ab0ea1c93dc3044458a5e4'
EXECUTOR_REF = ('docker.io/library/anxious-dev-shell-executor@sha256:'
                '9c87d626cdf6798db5bc5a562cc68af1b13a36c8f90d3738e19c6553e7cf4d12')
RUNNER_ID = 'sha256:4cac37e2173d5aec7b351e05c2977f2e0df76670f3befb91ce54306d2444e8ad'
EXECUTOR_ID = 'sha256:49b449cb31c5b344f876a9d9aee2d97dd3dcae906c3324bc0eaf00678dd6b883'
CONTAINERS = {'runner': (RUNNER_REF, RUNNER_ID), 'shell-executor': (EXECUTOR_REF, EXECUTOR_ID)}
INIT = {'prepare-shell': RUNNER_REF}
POD_NAME = 'runner-' + OWNER.replace('-', '')
NS = 'codex-runner'
CTRL = ('runner-control', 'runner-controller', '41564f26-95e7-4ad1-8af3-d73b93c4669a')
API = ('m2-hosting', 'api', 'fad6799a-6df5-4086-812f-cfae6212adfa')
ORIGIN = 'http://192.88.99.1'
API_HOST = '10.0.0.107'
API_PORT = 80
HOST_HEADER = '192.88.99.1'
PATH = '/api/research/codex/runner'
BROKER_AUDIENCE = 'codex-broker'
EVIDENCE = '/root/issue61/evidence'
K3S = '/usr/local/bin/k3s'
LOSETUP = '/usr/sbin/losetup'
BOOT_ID = '/proc/sys/kernel/random/boot_id'
MOUNTINFO = '/proc/self/mountinfo'
STORAGE_ROOT = '/var/lib/anxious-lookout/user-runners'
GIB = 1024 ** 3
# Recorded 2026-11-04T05:5xZ (read-only): the state's PVCs and their local PVs.
VOLUMES = {
    'native': {'pvc': 'native-' + STATE_HEX, 'pvcUid': '4b122f91-b409-4d6e-b049-cde699620897',
               'pv': 'native-' + STATE_HEX, 'pvUid': '02a00d97-063a-416f-91fe-364a43ec4204', 'capacity': '5Gi',
               'bytes': 5 * GIB, 'path': STORAGE_ROOT + '/' + STATE_HEX + '/native/data'},
    'control': {'pvc': 'control-' + STATE_HEX, 'pvcUid': '2a7c0559-c161-4ea6-99c7-103d2e6d51f5',
                'pv': 'control-' + STATE_HEX, 'pvUid': '2e72dd83-832a-4300-8107-062f92b04266', 'capacity': '1Gi',
                'bytes': 1 * GIB, 'path': STORAGE_ROOT + '/' + STATE_HEX + '/control/data'},
}
POD_WAIT = max(1, min(300, int(os.environ.get('W6_POD_WAIT_SECONDS', 300))))  # the harness may only shorten it
POST_TIMEOUT = max(1, min(60, int(os.environ.get('W6_POST_TIMEOUT', 60))))  # the harness may only shorten it
HEALTH_WAIT = max(1, min(300, int(os.environ.get('W6_HEALTH_WAIT_SECONDS', 300))))  # may only be shortened
HEALTH_POLL = max(0.2, min(5.0, float(os.environ.get('W6_HEALTH_POLL_SECONDS', 5))))  # may only be shortened
REBOOT_BOUND = 1200
HEALTH_KEYS = ('ownerId', 'stateId', 'podUid', 'available', 'reason', 'verification', 'failure', 'model')
UUID_RE = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
# Read inside the runner-controller Pod with the controller's own code (read-only: a registry SELECT, the Pod
# GET, the runner's GET /health). Prints only the sanitized keys. Unchanged from w4-enter.py.
HEALTH_PROBE = '''
import json, sys
from uuid import UUID
from ops.runner_controller_service import build
from app.runner_registry import Registry
OWNER, POD, KEYS = UUID(%r), %r, %r
service = build()[0]
with service.observer_engine.begin() as db:
    state = Registry(db).read(OWNER)
with service.observer_pool.bound(OWNER) as runtime:
    obj = service.pod(runtime)
    if state is None or obj is None or obj["metadata"]["uid"] != POD or str(state.pod_uid) != POD:
        print(json.dumps({"probe": "refused"})); sys.exit(3)
    health = service.runner(service.address(runtime, obj), runtime.token, OWNER, str(state.state_id)).call("GET", "/health", None)
print(json.dumps(dict({k: health.get(k) for k in KEYS}, probe="health"), sort_keys=True))
'''


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
            null = os.open(os.devnull, os.O_WRONLY)
            os.dup2(null, sys.stdout.fileno())
            os.close(null)
        except Exception:
            pass


def run(cmd, timeout=60, stdin=None):
    try:
        done = subprocess.run(cmd, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=max(1, timeout), check=False)
    except subprocess.TimeoutExpired:
        return 124, ''
    except Exception:
        return 125, ''
    return done.returncode, done.stdout.decode('utf-8', 'replace').strip()


def kubectl(args, code, message, timeout=60, stdin=None):
    rc, out = run([K3S, 'kubectl'] + args, timeout, stdin)
    if rc != 0:
        raise Stop(code, message)
    return out


def psql(tag, body, code, write=False):
    """JSON rows of one statement in its own transaction (READ ONLY unless write), as postgres in postgres-0."""
    text = '%s\n/*w6%s:%s*/ %s\nCOMMIT;\n' % ('BEGIN;' if write else 'BEGIN READ ONLY;', 'w' if write else '', tag, body)
    out = kubectl(['-n', 'm2-hosting', 'exec', '-i', 'postgres-0', '--', 'psql', '-U', 'postgres', '-d', 'hosting',
                   '-Atq', '-v', 'ON_ERROR_STOP=1', '-f', '-'], code, 'database %s failed' % tag, 60, text.encode())
    try:
        found = [json.loads(line) for line in out.splitlines() if line.strip()]
    except ValueError:
        raise Stop(code, 'database %s unreadable' % tag) from None
    if not all(isinstance(row, dict) for row in found):
        raise Stop(code, 'database %s unreadable' % tag)
    return found


def one(tag, body, code, write=False):
    found = psql(tag, body, code, write)
    if len(found) != 1:
        raise Stop(code, 'database %s: expected one row, got %d' % (tag, len(found)))
    return found[0]


def kget(ns, kind, name, code):
    args = (['-n', ns] if ns else []) + ['get', kind, name, '-o', 'json', '--ignore-not-found']
    out = kubectl(args, code, '%s lookup failed' % kind)
    try:
        return json.loads(out) if out else None
    except ValueError:
        raise Stop(code, '%s unreadable' % kind) from None


def digest(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def iso(epoch):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(epoch))


def epoch_of(text):
    """Seconds of a Kubernetes RFC 3339 UTC timestamp (YYYY-MM-DDTHH:MM:SSZ), or None."""
    try:
        return calendar.timegm(time.strptime(text, '%Y-%m-%dT%H:%M:%SZ'))
    except (TypeError, ValueError):
        return None


def sha256_file(path):
    h = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def boot_id():
    with open(BOOT_ID) as stream:
        value = stream.read().strip()
    if not UUID_RE.fullmatch(value):
        raise Stop(3, 'boot ID unreadable')
    return value


RUN = {}


def record(ev, **items):
    """run.json, replaced atomically and fsynced (file and directory)."""
    RUN.update(items)
    if not ev:
        return
    path = os.path.join(ev, 'run.json')
    fd = os.open(path + '.tmp', os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(RUN, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(path + '.tmp', path)
    dfd = os.open(ev, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


# Queries ----------------------------------------------------------------------------------------------
PRE = ("SELECT json_build_object('state',r.state_id,'phase',r.phase,'generation',r.generation,'revision',r.revision,"
       "'pod',r.pod_uid,'active',r.active_requests,'ambiguous',r.ambiguous,'bindingState',b.state_id,'plan',b.plan,"
       "'role',a.role,'approved',a.is_approved,"
       "'allOpenStops',(SELECT count(*) FROM runner_termination.stops WHERE completed_at IS NULL),"
       "'entries',(SELECT count(*) FROM runner_termination.entries e WHERE e.owner_id=r.owner_id AND e.consumed_at IS NULL),"
       "'admissions',(SELECT count(*) FROM runner_runtime.admissions x WHERE x.owner_id=r.owner_id AND x.completed_at IS NULL),"
       "'outbox',(SELECT count(*) FROM runner_runtime.outbox o WHERE o.owner_id=r.owner_id AND NOT o.settled),"
       "'waiting',(SELECT count(*) FROM runner_runtime.registry w WHERE w.phase IN ('provisioning','capacity_wait')),"
       "'liveSessions',(SELECT count(*) FROM auth.sessions s WHERE s.account_id=r.owner_id AND s.expires_at>now())) "
       "FROM runner_runtime.registry r JOIN runner_bindings.bindings b ON b.owner_id=r.owner_id "
       "JOIN auth.accounts a ON a.id=r.owner_id WHERE r.owner_id='%s';" % OWNER)
LIVE = ("SELECT json_build_object('state',r.state_id,'phase',r.phase,'generation',r.generation,'revision',r.revision,"
        "'pod',r.pod_uid,'active',r.active_requests,'ambiguous',r.ambiguous,"
        "'outbox',(SELECT count(*) FROM runner_runtime.outbox o WHERE o.owner_id=r.owner_id AND NOT o.settled),"
        "'openStops',(SELECT count(*) FROM runner_termination.stops s WHERE s.owner_id=r.owner_id AND s.completed_at IS NULL)) "
        "FROM runner_runtime.registry r WHERE r.owner_id='%s';" % OWNER)
CONV = ("SELECT json_build_object('n',count(*),'digest',coalesce(md5(string_agg(id::text||':'||state||':'||version::text,"
        "',' ORDER BY id)),'-')) FROM research.conversations;")
STOP5 = ("SELECT json_build_object('n',count(*),'stop',(SELECT json_build_object('stopId',stop_id,'pod',pod_uid,"
         "'generation',generation,'bootId',boot_id,'nodeUid',node_uid,'completed',completed_at IS NOT NULL,"
         "'version',receipt->'version','terminal',receipt ? 'terminal','proofBoot',proof->>'bootId',"
         "'proofEvidence',proof->>'evidence') FROM runner_termination.stops WHERE owner_id='%s' AND generation=5)) "
         "FROM runner_termination.stops WHERE owner_id='%s' AND generation=5;" % (OWNER, OWNER))


def conversations(code):
    row = one('conv', CONV, code)
    return {'n': row.get('n'), 'digest': row.get('digest')}


# Evidence chain ---------------------------------------------------------------------------------------
def w6_dirs():
    if not os.path.isdir(EVIDENCE):
        return {}
    found = {}
    for name in sorted(os.listdir(EVIDENCE)):
        match = re.fullmatch(r'w6-(enter1|reboot|observe|enter2)-(\d{8}T\d{6}Z)', name)
        if name.startswith('w6-') and not match:
            raise Stop(3, 'precheck: unexpected W6 evidence entry %s; back checks' % name)
        if match:
            found.setdefault(match.group(1), []).append(os.path.join(EVIDENCE, name))
    return found


def read_run(path, what):
    """A run.json of an earlier step: a regular root file, parsed, with its sha256 (partial = refused)."""
    target = os.path.join(path, 'run.json')
    try:
        info = os.lstat(target)
        if not (os.path.isdir(path) and not os.path.islink(path)) or not (info.st_mode & 0o170000 == 0o100000) \
                or info.st_uid != 0:
            raise ValueError
        fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            raw = stream.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError
    except (OSError, ValueError):
        raise Stop(3, 'precheck: the %s record is missing, partial or unreadable; back checks' % what) from None
    return value, hashlib.sha256(raw).hexdigest()


def mode_and_fence():
    """('enter1', FENCE1, None) or ('enter2', fence proved by w6-observe, chain)."""
    dirs = w6_dirs()
    if not dirs:
        return 'enter1', dict(FENCE1), None
    if dirs.get('enter2'):
        raise Stop(3, 'precheck: a re-entry (w6-enter2) already ran; never a third entry')
    if [len(dirs.get(k, [])) for k in ('enter1', 'reboot', 'observe')] != [1, 1, 1]:
        raise Stop(3, 'precheck: re-entry needs exactly one enter1, reboot and observe record; found %s'
                   % json.dumps({k: len(v) for k, v in dirs.items()}, sort_keys=True))
    enter1, enter1_sha = read_run(dirs['enter1'][0], 'enter1')
    reboot, reboot_sha = read_run(dirs['reboot'][0], 'reboot')
    observe, _ = read_run(dirs['observe'][0], 'observe')
    chain = observe.get('chain') or {}
    proved = observe.get('proved') or {}
    if enter1.get('result') != 0 or (enter1.get('mode'), enter1.get('newGeneration')) != ('enter1', 5):
        raise Stop(3, 'precheck: the enter1 record is not a passing gen-5 entry')
    if observe.get('result') != 0 or observe.get('pass') is not True:
        raise Stop(3, 'precheck: the w6-observe record is not a pass')
    if (chain.get('enter1Sha256'), chain.get('rebootSha256')) != (enter1_sha, reboot_sha):
        raise Stop(3, 'precheck: the enter1/reboot records differ from the hashes w6-observe recorded')
    b0, b1 = (reboot.get('before') or {}).get('bootId'), proved.get('bootId')
    if not (isinstance(b1, str) and UUID_RE.fullmatch(b1)) or b1 == b0 or boot_id() != b1:
        raise Stop(3, 'precheck: the current boot is not the B1 that w6-observe proved')
    pod5 = (enter1.get('pod') or {}).get('uid')
    stop = proved.get('stop') or {}
    fence = proved.get('fence') or {}
    if (stop.get('pod') != pod5 or not isinstance(stop.get('stopId'), str) or stop.get('bootId') != b1
            or not (isinstance(fence, dict) and set(fence) == set(FENCE1) and fence.get('generation') == 5
                    and fence.get('state') == STATE and fence.get('phase') == 'idle_stopped' and fence.get('pod') is None
                    and fence.get('active') == 0 and fence.get('ambiguous') is False
                    and type(fence.get('revision')) is int)):
        raise Stop(3, 'precheck: the w6-observe record does not carry the gen-5 release proof')
    row = one('stop5', STOP5, 3)
    live = row.get('stop') or {}
    if (row.get('n') != 1 or live.get('stopId') != stop['stopId'] or live.get('pod') != pod5 or live.get('completed') is not True
            or live.get('version') != 2 or live.get('terminal') is not True or live.get('bootId') != b1
            or live.get('proofBoot') != b1 or live.get('proofEvidence') != 'pod-deleted'):
        raise Stop(3, 'precheck: the gen-5 stop in the database is not the one w6-observe proved')
    return 'enter2', dict(fence), {'enter1Sha256': enter1_sha, 'rebootSha256': reboot_sha, 'b1': b1,
                                   'stopId': stop['stopId'], 'pod5': pod5, 'observe': os.path.basename(dirs['observe'][0])}


# Storage ----------------------------------------------------------------------------------------------
def mount_entries(target):
    with open(MOUNTINFO) as stream:
        lines = stream.read().splitlines()
    found = []
    for line in lines:
        fields = line.split()
        if len(fields) > 4 and fields[4] == target and '-' in fields:
            tail = fields[fields.index('-') + 1:]
            found.append({'majmin': fields[2], 'fstype': tail[0] if tail else '', 'source': tail[1] if len(tail) > 1 else ''})
    return found


def loop_devices():
    rc, out = run([LOSETUP, '-J', '-l', '-O', 'NAME,MAJ:MIN,BACK-INO,BACK-MAJ:MIN,BACK-FILE'])
    try:
        value = json.loads(out) if rc == 0 and out else {'loopdevices': []}
        return {str(d['maj:min']).strip(): d for d in value.get('loopdevices') or []}
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def storage_link(state_hex, sizes):
    """Problems with each <kind> target: an ext4 mount of a loop device whose backing file is <kind>.ext4."""
    problems, linked = [], {}
    loops = loop_devices()
    if loops is None:
        return ['losetup unreadable'], linked
    for kind, size in sizes.items():
        base = os.path.join(STORAGE_ROOT, state_hex)
        backing, target = os.path.join(base, kind + '.ext4'), os.path.join(base, kind)
        try:
            info = os.lstat(backing)
        except OSError:
            problems.append('%s backing missing' % kind)
            continue
        if not (info.st_mode & 0o170000 == 0o100000 and info.st_size == size and info.st_uid == 0 and info.st_nlink == 1
                and not info.st_mode & 0o077 and os.path.realpath(backing) == backing):
            problems.append('%s backing is not a %d-byte root 0600 single-link regular file' % (kind, size))
        if os.path.realpath(target) != target or not os.path.isdir(target):
            problems.append('%s target is not a real directory' % kind)
        entries = mount_entries(target)
        majmins = {e['majmin'] for e in entries}
        if not entries or len(majmins) != 1 or any(e['fstype'] != 'ext4' for e in entries):
            problems.append('%s target is not one ext4 mount' % kind)
            continue
        majmin = majmins.pop()
        loop = loops.get(majmin)
        if not majmin.startswith('7:') or loop is None:
            problems.append('%s mount is not a loop device' % kind)
            continue
        back_majmin = str(loop.get('back-maj:min', '')).strip()
        want = '%d:%d' % (os.major(info.st_dev), os.minor(info.st_dev))
        if loop.get('back-ino') != info.st_ino or back_majmin != want:
            problems.append('%s loop %s backs another file' % (kind, majmin))
            continue
        linked[kind] = {'loop': majmin, 'backIno': info.st_ino, 'backDev': want}
    return problems, linked


def volumes_check():
    """Problems with the state's PVCs/PVs, and the recorded identities."""
    problems, seen = [], {}
    for kind, want in VOLUMES.items():
        pvc = kget(NS, 'pvc', want['pvc'], 3)
        pv = kget(None, 'pv', want['pv'], 3)
        if pvc is None or pv is None:
            problems.append('%s PVC/PV missing' % kind)
            continue
        got = {'pvcUid': pvc['metadata'].get('uid'), 'volumeName': (pvc.get('spec') or {}).get('volumeName'),
               'pvcPhase': (pvc.get('status') or {}).get('phase'),
               'pvcCapacity': ((pvc.get('status') or {}).get('capacity') or {}).get('storage'),
               'pvUid': pv['metadata'].get('uid'), 'pvPhase': (pv.get('status') or {}).get('phase'),
               'pvCapacity': ((pv.get('spec') or {}).get('capacity') or {}).get('storage'),
               'path': ((pv.get('spec') or {}).get('local') or {}).get('path'),
               'claimUid': ((pv.get('spec') or {}).get('claimRef') or {}).get('uid')}
        seen[kind] = got
        if got != {'pvcUid': want['pvcUid'], 'volumeName': want['pv'], 'pvcPhase': 'Bound', 'pvcCapacity': want['capacity'],
                   'pvUid': want['pvUid'], 'pvPhase': 'Bound', 'pvCapacity': want['capacity'], 'path': want['path'],
                   'claimUid': want['pvcUid']}:
            problems.append('%s PVC/PV is not the recorded pair' % kind)
    return problems, seen


# Precheck ---------------------------------------------------------------------------------------------
def precheck():
    if os.geteuid() != 0:
        raise Stop(3, 'precheck: not root')
    mode, want, chain = mode_and_fence()
    for ns, name, uid in (CTRL, API):
        d = kget(ns, 'deployment', name, 3)
        status = (d or {}).get('status') or {}
        if d is None or d['metadata']['uid'] != uid or d['spec'].get('replicas') != 1 or status.get('readyReplicas') != 1:
            raise Stop(3, 'precheck: %s is not the recorded Deployment %s at 1/1' % (name, uid))
    pods = kubectl(['-n', NS, 'get', 'pods', '-o', 'name'], 3, 'precheck: runner Pod lookup failed').split()
    if pods:
        raise Stop(3, 'precheck: a runner Pod exists in %s (%s)' % (NS, ', '.join(pods)))
    row = one('pre', PRE, 3)
    fence = {k: row.get(k) for k in FENCE1}
    if fence != want:
        raise Stop(3, 'precheck: …5804 fence is %s, not %s' % (json.dumps(fence, sort_keys=True),
                                                                json.dumps(want, sort_keys=True)))
    plan = row.get('plan') or {}
    if (row.get('bindingState') != STATE or digest(plan) != PLAN_SHA or plan.get('image') != RUNNER_REF
            or (plan.get('executor') or {}).get('image') != EXECUTOR_REF):
        raise Stop(3, 'precheck: …5804 binding is not the W3b plan %s' % PLAN_SHA)
    blockers = {k: row.get(k) for k in ('allOpenStops', 'entries', 'admissions', 'outbox', 'waiting', 'liveSessions')}
    if any(blockers.values()):
        raise Stop(3, 'precheck: not quiet %s' % json.dumps(blockers, sort_keys=True))
    if row.get('role') not in ('editor', 'admin') or row.get('approved') is not True:
        raise Stop(3, 'precheck: …5804 account not an approved editor')
    origin = kubectl(['-n', 'm2-hosting', 'get', 'configmap', 'hosting-config', '-o',
                      'jsonpath={.data.AUTH_ORIGIN}|{.data.APP_BASE_PATH}'], 3, 'precheck: hosting-config lookup failed')
    if origin != ORIGIN + '|':
        raise Stop(3, 'precheck: API origin/base path is %s, not %s with an empty base path' % (origin, ORIGIN))
    for ref, image in ((RUNNER_REF, RUNNER_ID), (EXECUTOR_REF, EXECUTOR_ID)):
        rc, got = run([K3S, 'crictl', 'inspecti', '-o', 'go-template', '--template', '{{.status.id}}', ref])
        if rc != 0 or got != image:
            raise Stop(3, 'precheck: %s does not resolve to %s' % (ref, image))
    problems, volumes = volumes_check()
    if problems:
        raise Stop(3, 'precheck: storage objects: %s' % '; '.join(problems))
    problems, linked = storage_link(STATE_HEX, {k: v['bytes'] for k, v in VOLUMES.items()})
    if problems:
        raise Stop(3, 'precheck: host storage: %s' % '; '.join(problems))
    conv = conversations(3)
    say('precheck ok (%s): …5804 %s plan %s; no runner Pod, no open stop, nothing pending or waiting; no live '
        'session; origin %s; targets resolve; PVC/PV %s; host mounts %s; conversations n=%s digest=%s'
        % (mode, json.dumps(fence, sort_keys=True), PLAN_SHA[:12], ORIGIN,
           ', '.join('%s %s->%s' % (k, v['pvcUid'], v['pvUid']) for k, v in sorted(volumes.items())),
           ', '.join('%s loop %s ino %s' % (k, v['loop'], v['backIno']) for k, v in sorted(linked.items())),
           conv['n'], conv['digest']))
    if chain:
        say('chain ok: enter1 %s, reboot %s (hashes recorded by %s); boot %s; gen-5 stop %s (v2 terminal, proof boot B1)'
            % (chain['enter1Sha256'][:16], chain['rebootSha256'][:16], chain['observe'], chain['b1'], chain['stopId']))
    return mode, fence, conv, chain, {'volumes': volumes, 'hostMounts': linked}


# Session ----------------------------------------------------------------------------------------------
SESSION = {}  # 'hash' (token hash), 'created' (bool or None = unknown), 'revoked'


def session_hash(token):
    return hashlib.sha256(('m2-origin-session-v1\0' + ORIGIN + '\0' + token).encode()).hexdigest()


def create_session():
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    for value in (token, csrf):
        if not re.fullmatch(r'[A-Za-z0-9_-]{40,}', value):
            raise Stop(4, 'session: token generation failed')
    SESSION.update(hash=session_hash(token), created=None)
    try:
        row = one('insert', "WITH s AS (INSERT INTO auth.sessions(token_hash,account_id,csrf_token,expires_at) VALUES "
                  "('%s','%s','%s',now()+interval '1 hour') RETURNING token_hash,expires_at) SELECT json_build_object("
                  "'id',left(token_hash,16),'expires',expires_at) FROM s;" % (SESSION['hash'], OWNER, csrf), 4, write=True)
    except Stop:
        raise Stop(4, 'session: insert failed or its result is unknown (checked and removed below); no POST made')
    if row.get('id') != SESSION['hash'][:16]:
        raise Stop(4, 'session: insert answered another row')
    SESSION['created'] = True
    say('session created: id %s, expires %s (1 hour), owner …5804' % (row['id'], row.get('expires')))
    return token, csrf


def revoke_session():
    """Delete exactly this run's row (token hash and owner). True when no such row remains."""
    if not SESSION.get('hash') or SESSION.get('created') is False:
        return True
    gone = False
    try:
        row = one('revoke', "WITH d AS (DELETE FROM auth.sessions WHERE token_hash='%s' AND account_id='%s' RETURNING 1) "
                  "SELECT json_build_object('deleted',count(*)) FROM d;" % (SESSION['hash'], OWNER), 70, write=True)
        left = one('session', "SELECT json_build_object('n',count(*)) FROM auth.sessions WHERE token_hash='%s';"
                   % SESSION['hash'], 70)
        gone = left.get('n') == 0
        say('session %s revoked: deleted %s row(s); remaining %s' % (SESSION['hash'][:16], row.get('deleted'), left.get('n')))
    except Exception:
        say('session %s: revoke not proved (the row expires within the hour)' % SESSION['hash'][:16])
    SESSION['revoked'] = gone
    return gone


# POST -------------------------------------------------------------------------------------------------
def post(token, csrf, key):
    """The single POST. (status, sanitized body) or (None, None) when the outcome is unknown."""
    connection = http.client.HTTPConnection(API_HOST, API_PORT, timeout=POST_TIMEOUT)
    try:
        connection.request('POST', PATH, body=b'{}', headers={
            # The route requires Origin == settings.origin before the CSRF check (backend/app/user_runners.py).
            'Host': HOST_HEADER, 'Origin': ORIGIN, 'Cookie': 'anxious_session=' + token, 'x-csrf-token': csrf,
            'Idempotency-Key': key, 'Content-Type': 'application/json', 'Accept': 'application/json'})
        response = connection.getresponse()
        status, raw = response.status, response.read(65536)
    except Exception:
        return None, None
    finally:
        try:
            connection.close()
        except Exception:
            pass
    try:
        body = json.loads(raw)
        safe = {k: body.get(k) for k in ('available', 'reason', 'retryAfterSeconds', 'verification')} \
            if isinstance(body, dict) and 'error' not in body else {'error': ((body or {}).get('error') or {}).get('code')}
    except Exception:
        safe = {'body': 'unreadable'}
    return status, safe


def live(code):
    return one('live', LIVE, code)


# Pod --------------------------------------------------------------------------------------------------
def wait_pod(started, generation):
    """Poll read-only until the Pod is proved Running for the new registry generation, or POD_WAIT has passed."""
    deadline = started + POD_WAIT
    seen = {}
    while True:
        try:
            state = live(7)
            pod = kget(NS, 'pod', POD_NAME, 7)
        except Stop:
            state, pod = {'lookup': 'failed'}, None
        seen = {'registry': state, 'pod': None if pod is None else pod['metadata'].get('uid')}
        if (pod is not None and state.get('generation') == generation and state.get('state') == STATE
                and state.get('pod') == pod['metadata']['uid']
                and (pod['metadata'].get('labels') or {}).get('anxious.io/generation') == str(generation)
                and (pod.get('status') or {}).get('phase') == 'Running' and not pod['metadata'].get('deletionTimestamp')):
            statuses = {s.get('name'): s for s in (pod.get('status') or {}).get('containerStatuses', [])}
            if all('running' in ((statuses.get(n) or {}).get('state') or {}) for n in CONTAINERS):
                return pod, state
        if time.monotonic() >= deadline:
            say('pod not proved Running within %d s: %s' % (POD_WAIT, json.dumps(seen, sort_keys=True, default=str)))
            raise Stop(7, 'the POST was accepted but the Pod is not proved Running; provisioning may continue; no retry, '
                          'no reclaim; back checks read-only')
        time.sleep(min(5, max(0.2, deadline - time.monotonic())))


def crictl_json(args):
    """crictl <verb> -o json <positional…>: crictl reads every word after the first ID as another ID, so the
    options come first (observed on the guest)."""
    rc, out = run([K3S, 'crictl', args[0], '-o', 'json'] + args[1:])
    if rc != 0:
        return None
    try:
        return json.loads(out)
    except ValueError:
        return None


def prove(pod):
    """Problems with the instance, its images, its volumes and its token mount (empty list = proved)."""
    problems = []
    uid = pod['metadata']['uid']
    spec, status = pod.get('spec') or {}, pod.get('status') or {}
    labels = pod['metadata'].get('labels') or {}
    if (labels.get('anxious.io/owner'), labels.get('anxious.io/state')) != (OWNER, STATE):
        problems.append('owner/state labels')
    if [c.get('name') for c in spec.get('containers', [])] != list(CONTAINERS):
        problems.append('containers are %s' % [c.get('name') for c in spec.get('containers', [])])
    if [c.get('name') for c in spec.get('initContainers', [])] != list(INIT):
        problems.append('init containers are %s' % [c.get('name') for c in spec.get('initContainers', [])])
    for c in spec.get('containers', []) + spec.get('initContainers', []):
        want = (CONTAINERS.get(c.get('name')) or (INIT.get(c.get('name')),))[0]
        if c.get('image') != want:
            problems.append('%s spec image %s' % (c.get('name'), c.get('image')))
    statuses = {s.get('name'): s for s in status.get('containerStatuses', [])}
    for name, (ref, config) in CONTAINERS.items():
        s = statuses.get(name) or {}
        if s.get('imageID') != ref:
            problems.append('%s imageID %s (format not the observed repo@manifest reference)' % (name, s.get('imageID')))
        if s.get('restartCount') != 0 or 'running' not in (s.get('state') or {}):
            problems.append('%s not running with 0 restarts' % name)
        match = re.fullmatch(r'containerd://([0-9a-f]{64})', s.get('containerID') or '')
        if not match:
            problems.append('%s containerID format %s' % (name, s.get('containerID')))
            continue
        cid = match.group(1)
        info = crictl_json(['inspect', cid])
        cri = (info or {}).get('status') or {}
        cri_labels = cri.get('labels') or {}
        if cri.get('imageRef') != ref:
            problems.append('%s CRI inspect imageRef %s, want %s' % (name, cri.get('imageRef'), ref))
        if (cri_labels.get('io.kubernetes.pod.uid'), cri_labels.get('io.kubernetes.pod.namespace'),
                cri_labels.get('io.kubernetes.container.name')) != (uid, NS, name):
            problems.append('%s CRI container labels are another instance' % name)
        sandbox = ((info or {}).get('info') or {}).get('sandboxID')
        box = (crictl_json(['inspectp', sandbox]) or {}).get('status') if sandbox else None
        if not sandbox or ((box or {}).get('metadata') or {}).get('uid') != uid:
            problems.append('%s sandbox is not this Pod' % name)
        listed = (crictl_json(['ps', '--id', cid]) or {}).get('containers')
        if (not isinstance(listed, list) or len(listed) != 1 or listed[0].get('id') != cid
                or listed[0].get('imageRef') != config or listed[0].get('podSandboxId') != sandbox):
            problems.append('%s CRI ps --id is not exactly this container on config %s in this sandbox' % (name, config))
        rc, image = run([K3S, 'crictl', 'inspecti', '-o', 'go-template', '--template', '{{.status.id}}', s.get('imageID')])
        if rc != 0 or image != config:
            problems.append('%s inspecti(imageID) %s, want %s' % (name, image or 'none', config))
    inits = {s.get('name'): s for s in status.get('initContainerStatuses', [])}
    for name, ref in INIT.items():
        s = inits.get(name) or {}
        if s.get('imageID') != ref or ((s.get('state') or {}).get('terminated') or {}).get('exitCode') != 0:
            problems.append('init %s not exited 0 on %s' % (name, ref))
    if spec.get('automountServiceAccountToken') is not False:
        problems.append('automountServiceAccountToken is not false')
    volumes = {v.get('name'): v for v in spec.get('volumes', [])}
    for kind, want in VOLUMES.items():
        if ((volumes.get(kind) or {}).get('persistentVolumeClaim') or {}).get('claimName') != want['pvc']:
            problems.append('volume %s is not PVC %s' % (kind, want['pvc']))
    if any(n.startswith('kube-api-access') for n in volumes):
        problems.append('a kube-api-access volume exists')
    broker = (volumes.get('broker-token') or {}).get('projected') or {}
    sources = broker.get('sources') or []
    if (len(sources) != 1 or set(sources[0]) != {'serviceAccountToken'}
            or sources[0]['serviceAccountToken'].get('audience') != BROKER_AUDIENCE
            or sources[0]['serviceAccountToken'].get('path') != 'token'):
        problems.append('broker-token is not exactly one %s-audience projected token' % BROKER_AUDIENCE)
    for c in spec.get('containers', []) + spec.get('initContainers', []):
        for m in c.get('volumeMounts', []):
            if m.get('mountPath', '').startswith('/var/run/secrets/kubernetes.io'):
                problems.append('%s mounts a service-account path' % c.get('name'))
            if m.get('name') == 'broker-token' and (c.get('name') != 'runner' or m.get('mountPath') != '/run/codex-broker'
                                                     or m.get('readOnly') is not True):
                problems.append('broker-token mounted other than read-only at runner:/run/codex-broker')
    if not any(m.get('name') == 'broker-token' for c in spec.get('containers', []) if c.get('name') == 'runner'
               for m in c.get('volumeMounts', [])):
        problems.append('runner has no broker-token mount')
    return problems


def times(pod):
    status = pod.get('status') or {}
    started = {s.get('name'): ((s.get('state') or {}).get('running') or {}).get('startedAt')
               for s in status.get('containerStatuses', [])}
    return status.get('startTime'), started


def health_once(pod_uid):
    """One sanitized /health read through the controller, or None (probe failure)."""
    script = HEALTH_PROBE % (OWNER, pod_uid, HEALTH_KEYS)
    rc, out = run([K3S, 'kubectl', '-n', CTRL[0], 'exec', '-i', 'deploy/' + CTRL[1], '-c', 'controller', '--',
                   'python', '-B', '-'], 90, script.encode())
    lines = [l for l in out.splitlines() if l.startswith('{')]
    try:
        value = json.loads(lines[-1]) if lines else None
    except ValueError:
        value = None
    if rc != 0 or not isinstance(value, dict) or value.get('probe') != 'health':
        return None
    return {k: value.get(k) for k in HEALTH_KEYS}


def classify(health, pod_uid):
    """'probe_failed', 'identity', 'usable', 'auth_revoked', 'failure', 'not_configured' or 'pending'."""
    if health is None:
        return 'probe_failed'
    if (health.get('ownerId'), health.get('stateId'), health.get('podUid')) != (OWNER, STATE, pod_uid):
        return 'identity'
    if health.get('available') is True:
        return 'usable'
    failure = health.get('failure')
    if health.get('available') is False and isinstance(failure, str) and failure:
        return 'auth_revoked' if failure == 'auth_revoked' else 'failure'
    if health.get('available') is False and health.get('reason') == 'not_configured':
        return 'not_configured'
    return 'pending'


def health_poll(pod_uid):
    """Read until usable or a concrete failure, or HEALTH_WAIT has passed. (class, last health, counts)."""
    deadline = time.monotonic() + HEALTH_WAIT
    counts, last = {}, None
    while True:
        health = health_once(pod_uid)
        kind = classify(health, pod_uid)
        counts[kind] = counts.get(kind, 0) + 1
        if health is not None:
            last = health
        if kind in ('identity', 'usable', 'auth_revoked', 'failure'):
            return kind, health, counts
        if time.monotonic() >= deadline:
            return ('not_configured' if counts.get('not_configured') or counts.get('pending') else 'unobserved'), last, counts
        time.sleep(min(HEALTH_POLL, max(0.2, deadline - time.monotonic())))


# Main -------------------------------------------------------------------------------------------------
def main():
    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGHUP, interrupted)
    code, message, ev, posted = 0, '', None, False
    try:
        try:
            mode, fence, conv, chain, storage = precheck()
            generation = fence['generation'] + 1
            ev = os.path.join(EVIDENCE, 'w6-%s-%s' % (mode, time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())))
            os.makedirs(EVIDENCE, mode=0o700, exist_ok=True)
            os.mkdir(ev, 0o700)
            say('evidence: %s' % ev)
            record(ev, mode=mode, newGeneration=generation, started=now(), bootId=boot_id(),
                   before={'fence': fence, 'conversations': conv, 'storage': storage}, chain=chain)
            token, csrf = create_session()
            record(ev, session={'id': SESSION['hash'][:16]})
            key = str(uuid.uuid4())
            post_epoch = time.time()
            say('POST %s (Idempotency-Key %s) at %s' % (PATH, key, iso(post_epoch)))
            posted = True  # from here on, never a second POST
            record(ev, post={'idempotencyKey': key, 'at': iso(post_epoch), 'epoch': post_epoch})
            started = time.monotonic()
            status, body = post(token, csrf, key)
            token = csrf = None
            record(ev, post={'idempotencyKey': key, 'at': iso(post_epoch), 'epoch': post_epoch, 'status': status,
                             'body': body})
            revoke_session()
            record(ev, session={'id': SESSION['hash'][:16], 'revoked': SESSION.get('revoked')})
            if status is None:
                raise Stop(6, 'POST outcome unknown (timeout or connection lost): never repeated; back checks read-only')
            say('POST answered %d %s' % (status, json.dumps(body, sort_keys=True)))
            if status not in (200, 202):
                raise Stop(5, 'POST refused with %d: no runner requested by this run; back checks read-only' % status)
            pod, state = wait_pod(started, generation)
            uid = pod['metadata']['uid']
            start, containers = times(pod)
            start_epoch = epoch_of(start)
            say('pod %s uid %s Running; registry %s; pod start %s; containers %s'
                % (POD_NAME, uid, json.dumps(state, sort_keys=True), start, json.dumps(containers, sort_keys=True)))
            record(ev, pod={'uid': uid, 'start': start, 'startEpoch': start_epoch, 'containers': containers,
                            'containerIds': {s.get('name'): s.get('containerID') for s in
                                             (pod.get('status') or {}).get('containerStatuses', [])},
                            'registry': state})
            problems = prove(pod)
            if problems:
                record(ev, proof={'ok': False, 'problems': problems})
                raise Stop(8, 'instance/image/volume proof failed: %s; the Pod is left as it is, PM decides'
                           % '; '.join(problems))
            say('proof ok: spec images and status imageIDs are the approved references; each containerID resolves '
                'through CRI to this Pod (labels and sandbox) on config %s / %s; init prepare-shell exited 0; '
                'volumes are PVCs %s / %s; automount false, only the %s-audience token at runner:/run/codex-broker'
                % (RUNNER_ID[:19], EXECUTOR_ID[:19], VOLUMES['native']['pvc'], VOLUMES['control']['pvc'], BROKER_AUDIENCE))
            record(ev, proof={'ok': True})
            if mode == 'enter1':
                if start_epoch is None or start_epoch > time.time() + 5:
                    raise Stop(8, 'the Pod startTime is unreadable or in the future; the Pod is left as it is, PM decides')
                bound = min(post_epoch, start_epoch) + REBOOT_BOUND
                record(ev, rebootBy={'epoch': bound, 'at': iso(bound)})
            kind, health, counts = health_poll(uid)
            say('health %s: %s; class %s; reads %s' % (now(), json.dumps(health, sort_keys=True) if health else 'not observed',
                                                      kind, json.dumps(counts, sort_keys=True)))
            record(ev, health={'at': now(), 'class': kind, 'last': health, 'counts': counts})
            after = conversations(9)
            say('conversations n=%s digest=%s (%s)' % (after['n'], after['digest'],
                                                      'unchanged' if after == conv else 'CHANGED'))
            record(ev, after={'conversations': after})
            if kind == 'identity':
                raise Stop(8, 'a health read named another owner, state or Pod; the Pod is left as it is, PM decides')
            if kind == 'failure':
                raise Stop(9, 'health settled on a concrete failure other than auth_revoked; the runner is left running; '
                              'PM decides')
            if kind == 'not_configured':
                raise Stop(10, 'health still not settled (not_configured) after %d s; the runner is left running; '
                               'PM decides' % HEALTH_WAIT)
            if kind == 'unobserved':
                raise Stop(11, 'health never observed within %d s (probe failures only); the runner is left running; '
                               'PM decides' % HEALTH_WAIT)
            if mode == 'enter1':
                say('NEXT (step 2, separate user go): w6-reboot.py must START before %s (min(POST, Pod start) + %d s); '
                    'later it refuses' % (RUN['rebootBy']['at'], REBOOT_BOUND))
            else:
                say('NEXT: the gen-%d runner is left to the automatic idle drain; W7 (read-only) by Pod start + 60 min '
                    '= %s' % (generation, iso(start_epoch + 3600) if start_epoch else 'unknown'))
        except Stop as stop:
            code, message = stop.code, stop.message
        except Interrupted:
            code, message = (6, 'interrupted after the POST began') if posted else (130, 'interrupted before the POST')
        except Exception:
            code, message = (6, 'unexpected error after the POST began') if posted else (3, 'unexpected error')
    except BaseException:
        if not message:
            code, message = (6 if posted else 130), 'interrupted'
    return finish(code, message, ev, posted)


def finish(code, message, ev, posted):
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, signal.SIG_IGN)
        except Exception:
            pass
    if message:
        say('STOPPED (%d): %s' % (code, message))
    revoked = True
    if SESSION.get('hash') and 'revoked' not in SESSION:
        if SESSION.get('created') is None:
            # The insert's result was unknown: look for the row; if it exists (or the lookup fails) delete it.
            try:
                found = one('session', "SELECT json_build_object('n',count(*)) FROM auth.sessions WHERE "
                            "token_hash='%s';" % SESSION['hash'], 4).get('n')
                SESSION['created'] = {0: False, 1: True}.get(found)
            except Exception:
                SESSION['created'] = None
            say('session insert result was unknown: row %s' % {True: 'found', False: 'absent'}.get(SESSION['created'],
                                                                                              'unknown'))
        revoked = revoke_session()
    elif SESSION.get('hash'):
        revoked = SESSION.get('revoked') is True
    if posted and code in (5, 6, 7):
        try:
            say('live (read-only): %s' % json.dumps(live(code), sort_keys=True, default=str))
            pod = kget(NS, 'pod', POD_NAME, code)
            say('pod: %s' % ('absent' if pod is None else '%s %s' % (pod['metadata'].get('uid'),
                                                                     (pod.get('status') or {}).get('phase'))))
        except Exception:
            say('live state lookup failed')
    if not revoked:
        say('SESSION NOT PROVED REVOKED: id %s' % SESSION.get('hash', '')[:16])
        if code == 0:
            code = 70
    try:
        record(ev, result=code, finished=now(), message=message,
               session={'id': SESSION.get('hash', '')[:16], 'revoked': SESSION.get('revoked')})
    except Exception:
        say('evidence write failed at the end (the directory still blocks a rerun)')
    say('result rc=%d' % code)
    return code


if __name__ == '__main__':
    sys.exit(main())
