"""Stand-in for k3s kubectl and systemctl in the #61 W5 harness. State: /mock/w5.json (see test_w5.py).

The claims reader runs for real (python3 -I -c) against /var/run/secrets/kubernetes.io/serviceaccount in this
container. Before each reader run the token file is replaced by tokens[min(n, len-1)] (n = reader calls so far),
so a test sets when the rotation is seen. The proof process (controller code) cannot run here: its output is
state['proof'] with "JTI" replaced by the jti of the token file at that moment (or state['proofJti'] values).
Every call is appended to /mock/w5-calls.log. Unknown calls exit 97.
"""
import base64
import json
import os
import subprocess
import sys

STATE = '/mock/w5.json'
LOG = '/mock/w5-calls.log'
DIR = '/var/run/secrets/kubernetes.io/serviceaccount'
SIG = 'SECRET-SIGNATURE-NEVER-PRINT'


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def write_token(spec, n):
    data = os.path.join(DIR, '..2026_11_04_02_%02d_00.%d' % (n % 60, 100 + n))
    os.makedirs(data, exist_ok=True)
    if spec == 'garbage':
        body = 'not-a-jwt'
    else:
        body = b64(b'{"alg":"RS256","kid":"k"}') + '.' + b64(json.dumps(spec).encode()) + '.' + b64(SIG.encode())
    with open(os.path.join(data, 'token'), 'w') as stream:
        stream.write(body)
    for link, target in (('..data', os.path.basename(data)), ('token', '..data/token')):
        path = os.path.join(DIR, link)
        tmp = path + '.tmp'
        if os.path.lexists(tmp):
            os.remove(tmp)
        os.symlink(target, tmp)
        os.replace(tmp, path)


def current_jti():
    body = open(os.path.join(DIR, 'token')).read().split('.')[1]
    return json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4))).get('jti')


def load():
    with open(STATE) as stream:
        return json.load(stream)


def save(state):
    with open(STATE + '.tmp', 'w') as stream:
        json.dump(state, stream)
    os.replace(STATE + '.tmp', STATE)


def main():
    name = os.environ.get('FAKE_NAME', 'k3s')
    args = sys.argv[1:]
    with open(LOG, 'a') as stream:
        stream.write(json.dumps([name] + args) + '\n')
    state = load()
    if name == 'systemctl':
        verb, unit = args
        value = state.get('units', {}).get(unit, {}).get(verb.replace('is-', ''), 'inactive' if verb == 'is-active' else 'disabled')
        print(value)
        sys.exit(0 if value in ('active', 'enabled') else 3)
    fail = state.get('fail', [])
    if args[:5] == ['kubectl', '-n', 'runner-control', 'get', 'deployment'] and args[5:] == ['runner-controller', '-o', 'json']:
        if 'deploy' in fail:
            sys.exit(1)
        print(json.dumps(state['deploy']))
        return
    if args[:5] == ['kubectl', '-n', 'runner-control', 'get', 'pods'] and args[5:] == ['-l', 'app=runner-controller', '-o', 'json']:
        n = state.get('podGets', 0)
        state['podGets'] = n + 1
        save(state)
        pods = state['pods']
        print(json.dumps({'items': pods[min(n, len(pods) - 1)]}))
        return
    if args[:4] == ['kubectl', '-n', 'runner-control', 'exec'] and args[5:11] == ['-c', 'controller', '--', 'python', '-I', '-c'] \
            and len(args) == 12:
        n = state.get('reads', 0)
        state['reads'] = n + 1
        save(state)
        tokens = state['tokens']
        write_token(tokens[min(n, len(tokens) - 1)], n)
        if 'reader' in fail:
            sys.exit(1)
        if n in state.get('slowReads', []):
            import time
            time.sleep(state.get('slowSeconds', 3))
        done = subprocess.run(['python3', '-I', '-c', args[11]], stdout=subprocess.PIPE)
        if 'reader-extra' in fail:  # a reader output with a key outside the allow-list
            value = json.loads(done.stdout)
            value['PLANTED_KEY_NAME'] = 'PLANTED_VALUE'
            print(json.dumps(value))
            sys.exit(0)
        sys.stdout.buffer.write(done.stdout)
        sys.exit(done.returncode)
    if args[:5] == ['kubectl', '-n', 'runner-control', 'exec', '-i'] and args[6:] == ['-c', 'controller', '--', 'python', '-B', '-']:
        script = sys.stdin.read()
        if 'observer_pool.bound' not in script or 'def jti' not in script:
            sys.exit(96)
        if 'proof' in fail:
            sys.exit(1)
        if state.get('proofReal'):  # run the real PROOF body against mock/w5proof (the mocked controller module)
            env = dict(os.environ, PYTHONPATH=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'w5proof'),
                       **{k: v for k, v in state.get('proofEnv', {}).items()})
            done = subprocess.run(['python3', '-B', '-'], input=script.encode(), stdout=subprocess.PIPE, env=env)
            sys.stdout.buffer.write(done.stdout)
            sys.exit(done.returncode)
        jti = current_jti()
        before, after = state.get('proofJti', [jti, jti])
        out = dict(state['proof'], jtiBefore=before or jti, jtiAfter=after or jti)
        print(json.dumps(out))
        return
    if args[:4] == ['kubectl', '-n', 'runner-control', 'logs'] and args[5:7] == ['-c', 'controller'] \
            and args[7].startswith('--since-time='):
        if 'logs' in fail:
            sys.exit(1)
        sys.stdout.write(state.get('logs', ''))
        return
    sys.exit(97)


main()
