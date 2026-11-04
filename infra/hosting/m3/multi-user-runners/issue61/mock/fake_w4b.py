"""Stand-in for k3s kubectl in the #61 w4-broker-events harness. State: /mock/w4b.json (see test_w4b.py).

`get pod codex-broker-0 -o json` returns state['pods'][min(n, len-1)] for the n-th lookup, so a test can change
the Pod between the two lookups. `exec ... -- python -I -c CODE` really runs CODE with this container's python3,
so the fixed reader is exercised against real files under /data. Every call is appended to /mock/w4b-calls.log.
Switches in state['fail']: get, exec, exec-garbage, exec-slow.
"""
import json
import os
import subprocess
import sys
import time

STATE = '/mock/w4b.json'
LOG = '/mock/w4b-calls.log'


def main():
    args = sys.argv[1:]
    with open(LOG, 'a') as stream:
        stream.write(json.dumps(args) + '\n')
    with open(STATE) as stream:
        state = json.load(stream)
    fail = state.get('fail', [])
    if args[:6] == ['kubectl', '-n', 'codex-broker', 'get', 'pod', 'codex-broker-0'] and args[6:] == ['-o', 'json']:
        if 'get' in fail:
            sys.exit(1)
        n = state.get('gets', 0)
        state['gets'] = n + 1
        with open(STATE + '.tmp', 'w') as stream:
            json.dump(state, stream)
        os.replace(STATE + '.tmp', STATE)
        sys.stdout.write(json.dumps(state['pods'][min(n, len(state['pods']) - 1)]))
        return
    if args[:9] == ['kubectl', '-n', 'codex-broker', 'exec', 'codex-broker-0', '-c', 'broker', '--', 'python'] \
            and args[9:11] == ['-I', '-c'] and len(args) == 12:
        if 'exec' in fail:
            sys.exit(1)
        if 'exec-garbage' in fail:
            sys.stdout.write('DATA 999\nshort')
            return
        if 'exec-slow' in fail:
            time.sleep(40)
        done = subprocess.run(['python3', '-I', '-c', args[11]], stdout=subprocess.PIPE)
        sys.stdout.buffer.write(done.stdout)
        sys.exit(done.returncode)
    sys.exit(97)


main()
