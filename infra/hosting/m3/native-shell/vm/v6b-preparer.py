"""#68 dev-VM stage V6b: the host preparer is moved to the #68 release, and a new fixture owner …5805 gets its
first binding through it (user decisions 2026-11-06; PM 2026-11-08). Development VM only.

    python3 -I /root/native68/v6b-preparer.py precheck   # read-only: changes nothing, writes no file
    python3 -I /root/native68/v6b-preparer.py install    # once, after review: preparer files, no entry
    python3 -I /root/native68/v6b-preparer.py enter      # once, after review and install rc 0
    python3 -I /root/native68/v6b-preparer.py prepare-again   # once, only after an enter rc 9 and a fix
    python3 -I /root/native68/v6b-preparer.py restore    # only on a PM decision

The reviewed worker.py (repository file infra/hosting/m3/multi-user-runners/rollout/worker.py, SHA-256 a3eb51be…,
unchanged since 952e409) is copied with this script to /root/native68/v6b/worker.py (root 0600).

Host preparer (unit issue16-controller.service, oneshot, disabled; its timer disabled; neither is enabled here):
controller.sh runs `docker run … <image ID> -B /rollout/worker.py controller --inputs /private/inputs.json` with
its own CODEX_USER_RUNNER_ALLOWLIST. It reads inputs.json only to prepare a NEW owner: an approved editor with a
registry row in provisioning/capacity_wait, no Pod and no binding. issue16-restore.service (at boot, other image)
runs the same worker.py with `restore`; that path is identical in the old and the reviewed file.

precheck (read-only; also the first step of install and enter; rc 3 refused):
- entry closed: no live session, no runner Pod, api and runner-controller the recorded UIDs, 1/1, the #68 runtime,
  allowlist …5804 only; registry and bindings exactly the five owners, idle_stopped, no Pod, the fences and plan
  digests recorded after V6a; no pending stop/entry/outbox/admission; no …5805 account, registry row or binding;
  provision output and storage root hold exactly the five owners' entries.
- broker: StatefulSet and Pod as V6a left them; BROKER_ACCOUNT_ID equals inputs.json providerAccountId (boolean).
- host: unit and timer inactive and disabled, no runtime directory, no Docker container; restore.sh and
  stop-controller.sh the recorded files; controller.sh/worker.py the old files (before install) or the new ones
  with .r1 backups (after install); the #68 provisioner archive by SHA-256; the reviewed worker.py copy.

install (rc 3 refused, nothing changed; 4 image load or check failed; 5 a file step failed; no undo in the run):
1. evidence directory v6b-install-<UTC> (root 0700) with private/ (0700).
2. docker load of the #68 provisioner archive (SHA-256 checked first) unless already present; its ID must be the
   archive's manifest digest 710d9b15…; the six preparer modules inside it must equal the reviewed 42a1e8b files.
3. inputs.json: the original bytes copied to private/; `image` -> the #68 runner digest (the one V7a gave
   …5804); every other key unchanged (boolean). Temporary file in the same directory, root 0600, rename.
4. worker.py -> the reviewed file; controller.sh -> the old file with exactly two changes: the image ID and the
   allowlist (…5805 only). For each: the new file is written to a temporary file in the same directory (root,
   final mode), the old file is moved to <name>.r1 with `mv -n` (an existing .r1 is refused before the call:
   "backup collision", nothing moved; the move is proved by inode), then the temporary file is renamed to the
   name. The move and the rename run with signals blocked; only a failing rename leaves the name absent with
   .r1 = the old file, which restore accepts. The name is empty only between the two calls, inside the window
   where the unit cannot run (checked before and after). Modes (worker.py 0600, controller.sh 0700, root:root)
   and every SHA-256 checked.
No mixed version can run: nothing starts the unit (disabled, timer disabled, inactive), and even a manual start
during install has no owner to prepare (no …5805 row exists until enter; the five owners have bindings).
enter checks every installed file again before it starts the unit.

enter (rc 3 refused; 6 account/allowlist step failed; 7 session or POST failed; 8 token renewal failed; 9 the
preparer did not report …5805 processed; 10 the runner Pod not proved Running; 11 a result check failed;
70 every step passed but the session revoke is not proved: not a V6b pass):
1. evidence directory v6b-enter-<UTC>.
2. auth.accounts row …5805 (github_id 900000005805, login SYNTHETIC-DEVVM68-5805-NOT-A-USER, editor, approved).
3. allowlists (the V7a way): runner-controller and api to 0 by UID; one JSON Patch each (tests: UID,
   resourceVersion, replicas 0, container name, env name and the old value …5804) -> …5805 only; api started, then
   runner-controller; each 1/1 on the same #68 runtime manifest.
4. one session for …5805 (1 hour, the API's origin-bound hash) and one POST /api/research/codex/runner: 202 and
   a registry row in provisioning, generation 1, no Pod. An error status: session revoked, stop. An unknown
   outcome: never repeated.
5. the preparer's kube-token (24 h TokenRequest tokens, expired since 2026-10-26): one new token for
   runner-pool-controller with --duration=24h (rollout README "Private host inputs"); temporary file root 0600 in
   the same directory; the old file moved to kube-token.r1 with `mv -n` (as for the files above), then rename. Only
   the expiry time is printed. Nothing else holds the token. The request is made once whatever its outcome: a
   failed or unknown request, an invalid response, or a token that could not be installed is recorded as such
   and is never reissued (the approval covers one issuance). A failing rename leaves kube-token absent with
   kube-token.r1 = the old (expired) file: the preparer is then not started, and any further step is a PM
   decision (a new token needs a new approval).
6. `systemctl start issue16-controller.service` once (the timer stays disabled). Pass: unit Result=success and
   exactly one worker line {"owner": "…5805", "stage": "provision", "result": "processed"}.
7. the runner Pod (created by the in-cluster runner-controller from the published binding) Running within 300 s.
8. checks, printed as booleans: the binding is a v1 plan (no `version`, no `executor`, the nine plan fields) on
   the #68 runner digest for the registry's state, generation 1; the Pod has exactly one container `runner`, no
   init container, the #68 runner image, the section 2 seccomp profile, UID 10001, runner-workload, no token
   automount; the runner log has no `runner_policy_check` refused line; /control/operator-account on the new
   storage equals the broker's BROKER_ACCOUNT_ID; the runner /health through runner-controller's own code names
   the owner, state and Pod; the five owners' fences and plans unchanged; no other runner Pod.
9. the session is revoked, on success and on every failure after its insert was attempted. A revoke that is not
   proved (DELETE or the check failed) is printed as SESSION NOT REVOKED and recorded (sessionLeft) together
   with the step's own failure; after a run whose steps all passed it is rc 70. A session that may still be live
   is never a V6b pass. The Pod is left to the controller's idle stop (CODEX_USER_RUNNER_IDLE_SECONDS=1800). No
   model turn: V6-pre.
Reverting files does not undo state. After step 4 the …5805 registry row exists; after step 6 its storage
(loop-backed ext4 under the storage root), PV/PVC/Secret, provision output and binding exist. They stay until V9.

prepare-again (rc 3 refused, nothing changed; 9/10/11 as in enter): added after the 2026-11-08 enter stopped at
step 6 (rc 9): the preparer's DSN named an earlier postgres Pod IP. After the user fixed that outside this script,
it runs the preparer once more. One run only (v6b-prepare-<UTC>); it creates no session, makes no POST and issues
no token. Precheck (all read-only): exactly one install (rc 0) and one enter whose record ends at the preparer
(rc 1, exit-code, no worker line, session revoked, token done, no result); the enter token expires at least 1 h
later, checked once in the precheck and again right before `systemctl start` (that second value is recorded;
a start is refused from expiry - 1 h on, i.e. after 2026-11-09T08:36:00Z for the 2026-11-08 token; a new token
needs a new approval); kube-token 0600 newer than
the enter start and kube-token.r1 the old file; installed files, inputs.json and image as installed; the broker
account equals inputs.json; entry closed with both allowlists …5805 (no live session, no runner Pod, the five
owners as recorded); …5805 is the account enter created and its registry row is exactly provisioning, generation
1, revision 1, no Pod, on enter's state; no binding, binding resources, lock, provision output, storage, PV, PVC
or transport Secret for it; the unit failed once with Result=exit-code, NRestarts 0, CanStart yes (its start limit
is 5 in 10 s; one start this boot), the timer inactive and disabled, no runtime directory, no Docker container.
Then enter steps 6-8 (systemctl start once, the Pod, the checks) and a final "no live session" check. The DSN
file is not read.

An enter that stops inside step 3 can leave api and runner-controller at 0: entry is then closed for everyone.

restore (PM decision only; rc 3 refused, 5 failed): refused while a …5805 runner Pod exists (stop it through the
API first). Does only what is still missing: both allowlists back to …5804 and both Deployments 1/1 again (step 3
in reverse, also after an enter that stopped with them at 0); controller.sh and worker.py back from .r1 with
`mv -n` (the installed file first moved to <name>.v6b with `mv -n`); inputs.json back to the install backup
(temporary file, rename). Per file, identified by the SHA-256 of (name, .r1, .v6b), every state install or
restore can leave:
   (old, -, -) not installed, (old, -, new) restored: nothing to do;
   (new, old, -) installed: both moves; (-, old, new) restore stopped between its moves: the second move;
   (-, old, -) install's rename failed: the second move.
Anything else is refused. It never deletes the account row, registry row, binding, storage, image or kube-token.

Temporary files (the inputs.json and kube-token temporary files may hold the account id or a token; the patch
file holds no secret) are removed in the same run on every exit path the process can handle (as in V6a: O_EXCL
create and registration with signals blocked; an existing path is left and refused, rc 72; a removal failure is
rc 71; SIGINT/SIGTERM/SIGHUP raise inside the run, rc 130). SIGKILL and power loss are not covered: back then
checks read-only for `.inputs.json.v6b-*`, `.kube-token.v6b-*`, `.<name>.v6b-*` and reports to PM.

Output is fixed messages, UIDs, states, digests of non-secret files and booleans: no account id, token, Secret
data, DSN or command error text.
"""
import calendar
import hashlib
import http.client
import json
import os
import re
import secrets
import signal
import stat
import subprocess
import sys
import time
import uuid

NEW = '00000000-0000-4000-8000-000000005805'
NEW_HEX = uuid.UUID(NEW).hex
NEW_LOGIN, NEW_GITHUB = 'SYNTHETIC-DEVVM68-5805-NOT-A-USER', '900000005805'
OLD_CANARY = '00000000-0000-4000-8000-000000005804'
# The fences and canonical plan digests (ops.runner_binding_image.plan_digest) after V7a/V6a (2026-11-08).
FENCES = {
    '00000000-0000-4000-8000-000000000048': ('34823dfb-6b50-4b8e-b230-191739bdc57d', 1, 4,
                                             '75f6336aa81a874f512e2388c7b8c3060ad274900668e938a00174523f938228'),
    '00000000-0000-4000-8000-000000005801': ('eb766aef-8016-4439-b217-5ae6b9927a4c', 3, 12,
                                             'b27150dfcd18b2201b963a1e6c8a14ff26dd0d4ddc70c0e7c67c41789efbde5e'),
    '00000000-0000-4000-8000-000000005802': ('cfd1fedc-dd6b-4089-8df2-c1070a6146e1', 3, 12,
                                             'd40a779ce92b518c673be89a24c8536dbfe972089092f2a67c15cad942985afd'),
    '00000000-0000-4000-8000-000000005803': ('0383c764-bea9-48aa-8368-05220946e4c9', 2, 8,
                                             '28a60c7d1ff40954ceb84050aa8eb15d589e2ab1b5e97521d2dc5db30bad21b4'),
    OLD_CANARY: ('5ff583e9-abe1-4ef8-b277-4a5afead6b80', 7, 28,
                 '3d0c611a636f10c94ffe1f07560bb7073827c71fb7f5810e7a22a88fc06c91b4'),
}
OWNERS = tuple(sorted(FENCES))
OLD_ALLOW = ','.join(OWNERS)

K3S, DOCKER, SYSTEMCTL, JOURNALCTL = '/usr/local/bin/k3s', '/usr/bin/docker', '/usr/bin/systemctl', '/usr/bin/journalctl'
MV = '/usr/bin/mv'
EVIDENCE = '/root/native68/evidence'
LIB = '/usr/local/lib/anxious-lookout/issue16'
CONTROLLER_SH, WORKER = LIB + '/controller.sh', LIB + '/worker.py'
FIXED_FILES = {LIB + '/restore.sh': '3e519a67f11297d5911d72b06db1dd14b430be5c7b342a4d8ca129fa255b4c51',
               LIB + '/stop-controller.sh': 'f22327df4f28699d46bc0883845ff4c6710b77b0208a641873f227c8e7027285'}
OLD_CONTROLLER_SHA = 'c04f551ec302be427f5cf6917cfd990333393bdf346f417d710cce9f99f3ba17'
NEW_CONTROLLER_SHA = '41ddcbbf06decda55bfdd5f0c01d4ca706018bb2211f02ae77972155de57d63a'
OLD_WORKER_SHA = '77244e29f5ecb7f0ccdfa53c0bf62bf8fce3b21b86371fb58964944db4ce8afe'  # repository 997ba77 (#46)
NEW_WORKER_SHA = 'a3eb51bec6828add8661b4834d34058abf28840538358deeef5cf42214eef5b5'  # 952e409 .. 42a1e8b .. HEAD
WORKER_SOURCE = '/root/native68/v6b/worker.py'
MODES = {CONTROLLER_SH: 0o700, WORKER: 0o600}
OLD_IMAGE_ID = 'sha256:2bcc7cf6b69ea2a2d0e4083091da09ddab2f76e6e01c19dbbe0149ff7c008c15'  # review59 provisioner
NEW_IMAGE_ID = 'sha256:710d9b150babcbdb564fc4623492659ef975802312b364274eb9009b60ceb110'  # #68 manifest digest
PROVISIONER_TAG = 'anxious-dev-provisioner:68-42a1e8b'
ARCHIVE = '/home/opc/native68-images/anxious-dev-provisioner-68-42a1e8b.tar'
ARCHIVE_SHA = '252f9dfb59bf9d002c039ffa511fd08786d13584a8b863b4e7d39deff62eb3e6'
MODULES = {'/app/ops/runner_provision_worker.py': '5bc630531a79d408992d2f1914f542b97891a557ee490451f3edb7cb0a48023a',
           '/app/ops/user_runner_prepare.py': '430265307567c10292b6f61d5006a209596da17d5267eb8174bfbe5a4d8bb490',
           '/app/ops/runner_storage.py': '56263e7ac9b5c40ac3f1b973808a83f770b5c07193239672794eef404b2eb782',
           '/app/ops/runner_binding_import.py': 'bf33e48e875f54f6a2445160b40aafee940f5d9d6cbfb5df8580ebea309b0b33',
           '/app/app/runner_kube.py': 'e7d6bf21f0412a4939c757965b83694708ef3c9394ee6d1e68f2dc7fcd00aa57',
           '/app/app/runner_bindings.py': '0cc5706f8db9193d1b1ff61dac46533a62a09eb951dfff39f2d2b2d0160e5342'}
ALLOW_ENV = 'CODEX_USER_RUNNER_ALLOWLIST'
OLD_ALLOW_ARG = ' -e %s=%s ' % (ALLOW_ENV, OLD_ALLOW)
NEW_ALLOW_ARG = ' -e %s=%s ' % (ALLOW_ENV, NEW)

PRIVATE_DIR = '/etc/anxious-lookout/issue16/controller'
INPUTS, KUBE_TOKEN = PRIVATE_DIR + '/inputs.json', PRIVATE_DIR + '/kube-token'
INPUTS_V6A = 'a8918433949e3d4c7d4c5ae645c01159736a6b80dcc26a809d367444609311b8'
INPUT_KEYS = ['api', 'caFile', 'callback', 'hostRoot', 'image', 'kubeTokenFile', 'namespace', 'namespaceUid', 'node',
              'podCIDR', 'providerAccountId']
OLD_RUNNER_IMAGE = ('docker.io/library/anxious-review59-runner@sha256:'
                    'de5d3da1a73c3d953cc0386479e3801d529d10f06bb4be4a7e09a9c84baf2936')
RUNNER_REF = 'docker.io/library/anxious-dev-runner@sha256:840679946e8071b5495b9609c6e9f33c0708d9196e712e262361e200d4641801'
RUNNER_ID = 'sha256:ddd7bad4ea352eff5d905be329e7acf32c5ff415b52c8e9ae4a759e6b714f6c0'
PLAN_FIELDS = ['callback', 'control', 'generation', 'image', 'native', 'owner', 'serviceAccount', 'state', 'transport']
SECCOMP_PROFILE = 'profiles/anxious-runner-userns-v1.json'

PROVISION_OUTPUT = '/var/lib/anxious-lookout/issue16-d4/provision-output'
STORAGE_ROOT = '/var/lib/anxious-lookout/user-runners'
UNITS = ('issue16-controller.service', 'issue16-controller.timer')
RUNTIME_DIR = '/run/issue16-controller'

BROKER = ('codex-broker', 'codex-broker', 'a4016104-57c6-4dc1-b3d5-b271ba029958', '5659c91b-588c-4ba8-b423-b853a6b23f08')
RUNTIME_REF = 'docker.io/library/anxious-dev-runtime@sha256:f5a84ff5d7bf6f00a79fa3d421a8176e6198436d8266b91370f617475a159db5'
RUNTIME_MANIFEST = RUNTIME_REF.split('@')[1]
# (namespace, name, UID, Pod label, containers, init containers); every container runs the #68 runtime.
CTRL = ('runner-control', 'runner-controller', '41564f26-95e7-4ad1-8af3-d73b93c4669a', 'app=runner-controller',
        ('controller',), ())
API = ('m2-hosting', 'api', 'fad6799a-6df5-4086-812f-cfae6212adfa', 'app=hosting-api', ('api',),
       ('runner-pool-input',))
RUNNER_NS, POD_NAME = 'codex-runner', 'runner-' + NEW_HEX
ORIGIN, API_HOST, API_PORT, HOST_HEADER = 'http://192.88.99.1', '10.0.0.107', 80, '192.88.99.1'
RUNNER_PATH = '/api/research/codex/runner'
WAIT, POD_WAIT, PREPARER_TIMEOUT = 180, 300, 1900
HEALTH_KEYS = ('ownerId', 'stateId', 'podUid', 'available', 'reason', 'verification')
# Inside the runner-controller Pod with the controller's own code (read-only; as w6a2-enter.py).
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
    """SIGINT/SIGTERM/SIGHUP, raised inside the run so every `finally` cleanup runs."""


SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


def ignore_signals():
    for signum in SIGNALS:
        signal.signal(signum, signal.SIG_IGN)


def interrupted(signum, frame):
    ignore_signals()
    raise Interrupted()


TEMPORARY = []  # files THIS run created (O_EXCL succeeded)


def create_temporary(path, mode=0o600):
    old = signal.pthread_sigmask(signal.SIG_BLOCK, SIGNALS)
    try:
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
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


def short(owner):
    return '…' + owner[-4:]


def run(cmd, timeout=60, stdin=None):
    """(rc, stdout) of a fixed command; stderr discarded; 124 timeout, 125 not started."""
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


def sql(tag, query, code, write=False):
    """JSON rows (one json_build_object per line) as postgres in postgres-0; READ ONLY unless `write`."""
    text = '%s\n/*v6b:%s*/ %s\nCOMMIT;\n' % ('BEGIN;' if write else 'BEGIN READ ONLY;', tag, query)
    rc, out = run([K3S, 'kubectl', '-n', 'm2-hosting', 'exec', '-i', 'postgres-0', '--', 'psql', '-X', '-U', 'postgres',
                   '-d', 'hosting', '-Atq', '-v', 'ON_ERROR_STOP=1', '-f', '-'], 60, text.encode())
    if rc != 0:
        raise Stop(code, 'database %s failed (%s)' % ('change' if write else 'lookup', tag))
    try:
        found = [json.loads(line) for line in out.splitlines() if line.strip() not in ('', 'BEGIN', 'COMMIT')]
    except ValueError:
        raise Stop(code, 'database result unreadable (%s)' % tag) from None
    if not all(isinstance(row, dict) for row in found):
        raise Stop(code, 'database result unreadable (%s)' % tag)
    return found


def digest(plan):
    """ops.runner_binding_image.plan_digest: sha256 of the sorted, compact JSON."""
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


# Host files ---------------------------------------------------------------------------------------------
def read_file(path, code, mode=None):
    """Bytes of a root-owned regular file with one link (and `mode`), or None when absent."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != 0 or info.st_nlink != 1 \
            or (mode is not None and stat.S_IMODE(info.st_mode) != mode):
        raise Stop(code, '%s is not a root-owned regular file with one link%s' % (path, '' if mode is None else
                                                                                  ' and mode %o' % mode))
    with open(path, 'rb') as stream:
        return stream.read()


def host_files(code, state):
    """state 'old': controller.sh/worker.py the recorded files, no .r1/.v6b; 'new': the reviewed files with
    .r1 = the old ones; 'restored': the old files, .v6b = the installed ones where they were installed. Fixed
    files always the recorded ones."""
    for path, want in FIXED_FILES.items():
        data = read_file(path, code, 0o700)
        if data is None or sha_bytes(data) != want:
            raise Stop(code, '%s is not the recorded file' % path)
    shas = {CONTROLLER_SH: (OLD_CONTROLLER_SHA, NEW_CONTROLLER_SHA), WORKER: (OLD_WORKER_SHA, NEW_WORKER_SHA)}
    for path, (old, new) in shas.items():
        data = read_file(path, code, MODES[path])
        backup = read_file(path + '.r1', code, MODES[path])
        kept = read_file(path + '.v6b', code, MODES[path])
        current = None if data is None else sha_bytes(data)
        if state == 'old':
            ok = current == old and backup is None and kept is None
        elif state == 'restored':  # restore: the old file back, the installed one kept as .v6b (if installed)
            ok = current == old and backup is None and (kept is None or sha_bytes(kept) == new)
        else:
            ok = current == new and backup is not None and sha_bytes(backup) == old and kept is None
        if not ok:
            raise Stop(code, '%s is not in the %s state' % (path, 'pre-install' if state == 'old' else 'installed'))


def file_state(path, code):
    """(sha of path, of path.r1, of path.v6b), None where absent; each present one a root file with one link."""
    return tuple(None if data is None else sha_bytes(data)
                 for data in (read_file(name, code, MODES[path]) for name in (path, path + '.r1', path + '.v6b')))


def inputs_now(code):
    data = read_file(INPUTS, code, 0o600)
    if data is None:
        raise Stop(code, 'inputs.json missing')
    try:
        value = json.loads(data)
    except ValueError:
        raise Stop(code, 'inputs.json unreadable') from None
    if sorted(value) != INPUT_KEYS:
        raise Stop(code, 'inputs.json keys are not the recorded set (executor must be absent)')
    return value, data


def units_quiet(code, where):
    for unit in UNITS:
        rc, out = run([SYSTEMCTL, 'show', '-p', 'ActiveState,UnitFileState', unit])
        props = dict(line.split('=', 1) for line in out.splitlines() if '=' in line)
        if rc != 0 or props.get('ActiveState') != 'inactive' or props.get('UnitFileState') != 'disabled':
            raise Stop(code, '%s: %s is not inactive and disabled' % (where, unit))
    if os.path.lexists(RUNTIME_DIR):
        raise Stop(code, '%s: %s exists (a preparer run may be in progress)' % (where, RUNTIME_DIR))
    rc, out = run([DOCKER, 'ps', '-q'])
    if rc != 0 or out:
        raise Stop(code, '%s: a Docker container is running' % where)


def docker_image(code):
    """The ID Docker reports for the #68 provisioner tag, or None when it is not loaded."""
    rc, out = run([DOCKER, 'image', 'inspect', '--format', '{{.Id}}', PROVISIONER_TAG], 30)
    if rc != 0:
        return None
    if out != NEW_IMAGE_ID:
        raise Stop(code, '%s is present with another ID' % PROVISIONER_TAG)
    return out


# Broker and entry -------------------------------------------------------------------------------------------
def broker_account(code, value):
    """The broker's BROKER_ACCOUNT_ID (kept in memory only); refuses unless it equals inputs.json's."""
    ns, name, uid, pod_uid = BROKER
    d = kjson(['-n', ns, 'get', 'statefulset', name], code, 'broker lookup failed')
    p = kjson(['-n', ns, 'get', 'pod', name + '-0'], code, 'broker Pod lookup failed')
    try:
        t = d['spec']['template']['spec']
        env = dict((e['name'], e.get('value')) for e in t['containers'][0].get('env', []))
        ready = [c.get('ready') for c in p['status'].get('containerStatuses') or []]
        ok = (d['metadata']['uid'] == uid and d['metadata']['generation'] == 3 and [c['name'] for c in t['containers']]
              == ['broker'] and not t.get('initContainers') and p['metadata']['uid'] == pod_uid and ready == [True])
        account = env.get('BROKER_ACCOUNT_ID') or ''
    except Exception:
        ok, account = False, ''
    if not ok:
        raise Stop(code, 'the broker is not the V6a state (StatefulSet generation 3, Pod 5659c91b…, 1/1)')
    if not account or value.get('providerAccountId') != account:
        raise Stop(code, 'inputs.json providerAccountId is not the broker account (boolean check)')
    return account


def deployment(spec, code):
    d = kjson(['-n', spec[0], 'get', 'deployment', spec[1]], code, '%s lookup failed' % spec[1])
    try:
        pod = d['spec']['template']['spec']
        containers, inits = pod['containers'], pod.get('initContainers') or []
        if tuple(c['name'] for c in containers) != spec[4] or tuple(c['name'] for c in inits) != spec[5] \
                or any(c['image'] != RUNTIME_REF for c in containers + inits) or d['metadata']['uid'] != spec[2]:
            raise ValueError
        env = containers[0].get('env') or []
        index = [i for i, e in enumerate(env) if e.get('name') == ALLOW_ENV]
        if len(index) != 1 or 'value' not in env[index[0]]:
            raise ValueError
        status = d.get('status') or {}
        return d, env[index[0]]['value'], index[0], int(d['spec']['replicas']), int(status.get('readyReplicas') or 0)
    except Exception:
        raise Stop(code, '%s is not the recorded Deployment on the #68 runtime' % spec[1]) from None


def runner_pods(code):
    return kubectl(['-n', RUNNER_NS, 'get', 'pods', '-o', 'name'], code, 'runner Pod lookup failed').split()


def owners(code, where):
    """The five owners exactly as recorded (fence and plan digest), no pending work."""
    found = sql('owners', "SELECT json_build_object('owner',r.owner_id,'state',r.state_id,'phase',r.phase,"
                "'generation',r.generation,'revision',r.revision,'pod',r.pod_uid,'active',r.active_requests,"
                "'ambiguous',r.ambiguous,'bindingState',b.state_id,'plan',b.plan) FROM runner_runtime.registry r "
                "LEFT JOIN runner_bindings.bindings b ON b.owner_id=r.owner_id WHERE r.owner_id IN (%s) ORDER BY 1::text;"
                % ','.join("'%s'" % o for o in OWNERS), code)
    by = {row.get('owner'): row for row in found}
    if sorted(by) != list(OWNERS):
        raise Stop(code, '%s: the five owners are not all present' % where)
    for owner, (state, generation, revision, plan) in FENCES.items():
        row = by[owner]
        if (row['state'], row['phase'], row['generation'], row['revision'], row['pod'], row['active'],
                row['ambiguous'], row['bindingState']) != (state, 'idle_stopped', generation, revision, None, 0,
                                                           False, state) \
                or not isinstance(row['plan'], dict) or digest(row['plan']) != plan:
            raise Stop(code, '%s: %s fence or plan is not the recorded one' % (where, short(owner)))
    pending = sql('pending', "SELECT json_build_object('stops',(SELECT count(*) FROM runner_termination.stops WHERE "
                  "completed_at IS NULL AND owner_id IN (%s)),'entries',(SELECT count(*) FROM runner_termination.entries "
                  "WHERE consumed_at IS NULL AND owner_id IN (%s)),'outbox',(SELECT count(*) FROM runner_runtime.outbox "
                  "WHERE NOT settled AND owner_id IN (%s)),'admissions',(SELECT count(*) FROM runner_runtime.admissions "
                  "WHERE completed_at IS NULL AND owner_id IN (%s)));" % ((','.join("'%s'" % o for o in OWNERS),) * 4),
                  code)
    if pending != [{'stops': 0, 'entries': 0, 'outbox': 0, 'admissions': 0}]:
        raise Stop(code, '%s: pending runner work exists for the five owners' % where)


def new_owner(code):
    """…5805: account row, registry row and binding as JSON (None where absent)."""
    row = sql('new', "SELECT json_build_object('account',(SELECT json_build_object('github',github_id,'login',login,"
              "'role',role,'approved',is_approved) FROM auth.accounts WHERE id='%s'),'registry',(SELECT json_build_object("
              "'state',state_id,'phase',phase,'generation',generation,'revision',revision,'pod',pod_uid) FROM "
              "runner_runtime.registry WHERE owner_id='%s'),'binding',(SELECT json_build_object('state',state_id,'plan',"
              "plan) FROM runner_bindings.bindings WHERE owner_id='%s'),'sessions',(SELECT count(*) FROM auth.sessions "
              "WHERE account_id='%s' AND expires_at>now()));" % (NEW, NEW, NEW, NEW), code)
    if len(row) != 1:
        raise Stop(code, '…5805 lookup unreadable')
    return row[0]


def host_dirs(code, with_new):
    want_out = sorted([uuid.UUID(o).hex for o in OWNERS] + ['.lock-' + uuid.UUID(o).hex for o in OWNERS])
    want_storage = sorted(uuid.UUID(s[0]).hex for s in FENCES.values())
    out, storage = sorted(os.listdir(PROVISION_OUTPUT)), sorted(os.listdir(STORAGE_ROOT))
    if with_new:
        return out, storage
    if out != want_out or storage != want_storage:
        raise Stop(code, 'provision output or storage root is not exactly the five owners')
    return out, storage


def entry_closed(code, where, allow=OLD_CANARY):
    live = sql('sessions', "SELECT json_build_object('n',count(*)) FROM auth.sessions WHERE expires_at>now();", code)
    if live != [{'n': 0}]:
        raise Stop(code, '%s: a live session exists' % where)
    if runner_pods(code):
        raise Stop(code, '%s: a runner Pod exists' % where)
    for spec in (API, CTRL):
        _, value, _, want, ready = deployment(spec, code)
        if value != allow or want != 1 or ready != 1:
            raise Stop(code, '%s: %s is not 1/1 with allowlist %s only' % (where, spec[1], short(allow)))
    owners(code, where)


def precheck(code, state):
    """The common read-only checks. `state` is the host-file state expected ('old' before install)."""
    if os.geteuid() != 0:
        raise Stop(code, 'run as root')
    entry_closed(code, 'precheck')
    found = new_owner(code)
    if found['account'] is not None or found['registry'] is not None or found['binding'] is not None:
        raise Stop(code, 'precheck: …5805 already has an account row, registry row or binding')
    host_dirs(code, False)
    value, data = inputs_now(code)
    account = broker_account(code, value)
    units_quiet(code, 'precheck')
    host_files(code, state)
    source = read_file(WORKER_SOURCE, code, 0o600)
    if source is None or sha_bytes(source) != NEW_WORKER_SHA:
        raise Stop(code, 'the reviewed worker.py copy is missing or not the reviewed file')
    return value, data, account


# Evidence ---------------------------------------------------------------------------------------------------
def runs(kind):
    return sorted(d for d in os.listdir(EVIDENCE) if re.fullmatch(r'v6b-%s-\d{8}T\d{6}Z' % kind, d))


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


def evidence(kind):
    ev = os.path.join(EVIDENCE, 'v6b-%s-%s' % (kind, time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())))
    os.mkdir(ev, 0o700)
    os.mkdir(os.path.join(ev, 'private'), 0o700)
    record(ev, action=kind, started=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
    say('evidence: %s' % ev)
    return ev


def write_new(path, data, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def fsync_dir(folder):
    fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def move_noreplace(source, target, code):
    """`mv -n -T source target`, proved by inode: `target` did not exist, now holds the inode `source` had, and
    `source` is gone. mv -n never overwrites; an existing target is refused before the call."""
    before = os.lstat(source)
    if os.path.lexists(target):
        raise Stop(code, 'backup collision: %s exists (nothing moved)' % target)
    rc, _ = run([MV, '-n', '-T', source, target], 30)
    try:
        moved = not os.path.lexists(source) and os.lstat(target).st_ino == before.st_ino
    except OSError:
        moved = False
    if not moved:
        raise Stop(code, '%s not moved to %s (mv rc %d)' % (source, target, rc))


def replace_file(path, data, mode, code, keep=None):
    """Write `data` to a temporary file in the same directory (root, `mode`); optionally move the current file to
    `keep` with mv -n (refuses an existing `keep`); then rename the temporary file to `path`. The move and the
    rename run with the three signals blocked, so only a failing rename can leave `path` absent with `keep`
    holding the old file (restore accepts that state). True when the rename happened."""
    folder, base = os.path.split(path)
    temp = os.path.join(folder, '.%s.v6b-%d' % (base, os.getpid()))
    fd = create_temporary(temp, mode)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(temp, 0, 0)
        os.chmod(temp, mode)
        old = signal.pthread_sigmask(signal.SIG_BLOCK, SIGNALS)
        try:
            if keep is not None:
                move_noreplace(path, keep, code)
            try:
                os.rename(temp, path)
            except OSError:
                raise Stop(code, '%s: rename failed%s' % (path, '' if keep is None else
                                                          ' (the old file is %s; restore accepts this)' % keep)) from None
            TEMPORARY.remove(temp)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, old)
        fsync_dir(folder)
        return True
    finally:
        left = remove_temporary()
        if left:
            raise Stop(71, 'TEMPORARY FILE NOT REMOVED: %s (tell PM)' % ', '.join(left))


# install ----------------------------------------------------------------------------------------------------
def load_image(ev):
    if docker_image(3) is None:
        with open(ARCHIVE, 'rb') as stream:
            h = hashlib.sha256()
            for block in iter(lambda: stream.read(1 << 20), b''):
                h.update(block)
        if h.hexdigest() != ARCHIVE_SHA:
            raise Stop(4, 'the provisioner archive is not the recorded file')
        record(ev, image='pending')
        rc, _ = run([DOCKER, 'load', '-i', ARCHIVE], 900)
        if docker_image(4) != NEW_IMAGE_ID:
            record(ev, image='failed' if rc else 'uncertain')
            raise Stop(4, 'docker load did not give %s %s' % (PROVISIONER_TAG, NEW_IMAGE_ID[:19]))
        say('image: %s loaded, ID %s' % (PROVISIONER_TAG, NEW_IMAGE_ID[:19]))
    else:
        say('image: %s already present, ID %s' % (PROVISIONER_TAG, NEW_IMAGE_ID[:19]))
    rc, out = run([DOCKER, 'run', '--rm', '--pull=never', '--network=none', '--read-only', '--user=0:0',
                   '--cap-drop=ALL', '--entrypoint=sha256sum', NEW_IMAGE_ID] + sorted(MODULES), 120)
    got = dict(reversed(line.split(None, 1)) for line in out.splitlines() if len(line.split(None, 1)) == 2)
    if rc != 0 or got != MODULES:
        record(ev, image='modules differ')
        raise Stop(4, 'the provisioner modules are not the reviewed 42a1e8b files')
    record(ev, image='done')
    say('image: the six preparer modules equal the reviewed 42a1e8b files')


def install():
    if runs('install') or runs('enter'):
        raise Stop(3, 'install already ran (one run only)')
    value, data, _ = precheck(3, 'old')
    if sha_bytes(data) != INPUTS_V6A or value['image'] != OLD_RUNNER_IMAGE:
        raise Stop(3, 'inputs.json is not the V6a file')
    if docker_image(3) is None:
        if not os.path.isfile(ARCHIVE):
            raise Stop(3, 'the provisioner archive is missing')
    say('precheck ok: entry closed (five owners as recorded, allowlists …5804), no …5805, broker V6a state, '
        'preparer inactive/disabled, old controller.sh and worker.py, inputs.json the V6a file')
    ev = evidence('install')
    private = os.path.join(ev, 'private')
    load_image(ev)
    # inputs.json: image only.
    write_new(os.path.join(private, 'inputs.json.before-v6b'), data)
    new_value = dict(value, image=RUNNER_REF)
    new_data = (json.dumps(new_value, indent=2, sort_keys=True) + '\n').encode()
    record(ev, inputs='pending')
    replace_file(INPUTS, new_data, 0o600, 5)
    got, raw = inputs_now(5)
    same = {k: v for k, v in got.items() if k != 'image'} == {k: v for k, v in value.items() if k != 'image'}
    if got.get('image') != RUNNER_REF or not same:
        record(ev, inputs='failed')
        raise Stop(5, 'inputs.json is not the old file with the new image')
    record(ev, inputs='done', inputsAfterSha256=sha_bytes(raw))
    say('inputs.json: image -> #68 runner %s True; every other key unchanged %s; sha256 %s'
        % (RUNNER_REF.split('@')[1][:19], same, sha_bytes(raw)))
    # worker.py, then controller.sh (image and allowlist in one rename).
    record(ev, worker='pending')
    replace_file(WORKER, read_file(WORKER_SOURCE, 5, 0o600), MODES[WORKER], 5, keep=WORKER + '.r1')
    record(ev, worker='done')
    old = read_file(CONTROLLER_SH, 5, MODES[CONTROLLER_SH])
    if old.count(OLD_ALLOW_ARG.encode()) != 1 or old.count((' %s ' % OLD_IMAGE_ID).encode()) != 1:
        raise Stop(5, 'controller.sh does not hold the old image and allowlist exactly once')
    new = old.replace(OLD_ALLOW_ARG.encode(), NEW_ALLOW_ARG.encode()).replace(
        (' %s ' % OLD_IMAGE_ID).encode(), (' %s ' % NEW_IMAGE_ID).encode())
    if sha_bytes(new) != NEW_CONTROLLER_SHA:
        raise Stop(5, 'the new controller.sh is not the reviewed file')
    record(ev, controller='pending')
    replace_file(CONTROLLER_SH, new, MODES[CONTROLLER_SH], 5, keep=CONTROLLER_SH + '.r1')
    record(ev, controller='done')
    host_files(5, 'new')
    units_quiet(5, 'end')
    entry_closed(5, 'end')
    say('preparer: worker.py %s (kept .r1 %s), controller.sh %s (kept .r1 %s): image %s, allowlist …5805 only; '
        'modes 0600/0700 root' % (NEW_WORKER_SHA[:12], OLD_WORKER_SHA[:12], NEW_CONTROLLER_SHA[:12],
                                   OLD_CONTROLLER_SHA[:12], NEW_IMAGE_ID[:19]))
    record(ev, result=0, finished=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
    say('installed; entry still closed; next: enter')


# enter ------------------------------------------------------------------------------------------------------
def scale(spec, replicas, code):
    patch = {'metadata': {'uid': spec[2]}, 'spec': {'replicas': replicas}}
    kubectl(['-n', spec[0], 'patch', 'deployment', spec[1], '--type=merge', '-p', json.dumps(patch)], code,
            '%s scale to %d failed' % (spec[1], replicas))
    if replicas == 0:
        deadline = time.monotonic() + WAIT
        while kubectl(['-n', spec[0], 'get', 'pods', '-l', spec[3], '-o', 'name'], code, 'Pod lookup failed').split():
            if time.monotonic() >= deadline:
                raise Stop(code, '%s Pods still present after %d s' % (spec[1], WAIT))
            time.sleep(3)


def set_allowlist(spec, old, new, code, ev):
    d, value, index, want, _ = deployment(spec, code)
    if want != 0 or value != old:
        raise Stop(code, '%s is not at 0 with allowlist %s' % (spec[1], short(old)))
    env = '/spec/template/spec/containers/0/env/%d' % index
    ops = [{'op': 'test', 'path': '/metadata/uid', 'value': spec[2]},
           {'op': 'test', 'path': '/metadata/resourceVersion', 'value': d['metadata']['resourceVersion']},
           {'op': 'test', 'path': '/spec/replicas', 'value': 0},
           {'op': 'test', 'path': '/spec/template/spec/containers/0/name', 'value': spec[4][0]},
           {'op': 'test', 'path': env + '/name', 'value': ALLOW_ENV},
           {'op': 'test', 'path': env + '/value', 'value': old},
           {'op': 'replace', 'path': env + '/value', 'value': new}]
    record(ev, **{'allow-' + spec[1]: 'pending'})
    rc, _ = run([K3S, 'kubectl', '-n', spec[0], 'patch', 'deployment', spec[1], '--type=json', '-p', json.dumps(ops)])
    _, value, _, _, _ = deployment(spec, code)
    record(ev, **{'allow-' + spec[1]: 'done' if value == new else ('uncertain' if rc == 0 else 'failed')})
    if value != new:
        raise Stop(code, '%s allowlist patch failed' % spec[1])


def start(spec, code):
    scale(spec, 1, code)
    if run([K3S, 'kubectl', '-n', spec[0], 'rollout', 'status', 'deployment/' + spec[1], '--timeout=180s'], 210)[0]:
        raise Stop(code, '%s rollout did not finish' % spec[1])
    _, _, _, want, ready = deployment(spec, code)
    items = [p for p in kjson(['-n', spec[0], 'get', 'pods', '-l', spec[3]], code, 'Pod lookup failed')['items']
             if not p['metadata'].get('deletionTimestamp')]
    try:
        statuses = items[0]['status'].get('containerStatuses', []) + items[0]['status'].get('initContainerStatuses', [])
        ok = want == 1 and ready == 1 and len(items) == 1 and \
            {s['imageID'].rsplit('@', 1)[-1] for s in statuses} == {RUNTIME_MANIFEST}
    except Exception:
        ok = False
    if not ok:
        raise Stop(code, '%s is not 1/1 on the #68 runtime' % spec[1])


def allowlists(old, new, code, ev):
    """The V7a way: runner-controller and api to 0, patch both, api started, then runner-controller."""
    scale(CTRL, 0, code)
    scale(API, 0, code)
    set_allowlist(API, old, new, code, ev)
    set_allowlist(CTRL, old, new, code, ev)
    start(API, code)
    start(CTRL, code)
    say('allowlists: api and runner-controller %s only, restarted (api first), 1/1 on the #68 runtime' % short(new))


SESSION = {}


def create_session(ev):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(('m2-origin-session-v1\0' + ORIGIN + '\0' + token).encode()).hexdigest()
    SESSION.update(hash=token_hash, created=None)
    record(ev, session='pending', sessionId=token_hash[:16])
    try:
        row = sql('session', "WITH s AS (INSERT INTO auth.sessions(token_hash,account_id,csrf_token,expires_at) VALUES "
                  "('%s','%s','%s',now()+interval '1 hour') RETURNING token_hash) SELECT json_build_object('id',"
                  "left(token_hash,16)) FROM s;" % (token_hash, NEW, csrf), 7, write=True)
    except Stop:
        raise Stop(7, 'session: insert failed or its result is unknown (removed below); no POST made')
    if row != [{'id': token_hash[:16]}]:
        raise Stop(7, 'session: insert answered another row')
    SESSION['created'] = True
    record(ev, session='created')
    say('session created: id %s, 1 hour, owner …5805' % token_hash[:16])
    return token, csrf


def revoke_session(ev):
    if not SESSION.get('hash') or SESSION.get('revoked'):
        return True
    try:
        sql('revoke', "WITH d AS (DELETE FROM auth.sessions WHERE token_hash='%s' AND account_id='%s' RETURNING 1) "
            "SELECT json_build_object('deleted',count(*)) FROM d;" % (SESSION['hash'], NEW), 70, write=True)
        left = sql('session', "SELECT json_build_object('n',count(*)) FROM auth.sessions WHERE token_hash='%s';"
                   % SESSION['hash'], 70)
        SESSION['revoked'] = left == [{'n': 0}]
    except Stop:
        SESSION['revoked'] = False
    record(ev, session='revoked' if SESSION['revoked'] else 'revoke not proved (expires within the hour)')
    say('session %s: %s' % (SESSION['hash'][:16], 'revoked' if SESSION['revoked'] else
                            'revoke not proved (expires within the hour)'))
    return SESSION['revoked']


def post(token, csrf):
    connection = http.client.HTTPConnection(API_HOST, API_PORT, timeout=60)
    try:
        connection.request('POST', RUNNER_PATH, body=b'{}', headers={
            'Host': HOST_HEADER, 'Origin': ORIGIN, 'Cookie': 'anxious_session=' + token, 'x-csrf-token': csrf,
            'Idempotency-Key': str(uuid.uuid4()), 'Content-Type': 'application/json', 'Accept': 'application/json'})
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


def renew_token(ev):
    """One 24 h TokenRequest token for runner-pool-controller; only its expiry is printed. The request is made
    once: whatever its outcome, it is the one approved issuance and is never repeated."""
    before = os.lstat(KUBE_TOKEN)
    record(ev, kubeToken='requested (the one approved issuance)', kubeTokenOldMtime=int(before.st_mtime))
    rc, out = run([K3S, 'kubectl', '-n', RUNNER_NS, 'create', 'token', 'runner-pool-controller', '--duration=24h',
                   '-o', 'json'], 60)
    try:
        status = json.loads(out)['status'] if rc == 0 else {}
        token, expiry = status.get('token'), status.get('expirationTimestamp')
    except Exception:
        token, expiry = None, None
    out = None
    if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{100,4096}', token) or not expiry:
        token = None
        what = ('the request failed or its outcome is unknown (kubectl rc %d)' % rc if rc != 0 else
                'the response is invalid (a token may have been issued)')
        record(ev, kubeToken='not installed: %s; not reissued (the one issuance is used)' % what)
        raise Stop(8, 'kube-token: %s; not reissued; the old file is unchanged' % what)
    try:
        replace_file(KUBE_TOKEN, (token + '\n').encode(), 0o600, 8, keep=KUBE_TOKEN + '.r1')
    except Stop:
        record(ev, kubeToken='issued but not installed (discarded; not reissued; the one issuance is used)')
        raise
    finally:
        token = None
    info, kept = os.lstat(KUBE_TOKEN), os.lstat(KUBE_TOKEN + '.r1')
    ok = (stat.S_IMODE(info.st_mode) == 0o600 and info.st_uid == 0 and info.st_nlink == 1 and kept.st_ino == before.st_ino
          and kept.st_nlink == 1)
    record(ev, kubeToken='done' if ok else 'uncertain', kubeTokenExpiry=expiry)
    if not ok:
        raise Stop(8, 'kube-token: the new file or the kept old one is not as expected')
    say('kube-token: new runner-pool-controller token, expires %s; the old one kept as kube-token.r1' % expiry)


def preparer(ev, gate=None):
    """`gate`, if given, runs right before the start and may refuse it (prepare-again's token margin)."""
    if gate is not None:
        gate()
    since = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(time.time() - 2))
    record(ev, preparer='started', preparerStarted=since + ' UTC')
    rc, _ = run([SYSTEMCTL, 'start', 'issue16-controller.service'], PREPARER_TIMEOUT)
    _, out = run([SYSTEMCTL, 'show', '-p', 'Result,ExecMainStatus,ActiveState', 'issue16-controller.service'])
    props = dict(line.split('=', 1) for line in out.splitlines() if '=' in line)
    _, log = run([JOURNALCTL, '-u', 'issue16-controller.service', '--since', since + ' UTC', '-o', 'cat',
                     '--no-pager'],
                 60)
    lines = []
    for line in log.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and set(item) == {'owner', 'stage', 'result'}:
            lines.append({k: str(item[k])[:40] for k in ('owner', 'stage', 'result')})
    record(ev, preparer='rc %d' % rc, preparerResult=props.get('Result'), preparerStatus=props.get('ExecMainStatus'),
           preparerLines=lines)
    say('preparer: systemctl rc %d, Result=%s, ExecMainStatus=%s, lines %s' % (
        rc, props.get('Result'), props.get('ExecMainStatus'), json.dumps(lines, sort_keys=True)))
    if rc != 0 or props.get('Result') != 'success' or lines != [{'owner': NEW, 'stage': 'provision',
                                                                 'result': 'processed'}]:
        raise Stop(9, 'the preparer did not report …5805 processed; no rerun; back checks read-only')


def wait_pod():
    deadline = time.monotonic() + POD_WAIT
    while True:
        found = new_owner(10)
        reg = found['registry'] or {}
        pod = None
        rc, out = run([K3S, 'kubectl', '-n', RUNNER_NS, 'get', 'pod', POD_NAME, '-o', 'json'])
        if rc == 0:
            try:
                pod = json.loads(out)
            except ValueError:
                pod = None
        if pod is not None and reg.get('pod') == pod['metadata']['uid'] and (pod.get('status') or {}).get('phase') \
                == 'Running' and not pod['metadata'].get('deletionTimestamp'):
            statuses = (pod.get('status') or {}).get('containerStatuses') or []
            if [s.get('name') for s in statuses] == ['runner'] and statuses[0].get('ready'):
                return pod, found
        if time.monotonic() >= deadline:
            raise Stop(10, 'the runner Pod is not proved Running and ready within %d s (registry %s); no retry, no '
                           'reclaim' % (POD_WAIT, json.dumps({k: reg.get(k) for k in ('phase', 'generation', 'pod')})))
        time.sleep(5)


def result_checks(pod, found, account):
    reg, binding = found['registry'], found['binding'] or {}
    plan = binding.get('plan') or {}
    checks = {
        'binding v1 plan (nine fields, no version, no executor)': sorted(plan) == PLAN_FIELDS,
        'binding image is the #68 runner': plan.get('image') == RUNNER_REF,
        'binding owner/state/generation 1 = registry': (plan.get('owner'), plan.get('state'), plan.get('generation'),
                                                         binding.get('state')) == (NEW, reg['state'], 1, reg['state']),
    }
    spec = pod['spec']
    statuses = pod['status'].get('containerStatuses') or []
    checks['Pod one container runner, no init container'] = (
        [c['name'] for c in spec['containers']] == ['runner'] and not spec.get('initContainers'))
    checks['Pod image #68 runner'] = (spec['containers'][0]['image'] == RUNNER_REF and
                                      statuses[0].get('imageID', '').rsplit('@', 1)[-1] == RUNNER_REF.split('@')[1])
    sc = spec.get('securityContext') or {}
    checks['Pod section 2 seccomp profile, UID 10001'] = (
        sc.get('seccompProfile') == {'type': 'Localhost', 'localhostProfile': SECCOMP_PROFILE}
        and sc.get('runAsUser') == 10001 and sc.get('runAsNonRoot') is True)
    checks['Pod runner-workload, no token automount'] = (
        spec.get('serviceAccountName') == 'runner-workload' and spec.get('automountServiceAccountToken') is False)
    rc, log = run([K3S, 'kubectl', '-n', RUNNER_NS, 'logs', POD_NAME, '-c', 'runner'], 60)
    checks['runner log read'] = rc == 0
    checks['no runner_policy_check refused line'] = rc == 0 and not any(
        'runner_policy_check' in line and '"refused"' in line for line in log.splitlines())
    log = None
    path = os.path.join(STORAGE_ROOT, uuid.UUID(reg['state']).hex, 'control', 'data', 'operator-account')
    try:
        with open(path, 'rb') as stream:
            stored = stream.read(512).decode().strip()
    except Exception:
        stored = None
    checks['operator-account equals the broker account'] = stored is not None and stored == account
    stored = None
    probe = HEALTH_PROBE % (NEW, pod['metadata']['uid'], HEALTH_KEYS)
    rc, out = run([K3S, 'kubectl', '-n', CTRL[0], 'exec', '-i', 'deploy/' + CTRL[1], '-c', 'controller', '--', 'python',
                   '-B', '-'], 90, probe.encode())  # the controller's own code (w6a2-enter.py health_once)
    try:
        lines = [line for line in out.splitlines() if line.startswith('{')]
        health = json.loads(lines[-1]) if rc == 0 and lines else {}
    except Exception:
        health = {}
    checks['runner /health names the owner, state and Pod'] = (
        health.get('probe') == 'health' and (health.get('ownerId'), health.get('stateId'), health.get('podUid'))
        == (NEW, reg['state'], pod['metadata']['uid']))
    others = [p for p in runner_pods(11) if p != 'pod/' + POD_NAME]
    checks['no other runner Pod'] = not others
    try:
        owners(11, 'end')
        checks['five owners unchanged (fences, plans)'] = True
    except Stop:
        checks['five owners unchanged (fences, plans)'] = False
    files = sorted(os.listdir(os.path.join(PROVISION_OUTPUT, NEW_HEX)))
    return checks, {k: health.get(k) for k in ('available', 'reason', 'verification')}, files


def enter():
    installs = runs('install')
    if runs('enter') or len(installs) != 1:
        raise Stop(3, 'enter needs exactly one install record and no earlier enter (one run only)')
    done = json.load(open(os.path.join(EVIDENCE, installs[0], 'run.json')))
    if done.get('result') != 0:
        raise Stop(3, 'install did not finish with rc 0')
    value, data, account = precheck(3, 'new')
    if sha_bytes(data) != done.get('inputsAfterSha256') or value['image'] != RUNNER_REF:
        raise Stop(3, 'inputs.json is not the installed file')
    if docker_image(3) != NEW_IMAGE_ID:
        raise Stop(3, 'the #68 provisioner image is not present')
    if not os.path.isfile(KUBE_TOKEN) or os.path.lexists(KUBE_TOKEN + '.r1'):
        raise Stop(3, 'kube-token missing or kube-token.r1 already exists')
    say('precheck ok: entry closed, no …5805, installed preparer (controller.sh %s, worker.py %s, image %s), '
        'inputs.json the installed file, broker account = inputs.json True'
        % (NEW_CONTROLLER_SHA[:12], NEW_WORKER_SHA[:12], NEW_IMAGE_ID[:19]))
    ev = evidence('enter')
    try:
        enter_steps(ev, account)
    except BaseException:
        if not revoke_session(ev):
            record(ev, sessionLeft=True)
            say('SESSION NOT REVOKED: session %s may stay live until it expires (at most 1 hour); tell PM'
                % SESSION['hash'][:16])
        raise
    if not revoke_session(ev):
        record(ev, sessionLeft=True, result=70)
        raise Stop(70, 'every step passed but the session revoke is not proved: session %s may stay live until it '
                       'expires (at most 1 hour); V6b is not passed; tell PM' % SESSION['hash'][:16])
    record(ev, result=0, finished=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
    say('entered: …5805 has a v1 binding on the #68 runner from the #68 preparer and a running Pod; session revoked; '
        'the Pod is left to the idle stop; next: V6-pre')


def enter_steps(ev, account):
    """Steps 2-8 of enter. The caller revokes the session on every exit."""
    record(ev, account='pending')
    row = sql('account', "WITH a AS (INSERT INTO auth.accounts(id,github_id,login,role,is_approved) VALUES "
              "('%s','%s','%s','editor',true) RETURNING id) SELECT json_build_object('id',id) FROM a;"
              % (NEW, NEW_GITHUB, NEW_LOGIN), 6, write=True)
    found = new_owner(6)
    ok = row == [{'id': NEW}] and found['account'] == {'github': NEW_GITHUB, 'login': NEW_LOGIN, 'role': 'editor',
                                                        'approved': True}
    record(ev, account='done' if ok else 'uncertain')
    if not ok:
        raise Stop(6, 'account row …5805 not proved')
    say('account: …5805 %s, editor, approved' % NEW_LOGIN)
    allowlists(OLD_CANARY, NEW, 6, ev)
    if runner_pods(6):
        raise Stop(6, 'a runner Pod appeared after the restart')
    owners(6, 'after the allowlists')
    token, csrf = create_session(ev)
    record(ev, post='pending')
    status, body = post(token, csrf)
    token = csrf = None
    record(ev, post='unknown' if status is None else status, postBody=body)
    if status is None:
        raise Stop(7, 'POST outcome unknown (timeout or connection lost); never repeated; back checks read-only')
    say('POST %s: status %d %s' % (RUNNER_PATH, status, json.dumps(body, sort_keys=True)))
    if status != 202:
        raise Stop(7, 'POST answered %d; no retry' % status)
    reg = new_owner(7)['registry'] or {}
    if (reg.get('phase'), reg.get('generation'), reg.get('pod')) != ('provisioning', 1, None):
        raise Stop(7, 'registry …5805 is not provisioning, generation 1, no Pod: %s'
                   % json.dumps({k: reg.get(k) for k in ('phase', 'generation', 'pod')}))
    record(ev, state=reg['state'])
    say('registry …5805: state %s, provisioning, generation 1, no Pod' % reg['state'])
    renew_token(ev)
    host_files(9, 'new')
    units_quiet(9, 'before the preparer')
    preparer(ev)
    pod, found = wait_pod()
    say('runner Pod %s Running, ready; registry %s' % (pod['metadata']['uid'], json.dumps(
        {k: found['registry'].get(k) for k in ('phase', 'generation', 'revision')}, sort_keys=True)))
    checks, health, files = result_checks(pod, found, account)
    account = None
    for name, ok in checks.items():
        say('check %s: %s' % (name, ok))
    say('runner health: %s; provision output files: %s' % (json.dumps(health, sort_keys=True), ' '.join(files)))
    record(ev, pod=pod['metadata']['uid'], checks=checks, health=health, outputFiles=files)
    if not all(checks.values()):
        raise Stop(11, 'a result check failed; no rerun, no undo; back checks read-only')


# prepare-again ---------------------------------------------------------------------------------------------
TOKEN_MARGIN = 3600  # the run must start at least this long before the enter token expires


def unit_failed_once(code):
    """The preparer unit after enter's one failed run: failed with Result=exit-code and no restart, or inactive;
    exactly one start this boot; the timer inactive and disabled; no runtime directory or Docker container."""
    rc, out = run([SYSTEMCTL, 'show', '-p', 'ActiveState,Result,NRestarts,UnitFileState,CanStart',
                   'issue16-controller.service'])
    props = dict(line.split('=', 1) for line in out.splitlines() if '=' in line)
    ok = rc == 0 and props.get('UnitFileState') == 'disabled' and props.get('NRestarts') == '0' and \
        props.get('CanStart') == 'yes' and ((props.get('ActiveState'), props.get('Result')) in
                                            (('failed', 'exit-code'), ('inactive', 'success')))
    _, log = run([JOURNALCTL, '-u', 'issue16-controller.service', '-b', '-o', 'cat', '--no-pager'], 60)
    starts = sum(1 for line in log.splitlines() if line.startswith('Starting Issue18 runner controller'))
    if not ok or starts != 1:
        raise Stop(code, 'the preparer unit is not the state enter left (failed once, exit-code, no restart, '
                         'disabled): %s, starts this boot %d' % (json.dumps(props, sort_keys=True), starts))
    rc, out = run([SYSTEMCTL, 'show', '-p', 'ActiveState,UnitFileState', 'issue16-controller.timer'])
    props = dict(line.split('=', 1) for line in out.splitlines() if '=' in line)
    if rc != 0 or props.get('ActiveState') != 'inactive' or props.get('UnitFileState') != 'disabled':
        raise Stop(code, 'the preparer timer is not inactive and disabled')
    if os.path.lexists(RUNTIME_DIR):
        raise Stop(code, '%s exists' % RUNTIME_DIR)
    rc, out = run([DOCKER, 'ps', '-q'])
    if rc != 0 or out:
        raise Stop(code, 'a Docker container is running')


def no_new_objects(code, state):
    """No …5805 binding, resources, lock, output, storage, PV, PVC or transport Secret for `state`."""
    found = new_owner(code)
    if found['binding'] is not None:
        raise Stop(code, '…5805 already has a binding')
    count = sql('resources', "SELECT json_build_object('n',count(*)) FROM runner_bindings.resources WHERE owner_id='%s';"
                % NEW, code)
    if count != [{'n': 0}]:
        raise Stop(code, '…5805 has binding resources')
    host_dirs(code, False)  # exactly the five owners: no …5805 lock, output or storage
    hexed = uuid.UUID(state).hex
    names = set(kubectl(['-n', RUNNER_NS, 'get', 'pvc,secret', '-o', 'name'], code, 'PVC/Secret lookup failed').split())
    names |= set(kubectl(['get', 'pv', '-o', 'name'], code, 'PV lookup failed').split())
    clash = {'persistentvolumeclaim/native-' + hexed, 'persistentvolumeclaim/control-' + hexed,
             'persistentvolume/native-' + hexed, 'persistentvolume/control-' + hexed, 'secret/transport-' + hexed} & names
    if clash:
        raise Stop(code, '…5805 objects already exist: %s' % ' '.join(sorted(clash)))


def prepare_again():
    """After enter stopped at step 6 (rc 9) and the cause was fixed outside this script: the preparer once more,
    with no session, POST or token. One run only (v6b-prepare-<UTC>)."""
    if os.geteuid() != 0:
        raise Stop(3, 'run as root')
    installs, enters = runs('install'), runs('enter')
    if runs('prepare') or len(installs) != 1 or len(enters) != 1:
        raise Stop(3, 'prepare-again needs one install, one enter and no earlier prepare-again (one run only)')
    done = json.load(open(os.path.join(EVIDENCE, installs[0], 'run.json')))
    entered = json.load(open(os.path.join(EVIDENCE, enters[0], 'run.json')))
    if done.get('result') != 0 or 'result' in entered or entered.get('preparer') != 'rc 1' or \
            entered.get('preparerResult') != 'exit-code' or entered.get('preparerLines') != [] or \
            entered.get('session') != 'revoked' or entered.get('kubeToken') != 'done' or not entered.get('state'):
        raise Stop(3, 'the records are not install rc 0 and an enter that stopped at the preparer (rc 9)')
    try:
        expiry = calendar.timegm(time.strptime(entered['kubeTokenExpiry'], '%Y-%m-%dT%H:%M:%SZ'))
    except Exception:
        raise Stop(3, 'the token expiry in the enter record is unreadable') from None
    left = expiry - time.time()
    if left < TOKEN_MARGIN:
        raise Stop(3, 'the enter token expires at %s: less than %d s left (%d s); not started; a new token needs a '
                      'new approval' % (entered['kubeTokenExpiry'], TOKEN_MARGIN, int(left)))
    token, kept = os.lstat(KUBE_TOKEN), os.lstat(KUBE_TOKEN + '.r1')
    if stat.S_IMODE(token.st_mode) != 0o600 or token.st_uid != 0 or token.st_nlink != 1 or \
            int(kept.st_mtime) != entered.get('kubeTokenOldMtime') or kept.st_nlink != 1 or \
            token.st_mtime < calendar.timegm(time.strptime(entered['started'], '%Y-%m-%dT%H:%M:%SZ')):
        raise Stop(3, 'kube-token or kube-token.r1 is not what enter left')
    host_files(3, 'new')
    value, data = inputs_now(3)
    if sha_bytes(data) != done.get('inputsAfterSha256') or value['image'] != RUNNER_REF:
        raise Stop(3, 'inputs.json is not the installed file')
    if docker_image(3) != NEW_IMAGE_ID:
        raise Stop(3, 'the #68 provisioner image is not present')
    account = broker_account(3, value)
    entry_closed(3, 'precheck', allow=NEW)  # no live session, no runner Pod, both allowlists …5805, five owners
    reg = new_owner(3)
    if reg['account'] != {'github': NEW_GITHUB, 'login': NEW_LOGIN, 'role': 'editor', 'approved': True} or \
            reg['registry'] != {'state': entered['state'], 'phase': 'provisioning', 'generation': 1, 'revision': 1,
                                'pod': None} or reg['sessions'] != 0:
        raise Stop(3, '…5805 is not the account and provisioning registry row enter left')
    no_new_objects(3, entered['state'])
    unit_failed_once(3)
    say('precheck ok: one install (rc 0) and one enter stopped at the preparer; token expires %s (%d s left); '
        'installed files, inputs.json and image as installed; entry closed (allowlists …5805, no session, no '
        'runner Pod, five owners as recorded); …5805 provisioning gen 1 rev 1 with no binding, resources, lock, '
        'output, storage, PV, PVC or Secret; preparer failed once (exit-code), timer disabled'
        % (entered['kubeTokenExpiry'], int(left)))
    ev = evidence('prepare')
    record(ev, enterRecord=enters[0])

    def gate():
        # The margin is a gate on the start itself: checked again after the whole precheck, just before it.
        now_left = expiry - time.time()
        record(ev, tokenSecondsLeftAtStart=int(now_left))
        if now_left < TOKEN_MARGIN:
            record(ev, preparer='not started (token margin)')
            raise Stop(3, 'the enter token now has %d s left (< %d s) at the start gate: the preparer was not '
                          'started; a new token needs a new approval' % (int(now_left), TOKEN_MARGIN))
        say('start gate: token %d s left (>= %d s)' % (int(now_left), TOKEN_MARGIN))

    preparer(ev, gate)
    pod, found = wait_pod()
    say('runner Pod %s Running, ready; registry %s' % (pod['metadata']['uid'], json.dumps(
        {k: found['registry'].get(k) for k in ('phase', 'generation', 'revision')}, sort_keys=True)))
    checks, health, files = result_checks(pod, found, account)
    account = None
    live = sql('sessions', "SELECT json_build_object('n',count(*)) FROM auth.sessions WHERE expires_at>now();", 11)
    checks['no live session (none was created)'] = live == [{'n': 0}]
    for name, ok in checks.items():
        say('check %s: %s' % (name, ok))
    say('runner health: %s; provision output files: %s' % (json.dumps(health, sort_keys=True), ' '.join(files)))
    record(ev, pod=pod['metadata']['uid'], checks=checks, health=health, outputFiles=files)
    if not all(checks.values()):
        raise Stop(11, 'a result check failed; no rerun, no undo; back checks read-only')
    record(ev, result=0, finished=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
    say('prepared: …5805 has a v1 binding on the #68 runner from the #68 preparer and a running Pod; no session; '
        'the Pod is left to the idle stop; next: V6-pre')


# restore ----------------------------------------------------------------------------------------------------
def restore():
    if os.geteuid() != 0:
        raise Stop(3, 'run as root')
    installs = runs('install')
    if len(installs) != 1:
        raise Stop(3, 'expected exactly one install record')
    if any(p == 'pod/' + POD_NAME for p in runner_pods(3)):
        raise Stop(3, 'a …5805 runner Pod exists: stop it through the API first')
    units_quiet(3, 'restore')
    ev = evidence('restore')
    need = False
    for spec in (API, CTRL):
        _, value, _, want, ready = deployment(spec, 3)
        if value not in (OLD_CANARY, NEW):
            raise Stop(3, '%s allowlist is neither …5804 nor …5805' % spec[1])
        need |= value == NEW or want != 1 or ready != 1  # also an enter that stopped with both at 0
    if need:
        scale(CTRL, 0, 5)
        scale(API, 0, 5)
        for spec in (API, CTRL):
            if deployment(spec, 5)[1] == NEW:
                set_allowlist(spec, NEW, OLD_CANARY, 5, ev)
        start(API, 5)
        start(CTRL, 5)
        say('allowlists: back to …5804 only, api then runner-controller restarted')
    for path, new_sha, old_sha in ((CONTROLLER_SH, NEW_CONTROLLER_SHA, OLD_CONTROLLER_SHA),
                                   (WORKER, NEW_WORKER_SHA, OLD_WORKER_SHA)):
        state = file_state(path, 5)
        if state in ((old_sha, None, None), (old_sha, None, new_sha)):
            continue  # the old file (not installed, or restored earlier)
        if state not in ((new_sha, old_sha, None), (None, old_sha, new_sha), (None, old_sha, None)):
            raise Stop(5, '%s is in no state restore accepts' % path)
        record(ev, **{os.path.basename(path): 'pending'})
        old = signal.pthread_sigmask(signal.SIG_BLOCK, SIGNALS)
        try:
            if state[0] is not None:
                move_noreplace(path, path + '.v6b', 5)  # the installed file kept
            move_noreplace(path + '.r1', path, 5)      # the old file back (same inode)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, old)
        fsync_dir(os.path.dirname(path))
        record(ev, **{os.path.basename(path): 'done'})
    before = os.path.join(EVIDENCE, installs[0], 'private', 'inputs.json.before-v6b')
    original = read_file(before, 5, 0o600)
    _, data = inputs_now(5)
    if original is not None and data != original:
        record(ev, inputs='pending')
        replace_file(INPUTS, original, 0o600, 5)
        record(ev, inputs='done' if inputs_now(5)[1] == original else 'failed')
    host_files(5, 'restored')
    record(ev, result=0, finished=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
    say('restored: allowlists …5804, old controller.sh and worker.py (installed ones kept as .v6b), inputs.json the '
        'V6a file; account row, registry row, binding, storage, image and the new kube-token left as they are')


def main(argv):
    actions = {'precheck': None, 'install': install, 'enter': enter, 'prepare-again': prepare_again,
               'restore': restore}
    if len(argv) != 2 or argv[1] not in actions:
        say('usage: v6b-preparer.py precheck|install|enter|prepare-again|restore')
        return 2
    for signum in SIGNALS:
        signal.signal(signum, interrupted)
    code = 0
    try:
        if argv[1] == 'precheck':
            state = 'new' if runs('install') else 'old'
            precheck(3, state)
            say('precheck ok (%s): entry closed, no …5805, broker account = inputs.json True, preparer inactive/'
                'disabled, controller.sh/worker.py %s' % (state, 'installed' if state == 'new' else 'the old files'))
        else:
            actions[argv[1]]()
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
