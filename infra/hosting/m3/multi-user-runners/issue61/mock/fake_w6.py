"""Stand-in for k3s (kubectl/crictl), losetup, systemctl, journalctl and docker in the #61 W6 harness.
State: /mock/w6.json (see test_w6.py). The tool name comes from FAKE_NAME (busybox has no `exec -a`).
Anything not implemented fails with rc 97. Failure switches: the `fail` list in the state.
"""
import json
import os
import re
import sys
import time

STATE = '/mock/w6.json'
LOG = '/mock/w6-calls.log'
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
    gen = state['db']['generation']
    pod['metadata']['labels']['anxious.io/generation'] = str(gen)
    pod['metadata']['uid'] = state['podUids'][str(gen)]
    pod['status']['startTime'] = state.get('podStart') or time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    for s in pod['status']['containerStatuses']:
        s['containerID'] = 'containerd://' + state['cids'][str(gen)][s['name']]
    state['pod'] = pod
    state['db']['pod'] = pod['metadata']['uid']
    state['db']['phase'] = 'ready'
    # CRI view of this instance
    sandbox = state['sandbox'][str(gen)]
    for s in pod['status']['containerStatuses']:
        cid = state['cids'][str(gen)][s['name']]
        state['cri'][cid] = {'status': {'imageRef': s['imageID'], 'labels': {
            'io.kubernetes.pod.uid': pod['metadata']['uid'], 'io.kubernetes.pod.namespace': 'codex-runner',
            'io.kubernetes.container.name': s['name']}}, 'info': {'sandboxID': sandbox}}
        state['ps'][cid] = [{'id': cid, 'imageRef': state['images'][s['imageID']], 'podSandboxId': sandbox}]
    state['sandboxes'][sandbox] = {'status': {'metadata': {'uid': pod['metadata']['uid'], 'name': POD_NAME,
                                                           'namespace': 'codex-runner'}}}


def out_json(value):
    print(json.dumps(value))


def kubectl(state, args):
    ns = opt(args, '-n')
    rest = [a for i, a in enumerate(args) if not (a == '-n' or (i > 0 and args[i - 1] == '-n'))]
    verb = rest[0]
    if failing(state, 'kube-down') and verb != 'exec':
        die()
    if verb == 'get' and rest[1] in ('deployment', 'statefulset'):
        d = state['workloads'].get('%s/%s/%s' % (rest[1], ns, rest[2]))
        if d is None:
            die()
        out_json({'metadata': {'uid': d['uid']}, 'spec': {'replicas': d['replicas']}, 'status': {'readyReplicas': d['ready']}})
        return
    if verb == 'get' and rest[1] == 'pods':
        if '-A' in rest:
            items = ([state['pod']] if state.get('pod') else []) + state.get('otherPods', [])
            out_json({'items': items})
            return
        if ns == 'codex-runner':
            if failing(state, 'pods-list-fail'):
                die()
            items = ([state['pod']] if state.get('pod') else []) + [p for p in state.get('otherPods', [])
                                                                   if p['metadata']['namespace'] == 'codex-runner']
            items += state.get('listExtra', [])
            if opt(rest, '-o') == 'name':
                print('\n'.join('pod/' + p['metadata']['name'] for p in items))
            else:
                out_json({'items': items})
            return
        if ns == 'runner-control':
            out_json({'items': [state['controllerPod']]})
            return
        die(97)
    if verb == 'get' and rest[1] == 'pod' and rest[2] == POD_NAME:
        if state.get('podGetSleep'):
            time.sleep(state['podGetSleep'])
        if failing(state, 'pod-get-fail'):
            die()
        if failing(state, 'pod-get-badjson'):
            print('{not json')
            return
        maybe_create_pod(state)
        save(state)
        pod = state.get('pod')
        if pod:
            out_json(pod)
        return
    if verb == 'get' and rest[1] == 'pvc':
        obj = state['pvc'].get(rest[2])
        if obj:
            out_json(obj)
        return
    if verb == 'get' and rest[1] == 'pv':
        obj = state['pv'].get(rest[2])
        if obj:
            out_json(obj)
        return
    if verb == 'get' and rest[1] == 'node':
        if failing(state, 'node-down'):
            die()
        if state.get('nodeSleep'):
            time.sleep(state['nodeSleep'])
        out_json(state['node'])
        return
    if verb == 'get' and rest[1] == 'namespace':
        print('\n'.join('namespace/' + n for n in state['namespaces']))
        return
    if verb == 'get' and rest[1] == 'configmap':
        print(state.get('origin', 'http://192.88.99.1|'), end='')
        return
    if verb == 'logs':
        print('\n'.join(state.get('controllerLogs', [])))
        return
    if verb == 'exec' and 'deploy/runner-controller' in rest:
        script = sys.stdin.read()
        state.setdefault('healthProbes', []).append(script)
        assert 'Registry(db).read(OWNER)' in script and '"GET", "/health"' in script
        seq = state.get('healthSeq')
        value = seq.pop(0) if seq else state['health']
        save(state)
        if state.get('healthSleep'):
            time.sleep(state['healthSleep'])
        if value == 'fail' or failing(state, 'health-fail'):
            die()
        value = dict(value)
        if value.get('podUid') == 'CURRENT':
            value['podUid'] = (state.get('pod') or {}).get('metadata', {}).get('uid')
        print(json.dumps(dict(value, probe='health')))
        return
    if verb == 'exec' and 'postgres-0' in rest:
        return psql(state, sys.stdin.read())
    die(97)


def stop5(state):
    return state['db'].get('stop5')


def psql(state, query):
    state.setdefault('queries', []).append(query)
    save(state)
    match = re.search(r'/\*(w6w?|w6r|w6o):(\w+)\*/', query)
    kind, tag = match.group(1), match.group(2)
    if kind == 'w6w':
        assert query.startswith('BEGIN;') and not query.startswith('BEGIN READ ONLY'), query
    else:
        assert query.startswith('BEGIN READ ONLY;'), query
    if failing(state, 'db-fail-' + tag) or failing(state, 'db-down'):
        die()
    db = state['db']
    if tag == 'pre':
        print(json.dumps(db['pre'] | {k: db[k] for k in ('state', 'phase', 'generation', 'revision', 'pod', 'active',
                                                           'ambiguous')}))
    elif tag == 'conv':
        print(json.dumps(db['conv']))
    elif tag == 'live':
        maybe_create_pod(state)
        save(state)
        print(json.dumps({k: db[k] for k in ('state', 'phase', 'generation', 'revision', 'pod', 'active', 'ambiguous')}
                         | {'outbox': 0, 'openStops': db['allOpenStops']}))
    elif tag == 'stop5':
        s = stop5(state)
        print(json.dumps({'n': 1 if s else 0, 'stop': None if not s else {
            'stopId': s['stopId'], 'pod': s['pod'], 'generation': 5, 'bootId': s['bootId'], 'nodeUid': s['nodeUid'],
            'completed': s['completed'], 'version': (s['receipt'] or {}).get('version'),
            'terminal': 'terminal' in (s['receipt'] or {}), 'proofBoot': (s['proof'] or {}).get('bootId'),
            'proofEvidence': (s['proof'] or {}).get('evidence')}}))
    elif tag in ('reg', 'final'):
        if tag == 'final' and state.get('finalChange'):
            db.update(state['finalChange'])
            save(state)
        row = {k: db[k] for k in ('state', 'phase', 'generation', 'revision', 'pod', 'active', 'ambiguous')}
        row.update(lastActivity='2026-11-04T06:00:00+00:00', allOpenStops=db['allOpenStops'],
                   stop5=1 if stop5(state) else 0, stops=db['stops'], entries=db['pre']['entries'],
                   admissions=db['pre']['admissions'], outbox=db['pre']['outbox'], waiting=db['pre']['waiting'],
                   running=db.get('running', 0), conversations=db['conv'], dbEpoch=time.time() + state.get('dbSkew', 0))
        print(json.dumps(row))
    elif tag == 'obs':
        state['obsReads'] = state.get('obsReads', 0) + 1
        for step in list(state.get('timeline', [])):
            if state['obsReads'] >= step['after']:
                db.update(step.get('db', {}))
                if 'pod' in step:
                    state['pod'] = step['pod']
                    state['entered'] = False
                state['timeline'].remove(step)
        save(state)
        reg = {k: db[k] for k in ('state', 'phase', 'generation', 'revision', 'pod', 'active', 'ambiguous')}
        print(json.dumps({'registry': reg, 'stop5': stop5(state), 'stops': db['stops'], 'allOpenStops': db['allOpenStops'],
                          'conversations': db['conv']}))
    elif tag == 'insert':
        token_hash, owner, csrf = re.search(r"VALUES \('([0-9a-f]{64})','([0-9a-f-]{36})','([A-Za-z0-9_-]+)'", query).groups()
        state['sessions'][token_hash] = {'owner': owner, 'csrf': csrf}
        save(state)
        if failing(state, 'insert-lost'):
            die()
        print(json.dumps({'id': token_hash[:16], 'expires': '2026-11-04T07:00:00+00:00'}))
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
    else:
        die(97)


def crictl(state, args):
    if args[0] == 'inspecti':
        got = state['images'].get(args[-1])
        if got is None:
            die(1)
        print(got)
        return
    if args[1:3] != ['-o', 'json'] or '-o' in args[3:]:
        die(1)
    if args[0] == 'inspect':
        info = state['cri'].get(args[3])
        if info is None or len(args) != 4:
            die(1)
        out_json(info)
        return
    if args[0] == 'inspectp':
        box = state['sandboxes'].get(args[3])
        if box is None or len(args) != 4:
            die(1)
        out_json(box)
        return
    if args[0] == 'ps':
        assert args[3] == '--id' and len(args) == 5
        out_json({'containers': state['ps'].get(args[4], [])})
        return
    die(97)


def losetup(state, args):
    assert args[:3] == ['-J', '-l', '-O'], args
    if failing(state, 'losetup-fail'):
        die()
    out_json({'loopdevices': state['loops']})


def systemctl(state, args):
    if args[0] == 'reboot':
        state['rebootCalls'] = state.get('rebootCalls', 0) + 1
        save(state)
        if state.get('rebootSleep'):
            time.sleep(state['rebootSleep'])
        die(state.get('rebootRc', 0))
    if args[0] == 'show':
        u = state['units'].get(args[1], {})
        props = [args[i + 1] for i, a in enumerate(args) if a == '-p']
        print('\n'.join('%s=%s' % (p, u.get(p, '')) for p in props))
        return
    die(97)


def journalctl(state, args):
    if failing(state, 'journal-fail'):
        die(1)
    if args[:2] == ['-u', 'k3s']:
        if state.get('journalSleep'):
            time.sleep(state['journalSleep'])
        line = json.dumps({'MESSAGE': 'k3s line', '_BOOT_ID': state['bootHex']}) + '\n'
        n = max(1, state.get('k3sJournalBytes', 2000) // len(line))
        sys.stdout.write(line * n)
        return
    if '-u' in args and opt(args, '-u') == 'issue16-restore.service':
        for e in state['journal'].get('restore', []):
            print(json.dumps(e))
        return
    if '_COMM=sudo' in args:
        for e in state['journal'].get('sudo', []):
            print(json.dumps(e))
        return
    if '_COMM=sshd' in args:
        for e in state['journal'].get('sshd', []):
            print(json.dumps(e))
        return
    die(97)


def docker(state, args):
    if args[:2] == ['image', 'inspect'] and state.get('restoreImage'):
        print(args[-1])
        return
    die(1)


def main():
    tool, args = os.environ.get('FAKE_NAME', ''), sys.argv[1:]
    with open(LOG, 'a') as stream:
        stream.write(tool + ' ' + ' '.join(args) + '\n')
    state = load()
    if tool == 'k3s' and args[0] == 'kubectl':
        return kubectl(state, args[1:])
    if tool == 'k3s' and args[0] == 'crictl':
        return crictl(state, args[1:])
    if tool == 'losetup':
        return losetup(state, args)
    if tool == 'systemctl':
        return systemctl(state, args)
    if tool == 'journalctl':
        return journalctl(state, args)
    if tool == 'docker':
        return docker(state, args)
    die(97)


if __name__ == '__main__':
    main()
