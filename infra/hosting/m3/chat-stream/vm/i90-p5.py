"""#90 P5 (chat-stream/DEV-VM.md P5), development VM only: 8 real Codex turns on the stage R baseline (plan r0
artifacts/back/issue90/p5-plan/P5-PLAN.md 9ffd2ba6…, PM decisions 2026-11-15, review plan notes 2026-11-15). Run as
root on the guest, each action other than `check` once, as a systemd unit, after review and PM's go:

    python3 -I -B /root/issue90/i90-p5.py check     # read-only: changes nothing, writes no file
    systemd-run … python3 -I -B /root/issue90/i90-p5.py s0       # flags off: 1 turn (S0 regression), runner stopped
    systemd-run … python3 -I -B /root/issue90/i90-p5.py enable   # controller image + stream env, api stream env
    systemd-run … python3 -I -B /root/issue90/i90-p5.py turns    # S1-S6: 7 turns, stops at the first False step
    systemd-run … python3 -I -B /root/issue90/i90-p5.py disable  # both templates back to enable's recorded ones

Budget (PM: 8 turns, hard cap 8, a failed turn counts, no retry; a 9th needs the user through PM). Review plan-notes
major: a turn is **reserved durably before its POST**, under the shared lock (evidence/i90-p5-budget.json, written and
read back before the send; a failed write sends nothing). Every reservation counts as a used turn whatever followed it
(2xx, an error, a timeout, a lost answer, an interrupt, a crash): the file is the count, across actions and process
deaths. The 9th reservation refuses (rc 3).

enable (review major: two Deployment patches, not one atomic change): before anything is sent, the exact pre-enable
template hashes of both Deployments (sha256 of the whole pod template, canonical JSON) and the progress marker are on
disk (record_required). Then runner-controller (container `controller`: image -> the r4 runtime digest reference
`newApiImage` of i90r-expected.json, env CODEX_RUNNER_STREAM_TEXT=true, CODEX_RUNNER_STREAM_TRACE=true), its rollout,
progress `controller`; then api (container `api`: env RESEARCH_STREAM_ENABLE=true, CODEX_RUNNER_STREAM_TEXT=true,
CODEX_RUNNER_STREAM_TRACE=true, STREAM_TRACE=true), its rollout, progress `api`. Each is one JSON patch that tests the
UID, the resourceVersion just read and the container name, applied to a local copy first (sent only when the local
result is the wanted template). A refused controller patch: rc 4, nothing changed. A refused api patch after the
controller moved: the controller is restored at once by the inverse patch and its template hash proved equal to the
recorded one (rc 4), else rc 8 (held). A template that is not the wanted one after a patch: rc 8; a rollout that does
not finish 1/1 healthy: rc 7 (held). disable restores whatever enable's progress says moved (api first, then the
controller) by inverse patches whose tests name every entry it removes, and proves both whole-template hashes equal to
the recorded pre-enable ones (`equal: True`). After enable, the PM stop table (2026-11-15; `next_step()`, printed
with every result) decides: 7 / 8 hold everything (disable refuses); 80 / 6 need a read-only proof, then disable on PM
go; 70 / 9 disable on PM go; any other nonzero or a False overall: stop, disable next; a missing or unreadable
exit.json is an unknown result (disable refuses; read-only check, back / review decide).

Turns: one browser session for …5805 (3 h: `turns` runs 7 turns; revoked in a `finally` that never raises), the P5 conversation (created by
`s0`, POST /sessions is not a turn), an explicit runner entry before the first send of an action (POST /codex/runner,
ready by the D1 rule: available true at the +65 s read) with the runner Pod env checked (`s0`: no stream key; `turns`:
RUNNER_STREAM_TEXT=true and STREAM_TRACE=true), review's measuring client (CLIENT_SHA) in throwaway guest containers
(D3's), and at the end of the action DELETE /codex/runner with the release proved (one completed stop, drain receipt
1, the Pod gone within the 20 s grace + 1 s). The runner log is followed through a filter: only `runner_stream_summary`
lines and the shutdown line reach the evidence (review: no answer body, command output or raw log in any file); the
api log is read for `api_stream_summary` lines and a count of `conversation_refresh_write` lines only. Answer text is
read from GET /sessions/{id} into memory, hashed, and never written; ids are written only as sha256(salt + id)[:16]
with a per-action random salt the client shares (--id-salt-file).

Steps (each prints `verdict <step>: True|False|unknown`; a False or unknown step ends `turns` after its own cleanup,
the remaining budget unspent; PM judges rc and verdicts together):
- S0 (flags off): research_list tool call + a short answer: 202, the turn ends idle, an assistant message and a tool
  item in GET, /events still 404 stream_disabled.
- S1: one long answer, one reader (hop ③ Traefik). V1: the hashed live item ids of the streamed items equal those of
  GET; the reconstructed agentMessage text sha256 equals GET's. Printed apart (PM): `S1 runtime:` runner_stream_summary
  legacyL / cutDelayPeakChars / holdbackPeakChars as the runner logged them, and `S1 measured:` the reader's first text
  after the send and the runner-to-reader trace delays (end to end; not character holdback, not isolated link latency
  - review timing major). DB counters before / after.
- S2: one shell command with output: a commandExecution item live and in GET, its output pieces, the delta methods.
- S3: a long answer; after 10 text deltas of this request POST …/interrupt with the requestId. Pass: the interrupt
  answered 200 interrupting (not 409) and this turn ended interrupted (turn.completed status and GET turnStatus).
  This turn completing first is a race (False) and no interrupt is sent; fewer than 10 text deltas of this request in
  300 s is unknown and no interrupt is sent (review r0 majors).
- S4 (2 turns): a long answer each, readers at hop ② (api Service) and ③ (Traefik) at once (no hop ④: the dev VM
  has no HTTP host forward, PM 2026-11-15); per hop the runner-to-reader delay distribution by trace seq, aligned by request and seq.
  DB counters.
- S5: one large command output then prose; reader A closes after 5 text deltas, reader B opens at once (snapshot then
  live). Pass: B's snapshot of this request is mid-turn (turnState running) with live text after it, B's own
  reconstruction of the agentMessage equals GET's text sha256 (review: never A and B joined), A saw the command's
  output pieces, and the runner's `outputDroppedBytes` > 0 (seq 1 20000 is above the 16 KiB per-item stream cap). No
  runner summary or no command item of this request: unknown. B joining a completed turn is a race (False). DB counters.
Every predicate reads only the frames of the turn's own request (review r0 major: a reader opened before the send
replays the previous turn's snapshot and turn.completed); a reader's text comes from its item.completed or, when it
joined a completed turn, its snapshot.end items; the send time is taken after the reservation, just before the POST.
- S6: one large command output with prose; reader P pauses after its first text (`pause-after-text`, 1024-byte receive
  buffer, 30 s) next to a normal reader N. Pass only with: P reported `reader_paused`, enough output after the pause
  started (output pieces on N after P's pause > 0), the api's `api_stream_summary` reason `stalled` for exactly one
  connection of this window, N complete (its reconstruction = GET) and the turn completed. No stall evidence: unknown.
DB counters (review question): `pg_stat_user_tables` of research.conversations read with pg_stat_clear_snapshot()
first, the 'after' read 12 s after the turn ended (a backend flushes its pending stats when idle within 10 s); other
conversations updated inside the window are counted and reported (a non-zero count makes the delta unattributable);
a zero delta is reported as observed, never as "no writes".

rc (the step rc is kept as `stepRc`; held 7 / 8 stay; otherwise, in this order: runner release not proved 6 > a
container or private files left 80 > session revoke not proved 70 > evidence 9 > the step rc): 0 ran to its end (the
verdicts decide); 3 refused before any change (precheck, guard, budget); 4 a patch refused (nothing changed, or the
controller restored and proved); 5 a step could not run (cleanup ran); 6 runner release not proved; 7 a rollout not
1/1 healthy (held); 8 a template not the wanted one after a patch (held); 9 an evidence write failed; 70 session revoke
not proved; 80 a client container or the private files left; 130 interrupted twice. The result is the last
`result rc=` line.
"""
import calendar
import fcntl
import hashlib
import http.client
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid

OWNER = '00000000-0000-4000-8000-000000005805'
OWNER_HEX = uuid.UUID(OWNER).hex
STATE = '75780e81-ef7f-4aec-a59c-ed9d71f4061a'
EXPECTED_FILE = '/root/issue90/i90r-expected.json'
EXPECTED_SHA = '65d375777fac533edf2f62d392bf69c8858ba71b627e5eb114b0b0194587d46e'  # i90r-expected.json, from sha256sum
CLIENT_FILE = '/root/issue90/p5_client.py'
CLIENT_SHA = 'e963d3986150fa3db031f5b4e32326b5206529080b8c812ceafa34edd8b51484'  # review's p5_client.py r1, from sha256sum (review 2026-11-15)
SOURCE = 'bb3aa97070f175a74eec3a0c024cd158cd64fadd'  # IMAGE_SHA of the stage R images: /api/version must name it
K3S, DOCKER = '/usr/local/bin/k3s', '/usr/bin/docker'
EVIDENCE = '/root/issue90/evidence'
LOCK = EVIDENCE + '/i90-d.lock'
BUDGET = EVIDENCE + '/i90-p5-budget.json'
TURN_CAP = 8
RUNNER_NS, POD_NAME = 'codex-runner', 'runner-' + OWNER_HEX
API = ('m2-hosting', 'api', 'fad6799a-6df5-4086-812f-cfae6212adfa', 'app=hosting-api', 'api')
CTRL = ('runner-control', 'runner-controller', '41564f26-95e7-4ad1-8af3-d73b93c4669a', 'app=runner-controller',
        'controller')
API_ENV = ({'name': 'RESEARCH_STREAM_ENABLE', 'value': 'true'}, {'name': 'CODEX_RUNNER_STREAM_TEXT', 'value': 'true'},
           {'name': 'CODEX_RUNNER_STREAM_TRACE', 'value': 'true'}, {'name': 'STREAM_TRACE', 'value': 'true'})
CTRL_ENV = ({'name': 'CODEX_RUNNER_STREAM_TEXT', 'value': 'true'}, {'name': 'CODEX_RUNNER_STREAM_TRACE', 'value': 'true'})
STREAM_NAMES = {e['name'] for e in API_ENV + CTRL_ENV}
POD_STREAM_ENV = {'RUNNER_STREAM_TEXT': 'true', 'STREAM_TRACE': 'true'}  # runner_kube.STREAM_FLAGS targets
ORIGIN, API_HOST, API_PORT, HOST_HEADER = 'http://192.88.99.1', '10.0.0.107', 80, '192.88.99.1'
HOP4 = None  # PM 2026-11-15: no HTTP host forward on the dev VM (SSH only); S4 measures hops ② and ③ only
BASE = '/api/research'
READS = (5, 35, 65)
RUNNER_GRACE = 20
SUMMARY_WAIT = 30
TURN_LIMIT = 900             # seconds a turn may run before the step gives up (the turn is not stopped by this)
STATS_IDLE = 12              # > the 10 s a PostgreSQL backend may keep pending statistics while idle
ACTIONS = ('check', 's0', 'enable', 'turns', 'disable')
PROMPTS = {
    'S0': 'Use the research_list tool once to list my research items, then reply with one short sentence saying how '
          'many items there are.',
    'S1': 'Write about 600 words in plain prose (no lists, no headings) on how a nineteenth-century lighthouse '
          "keeper's daily routine worked.",
    'S2': "Run exactly one shell command: printf 'i90-p5-s2\\n'; uname -s. Then state its output in one sentence.",
    'S3': 'Write about 1500 words in plain prose (no lists, no headings) on the history of the printing press.',
    'S4': 'Write about 1200 words in plain prose (no lists, no headings) explaining how ocean tides work.',
    'S5': 'Run exactly one shell command: seq 1 20000. Then write about 500 words in plain prose on why counting '
          'sequences are useful in programming.',
    'S6': 'Run exactly one shell command: seq 1 200000. Then write about 400 words in plain prose on number systems.',
}
TURN_STEPS = ('S1', 'S2', 'S3', 'S4a', 'S4b', 'S5', 'S6')
SESSION, OUT, STOPPING, CLIENTS, FOLLOWERS, ENTERED, PRIVATE = {}, {'stream': None}, {'requested': False}, [], [], {}, {}


class Stop(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


class Interrupted(BaseException):
    pass


def on_signal(signum, frame):
    if STOPPING['requested']:
        for each in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(each, signal.SIG_IGN)
        raise Interrupted()
    STOPPING['requested'] = True


def say(text):
    line = str(text)
    try:
        print(line, flush=True)
    except BaseException:
        pass
    if OUT['stream'] is not None and not OUT.get('failed'):
        try:  # never raised into a cleanup step; reported as rc 9
            OUT['stream'].write(line + '\n')
            OUT['stream'].flush()
            os.fsync(OUT['stream'].fileno())
        except BaseException:
            OUT['failed'] = True


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time()))


def run(cmd, timeout=60, stdin=None):
    try:
        done = subprocess.run(cmd, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=max(1, timeout), check=False)
    except subprocess.TimeoutExpired:
        return 124, ''
    except Exception:
        return 125, ''
    return done.returncode, done.stdout.decode('utf-8', 'replace').strip()


def kjson(args, code, message):
    rc, out = run([K3S, 'kubectl'] + args + ['-o', 'json'], 60)
    if rc != 0:
        raise Stop(code, message)
    try:
        return json.loads(out)
    except ValueError:
        raise Stop(code, message) from None


def sql(tag, query, code, write=False):
    text = '%s\n/*i90p5:%s*/ %s\nCOMMIT;\n' % ('BEGIN;' if write else 'BEGIN READ ONLY;', tag, query)
    rc, out = run([K3S, 'kubectl', '-n', 'm2-hosting', 'exec', '-i', 'postgres-0', '--', 'psql', '-X', '-U', 'postgres',
                   '-d', 'hosting', '-Atq', '-v', 'ON_ERROR_STOP=1', '-f', '-'], 60, text.encode())
    if rc != 0:
        raise Stop(code, 'database %s failed (%s)' % ('change' if write else 'lookup', tag))
    try:
        rows = [json.loads(line) for line in out.splitlines() if line.strip() not in ('', 'BEGIN', 'COMMIT')]
    except ValueError:
        raise Stop(code, 'database result unreadable (%s)' % tag) from None
    if not all(isinstance(row, dict) for row in rows):
        raise Stop(code, 'database result unreadable (%s)' % tag)
    return rows


def durable_json(path, value):
    temporary = '%s.%d.tmp' % (path, os.getpid())
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, sort_keys=True, indent=1)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)
    folder = os.open(os.path.dirname(path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(folder)
    finally:
        os.close(folder)


def record(ev, **items):
    try:
        path = os.path.join(ev, 'run.json')
        value = json.load(open(path)) if os.path.exists(path) else {}
        value.update(items)
        durable_json(path, value)
    except BaseException:
        OUT['failed'] = True


def record_required(ev, code, message, **items):
    """Like record, but a failed durable write (or a read-back that differs) raises Stop(code)."""
    try:
        path = os.path.join(ev, 'run.json')
        value = json.load(open(path)) if os.path.exists(path) else {}
        value.update(items)
        durable_json(path, value)
        if {k: v for k, v in json.load(open(path)).items() if k in items} != items:
            raise OSError()
    except Stop:
        raise
    except Exception:
        raise Stop(code, message) from None


def read_pinned(path, want, name):
    """The bytes of a root regular file without group/other write whose sha256 is `want` (lstat, then O_NOFOLLOW |
    O_NONBLOCK and fstat: a FIFO or symlink never blocks or is followed)."""
    if not re.fullmatch(r'[0-9a-f]{64}', want):
        raise Stop(3, '%s pin is still a placeholder' % name)
    try:
        if not os.path.isfile(path) or os.path.islink(path):
            raise OSError()
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            data = b''
            while len(data) <= 1 << 20:
                chunk = os.read(fd, 1 << 16)
                if not chunk:
                    break
                data += chunk
        finally:
            os.close(fd)
    except Exception:
        raise Stop(3, '%s missing or unreadable' % name) from None
    if not (info.st_uid == 0 and not info.st_mode & 0o022 and hashlib.sha256(data).hexdigest() == want):
        raise Stop(3, '%s is not the reviewed root file' % name)
    return data


def expected():
    return json.loads(read_pinned(EXPECTED_FILE, EXPECTED_SHA, 'i90r-expected.json'))


# Deployments: templates, patches, enable / disable ----------------------------------------------------------
def deployment(spec, code):
    return kjson(['-n', spec[0], 'get', 'deployment', spec[1]], code, '%s Deployment lookup failed' % spec[1])


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def template_sha(d):
    """The whole pod template (image and env included): the exact-restore check."""
    try:
        return canonical_sha(d['spec']['template'])
    except Exception:
        return None


def api_template_sha(d):
    """i90-switch.py's apiTemplateSha256: the api template with containers[0].image blanked."""
    try:
        template = json.loads(json.dumps(d['spec']['template']))
        if template['spec']['containers'][0].get('name') != API[4]:
            return None
        template['spec']['containers'][0]['image'] = ''
        return canonical_sha(template)
    except Exception:
        return None


def container(d, spec):
    found = [c for c in d['spec']['template']['spec']['containers'] if c.get('name') == spec[4]]
    return found[0] if len(found) == 1 else None


def index_of(d, spec):
    for i, c in enumerate(d['spec']['template']['spec']['containers']):
        if c.get('name') == spec[4]:
            return i
    return None


def stream_entries(d, spec):
    """{name: [indexes]} of the stream env names in the spec's container."""
    found = {}
    for i, e in enumerate((container(d, spec) or {}).get('env', []) or []):
        if e.get('name') in STREAM_NAMES:
            found.setdefault(e['name'], []).append(i)
    return found


def pointer(path):
    return [part.replace('~1', '/').replace('~0', '~') for part in path.split('/')[1:]]


def apply_patch(d, ops):
    """A local copy of `d` with the JSON patch applied (test / add / remove / replace), or None when a test fails."""
    doc = json.loads(json.dumps(d))
    for op in ops:
        parts = pointer(op['path'])
        parent = doc
        try:
            for part in parts[:-1]:
                parent = parent[int(part)] if isinstance(parent, list) else parent[part]
            last = parts[-1]
            # a copy: inserting the op's own object would let a later op mutate the patch itself (an 'add env: []'
            # followed by 'add env/-' changed the [] in `ops`, so a second local apply of the same ops differed)
            op = dict(op, value=json.loads(json.dumps(op['value']))) if 'value' in op else op
            if op['op'] == 'test':
                value = parent[int(last)] if isinstance(parent, list) else parent.get(last, object())
                if value != op['value']:
                    return None
            elif op['op'] == 'add':
                if isinstance(parent, list):
                    parent.append(op['value']) if last == '-' else parent.insert(int(last), op['value'])
                else:
                    parent[last] = op['value']
            elif op['op'] == 'remove':
                del parent[int(last) if isinstance(parent, list) else last]
            elif op['op'] == 'replace':
                parent[int(last) if isinstance(parent, list) else last] = op['value']
        except (KeyError, IndexError, ValueError, TypeError):
            return None
    return doc


def guards(d, spec):
    version = (d.get('metadata') or {}).get('resourceVersion')
    index = index_of(d, spec)
    if not isinstance(version, str) or not version or index is None:
        raise Stop(3, '%s has no resourceVersion or no %s container' % (spec[1], spec[4]))
    return index, [{'op': 'test', 'path': '/metadata/uid', 'value': spec[2]},
                   {'op': 'test', 'path': '/metadata/resourceVersion', 'value': version},
                   {'op': 'test', 'path': '/spec/template/spec/containers/%d/name' % index, 'value': spec[4]}]


def forward_ops(d, spec, entries, image=None, old_image=None):
    """Adds `entries` to the container env (creating env when absent) and, for the controller, replaces the image."""
    index, ops = guards(d, spec)
    path = '/spec/template/spec/containers/%d' % index
    if image is not None:
        ops += [{'op': 'test', 'path': path + '/image', 'value': old_image},
                {'op': 'replace', 'path': path + '/image', 'value': image}]
    if 'env' not in container(d, spec):
        ops.append({'op': 'add', 'path': path + '/env', 'value': []})
    ops += [{'op': 'add', 'path': path + '/env/-', 'value': e} for e in entries]
    return ops


def inverse_ops(d, spec, entries, had_env, image=None, new_image=None):
    """Removes exactly `entries` (each tested at its index, highest index first), the env list itself when enable
    created it, and puts the controller image back."""
    index, ops = guards(d, spec)
    path = '/spec/template/spec/containers/%d' % index
    env = container(d, spec).get('env', []) or []
    places = []
    for e in entries:
        found = [i for i, x in enumerate(env) if x == e]
        if len(found) != 1:
            raise Stop(3, '%s: the entry %s is not there exactly once; nothing sent' % (spec[1], e['name']))
        places.append(found[0])
    for i in sorted(places, reverse=True):
        ops += [{'op': 'test', 'path': '%s/env/%d' % (path, i), 'value': env[i]},
                {'op': 'remove', 'path': '%s/env/%d' % (path, i)}]
    if not had_env:
        ops += [{'op': 'test', 'path': path + '/env', 'value': [x for i, x in enumerate(env) if i not in places]},
                {'op': 'remove', 'path': path + '/env'}]
    if image is not None:
        ops += [{'op': 'test', 'path': path + '/image', 'value': new_image},
                {'op': 'replace', 'path': path + '/image', 'value': image}]
    return ops


def rollout_ready(spec, code, where):
    """Rollout finished, 1/1 ready with the recorded UID (api: /api/health 200 within 60 s); else Stop(code) (held)."""
    if run([K3S, 'kubectl', '-n', spec[0], 'rollout', 'status', 'deployment/' + spec[1], '--timeout=180s'], 210)[0]:
        raise Stop(code, '%s: %s rollout did not finish' % (where, spec[1]))
    d = deployment(spec, code)
    status = d.get('status') or {}
    if d['metadata']['uid'] != spec[2] or d['spec'].get('replicas') != 1 or status.get('readyReplicas') != 1:
        raise Stop(code, '%s: %s is not 1/1 ready' % (where, spec[1]))
    if spec is API:
        deadline = time.time() + 60
        while call('GET', '/api/health', session=False)[0] != 200:
            if time.time() >= deadline:
                raise Stop(code, '%s: /api/health is not 200' % where)
            time.sleep(2)
    return d


def send_patch(spec, d, ops, want_sha, where, refused_code=4):
    """Sends `ops` only when the local result has `want_sha`; after it, the live template must be that one (else 8)."""
    local = apply_patch(d, ops)
    if local is None or template_sha(local) != want_sha:
        raise Stop(3, '%s: no patch gives the wanted %s template; nothing sent' % (where, spec[1]))
    say('%s: %s patch of %d operations (tests: uid, resourceVersion, container name, the values it changes)'
        % (where, spec[1], len(ops)))
    rc, _ = run([K3S, 'kubectl', '-n', spec[0], 'patch', 'deployment', spec[1], '--type=json', '-p', json.dumps(ops)],
                60)
    after = deployment(spec, 8)
    say('%s: %s patch rc %d; template sha256 %s; wanted %s' % (where, spec[1], rc, template_sha(after), want_sha))
    if template_sha(after) == want_sha:
        return after
    if rc != 0 and template_sha(after) == template_sha(d):
        raise Stop(refused_code, '%s: %s patch refused; nothing changed' % (where, spec[1]))
    raise Stop(8, '%s: the %s template is not the wanted one after the patch (held)' % (where, spec[1]))


def enable(ev, doc):
    api, ctrl = deployment(API, 3), deployment(CTRL, 3)
    ctrl_image = doc['deployments']['runner-controller']['images']['controller']
    if container(ctrl, CTRL).get('image') != ctrl_image or container(api, API).get('image') != doc['newApiImage']:
        raise Stop(3, 'enable: the controller or api image is not the stage R one')
    if stream_entries(api, API) or stream_entries(ctrl, CTRL):
        raise Stop(3, 'enable: a stream env entry already exists')
    before = {'api': template_sha(api), 'controller': template_sha(ctrl),
              'apiHadEnv': 'env' in container(api, API), 'controllerHadEnv': 'env' in container(ctrl, CTRL),
              'controllerImage': ctrl_image, 'newControllerImage': doc['newApiImage']}
    # Review major: the exact pre-enable state and the progress are on disk before anything is sent.
    record_required(ev, 3, 'enable: the pre-enable record could not be saved; nothing sent', before=before,
                    progress='none')
    ops = forward_ops(ctrl, CTRL, CTRL_ENV, doc['newApiImage'], ctrl_image)
    local = apply_patch(ctrl, ops)
    send_patch(CTRL, ctrl, ops, template_sha(local) if local else None, 'enable')
    record_required(ev, 8, 'enable: progress not saved after the controller patch (held)', progress='controller')
    rollout_ready(CTRL, 7, 'enable')
    say('enable: runner-controller 1/1 on the r4 runtime with the two stream keys')
    api = deployment(API, 3)
    try:
        ops = forward_ops(api, API, API_ENV)
        local = apply_patch(api, ops)
        send_patch(API, api, ops, template_sha(local) if local else None, 'enable')
    except Stop as stop:
        if stop.code not in (3, 4):
            raise
        # The api did not move: restore the controller at once and prove it (review major: no assumed outcome).
        say('enable: the api patch was not applied (%s); restoring the controller' % stop.message)
        current = deployment(CTRL, 8)
        back = inverse_ops(current, CTRL, CTRL_ENV, before['controllerHadEnv'], ctrl_image, doc['newApiImage'])
        send_patch(CTRL, current, back, before['controller'], 'enable restore', refused_code=8)
        record(ev, progress='none (controller restored)')
        rollout_ready(CTRL, 7, 'enable restore')
        raise Stop(4, 'enable: the api patch was refused; the controller is restored (template equal: True)')
    record_required(ev, 8, 'enable: progress not saved after the api patch (held)', progress='api')
    rollout_ready(API, 7, 'enable')
    say('enable: api 1/1 with the stream env, /api/health 200; runner-controller and api templates %s / %s'
        % (template_sha(deployment(CTRL, 8)), template_sha(deployment(API, 8))))


def enable_record():
    """run.json of the one enable run: (before, progress) or Stop(3)."""
    runs = runs_of('enable')
    if len(runs) != 1:
        raise Stop(3, 'disable needs exactly one enable run (found %d)' % len(runs))
    try:
        value = json.load(open(os.path.join(EVIDENCE, runs[0], 'run.json')))
        before, progress = value['before'], value['progress']
    except Exception:
        raise Stop(3, 'the enable run.json is unreadable; nothing sent') from None
    return before, progress


def disable(ev, doc):
    before, progress = enable_record()
    say('disable: enable progress %s' % progress)
    if progress == 'api':
        api = deployment(API, 3)
        ops = inverse_ops(api, API, API_ENV, before['apiHadEnv'])
        send_patch(API, api, ops, before['api'], 'disable')
        rollout_ready(API, 7, 'disable')
        record_required(ev, 8, 'disable: progress not saved after the api patch (held)', restored='api')
    if progress in ('api', 'controller'):
        ctrl = deployment(CTRL, 3)
        ops = inverse_ops(ctrl, CTRL, CTRL_ENV, before['controllerHadEnv'], before['controllerImage'],
                          before['newControllerImage'])
        send_patch(CTRL, ctrl, ops, before['controller'], 'disable')
        rollout_ready(CTRL, 7, 'disable')
        record_required(ev, 8, 'disable: progress not saved after the controller patch (held)', restored='controller')
    api, ctrl = deployment(API, 8), deployment(CTRL, 8)
    equal = template_sha(api) == before['api'] and template_sha(ctrl) == before['controller']
    say('templates after disable: api %s, runner-controller %s; pre-enable api %s, runner-controller %s; equal: %s'
        % (template_sha(api), template_sha(ctrl), before['api'], before['controller'], equal))
    if not equal:
        raise Stop(8, 'disable: a template is not the pre-enable one (held)')


# HTTP and session (D1 / D3) ------------------------------------------------------------------------------------
def call(method, path, body=None, session=True):
    headers = {'Host': HOST_HEADER, 'Accept': 'application/json'}
    if session:
        headers['Cookie'] = 'anxious_session=' + SESSION['token']
    data = None
    if method != 'GET':
        headers.update({'Origin': ORIGIN, 'Idempotency-Key': str(uuid.uuid4()), 'Content-Type': 'application/json'})
        if session:
            headers['x-csrf-token'] = SESSION['csrf']
        data = json.dumps(body if body is not None else {}).encode()
    connection = http.client.HTTPConnection(API_HOST, API_PORT, timeout=60)
    try:
        connection.request(method, path, body=data, headers=headers)
        response = connection.getresponse()
        status, raw = response.status, response.read(1 << 22)
    except Exception:
        return None, None, ''
    finally:
        try:
            connection.close()
        except Exception:
            pass
    try:
        return status, json.loads(raw), ''
    except ValueError:
        return status, None, ''


def create_session(ev):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(('m2-origin-session-v1\0' + ORIGIN + '\0' + token).encode()).hexdigest()
    SESSION.update(hash=token_hash, created=None, revoked=None)
    record(ev, session='pending', sessionId=token_hash[:16])
    try:
        rows = sql('session', "WITH s AS (INSERT INTO auth.sessions(token_hash,account_id,csrf_token,expires_at) VALUES "
                   "('%s','%s','%s',now()+interval '3 hours') RETURNING token_hash) SELECT "
                   "json_build_object('id',left(token_hash,16)) FROM s;" % (token_hash, OWNER, csrf), 5, write=True)
    except Stop:
        raise Stop(5, 'session: insert failed or its result is unknown (removed below)') from None
    if len(rows) != 1 or rows[0].get('id') != token_hash[:16]:
        raise Stop(5, 'session: insert answered another row')
    SESSION.update(created=True, token=token, csrf=csrf)
    record(ev, session='created')
    say('session created: id %s, 3 hours, owner …5805' % token_hash[:16])


def revoke_session(ev):
    """Never raises (D1 r2)."""
    if not SESSION.get('hash'):
        return None
    if SESSION.get('revoked'):
        return True
    try:
        sql('revoke', "WITH d AS (DELETE FROM auth.sessions WHERE token_hash='%s' AND account_id='%s' RETURNING 1) "
            "SELECT json_build_object('deleted',count(*)) FROM d;" % (SESSION['hash'], OWNER), 70, write=True)
        left = sql('session', "SELECT json_build_object('n',count(*)) FROM auth.sessions WHERE token_hash='%s';"
                   % SESSION['hash'], 70)
        SESSION['revoked'] = left == [{'n': 0}]
    except BaseException:
        SESSION['revoked'] = False
    if ev:
        record(ev, session='revoked' if SESSION['revoked'] else 'revoke not proved (expires by itself)')
    say('session %s: %s' % (SESSION['hash'][:16], 'revoked' if SESSION['revoked'] else 'REVOKE NOT PROVED'))
    return SESSION['revoked']


# Budget (review major: reserve before the send) -----------------------------------------------------------------
def budget_read():
    if not os.path.lexists(BUDGET):
        return []
    try:
        if os.path.islink(BUDGET) or not os.path.isfile(BUDGET):
            raise OSError()
        value = json.load(open(BUDGET))
        turns = value['turns']
        if not isinstance(turns, list) or not all(isinstance(t, dict) and type(t.get('n')) is int for t in turns):
            raise ValueError()
        return turns
    except Exception:
        raise Stop(3, 'the turn budget file is unreadable; nothing sent') from None


def reserve(step):
    """Writes the reservation durably and reads it back before the send; the 9th refuses (rc 3)."""
    turns = budget_read()
    if len(turns) >= TURN_CAP:
        raise Stop(3, '%s: the turn budget is used (%d of %d); a further turn needs the user through PM'
                   % (step, len(turns), TURN_CAP))
    entry = {'n': len(turns) + 1, 'step': step, 'reservedAt': now_utc(), 'outcome': 'reserved'}
    try:
        durable_json(BUDGET, {'cap': TURN_CAP, 'turns': turns + [entry]})
        if budget_read()[-1] != entry:
            raise OSError()
    except Stop:
        raise
    except Exception:
        raise Stop(3, '%s: the reservation could not be saved; nothing sent' % step) from None
    say('%s: turn %d of %d reserved' % (step, entry['n'], TURN_CAP))
    return entry['n']


def settle(n, outcome):
    """Records what followed the send; the reservation counts whatever this write does."""
    try:
        turns = budget_read()
        turns[n - 1]['outcome'] = outcome
        durable_json(BUDGET, {'cap': TURN_CAP, 'turns': turns})
    except BaseException:
        OUT['failed'] = True


# Runner ---------------------------------------------------------------------------------------------------------
def registry():
    rows = sql('registry', "SELECT json_build_object('phase',phase,'gen',generation,'pod',pod_uid,'active',"
               "active_requests,'state',state_id) FROM runner_runtime.registry WHERE owner_id='%s';" % OWNER, 3)
    if len(rows) != 1 or rows[0]['state'] != STATE:
        raise Stop(3, '…5805 registry row missing or on another state')
    return rows[0]


def runner_pods(code):
    return kjson(['-n', RUNNER_NS, 'get', 'pods'], code, 'runner Pod lookup failed')['items']


KEEP = ('runner_stream_summary ', 'Application shutdown complete')


def follow(namespace, pod, path):
    """`kubectl logs -f --timestamps` of one Pod; only lines with a KEEP marker are written (review: no raw log)."""
    stream = open(path, 'wb')
    process = subprocess.Popen([K3S, 'kubectl', '-n', namespace, 'logs', '-f', '--timestamps', pod],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def pump():
        try:
            for line in process.stdout:
                text = line.decode('utf-8', 'replace')
                if any(marker in text for marker in KEEP):
                    stream.write(line)
                    stream.flush()
        except Exception:
            pass

    thread = threading.Thread(target=pump, daemon=True)
    thread.start()
    FOLLOWERS.append((process, stream, thread))
    return path


def stop_followers():
    for process, stream, thread in FOLLOWERS:
        try:
            process.terminate()
            process.wait(10)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
        try:
            thread.join(10)
        except Exception:
            pass
        try:
            stream.close()
        except Exception:
            pass
    del FOLLOWERS[:]


def pod_env(pod):
    runner = [c for c in pod['spec']['containers'] if c.get('name') == 'runner']
    return {e.get('name'): e.get('value') for e in (runner[0].get('env', []) if runner else [])}


def start_runner(ev, step, stream_on, doc):
    """Entry, the next generation's Pod Running and ready on the stage R runner image with the wanted stream env,
    its log followed (filtered), available true at the +65 s read (D1)."""
    before = registry()
    if (before['phase'], before['pod']) != ('idle_stopped', None) or runner_pods(5):
        raise Stop(5, '%s: …5805 is not idle_stopped with no runner Pod before the entry' % step)
    generation = before['gen'] + 1
    ENTERED['step'] = step
    status, body, _ = call('POST', BASE + '/codex/runner', {})
    say('%s entry: POST /codex/runner answered %s' % (step, status))
    if status not in (200, 202):
        raise Stop(5, '%s: entry refused or unknown (%s)' % (step, status))
    deadline, pod = time.time() + 300, None
    while time.time() < deadline and pod is None:
        state = registry()
        if state['gen'] == generation and state['pod']:
            found = [p for p in runner_pods(5) if p['metadata']['name'] == POD_NAME
                     and p['metadata']['uid'] == state['pod'] and p['status'].get('phase') == 'Running'
                     and all(c.get('ready') for c in p['status'].get('containerStatuses', []))]
            pod = found[0] if found else None
        if pod is None:
            time.sleep(2)
    if pod is None:
        raise Stop(5, '%s: no Running, ready Pod on generation %d within 300 s' % (step, generation))
    log = follow(RUNNER_NS, POD_NAME, os.path.join(ev, 'runner-%s.log' % step))
    images = [c.get('image') for c in pod['spec']['containers'] if c.get('name') == 'runner']
    env = pod_env(pod)
    keys = {k: env.get(k) for k in POD_STREAM_ENV}
    wanted = POD_STREAM_ENV if stream_on else {k: None for k in POD_STREAM_ENV}
    say('%s runner Pod %s generation %d image is stage R %s; stream env %s' % (
        step, pod['metadata']['uid'], generation, images == [doc['newImage']], json.dumps(keys, sort_keys=True)))
    if images != [doc['newImage']] or keys != wanted:
        raise Stop(5, '%s: the runner Pod is not on the stage R image with the wanted stream env' % step)
    statuses = pod['status']['containerStatuses'][0]
    started = ((statuses.get('state') or {}).get('running') or {}).get('startedAt')
    t0 = calendar.timegm(time.strptime(started, '%Y-%m-%dT%H:%M:%SZ'))
    last = None
    for offset in READS:
        time.sleep(max(0.0, t0 + offset - time.time()))
        s_status, s_body, _ = call('GET', BASE + '/codex/status')
        last = (s_status, s_body.get('available') if isinstance(s_body, dict) else None,
                s_body.get('reason') if isinstance(s_body, dict) else None)
        say('%s status read +%d s: %s available %s reason %s' % (step, offset, last[0], last[1], last[2]))
    if last != (200, True, None):
        raise Stop(5, '%s: the runner was not available at the +65 s read' % step)
    return {'generation': generation, 'pod': pod['metadata']['uid'], 'log': log}


def wait_release(runner, step, limit):
    deadline, deleted = time.time() + limit, None
    while time.time() < deadline:
        pods = [p for p in runner_pods(6) if p['metadata']['uid'] == runner['pod']]
        if pods and pods[0]['metadata'].get('deletionTimestamp') and deleted is None:
            deleted = pods[0]['metadata']['deletionTimestamp']
        state = registry()
        if not pods and state['phase'] == 'idle_stopped' and state['pod'] is None:
            return time.time(), deleted
        time.sleep(1)
    raise Stop(6, '%s: release not proved within %d s' % (step, limit))


def runner_evidence(runner, step, gone, deleted):
    stops = sql('stops', "SELECT json_build_object('receipt',receipt->'version','clean',receipt->'clean','completed',"
                "completed_at IS NOT NULL,'requested',extract(epoch FROM requested_at),'completedAt',extract(epoch FROM "
                "completed_at)) FROM runner_termination.stops WHERE owner_id='%s' AND generation=%d ORDER BY "
                "requested_at;" % (OWNER, runner['generation']), 6)
    time.sleep(2)
    stop_followers()
    try:
        logged = 'Application shutdown complete' in open(runner['log'], 'rb').read().decode('utf-8', 'replace')
    except Exception:
        logged = False
    one = stops[0] if len(stops) == 1 else {}
    within = isinstance(one.get('requested'), (int, float)) and gone - one['requested'] <= RUNNER_GRACE + 1
    say('%s runner side: stops %s; deletionTimestamp %s; gone %.1f s after the stop request (poll 1 s); '
        'log "Application shutdown complete" %s' % (step, json.dumps(stops, sort_keys=True), deleted,
                                                    gone - one['requested'] if 'requested' in one else -1, logged))
    return bool(one.get('receipt') == 1 and one.get('clean') is True and one.get('completed') is True and within
                and logged)


def stop_runner(ev, runner, step):
    status, body, _ = call('DELETE', BASE + '/codex/runner')
    say('%s DELETE /codex/runner answered %s' % (step, status))
    if status != 200 or not isinstance(body, dict) or body.get('terminationRequested') is not True:
        raise Stop(6, '%s: stop request refused or unknown (%s)' % (step, status))
    gone, deleted = wait_release(runner, step, 120)
    ENTERED.clear()
    return runner_evidence(runner, step, gone, deleted)


def released():
    state = registry()
    return (state['phase'], state['pod']) == ('idle_stopped', None) and \
        not [p for p in runner_pods(6) if p['metadata']['name'] == POD_NAME]


def ensure_stopped(step):
    """After an entry whose release was not proved: the stop and its release (120 s). None / True / False; never
    raises."""
    if not ENTERED:
        return None
    try:
        status, body, _ = call('DELETE', BASE + '/codex/runner')
        say('%s cleanup: DELETE /codex/runner answered %s' % (step, status))
        if status != 200 or not isinstance(body, dict) or body.get('terminationRequested') is not True:
            say('%s cleanup: the stop was not accepted; RUNNER RELEASE NOT PROVED (report)' % step)
            return False
        deadline = time.time() + 120
        while time.time() < deadline:
            if released():
                ENTERED.clear()
                say('%s cleanup: runner released (idle_stopped, no Pod)' % step)
                return True
            time.sleep(1)
        say('%s cleanup: RUNNER RELEASE NOT PROVED within 120 s (report)' % step)
        return False
    except BaseException:
        say('%s cleanup: the stop or its check failed; RUNNER RELEASE NOT PROVED (report)' % step)
        return False


# Measuring client (D3's containers, review's p5_client.py) ------------------------------------------------------
def prepare_private(ev, image):
    client = read_pinned(CLIENT_FILE, CLIENT_SHA, 'p5_client.py')
    rc, found = run([DOCKER, 'image', 'inspect', '--format', '{{.Id}}', image], 30)
    if not (isinstance(image, str) and re.fullmatch(r'sha256:[0-9a-f]{64}', image) and rc == 0 and found == image):
        raise Stop(5, 'the tool image is not present in guest Docker under its recorded id')
    folder = os.path.join(ev, 'private')
    os.mkdir(folder, 0o711)
    PRIVATE['folder'] = folder
    PRIVATE['salt'] = secrets.token_hex(32)
    for name, data in (('p5_client.py', client), ('cookie', ('anxious_session=' + SESSION['token']).encode()),
                       ('salt', PRIVATE['salt'].encode())):
        path = os.path.join(folder, name)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
        try:
            os.write(fd, data)
            os.fchown(fd, 10001, 10001)
        finally:
            os.close(fd)
    PRIVATE['image'] = image


def remove_private():
    folder = PRIVATE.get('folder')
    if not folder:
        return None
    try:
        if os.path.lexists(folder):
            shutil.rmtree(folder)
        gone = not os.path.lexists(folder)
    except BaseException:
        gone = False
    say('private directory (client copy, cookie, salt) %s' % ('removed' if gone else 'NOT REMOVED (report): ' + folder))
    return gone


MOUNTS = ('p5_client.py:/probe/p5_client.py', 'cookie:/run/probe-cookie', 'salt:/run/probe-salt')


def mounts():
    out = []
    for each in MOUNTS:
        out += ['-v', PRIVATE['folder'] + '/' + each + ':ro']
    return out


def probe_private():
    name = 'issue90-i90-p5-probe-%d' % os.getpid()
    rc, out = run([DOCKER, 'run', '--rm', '--name', name, '--pull=never', '--network', 'none', '--read-only',
                   '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--security-opt', 'label=disable',
                   '--user', '10001:10001'] + mounts() + ['--entrypoint', 'python3', PRIVATE['image'], '-I',
                   '-c', "import sys; [open(p, 'rb').read() for p in sys.argv[1:]]; print('readable')",
                   '/probe/p5_client.py', '/run/probe-cookie', '/run/probe-salt'], 60)
    gone = proved_gone(name)
    say('private files readable by 10001 in the client container: %s (probe rc %d)' % (out == 'readable', rc))
    if not gone:
        raise Stop(80, 'the probe container was not proved gone')
    if rc != 0 or out != 'readable':
        raise Stop(5, 'the client container cannot read its private files; nothing measured')


def present(name):
    rc, out = run([DOCKER, 'ps', '-a', '--filter', 'name=^%s$' % name, '--format', '{{.State}}'], 30)
    if rc != 0:
        return None
    return out != ''


def proved_gone(name):
    for _ in range(3):
        state = present(name)
        if state is False:
            return True
        if state is None:
            return False
        run([DOCKER, 'stop', '-t', '30', name], 60)
        run([DOCKER, 'rm', '-f', name], 30)
        time.sleep(1)
    return present(name) is False


class Client:
    """One reader: a throwaway container running review's client at one hop (`address`, `port`)."""

    def __init__(self, ev, step, label, conversation, hop, duration, extra=()):
        address, port = hop
        self.name = 'issue90-i90-p5-%s-%s-%d' % (step.lower(), label, os.getpid())
        self.path = os.path.join(ev, 'client-%s-%s.jsonl' % (step, label))
        self.label = '%s reader %s' % (step, label)
        url = 'http://%s%s%s/sessions/%s/events' % (HOST_HEADER, '' if port == 80 else ':%d' % port, BASE,
                                                     conversation)
        self.stream = open(self.path, 'wb')
        CLIENTS.append(self)
        self.process = subprocess.Popen(
            [DOCKER, 'run', '--rm', '--name', self.name, '--pull=never', '--network', 'host', '--read-only',
             '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--security-opt', 'label=disable',
             '--user', '10001:10001'] + mounts() +
            ['--entrypoint', 'python3', PRIVATE['image'], '-I', '-B', '/probe/p5_client.py', '--url', url,
             '--connect-address', address, '--cookie-file', '/run/probe-cookie', '--id-salt-file', '/run/probe-salt',
             '--duration', str(duration)] + list(extra), stdout=self.stream, stderr=subprocess.DEVNULL)

    def records(self):
        try:
            return [json.loads(line) for line in open(self.path, 'rb').read().decode().splitlines() if line.strip()]
        except Exception:
            return []

    def wait(self, predicate, limit):
        deadline = time.time() + limit
        while time.time() < deadline:
            for item in self.records():
                if predicate(item):
                    return item
            if self.process.poll() is not None:
                found = [item for item in self.records() if predicate(item)]
                return found[0] if found else None
            time.sleep(0.5)
        return None

    def status(self, limit=20):
        item = self.wait(lambda r: r.get('kind') == 'headers', limit)
        return item.get('status') if item else None

    def until(self, condition, limit):
        """Polls `condition()` (one file read per poll) until true or `limit` s; its last value."""
        deadline = time.time() + limit
        while True:
            value = condition()
            if value or time.time() >= deadline:
                return value
            time.sleep(0.5)

    def end(self, limit):
        try:
            self.process.wait(limit)
        except subprocess.TimeoutExpired:
            return None
        ended = [r for r in self.records() if r.get('kind') == 'end']
        say('%s: rc %s, end %s' % (self.label, self.process.returncode, json.dumps(
            {k: v for k, v in (ended[-1] if ended else {}).items() if k in ('reason', 'paused', 'peerEofObserved',
                                                                           'firstTextMs', 'errorType')},
            sort_keys=True)))
        return ended[-1] if ended else {}

    def frames(self, event=None):
        return [r for r in self.records() if r.get('kind') == 'frame' and (event is None or r.get('event') == event)]

    def texts(self):
        return [r for r in self.frames('item.delta') if (r.get('chars') or 0) > 0]

    def completed(self):
        """{hashed item: {textChars, textSha256, …}} from the item.completed frames (the reader's own reconstruction,
        snapshot pieces included)."""
        return {r.get('item'): r for r in self.frames('item.completed') if r.get('item')}


def clients_gone():
    ok = True
    for client in CLIENTS:
        try:
            if client.process.poll() is None:
                client.process.send_signal(signal.SIGINT)
                client.process.wait(30)
        except Exception:
            pass
        try:
            client.stream.close()
        except Exception:
            pass
        if not proved_gone(client.name):
            say('client container %s NOT PROVED GONE' % client.name)
            ok = False
    del CLIENTS[:]
    return ok


def step_end(step):
    gone = clients_gone()
    rc, out = run([DOCKER, 'ps', '-a', '--filter', 'name=^issue90-i90-p5-', '--format', '{{.Names}}'], 30)
    if not gone or rc != 0 or out:
        raise Stop(80, '%s: a client container was not proved gone' % step)


# API log evidence (lines kept as parsed facts only) --------------------------------------------------------------
def api_pod(code=5):
    items = kjson(['-n', API[0], 'get', 'pods', '-l', API[3]], code, 'api Pod lookup failed')['items']
    live = [p for p in items if not p['metadata'].get('deletionTimestamp')]
    if len(live) != 1:
        raise Stop(code, 'not exactly one live api Pod')
    return live[0]


STAMP = re.compile(r'(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})')


def instant(stamp):
    match = STAMP.fullmatch(stamp or '')
    if not match:
        return None
    parts = [int(x) for x in match.groups()[:6]]
    value = calendar.timegm((parts[0], parts[1], parts[2], parts[3], parts[4], parts[5], 0, 0, 0))
    value += float('0.' + match.group(7)) if match.group(7) else 0.0
    zone = match.group(8)
    if zone != 'Z':
        offset = int(zone[1:3]) * 3600 + int(zone[4:6]) * 60
        value -= offset if zone[0] == '+' else -offset
    return value


def api_lines(since, until, conversation):
    """(api_stream_summary payloads of `conversation` in the window, conversation_refresh_write count) from the live
    api Pod's log; nothing else of the log is kept."""
    start, end = instant(since), instant(until) + 1
    rc, out = run([K3S, 'kubectl', '-n', API[0], 'logs', '--timestamps', api_pod()['metadata']['name'],
                   '--since-time=' + since], 60)
    found, refresh = [], 0
    for line in out.splitlines() if rc == 0 else []:
        moment = instant(line.split(' ', 1)[0])
        if moment is None or moment < start or moment >= end:
            continue
        if 'conversation_refresh_write' in line:
            refresh += 1
        if 'api_stream_summary ' in line:
            try:
                payload = json.loads(line.split('api_stream_summary ', 1)[1])
            except (ValueError, IndexError):
                continue
            if isinstance(payload, dict) and payload.get('session') == conversation:
                found.append(payload)
    return found, refresh


def runner_summaries(runner, request):
    """The runner_stream_summary payloads of `request` from the filtered runner log."""
    found = []
    try:
        text = open(runner['log'], 'rb').read().decode('utf-8', 'replace')
    except Exception:
        return found
    for line in text.splitlines():
        if 'runner_stream_summary ' not in line:
            continue
        try:
            payload = json.loads(line.split('runner_stream_summary ', 1)[1])
        except (ValueError, IndexError):
            continue
        if isinstance(payload, dict) and payload.get('requestId') == request:
            found.append(payload)
    return found


# Turn machinery -------------------------------------------------------------------------------------------------
def hashed(value):
    """review P5-CLIENT.md r1: sha256(ASCII salt + UTF-8 id)[:16], the client's own formula."""
    return hashlib.sha256((PRIVATE['salt'] + value).encode()).hexdigest()[:16]


def record_of(conversation):
    status, body, _ = call('GET', BASE + '/sessions/%s' % conversation)
    if status != 200 or not isinstance(body, dict):
        raise Stop(5, 'GET of the P5 conversation answered %s' % status)
    return body


def facts_of(body):
    """The record's items reduced in memory to (hashed id, type, role, text length, text sha256, turnStatus,
    displayOnly); the text itself is dropped here."""
    out = []
    for item in body.get('items') or []:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str):
            continue
        text = item.get('text') if isinstance(item.get('text'), str) else None
        out.append({'item': hashed(item['id']), 'type': item.get('type'), 'role': item.get('role'),
                    'textChars': len(text) if text is not None else None,
                    'textSha256': hashlib.sha256(text.encode()).hexdigest() if text is not None else None,
                    'turnStatus': item.get('turnStatus'), 'displayOnly': item.get('displayOnly') is True})
    return out


def db_counters(conversation):
    rows = sql('stats', "SELECT pg_stat_clear_snapshot(); SELECT json_build_object('upd',n_tup_upd,'ins',n_tup_ins,"
               "'hot',n_tup_hot_upd) FROM pg_stat_user_tables WHERE schemaname='research' AND relname='conversations';",
               5)
    return rows[0] if len(rows) == 1 else None


def others_updated(conversation, since):
    rows = sql('others', "SELECT json_build_object('n',count(*)) FROM research.conversations WHERE id<>'%s' AND "
               "updated_at>='%s';" % (conversation, since), 5)
    return rows[0].get('n') if len(rows) == 1 else None


def send(step, conversation, prompt):
    """Reserve, then POST the message: (requestId, the time just before the POST, the time its answer came), or
    Stop(5) (the turn stays counted). review r0 major: the send time is taken after the reservation and before the
    POST, never after the answer."""
    body = record_of(conversation)
    if body.get('state') == 'running':
        raise Stop(5, '%s: the conversation is running already; nothing sent' % step)
    n = reserve(step)
    sent = time.time()
    try:
        status, answer, _ = call('POST', BASE + '/sessions/%s/messages' % conversation,
                                 {'text': prompt, 'expectedVersion': body.get('version')})
    except BaseException:
        settle(n, 'send raised')
        raise
    answered = time.time()
    request = answer.get('requestId') if status == 202 and isinstance(answer, dict) else None
    settle(n, 'accepted' if request else 'answered %s' % status)
    say('%s: POST …/messages answered %s in %.0f ms (turn %d)' % (step, status, (answered - sent) * 1000, n))
    if not request:
        raise Stop(5, '%s: the send was not accepted or its answer was lost (the turn counts)' % step)
    return request, sent, answered


def wait_done(step, conversation, limit=TURN_LIMIT):
    deadline = time.time() + limit
    while time.time() < deadline:
        body = record_of(conversation)
        if body.get('state') != 'running':
            say('%s: the turn ended: state %s' % (step, body.get('state')))
            return body
        time.sleep(2)
    raise Stop(5, '%s: the turn did not end within %d s' % (step, limit))


# Request scoping (review r0 major: a reader opened before the send replays the previous turn) --------------------
def scoped(client, request, event=None):
    """The reader's frames of `request` only, in stream order: a frame carrying a salted `request` sets the current
    one; a frame without it (snapshot.end) belongs to the last one seen. Frames before any request (runner, idle) and
    every frame of another request (the previous turn's replayed snapshot and turn.completed) are left out."""
    if client is None:
        return []
    target, current, out = hashed(request), None, []
    for r in client.records():
        if r.get('kind') != 'frame':
            continue
        if r.get('request'):
            current = r['request']
        if current == target and (event is None or r.get('event') == event):
            out.append(r)
    return out


def live_texts(client, request):
    return [r for r in scoped(client, request, 'item.delta') if (r.get('chars') or 0) > 0]


def completed_turn(client, request):
    done = scoped(client, request, 'turn.completed')
    return done[-1] if done else None


def snapshot_open(client, request):
    """A snapshot.begin of `request` whose snapshot.end has not arrived yet. review r1 major: the runner sends a
    completed snapshot's turn.completed *before* its snapshot.end (runner/stream.py snapshot_rest), and the client's
    reconstructed hashes are in that snapshot.end: the reader must not be closed, or its summary trusted, before."""
    state = None
    for r in scoped(client, request):
        if r.get('event') == 'snapshot.begin':
            state = 'open'
        elif r.get('event') == 'snapshot.end':
            state = 'closed'
    return state == 'open'


def finished(client, request):
    """This request's turn.completed seen and no snapshot of it left open."""
    return bool(completed_turn(client, request)) and not snapshot_open(client, request)


def reconstruction(client, request, item):
    """The reader's own summary of `item` for `request` (review r0 major): its item.completed, else (a reader that
    joined a completed turn gets no replayed item.completed) the item in its last snapshot.end; either only with that
    request's turn.completed seen. (summary, source) or (None, why)."""
    if completed_turn(client, request) is None:
        return None, 'no turn.completed of this request on the reader'
    if snapshot_open(client, request):
        return None, 'a snapshot of this request has no snapshot.end on the reader (incomplete, not a pass)'
    done = [r for r in scoped(client, request, 'item.completed') if r.get('item') == item]
    if done:
        return done[-1], 'item.completed'
    snaps = [r for r in scoped(client, request, 'snapshot.end') if item in (r.get('items') or {})]
    if snaps:
        return snaps[-1]['items'][item], 'snapshot.end'
    return None, 'no summary of the item on the reader'


def last_agent(facts):
    found = [f for f in facts if f['type'] == 'message' and f['role'] == 'assistant' and not f['displayOnly']]
    return found[-1] if found else None


def same_text(client, body, request):
    """The reader's reconstructed final assistant message against GET's: (equal, detail)."""
    agent = last_agent(facts_of(body))
    if agent is None:
        return False, 'no assistant message in GET'
    mine, source = reconstruction(client, request, agent['item'])
    if not mine:
        return False, source
    equal = mine.get('textSha256') == agent['textSha256'] and mine.get('textChars') == agent['textChars']
    return equal, 'reader %s chars (%s) vs GET %s chars, sha equal %s' % (
        mine.get('textChars'), source, agent['textChars'], mine.get('textSha256') == agent['textSha256'])


def first_text_ms(client, request, sent):
    texts = live_texts(client, request)
    return round(texts[0]['unixNs'] / 1e6 - sent * 1000, 1) if texts else None


def front(ev, step, client, body, request, sent):
    """Front evidence (PM): the reader's frame sequence of this request (event types, consecutive runs counted, no
    text), its first text after the send, and the final GET's item kinds and text lengths."""
    sequence = []
    for r in scoped(client, request):
        if sequence and sequence[-1][0] == r.get('event'):
            sequence[-1][1] += 1
        else:
            sequence.append([r.get('event'), 1])
    entry = {'step': step, 'frames': sequence, 'firstTextAfterSendMs': first_text_ms(client, request, sent),
             'items': [{k: f[k] for k in ('type', 'role', 'textChars', 'turnStatus', 'displayOnly')}
                       for f in facts_of(body)]}
    path = os.path.join(ev, 'front-evidence.json')
    try:
        value = json.load(open(path)) if os.path.exists(path) else {'steps': []}
        value['steps'].append(entry)
        durable_json(path, value)
    except BaseException:
        OUT['failed'] = True


def delays(client, sent):
    """Runner-to-reader delays (ms) of the trace lines received after the send: end to end (path, queue, parse and
    clock offset), not link latency (review timing major)."""
    values = [r['unixNs'] / 1e6 - r['runnerUnixMs'] for r in client.records()
              if r.get('kind') == 'trace' and isinstance(r.get('runnerUnixMs'), (int, float))
              and isinstance(r.get('unixNs'), int) and r['unixNs'] / 1e9 >= sent]
    values.sort()
    if not values:
        return {'n': 0}
    return {'n': len(values), 'min': round(values[0], 1), 'median': round(values[len(values) // 2], 1),
            'p95': round(values[min(len(values) - 1, int(len(values) * 0.95))], 1), 'max': round(values[-1], 1)}


def service_hop():
    items = kjson(['-n', API[0], 'get', 'services'], 5, 'api Service lookup failed')['items']
    found = [s for s in items if (s.get('spec') or {}).get('selector') == {'app': 'hosting-api'}]
    if len(found) != 1 or not found[0]['spec'].get('clusterIP') or not found[0]['spec'].get('ports'):
        raise Stop(5, 'not exactly one api Service with a cluster IP')
    return found[0]['spec']['clusterIP'], int(found[0]['spec']['ports'][0]['port'])


def turn(ev, step, conversation, runner, prompt, readers, db=False, during=None):
    """One turn: readers opened (headers 200), DB counters, the send, `during` (the step's action while it runs), the
    end, the readers' end, the api / runner summaries of the window, DB counters after. Returns the facts."""
    since = now_utc()
    clients = {}
    for label, hop, extra in readers:
        clients[label] = Client(ev, step, label, conversation, hop, TURN_LIMIT, extra)
        if clients[label].status() != 200:
            raise Stop(5, '%s: reader %s did not open (%s)' % (step, label, clients[label].status(1)))
    before = db_counters(conversation) if db else None
    request, sent, answered = send(step, conversation, prompt)
    extra = during(request, clients) if during else None
    body = wait_done(step, conversation)
    for client in clients.values():
        # this request's turn.completed AND any snapshot of it ended (its snapshot.end follows the turn.completed)
        client.until(lambda c=client: finished(c, request) or c.process.poll() is not None, 60)
        client.process.send_signal(signal.SIGINT)  # the client writes its end record (rc 130); SIGTERM leaves none
        client.end(30)
    after, others = None, None
    if db:
        time.sleep(STATS_IDLE)
        after = db_counters(conversation)
        others = others_updated(conversation, since)
    until = now_utc()
    time.sleep(2)
    api, refresh = api_lines(since, until, conversation)
    summary = runner_summaries(runner, request)
    facts = {'request': request, 'sent': sent, 'answered': answered, 'body': body, 'clients': clients, 'api': api,
             'refresh': refresh, 'runner': summary, 'extra': extra}
    say('%s: api_stream_summary %d line(s), reasons %s; conversation_refresh_write %d; runner_stream_summary %d'
        % (step, len(api), json.dumps(sorted(str(p.get('reason')) for p in api)), refresh, len(summary)))
    if db:
        delta = None if not (before and after) else {k: after[k] - before[k] for k in before}
        say('%s DB: research.conversations counters before %s after %s delta %s (read %d s after the end, snapshot '
            'cleared); other conversations updated in the window %s%s' % (
                step, json.dumps(before), json.dumps(after), json.dumps(delta), STATS_IDLE, others,
                '' if others == 0 else ' - the delta is not attributable to this turn alone'))
        facts['db'] = {'delta': delta, 'others': others}
    front(ev, step, list(clients.values())[0] if clients else None, body, request, sent)
    step_end(step)
    return facts


def ended_ok(body, status='idle'):
    return body.get('state') == status


# Steps ----------------------------------------------------------------------------------------------------------
def step_s0(ev, conversation, runner, verdicts):
    n_before = len(budget_read())
    request, sent, _ = send('S0', conversation, PROMPTS['S0'])
    body = wait_done('S0', conversation)
    facts = facts_of(body)
    kinds = sorted({str(f['type']) for f in facts})
    status, answer, _ = call('GET', BASE + '/sessions/%s/events' % conversation)
    code = ((answer or {}).get('error') or {}).get('code') if isinstance(answer, dict) else None
    tool = [f for f in facts if f['type'] not in ('message', 'notice')]
    say('S0: items %d, kinds %s, assistant message %s, tool item %s; events %s %s' % (
        len(facts), kinds, last_agent(facts) is not None, bool(tool), status, code))
    front(ev, 'S0', None, body, request, sent)
    verdicts['S0'] = bool(ended_ok(body) and last_agent(facts) and tool and status == 404 and code == 'stream_disabled'
                          and len(budget_read()) == n_before + 1)


def hop3():
    return API_HOST, API_PORT


def step_s1(ev, conversation, runner, verdicts):
    f = turn(ev, 'S1', conversation, runner, PROMPTS['S1'], [('hop3', hop3(), ())], db=True)
    client, request = f['clients']['hop3'], f['request']
    live = {r.get('item') for r in scoped(client, request, 'item.started') if r.get('item')}
    stored = {x['item'] for x in facts_of(f['body']) if x['type'] != 'message' or x['role'] == 'assistant'}
    equal, detail = same_text(client, f['body'], request)
    summary = f['runner'][-1] if f['runner'] else {}
    say('S1 V1: live streamed item ids of this request %d, all in GET %s; final text: %s' % (
        len(live), live <= stored, detail))
    say('S1 runtime: runner_stream_summary %s' % json.dumps(
        {k: summary.get(k) for k in ('legacyL', 'cutDelayPeakChars', 'holdbackPeakChars', 'text', 'stopped',
                                     'discarded', 'items', 'samples', 'firstDeltaMs', 'firstEventMs')
         if k in summary}, sort_keys=True))
    say('S1 measured: first text %s ms after the send (send = just before the POST, which answered after %.0f ms; '
        'includes entry, subscription and production; not holdback); runner-to-reader delays at hop ③ %s' % (
            first_text_ms(client, request, f['sent']), (f['answered'] - f['sent']) * 1000,
            json.dumps(delays(client, f['sent']), sort_keys=True)))
    verdicts['S1'] = bool(ended_ok(f['body']) and live and live <= stored and equal
                          and type(summary.get('legacyL')) is int)


def step_s2(ev, conversation, runner, verdicts):
    f = turn(ev, 'S2', conversation, runner, PROMPTS['S2'], [('hop3', hop3(), ())])
    client, request = f['clients']['hop3'], f['request']
    commands = [r for r in scoped(client, request, 'item.started') if r.get('itemKind') == 'commandExecution']
    outputs = [r for r in scoped(client, request, 'item.output') if (r.get('chars') or 0) > 0]
    methods = sorted({str(r.get('method')) for r in scoped(client, request, 'item.delta') if r.get('method')})
    stored = {x['item'] for x in facts_of(f['body'])}
    summary = f['runner'][-1] if f['runner'] else {}
    say('S2: commandExecution items of this request %d (in GET %s); output pieces %d; SSE delta methods %s; item '
        'kinds %s; runner deltaIn %s' % (
            len(commands), all(r.get('item') in stored for r in commands), len(outputs), methods,
            sorted({str(r.get('itemKind')) for r in scoped(client, request, 'item.started')}),
            json.dumps(summary.get('deltaIn'), sort_keys=True)))
    verdicts['S2'] = bool(ended_ok(f['body']) and commands and all(r.get('item') in stored for r in commands)
                          and outputs)


INTERRUPT_AFTER = 10


def step_s3(ev, conversation, runner, verdicts):
    state = {}

    def interrupt(request, clients):
        """review r0 majors: only this request's text and completion count; no text in time or this turn already
        completed means no interrupt is sent (unknown / race), never a blind POST."""
        reader = clients['hop3']
        reader.until(lambda: len(live_texts(reader, request)) >= INTERRUPT_AFTER or completed_turn(reader, request),
                     300)
        texts, done = len(live_texts(reader, request)), completed_turn(reader, request)
        state.update(deltas=texts)
        if done:
            state['outcome'] = 'race: this turn completed before %d text deltas; no interrupt sent' % INTERRUPT_AFTER
        elif texts < INTERRUPT_AFTER:
            state['outcome'] = 'no %d text deltas of this turn within 300 s; no interrupt sent' % INTERRUPT_AFTER
        else:
            status, answer, _ = call('POST', BASE + '/sessions/%s/interrupt' % conversation, {'requestId': request})
            state.update(status=status, state=(answer or {}).get('state') if isinstance(answer, dict) else None,
                         completedBefore=bool(completed_turn(reader, request)))
            state['outcome'] = 'sent'
        say('S3: %s (text deltas of this turn %d)%s' % (
            state['outcome'], texts, '; interrupt answered %s %s' % (state.get('status'), state.get('state'))
            if state['outcome'] == 'sent' else ''))
        return state

    f = turn(ev, 'S3', conversation, runner, PROMPTS['S3'], [('hop3', hop3(), ())], during=interrupt)
    client, request = f['clients']['hop3'], f['request']
    done = completed_turn(client, request)
    status = done.get('status') if done else None
    facts = facts_of(f['body'])
    marked = [x for x in facts if x['turnStatus'] == 'interrupted']
    partial = [x for x in marked if x['displayOnly']]
    say('S3: turn.completed status of this turn %s; GET items marked interrupted %d (display-only partial %d); '
        'state %s' % (status, len(marked), len(partial), f['body'].get('state')))
    if state.get('outcome', '').startswith('no '):
        verdicts['S3'] = None  # the experiment did not happen: unknown, no extra turn
        return
    race = state.get('outcome', '').startswith('race') or state.get('status') == 409
    if race:
        say('S3: reported as a race (the turn completed first); not relabelled interrupted')
    verdicts['S3'] = bool(not race and state.get('status') == 200 and state.get('state') == 'interrupting'
                          and status == 'interrupted' and marked)


def step_s4(ev, conversation, runner, verdicts, name):
    readers = [('hop2', service_hop(), ()), ('hop3', hop3(), ())]
    if HOP4 is not None:
        readers.append(('hop4', HOP4, ()))
    f = turn(ev, name, conversation, runner, PROMPTS['S4'], readers, db=(name == 'S4a'))
    found, texts = {}, {}
    for label, client in f['clients'].items():
        found[label] = delays(client, f['sent'])
        texts[label] = same_text(client, f['body'], f['request'])
        say('%s %s: runner-to-reader delays %s; final text %s' % (name, label, json.dumps(found[label],
                                                                                        sort_keys=True),
                                                                  texts[label][1]))
    clock = run(['/usr/bin/chronyc', 'tracking'], 10)[1].splitlines()
    say('%s guest clock: %s' % (name, '; '.join(x for x in clock if 'System time' in x or 'Last offset' in x)
                                or 'chronyc tracking unavailable'))
    verdicts[name] = bool(ended_ok(f['body']) and all(v['n'] > 0 for v in found.values())
                          and all(equal for equal, _ in texts.values()))


OUTPUT_ITEM_LIMIT = 16 * 1024   # runner/stream.py: command output streamed per item (S5 expects the cap to apply)
OUTPUT_TURN_LIMIT = 128 * 1024  # runner/stream.py: command output streamed per turn


def step_s5(ev, conversation, runner, verdicts):
    state = {}

    def reload(request, clients):
        first = clients['a']
        first.until(lambda: first.process.poll() is not None or completed_turn(first, request), 600)
        second = Client(ev, 'S5', 'b', conversation, hop3(), TURN_LIMIT, ())
        clients['b'] = second
        state['bStatus'] = second.status()
        say('S5: reader a ended %s; reader b opened %s' % (
            json.dumps([r.get('reason') for r in first.records() if r.get('kind') == 'end']), state['bStatus']))
        return state

    f = turn(ev, 'S5', conversation, runner, PROMPTS['S5'], [('a', hop3(), ('--close-after-text', '5'))],
             db=True, during=reload)
    a, b, request = f['clients']['a'], f['clients'].get('b'), f['request']
    a_end = [r.get('reason') for r in a.records() if r.get('kind') == 'end']
    begin = scoped(b, request, 'snapshot.begin')
    mid_turn = bool(begin) and begin[-1].get('turnState') == 'running'
    continued = [r for r in live_texts(b, request) if begin and r['unixNs'] > begin[-1]['unixNs']]
    equal, detail = same_text(b, f['body'], request) if b else (False, 'no reader b')
    commands = {r.get('item') for r in scoped(a, request, 'item.started') if r.get('itemKind') == 'commandExecution'}
    output = [r for r in scoped(a, request, 'item.output') if r.get('item') in commands]
    per_item = {}
    for r in output:
        per_item[r.get('item')] = per_item.get(r.get('item'), 0) + (r.get('chars') or 0)
    out_chars = sum(per_item.values())
    summary = f['runner'][-1] if f['runner'] else None
    dropped = summary.get('outputDroppedBytes') if isinstance(summary, dict) else None
    raw = (((summary or {}).get('deltaIn') or {}).get('commandExecution') or {}).get('bytes')
    # review r1 major: the cap is enforced, not inferred from "something was dropped". A Unicode code point is at least
    # one UTF-8 byte, so streamed chars above a byte limit are a certain overflow; chars at or under it are necessary,
    # not sufficient (no per-item byte count is in the client's or the runner's evidence; seq prints ASCII only).
    over_item = {k: v for k, v in per_item.items() if v > OUTPUT_ITEM_LIMIT}
    over_turn = out_chars > OUTPUT_TURN_LIMIT
    say('S5: reader a end %s; command items %d, output pieces %d, output chars per item %s (cap %d bytes per item, %d '
        'per turn; chars <= cap is necessary, not a byte-exact proof); runner outputDroppedBytes %s, deltaIn '
        'commandExecution bytes %s, eventsOut %s, eventBytes %s' % (
            a_end, len(commands), len(output), json.dumps(sorted(per_item.values())), OUTPUT_ITEM_LIMIT,
            OUTPUT_TURN_LIMIT, dropped, raw, (summary or {}).get('eventsOut'), (summary or {}).get('eventBytes')))
    say('S5: reader b snapshot of this turn %d (turnState %s: mid-turn %s), live text after it %d; b final text %s' % (
        len(begin), begin[-1].get('turnState') if begin else None, mid_turn, len(continued), detail))
    if over_item or over_turn:
        say('S5: CAP EXCEEDED on the reader (items over %d chars: %d; turn total %d chars): False' % (
            OUTPUT_ITEM_LIMIT, len(over_item), out_chars))
        verdicts['S5'] = False
        return
    if type(dropped) is not int or type(raw) is not int or not commands:
        say('S5: required cap evidence missing (runner outputDroppedBytes / deltaIn commandExecution bytes) or no '
            'command item of this turn: unknown, no extra turn')
        verdicts['S5'] = None
        return
    if begin and not mid_turn:
        say('S5: reader b joined a completed turn (race); not counted as a mid-turn reload')
    verdicts['S5'] = bool(ended_ok(f['body']) and a_end == ['local_close'] and state.get('bStatus') == 200
                          and mid_turn and continued and equal and output and dropped > 0
                          and raw > OUTPUT_ITEM_LIMIT)


def step_s6(ev, conversation, runner, verdicts):
    readers = [('n', hop3(), ()), ('p', hop3(), ('--mode', 'pause-after-text', '--pause', '30',
                                                 '--receive-buffer', '1024'))]
    f = turn(ev, 'S6', conversation, runner, PROMPTS['S6'], readers)
    normal, paused, request = f['clients']['n'], f['clients']['p'], f['request']
    ready = [r for r in paused.records() if r.get('kind') == 'reader_paused']
    after = [r for r in scoped(normal, request) if r.get('event') in ('item.output', 'item.delta')
             and ready and r['unixNs'] > ready[0]['unixNs']]
    stalled = [p for p in f['api'] if p.get('reason') == 'stalled']
    equal, detail = same_text(normal, f['body'], request)
    say('S6: paused reader ready %s; pieces of this turn on the normal reader after the pause %d; api summaries %d, '
        'stalled %d; normal reader final text %s' % (bool(ready), len(after), len(f['api']), len(stalled), detail))
    if not stalled:
        say('S6: no server-side stall evidence (api_stream_summary reason stalled): unknown, no extra turn')
        verdicts['S6'] = None
        return
    verdicts['S6'] = bool(ended_ok(f['body']) and ready and after and len(stalled) == 1 and equal)


# Actions --------------------------------------------------------------------------------------------------------
def runs_of(action):
    return sorted(x for x in os.listdir(EVIDENCE) if re.fullmatch(r'i90-p5-%s-\d{8}T\d{6}Z' % re.escape(action), x))


def exit_of(name):
    try:
        done = json.load(open(os.path.join(EVIDENCE, name, 'exit.json')))
    except Exception:
        return None
    if not isinstance(done, dict) or type(done.get('rc')) is not int:
        return None
    verdicts = done.get('verdicts', {})
    if not isinstance(verdicts, dict) or not all(isinstance(k, str) and (v is True or v is False or v is None)
                                                 for k, v in verdicts.items()):
        return None
    return done


def conversation_of_s0():
    runs = runs_of('s0')
    try:
        value = json.load(open(os.path.join(EVIDENCE, runs[0], 'run.json'))) if len(runs) == 1 else {}
    except Exception:
        value = {}
    conversation = value.get('conversation')
    if not (isinstance(conversation, str) and re.fullmatch(r'[0-9a-f-]{36}', conversation)):
        raise Stop(3, 'turns needs the conversation recorded by the one s0 run')
    return conversation


def held():
    """A held state (rc 7 / 8) of any earlier p5 action."""
    for action in ACTIONS[1:]:
        for name in runs_of(action):
            done = exit_of(name)
            if done is None or done['rc'] in (7, 8):
                return name
    return None


def guard(action):
    if action != 'check' and runs_of(action):
        raise Stop(3, 'i90-p5 %s already ran; a second run is a PM decision' % action)
    if action == 'enable':
        done = [exit_of(n) for n in runs_of('s0')]
        if len(done) != 1 or not done[0] or done[0]['rc'] != 0 or done[0].get('verdicts', {}).get('S0') is not True:
            raise Stop(3, 'enable needs exactly one s0 that ended rc 0 with S0 True')
    if action == 'turns':
        done = [exit_of(n) for n in runs_of('enable')]
        if len(done) != 1 or not done[0] or done[0]['rc'] != 0:
            raise Stop(3, 'turns needs exactly one enable that ended rc 0')
        if runs_of('disable'):
            raise Stop(3, 'turns after disable is refused')
    if action == 'disable':
        if len(runs_of('enable')) != 1:
            raise Stop(3, 'disable needs exactly one enable run')
        name = held()
        if name:
            raise Stop(3, 'a held state (rc 7 / 8, or an unreadable exit) of %s: back and review decide; nothing sent'
                       % name)


def precheck(doc, action):
    api, ctrl = deployment(API, 3), deployment(CTRL, 3)
    ready = all(d['metadata']['uid'] == spec[2] and (d.get('status') or {}).get('readyReplicas') == 1
                and d['spec'].get('replicas') == 1 for d, spec in ((api, API), (ctrl, CTRL)))
    api_image = (container(api, API) or {}).get('image')
    ctrl_image = (container(ctrl, CTRL) or {}).get('image')
    say('api: recorded and 1/1 %s; image stage R %s; template = stage R %s; stream env %s' % (
        ready, api_image == doc['newApiImage'], api_template_sha(api) == doc['apiTemplateSha256'],
        json.dumps(sorted(stream_entries(api, API)))))
    say('runner-controller: image %s; stream env %s' % (
        'stage R (pre-#90)' if ctrl_image == doc['deployments']['runner-controller']['images']['controller'] else
        'the r4 runtime' if ctrl_image == doc['newApiImage'] else 'OTHER', json.dumps(sorted(stream_entries(ctrl,
                                                                                                         CTRL)))))
    state, pods = registry(), runner_pods(3)
    live = sql('sessions', "SELECT json_build_object('n',count(*)) FROM auth.sessions WHERE expires_at>now();", 3)[0]
    used = budget_read()
    say('…5805 registry %s; runner Pods %d; live sessions %s; turns used %d of %d' % (
        json.dumps(state, sort_keys=True), len(pods), live['n'], len(used), TURN_CAP))
    status, body, _ = call('GET', '/api/version', session=False)
    version = body.get('sha') if isinstance(body, dict) else None
    say('/api/version %s %s' % (status, version))
    quiet = (state['phase'], state['pod']) == ('idle_stopped', None) and not pods and live['n'] == 0
    if action == 'disable':
        if not ready:
            raise Stop(3, 'disable: a Deployment is not the recorded 1/1 one')
        return
    if not (ready and quiet and version == SOURCE and api_image == doc['newApiImage']):
        raise Stop(3, 'not the stage R baseline (1/1 recorded Deployments, …5805 idle with no Pod, no live session, '
                      'version, api image)')
    flags = bool(stream_entries(api, API) or stream_entries(ctrl, CTRL))
    if action in ('check', 's0', 'enable'):
        if flags or api_template_sha(api) != doc['apiTemplateSha256'] or \
                ctrl_image != doc['deployments']['runner-controller']['images']['controller']:
            raise Stop(3, '%s needs the stage R templates with no stream env' % action)
    if action == 'turns':
        want_api = all(stream_entries(api, API).get(e['name']) for e in API_ENV)
        want_ctrl = all(stream_entries(ctrl, CTRL).get(e['name']) for e in CTRL_ENV) and ctrl_image == doc['newApiImage']
        if not (want_api and want_ctrl):
            raise Stop(3, 'turns needs the enabled state (api and controller stream env, controller on r4)')


def take_lock():
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise Stop(3, 'another i90 action holds the lock; nothing done') from None
    return fd


def measure(ev, doc, action, verdicts):
    create_session(ev)
    if action == 's0':
        status, body, _ = call('POST', BASE + '/sessions', {'title': 'i90 P5 %s' % now_utc()})
        conversation = body.get('id') if status == 201 and isinstance(body, dict) else None
        if not conversation:
            raise Stop(5, 'the P5 conversation was not created (%s); no turn sent' % status)
        record_required(ev, 5, 'the conversation id could not be saved; no turn sent', conversation=conversation)
        say('P5 conversation %s created (not a turn)' % conversation[:8])
    else:
        conversation = conversation_of_s0()
        say('P5 conversation %s (from s0)' % conversation[:8])
    if action == 'turns':
        prepare_private(ev, doc['toolImage'])
        probe_private()
    else:
        PRIVATE['salt'] = secrets.token_hex(32)  # s0 has no reader: ids are hashed for its facts only
    runner = start_runner(ev, action, action == 'turns', doc)
    if action == 's0':
        steps = (('S0', lambda: step_s0(ev, conversation, runner, verdicts)),)
    else:
        steps = (('S1', lambda: step_s1(ev, conversation, runner, verdicts)),
                 ('S2', lambda: step_s2(ev, conversation, runner, verdicts)),
                 ('S3', lambda: step_s3(ev, conversation, runner, verdicts)),
                 ('S4a', lambda: step_s4(ev, conversation, runner, verdicts, 'S4a')),
                 ('S4b', lambda: step_s4(ev, conversation, runner, verdicts, 'S4b')),
                 ('S5', lambda: step_s5(ev, conversation, runner, verdicts)),
                 ('S6', lambda: step_s6(ev, conversation, runner, verdicts)))
    stopped_at = None
    for name, step in steps:
        time.sleep(1.1)
        say('--- %s %s' % (name, now_utc()))
        step()
        if verdicts.get(name) is not True:
            say('stopped after %s (verdict %s): the remaining steps are not run, their turns unspent' % (
                name, {False: 'False'}.get(verdicts.get(name), 'unknown')))
            stopped_at = name
            break
    verdicts['release'] = stop_runner(ev, runner, action)
    return stopped_at


def cleanup(action):
    try:
        runner = ensure_stopped(action)
    except BaseException:
        runner = False
    try:
        gone = clients_gone()
        rc, out = run([DOCKER, 'ps', '-a', '--filter', 'name=^issue90-i90-p5-', '--format', '{{.Names}}'], 30)
        gone = gone and rc == 0 and not out
    except BaseException:
        gone = False
    try:
        stop_followers()
    except BaseException:
        pass
    try:
        private = remove_private()
    except BaseException:
        private = False
    return runner, gone, private


def main(argv):
    if len(argv) != 2 or argv[1] not in ACTIONS:
        print('usage: i90-p5.py %s' % '|'.join(ACTIONS))
        return 2
    action = argv[1]
    if os.geteuid() != 0:
        print('not root; nothing done\nresult rc=3')
        return 3
    SESSION.clear(); ENTERED.clear(); PRIVATE.clear(); del CLIENTS[:]; del FOLLOWERS[:]
    OUT.clear(); OUT['stream'] = None; STOPPING['requested'] = False
    if action == 'check':
        try:
            precheck(expected(), 'check')
            guard('check')
        except Stop as stop:
            say('STOPPED (%d): %s' % (stop.code, stop.message))
            say('result rc=%d' % stop.code)
            return stop.code
        say('result rc=0 (read-only)')
        return 0
    for each in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(each, on_signal)
    ev, code, verdicts, revoked, lock, stopped_at = None, 0, {}, None, None, None
    runner, gone, private = None, True, None
    try:
        try:
            doc = expected()
            lock = take_lock()
            guard(action)
            precheck(doc, action)
            ev = os.path.join(EVIDENCE, 'i90-p5-%s-%s' % (action, time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())))
            os.mkdir(ev, 0o700)
            fd = os.open(os.path.join(ev, 'out.txt'), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            OUT['stream'] = os.fdopen(fd, 'w')
            record(ev, action=action, started=now_utc(), expectedSha256=EXPECTED_SHA, clientSha256=CLIENT_SHA)
            say('evidence: %s' % ev)
            if action == 'enable':
                enable(ev, doc)
            elif action == 'disable':
                disable(ev, doc)
            else:
                stopped_at = measure(ev, doc, action, verdicts)
        except Stop as stop:
            code = stop.code
            say('STOPPED (%d): %s' % (stop.code, stop.message))
        except Interrupted:
            code = 130
            say('STOPPED (130): interrupted twice')
        except Exception as error:
            code = 5 if ev else 3
            say('STOPPED (%d): unexpected %s' % (code, type(error).__name__))
    finally:
        runner, gone, private = cleanup(action)
        revoked = revoke_session(ev)
        if lock is not None:
            os.close(lock)
    step_rc = code
    facts = {'runner': runner, 'gone': gone, 'private': private, 'revoked': revoked}
    code = final_rc(step_rc, facts, OUT.get('failed'))
    say('cleanup: runner released %s; client containers gone %s; private files removed %s; session revoked %s'
        % ({None: 'n/a'}.get(runner, runner), gone, {None: 'n/a'}.get(private, private),
           {None: 'n/a'}.get(revoked, revoked)))
    items = ('S0',) if action == 's0' else TURN_STEPS if action == 'turns' else ()
    for name in items:
        say('verdict %s: %s' % (name, {True: 'True', False: 'False'}.get(verdicts.get(name), 'unknown')))
    if items:
        say('verdict release: %s' % {True: 'True', False: 'False'}.get(verdicts.get('release'), 'unknown'))
        failing = [n for n in items + ('release',) if verdicts.get(n) is not True]
        if stopped_at:
            say('stopped at: %s' % stopped_at)
        if failing:
            say('verdicts False or unknown: %s' % ', '.join(failing))
    try:
        say('turns used %d of %d' % (len(budget_read()), TURN_CAP))
    except Stop:
        say('turns used: THE BUDGET FILE IS UNREADABLE (report)')
    code = final_rc(step_rc, facts, OUT.get('failed'))
    passed = not [n for n in items + ('release',) if verdicts.get(n) is not True] and code == 0
    if items:
        say('verdict overall pass: %s' % passed)
    say('next (PM stop table 2026-11-15): %s' % next_step(action, code, passed if items else code == 0))
    say('result rc=%d' % code)
    written = False
    if ev:
        try:
            durable_json(os.path.join(ev, 'exit.json'), {'rc': final_rc(step_rc, facts, OUT.get('failed')),
                                                        'stepRc': step_rc, 'verdicts': verdicts,
                                                        'stoppedAt': stopped_at, 'runnerReleased': runner,
                                                        'clientsGone': gone, 'privateRemoved': private,
                                                        'sessionRevoked': revoked, 'finished': now_utc()})
            written = True
        except BaseException:
            say('exit.json not written: the result is unknown on disk')
    final = final_rc(step_rc, facts, OUT.get('failed') or (ev is not None and not written))
    if final != code:
        say('result rc=%d (an evidence write failed after the result line)' % final)
    return final


NEXT = ('s0', 'enable', 'turns', 'disable')


def next_step(action, code, passed):
    """The PM stop table (2026-11-15, review-accepted) for this action's result: one rule, printed with every result.
    A result missing from disk (no exit.json) is not covered here: unknown, read-only check, back / review decide."""
    enabled = action in ('enable', 'turns')
    if code in (7, 8):
        return 'HELD: report at once; nothing else is run, disable included; back and review decide'
    if code == 80:
        return ('report at once; no further turn or measurement; prove read-only that no client container or private '
                'file is left, then %s' % ('disable on PM go' if enabled else 'PM decides'))
    if code == 6:
        return ('report at once; prove the runner release read-only, then %s'
                % ('disable on PM go' if enabled else 'PM decides'))
    if code == 70:
        return ('report; the session expires 3 hours after it was created (expiry is not proof of a revoke); %s'
                % ('disable on PM go' if enabled else 'PM decides'))
    if code == 9:
        return 'report; %s' % ('disable on PM go' if enabled else 'PM decides')
    if code != 0 or not passed:
        return ('stop here, the remaining turns unspent; %s' % ('disable next' if enabled else 'report'))
    if action == 'disable':
        return 'P5 complete: report the results'
    return 'the next action: %s' % NEXT[NEXT.index(action) + 1] if action in NEXT[:-1] else 'report'


def final_rc(step, facts, evidence_failed):
    """D3's rule: held 7 / 8 stay; otherwise runner release not proved 6 > a container or the private files left 80
    > session revoke not proved 70 > an evidence write failed 9 > the step rc."""
    if step in (7, 8):
        return step
    if facts['runner'] is False:
        return 6
    if not facts['gone'] or facts['private'] is False:
        return 80
    if facts['revoked'] is False:
        return 70
    if evidence_failed:
        return 9
    return step


if __name__ == '__main__':
    sys.exit(main(sys.argv))
