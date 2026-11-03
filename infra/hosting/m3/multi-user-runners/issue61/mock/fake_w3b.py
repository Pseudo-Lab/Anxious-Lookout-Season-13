"""Stand-in for k3s (kubectl/crictl), docker, systemctl and curl in the #61 W3b harness. State: /mock/w3b.json.

The tool container (`docker run … -m ops.runner_binding_image report|apply`) is emulated after
backend/ops/runner_binding_image.py: the same fence/pending/proof/plan-digest guards, the same report, the apply
CAS against the mounted report, and read_private's file checks. Anything not implemented fails with rc 97.
Failure switches: the `fail` list in the state (see test_w3b.py).
"""
import base64
import hashlib
import json
import os
import re
import stat
import sys
import time

STATE = '/mock/w3b.json'
LOG = '/mock/w3b-calls.log'


def load():
    with open(STATE) as stream:
        return json.load(stream)


def save(state):
    temp = STATE + '.tmp'
    with open(temp, 'w') as stream:
        json.dump(state, stream)
    os.replace(temp, STATE)


def die(rc=1, text=''):
    if text:
        sys.stderr.write(text + '\n')
    sys.exit(rc)


def opt(args, flag):
    if flag in args:
        return args[args.index(flag) + 1]
    for arg in args:
        if arg.startswith(flag + '='):
            return arg.split('=', 1)[1]
    return None


def failing(state, name):
    return name in state.get('fail', [])


def digest(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def clients(state):
    """Sessions on hosting: api (1 per Pod) and the controller (3 per Pod: controller + 2 observer), plus extras."""
    count, roles = state.get('extraClients', 0), set(state.get('extraRoles', []))
    for key, role, per in (('m2-hosting/api', ['anxious_api'], 1),
                           ('runner-control/runner-controller', ['r4_controller', 'r4_observer'], 3)):
        live = state['pods'].get(state['deployments'][key]['label'], [])
        if live:
            count += per * len(live)
            roles.update(role)
    return {'clients': count, 'roles': ','.join(sorted(roles))}


def kubectl(state, args):
    ns = opt(args, '-n')
    rest = [a for i, a in enumerate(args) if not (a == '-n' or (i > 0 and args[i - 1] == '-n'))]
    verb = rest[0]
    if verb == 'get' and rest[1] == 'deployment':
        d = state['deployments'].get('%s/%s' % (ns, rest[2]))
        if d is None or failing(state, 'get-' + rest[2]):
            die()
        print(json.dumps({'metadata': {'uid': d['uid'], 'name': rest[2]}, 'spec': {'replicas': d['replicas']},
                          'status': {'readyReplicas': d['ready'], 'replicas': d['current']}}))
        return
    if verb == 'get' and rest[1] == 'pods':
        label = opt(rest, '-l')
        if failing(state, 'pods-fail'):
            die()
        names = state['pods'].get(label, []) if label else state['runnerPods'] if ns == 'codex-runner' else []
        print('\n'.join(names))
        return
    if verb == 'get' and rest[1:3] == ['pod', 'postgres-0']:
        print(state['podIP'], end='')
        return
    if verb == 'get' and rest[1:3] == ['secret', 'hosting-ops']:
        value = 'garbage' if failing(state, 'dsn-bad') else state['dsn']
        print(base64.b64encode(value.encode()).decode(), end='')
        return
    if verb == 'patch' and rest[1] == 'deployment':
        key = '%s/%s' % (ns, rest[2])
        d = state['deployments'][key]
        patch = json.loads(opt(rest, '-p'))
        replicas = patch['spec']['replicas']
        if patch['metadata']['uid'] != d['uid'] or failing(state, 'scale%d-%s' % (replicas, rest[2])):
            die()
        d['replicas'] = replicas
        if replicas == 0:
            if not failing(state, 'linger-' + rest[2]):
                state['pods'][d['label']] = []
                d['ready'] = d['current'] = 0
            for change in state.pop('onScale0-' + rest[2], []):
                if 'fail' in change:
                    state['fail'].append(change['fail'])
                else:
                    state['db'][change['owner']][change['field']] = change['value']
        else:
            state['pods'][d['label']] = ['pod/%s-new' % rest[2]]
            d['current'] = replicas
            d['ready'] = 0 if failing(state, 'notready-' + rest[2]) else replicas
        state.setdefault('scaled', []).append('%s=%d' % (rest[2], replicas))
        save(state)
        return
    if verb == 'rollout':
        if failing(state, 'rollout-' + rest[2].split('/')[1]):
            die()
        return
    if verb == 'exec':
        query = sys.stdin.read()
        state.setdefault('queries', []).append(query)
        save(state)
        assert query.startswith('BEGIN READ ONLY;') and query.rstrip().endswith('COMMIT;'), query
        tag = re.search(r'/\*w3b:(\w+)\*/', query).group(1)
        if failing(state, 'db-fail') or failing(state, 'db-fail-' + tag):
            die()
        if tag == 'clients':
            print(json.dumps(clients(state)))
            return
        if tag == 'state':
            for owner in sorted(state['db']):
                print(json.dumps(dict(state['db'][owner], owner=owner)))
            return
    die(97)


def crictl(state, args):
    if args[0] == 'inspecti':
        got = state['images'].get(args[-1])
        if got is None:
            die(1)
        print(got)
        return
    die(97)


# The tool ---------------------------------------------------------------------------------------------
def private_file(path):
    parent = os.lstat(os.path.dirname(path))
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != 0 or parent.st_mode & 0o077:
        die(1, 'Binding image change refused')
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077 or info.st_nlink != 1:
        die(1, 'Binding image change refused')
    with open(path) as stream:
        return stream.read()


def upgrade(state, config):
    """runner_binding_image.upgrade's guards and report (no write)."""
    row = state['db'][config['owner']]
    plan = row['plan']
    if (row['state'] != config['state'] or row['generation'] != config['generation']
            or row['revision'] != config['revision'] or row['phase'] != 'idle_stopped' or row['pod'] is not None
            or row['ambiguous'] or row['active'] or any(row[k] for k in ('outbox', 'entries', 'stops', 'admissions'))
            or row['proof'] < 1 or row['bindingState'] != row['state'] or digest(plan) != config['oldPlanSha256']):
        die(1, 'Binding image change refused')
    updated = dict(plan, image=config['newImage'])
    updated['executor'] = dict(plan['executor'], image=config['executorImage'])
    if updated == plan:
        die(1, 'Binding image change refused')
    report = {'owner': config['owner'], 'state': row['state'], 'generation': row['generation'],
              'revision': row['revision'], 'oldImage': plan['image'], 'newImage': config['newImage'],
              'oldPlanSha256': digest(plan), 'newPlanSha256': digest(updated),
              'oldExecutorImage': plan['executor']['image'], 'newExecutorImage': config['executorImage']}
    return report, updated


def tool(state, args, mounts, name):
    action = args[args.index('ops.runner_binding_image') + 1]
    owner_short = os.path.basename(opt(args, '--inputs')).split('-')[1].split('.')[0]

    def host(path):
        for src, dst in mounts:
            if path.startswith(dst + '/'):
                return os.path.join(src, path[len(dst) + 1:])
        die(1, 'unmounted path')
    inputs, report_path = host(opt(args, '--inputs')), host(opt(args, '--report'))
    dsn = private_file(host('/private/admin-dsn'))
    assert dsn.startswith('postgresql') and '@%s:' % state['podIP'] in dsn, 'admin DSN not pointed at the Pod IP'
    config = json.loads(private_file(inputs))
    assert set(config) == {'owner', 'state', 'generation', 'revision', 'oldPlanSha256', 'newImage', 'executorImage'}
    if failing(state, '%s-fail-%s' % (action, owner_short)):
        die(1, 'Binding image change refused')
    report, updated = upgrade(state, config)
    if action == 'report':
        if os.path.lexists(report_path):
            die(1, 'Binding image change refused')
        if failing(state, 'report-diff-' + owner_short):
            report['newPlanSha256'] = '0' * 64
        fd = os.open(report_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(report, stream)
    else:
        if json.loads(private_file(report_path)) != report:
            die(1, 'Binding image change refused')
        state['db'][config['owner']]['plan'] = updated
        state.setdefault('applied', []).append(config['owner'])
        state.update(state.pop('afterApply-' + owner_short, {}))  # e.g. a session that appears afterwards
        save(state)
        if failing(state, 'apply-lost-' + owner_short):
            die(1)
        for flag, seconds in (('apply-slow-', 6), ('apply-timeout-', 30)):
            if failing(state, flag + owner_short):
                time.sleep(seconds)
    print(json.dumps({'owner': config['owner'], 'stage': 'binding-image-' + action, 'result': 'verified'}))


def docker(state, args):
    if failing(state, 'docker-fail'):
        die()
    if args[:2] == ['image', 'inspect']:
        got = state['dockerIds'].get(args[-1])
        if got is None:
            die()
        print(got)
        return
    if args[0] == 'ps':
        if failing(state, 'ps-fail'):
            die()
        name = opt(args, '--filter')
        if name:
            name = name.split('=', 1)[1].strip('^$')
            print('running' if name in state['containers'] else '')
        else:
            print('\n'.join(state['containers']))
        return
    if args[0] in ('stop', 'rm'):
        name = args[-1]
        if name in state['containers'] and not failing(state, 'stuck-' + name.split('-')[2] + '-' + name.split('-')[3]):
            state['containers'].remove(name)
            save(state)
        return
    if args[0] == 'run':
        image = args[args.index('--entrypoint') + 2]
        assert image == state['dockerIds']['anxious-dev-runtime:61-5e73bc4'], 'tool must run by the verified Id'
        for flag in ('--rm', '--read-only', '--pull=never'):
            assert flag in args, flag
        assert opt(args, '--user') == '0:0' and opt(args, '--cap-drop') == 'ALL'
        name = opt(args, '--name')
        assert name and name not in state['containers']
        state['containers'].append(name)
        state.setdefault('runs', []).append(name)
        save(state)
        try:
            if opt(args, '--entrypoint') == 'sha256sum':
                assert opt(args, '--network') == 'none'
                for path in args[args.index(image) + 1:]:
                    print('%s  %s' % (state['modules'].get(path, 'missing'), path))
            else:
                assert opt(args, '--network') == 'host' and opt(args, '-e') == 'ADMIN_DATABASE_URL_FILE=/private/admin-dsn'
                mounts = []
                for i, arg in enumerate(args):
                    if arg == '--mount':
                        fields = dict(f.split('=', 1) for f in args[i + 1].split(',') if '=' in f)
                        mounts.append((fields['src'], fields['dst']))
                        if fields['dst'] == '/private' or 'apply' in args:
                            assert args[i + 1].endswith(',readonly'), args[i + 1]
                tool(state, args, mounts, name)
        finally:
            # --rm: the container goes when its process exits (also on a refusal). A killed CLI never gets here,
            # so its container stays, as with real Docker; a "stuck-<action>-<owner>" switch keeps it as well.
            state = load()
            stuck = [f for f in state.get('fail', []) if f.startswith('stuck-')]
            if not any(name.startswith('issue61-w3b-' + f[len('stuck-'):]) for f in stuck):
                state['containers'].remove(name)
            save(state)
        return
    die(97)


def main():
    tool_name, args = os.path.basename(sys.argv[1]), sys.argv[2:]
    with open(LOG, 'a') as stream:
        stream.write(tool_name + ' ' + ' '.join(args) + '\n')
    state = load()
    if tool_name == 'k3s':
        if args[0] == 'kubectl':
            return kubectl(state, args[1:])
        if args[0] == 'crictl':
            return crictl(state, args[1:])
    if tool_name == 'docker':
        return docker(state, args)
    if tool_name == 'systemctl':
        active = state['units'].get(args[-1], 'inactive')
        print(active)
        die(0 if active == 'active' else 3)
    if tool_name == 'curl':
        print(state.get('health', '200'), end='')
        return
    die(97)


if __name__ == '__main__':
    main()
