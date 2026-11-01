"""Stand-in for k3s (kubectl/ctr/crictl), docker and systemctl in the #61 W1/W2 harness. State: /mock/state.json.

Only the commands the gate scripts use are implemented; anything else fails loudly (rc 97) so a script can never
silently depend on an unmocked call. /mock/fail holds stage names to fail (one per line), e.g. "set-image".
"""
import json
import os
import re
import sys
import uuid

STATE = '/mock/state.json'
LOG = '/mock/calls.log'
SINGULAR = {'pods': 'pod', 'pod': 'pod', 'namespace': 'namespace', 'namespaces': 'namespace', 'node': 'node',
            'deployment': 'deployment', 'deployments.apps': 'deployment', 'secret': 'secret', 'secrets': 'secret',
            'serviceaccount': 'serviceaccount', 'serviceaccounts': 'serviceaccount', 'role': 'role',
            'rolebinding': 'rolebinding', 'clusterrole': 'clusterrole', 'clusterrolebinding': 'clusterrolebinding',
            'resourcequota': 'resourcequota', 'ciliumnetworkpolicy': 'ciliumnetworkpolicy',
            'configmaps': 'configmap', 'configmap': 'configmap', 'replicasets.apps': 'replicaset'}
CLUSTER = {'namespace', 'node', 'clusterrole', 'clusterrolebinding'}


def load():
    with open(STATE) as stream:
        return json.load(stream)


def save(state):
    with open(STATE, 'w') as stream:
        json.dump(state, stream)


def failing(stage):
    try:
        with open('/mock/fail') as stream:
            return stage in stream.read().split()
    except FileNotFoundError:
        return False


def die(rc=1):
    sys.exit(rc)


def okey(kind, ns, name):
    kind = SINGULAR.get(kind.lower(), kind.lower())
    return '%s|%s|%s' % (kind, '' if kind in CLUSTER else (ns or 'default'), name)


def opt(args, flag):
    if flag in args:
        return args[args.index(flag)+1]
    for arg in args:
        if arg.startswith(flag+'='):
            return arg.split('=', 1)[1]
    return None


def upsert(state, obj):
    """Create or update; a payload UID is a precondition (the API refuses a write to a replaced object)."""
    k = okey(obj['kind'], obj['metadata'].get('namespace'), obj['metadata']['name'])
    old = state['objects'].get(k)
    want = obj['metadata'].get('uid')
    if want and (old is None or old['metadata']['uid'] != want):
        die()
    obj = json.loads(json.dumps(obj))
    obj['metadata']['uid'] = old['metadata']['uid'] if old else str(uuid.uuid4())
    if obj['kind'] == 'Secret' and 'stringData' in obj:
        obj['data'] = {key: '<set>' for key in obj.pop('stringData')}
    state['objects'][k] = obj
    if obj['kind'] == 'Namespace' and not old:
        for kind, name in (('ServiceAccount', 'default'), ('ConfigMap', 'kube-root-ca.crt')):
            state['objects'][okey(kind, obj['metadata']['name'], name)] = {
                'kind': kind, 'metadata': {'name': name, 'namespace': obj['metadata']['name'], 'uid': str(uuid.uuid4())}}


def kubectl(args):
    state = load()
    ns = opt(args, '-n')
    verb = args[0] if args[0] != '-n' else args[2]
    rest = [a for i, a in enumerate(args) if not (a == '-n' or (i > 0 and args[i-1] == '-n'))]
    verb = rest[0]
    if verb == 'get':
        if '-l' in rest:
            print('\n'.join('pod/'+p for p in state.get('controllerPods', [])))
            return
        if rest[1].count(',') or (ns and rest[1] != 'node' and '-o' in rest and opt(rest, '-o') == 'name'):
            kinds = [SINGULAR.get(k, k) for k in rest[1].split(',')]
            for k, obj in sorted(state['objects'].items()):
                kind, space, name = k.split('|')
                if space == ns and kind in kinds:
                    print('%s/%s' % (kind, name))
            return
        obj = state['objects'].get(okey(rest[1], ns, rest[2]))
        if obj is None:
            if '--ignore-not-found' in rest:
                return
            die()
        print(json.dumps(obj))
        return
    if verb == 'apply':
        stage = state.get('applyStages', {}).get(str(state.get('applies', 0)), 'apply')
        state['applies'] = state.get('applies', 0) + 1
        if failing(stage):
            save(state); die()
        doc = json.load(sys.stdin)
        written = []
        for obj in (doc['items'] if doc.get('kind') == 'List' else [doc]):
            upsert(state, obj)
            k = okey(obj['kind'], obj['metadata'].get('namespace'), obj['metadata']['name'])
            written.append(state['objects'][k])
        save(state)
        if failing(stage + '-lost'):
            die()  # Applied on the server, answer lost.
        print(json.dumps({'kind': 'List', 'items': written}))
        return
    if verb == 'replace':
        if failing('replace'):
            die()
        obj = json.load(sys.stdin)
        for field in ('resourceVersion', 'managedFields'):
            assert field not in obj['metadata'], 'replace payload kept ' + field
        assert obj['metadata'].get('uid'), 'replace payload must carry the backup UID'
        upsert(state, obj)
        save(state)
        return
    if verb == 'create' and rest[1] != 'secret':
        assert opt(rest, '-o') == 'json' and opt(rest, '-f') == '-', rest
        obj = json.load(sys.stdin)
        k = okey(obj['kind'], obj['metadata'].get('namespace'), obj['metadata']['name'])
        race = state.get('foreignBeforeCreate', {}).pop(k, None)
        if race:  # Another actor creates the name after the script's last lookup.
            upsert(state, race)
        if k in state['objects'] or obj['metadata'].get('uid'):
            save(state); die()  # AlreadyExists
        upsert(state, obj)
        save(state)
        if failing('create-' + obj['kind'].lower() + '-lost'):
            die()
        if failing('kill-create-' + obj['kind'].lower()):
            os.kill(os.getppid(), 9)  # The script dies after the server created the object.
            die()
        print(json.dumps(state['objects'][k]))
        return
    if verb == 'create':
        assert rest[1:4] == ['secret', 'generic', 'runner-controller-db'], rest
        if failing('secret'):
            die()
        data = {}
        assert opt(rest, '-o') == 'json', rest
        for arg in [a for a in rest[4:] if a.startswith('--from-file=')]:
            name, path = arg.split('=', 1)[1].split('=', 1)
            with open(path) as stream:
                value = stream.read()
            data[name] = '<set>'
            state.setdefault('dsnHosts', {})[name] = re.sub(r'^.*@', '', value).split('/')[0]
            state.setdefault('dsnTemp', []).append(path)
        obj = {'kind': 'Secret', 'metadata': {'name': 'runner-controller-db', 'namespace': ns, 'uid': str(uuid.uuid4())},
               'data': data}
        state['objects'][okey('secret', ns, 'runner-controller-db')] = obj
        save(state)
        if failing('secret-lost'):
            die()
        print(json.dumps(obj))
        return
    if verb == 'patch':
        patch = json.loads(opt(rest, '-p'))
        obj = state['objects'].get(okey(rest[1], ns, rest[2]))
        if obj is None or patch['metadata']['uid'] != obj['metadata']['uid']:
            die()
        if rest[2] == 'api':
            if failing('set-image'):
                die()
            obj['spec']['template']['spec']['containers'][0]['image'] = \
                patch['spec']['template']['spec']['containers'][0]['image']
            save(state)
            if failing('set-image-lost'):
                die()
        else:
            obj.setdefault('spec', {})['replicas'] = patch['spec']['replicas']
            state['scaled'] = True
            state['controllerPods'] = []
            save(state)
        return
    if verb == 'rollout':
        if failing('rollout-' + rest[2].split('/')[1]):
            die()
        return
    if verb == 'auth':
        sa = opt(rest, '--as').split(':', 2)[2]
        answer = state['cani'].get('%s %s %s' % (rest[2], rest[3], sa), 'no')
        print(answer)
        die(0 if answer == 'yes' else 1)
    if verb == 'logs':
        print('{"owner": "00000000-0000-4000-8000-000000000048", "stage": "controller", "result": "deleting"}')
        print('Traceback: postgresql://user:SECRET@host/db')
        return
    if verb == 'exec':
        query = sys.stdin.read()
        state.setdefault('queries', []).append(query)
        save(state)
        if failing('grants') and 'GRANT' in query:
            die()
        if 'GRANT' in query:
            state['granted'] = query.count('GRANT')
            save(state)
            if failing('grants-lost'):
                die()
            if failing('kill-grants'):
                os.kill(os.getppid(), 9)
                die()
        if 'capacity_wait' in query:
            print(state.get('waiting', 0))
        elif 'runner_termination.stops' in query:
            print('\n'.join(state.get('openStops', [])))
        return
    if verb == 'scale':
        die(97)  # Scaling by name is not allowed: only the UID-preconditioned patch.
    if verb == 'wait':
        return
    if verb == 'api-resources':
        print('pods\nserviceaccounts\nsecrets\nconfigmaps\ndeployments.apps\nreplicasets.apps\nevents')
        return
    if verb == 'delete':
        path = rest[2]
        with open(opt(rest, '-f')) as stream:
            options = json.load(stream)
        parts = path.strip('/').split('/')
        name = parts[-1]
        space = parts[parts.index('namespaces')+1] if 'namespaces' in parts and parts[-2] != 'namespaces' else None
        kind = {'namespaces': 'namespace', 'serviceaccounts': 'serviceaccount', 'secrets': 'secret',
                'deployments': 'deployment', 'roles': 'role', 'rolebindings': 'rolebinding',
                'clusterroles': 'clusterrole', 'clusterrolebindings': 'clusterrolebinding',
                'resourcequotas': 'resourcequota'}[parts[-2]]
        k = okey(kind, space, name)
        obj = state['objects'].get(k)
        if obj is None:
            die()
        if obj['metadata']['uid'] != options['preconditions']['uid']:
            die()
        del state['objects'][k]
        if kind == 'namespace':
            for other in [o for o in state['objects'] if o.split('|')[1] == name]:
                del state['objects'][other]
        state.setdefault('deleted', []).append(k)
        save(state)
        return
    die(97)


def main():
    tool, args = os.path.basename(sys.argv[1]), sys.argv[2:]
    with open(LOG, 'a') as stream:
        stream.write(tool + ' ' + ' '.join(args) + '\n')
    state = load()
    if tool == 'k3s':
        if args[0] == 'kubectl':
            return kubectl(args[1:])
        if args[0] == 'ctr':
            if args[3:5] == ['images', 'import']:
                if failing('import'):
                    die()
                name = os.path.basename(args[5])
                state['images'][state['archives'][name]['ref']] = state['archives'][name]
                save(state)
                return
            if args[3:5] == ['images', 'ls']:
                if failing('ctr-ls'):
                    die()
                if '-q' in args:
                    print('\n'.join(state['images']))
                else:
                    print('REF TYPE DIGEST SIZE PLATFORMS LABELS')
                    for ref, image in state['images'].items():
                        print('%s application/vnd.oci.image.manifest.v1+json %s 1 linux/amd64 -' % (ref, image['digest']))
                return
        if args[0] == 'crictl' and args[1] == 'inspecti':
            image = state['images'].get(args[-1])
            if image is None:
                die()
            print(image['id'])
            return
    if tool == 'docker':
        if args[:2] == ['image', 'inspect']:
            if args[-1] not in state['docker']:
                die()
            print(state['docker'][args[-1]])
            return
        if args[0] == 'load':
            state['docker']['anxious-dev-runtime:61-5e73bc4'] = state['archives']['anxious-dev-runtime-61-5e73bc4.tar']['id']
            save(state)
            return
        if args[0] == 'run':
            if failing('render'):
                die()
            mounts = [args[i+1] for i, a in enumerate(args) if a == '-v']
            parent = [m.split(':')[0] for m in mounts if ':/out-parent' in m][0]
            os.mkdir(os.path.join(parent, 'out'))
            with open('/mock/resources.json') as source, open(os.path.join(parent, 'out', 'resources.json'), 'w') as out:
                out.write(source.read())
            return
    if tool == 'systemctl':
        unit = args[-1]
        load_state, active = state['units'].get(unit, 'loaded inactive').split()
        print('LoadState=%s\nActiveState=%s' % (load_state, active))
        return
    die(97)


if __name__ == '__main__':
    main()
