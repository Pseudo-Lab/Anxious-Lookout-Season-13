"""Mock harness for the #61 w4-broker-events.py one-shot. Docker only, never on a real node:
    docker run --rm -v "$PWD/infra/hosting/m3:/m3:ro" python:3.9-alpine \
      python3 -I /m3/multi-user-runners/issue61/test_w4b.py
mock/fake_w4b.py stands in for k3s kubectl; its `exec` really runs the script's fixed reader against /data here.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

SRC = '/m3/multi-user-runners/issue61'
INSTALL = '/root/issue61'
PATH = '/data/broker-events.jsonl'
UID = '1a92b7f7-37c1-47f6-86e1-ada85e753fa5'
SECRET = 'eyJhbGciOiJSUzI1NiJ9.SECRETPAYLOAD.sig-should-never-print'
passed = failed = 0


def check(name, condition, detail=''):
    global passed, failed
    if condition:
        passed += 1
        print('ok   ' + name)
    else:
        failed += 1
        print('FAIL ' + name + (' (' + detail[-1500:] + ')' if detail else ''))


def pod(uid=UID, phase='Running', cid='containerd://' + 'd' * 64, restarts=0, deleting=False, running=True):
    meta = {'name': 'codex-broker-0', 'namespace': 'codex-broker', 'uid': uid}
    if deleting:
        meta['deletionTimestamp'] = '2026-11-04T03:00:00Z'
    state = {'running': {'startedAt': '2026-10-26T05:03:50Z'}} if running else {'waiting': {'reason': 'x'}}
    return {'metadata': meta, 'spec': {'containers': [{'name': 'broker'}, {'name': 'mock-oauth'}]},
            'status': {'phase': phase, 'containerStatuses': [
                {'name': 'broker', 'containerID': cid, 'restartCount': restarts, 'state': state},
                {'name': 'mock-oauth', 'containerID': 'containerd://' + 'e' * 64, 'restartCount': 0,
                 'state': {'running': {}}}]}}


def entry(at, kind='scheduled', ok=True, before='11:22', after='11:33'):
    return json.dumps({'at': at, 'event': 'refresh', 'kind': kind, 'ok': ok, 'before': before, 'after': after},
                      sort_keys=True)


def setup(pods=None, fail=(), content=None, kind='file'):
    shutil.rmtree('/data', ignore_errors=True)
    os.makedirs('/data')
    if kind == 'file' and content is not None:
        with open(PATH, 'wb') as stream:
            stream.write(content)
    elif kind == 'symlink':
        with open('/data/auth.json', 'w') as stream:
            stream.write('{"tokens": "' + SECRET + '"}')
        os.symlink('/data/auth.json', PATH)
    elif kind == 'dir':
        os.makedirs(PATH)
    elif kind == 'fifo':
        os.mkfifo(PATH)
    with open('/mock/w4b.json', 'w') as stream:
        json.dump({'pods': pods or [pod()], 'fail': list(fail), 'gets': 0}, stream)
    if os.path.exists('/mock/w4b-calls.log'):
        os.remove('/mock/w4b-calls.log')


def snapshot():
    out = {}
    for root, dirs, files in os.walk('/data'):
        for name in dirs + files:
            path = os.path.join(root, name)
            info = os.lstat(path)
            out[path] = (info.st_mode, info.st_size, info.st_mtime_ns)
    return out


def run():
    done = subprocess.run(['python3', '-I', INSTALL + '/w4-broker-events.py'], stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, timeout=120)
    return done.returncode, done.stdout.decode()


def calls():
    if not os.path.exists('/mock/w4b-calls.log'):
        return []
    with open('/mock/w4b-calls.log') as stream:
        return [json.loads(l) for l in stream]


def main():
    os.makedirs(INSTALL, exist_ok=True)
    os.makedirs('/mock', exist_ok=True)
    shutil.copy(SRC + '/w4-broker-events.py', INSTALL + '/w4-broker-events.py')
    with open('/usr/local/bin/k3s', 'w') as stream:
        stream.write('#!/bin/sh\nexec python3 -I ' + SRC + '/mock/fake_w4b.py "$@"\n')
    os.chmod('/usr/local/bin/k3s', 0o755)
    source = open(INSTALL + '/w4-broker-events.py').read()

    # Static: no write-capable call, no broker endpoint, only the fixed path.
    check('static: no file writes', not re.search(r"open\([^)]*['\"][wa]|os\.(remove|unlink|rename|replace|mkdir|"
                                                   r"chmod|chown)|O_WRONLY|O_RDWR|O_CREAT|shutil", source))
    check('static: no broker endpoint, login or refresh call', not re.search(r'/v1/access|account/|login|urllib|http\.client|'
                                                                          r'requests|curl', source.split('"""')[2].split('LIMITS =')[0]))
    check('static: exactly one path literal', source.count("'/data/") == 1 and "PATH = '/data/broker-events.jsonl'" in source)

    # Happy path: window filtering, rejected lines counted not echoed, chain line, limits printed.
    lo, hi = 1793754742, 1793760142  # 2026-11-04T01:12:22Z, 02:42:22Z
    lines = [entry(lo - 7200, before='1:1', after='1:2'), entry(lo - 10, before='1:2', after='1:3'),
             entry(lo + 1809, after='1:4', before='1:3'), entry(lo + 1809, kind='forced', before='1:4', after='1:5'),
             '{"at": %d, "event": "refresh", "kind": "forced", "ok": true, "before": "1:5", "after": "1:6", '
             '"token": "%s"}' % (lo + 1810, SECRET),
             '{"at": %d, "at": %d, "event": "refresh", "kind": "forced", "ok": true, "before": "-", "after": "-"}'
             % (lo + 1, lo + 2),
             '{"at": true, "event": "refresh", "kind": "forced", "ok": true, "before": "-", "after": "-"}',
             '{"at": %d, "event": "refresh", "kind": "forced", "ok": 1, "before": "-", "after": "-"}' % (lo + 3),
             '{"at": %d, "event": "refresh", "kind": "manual", "ok": true, "before": "-", "after": "-"}' % (lo + 4),
             '{"at": %d, "event": "refresh", "kind": "forced", "ok": true, "before": "%s", "after": "-"}' % (lo + 5, SECRET),
             '{"at": NaN, "event": "refresh"}', 'not json ' + SECRET, '', entry(hi + 5)]
    content = ('\n'.join(lines) + '\n').encode() + b'\xff\xfe' + SECRET.encode() + b'\n' + b'{"partial": "' + SECRET.encode()
    setup(content=content)
    before = snapshot()
    rc, out = run()
    check('happy: rc 0', rc == 0, out)
    check('happy: secret never printed', SECRET not in out and 'SECRETPAYLOAD' not in out and '"token"' not in out, out)
    check('happy: raw hash printed', hashlib.sha256(content).hexdigest() in out, out)
    check('happy: counts', 'valid entries 5;' in out and '"blank": 1' in out and '"json": 3' in out
          and '"shape": 5' in out and '"utf8": 1' in out and '"partial": 1' in out, out)
    check('happy: window has 2 entries, chain line from the last earlier entry',
          ': 2 entries;' in out and 'before-window ' + json.dumps(json.loads(entry(lo - 10, before='1:2', after='1:3')), sort_keys=True,
                                                                  separators=(',', ':')) in out, out)
    check('happy: out-of-window entries not listed', '"after":"1:2"' not in out and str(hi + 5) not in out, out)
    check('happy: forced entry printed with UTC time', '2026-11-04T01:42:31Z {"after":"1:5","at":%d' % (lo + 1809) in out, out)
    check('happy: tally', '"forced/ok": 1' in out and '"scheduled/ok": 4' in out, out)
    printed = [l for l in out.splitlines() if l.startswith('before-window ') or l.startswith('2026-')]
    check('happy: printed-entries hash covers exactly the printed lines',
          'printed entries sha256 ' + hashlib.sha256(('\n'.join(printed) + '\n').encode()).hexdigest() in out, out)
    check('happy: limits text', 'interpretation limits' in out and 'suppressed within 60 s' in out
          and 'best-effort' in out and 'no owner, state or Pod UID' in out and 'does not decide' in out, out)
    check('happy: /data unchanged', snapshot() == before)
    c = calls()
    check('happy: exactly get, exec, get', [x[3] for x in c] == ['get', 'exec', 'get'], json.dumps(c))
    check('happy: exec targets broker container with the fixed reader',
          c[1][:11] == ['kubectl', '-n', 'codex-broker', 'exec', 'codex-broker-0', '-c', 'broker', '--', 'python', '-I', '-c']
          and "'/data/broker-events.jsonl'" in c[1][11], json.dumps(c[1][:11]))

    # More than 100 entries in the window: only the last 100 printed.
    many = [entry(lo + i, before='%d:1' % i, after='%d:2' % i) for i in range(130)]
    setup(content=('\n'.join(many) + '\n').encode())
    rc, out = run()
    check('cap: rc 0 and truncation stated', rc == 0 and '130 entries (only the last 100 printed)' in out, out)
    check('cap: first 30 not printed, last printed', '"before":"29:1"' not in out and '"before":"129:1"' in out, out)
    check('cap: no before-window line when none earlier', 'before-window' not in out, out)

    # Empty file: rc 0, zero entries.
    setup(content=b'')
    rc, out = run()
    check('empty: rc 0, 0 entries', rc == 0 and 'valid entries 0;' in out and ': 0 entries;' in out, out)

    # File problems -> rc 4, nothing of the target echoed.
    for kind, word in (('absent', 'file absent'), ('symlink', 'symlink'), ('dir', 'not a regular file'),
                       ('fifo', 'not a regular file')):
        setup(kind=kind)
        rc, out = run()
        check('file %s: rc 4' % kind, rc == 4 and word in out and SECRET not in out, out)
    setup(content=b'x' * (4 * 1024 * 1024 + 1))
    rc, out = run()
    check('file too large: rc 4', rc == 4 and 'larger than' in out, out)

    # Pod identity -> rc 3, no exec.
    for name, p in (('uid', pod(uid='00000000-0000-0000-0000-000000000000')), ('phase', pod(phase='Pending')),
                    ('deleting', pod(deleting=True)), ('container not running', pod(running=False))):
        setup(pods=[p], content=(entry(lo + 1) + '\n').encode())
        rc, out = run()
        check('pod %s: rc 3, no exec' % name, rc == 3 and all(x[3] != 'exec' for x in calls()), out)

    # Pod changed during the read -> rc 5, no entries printed.
    for name, second in (('restart', pod(restarts=1)), ('container id', pod(cid='containerd://' + 'f' * 64)),
                         ('uid', pod(uid='11111111-1111-1111-1111-111111111111'))):
        setup(pods=[pod(), second], content=(entry(lo + 1) + '\n').encode())
        rc, out = run()
        check('changed %s: rc 5, nothing printed' % name, rc == 5 and '"kind"' not in out and 'file:' not in out, out)

    # kubectl failures -> rc 5.
    for name in ('get', 'exec', 'exec-garbage'):
        setup(fail=[name], content=(entry(lo + 1) + '\n').encode())
        rc, out = run()
        check("kubectl %s failure: rc 5" % name,
              rc == (5 if name != 'get' else 5) and '"kind"' not in out, out)
    setup(fail=['exec-slow'], content=(entry(lo + 1) + '\n').encode())
    rc, out = run()
    check('exec timeout: rc 5', rc == 5 and 'timed out' in out, out)

    # Counter-test: a reader without O_NOFOLLOW would follow the symlink to auth.json.
    weak = source.replace(' | os.O_NOFOLLOW', '')
    with open(INSTALL + '/w4-broker-events.py', 'w') as stream:
        stream.write(weak)
    setup(kind='symlink')
    rc, out = run()
    check('counter: without O_NOFOLLOW the symlink is followed (so the guard matters)', rc != 4, out)
    check('counter: even then the secret is not echoed (validation)', SECRET not in out, out)
    shutil.copy(SRC + '/w4-broker-events.py', INSTALL + '/w4-broker-events.py')

    # Mutations the harness must catch.
    def mutated(old, new):
        assert old in source, old
        with open(INSTALL + '/w4-broker-events.py', 'w') as stream:
            stream.write(source.replace(old, new, 1))

    mutated("    return (type(obj) is dict and set(obj) == KEYS", "    return True or (type(obj) is dict and set(obj) == KEYS")
    setup(content=('{"at": %d, "event": "refresh", "kind": "forced", "ok": true, "before": "%s", "after": "-"}\n'
                   % (lo + 5, SECRET)).encode())
    rc, out = run()
    check('mutation: validation bypassed -> the harness sees the leak', SECRET in out, out)
    mutated("        if after != before:", "        if False:")
    setup(pods=[pod(), pod(restarts=1)], content=(entry(lo + 1) + '\n').encode())
    rc, out = run()
    check('mutation: after-read identity check removed -> rc 0 (so the check matters)', rc == 0, out)
    shutil.copy(SRC + '/w4-broker-events.py', INSTALL + '/w4-broker-events.py')

    print('%d passed, %d failed' % (passed, failed))
    sys.exit(1 if failed else 0)


main()
