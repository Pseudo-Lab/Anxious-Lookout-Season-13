#!/usr/bin/env python3
"""#61 W4 follow-up: one read-only look at codex-broker's refresh event file. Run as root on the dev VM:

    sudo -n python3 -I /root/issue61/w4-broker-events.py

It reads exactly /data/broker-events.jsonl in container `broker` of Pod codex-broker-0 (fixed UID), once,
through `kubectl exec ... python -I -c <fixed reader>`. The reader opens that one path with O_NOFOLLOW and
returns its bytes; it lists nothing, touches no other file and writes nothing. The bytes stay in this process
on the guest. Only entries that pass strict validation are printed; anything else is counted, never echoed.
It calls no broker endpoint, starts no refresh or login, restarts nothing and writes no file anywhere.

rc: 0 printed; 3 broker Pod identity/state not as fixed; 4 file absent, not a regular file, too large or
unreadable; 5 kubectl failed or timed out, or the Pod changed during the read; 130 interrupted.
"""
import calendar
import hashlib
import json
import re
import signal
import subprocess
import sys
import time

K3S = '/usr/local/bin/k3s'
NS, POD, POD_UID, CONTAINER = 'codex-broker', 'codex-broker-0', '1a92b7f7-37c1-47f6-86e1-ada85e753fa5', 'broker'
PATH = '/data/broker-events.jsonl'
CAP = 4 * 1024 * 1024
MAX_PRINT = 100
TIMEOUT = 30
# W4: entry 01:42:22Z, deadline 02:42:22Z. The window starts 30 min before the entry.
WINDOW = ('2026-11-04T01:12:22Z', '2026-11-04T02:42:22Z')
KEYS = frozenset(('at', 'event', 'kind', 'ok', 'before', 'after'))
IDENT = re.compile(r'(?:[0-9]{1,20}:[0-9]{1,20}|-)')
LIMITS = '''interpretation limits (read before using any line above):
- The file records only refreshes the broker itself drives (Credentials.access); the app-server's own refreshes are not in it.
- record() is best-effort: a lost write leaves no line. A missing line proves nothing.
- A runner's forced refresh is suppressed within 60 s of the previous one (credentials.py:169-170); it then yields
  either no line (token still has the margin) or a `scheduled` line. Absence of `forced` does not exclude the
  runner 401 path (cause 4).
- Entries carry no owner, state or Pod UID. A `forced` line is a hint for the refresh path, not proof that the
  W4 Pod 8e903f8e-e1cf-43e0-ad6c-06431ac50d3e caused it; other runners are not excluded by this output.
- This narrows the auth_revoked cause candidates of W4; it does not decide them. W4 stays diagnostic (entry rc 9).'''
READER = r'''
import os, stat, sys
p, cap = %r, %d
out = sys.stdout.buffer
try:
    fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
except FileNotFoundError:
    out.write(b"ABSENT\n"); sys.exit(0)
except OSError:
    out.write(b"OPENERR\n"); sys.exit(0)
info = os.fstat(fd)
if not stat.S_ISREG(info.st_mode):
    out.write(b"NOTREG\n"); sys.exit(0)
if info.st_size > cap:
    out.write(b"TOOBIG\n"); sys.exit(0)
chunks, size = [], 0
while size <= cap:
    chunk = os.read(fd, 65536)
    if not chunk:
        break
    chunks.append(chunk); size += len(chunk)
data = b"".join(chunks)
if len(data) > cap:
    out.write(b"TOOBIG\n"); sys.exit(0)
out.write(b"DATA %%d\n" %% len(data) + data)
''' % (PATH, CAP)


class Stop(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def interrupted(signum, frame):
    raise KeyboardInterrupt()


def say(text):
    try:
        print(text, flush=True)
    except Exception:
        pass


def utc(epoch):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(epoch))


def epoch(text):
    return calendar.timegm(time.strptime(text, '%Y-%m-%dT%H:%M:%SZ'))


def run(args):
    try:
        done = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        raise Stop(5, 'kubectl timed out: ' + ' '.join(args[1:4]))
    except OSError:
        raise Stop(5, 'kubectl not runnable')
    if done.returncode != 0:
        raise Stop(5, 'kubectl failed (rc %d): %s' % (done.returncode, ' '.join(args[1:4])))
    return done.stdout


def pod_identity():
    try:
        pod = json.loads(run([K3S, 'kubectl', '-n', NS, 'get', 'pod', POD, '-o', 'json']))
        uid = pod['metadata']['uid']
        phase = (pod.get('status') or {}).get('phase')
        names = [c.get('name') for c in pod['spec']['containers']]
        status = {c.get('name'): c for c in (pod.get('status') or {}).get('containerStatuses', [])}
    except Stop:
        raise
    except Exception:
        raise Stop(3, 'broker Pod unreadable')
    if uid != POD_UID:
        raise Stop(3, 'broker Pod UID %s is not the fixed %s' % (uid, POD_UID))
    if pod['metadata'].get('deletionTimestamp') or phase != 'Running':
        raise Stop(3, 'broker Pod not Running or deleting (phase %s)' % phase)
    broker = status.get(CONTAINER) or {}
    if CONTAINER not in names or not (broker.get('state') or {}).get('running') or not broker.get('containerID'):
        raise Stop(3, 'container %s not running in the broker Pod' % CONTAINER)
    return {'uid': uid, 'containerID': broker['containerID'], 'restarts': broker.get('restartCount'),
            'started': broker['state']['running'].get('startedAt')}


def strict(pairs):
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError('duplicate key')
    return dict(pairs)


def no_constant(name):
    raise ValueError('constant ' + name)


def validate(obj):
    return (type(obj) is dict and set(obj) == KEYS
            and type(obj['at']) is int and 1600000000 <= obj['at'] <= 2000000000
            and obj['event'] == 'refresh' and obj['kind'] in ('scheduled', 'forced') and type(obj['ok']) is bool
            and all(type(obj[k]) is str and IDENT.fullmatch(obj[k]) for k in ('before', 'after')))


def parse(data):
    """Valid entries (in file order) and error counts by class. Raw text is never returned."""
    entries, errors = [], {'utf8': 0, 'json': 0, 'shape': 0, 'partial': 0, 'blank': 0}
    lines = data.split(b'\n')
    if lines and lines[-1] == b'':
        lines.pop()
    elif lines:
        errors['partial'] += 1  # A last line without its newline: a write in progress or a lost tail.
        lines.pop()
    for raw in lines:
        if not raw.strip():
            errors['blank'] += 1
            continue
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            errors['utf8'] += 1
            continue
        try:
            obj = json.loads(text, object_pairs_hook=strict, parse_constant=no_constant)
        except ValueError:
            errors['json'] += 1
            continue
        if not validate(obj):
            errors['shape'] += 1
            continue
        entries.append({k: obj[k] for k in sorted(KEYS)})
    return entries, errors


def line(entry):
    return json.dumps(entry, sort_keys=True, separators=(',', ':'))


def main():
    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    started = time.time()
    try:
        say('w4-broker-events: started %s; target %s/%s uid %s container %s path %s'
            % (utc(started), NS, POD, POD_UID, CONTAINER, PATH))
        before = pod_identity()
        say('broker Pod ok: container %s started %s restarts %s' % (CONTAINER, before['started'], before['restarts']))
        out = run([K3S, 'kubectl', '-n', NS, 'exec', POD, '-c', CONTAINER, '--', 'python', '-I', '-c', READER])
        try:
            after = pod_identity()
        except Stop as stop:
            raise Stop(5, 'broker Pod lookup after the read refused (%s); nothing printed' % stop.message)
        if after != before:
            raise Stop(5, 'broker Pod or container changed during the read; nothing printed')
        head, _, rest = out.partition(b'\n')
        if head in (b'ABSENT', b'OPENERR', b'NOTREG', b'TOOBIG'):
            raise Stop(4, {b'ABSENT': 'file absent', b'OPENERR': 'file not openable (or a symlink)',
                           b'NOTREG': 'not a regular file', b'TOOBIG': 'file larger than %d bytes' % CAP}[head])
        match = re.fullmatch(rb'DATA ([0-9]{1,8})', head)
        if not match or int(match.group(1)) != len(rest):
            raise Stop(5, 'reader output malformed')
        data = rest
        raw_sha = hashlib.sha256(data).hexdigest()
        entries, errors = parse(data)
        low, high = epoch(WINDOW[0]), epoch(WINDOW[1])
        inside = [e for e in entries if low <= e['at'] <= high]
        earlier = [e for e in entries if e['at'] < low]
        tally = {}
        for e in entries:
            key = '%s/%s' % (e['kind'], 'ok' if e['ok'] else 'failed')
            tally[key] = tally.get(key, 0) + 1
        say('file: %d bytes sha256 %s; valid entries %d; rejected %s (counts only, never echoed)'
            % (len(data), raw_sha, len(entries), json.dumps(errors, sort_keys=True)))
        if entries:
            say('all valid entries: first at %s, last at %s; by kind/ok %s'
                % (utc(entries[0]['at']), utc(entries[-1]['at']), json.dumps(tally, sort_keys=True)))
        shown = inside[-MAX_PRINT:]
        printed = []
        if earlier:
            printed.append('before-window ' + line(earlier[-1]))
        for e in shown:
            printed.append('%s %s' % (utc(e['at']), line(e)))
        say('window %s .. %s: %d entries%s; last entry before the window shown for the before/after chain'
            % (WINDOW[0], WINDOW[1], len(inside),
               '' if len(inside) <= MAX_PRINT else ' (only the last %d printed)' % MAX_PRINT))
        for text in printed:
            say(text)
        say('printed entries sha256 %s' % hashlib.sha256(('\n'.join(printed) + '\n').encode()).hexdigest())
        say(LIMITS)
        say('finished %s (%.1f s); nothing written, no broker endpoint called' % (utc(time.time()), time.time() - started))
        code = 0
    except Stop as stop:
        say('STOPPED (%d): %s' % (stop.code, stop.message))
        code = stop.code
    except KeyboardInterrupt:
        say('STOPPED (130): interrupted')
        code = 130
    say('result rc=%d' % code)
    return code


if __name__ == '__main__':
    sys.exit(main())
