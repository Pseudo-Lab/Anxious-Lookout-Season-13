"""#61 dev-VM gate W6, attempt 2, step 2 (the one irreversible step; chained after step 1 by the PM decision of
2026-11-04T10:37Z): final checks, pre-reboot evidence,
then ONE guest reboot request. Run by the user as root in the guest, in the same ssh command as step 1 and only
if step 1 returned rc 0 (`&&`); this script does not rely on the chain: its own precheck and bound decide.

    python3 -I /root/issue61/w6a2-reboot.py

1. precheck (read-only; nothing changes, no evidence directory):
   - evidence: attempt 1's w6-* records are exactly w6-enter1-20261104T095411Z (read, never written); exactly
     one w6a2-enter1 record with result 0 (gen 6, Pod UID, POST time, Pod start, health class
     usable or auth_revoked); no w6a2-reboot, w6a2-observe or w6a2-enter2 yet.
   - time fence: bound = min(enter1 POST time, Pod startTime) + 1200 s (equal to the bound enter1 recorded). The
     guest clock and the database clock agree within 5 s; the POST and Pod start are not in the future and the
     Pod did not start before the POST; now <= bound. last_activity is recorded as context only.
   - registry …5804: state 5ff583e9…, gen 6, Pod = the enter1 UID, phase ready or runner_unreachable (ambiguous
     only with runner_unreachable), 0 active; 5 stops for …5804 in all (as before attempt 2), no gen-6 stop, no open stop anywhere, no unconsumed entry, open
     admission or unsettled outbox for …5804, no owner waiting; 0 conversations running.
   - Pod: the same UID, Running, both containers running with 0 restarts, not being deleted; it is the only Pod in
     codex-runner. No Pod in a namespace outside the known set; none in shell-full / shell-pilot (the #64 paid
     window is closed; those namespace objects may still exist, empty, and are recorded as such).
   - processes: no `codex … login`, `--device-auth`, `runner_controller_service … reclaim` or w3-reclaim process
     (a snapshot that the #61 window commitment holds, not the guarantee itself).
   - restore, two separate read-only mirrors (nothing is mounted, nothing is restored):
     (a) worker.py restore_inventory + manifest() + retained_file(): roots, .lock files, per-owner directories,
         storage-restore.json (root 0600, single link, <= 64 KiB, exact keys/version/layout/path), runtime.json
         (uid 10001 0600, single link, <= 1 MB, unchanged during the read, plan owner/state), no duplicate state,
         no stray storage entry; plus the exact recorded owner -> state map (5 owners).
     (b) runner_storage.restore()/verify() of the pinned provisioner image (tag 50ac30b; sizes from restore.sh:
         native 5 GiB, control 1 GiB): state dir, shared propagation of the storage root, backing files (exact
         size, uid 0, one link, no group/other bits, real path), targets real directories and mounted ext4 with
         0 < size <= limit; and each target's mount is the loop device whose backing inode is that file.
     Pinned hashes of restore.sh, storage-root.sh, worker.py, stop-restore.sh, both units, k3s.service and its
     drop-in; the restore image present in Docker; k3s and docker enabled and active; issue16-restore and
     issue16-storage-root active with Result=success; host issue16 controller/observer/supply not running.
   - Node anxious-lookout (recorded UID) Ready; its bootID equals /proc (B0); hostname simple; controller at the
     recorded Deployment, 1/1.
2. evidence (root 0700 dir, files 0600 O_EXCL, each fsynced, then the directory): free space >= 2 GiB first;
   the current-boot k3s journal (journalctl -u k3s -b -o json) in full: size and sha256 recorded, never printed;
   above the 16,000,000-byte tool cap it is recorded as over cap, never as empty; a copy of the k3s containerd
   log with its source metadata; PID/cgroup namespace IDs, hostname, B0, Node UID/name/hostname label and k3s
   hostname annotation, controller and workload identities. Any write, space or fsync failure: rc 4, NO reboot.
3. final look (no slow work after it): same boot; the registry checks of step 1 again with the same state, gen,
   Pod UID and stop count (phase may be ready or runner_unreachable); the same Pod; still inside the time fence.
4. intent: {"intent": B0, time} is written and fsynced BEFORE the call; then `systemctl reboot` is called ONCE.
   Signals are ignored from the final look on. The call's rc is recorded if the system lets us. Nothing ever
   calls it a second time.

"reboot requested" (and an ssh `remote rc=255` after it) only says the request was made. Success is shown by
w6a2-observe.py after the guest is back: same Node UID, B1 != B0 on /proc and the Node API, this boot's restore
PASS and Result=success, Node Ready.

rc (never rerun; back checks read-only and PM decides):
  0   reboot requested (systemctl returned 0); the ssh session will drop
  3   precheck refused: nothing changed
  4   the evidence could not be saved completely (space, write, fsync, export rc): NO reboot; the runner keeps
      running and the idle drain stops it at about 1800 s
  5   the final look differs from the precheck (state, Pod, boot or time): NO reboot
  7   `systemctl reboot` returned non-zero or timed out: not repeated; PM decides
  8   reboot requested (systemctl returned 0), but the call result or the final record could not be written;
      the request is NOT repeated; w6a2-observe proves the boot
  130 interrupted before the final look: NO reboot
lock: the root-only /root/issue61/w6a2.lock (flock, non-blocking) is taken before the evidence directory is read and
held until the process ends, so a second W6 process is refused (rc 3) before any evidence, intent or reboot call.
conversations: the current snapshot must equal enter1's before and after snapshots (a changed baseline is refused).
final look: also repeats the process scan (login/reclaim) and the Pod/namespace overview (#64 paid namespaces,
unknown namespaces, other runner Pods) after the evidence export, and the conversation snapshot.
"""
import calendar
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
from uuid import UUID

OWNER = '00000000-0000-4000-8000-000000005804'
STATE = '5ff583e9-abe1-4ef8-b277-4a5afead6b80'
ATTEMPT = 'a2'
GEN1 = 6  # attempt 2: the generation enter1 starts and this step reboots under
ATTEMPT1 = ('w6-enter1-20261104T095411Z',)  # attempt 1's only record: read-only, never written
STOPS0 = 5  # …5804 stops before attempt 2 (read-only diagnosis 2026-11-04T10:34Z)
POD_NAME = 'runner-' + OWNER.replace('-', '')
NS = 'codex-runner'
NODE = ('anxious-lookout', '3a59eb5e-0800-4445-af68-9ae0bf4ed457')
HOSTNAME = 'simple'
CTRL = ('runner-control', 'runner-controller', '41564f26-95e7-4ad1-8af3-d73b93c4669a')
WORKLOADS = {  # recorded 2026-11-04T05:5xZ
    'deployment/runner-control/runner-controller': '41564f26-95e7-4ad1-8af3-d73b93c4669a',
    'deployment/m2-hosting/api': 'fad6799a-6df5-4086-812f-cfae6212adfa',
    'deployment/m2-hosting/web': 'd35eafcc-6d75-43c8-b093-fee29ec9b6d4',
    'statefulset/m2-hosting/postgres': 'a5ce2d32-8688-4d9a-a003-66c71982681b',
    'statefulset/codex-broker/codex-broker': 'a4016104-57c6-4dc1-b3d5-b271ba029958',
}
KNOWN_NS = {'kube-system', 'runner-control', 'codex-broker', 'codex-runner', 'm2-hosting', 'csi-test', 'fqdn-check',
            'openebs', 'topolvm-system'}
PAID_NS = ('shell-full', 'shell-pilot')  # #64 remnants: must hold no Pod
EVIDENCE = '/root/issue61/evidence'
K3S = '/usr/local/bin/k3s'
LOSETUP = '/usr/sbin/losetup'
SYSTEMCTL = '/usr/bin/systemctl'
JOURNALCTL = '/usr/bin/journalctl'
DOCKER = '/usr/bin/docker'
BOOT_ID = '/proc/sys/kernel/random/boot_id'
MOUNTINFO = '/proc/self/mountinfo'
PROC = '/proc'
ROOT = '/var/lib/anxious-lookout/issue16-d4/provision-output'
STORAGE = '/var/lib/anxious-lookout/user-runners'
CONTAINERD_LOG = '/var/lib/rancher/k3s/agent/containerd/containerd.log'
GIB = 1024 ** 3
SIZES = {'native': 5 * GIB, 'control': 1 * GIB}  # restore.sh: CODEX_RUNNER_NATIVE/CONTROL_SIZE_GIB=5/1
RUNTIME_UID = 10001
OWNERS = {  # owner hex -> state hex (storage-restore.json of each, read 2026-11-04)
    '00000000000040008000000000000048': '34823dfb6b504b8eb230191739bdc57d',
    '00000000000040008000000000005801': 'eb766aef80164439b2175ae6b9927a4c',
    '00000000000040008000000000005802': 'cfd1fedcdd6b40898df2c1070a6146e1',
    '00000000000040008000000000005803': '0383c764bea948aa836805220946e4c9',
    '00000000000040008000000000005804': '5ff583e9abe14ef8b2774a5afead6b80',
}
RESTORE_IMAGE = 'sha256:5fde2ed7dc1d9ffa8a2ffa4e8980aa98da6eed5ccd324e9521b751000f7cf8a3'
PINNED = {  # read 2026-11-04 (read-only)
    '/usr/local/lib/anxious-lookout/issue16/restore.sh': '3e519a67f11297d5911d72b06db1dd14b430be5c7b342a4d8ca129fa255b4c51',
    '/usr/local/lib/anxious-lookout/issue16/storage-root.sh': '2528d9284779886a0ac26a574baf3240d6b5742209765ba05899691522c429d5',
    '/usr/local/lib/anxious-lookout/issue16/worker.py': '77244e29f5ecb7f0ccdfa53c0bf62bf8fce3b21b86371fb58964944db4ce8afe',
    '/usr/local/lib/anxious-lookout/issue16/stop-restore.sh': 'ef15935ca6326d4a02bb4951e2b0a1fd047dac91cd05f8655deb3f630eeb49e7',
    '/etc/systemd/system/issue16-restore.service': '98d2e2a9eb060e8c4552e5ab8f9d6850bf36dc9366cd0b8c693ec4662a2c119a',
    '/etc/systemd/system/issue16-storage-root.service': '760d70691d9835b6c4d66871238ec060dc16f19be6cd493548641a9587056b8f',
    '/etc/systemd/system/k3s.service.d/issue16.conf': '35bb3a30b5d0d7265d8b853048bfb39b67121341fc14ffa17195254828688d00',
    '/etc/systemd/system/k3s.service': 'adb161a79a851f710cb20acac0721e02c65b67c815fa86f6075a3be7b5997d96',
}
UNITS_UP = ('k3s.service', 'docker.service')
UNITS_DONE = ('issue16-restore.service', 'issue16-storage-root.service')
UNITS_IDLE = ('issue16-controller.service', 'issue16-controller.timer', 'issue16-observer.service',
              'issue16-observer.timer', 'issue16-supply.service', 'issue16-supply.timer', 'issue16-restore.timer')
PROCESS_PATTERNS = {
    'codex-login': re.compile(r'\bcodex\S*\s+(?:\S+\s+)*login\b'),
    'device-auth': re.compile(r'--device-auth\b'),
    'controller-reclaim': re.compile(r'runner_controller_service\S*\s+(?:\S+\s+)*reclaim\b'),
    'w3-reclaim': re.compile(r'w3-reclaim'),
}
REBOOT_BOUND = 1200
CLOCK_SKEW = 5
FREE_MIN = 2 * GIB
TOOL_CAP = 16000000
EXPORT_TIMEOUT = max(5, min(900, int(os.environ.get('W6_EXPORT_TIMEOUT', 900))))  # the harness may only shorten it
UUID_RE = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
LOCK = '/root/issue61/w6a2.lock'


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
    try:
        print(text, flush=True)
    except Exception:
        try:
            null = os.open(os.devnull, os.O_WRONLY)
            os.dup2(null, sys.stdout.fileno())
            os.close(null)
        except Exception:
            pass


def take_lock():
    """The W6 step lock, held by this process until it exits. Refused (rc 3) if another W6 step holds it."""
    try:
        fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    except OSError:
        raise Stop(3, 'precheck: the W6 lock file is unusable') from None
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise Stop(3, 'precheck: the W6 lock file is not a root-only regular file')
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise Stop(3, 'precheck: another W6 step is running (lock held); nothing done') from None
    return fd


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


def kjson(args, code, message):
    out = kubectl(args + ['-o', 'json'], code, message)
    try:
        return json.loads(out) if out else None
    except ValueError:
        raise Stop(code, message) from None


def one(tag, body, code):
    text = 'BEGIN READ ONLY;\n/*w6r:%s*/ %s\nCOMMIT;\n' % (tag, body)
    out = kubectl(['-n', 'm2-hosting', 'exec', '-i', 'postgres-0', '--', 'psql', '-U', 'postgres', '-d', 'hosting',
                   '-Atq', '-v', 'ON_ERROR_STOP=1', '-f', '-'], code, 'database %s failed' % tag, 60, text.encode())
    try:
        rows = [json.loads(line) for line in out.splitlines() if line.strip()]
    except ValueError:
        raise Stop(code, 'database %s unreadable' % tag) from None
    if len(rows) != 1 or not isinstance(rows[0], dict):
        raise Stop(code, 'database %s: expected one row' % tag)
    return rows[0]


def now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def iso(epoch):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(epoch))


def boot_id():
    with open(BOOT_ID) as stream:
        value = stream.read().strip()
    if not UUID_RE.fullmatch(value):
        raise Stop(3, 'boot ID unreadable')
    return value


def sha256_path(path, limit=None):
    h = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


RUN = {}


def record(ev, **items):
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
    fsync_dir(ev)


# Evidence chain ---------------------------------------------------------------------------------------
def w6_dirs():
    """The attempt-2 records by kind. Attempt 1's records are read, never written: they must be exactly ATTEMPT1."""
    if not os.path.isdir(EVIDENCE):
        raise Stop(3, 'precheck: the evidence directory (with the attempt-1 record) is missing; back checks')
    found, attempt1 = {}, []
    for name in sorted(os.listdir(EVIDENCE)):
        if name.startswith('w6-'):
            attempt1.append(name)
            continue
        match = re.fullmatch(r'w6a2-(enter1|reboot|observe|enter2)-(\d{8}T\d{6}Z)', name)
        if name.startswith('w6a2-') and not match:
            raise Stop(3, 'precheck: unexpected W6 evidence entry %s; back checks' % name)
        if match:
            found.setdefault(match.group(1), []).append(os.path.join(EVIDENCE, name))
    if tuple(attempt1) != ATTEMPT1:
        raise Stop(3, 'precheck: attempt-1 records are %s, not exactly %s; back checks'
                   % (', '.join(attempt1) or 'none', ', '.join(ATTEMPT1)))
    return found


def read_run(path, what):
    target = os.path.join(path, 'run.json')
    try:
        info = os.lstat(target)
        if os.path.islink(path) or not stat.S_ISREG(info.st_mode) or info.st_uid != 0:
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


def enter1_record():
    dirs = w6_dirs()
    if [len(dirs.get(k, [])) for k in ('enter1', 'reboot', 'observe', 'enter2')] != [1, 0, 0, 0]:
        raise Stop(3, 'precheck: needs exactly one w6a2-enter1 and no later W6 record; found %s'
                   % json.dumps({k: len(v) for k, v in dirs.items()}, sort_keys=True))
    value, sha = read_run(dirs['enter1'][0], 'enter1')
    pod = value.get('pod') or {}
    post = value.get('post') or {}
    bound = (value.get('rebootBy') or {}).get('epoch')
    if (value.get('result') != 0 or value.get('mode') != 'enter1' or value.get('newGeneration') != GEN1
            or not UUID_RE.fullmatch(str(pod.get('uid'))) or not isinstance(post.get('epoch'), (int, float))
            or not isinstance(pod.get('startEpoch'), int) or not isinstance(bound, (int, float))
            or (value.get('health') or {}).get('class') not in ('usable', 'auth_revoked')
            or (value.get('proof') or {}).get('ok') is not True):
        raise Stop(3, 'precheck: the enter1 record is not a passing gen-%d entry with Pod, POST time and bound' % GEN1)
    return value, sha, os.path.basename(dirs['enter1'][0])


def time_fence(enter1, db_epoch):
    """Problems with the time fence (empty = inside), and the bound."""
    t = time.time()
    post, start = enter1['post']['epoch'], enter1['pod']['startEpoch']
    bound = min(post, start) + REBOOT_BOUND
    problems = []
    if abs(t - db_epoch) > CLOCK_SKEW:
        problems.append('guest and database clocks differ by %.1f s' % (t - db_epoch))
    if post > t + 1 or start > t + 1:
        problems.append('the POST or Pod start is in the future')
    if start < post - CLOCK_SKEW:
        problems.append('the Pod started before the POST')
    if abs(bound - enter1['rebootBy']['epoch']) > 1:
        problems.append('the bound differs from the one enter1 recorded')
    if t > bound:
        problems.append('too late: %s is past the bound %s' % (iso(t), iso(bound)))
    return problems, bound


# Read-only state --------------------------------------------------------------------------------------
REG = ("SELECT json_build_object('state',r.state_id,'phase',r.phase,'generation',r.generation,'revision',r.revision,"
       "'pod',r.pod_uid,'active',r.active_requests,'ambiguous',r.ambiguous,'lastActivity',r.last_activity,"
       "'allOpenStops',(SELECT count(*) FROM runner_termination.stops WHERE completed_at IS NULL),"
       "'stopRun',(SELECT count(*) FROM runner_termination.stops s WHERE s.owner_id=r.owner_id AND s.generation=%(g)d),"
       "'stops',(SELECT count(*) FROM runner_termination.stops s WHERE s.owner_id=r.owner_id),"
       "'entries',(SELECT count(*) FROM runner_termination.entries e WHERE e.owner_id=r.owner_id AND e.consumed_at IS NULL),"
       "'admissions',(SELECT count(*) FROM runner_runtime.admissions x WHERE x.owner_id=r.owner_id AND x.completed_at IS NULL),"
       "'outbox',(SELECT count(*) FROM runner_runtime.outbox o WHERE o.owner_id=r.owner_id AND NOT o.settled),"
       "'waiting',(SELECT count(*) FROM runner_runtime.registry w WHERE w.phase IN ('provisioning','capacity_wait')),"
       "'running',(SELECT count(*) FROM research.conversations c WHERE c.state='running'),"
       "'conversations',(SELECT json_build_object('n',count(*),'digest',coalesce(md5(string_agg(id::text||':'||state||':'"
       "||version::text,',' ORDER BY id)),'-')) FROM research.conversations),"
       "'dbEpoch',extract(epoch from clock_timestamp())) "
       "FROM runner_runtime.registry r WHERE r.owner_id='%(o)s';" % {'o': OWNER, 'g': GEN1})


def registry_problems(row, pod_uid):
    problems = []
    if (row.get('state'), row.get('generation'), row.get('pod')) != (STATE, GEN1, pod_uid):
        problems.append('registry is not state %s gen %d Pod %s' % (STATE, GEN1, pod_uid))
    if row.get('phase') not in ('ready', 'runner_unreachable'):
        problems.append('registry phase %s' % row.get('phase'))
    if row.get('ambiguous') is not False and not (row.get('ambiguous') is True and row.get('phase') == 'runner_unreachable'):
        problems.append('registry ambiguous outside runner_unreachable')
    if row.get('stops') != STOPS0:
        problems.append('…5804 has %s stops, not the %d before attempt 2' % (row.get('stops'), STOPS0))
    for key in ('active', 'allOpenStops', 'stopRun', 'entries', 'admissions', 'outbox', 'waiting', 'running'):
        if row.get(key) != 0:
            problems.append('%s=%s' % (key, row.get(key)))
    if not isinstance(row.get('dbEpoch'), (int, float)):
        problems.append('database clock unreadable')
    return problems


def pod_problems(pod, pod_uid):
    if pod is None:
        return ['the runner Pod is gone']
    problems = []
    if pod['metadata'].get('uid') != pod_uid:
        problems.append('the runner Pod is another UID')
    if pod['metadata'].get('deletionTimestamp'):
        problems.append('the runner Pod is being deleted')
    if (pod.get('status') or {}).get('phase') != 'Running':
        problems.append('the runner Pod phase is %s' % (pod.get('status') or {}).get('phase'))
    statuses = (pod.get('status') or {}).get('containerStatuses') or []
    if sorted(s.get('name') for s in statuses) != ['runner', 'shell-executor'] or any(
            s.get('restartCount') != 0 or 'running' not in (s.get('state') or {}) for s in statuses):
        problems.append('the runner containers are not both running with 0 restarts')
    return problems


def pods_overview(pod_uid, code=3):
    listed = kjson(['get', 'pods', '-A'], code, 'Pod list failed')
    items = listed.get('items') if isinstance(listed, dict) else None
    if not isinstance(items, list):
        raise Stop(code, 'Pod list unreadable')
    problems, paid = [], {ns: 0 for ns in PAID_NS}
    for p in items:
        ns, uid = p['metadata'].get('namespace'), p['metadata'].get('uid')
        if ns in paid:
            paid[ns] += 1
        elif ns == NS:
            if uid != pod_uid:
                problems.append('another Pod in %s' % NS)
        elif ns not in KNOWN_NS:
            problems.append('a Pod in unknown namespace %s' % ns)
    for ns, n in paid.items():
        if n:
            problems.append('%d Pod(s) in %s' % (n, ns))
    out = kubectl(['get', 'namespace', '-o', 'name'], code, 'namespace list failed').split()
    present = [ns for ns in PAID_NS if 'namespace/' + ns in out]
    return problems, {'paidNamespacesPresent': present, 'paidNamespacePods': paid, 'pods': len(items)}


def process_scan():
    hits = {k: 0 for k in PROCESS_PATTERNS}
    me = os.getpid()
    for name in os.listdir(PROC):
        if not name.isdigit() or int(name) == me:
            continue
        try:
            with open(os.path.join(PROC, name, 'cmdline'), 'rb') as stream:
                line = stream.read(65536).replace(b'\0', b' ').decode('utf-8', 'replace')
        except OSError:
            continue
        for key, pattern in PROCESS_PATTERNS.items():
            if pattern.search(line):
                hits[key] += 1
    return hits


# Restore mirrors --------------------------------------------------------------------------------------
class Refused(Exception):
    pass


def need(ok, what):
    if not ok:
        raise Refused(what)


def is_canonical(value):
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except ValueError:
        return False


def is_hex(name):
    try:
        return UUID(hex=name).hex == name
    except ValueError:
        return False


def manifest(path):
    """worker.py manifest(): root 0600 single-link regular file <= 64 KiB, opened without following links."""
    need(os.path.realpath(path) == path and stat.S_ISREG(os.lstat(path).st_mode), 'manifest path')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as source:
        s = os.fstat(source.fileno())
        need(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o077 and s.st_nlink == 1
             and s.st_size <= 65536, 'manifest file')
        return json.load(source)


def retained_file(path):
    """runner_binding_import.retained_file(): uid-10001 0600 single-link file <= 1 MB, unchanged during the read."""
    need(os.path.isabs(path) and os.path.realpath(path) == path and not os.path.islink(path), 'runtime path')
    parent = os.stat(os.path.dirname(path))
    need(parent.st_uid == RUNTIME_UID and stat.S_ISDIR(parent.st_mode) and not parent.st_mode & 0o077, 'runtime parent')
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as source:
        info = os.fstat(source.fileno())
        need(stat.S_ISREG(info.st_mode) and info.st_uid == RUNTIME_UID and not info.st_mode & 0o077
             and info.st_nlink == 1 and info.st_size <= 1000000, 'runtime file')
        raw = source.read(1000001)
        need(len(raw) <= 1000000, 'runtime size')
        after = os.fstat(source.fileno())
        need((info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
             == (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns), 'runtime changed during the read')
    need(os.stat(path).st_ino == info.st_ino, 'runtime replaced')
    return raw


def restore_inventory():
    """Mirror of worker.py restore_inventory(); returns the state paths. Refused(what) on any difference."""
    for path in (ROOT, STORAGE):
        s = os.stat(path)
        need(os.path.realpath(path) == path and stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o077,
             'root %s' % path)
    states, owners = set(), {}
    for name in sorted(os.listdir(ROOT)):
        output = os.path.join(ROOT, name)
        if name.startswith('.lock-'):
            need(is_hex(name[6:]) and stat.S_ISREG(os.lstat(output).st_mode), 'lock file %s' % name)
            continue
        s = os.lstat(output)
        need(is_hex(name) and stat.S_ISDIR(s.st_mode) and os.path.realpath(output) == output
             and s.st_uid == RUNTIME_UID and not s.st_mode & 0o077, 'output directory %s' % name)
        value = manifest(os.path.join(output, 'storage-restore.json'))
        need(isinstance(value, dict) and set(value) == {'version', 'owner', 'state', 'path', 'layout'}
             and type(value['version']) is int and value['version'] == 1 and value['layout'] == 'bounded-ext4'
             and is_canonical(value['owner']) and UUID(value['owner']).hex == name and is_canonical(value['state']),
             'storage-restore.json of %s' % name)
        state = os.path.join(STORAGE, UUID(value['state']).hex)
        need(value['path'] == state and state not in states, 'state path of %s' % name)
        runtime = json.loads(retained_file(os.path.join(output, 'runtime.json')))
        plan = runtime.get('plan') if isinstance(runtime, dict) else None
        need(isinstance(plan, dict) and plan.get('owner') == value['owner'] and plan.get('state') == value['state'],
             'runtime.json of %s' % name)
        states.add(state)
        owners[name] = UUID(value['state']).hex
    need(set(os.path.join(STORAGE, n) for n in os.listdir(STORAGE)) == states, 'stray or missing storage entry')
    need(owners == OWNERS, 'owner -> state map differs from the recorded one')
    return sorted(states)


def mount_entries(target):
    with open(MOUNTINFO) as stream:
        lines = stream.read().splitlines()
    found = []
    for line in lines:
        fields = line.split()
        if len(fields) > 4 and fields[4] == target and '-' in fields:
            tail = fields[fields.index('-') + 1:]
            found.append({'majmin': fields[2], 'optional': fields[6:fields.index('-')],
                          'fstype': tail[0] if tail else ''})
    return found


def loop_devices():
    rc, out = run([LOSETUP, '-J', '-l', '-O', 'NAME,MAJ:MIN,BACK-INO,BACK-MAJ:MIN,BACK-FILE'])
    try:
        value = json.loads(out) if rc == 0 and out else {'loopdevices': []}
        return {str(d['maj:min']).strip(): d for d in value.get('loopdevices') or []}
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def restore_conditions(state, loops):
    """Mirror of runner_storage.restore()+verify() for a mounted state, plus the loop <-> backing link."""
    s = os.stat(state)
    need(os.path.isabs(state) and os.path.realpath(state) == state and state.startswith('/var/lib/anxious-lookout/')
         and is_hex(os.path.basename(state)) and s.st_uid == 0, 'state directory %s' % state)
    need(any('shared:' in ' '.join(e['optional']) for e in mount_entries(os.path.dirname(state))),
         'storage root not a shared mount')
    linked = {}
    for kind, limit in SIZES.items():
        backing, target = os.path.join(state, kind + '.ext4'), os.path.join(state, kind)
        b = os.lstat(backing)
        need(os.path.realpath(backing) == backing and stat.S_ISREG(b.st_mode) and b.st_size == limit and b.st_uid == 0
             and b.st_nlink == 1 and not b.st_mode & 0o077 and os.path.realpath(target) == target
             and os.path.isdir(target), '%s backing/target of %s' % (kind, state))
        need(os.path.ismount(target), '%s target of %s not mounted' % (kind, state))
        info = os.statvfs(target)
        need(0 < info.f_blocks * info.f_frsize <= limit, '%s filesystem size of %s' % (kind, state))
        entries = mount_entries(target)
        majmins = {e['majmin'] for e in entries}
        need(entries and len(majmins) == 1 and all(e['fstype'] == 'ext4' for e in entries),
             '%s target of %s is not one ext4 mount' % (kind, state))
        majmin = majmins.pop()
        loop = (loops or {}).get(majmin)
        want = '%d:%d' % (os.major(b.st_dev), os.minor(b.st_dev))
        need(majmin.startswith('7:') and loop is not None and loop.get('back-ino') == b.st_ino
             and str(loop.get('back-maj:min', '')).strip() == want, '%s loop of %s backs another file' % (kind, state))
        linked[kind] = {'loop': majmin, 'backIno': b.st_ino}
    return linked


def restore_check():
    try:
        states = restore_inventory()
        loops = loop_devices()
        need(loops is not None, 'losetup unreadable')
        links = {os.path.basename(s): restore_conditions(s, loops) for s in states}
    except Refused as refusal:
        raise Stop(3, 'precheck: restore precondition: %s' % refusal) from None
    except (OSError, ValueError, TypeError, KeyError):
        raise Stop(3, 'precheck: restore precondition unreadable') from None
    return links


def unit(name):
    rc, out = run([SYSTEMCTL, 'show', name, '-p', 'ActiveState', '-p', 'SubState', '-p', 'Result', '-p', 'UnitFileState'])
    values = dict(line.split('=', 1) for line in out.splitlines() if '=' in line) if rc == 0 else {}
    return {k: values.get(k) for k in ('ActiveState', 'SubState', 'Result', 'UnitFileState')}


def host_check():
    problems, seen = [], {'files': {}, 'units': {}}
    for path, want in sorted(PINNED.items()):
        try:
            got = sha256_path(path)
        except OSError:
            got = None
        seen['files'][path] = got
        if got != want:
            problems.append('%s changed' % path)
    rc, image = run([DOCKER, 'image', 'inspect', '--format', '{{.Id}}', RESTORE_IMAGE])
    seen['restoreImage'] = image if rc == 0 else None
    if rc != 0 or image != RESTORE_IMAGE:
        problems.append('restore image missing')
    for name in UNITS_UP + UNITS_DONE + UNITS_IDLE:
        seen['units'][name] = unit(name)
    for name in UNITS_UP:
        u = seen['units'][name]
        if (u['ActiveState'], u['UnitFileState']) != ('active', 'enabled'):
            problems.append('%s not enabled and active' % name)
    for name in UNITS_DONE:
        u = seen['units'][name]
        if (u['ActiveState'], u['SubState'], u['Result']) != ('active', 'exited', 'success'):
            problems.append('%s not active/exited/success' % name)
    for name in UNITS_IDLE:
        if seen['units'][name]['ActiveState'] not in ('inactive', 'failed'):
            problems.append('%s is running' % name)
    return problems, seen


def node_check(b0):
    node = kjson(['get', 'node', NODE[0]], 3, 'precheck: Node lookup failed')
    ready = [c for c in ((node or {}).get('status') or {}).get('conditions', []) if c.get('type') == 'Ready']
    info = {'name': NODE[0], 'uid': node['metadata'].get('uid'), 'bootId': node['status']['nodeInfo'].get('bootID'),
            'ready': [c.get('status') for c in ready],
            'hostnameLabel': (node['metadata'].get('labels') or {}).get('kubernetes.io/hostname'),
            'k3sHostname': (node['metadata'].get('annotations') or {}).get('k3s.io/hostname')}
    problems = []
    if info['uid'] != NODE[1]:
        problems.append('Node UID differs')
    if info['bootId'] != b0:
        problems.append('Node bootID differs from /proc')
    if info['ready'] != ['True']:
        problems.append('Node not Ready')
    if socket.gethostname() != HOSTNAME or info['k3sHostname'] != HOSTNAME:
        problems.append('hostname mapping differs')
    return problems, info


def workloads():
    seen = {}
    for key, uid in WORKLOADS.items():
        kind, ns, name = key.split('/')
        obj = kjson(['-n', ns, 'get', kind, name], 3, 'precheck: %s lookup failed' % key) or {}
        seen[key] = {'uid': obj.get('metadata', {}).get('uid'), 'ready': (obj.get('status') or {}).get('readyReplicas'),
                     'replicas': (obj.get('spec') or {}).get('replicas')}
    pods = (kjson(['-n', CTRL[0], 'get', 'pods', '-l', 'app=' + CTRL[1]], 3, 'precheck: controller Pod lookup failed')
            or {}).get('items') or []
    seen['controllerPods'] = [{'name': p['metadata'].get('name'), 'uid': p['metadata'].get('uid'),
                               'containers': {s.get('name'): {'id': s.get('containerID'), 'restarts': s.get('restartCount')}
                                              for s in (p.get('status') or {}).get('containerStatuses') or []}}
                              for p in pods]
    problems = ['%s is not the recorded object at full readiness' % k for k, uid in WORKLOADS.items()
                if seen[k]['uid'] != uid or seen[k]['ready'] != seen[k]['replicas'] or not seen[k]['replicas']]
    return problems, seen


def precheck():
    if os.geteuid() != 0:
        raise Stop(3, 'precheck: not root')
    enter1, enter1_sha, enter1_dir = enter1_record()
    pod_uid = enter1['pod']['uid']
    b0 = boot_id()
    if b0 != enter1.get('bootId'):
        raise Stop(3, 'precheck: the boot changed since enter1')
    row = one('reg', REG, 3)
    problems = registry_problems(row, pod_uid)
    baseline = [(enter1.get('before') or {}).get('conversations'), (enter1.get('after') or {}).get('conversations')]
    if baseline[0] != baseline[1] or row.get('conversations') != baseline[0]:
        problems.append('the conversation snapshot differs from enter1 before/after')
    fence_problems, bound = time_fence(enter1, row.get('dbEpoch') if isinstance(row.get('dbEpoch'), (int, float)) else 0)
    problems += fence_problems
    problems += pod_problems(kjson(['-n', NS, 'get', 'pod', POD_NAME, '--ignore-not-found'], 3,
                                   'precheck: Pod lookup failed'), pod_uid)
    pod_list_problems, overview = pods_overview(pod_uid)
    problems += pod_list_problems
    hits = process_scan()
    problems += ['%d %s process(es)' % (n, k) for k, n in sorted(hits.items()) if n]
    host_problems, host = host_check()
    problems += host_problems
    node_problems, node = node_check(b0)
    problems += node_problems
    workload_problems, seen = workloads()
    problems += workload_problems
    if problems:
        raise Stop(3, 'precheck: %s' % '; '.join(problems))
    links = restore_check()
    say('precheck ok: enter1 %s (sha256 %s); …5804 gen 6 Pod %s phase %s ambiguous %s, 0 active, no stop/entry/'
        'admission/outbox, nobody waiting, 0 running conversations; inside the time fence (bound %s, %d s left); '
        'paid window closed (#64): namespaces present %s, no Pod in them; no login/reclaim process; restore '
        'inventory and conditions hold for %d states (loop<->backing linked); pinned files, restore image and units '
        'as recorded; Node %s uid %s Ready, B0 %s, hostname %s'
        % (enter1_dir, enter1_sha[:16], pod_uid, row['phase'], row['ambiguous'], iso(bound), int(bound - time.time()),
           overview['paidNamespacesPresent'] or 'none', len(links), NODE[0], NODE[1], b0, HOSTNAME))
    return {'enter1': {'dir': enter1_dir, 'sha256': enter1_sha}, 'podUid': pod_uid, 'bootId': b0, 'bound': bound,
            'registry': row, 'overview': overview, 'processes': hits, 'host': host, 'node': node, 'workloads': seen,
            'storage': links}, enter1


# Evidence capture -------------------------------------------------------------------------------------
def exclusive(ev, name):
    return os.open(os.path.join(ev, name), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)


def export_journal(ev):
    fd = exclusive(ev, 'k3s-journal.jsonl')
    try:
        try:
            proc = subprocess.Popen([JOURNALCTL, '-u', 'k3s', '-b', '-o', 'json', '--no-pager'], stdout=fd,
                                    stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
            rc = proc.wait(timeout=EXPORT_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise Stop(4, 'the k3s journal export timed out: NO reboot')
        if rc != 0:
            raise Stop(4, 'the k3s journal export failed (rc %d): NO reboot' % rc)
        os.fsync(fd)
        size = os.fstat(fd).st_size
    finally:
        os.close(fd)
    if size == 0:
        raise Stop(4, 'the k3s journal export is empty: NO reboot')
    return {'file': 'k3s-journal.jsonl', 'bytes': size, 'sha256': sha256_path(os.path.join(ev, 'k3s-journal.jsonl')),
            'overToolCap': size > TOOL_CAP, 'toolCap': TOOL_CAP}


def copy_containerd_log(ev):
    try:
        source = os.lstat(CONTAINERD_LOG)
    except OSError:
        raise Stop(4, 'the k3s containerd log is missing: NO reboot') from None
    if not stat.S_ISREG(source.st_mode):
        raise Stop(4, 'the k3s containerd log is not a regular file: NO reboot')
    fd = exclusive(ev, 'containerd.log')
    try:
        with open(CONTAINERD_LOG, 'rb') as src:
            while True:
                block = src.read(1 << 20)
                if not block:
                    break
                view = memoryview(block)
                while view:
                    view = view[os.write(fd, view):]
        os.fsync(fd)
        size = os.fstat(fd).st_size
    finally:
        os.close(fd)
    if size < source.st_size:
        raise Stop(4, 'the containerd log copy is shorter than its source: NO reboot')
    return {'file': 'containerd.log', 'bytes': size, 'sha256': sha256_path(os.path.join(ev, 'containerd.log')),
            'source': {'path': CONTAINERD_LOG, 'bytes': source.st_size, 'inode': source.st_ino,
                       'mtime': iso(source.st_mtime)}}


def namespaces():
    found = {}
    for proc in ('1', 'self'):
        for kind in ('pid', 'cgroup', 'mnt'):
            found['%s/%s' % (proc, kind)] = os.readlink(os.path.join(PROC, proc, 'ns', kind))
    return found


def capture(ev):
    free = os.statvfs(EVIDENCE)
    available = free.f_bavail * free.f_frsize
    if available < FREE_MIN:
        raise Stop(4, 'only %d bytes free under %s (need %d): NO reboot' % (available, EVIDENCE, FREE_MIN))
    journal = export_journal(ev)
    containerd = copy_containerd_log(ev)
    ids = {'namespaces': namespaces(), 'hostname': socket.gethostname(), 'bootId': boot_id(), 'freeBytes': available}
    fsync_dir(ev)
    fsync_dir(EVIDENCE)
    return {'journal': journal, 'containerd': containerd, 'identity': ids}


# Final look and reboot --------------------------------------------------------------------------------
def final_look(before, enter1):
    if boot_id() != before['bootId']:
        raise Stop(5, 'final look: the boot changed: NO reboot')
    row = one('final', REG, 5)
    problems = registry_problems(row, before['podUid'])
    problems += time_fence(enter1, row['dbEpoch'] if isinstance(row.get('dbEpoch'), (int, float)) else 0)[0]
    pod = kjson(['-n', NS, 'get', 'pod', POD_NAME, '--ignore-not-found'], 5, 'final look: Pod lookup failed')
    problems += pod_problems(pod, before['podUid'])
    for key in ('state', 'generation', 'pod', 'stops', 'conversations'):  # phase may move ready <-> unreachable
        if row.get(key) != before['registry'].get(key):
            problems.append('registry %s changed' % key)
    overview_problems, overview = pods_overview(before['podUid'], 5)
    problems += overview_problems
    hits = process_scan()
    problems += ['%d %s process(es)' % (n, k) for k, n in sorted(hits.items()) if n]
    if problems:
        raise Stop(5, 'final look: %s: NO reboot' % '; '.join(problems))
    return {'at': now(), 'registry': row, 'overview': overview, 'processes': hits, 'pod': {'uid': pod['metadata']['uid'], 'containers': {
        s.get('name'): s.get('containerID') for s in (pod.get('status') or {}).get('containerStatuses') or []}}}


def main():
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, interrupted)
    code, message, ev, intent, requested = 0, '', None, False, False
    try:
        try:
            take_lock()
            before, enter1 = precheck()
            ev = os.path.join(EVIDENCE, 'w6a2-reboot-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()))
            os.mkdir(ev, 0o700)
            say('evidence: %s' % ev)
            try:
                record(ev, attempt=ATTEMPT, started=now(), before=before)
                captured = capture(ev)
                record(ev, captured=captured)
            except Stop:
                raise
            except OSError as error:
                raise Stop(4, 'saving the evidence failed (%s): NO reboot' % (os.strerror(error.errno) if error.errno
                                                                             else 'error'))
            say('saved: k3s journal %d bytes sha256 %s%s; containerd log copy %d bytes sha256 %s; namespaces, hostname, '
                'B0, Node and workload identities recorded'
                % (captured['journal']['bytes'], captured['journal']['sha256'],
                   ' (OVER the %d-byte tool cap: kept in full, not empty)' % TOOL_CAP
                   if captured['journal']['overToolCap'] else '', captured['containerd']['bytes'],
                   captured['containerd']['sha256']))
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                signal.signal(sig, signal.SIG_IGN)  # from the final look on, nothing interrupts the sequence
            look = final_look(before, enter1)
            try:
                record(ev, finalLook=look, intent={'at': now(), 'epoch': time.time(), 'bootId': before['bootId'],
                                                   'nodeUid': NODE[1], 'podUid': before['podUid']})
            except OSError:
                raise Stop(4, 'writing the intent record failed: NO reboot')
            intent = True
            say('intent recorded (B0 %s, Pod %s); calling systemctl reboot once at %s' % (before['bootId'],
                                                                                     before['podUid'], now()))
            rc, _ = run([SYSTEMCTL, 'reboot'], timeout=30)
            requested = rc == 0
            try:
                record(ev, rebootCall={'rc': rc, 'at': now()})
            except OSError:
                if rc == 0:
                    raise Stop(8, 'reboot requested (systemctl rc 0) but the call result could not be recorded; NOT '
                                  'repeated; w6a2-observe proves the boot')
            if rc != 0:
                raise Stop(7, 'systemctl reboot returned %d: not repeated; PM decides' % rc)
            say('reboot requested (systemctl rc 0); this ssh session will drop. This is the request, not its success: '
                'w6a2-observe.py proves the new boot')
        except Stop as stop:
            code, message = stop.code, stop.message
        except Interrupted:
            code, message = 130, 'interrupted before the final look: NO reboot'
        except Exception:
            code, message = ((8, 'reboot requested, then an unexpected error: NOT repeated') if requested else
                             (7, 'unexpected error after the intent: not repeated; PM decides')) if intent else \
                (4 if ev else 3, 'unexpected error: NO reboot')
    except BaseException:
        if not message:
            code, message = (7 if intent else 130), 'interrupted'
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, signal.SIG_IGN)
        except Exception:
            pass
    if message:
        say('STOPPED (%d): %s' % (code, message))
    try:
        record(ev, result=code, finished=now(), message=message)
    except Exception:
        say('evidence write failed at the end (the directory still blocks a rerun)')
        if code == 0:
            code = 8
            say('STOPPED (8): reboot requested, but the final record could not be written; NOT repeated')
    say('result rc=%d' % code)
    return code


if __name__ == '__main__':
    sys.exit(main())
