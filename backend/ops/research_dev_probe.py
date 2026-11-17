"""#97 fixed, no-model probes. Execution is review-owned after PM dev approval.

No turn/start, native admission, token issuance or provider request. Network modes
are suitable as the command of `codex sandbox -P native --include-managed-config`.
The inode mode itself wraps that sandbox and requires a fresh measurement Pod.
"""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import shutil
import signal
import ssl
import subprocess
import sys
import time

HOST = 'research97.dev.invalid'
WORK = Path('/state/workspace')
GRANT = Path('/state/research-api/turn.json')
ROOTS = [Path('/state/research-api'), WORK, Path('/state/home'), Path('/state/codex')]
SANDBOX = ['/usr/local/bin/codex', 'sandbox', '-P', 'native', '--include-managed-config', '-C', str(WORK), '--']
ENV = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8',
       'HOME': '/state/home', 'CODEX_HOME': '/state/codex'}
DUMMY = 'rturn1.00000000-0000-4000-8000-000000000097.00000000-0000-4000-8000-000000000098.' + 'A' * 43


def emit(value):
    print(json.dumps(value, sort_keys=True), flush=True)


def https(ca=None, wrong_name=False):
    # Optional trust is endpoint-specific to this no-model fixture, never CLI or OS trust.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if ca:
        context.load_verify_locations(cafile=ca)
    else:
        context.load_default_certs()
    host = 'wrong97.dev.invalid' if wrong_name else HOST
    connection = http.client.HTTPSConnection(host, 443, timeout=5, context=context)
    try:
        connection.connect()
        peer = connection.sock.getpeername()
        fingerprint = hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()
        connection.request('GET', '/api/health', headers={'Accept': 'application/json'})
        response = connection.getresponse()
        raw = response.read(1025)
        ready = len(raw) <= 1024 and json.loads(raw).get('status') == 'ok'
        expected_peer = peer[0] == '198.18.97.2' and peer[1] == 443
        emit({'tls_verified': True, 'peer': peer[0], 'port': peer[1], 'expected_peer': expected_peer,
              'certificate_sha256': fingerprint, 'http': response.status, 'ready': ready})
        return expected_peer and response.status == 200 and ready
    finally:
        connection.close()


def internal():
    """No real credentials: only missing, malformed and never-issued research syntax."""
    checks = {}
    targets = [('broker', 'codex-broker.codex-broker.svc.cluster.local', 8080, '/v1/access',
                {'ownerId': '00000000-0000-4000-8000-000000000097', 'stateId': '00000000-0000-4000-8000-000000000098'}),
               ('callback', 'api-internal.m2-hosting.svc.cluster.local', 8081, '/api/internal/research/tools',
                {'sessionId': '00000000-0000-4000-8000-000000000097',
                 'requestId': '00000000-0000-4000-8000-000000000098', 'name': 'runner_shell_authorize', 'arguments': {}})]
    for label, host, port, path, body in targets:
        for purpose, token in [('missing', None), ('malformed', 'issue97-invalid'), ('research-shaped', DUMMY)]:
            connection = http.client.HTTPConnection(host, port, timeout=3)
            try:
                headers = {'Content-Type': 'application/json'}
                if token:
                    headers['Authorization'] = 'Bearer ' + token
                connection.request('POST', path, body=json.dumps(body), headers=headers)
                response = connection.getresponse()
                response.read(1024)  # Never print response content/headers.
                expected = 401 if purpose == 'missing' else 403 if label == 'broker' else 404
                checks[label + ':' + purpose] = response.status == expected
                emit({'service': label, 'purpose': purpose, 'http': response.status})
            finally:
                connection.close()
    return all(checks.values())


INNER = r'''
import json, os, pathlib, sys, time
work=pathlib.Path('/state/workspace'); grant='/state/research-api/turn.json'
def read(path):
    with open(path) as f: return json.load(f)
def denied(path, listing=False, writing=False):
    try:
        if listing: os.listdir(path)
        elif writing:
            with open(path, 'a') as f: f.write('UNEXPECTED-WRITE')
        else:
            with open(path, 'rb') as f: f.read(1)
    except OSError: return True
    return False
def same_fd_view(path, identity):
    try: source=open(path)
    except OSError: return True  # Reopening an authorized FD need not be available.
    with source:
        try:
            info=os.fstat(source.fileno())
            return (info.st_dev,info.st_ino)==identity and json.load(source)=={'available':False,'probeMarker':'old'}
        except (OSError,ValueError): return False
def scoped_parent():
    try: return set(os.listdir('/state/research-api')) <= {'turn.json'}
    except OSError: return True  # A virtual parent may be unlistable.
fd=os.open(grant, os.O_RDONLY|os.O_NOFOLLOW)
info=os.fstat(fd); before=(info.st_dev,info.st_ino)
checks={'old_read': json.loads(os.read(fd,4096))=={'available':False,'probeMarker':'old'},
        'readonly':denied(grant,writing=True)}
for name,path in [('sibling','/state/research-api/sibling'), ('parent-canary','/state/research-api/parent-canary'),
                  ('home','/state/home/sentinel'),
                  ('control','/control/issue97-sentinel'), ('codex','/state/codex/sentinel'),
                  ('alias',str(work/'private-alias')), ('proc-parent','/proc/'+sys.argv[1]+'/environ'),
                  ('proc-root','/proc/'+sys.argv[1]+'/root/control/issue97-sentinel'),
                  ('proc-control-fd','/proc/'+sys.argv[1]+'/fd/'+sys.argv[2])]:
    checks[name]=denied(path)
checks['proc-parent-fds']=denied('/proc/'+sys.argv[1]+'/fd',listing=True)
checks['proc-self-fd']=same_fd_view('/proc/self/fd/'+str(fd),before)
checks['parent-list']=scoped_parent()
checks['credential-env']=not any(k in os.environ for k in ('RUNNER_TOKEN','OPENAI_API_KEY','CODEX_API_KEY','ISSUE97_PARENT_CANARY'))
(work/'ready').write_text('ready')
until=time.monotonic()+15
while not (work/'advance').exists():
    if time.monotonic()>until: raise TimeoutError()
    time.sleep(.02)
os.lseek(fd,0,0)
checks['old-fd']=json.loads(os.read(fd,4096))=={'available':False,'probeMarker':'old'}
checks['old-path']=read(grant)=={'available':False,'probeMarker':'old'}
info=os.stat(grant)
checks['old-inode']=(info.st_dev,info.st_ino)==before
checks['proc-self-fd-after']=same_fd_view('/proc/self/fd/'+str(fd),before)
checks['parent-list-after']=scoped_parent()
os.close(fd)
print(json.dumps(checks),flush=True)
sys.exit(0 if all(checks.values()) else 1)
'''


def fresh_layout():
    if (os.getuid() != 10001 or list(Path('/state').iterdir()) or list(Path('/control').iterdir())
            or Path('/run/codex-broker').exists()):
        raise ValueError('Fresh disposable measurement volumes required')


def replace_marker(marker):
    path = GRANT.with_name('next')
    with path.open('x') as output:
        os.chmod(path, 0o600)
        json.dump({'available': False, 'probeMarker': marker}, output)
        output.flush()
        os.fsync(output.fileno())
    os.replace(path, GRANT)


def inode():
    fresh_layout()
    process = None
    control_fd = None
    try:
        for path in ROOTS:
            path.mkdir(mode=0o700)
        for path in [GRANT.with_name('sibling'), GRANT.with_name('parent-canary'),
                     ROOTS[2]/'sentinel', ROOTS[3]/'sentinel', Path('/control/issue97-sentinel')]:
            path.write_text('issue97-private-canary')
        control_fd = os.open('/control/issue97-sentinel', os.O_RDONLY | os.O_NOFOLLOW)
        if os.read(control_fd, 4096) != b'issue97-private-canary':
            raise RuntimeError('Outside control FD is unavailable')
        os.lseek(control_fd, 0, os.SEEK_SET)
        (WORK/'private-alias').symlink_to(GRANT.with_name('sibling'))
        replace_marker('old')
        old_inode = GRANT.stat().st_ino
        process = subprocess.Popen(SANDBOX + ['python', '-I', '-c', INNER, str(os.getpid()), str(control_fd)],
            env=ENV, cwd=WORK, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, close_fds=True, start_new_session=True)
        until = time.monotonic() + 20
        while not (WORK/'ready').exists():
            if process.poll() is not None or time.monotonic() > until:
                raise RuntimeError('Sandbox did not reach inode probe')
            time.sleep(.02)
        replace_marker('new')
        if GRANT.stat().st_ino == old_inode:
            raise RuntimeError('Inode did not change')
        (WORK/'advance').write_text('advance')
        output, _ = process.communicate(timeout=20)
        checks = json.loads(output)
        if process.returncode or not checks or not all(value is True for value in checks.values()):
            emit({'inode_checks': checks, 'passed': False})
            return False
        fresh = subprocess.Popen(SANDBOX + ['python', '-I', '-c',
            'import json; assert json.load(open("/state/research-api/turn.json"))=={"available":False,"probeMarker":"new"}'],
            env=ENV, cwd=WORK, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, close_fds=True)
        try:
            result = fresh.wait(timeout=20)
        finally:
            stop(fresh)
        emit({'inode_checks': checks, 'fresh_sandbox_new_inode': result == 0})
        return result == 0
    finally:
        if process is not None:
            stop(process)
        if control_fd is not None:
            os.close(control_fd)
        for path in ROOTS:
            if path.exists():
                shutil.rmtree(path)
        Path('/control/issue97-sentinel').unlink(missing_ok=True)


def stop(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['inode', 'https', 'internal'])
    parser.add_argument('--ca')
    parser.add_argument('--wrong-name', action='store_true')
    args = parser.parse_args()
    signal.signal(signal.SIGALRM, lambda *a: (_ for _ in ()).throw(TimeoutError()))
    signal.alarm(75)
    try:
        passed = inode() if args.mode == 'inode' else internal() if args.mode == 'internal' else https(args.ca, args.wrong_name)
        return 0 if passed else 1
    except Exception as error:
        emit({'passed': False, 'failure_type': type(error).__name__})
        return 1
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    sys.exit(main())
