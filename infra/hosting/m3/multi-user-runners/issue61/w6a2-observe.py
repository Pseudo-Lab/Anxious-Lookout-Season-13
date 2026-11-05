"""#61 dev-VM gate W6, attempt 2, step 3 (read-only): after the guest reboot, wait at most 30 min for G2 (the new boot) and
G3 (the runner-controller's own dead-runner path, v2 terminal receipt, proof on the new boot). Run by the user as
root in the guest once ssh answers again:

    python3 -I /root/issue61/w6a2-observe.py

precheck (no evidence directory on refusal): attempt 1's w6-* records are exactly w6-enter1-20261104T095411Z (read,
never written); the reboot record counts the 5 …5804 stops of before attempt 2; exactly one passing w6a2-enter1 and one w6a2-reboot record with an
intent (B0, time) and no failed reboot call; no w6a2-observe or w6a2-enter2 yet; the current /proc boot ID differs
from B0 (otherwise: not rebooted, or run too early -> rc 3, nothing written).

G2, all together: k3s active; Node anxious-lookout with the recorded UID, Ready, its bootID == /proc == B1 != B0;
journalctl -b (this boot only) of issue16-restore.service has "PASS bounded restore inventory; no formatting" with
_BOOT_ID == B1; issue16-restore and issue16-storage-root ran in this boot (ExecMainExitTimestampMonotonic > 0)
with Result=success and status 0; every state's native/control target is an ext4 mount of the loop device whose
backing inode is its <kind>.ext4.

G3, all together: the gen-6 stop of …5804 exists once, requested after the intent; its receipt is version 2 with
exactly {stopId, ownerId, stateId, podUid, generation, version, terminal}, equal to the stop row (owner, state,
gen 6, the enter1 Pod UID); terminal is a subset of {podPhase, containers, podAbsent}; it is completed with proof
{podUid, nodeName, nodeUid, bootId, verifiedAt, evidence=pod-deleted} on the recorded Node UID and boot B1, equal
to the row's node_uid/boot_id; exactly one new stop since the reboot record (the owner total is the integer 6 = 5 + 1;
anything else never passes); registry idle_stopped, gen 6, no Pod,
0 active, not ambiguous; no Pod in codex-runner; no open stop anywhere; conversations unchanged; the recorded
workloads (controller, api, web, postgres, broker) back at full readiness with the recorded UIDs.

Definitive mismatches stop at once with rc 6: a receipt of another version (v1/v3/v4) or shape, a stop for another
Pod UID, a stop requested before the intent, a proof on B0 or another Node/boot, more than one new stop, fewer stops than
before the reboot (or a gen-6 stop while the total is still 5), a
replacement Pod, a registry past gen 6 or on another state. Anything not yet reached (k3s starting, Node NotReady,
the Pod still listed Running or not yet terminal, the stop not yet written or completed, an unreadable stop count) is waited on until the
deadline, then rc 5.

No-input evidence (within the observed scope, after the pass): the raw sudo and sshd journal of this boot and the
controller log are saved privately (0600, O_EXCL) with sha256; only counts, times and digests are printed. The
controller's own outcome lines for …5804 are counted. Absence from these logs is not proof of absence: there is
no API audit log, so a DELETE by another privileged actor cannot be fully excluded.

The new controller container after the reboot is not resident token-reconnect evidence (W5 limits stand).

Lookups are three-way: success, explicit absence (a successful GET with --ignore-not-found and no object), or
failure (rc, timeout, invalid JSON). G3 needs the single Pod GET to succeed with an explicit absence AND a
successful Pod List of codex-runner holding no Pod at all (same name with any UID included); a failure or two
contradicting reads is waited on, never a pass. A Pod of that name with another UID is a replacement (rc 6).

The v2 receipt's terminal must be one runner_controller_service.terminal_evidence() can produce: exactly
{podAbsent: true}, or exactly {podPhase, containers} with a terminal phase (Failed/Succeeded; containers may then be
empty, as for a Pod without containerStatuses) or a terminated runner container ({exitCode, reason}). Times are parsed: requested_at >= intent, proof verifiedAt >= requested_at,
completed_at >= verifiedAt (5 s tolerance each), none in the future.

Deadline: every call timeout and sleep inside the 30-min window is bounded by the monotonic time left (floor 5 s),
and a pass whose reads returned after the window is rc 5, not 0. Saving the no-input evidence and the final record
after a pass is grace, outside the window.

Lock: the root-only /root/issue61/w6a2.lock is taken before the evidence directory is read and held to the end.

rc (never rerun; back checks read-only and PM decides):
  0   G2 and G3 proved; evidence written (with the enter1/reboot record hashes that enter2 checks)
  3   precheck refused: nothing changed, nothing written
  5   not reached within 30 min (nothing changed by this script)
  6   a definitive mismatch (above)
  9   proved, but writing the final evidence failed
  130 interrupted
"""
import calendar
import fcntl
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import time

OWNER = '00000000-0000-4000-8000-000000005804'
STATE = '5ff583e9-abe1-4ef8-b277-4a5afead6b80'
ATTEMPT = 'a2'
GEN1 = 6  # attempt 2: the generation the reboot ran under
ATTEMPT1 = ('w6-enter1-20261104T095411Z',)  # attempt 1's only record: read-only, never written
STOPS0 = 5  # …5804 stops before attempt 2 (read-only diagnosis 2026-11-04T10:34Z)
POD_NAME = 'runner-' + OWNER.replace('-', '')
NS = 'codex-runner'
NODE = ('anxious-lookout', '3a59eb5e-0800-4445-af68-9ae0bf4ed457')
CTRL = ('runner-control', 'runner-controller')
WORKLOADS = {
    'deployment/runner-control/runner-controller': '41564f26-95e7-4ad1-8af3-d73b93c4669a',
    'deployment/m2-hosting/api': 'fad6799a-6df5-4086-812f-cfae6212adfa',
    'deployment/m2-hosting/web': 'd35eafcc-6d75-43c8-b093-fee29ec9b6d4',
    'statefulset/m2-hosting/postgres': 'a5ce2d32-8688-4d9a-a003-66c71982681b',
    'statefulset/codex-broker/codex-broker': 'a4016104-57c6-4dc1-b3d5-b271ba029958',
}
STATES = ('34823dfb6b504b8eb230191739bdc57d', 'eb766aef80164439b2175ae6b9927a4c', 'cfd1fedcdd6b40898df2c1070a6146e1',
          '0383c764bea948aa836805220946e4c9', '5ff583e9abe14ef8b2774a5afead6b80')
EVIDENCE = '/root/issue61/evidence'
K3S = '/usr/local/bin/k3s'
LOSETUP = '/usr/sbin/losetup'
SYSTEMCTL = '/usr/bin/systemctl'
JOURNALCTL = '/usr/bin/journalctl'
BOOT_ID = '/proc/sys/kernel/random/boot_id'
MOUNTINFO = '/proc/self/mountinfo'
STORAGE = '/var/lib/anxious-lookout/user-runners'
GIB = 1024 ** 3
SIZES = {'native': 5 * GIB, 'control': 1 * GIB}
PASS_LINE = 'PASS bounded restore inventory; no formatting'
RECEIPT_KEYS = {'stopId', 'ownerId', 'stateId', 'podUid', 'generation', 'version', 'terminal'}
PROOF_KEYS = {'podUid', 'nodeName', 'nodeUid', 'bootId', 'verifiedAt', 'evidence'}
RISKY = re.compile(r'\b(delete|reclaim|scale|patch|apply|edit|replace|rollout|drain|cordon|annotate|label|umount|mount|'
                   r'losetup|reboot|systemctl\s+(?:start|stop|restart|enable|disable|kill))\b')
WAIT = max(1, min(1800, int(os.environ.get('W6_OBSERVE_WAIT_SECONDS', 1800))))  # the harness may only shorten it
POLL = max(0.2, min(10.0, float(os.environ.get('W6_OBSERVE_POLL_SECONDS', 10))))
INTENT_SKEW = 5
UUID_RE = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
TS_RE = re.compile(r'(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?(Z|[+-]\d\d:\d\d)')
LOCK = '/root/issue61/w6a2.lock'
DEADLINE = []
LIMITS = ('within the observed scope: the v2 receipt and proof, the controller outcome lines, the host units, and the '
          'sudo/sshd journal of this boot; absence from these logs is not proof of absence (no API audit log, so a '
          'DELETE by another privileged actor cannot be fully excluded); the post-reboot controller container is not '
          'resident token-reconnect evidence')


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


def bounded(cap):
    if not DEADLINE:
        return cap
    return max(5, min(cap, DEADLINE[-1] - time.monotonic() + 1))


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


def epoch_of(text):
    """Seconds of an ISO 8601 timestamp with an explicit offset (any fraction length), or None."""
    match = TS_RE.fullmatch(text) if isinstance(text, str) else None
    if not match:
        return None
    base = calendar.timegm(time.strptime(match.group(1), '%Y-%m-%dT%H:%M:%S'))
    fraction = float(match.group(2)) if match.group(2) else 0.0
    zone = match.group(3)
    offset = 0 if zone == 'Z' else (1 if zone[0] == '+' else -1) * (int(zone[1:3]) * 3600 + int(zone[4:6]) * 60)
    return base + fraction - offset


def run(cmd, timeout=60, stdin=None):
    timeout = bounded(timeout)
    try:
        done = subprocess.run(cmd, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=max(1, timeout), check=False)
    except subprocess.TimeoutExpired:
        return 124, ''
    except Exception:
        return 125, ''
    return done.returncode, done.stdout.decode('utf-8', 'replace').strip()


def kread(args):
    """('ok', object), ('absent', None) for a successful empty answer (--ignore-not-found), or ('error', None)."""
    rc, out = run([K3S, 'kubectl'] + args + ['-o', 'json'])
    if rc != 0:
        return 'error', None
    if not out:
        return 'absent', None
    try:
        value = json.loads(out)
    except ValueError:
        return 'error', None
    return ('ok', value) if isinstance(value, dict) else ('error', None)


def kjson(args):
    """The object, or None when the lookup did not succeed with one (a starting cluster is waited on)."""
    state, value = kread(args)
    return value if state == 'ok' else None


def db_row():
    text = 'BEGIN READ ONLY;\n/*w6o:obs*/ %s\nCOMMIT;\n' % OBS
    rc, out = run([K3S, 'kubectl', '-n', 'm2-hosting', 'exec', '-i', 'postgres-0', '--', 'psql', '-U', 'postgres', '-d',
                   'hosting', '-Atq', '-v', 'ON_ERROR_STOP=1', '-f', '-'], 60, text.encode())
    try:
        rows = [json.loads(line) for line in out.splitlines() if line.strip()] if rc == 0 else []
    except ValueError:
        rows = []
    return rows[0] if len(rows) == 1 and isinstance(rows[0], dict) else None


def now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def boot_id():
    with open(BOOT_ID) as stream:
        value = stream.read().strip()
    if not UUID_RE.fullmatch(value):
        raise Stop(3, 'boot ID unreadable')
    return value


def sha256_path(path):
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


OBS = ("SELECT json_build_object('registry',(SELECT json_build_object('state',r.state_id,'phase',r.phase,'generation',"
       "r.generation,'revision',r.revision,'pod',r.pod_uid,'active',r.active_requests,'ambiguous',r.ambiguous) "
       "FROM runner_runtime.registry r WHERE r.owner_id='%(o)s'),"
       "'stop6',(SELECT json_build_object('stopId',s.stop_id,'owner',s.owner_id,'state',s.state_id,'generation',"
       "s.generation,'pod',s.pod_uid,'requestedEpoch',extract(epoch from s.requested_at),'nodeName',s.node_name,"
       "'nodeUid',s.node_uid,'bootId',s.boot_id,'receipt',s.receipt,'proof',s.proof,'completed',s.completed_at IS NOT NULL,"
       "'completedAt',s.completed_at,'completedEpoch',extract(epoch from s.completed_at)) FROM runner_termination.stops s WHERE s.owner_id='%(o)s' AND s.generation=%(g)d),"
       "'stops',(SELECT count(*) FROM runner_termination.stops s WHERE s.owner_id='%(o)s'),"
       "'allOpenStops',(SELECT count(*) FROM runner_termination.stops WHERE completed_at IS NULL),"
       "'conversations',(SELECT json_build_object('n',count(*),'digest',coalesce(md5(string_agg(id::text||':'||state||':'"
       "||version::text,',' ORDER BY id)),'-')) FROM research.conversations));" % {'o': OWNER, 'g': GEN1})


# Precheck ---------------------------------------------------------------------------------------------
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


def precheck():
    if os.geteuid() != 0:
        raise Stop(3, 'precheck: not root')
    dirs = w6_dirs()
    if [len(dirs.get(k, [])) for k in ('enter1', 'reboot', 'observe', 'enter2')] != [1, 1, 0, 0]:
        raise Stop(3, 'precheck: needs exactly one w6a2-enter1 and one w6a2-reboot record and no later one; found %s'
                   % json.dumps({k: len(v) for k, v in dirs.items()}, sort_keys=True))
    enter1, enter1_sha = read_run(dirs['enter1'][0], 'enter1')
    reboot, reboot_sha = read_run(dirs['reboot'][0], 'reboot')
    pod6 = (enter1.get('pod') or {}).get('uid')
    if enter1.get('result') != 0 or enter1.get('newGeneration') != GEN1 or not UUID_RE.fullmatch(str(pod6)):
        raise Stop(3, 'precheck: the enter1 record is not a passing gen-%d entry' % GEN1)
    intent = reboot.get('intent') or {}
    call = reboot.get('rebootCall')
    b0 = intent.get('bootId')
    before = reboot.get('before') or {}
    if (not UUID_RE.fullmatch(str(b0)) or not isinstance(intent.get('epoch'), (int, float)) or intent.get('podUid') != pod6
            or b0 != before.get('bootId') or before.get('podUid') != pod6):
        raise Stop(3, 'precheck: the reboot record has no consistent intent (no reboot was requested by w6a2-reboot)')
    if (call is not None and call.get('rc') != 0) or reboot.get('result') not in (None, 0, 8):
        raise Stop(3, 'precheck: the reboot record shows a failed reboot call; PM decides')
    b1 = boot_id()
    if b1 == b0:
        raise Stop(3, 'precheck: the boot ID is still B0 %s: not rebooted (or run too early); nothing written' % b0)
    registry = before.get('registry') or {}
    if registry.get('stops') != STOPS0:
        raise Stop(3, 'precheck: the reboot record counts %s …5804 stops, not the %d before attempt 2' % (registry.get('stops'), STOPS0))
    say('precheck ok: enter1 sha256 %s (Pod %s), reboot sha256 %s (intent %s, B0 %s); this boot %s'
        % (enter1_sha[:16], pod6, reboot_sha[:16], intent.get('at'), b0, b1))
    return {'enter1Sha256': enter1_sha, 'rebootSha256': reboot_sha, 'enter1': os.path.basename(dirs['enter1'][0]),
            'reboot': os.path.basename(dirs['reboot'][0])}, {
        'pod6': pod6, 'b0': b0, 'b1': b1, 'intentEpoch': intent['epoch'], 'stopsBefore': registry.get('stops'),
        'conversations': registry.get('conversations')}


# Checks -----------------------------------------------------------------------------------------------
def unit(name):
    rc, out = run([SYSTEMCTL, 'show', name, '-p', 'ActiveState', '-p', 'SubState', '-p', 'Result', '-p', 'ExecMainStatus',
                   '-p', 'ExecMainExitTimestampMonotonic'])
    return dict(line.split('=', 1) for line in out.splitlines() if '=' in line) if rc == 0 else {}


def restore_pass(b1):
    rc, out = run([JOURNALCTL, '-b', '-u', 'issue16-restore.service', '-o', 'json', '--no-pager'], 60)
    if rc != 0:
        return False
    want = b1.replace('-', '')
    for line in out.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get('MESSAGE') == PASS_LINE and entry.get('_BOOT_ID') == want:
            return True
    return False


def mount_entries(target):
    with open(MOUNTINFO) as stream:
        lines = stream.read().splitlines()
    found = []
    for line in lines:
        fields = line.split()
        if len(fields) > 4 and fields[4] == target and '-' in fields:
            tail = fields[fields.index('-') + 1:]
            found.append({'majmin': fields[2], 'fstype': tail[0] if tail else ''})
    return found


def storage_links():
    """Problems with the loop mounts of every state (empty = linked), and the links."""
    rc, out = run([LOSETUP, '-J', '-l', '-O', 'NAME,MAJ:MIN,BACK-INO,BACK-MAJ:MIN,BACK-FILE'])
    try:
        loops = {str(d['maj:min']).strip(): d for d in (json.loads(out) if rc == 0 and out else {}).get('loopdevices') or []}
    except (ValueError, KeyError, TypeError, AttributeError):
        return ['losetup unreadable'], {}
    problems, links = [], {}
    for state in STATES:
        for kind, size in SIZES.items():
            backing, target = os.path.join(STORAGE, state, kind + '.ext4'), os.path.join(STORAGE, state, kind)
            try:
                b = os.lstat(backing)
            except OSError:
                problems.append('%s/%s backing missing' % (state[:8], kind))
                continue
            entries = mount_entries(target)
            majmins = {e['majmin'] for e in entries}
            if not (stat.S_ISREG(b.st_mode) and b.st_size == size) or not entries or len(majmins) != 1 or any(
                    e['fstype'] != 'ext4' for e in entries):
                problems.append('%s/%s not one ext4 mount of its backing' % (state[:8], kind))
                continue
            majmin = majmins.pop()
            loop = loops.get(majmin)
            want = '%d:%d' % (os.major(b.st_dev), os.minor(b.st_dev))
            if not majmin.startswith('7:') or loop is None or loop.get('back-ino') != b.st_ino or \
                    str(loop.get('back-maj:min', '')).strip() != want:
                problems.append('%s/%s loop backs another file' % (state[:8], kind))
                continue
            links['%s/%s' % (state, kind)] = {'loop': majmin, 'backIno': b.st_ino}
    return problems, links


def g2(ctx):
    """(done, pending reasons, facts). Never definitive: a starting node is waited on."""
    pending, facts = [], {}
    k3s = unit('k3s.service').get('ActiveState')
    facts['k3s'] = k3s
    if k3s != 'active':
        pending.append('k3s %s' % k3s)
    node = kjson(['get', 'node', NODE[0]])
    if node is None:
        pending.append('Node not readable')
    else:
        ready = [c.get('status') for c in (node.get('status') or {}).get('conditions', []) if c.get('type') == 'Ready']
        facts['node'] = {'uid': node['metadata'].get('uid'), 'bootId': (node['status'].get('nodeInfo') or {}).get('bootID'),
                         'ready': ready}
        if facts['node']['uid'] != NODE[1]:
            pending.append('Node UID differs')
        if facts['node']['bootId'] != ctx['b1'] or ctx['b1'] == ctx['b0']:
            pending.append('Node bootID is not B1')
        if ready != ['True']:
            pending.append('Node not Ready')
    facts['restorePass'] = restore_pass(ctx['b1'])
    if not facts['restorePass']:
        pending.append('no restore PASS line in this boot')
    for name in ('issue16-restore.service', 'issue16-storage-root.service'):
        u = unit(name)
        facts[name] = {k: u.get(k) for k in ('ActiveState', 'SubState', 'Result', 'ExecMainStatus')}
        try:
            this_boot = int(u.get('ExecMainExitTimestampMonotonic', '0')) > 0
        except ValueError:
            this_boot = False
        if (u.get('ActiveState'), u.get('SubState'), u.get('Result'), u.get('ExecMainStatus')) != \
                ('active', 'exited', 'success', '0') or not this_boot:
            pending.append('%s not active/exited/success in this boot' % name)
    problems, links = storage_links()
    facts['storageLinks'] = len(links)
    pending += problems
    return not pending, pending, facts


def terminal_ok(terminal):
    """True only for a terminal that runner_controller_service.terminal_evidence() can produce."""
    if not isinstance(terminal, dict):
        return False
    if set(terminal) == {'podAbsent'}:
        return terminal['podAbsent'] is True
    if set(terminal) != {'podPhase', 'containers'}:
        return False
    containers = terminal['containers']
    # terminal_evidence() of a Pod without containerStatuses gives {} (e.g. {'status': {'phase': 'Failed'}}): accepted
    # only with a terminal phase, because then runner_dead below is false.
    if not isinstance(containers, dict) or not set(containers) <= {'runner', 'shell-executor'}:
        return False
    for value in containers.values():
        if not isinstance(value, dict):
            return False
        if set(value) == {'exitCode', 'reason'}:
            code = value['exitCode']
            if not (code is None or (type(code) is int)) or not isinstance(value['reason'], str):
                return False
        elif set(value) != {'state'} or not isinstance(value['state'], str):
            return False
    runner_dead = set(containers.get('runner') or {}) == {'exitCode', 'reason'}
    return terminal['podPhase'] in ('Failed', 'Succeeded') or runner_dead


def times_ok(ctx, stop, proof):
    """requested >= intent, verifiedAt >= requested, completed >= verifiedAt; none in the future."""
    requested, completed = stop.get('requestedEpoch'), stop.get('completedEpoch')
    verified = epoch_of(proof.get('verifiedAt'))
    if not all(isinstance(v, (int, float)) for v in (requested, completed)) or verified is None:
        return False
    skew, now_ = INTENT_SKEW, time.time()
    return (requested >= ctx['intentEpoch'] - skew and verified >= requested - skew and completed >= verified - skew
            and max(requested, verified, completed) <= now_ + skew)


def pod_summary(pod, state='ok'):
    if state == 'error':
        return 'unreadable'
    if pod is None:
        return 'absent'
    status = pod.get('status') or {}
    containers = ','.join('%s:%s' % (s.get('name'), next(iter(s.get('state') or {}), 'unknown'))
                          for s in status.get('containerStatuses') or [])
    return '%s %s [%s]%s' % (pod['metadata'].get('uid'), status.get('phase'), containers,
                             ' deleting' if pod['metadata'].get('deletionTimestamp') else '')


def g3(ctx, row, pod_state, pod, list_state, items):
    """(done, pending reasons, definitive problems)."""
    pending, definitive = [], []
    pod6, b0, b1 = ctx['pod6'], ctx['b0'], ctx['b1']
    if pod_state == 'ok' and (pod.get('metadata') or {}).get('uid') != pod6:
        definitive.append('a replacement runner Pod %s' % (pod.get('metadata') or {}).get('uid'))
    for item in items or []:
        meta = item.get('metadata') or {} if isinstance(item, dict) else {}
        if meta.get('name') == POD_NAME and meta.get('uid') != pod6:
            definitive.append('a replacement runner Pod %s in the list' % meta.get('uid'))
    if row is None:
        return False, pending + ['database not readable'], definitive
    reg = row.get('registry') or {}
    if reg.get('state') != STATE or (isinstance(reg.get('generation'), int) and reg['generation'] > GEN1):
        definitive.append('registry moved to state %s gen %s' % (reg.get('state'), reg.get('generation')))
    stop = row.get('stop6')
    # Exactly one new stop: the owner's total must be an integer equal to STOPS0 + 1 for a pass (precheck has
    # already required the reboot record's count to be STOPS0). Fewer is waited on only while no gen-6 stop exists.
    stops = row.get('stops')
    if type(stops) is not int:
        pending.append('the owner stop count is unreadable (%r)' % (stops,))
    elif stops > STOPS0 + 1:
        definitive.append('%d new stops since the reboot record' % (stops - STOPS0))
    elif stops < STOPS0:
        definitive.append('the owner has %d stops, fewer than the %d before the reboot' % (stops, STOPS0))
    elif stops == STOPS0 and stop is not None:
        definitive.append('a gen-6 stop exists but the owner still has %d stops: an earlier stop is gone' % stops)
    elif stops == STOPS0:
        pending.append('no new stop yet (%d stops)' % stops)
    if stop is None:
        pending.append('no gen-6 stop yet')
    else:
        if (stop.get('owner'), stop.get('state'), stop.get('generation'), stop.get('pod')) != (OWNER, STATE, GEN1, pod6):
            definitive.append('the gen-6 stop is for another owner/state/Pod')
        if not isinstance(stop.get('requestedEpoch'), (int, float)) or stop['requestedEpoch'] < ctx['intentEpoch'] - INTENT_SKEW:
            definitive.append('the gen-6 stop was requested before the reboot intent')
        receipt = stop.get('receipt')
        if receipt is None:
            pending.append('gen-6 stop has no receipt yet')
        elif not isinstance(receipt, dict) or receipt.get('version') != 2:
            definitive.append('receipt version %s (not the v2 terminal path)' % (receipt or {}).get('version'))
        elif (set(receipt) != RECEIPT_KEYS or (receipt.get('stopId'), receipt.get('ownerId'), receipt.get('stateId'),
                                                receipt.get('podUid'), receipt.get('generation'))
              != (stop.get('stopId'), OWNER, STATE, pod6, GEN1)
              or not terminal_ok(receipt.get('terminal'))):
            definitive.append('the v2 receipt does not match the stop row or its terminal is not terminal evidence')
        if stop.get('completed') is not True:
            pending.append('gen-6 stop not completed yet')
        else:
            proof = stop.get('proof') or {}
            if proof.get('bootId') == b0 or stop.get('bootId') == b0:
                definitive.append('the proof is on B0')
            elif (not isinstance(proof, dict) or set(proof) != PROOF_KEYS or proof.get('evidence') != 'pod-deleted'
                  or (proof.get('podUid'), proof.get('nodeName'), proof.get('nodeUid'), proof.get('bootId'))
                  != (pod6, NODE[0], NODE[1], b1)
                  or (stop.get('nodeName'), stop.get('nodeUid'), stop.get('bootId')) != (NODE[0], NODE[1], b1)):
                definitive.append('the completed proof is not pod-deleted on the recorded Node and B1')
            elif not times_ok(ctx, stop, proof):
                definitive.append('the stop/proof times are not a valid chain after the intent')
    if stop is not None and stop.get('completed') is True and pod_state == 'ok' and \
            (pod.get('metadata') or {}).get('uid') == pod6:
        definitive.append('the stop is completed but the gen-6 Pod still exists')
    if (reg.get('phase'), reg.get('generation'), reg.get('pod'), reg.get('active'), reg.get('ambiguous')) != \
            ('idle_stopped', GEN1, None, 0, False):
        pending.append('registry not released yet (%s)' % reg.get('phase'))
    if pod_state == 'error':
        pending.append('runner Pod GET failed (not an absence)')
    elif pod_state == 'ok':
        pending.append('runner Pod still there: %s' % pod_summary(pod))
    if list_state != 'ok' or not isinstance(items, list):
        pending.append('runner Pod list failed (not an absence)')
    elif items:
        pending.append('%d Pod(s) listed in %s' % (len(items), NS))
        if pod_state == 'absent' and any(((i.get('metadata') or {}).get('name') == POD_NAME) for i in items
                                         if isinstance(i, dict)):
            pending.append('the GET says absent but the list still has %s' % POD_NAME)
    if row.get('allOpenStops') != 0:
        pending.append('%s open stop(s)' % row.get('allOpenStops'))
    if row.get('conversations') != ctx['conversations']:
        pending.append('conversations differ from the reboot record')
    return not pending and not definitive, pending, definitive


def workloads():
    pending, seen = [], {}
    for key, uid in WORKLOADS.items():
        kind, ns, name = key.split('/')
        obj = kjson(['-n', ns, 'get', kind, name]) or {}
        seen[key] = {'uid': (obj.get('metadata') or {}).get('uid'), 'ready': (obj.get('status') or {}).get('readyReplicas'),
                     'replicas': (obj.get('spec') or {}).get('replicas')}
        if seen[key]['uid'] != uid or not seen[key]['replicas'] or seen[key]['ready'] != seen[key]['replicas']:
            pending.append('%s not back at full readiness' % key)
    return pending, seen


# No-input evidence ------------------------------------------------------------------------------------
def save_command(ev, name, cmd, timeout=120):
    fd = os.open(os.path.join(ev, name), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        proc = subprocess.Popen(cmd, stdout=fd, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        try:
            rc = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            rc = 124
        os.fsync(fd)
        size = os.fstat(fd).st_size
    finally:
        os.close(fd)
    return {'file': name, 'rc': rc, 'bytes': size, 'sha256': sha256_path(os.path.join(ev, name))}


def journal_entries(path):
    found = []
    with open(path, 'rb') as stream:
        for raw in stream:
            try:
                entry = json.loads(raw)
            except ValueError:
                continue
            if isinstance(entry, dict):
                found.append(entry)
    return found


def at(entry):
    try:
        return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(int(entry.get('__REALTIME_TIMESTAMP')) / 1e6))
    except (TypeError, ValueError):
        return 'unknown'


def no_input(ev):
    """Counts, times and digests only; the raw logs stay in the private evidence directory."""
    sudo = save_command(ev, 'sudo-journal.jsonl', [JOURNALCTL, '-b', '_COMM=sudo', '-o', 'json', '--no-pager'])
    sshd = save_command(ev, 'sshd-journal.jsonl', [JOURNALCTL, '-b', '_COMM=sshd', '-o', 'json', '--no-pager'])
    ctrl = save_command(ev, 'controller.log', [K3S, 'kubectl', '-n', CTRL[0], 'logs', 'deploy/' + CTRL[1], '-c',
                                               'controller'])
    commands = [e for e in journal_entries(os.path.join(ev, sudo['file']))
                if 'COMMAND=' in str(e.get('MESSAGE', ''))]
    risky = [at(e) for e in commands if RISKY.search(str(e.get('MESSAGE', '')).split('COMMAND=', 1)[1])]
    logins = [at(e) for e in journal_entries(os.path.join(ev, sshd['file']))
              if str(e.get('MESSAGE', '')).startswith('Accepted ')]
    outcomes = {}
    with open(os.path.join(ev, ctrl['file']), 'rb') as stream:
        for raw in stream:
            try:
                line = json.loads(raw)
            except ValueError:
                continue
            if isinstance(line, dict) and line.get('owner') == OWNER and line.get('stage') == 'controller':
                key = str(line.get('result'))[:40]
                outcomes[key] = outcomes.get(key, 0) + 1
    units = {name: unit(name).get('ActiveState') for name in (
        'issue16-controller.service', 'issue16-controller.timer', 'issue16-observer.service', 'issue16-observer.timer',
        'issue16-supply.service', 'issue16-supply.timer')}
    fsync_dir(ev)
    return {'sudo': dict(sudo, commands=len(commands), riskyAt=risky), 'sshd': dict(sshd, acceptedAt=logins),
            'controller': dict(ctrl, outcomes5804=outcomes), 'hostUnits': units, 'limits': LIMITS}


# Main -------------------------------------------------------------------------------------------------
def main():
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, interrupted)
    code, message, ev, passed = 0, '', None, False
    try:
        try:
            take_lock()
            chain, ctx = precheck()
            ev = os.path.join(EVIDENCE, 'w6a2-observe-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()))
            os.mkdir(ev, 0o700)
            say('evidence: %s' % ev)
            record(ev, attempt=ATTEMPT, started=now(), chain=chain, context=ctx, waitSeconds=WAIT)
            deadline = time.monotonic() + WAIT
            DEADLINE.append(deadline)
            seen_pod, last, g2_at = [], {}, None
            while True:
                done2, pending2, facts = g2(ctx)
                if done2 and g2_at is None:
                    g2_at = now()
                    say('G2 at %s: k3s active; Node %s uid %s Ready; bootID %s == /proc, B0 was %s; restore PASS in this '
                        'boot; restore and storage-root Result=success; %d loop mounts linked to their backing files'
                        % (g2_at, NODE[0], NODE[1], ctx['b1'], ctx['b0'], facts['storageLinks']))
                    record(ev, g2={'at': g2_at, 'facts': facts})
                # The database first: a completed proof means the Pod was already gone, so a later read of the same
                # UID is a real inconsistency, not a race between the two reads.
                row = db_row() if done2 else None
                pod_state, pod = kread(['-n', NS, 'get', 'pod', POD_NAME, '--ignore-not-found']) if done2 else ('error', None)
                list_state, listed = kread(['-n', NS, 'get', 'pods']) if done2 else ('error', None)
                items = listed.get('items') if list_state == 'ok' else None
                summary = pod_summary(pod, pod_state) if done2 else 'not read'
                if done2 and (not seen_pod or seen_pod[-1][1] != summary) and len(seen_pod) < 40:
                    seen_pod.append((now(), summary))
                    say('pod %s at %s' % (summary, seen_pod[-1][0]))
                    record(ev, podSeen=seen_pod)
                if done2:
                    done3, pending3, definitive = g3(ctx, row, pod_state, pod, list_state, items)
                    wpending, wseen = workloads()
                else:
                    done3, pending3, definitive, wpending, wseen = False, [], [], ['G2 first'], {}
                last = {'g2': pending2, 'g3': pending3, 'workloads': wpending}
                if definitive:
                    record(ev, stopRow=(row or {}).get('stop6'), registry=(row or {}).get('registry'))
                    raise Stop(6, 'definitive mismatch: %s' % '; '.join(definitive))
                late = time.monotonic() > deadline
                if done2 and done3 and not wpending and not late:
                    break
                if late or time.monotonic() >= deadline:
                    if done2 and done3 and not wpending:
                        last = {'late': 'every condition held, but the reads returned after the window'}
                    record(ev, last=last, stopRow=(row or {}).get('stop6'), registry=(row or {}).get('registry'))
                    raise Stop(5, 'not reached within %d s: %s' % (WAIT, json.dumps(last, sort_keys=True)))
                time.sleep(min(POLL, max(0.2, deadline - time.monotonic())))
            DEADLINE.clear()  # from here on: grace for the evidence and the final record, not the gate window
            stop = row['stop6']
            reg = row['registry']
            fence = {k: reg.get(k) for k in ('state', 'phase', 'generation', 'revision', 'pod', 'active', 'ambiguous')}
            passed = True
            say('G3 at %s: gen-6 stop %s requested after the intent; v2 terminal receipt %s; proof pod-deleted on Node %s '
                'boot %s; registry %s; no runner Pod; no open stop; conversations unchanged; workloads back'
                % (now(), stop['stopId'], json.dumps(stop['receipt']['terminal'], sort_keys=True), NODE[1], ctx['b1'],
                   json.dumps(fence, sort_keys=True)))
            evidence = no_input(ev)
            say('no-input evidence (observed scope): sudo commands this boot %d, matching delete/reclaim/scale/patch/'
                'apply/edit/rollout/mount/reboot words %d %s; ssh logins %d %s; controller outcomes for …5804 %s; host '
                'units %s; raw logs kept private: sudo %s, sshd %s, controller %s'
                % (evidence['sudo']['commands'], len(evidence['sudo']['riskyAt']), evidence['sudo']['riskyAt'],
                   len(evidence['sshd']['acceptedAt']), evidence['sshd']['acceptedAt'],
                   json.dumps(evidence['controller']['outcomes5804'], sort_keys=True),
                   json.dumps(evidence['hostUnits'], sort_keys=True), evidence['sudo']['sha256'][:16],
                   evidence['sshd']['sha256'][:16], evidence['controller']['sha256'][:16]))
            say('limits: ' + LIMITS)
            record(ev, g3={'at': now(), 'stop': stop, 'registry': reg, 'workloads': wseen}, noInput=evidence,
                   proved={'bootId': ctx['b1'], 'stop': {'stopId': stop['stopId'], 'pod': stop['pod'],
                                                        'bootId': stop['bootId']}, 'fence': fence},
                   **{'pass': True})
        except Stop as stop_:
            code, message = stop_.code, stop_.message
        except Interrupted:
            code, message = 130, 'interrupted'
        except OSError:
            code, message = (9, 'proved, but writing the evidence failed') if passed else (6, 'evidence write failed')
        except Exception:
            code, message = (9, 'proved, but an unexpected error followed') if passed else (6, 'unexpected error')
    except BaseException:
        if not message:
            code, message = 130, 'interrupted'
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, signal.SIG_IGN)
        except Exception:
            pass
    if message:
        say('STOPPED (%d): %s' % (code, message))
    if ev:
        try:
            record(ev, result=code, finished=now(), message=message)
        except Exception:
            if code == 0:
                code = 9
            say('evidence write failed at the end')
    if code == 0:
        say('W6 G2+G3 ok: boot %s -> %s on Node %s; the gen-6 runner was released by the controller with a v2 terminal '
            'receipt and a pod-deleted proof on the new boot. NEXT: re-entry (w6a2-enter.py) as relayed by PM'
            % (ctx['b0'], ctx['b1'], NODE[1]))
    say('result rc=%d' % code)
    return code


if __name__ == '__main__':
    sys.exit(main())
