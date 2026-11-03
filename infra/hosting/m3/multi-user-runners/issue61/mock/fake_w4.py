"""Stand-in for k3s (kubectl/crictl) in the #61 W4 harness. State: /mock/w4.json (see test_w4.py).

The API POST is served by the harness itself (an HTTP server that checks the session row, CSRF and headers);
it marks the owner entered, and this stand-in then lets the controller "create" the Pod after `podAfter` Pod
lookups. Anything not implemented fails with rc 97. Failure switches: the `fail` list in the state.
"""
import json
import os
import re
import sys
import time

STATE = '/mock/w4.json'
LOG = '/mock/w4-calls.log'
OWNER = '00000000-0000-4000-8000-000000005804'
POD_NAME = 'runner-' + OWNER.replace('-', '')


def load():
    with open(STATE) as stream:
        return json.load(stream)


def save(state):
    with open(STATE + '.tmp', 'w') as stream:
        json.dump(state, stream)
    os.replace(STATE + '.tmp', STATE)


def die(rc=1):
    sys.exit(rc)


def failing(state, name):
    return name in state.get('fail', [])


def opt(args, flag):
    if flag in args:
        return args[args.index(flag) + 1]
    for arg in args:
        if arg.startswith(flag + '='):
            return arg.split('=', 1)[1]
    return None


def maybe_create_pod(state):
    """After the POST entered the owner, the controller creates the Pod once `podAfter` lookups have passed."""
    if not state.get('entered') or state.get('pod') or failing(state, 'no-pod'):
        return
    state['podAfter'] = state.get('podAfter', 1) - 1
    if state['podAfter'] > 0:
        return
    pod = json.loads(json.dumps(state['podTemplate']))
    state['pod'] = pod
    state['db']['pod'] = pod['metadata']['uid']
    state['db']['phase'] = 'ready'


def kubectl(state, args):
    ns = opt(args, '-n')
    rest = [a for i, a in enumerate(args) if not (a == '-n' or (i > 0 and args[i - 1] == '-n'))]
    verb = rest[0]
    if verb == 'get' and rest[1] == 'deployment':
        d = state['deployments'].get('%s/%s' % (ns, rest[2]))
        if d is None:
            die()
        print(json.dumps({'metadata': {'uid': d['uid']}, 'spec': {'replicas': d['replicas']},
                          'status': {'readyReplicas': d['ready']}}))
        return
    if verb == 'get' and rest[1] == 'pods':
        pods = (['pod/' + POD_NAME] if state.get('pod') else []) + state.get('otherPods', [])
        print('\n'.join(pods))
        return
    if verb == 'get' and rest[1] == 'pod' and rest[2] == POD_NAME:
        if failing(state, 'pod-get-fail'):
            die()
        maybe_create_pod(state)
        save(state)
        pod = state.get('pod')
        if opt(rest, '-o') == 'jsonpath={.metadata.uid}':
            print(pod['metadata']['uid'] if pod else '', end='')
        elif pod:
            print(json.dumps(pod))
        return
    if verb == 'get' and rest[1] == 'configmap':
        print(state.get('origin', 'http://192.88.99.1|'), end='')
        return
    if verb == 'logs':
        print('\n'.join(state.get('logs', [])))
        return
    if verb == 'exec' and 'deploy/runner-controller' in rest:
        script = sys.stdin.read()
        state.setdefault('healthProbes', []).append(script)
        save(state)
        assert 'Registry(db).read(OWNER)' in script and '"GET", "/health"' in script
        if failing(state, 'health-fail'):
            die()
        print(json.dumps(dict(state['health'], probe='health')))
        return
    if verb == 'exec' and POD_NAME in rest:
        assert rest[rest.index('-c') + 1] == 'runner' and 'mode=ro' in rest[-1]
        print(state.get('ledger', 'ledger blocked=auth_revoked rejected=141 current_rejected=True snapshotId=x'))
        return
    if verb == 'exec' and 'postgres-0' in rest:
        return psql(state, sys.stdin.read())
    die(97)


def psql(state, query):
    state.setdefault('queries', []).append(query)
    save(state)
    match = re.search(r'/\*(w4w?|w4o):(\w+)\*/', query)
    kind, tag = match.group(1), match.group(2)
    if kind == 'w4w':
        assert query.startswith('BEGIN;') and not query.startswith('BEGIN READ ONLY'), query
    else:
        assert query.startswith('BEGIN READ ONLY;'), query
    if failing(state, 'db-fail-' + tag):
        die()
    db = state['db']
    if tag == 'pre':
        print(json.dumps(db))
    elif tag == 'conv':
        print(json.dumps(state['convAfter'] if state.get('entered') and 'convAfter' in state else state['conv']))
    elif tag == 'live':
        maybe_create_pod(state)
        save(state)
        print(json.dumps({k: db[k] for k in ('state', 'phase', 'generation', 'revision', 'pod', 'active', 'ambiguous',
                                             'outbox')} | {'openStops': db['allOpenStops']}))
    elif tag == 'insert':
        token_hash, owner, csrf = re.search(r"VALUES \('([0-9a-f]{64})','([0-9a-f-]{36})','([A-Za-z0-9_-]+)'", query).groups()
        state['sessions'][token_hash] = {'owner': owner, 'csrf': csrf}
        save(state)
        if failing(state, 'insert-lost'):
            die()
        print(json.dumps({'id': token_hash[:16], 'expires': '2026-11-04T01:00:00+00:00'}))
    elif tag == 'revoke':
        if failing(state, 'revoke-fail'):
            die()
        token_hash, owner = re.search(r"token_hash='([0-9a-f]{64})' AND account_id='([0-9a-f-]{36})'", query).groups()
        row = state['sessions'].get(token_hash)
        deleted = 1 if row and row['owner'] == owner else 0
        if deleted:
            del state['sessions'][token_hash]
        state.setdefault('revoked', []).append(token_hash)
        save(state)
        print(json.dumps({'deleted': deleted}))
    elif tag == 'session':
        token_hash = re.search(r"token_hash='([0-9a-f]{64})'", query).group(1)
        print(json.dumps({'n': 1 if token_hash in state['sessions'] else 0}))
    elif tag == 'state':  # w4o
        if failing(state, 'slow-db'):
            time.sleep(3)
        print(json.dumps({k: db[k] for k in ('state', 'phase', 'generation', 'revision', 'pod')}
                         | {'stop': state.get('stopGen4'), 'openStops': db['allOpenStops']}))
    else:
        die(97)


def crictl(state, args):
    if args[0] == 'inspecti':
        got = state['images'].get(args[-1])
        if got is None:
            die(1)
        print(got)
        return
    if args[0] in ('inspect', 'inspectp', 'ps'):
        # As the real crictl: options first; a word after the first ID is read as another ID (NotFound).
        if args[1:3] != ['-o', 'json'] or '-o' in args[3:]:
            die(1)
    if args[0] == 'inspect':
        info = state['cri'].get(args[3])
        if info is None or len(args) != 4:
            die(1)
        print(json.dumps(info))
        return
    if args[0] == 'inspectp':
        box = state['sandboxes'].get(args[3])
        if box is None or len(args) != 4:
            die(1)
        print(json.dumps(box))
        return
    if args[0] == 'ps':
        assert args[3] == '--id' and len(args) == 5
        print(json.dumps({'containers': state['ps'].get(args[4], [])}))
        return
    die(97)


def main():
    tool, args = os.path.basename(sys.argv[1]), sys.argv[2:]
    with open(LOG, 'a') as stream:
        stream.write(tool + ' ' + ' '.join(args) + '\n')
    state = load()
    if tool == 'k3s' and args[0] == 'kubectl':
        return kubectl(state, args[1:])
    if tool == 'k3s' and args[0] == 'crictl':
        return crictl(state, args[1:])
    die(97)


if __name__ == '__main__':
    main()
