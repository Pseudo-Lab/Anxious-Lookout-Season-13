"""#90 stage D3 (chat-stream/DEV-VM.md P4), development VM only, no Codex turn: runner and API shutdown with a real
runner while browser streams are open (RESEARCH_STREAM_ENABLE=true on the api only; RUNNER_STREAM_TEXT stays off).
Run as root on the guest, each action other than `check` once, as a systemd unit, after review and PM's go:

    python3 -I -B /root/issue90/i90-d3.py check      # read-only: changes nothing, writes no file
    systemd-run … python3 -I -B /root/issue90/i90-d3.py flag-on   # api env RESEARCH_STREAM_ENABLE=true, rollout
    systemd-run … python3 -I -B /root/issue90/i90-d3.py p4        # P4-0, P4-1, P4-3, P4-4
    systemd-run … python3 -I -B /root/issue90/i90-d3.py p4-2      # idle stop (CODEX_USER_RUNNER_IDLE_SECONDS)
    systemd-run … python3 -I -B /root/issue90/i90-d3.py flag-off  # env entry and P4-3's restartedAt removed

flag-on / flag-off: one JSON patch of Deployment m2-hosting/api that first tests the UID, the resourceVersion just
read and the container name (and, for flag-off, the env entry it removes). The patch is applied to a local copy first:
it is sent only when the result's api template hash (the api image blanked, as i90-switch.py) is the wanted one -
flag-on: the stage C `apiTemplateSha256` once the flag entry is taken out again; flag-off: exactly the stage C
`apiTemplateSha256` (so i90-switch.py's reverse path stays valid; printed as `api template sha256 after flag-off`).
Then the rollout must finish, 1/1 ready, /api/health 200 (else rc 7, held: nothing else is patched).

p4 / p4-2: one browser session for …5805 (1 h; revoked in a `finally` that never raises), one existing conversation
(the newest), review's measuring client (CLIENT_SHA, standard library only) in throwaway containers of the guest
Docker tool image i90-expected.json names (`toolImage`, checked by id; the stage C runtime image is in k3s containerd
only) (`--network host --read-only --cap-drop ALL --security-opt no-new-privileges --user 10001:10001`; the client bytes
whose sha256 was checked and the Cookie header value are written, 0400 owned by 10001, to a private directory that is
removed at the end; never printed or put in argv). The client connects to API_HOST with `Host: 192.88.99.1`
(`--connect-address`); nothing is sent to 192.88.99.1. After a runner entry, every exit the process can handle sends
the stop first. Evidence is kept apart (review 70e92e7): runner side (stops row: receipt version, clean, requested /
completed; Pod deletion and gone times polled every second; the runner log followed from the Pod start), browser
(the client's JSONL), API slot (`api_stream_summary` lines of the session, from the api Pod logs; P4-3 follows the old
Pod's log from before the restart).

Every step prints `verdict <step>: True|False`; rc 0 means every step ran and its evidence was recorded, not that
every verdict is True (a last line names the False ones). PM judges rc and the verdict lines together.

rc: 0 ran to its end; 3 refused before any change; 4 a flag patch was refused (nothing changed); 5 a measurement
step could not run (cleanup ran: clients gone, runner stopped, session revoked); 6 runner stop/release not proved;
7 the api is not 1/1 healthy after a rollout (held); 8 the api template is not the wanted one after a patch (held);
9 every step ran but an evidence write failed; 70 every step ran but the session revoke is not proved; 80 a client
container was not proved gone; 130 interrupted twice. The result is the last `result rc=` line.
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
import time
import uuid

OWNER = '00000000-0000-4000-8000-000000005805'
OWNER_HEX = uuid.UUID(OWNER).hex
STATE = '75780e81-ef7f-4aec-a59c-ed9d71f4061a'
EXPECTED_FILE = '/root/issue90/i90-expected.json'
EXPECTED_SHA = '2476bdb2535e7733a58494fbac63057a02d8724b6b9f599b94fbfb07efa31b9f'  # i90-expected.json, from sha256sum
CLIENT_FILE = '/root/issue90/p4_client.py'
CLIENT_SHA = '48f83167348ee20de6f73f54d650471aaa2e8036a8605e0900bf853d1641571c'  # review's p4_client.py, from sha256sum
K3S, DOCKER = '/usr/local/bin/k3s', '/usr/bin/docker'
EVIDENCE = '/root/issue90/evidence'
LOCK = EVIDENCE + '/i90-d.lock'
RUNNER_NS, POD_NAME = 'codex-runner', 'runner-' + OWNER_HEX
API = ('m2-hosting', 'api', 'fad6799a-6df5-4086-812f-cfae6212adfa', 'app=hosting-api')
API_CONTAINER = 'api'
FLAG = {'name': 'RESEARCH_STREAM_ENABLE', 'value': 'true'}
RESTARTED = 'kubectl.kubernetes.io/restartedAt'
ORIGIN, API_HOST, API_PORT, HOST_HEADER = 'http://192.88.99.1', '10.0.0.107', 80, '192.88.99.1'
BASE = '/api/research'
READS = (5, 35, 65)          # runner ready reads after the container start (D1)
RUNNER_GRACE = 20            # the runner Pod's stop grace (DEV-VM P4)
IDLE_SECONDS = 1800          # CODEX_USER_RUNNER_IDLE_SECONDS on this VM (PM 2026-11-14); checked in the api env
SUMMARY_WAIT = 30
ACTIONS = ('check', 'flag-on', 'p4', 'p4-2', 'flag-off')
SESSION, OUT, STOPPING, CLIENTS, FOLLOWERS = {}, {'stream': None}, {'requested': False}, [], []


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
    except Exception:
        pass
    if OUT['stream'] is not None and not OUT.get('failed'):
        try:  # never raised into a cleanup step; reported as rc 9
            OUT['stream'].write(line + '\n')
            OUT['stream'].flush()
            os.fsync(OUT['stream'].fileno())
        except Exception:
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
    text = '%s\n/*i90d3:%s*/ %s\nCOMMIT;\n' % ('BEGIN;' if write else 'BEGIN READ ONLY;', tag, query)
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
    except Exception:
        OUT['failed'] = True


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
    return json.loads(read_pinned(EXPECTED_FILE, EXPECTED_SHA, 'i90-expected.json'))


# api Deployment: template hash and patches -----------------------------------------------------------------
def api_object(code):
    return kjson(['-n', API[0], 'get', 'deployment', API[1]], code, 'api Deployment lookup failed')


def template_sha(d, without_flag=False):
    """i90-switch.py's api_template_sha (containers[0].image blanked); `without_flag` also takes the flag entry out."""
    try:
        template = json.loads(json.dumps(d['spec']['template']))
        container = template['spec']['containers'][0]
        if container.get('name') != API_CONTAINER:
            return None
        container['image'] = ''
        if without_flag:
            container['env'] = [e for e in container.get('env', []) if e != FLAG]
        return hashlib.sha256(json.dumps(template, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    except Exception:
        return None


def flag_state(d):
    return {None: 'absent', -1: 'NOT A SINGLE true ENTRY'}.get(flag_index(d), 'present')


def flag_index(d):
    env = d['spec']['template']['spec']['containers'][0].get('env', [])
    found = [i for i, e in enumerate(env) if e.get('name') == FLAG['name']]
    return found[0] if len(found) == 1 and env[found[0]] == FLAG else (None if not found else -1)


def pointer(path):
    return [part.replace('~1', '/').replace('~0', '~') for part in path.split('/')[1:]]


def apply_patch(d, ops):
    """A local copy of `d` with the JSON patch applied (test / add / remove only), or None when a test fails."""
    doc = json.loads(json.dumps(d))
    for op in ops:
        parts = pointer(op['path'])
        parent = doc
        for part in parts[:-1]:
            parent = parent[int(part)] if isinstance(parent, list) else parent[part]
        last = parts[-1]
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
    return doc


def encode(value):
    return {'absent': True} if value is ABSENT else {'value': value}


def decode(item):
    if item == {'absent': True}:
        return ABSENT
    if isinstance(item, dict) and set(item) == {'value'} and isinstance(item['value'], str):
        return item['value']
    raise Stop(3, 'a recorded restartedAt state is unreadable')


def recorded(action, key):
    """`key` from the run.json of the one earlier run of `action` (None when it did not run or did not record it)."""
    runs = sorted(x for x in os.listdir(EVIDENCE) if re.fullmatch(r'i90-d3-%s-\d{8}T\d{6}Z' % re.escape(action), x))
    if not runs:
        return None
    try:
        value = json.load(open(os.path.join(EVIDENCE, runs[-1], 'run.json'))).get(key)
    except Exception:
        raise Stop(3, '%s run.json unreadable' % action) from None
    return None if value is None else decode(value)


ANNOTATIONS = '/spec/template/metadata/annotations'
RESTARTED_PATH = ANNOTATIONS + '/' + RESTARTED.replace('~', '~0').replace('/', '~1')
ABSENT = object()


def restarted_of(d):
    """The template's restartedAt annotation value, or ABSENT."""
    return ((d['spec']['template'].get('metadata') or {}).get('annotations') or {}).get(RESTARTED, ABSENT)


def flag_patches(d, direction, baseline=ABSENT, current=ABSENT):
    """Candidate patches for `direction`, built from the object read just before (the caller sends the one whose local
    result has the wanted template hash), or Stop(3). flag-off (review/PM D3 major): the env entry is removed, and the
    restartedAt annotation is restored to `baseline` (flag-on's recorded state; removed only when that state was
    absent) on a compare-and-set of `current` (the value P4-3 left, recorded by p4; the baseline when P4-3 did not
    run)."""
    version = (d.get('metadata') or {}).get('resourceVersion')
    if not isinstance(version, str) or not version:
        raise Stop(3, 'api Deployment has no resourceVersion')
    path = '/spec/template/spec/containers/0'
    ops = [{'op': 'test', 'path': '/metadata/uid', 'value': API[2]},
           {'op': 'test', 'path': '/metadata/resourceVersion', 'value': version},
           {'op': 'test', 'path': path + '/name', 'value': API_CONTAINER}]
    if direction == 'on':
        if flag_index(d) is not None:
            raise Stop(3, 'the api already has a %s entry' % FLAG['name'])
        return [ops + [{'op': 'add', 'path': path + '/env/-', 'value': FLAG}]]
    index = flag_index(d)
    if index is None or index < 0:
        raise Stop(3, 'the api has no single %s=true entry to remove' % FLAG['name'])
    ops += [{'op': 'test', 'path': '%s/env/%d' % (path, index), 'value': FLAG},
            {'op': 'remove', 'path': '%s/env/%d' % (path, index)}]
    if restarted_of(d) is not current and restarted_of(d) != current:
        raise Stop(3, 'the restartedAt annotation is not the recorded one (another change); nothing sent')
    if current is ABSENT:
        return [ops] if baseline is ABSENT else []
    cas = {'op': 'test', 'path': RESTARTED_PATH, 'value': current}
    if baseline is not ABSENT:
        return [ops + [cas, {'op': 'replace', 'path': RESTARTED_PATH, 'value': baseline}]]
    annotations = (d['spec']['template'].get('metadata') or {}).get('annotations') or {}
    candidates = [ops + [cas, {'op': 'remove', 'path': RESTARTED_PATH}]]
    if set(annotations) == {RESTARTED}:  # the dict may have been created by the restart itself
        candidates.append(ops + [cas, {'op': 'remove', 'path': ANNOTATIONS}])
    return candidates


def api_ready(code, where):
    """Rollout finished, 1/1 ready with the recorded UID, /api/health 200 within 60 s; else Stop(code) (held)."""
    if run([K3S, 'kubectl', '-n', API[0], 'rollout', 'status', 'deployment/' + API[1], '--timeout=180s'], 210)[0]:
        raise Stop(code, '%s: api rollout did not finish' % where)
    d = api_object(code)
    status = d.get('status') or {}
    if d['metadata']['uid'] != API[2] or d['spec'].get('replicas') != 1 or status.get('readyReplicas') != 1:
        raise Stop(code, '%s: api is not 1/1 ready' % where)
    deadline = time.time() + 60
    while call('GET', '/api/health', session=False)[0] != 200:
        if time.time() >= deadline:
            raise Stop(code, '%s: /api/health is not 200' % where)
        time.sleep(2)
    return d


def flag_change(ev, doc, direction):
    d = api_object(3)
    if d['metadata']['uid'] != API[2] or (d.get('status') or {}).get('readyReplicas') != 1:
        raise Stop(3, 'api is not the recorded 1/1 Deployment')
    image = d['spec']['template']['spec']['containers'][0].get('image')
    if image != doc['newApiImage']:
        raise Stop(3, 'api image is not the stage C runtime digest reference')
    want = doc['apiTemplateSha256']
    if direction == 'on':
        if template_sha(d) != want:
            raise Stop(3, 'api template is not the stage C one (apiTemplateSha256)')
        record(ev, restartedAtBefore=encode(restarted_of(d)))  # flag-off restores exactly this state
        candidates = flag_patches(d, 'on')
    else:
        baseline = recorded('flag-on', 'restartedAtBefore')
        if baseline is None:
            raise Stop(3, 'no flag-on run recorded the restartedAt baseline; nothing sent')
        after_p43 = recorded('p4', 'restartedAtAfter')
        current = baseline if after_p43 is None else after_p43
        say('flag-off: restartedAt baseline %s, expected now %s' % (
            'absent' if baseline is ABSENT else 'a value', 'absent' if current is ABSENT else
            ('the baseline' if current == baseline else 'the value P4-3 left')))
        candidates = flag_patches(d, 'off', baseline, current)
    ops = None
    for candidate in candidates:
        local = apply_patch(d, candidate)
        if local is not None and template_sha(local, without_flag=(direction == 'on')) == want \
                and (direction == 'off' or flag_index(local) is not None):
            ops = candidate
            break
    if ops is None:
        raise Stop(3, 'no %s patch gives the wanted api template; nothing sent' % direction)
    say('flag-%s: patch of %d operations (tests: uid, resourceVersion, container name%s)'
        % (direction, len(ops), '' if direction == 'on' else ', the env entry, the restartedAt annotation if any'))
    rc, _ = run([K3S, 'kubectl', '-n', API[0], 'patch', 'deployment', API[1], '--type=json', '-p', json.dumps(ops)], 60)
    after = api_object(8)
    sha, sha_flagless = template_sha(after), template_sha(after, without_flag=True)
    if direction == 'on':
        landed = flag_index(after) is not None and flag_index(after) >= 0 and sha_flagless == want
    else:
        landed = flag_index(after) is None and sha == want
    say('flag-%s: patch rc %d; flag entry %s; api template sha256 %s' % (
        direction, rc, flag_state(after), sha))
    if direction == 'off':
        say('api template sha256 after flag-off: %s; stage C apiTemplateSha256: %s; equal: %s' % (sha, want, sha == want))
    if not landed:
        if rc != 0 and template_sha(after) == template_sha(d):
            raise Stop(4, 'flag-%s patch refused; nothing changed' % direction)
        raise Stop(8, 'flag-%s: the api template is not the wanted one after the patch (held)' % direction)
    api_ready(7, 'flag-' + direction)
    say('flag-%s: api 1/1, rollout finished, /api/health 200' % direction)


# HTTP and session (D1's) ----------------------------------------------------------------------------------
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
        status, raw = response.status, response.read(1 << 20)
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
                   "('%s','%s','%s',now()+interval '1 hour') RETURNING token_hash) SELECT "
                   "json_build_object('id',left(token_hash,16)) FROM s;" % (token_hash, OWNER, csrf), 5, write=True)
    except Stop:
        raise Stop(5, 'session: insert failed or its result is unknown (removed below)') from None
    if len(rows) != 1 or rows[0].get('id') != token_hash[:16]:
        raise Stop(5, 'session: insert answered another row')
    SESSION.update(created=True, token=token, csrf=csrf)
    record(ev, session='created')
    say('session created: id %s, 1 hour, owner …5805' % token_hash[:16])


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
    except Exception:
        SESSION['revoked'] = False
    record(ev, session='revoked' if SESSION['revoked'] else 'revoke not proved (expires by itself)')
    say('session %s: %s' % (SESSION['hash'][:16], 'revoked' if SESSION['revoked'] else 'REVOKE NOT PROVED'))
    return SESSION['revoked']


# Runner ---------------------------------------------------------------------------------------------------
def registry():
    rows = sql('registry', "SELECT json_build_object('phase',phase,'gen',generation,'pod',pod_uid,'active',"
               "active_requests,'state',state_id) FROM runner_runtime.registry WHERE owner_id='%s';" % OWNER, 3)
    if len(rows) != 1 or rows[0]['state'] != STATE:
        raise Stop(3, '…5805 registry row missing or on another state')
    return rows[0]


def runner_pods(code):
    return kjson(['-n', RUNNER_NS, 'get', 'pods'], code, 'runner Pod lookup failed')['items']


def follow(namespace, pod, path):
    """`kubectl logs -f --timestamps` of one Pod into an evidence file, until stopped (or the Pod ends)."""
    stream = open(path, 'wb')
    process = subprocess.Popen([K3S, 'kubectl', '-n', namespace, 'logs', '-f', '--timestamps', pod],
                               stdout=stream, stderr=subprocess.DEVNULL)
    FOLLOWERS.append((process, stream))
    return path


def stop_followers():
    for process, stream in FOLLOWERS:
        try:
            process.terminate()
            process.wait(10)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
        try:
            stream.close()
        except Exception:
            pass
    del FOLLOWERS[:]


def start_runner(ev, step):
    """Entry, the next generation's Pod Running and ready, its log followed, available true at the +65 s read."""
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
    say('%s runner Pod %s generation %d ready' % (step, pod['metadata']['uid'], generation))
    return {'generation': generation, 'pod': pod['metadata']['uid'], 'log': log}


def wait_release(runner, step, limit):
    """Poll every second until idle_stopped with no Pod: (gone time, deletion timestamp seen) or Stop(6)."""
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
    ENTERED.clear()
    gone, deleted = wait_release(runner, step, 120)
    return runner_evidence(runner, step, gone, deleted)


ENTERED = {}


def ensure_stopped(step):
    """After an entry: whatever happened, the stop is sent (never raises)."""
    if not ENTERED:
        return
    try:
        status, _, _ = call('DELETE', BASE + '/codex/runner')
        say('%s cleanup: DELETE /codex/runner answered %s' % (step, status))
        ENTERED.clear()
    except Exception:
        say('%s cleanup: DELETE /codex/runner not sent (report)' % step)


# Measuring client -------------------------------------------------------------------------------------------
PRIVATE = {}


def prepare_private(ev, image):
    """A private directory with the client bytes (sha256 checked) and the Cookie header value, both 0400 owned by
    10001 (the client's user); the client never reads the root 0600 installed file directly. `image` is the guest
    Docker tool image, present under its recorded id."""
    client = read_pinned(CLIENT_FILE, CLIENT_SHA, 'p4_client.py')
    rc, found = run([DOCKER, 'image', 'inspect', '--format', '{{.Id}}', image], 30)
    if not (isinstance(image, str) and re.fullmatch(r'sha256:[0-9a-f]{64}', image) and rc == 0 and found == image):
        raise Stop(5, 'the tool image is not present in guest Docker under its recorded id')
    folder = os.path.join(ev, 'private')
    os.mkdir(folder, 0o711)
    PRIVATE['folder'] = folder
    for name, data in (('p4_client.py', client), ('cookie', ('anxious_session=' + SESSION['token']).encode())):
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
    if folder and os.path.isdir(folder):
        try:
            shutil.rmtree(folder)
            say('private directory (client copy, cookie) removed')
        except Exception:
            say('PRIVATE DIRECTORY NOT REMOVED: %s (report)' % folder)


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
    def __init__(self, ev, step, n, conversation, mode, duration):
        self.name = 'issue90-i90-d3-%s-%d-%d' % (step, n, os.getpid())
        self.path = os.path.join(ev, 'client-%s-%d.jsonl' % (step, n))
        self.label = '%s client %d (%s, %d s)' % (step, n, mode, duration)
        folder = PRIVATE['folder']
        url = 'http://%s%s/sessions/%s/events' % (HOST_HEADER, BASE, conversation)
        self.stream = open(self.path, 'wb')
        CLIENTS.append(self)
        self.process = subprocess.Popen(
            [DOCKER, 'run', '--rm', '--name', self.name, '--pull=never', '--network', 'host', '--read-only',
             '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--user', '10001:10001',
             '-v', folder + '/p4_client.py:/probe/p4_client.py:ro', '-v', folder + '/cookie:/run/probe-cookie:ro',
             '--entrypoint', 'python3', PRIVATE['image'], '-I', '-B', '/probe/p4_client.py', '--url', url,
             '--connect-address', API_HOST, '--cookie-file', '/run/probe-cookie', '--mode', mode,
             '--duration', str(duration)], stdout=self.stream, stderr=subprocess.DEVNULL)

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

    def end(self, limit):
        try:
            self.process.wait(limit)
        except subprocess.TimeoutExpired:
            return None
        ended = [r for r in self.records() if r.get('kind') == 'end']
        say('%s: rc %s, end %s, frames %s' % (self.label, self.process.returncode,
                                              json.dumps(ended[-1] if ended else None, sort_keys=True),
                                              json.dumps(self.frames(), sort_keys=True)))
        return ended[-1] if ended else {}

    def frames(self):
        counts = {}
        for r in self.records():
            if r.get('kind') == 'frame':
                key = r.get('event') + ('' if 'available' not in r else ':available=%s' % r['available'])
                counts[key] = counts.get(key, 0) + 1
        return counts


def clients_gone():
    ok = True
    for client in CLIENTS:
        try:
            if client.process.poll() is None:
                client.process.terminate()
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


# API slot evidence -------------------------------------------------------------------------------------------
def api_pod(code=5):
    items = kjson(['-n', API[0], 'get', 'pods', '-l', API[3]], code, 'api Pod lookup failed')['items']
    live = [p for p in items if not p['metadata'].get('deletionTimestamp')]
    if len(live) != 1:
        raise Stop(code, 'not exactly one live api Pod')
    return live[0]


def summaries(conversation, since, until=None, extra_logs=()):
    """The api_stream_summary lines of `conversation` whose log timestamp (second) is in [since, until], from the live
    api Pod's log (`--timestamps`) and the given followed logs: [(timestamp, reason), …]. Each step counts only its
    own window (review/PM D3 major)."""
    texts = []
    rc, out = run([K3S, 'kubectl', '-n', API[0], 'logs', '--timestamps', api_pod()['metadata']['name'],
                   '--since-time=' + since], 60)
    if rc == 0:
        texts.append(out)
    for path in extra_logs:
        try:
            texts.append(open(path, 'rb').read().decode('utf-8', 'replace'))
        except Exception:
            pass
    found = []  # one entry per line: the live Pod and a followed (old) Pod are different logs, never merged
    for text in texts:
        for line in text.splitlines():
            if 'api_stream_summary' not in line or '"session": "%s"' % conversation not in line:
                continue
            stamp = line.split(' ', 1)[0]
            if stamp[:19] < since[:19] or (until is not None and stamp[:19] > until[:19]):
                continue
            match = re.search(r'"reason": "([a-z_]+)"', line)
            found.append((stamp, match.group(1) if match else None))
    return sorted(found)


def wait_summaries(conversation, since, count, limit=SUMMARY_WAIT, extra_logs=()):
    deadline = time.time() + limit
    found = []
    while time.time() < deadline:
        found = summaries(conversation, since, None, extra_logs)
        if len(found) >= count:
            break
        time.sleep(2)
    return found


def step_end(step):
    """Before the next step: every client of this step ended and proved gone, every log follower stopped, and no
    issue90-i90-d3 container left (else Stop(80))."""
    gone = clients_gone()
    stop_followers()
    rc, out = run([DOCKER, 'ps', '-a', '--filter', 'name=^issue90-i90-d3-', '--format', '{{.Names}}'], 30)
    if not gone or rc != 0 or out:
        raise Stop(80, '%s: a client container was not proved gone' % step)
    say('%s clean: clients ended and gone, log followers stopped' % step)


# P4 steps --------------------------------------------------------------------------------------------------
def p4_0(ev, conversation, verdicts):
    """Slot baseline: three streams (the per-user cap), a 4th refused 429, the first closed and its summary line seen,
    then a new stream accepted; one summary per accepted stream inside this step's window."""
    since = now_utc()
    a, b, c = (Client(ev, 'p4-0', n, conversation, 'read', d) for n, d in ((1, 30), (2, 60), (3, 60)))
    opened = [x.status() for x in (a, b, c)]
    d = Client(ev, 'p4-0', 4, conversation, 'read', 10)
    fourth = d.status()
    d.end(30)
    say('P4-0: three streams %s; the 4th %s' % (opened, fourth))
    a.end(60)
    after_a = wait_summaries(conversation, since, 1)
    e = Client(ev, 'p4-0', 5, conversation, 'read', 20)
    again = e.status()
    for x in (b, c, e):
        x.end(120)
    total = wait_summaries(conversation, since, 4)
    until = now_utc()
    step_end('P4-0')
    total = summaries(conversation, since, until)
    say('P4-0: window %s..%s; summary after the first close %s; a new stream after it %s; summaries %s'
        % (since, until, after_a[:1], again, total))
    verdicts['P4-0'] = opened == [200, 200, 200] and fourth == 429 and len(after_a) >= 1 and again == 200 \
        and len(total) == 4


def p4_runner_stop(ev, conversation, verdicts, step, mode):
    """P4-1 (read) / P4-4 (no-read): a stream open, the runner stopped with DELETE. A no-read client's local deadline
    is never evidence of the server closing (P4-CLIENT.md); P4-4 is judged on the runner side and the summary line."""
    runner = start_runner(ev, step)
    since = now_utc()
    client = Client(ev, step, 1, conversation, mode, 90 if mode == 'read' else 45)
    opened = client.status()
    if mode == 'no-read':
        client.wait(lambda r: r.get('kind') == 'reader_paused', 20)
    time.sleep(10)
    runner_ok = stop_runner(ev, runner, step)
    unavailable = None
    if mode == 'read':
        unavailable = client.wait(lambda r: r.get('kind') == 'frame' and r.get('available') is False, 40) is not None
    client.end(120)
    wait_summaries(conversation, since, 1)
    until = now_utc()
    step_end(step.upper())
    count = summaries(conversation, since, until)
    say('%s: window %s..%s; stream %s; runner{available:false} seen %s; summaries %s'
        % (step.upper(), since, until, opened, unavailable, count))
    verdicts[step.upper()] = opened == 200 and runner_ok and unavailable is not False and len(count) == 1


def p4_3(ev, conversation, verdicts):
    """A stream held without reading; `rollout restart deploy/api`: the old Pod gone inside its live grace (seconds and
    margin reported), no lasting `Waiting for connections to close`, the new Pod healthy."""
    since = now_utc()
    old = api_pod()
    grace = old['spec'].get('terminationGracePeriodSeconds', 30)
    old_log = follow(API[0], old['metadata']['name'], os.path.join(ev, 'api-old-%s.log' % old['metadata']['name']))
    client = Client(ev, 'p4-3', 1, conversation, 'no-read', 45)
    opened = client.status()
    client.wait(lambda r: r.get('kind') == 'reader_paused', 20)
    issued = time.time()
    rc, _ = run([K3S, 'kubectl', '-n', API[0], 'rollout', 'restart', 'deployment/' + API[1]], 60)
    say('P4-3: rollout restart rc %d; old Pod %s, terminationGracePeriodSeconds %s' % (rc, old['metadata']['name'], grace))
    deleted, gone = None, None
    deadline = time.time() + grace + 120
    while time.time() < deadline and gone is None:
        items = kjson(['-n', API[0], 'get', 'pods', '-l', API[3]], 7, 'api Pod lookup failed')['items']
        mine = [p for p in items if p['metadata']['uid'] == old['metadata']['uid']]
        if mine and mine[0]['metadata'].get('deletionTimestamp') and deleted is None:
            deleted = time.time()
        if not mine:
            gone = time.time()
        time.sleep(1)
    after = api_ready(7, 'P4-3')
    record(ev, restartedAtAfter=encode(restarted_of(after)))  # flag-off's compare-and-set value
    client.end(120)
    time.sleep(2)
    stop_followers()
    try:
        text = open(old_log, 'rb').read().decode('utf-8', 'replace')
    except Exception:
        text = ''
    waiting = text.count('Waiting for connections to close')
    wait_summaries(conversation, since, 1, extra_logs=(old_log,))
    until = now_utc()
    step_end('P4-3')
    count = summaries(conversation, since, until, extra_logs=(old_log,))
    elapsed = None if gone is None or deleted is None else gone - deleted
    say('P4-3: stream %s; old Pod deletion seen %s s after the restart; gone %s s after its deletion; grace %s s; '
        'margin %s s (poll 1 s); "Waiting for connections to close" lines %d; new Pod 1/1, /api/health 200; '
        'summaries %s (the old Pod log included)'
        % (opened, None if deleted is None else round(deleted - issued, 1),
           None if elapsed is None else round(elapsed, 1), grace,
           None if elapsed is None else round(grace - elapsed, 1), waiting, count))
    verdicts['P4-3'] = rc == 0 and opened == 200 and elapsed is not None and elapsed < grace


def p4_2(ev, conversation, verdicts):
    """P4-1 with the idle stop instead of DELETE. The idle clock is the registry's last_activity (set by the entry, a
    message and a turn completion; runner_registry / lifecycle) and the runner's idle_since (app start, turn start and
    end): no /codex/status read is made after the ready reads, and an SSE stream (heartbeats) touches neither. The
    release time from the entry is reported; a release much later than idle + the controller period is a finding."""
    env = {e['name']: e.get('value') for e in api_object(5)['spec']['template']['spec']['containers'][0].get('env', [])}
    idle = int(env.get('CODEX_USER_RUNNER_IDLE_SECONDS', IDLE_SECONDS))
    entered = time.time()
    runner = start_runner(ev, 'p4-2')
    since = now_utc()
    client = Client(ev, 'p4-2', 1, conversation, 'read', idle + 240)
    opened = client.status()
    gone, deleted = wait_release(runner, 'p4-2', idle + 600)
    ENTERED.clear()
    runner_ok = runner_evidence(runner, 'p4-2', gone, deleted)
    unavailable = client.wait(lambda r: r.get('kind') == 'frame' and r.get('available') is False, 60) is not None
    client.process.terminate()
    client.end(60)
    wait_summaries(conversation, since, 1)
    until = now_utc()
    step_end('P4-2')
    count = summaries(conversation, since, until)
    after = gone - entered
    say('P4-2: idle %d s; released %.0f s after the entry (window idle..idle+600); stream %s; '
        'runner{available:false} seen %s; summaries %s' % (idle, after, opened, unavailable, count))
    verdicts['P4-2'] = opened == 200 and runner_ok and unavailable and len(count) == 1 and idle <= after <= idle + 600


# Checks, guards, main ----------------------------------------------------------------------------------------
def precheck(doc, action):
    d = api_object(3)
    index = flag_index(d)
    say('api: uid %s, %s/%s ready, image is stage C %s, flag entry %s, template = stage C (flag taken out) %s'
        % (d['metadata']['uid'] == API[2], (d.get('status') or {}).get('readyReplicas'), d['spec'].get('replicas'),
           d['spec']['template']['spec']['containers'][0].get('image') == doc['newApiImage'],
           flag_state(d),
           template_sha(d, without_flag=True) == doc['apiTemplateSha256']))
    state = registry()
    say('…5805 registry %s; runner Pods %d' % (json.dumps(state, sort_keys=True), len(runner_pods(3))))
    live = sql('sessions', "SELECT json_build_object('n',count(*)) FROM auth.sessions WHERE expires_at>now();", 3)[0]
    say('live sessions %s' % live['n'])
    if action == 'check':
        return
    if action in ('p4', 'p4-2'):
        if index is None or index < 0:
            raise Stop(3, '%s needs the flag on (run flag-on first)' % action)
        if (state['phase'], state['pod']) != ('idle_stopped', None) or runner_pods(3) or live['n']:
            raise Stop(3, '%s needs …5805 idle_stopped, no runner Pod and no live session' % action)
        if template_sha(d, without_flag=True) != doc['apiTemplateSha256'] and action == 'p4':
            raise Stop(3, 'p4 needs the api template = stage C plus the flag only')


def flag_line(action, code):
    """The flag state at the end of every action (never raises): an open flag after a failure is reported at once."""
    try:
        d = api_object(3)
        state, sha = flag_state(d), template_sha(d)
    except Exception:
        return 'FLAG STATE UNKNOWN (api lookup failed): check read-only and report'
    line = 'flag state at the end: %s %s; api template sha256 %s' % (FLAG['name'], state, sha)
    if state != 'absent' and code not in (0, 3):
        line += ' - THE FLAG IS OPEN after a failure: report at once; flag-off needs its own PM go (sheet table)'
    elif state != 'absent' and action == 'p4-2':
        line += ' - the flag is open; flag-off is the next step'
    return line


def guard(action):
    if [x for x in os.listdir(EVIDENCE) if re.fullmatch(r'i90-d3-%s-\d{8}T\d{6}Z' % re.escape(action), x)]:
        raise Stop(3, 'i90-d3 %s already ran; a second run is a PM decision' % action)


def take_lock():
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise Stop(3, 'another i90 stage D action holds the lock; nothing done') from None
    return fd


def measure(ev, doc, action, verdicts):
    create_session(ev)
    status, body, _ = call('GET', BASE + '/sessions?limit=1')
    items = body.get('items') if status == 200 and isinstance(body, dict) else None
    if not items:
        raise Stop(5, 'no existing conversation to stream')
    conversation = items[0]['id']
    record(ev, conversation=conversation)
    say('conversation %s (the newest, read only)' % conversation)
    prepare_private(ev, doc['toolImage'])
    if action == 'p4':
        steps = (('P4-0', lambda: p4_0(ev, conversation, verdicts)),
                 ('P4-1', lambda: p4_runner_stop(ev, conversation, verdicts, 'p4-1', 'read')),
                 ('P4-3', lambda: p4_3(ev, conversation, verdicts)),
                 ('P4-4', lambda: p4_runner_stop(ev, conversation, verdicts, 'p4-4', 'no-read')))
    else:
        steps = (('P4-2', lambda: p4_2(ev, conversation, verdicts)),)
    for name, step in steps:
        say('--- %s %s' % (name, now_utc()))
        step()


def main(argv):
    if len(argv) != 2 or argv[1] not in ACTIONS:
        print('usage: i90-d3.py %s' % '|'.join(ACTIONS))
        return 2
    action = argv[1]
    if os.geteuid() != 0:
        print('not root; nothing done\nresult rc=3')
        return 3
    if action == 'check':
        try:
            precheck(expected(), 'check')
        except Stop as stop:
            say('STOPPED (%d): %s' % (stop.code, stop.message))
            say('result rc=%d' % stop.code)
            return stop.code
        say('result rc=0 (read-only)')
        return 0
    for each in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(each, on_signal)
    ev, code, verdicts, revoked, gone, lock = None, 0, {}, None, True, None
    try:
        try:
            doc = expected()
            lock = take_lock()  # held until the end of main
            guard(action)
            precheck(doc, action)
            ev = os.path.join(EVIDENCE, 'i90-d3-%s-%s' % (action, time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())))
            os.mkdir(ev, 0o700)
            fd = os.open(os.path.join(ev, 'out.txt'), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            OUT['stream'] = os.fdopen(fd, 'w')
            record(ev, action=action, started=now_utc(), expectedSha256=EXPECTED_SHA, clientSha256=CLIENT_SHA)
            say('evidence: %s' % ev)
            if action in ('flag-on', 'flag-off'):
                flag_change(ev, doc, action[5:])
            else:
                measure(ev, doc, action, verdicts)
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
        # Never raises: the runner stop (after an entry), clients gone, followers, private files, session revoke.
        try:
            ensure_stopped(action)
        except BaseException:
            pass
        try:
            gone = clients_gone()
        except BaseException:
            gone = False
        try:
            stop_followers()
        except BaseException:
            pass
        try:
            remove_private()
        except BaseException:
            pass
        revoked = revoke_session(ev)
        if lock is not None:
            os.close(lock)  # releases the flock
    step_rc = code
    items = {'p4': ('P4-0', 'P4-1', 'P4-3', 'P4-4'), 'p4-2': ('P4-2',)}.get(action, ())
    for name in items:  # a step that did not run is `unknown`, never a pass (review/PM D3 major)
        say('verdict %s: %s' % (name, {True: 'True', False: 'False'}.get(verdicts.get(name), 'unknown')))
    if items:
        failing = [name for name in items if verdicts.get(name) is not True]
        say('verdict overall pass: %s' % (not failing and code == 0))
        if failing:
            say('verdicts False or unknown: %s' % ', '.join(failing))
    say(flag_line(action, code))
    if code == 0 and not gone:
        code = 80
    if code == 0 and revoked is False:
        code = 70
    if code == 0 and OUT.get('failed'):
        code = 9
    said = code
    say('result rc=%d' % code)
    if code == 0 and OUT.get('failed'):
        code = 9
    if ev:
        try:
            durable_json(os.path.join(ev, 'exit.json'), {'rc': code, 'stepRc': step_rc, 'verdicts': verdicts,
                                                        'sessionRevoked': revoked, 'finished': now_utc()})
        except Exception:
            if code == 0:
                code = 9
            say('exit.json not written: the result is unknown on disk')
    if code != said:
        say('result rc=%d (an evidence write failed after the result line)' % code)
    return code


if __name__ == '__main__':
    sys.exit(main(sys.argv))
