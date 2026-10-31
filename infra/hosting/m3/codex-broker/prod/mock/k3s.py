#!/usr/bin/env python3
"""Stub `k3s kubectl` for test_steps.sh. State lives in /mock; MOCK_* variables inject failures."""
import hashlib
import json
import os
import sys
from pathlib import Path

M = Path('/mock')
args = sys.argv[2:]  # drop "kubectl"
line = ' '.join(args)
env = os.environ.get


def keys(path):
    data = json.load(open(path))
    items = data['items'] if data.get('kind') == 'List' else [data]
    return {f"{i['kind']}/{i['metadata'].get('namespace', '')}/{i['metadata']['name']}" for i in items}


def applied():
    p = M / 'applied'
    return set(p.read_text().split()) if p.exists() else set()


def out(text, rc=0):
    sys.stdout.write(text)
    sys.exit(rc)


def notfound():
    sys.stderr.write('Error from server (NotFound): not found\n')
    sys.exit(1)


def psql(sql):
    with open(M / 'psql.log', 'a') as log:  # E4 must never reach the database
        log.write(sql.split()[0] + '\n')
    if env('MOCK_PSQL_FAIL'):
        sys.exit(1)
    # D: one owner's registry fence and binding plan.
    if 'from runner_runtime.registry where owner_id=' in sql:
        out((M / 'd-registry').read_text())
    if sql.startswith('select plan::text'):
        out((M / 'd-plan.json').read_text() + '\n')
    if "(plan::jsonb->>'image')||'|'||" in sql:
        p = json.loads((M / 'd-plan.json').read_text())
        out(p['image'] + '|' + p['executor']['image'] + '\n')
    if 'pg_stat_activity' in sql:
        out((M / 'role').read_text())
    if sql.startswith('alter role'):
        if env('MOCK_ALTER_FAIL'):
            sys.exit(1)
        (M / 'role').write_text('false|0')
        out('ALTER ROLE\n')
    if 'rolcanlogin::text from' in sql:
        out((M / 'role').read_text().split('|')[0] + '\n')
    if 'runner_bindings' in sql and sql.startswith("select 'runner-'"):
        new = 'docker.io/library/runner@sha256:' + 'b' * 64
        rows = ['runner-00000000000040008000000000000048|' + (env('MOCK_BINDING_IMAGE') or new),
                'runner-3cb453bfd87040dcb7e13a58aa0cbaf2|old@sha256:' + 'c' * 64]
        out('\n'.join(rows) + '\n')
    if 'runner_bindings' in sql:
        out('00000000-0000-4000-8000-000000000048|runner@sha256:old|-\n')
    if '3cb453bf' in sql:
        out('3cb453bf-0000-4000-8000-000000000001|runner_unreachable|1|1|-\n')
    out('00000000-0000-4000-8000-000000000048|runner_unreachable|1|2|uid48\n')


def broker_pod(jsonpath):
    if env('MOCK_BROKER_ABSENT'):
        notfound()
    fields = [env('MOCK_BROKER_PHASE', 'Running'), env('MOCK_BROKER_READY', 'true')]
    if 'restartCount' in jsonpath:
        fields.append(env('MOCK_BROKER_RESTARTS', '0'))
    if 'imageID' in jsonpath:
        fields.append('runner@sha256:' + 'b' * 64)
    out('|'.join(fields))


ignore = '--ignore-not-found' in args
# Generic lookup failure: any call whose arguments contain MOCK_KFAIL fails like an RBAC refusal,
# after optionally printing partial output.
if env('MOCK_KFAIL') and env('MOCK_KFAIL') in line:
    sys.stdout.write(env('MOCK_KFAIL_PARTIAL', ''))
    sys.stderr.write('Error from server (Forbidden): mock refusal\n')
    sys.exit(1)
if sys.argv[1] == 'crictl':  # B5: `k3s crictl inspecti <ref>` succeeds only for an imported reference
    p = M / 'ctr-images'
    sys.exit(0 if p.exists() and sys.argv[-1] in p.read_text().split() else 1)
DEP = M / 'api-deployment.json'
if 'deployment' in args and 'patch' in args:
    # B5: JSON Patch with test ops on the api Deployment.
    if env('MOCK_PATCH_FAIL'):
        sys.exit(1)
    d = json.loads(DEP.read_text())
    if env('MOCK_PATCH_RV_BUMP'):  # someone changed the Deployment after the precheck read it
        d['metadata']['resourceVersion'] = str(int(d['metadata']['resourceVersion']) + 1)
    for op in json.loads(args[args.index('-p') + 1]):
        *parents, leaf = [int(x) if x.isdigit() else x for x in op['path'].strip('/').split('/')]
        node = d
        for x in parents:
            node = node[x]
        if op['op'] == 'test' and node[leaf] != op['value']:
            sys.stderr.write('error: testing value failed\n')
            sys.exit(1)
        if op['op'] == 'replace':
            node[leaf] = op['value']
    d['metadata']['resourceVersion'] = str(int(d['metadata']['resourceVersion']) + 1)
    DEP.write_text(json.dumps(d))
    out('deployment.apps/api patched\n')
if 'deployment' in args and 'api' in args:
    d = json.loads(DEP.read_text())
    if '-o' in args and args[args.index('-o') + 1] == 'json':
        out(json.dumps(d))
    s = d['spec']['template']['spec']
    out(s['containers'][0]['image'] + '|' + s['initContainers'][0]['image'])
if 'pods' in args and '[?(@.name=="api")]' in line:
    image = json.loads(DEP.read_text())['spec']['template']['spec']['containers'][0]['image']
    out('api-1|Running|' + ('false' if env('MOCK_API_NOT_READY') else 'true') + '|' + image + '\n')
if 'pods' in args and 'status.phase!=Failed,status.phase!=Succeeded' in line:
    rows = [l.split('|') for l in (M / 'runners').read_text().splitlines()]
    out(''.join(r[0] + '\n' for r in rows if r[1] not in ('Failed', 'Succeeded')))
if 'pod' in args and args[args.index('pod') + 1].startswith('runner-'):
    name = args[args.index('pod') + 1]
    p = M / 'd-pods'
    out(('pod/' + name) if p.exists() and name in p.read_text().split() else '')
if args[:1] == ['diff']:
    f = args[args.index('-f') + 1]
    if env('MOCK_DIFF_STDERR'):
        sys.stderr.write('error: unable to recognize\n')
        sys.exit(1)
    if env('MOCK_DIFF_ZERO') or keys(f) <= applied():
        sys.exit(0)
    sys.exit(1)
if args[:1] == ['apply']:
    f = args[args.index('-f') + 1]
    k = keys(f)
    if env('MOCK_APPLY_FAIL') and any(env('MOCK_APPLY_FAIL') in x for x in k):
        sys.exit(1)
    (M / 'applied').write_text(' '.join(sorted(applied() | k)))
    if any('runner-pool-supply' in x for x in k):
        (M / 'rbac-emptied').touch()
    if env('MOCK_CILIUM_CHANGE'):
        (M / 'cilium-changed').touch()
    out('configured\n')
if args[:2] == ['auth', 'can-i']:
    # kubectl answers "no" with exit 1 and "yes" with exit 0.
    if (M / 'rbac-emptied').exists():
        out('no\n', 0 if env('MOCK_CANI_NO_RC0') else 1)
    out('yes\n')
if 'exec' in args:
    cmd = args[args.index('--') + 1:]
    if cmd[0] == 'psql':
        psql(cmd[-1])
    if cmd == ['cat', '/app/release.json'] and 'deploy/api' in args:
        image = json.loads(DEP.read_text())['spec']['template']['spec']['containers'][0]['image']
        sha = 'd026b77212063e8f4eeb8b92aba116b9a9a9a8c6' if 'issue36' in image else env('MOCK_API_RELEASE', 'f' * 40)
        out(json.dumps({'sha': sha, 'builtAt': '2026-11-01T00:00:00Z'}))
    if cmd[:3] == ['codex', 'login', 'status']:
        sys.stdout.write('Logged in using ChatGPT\n')
        sys.exit(int(env('MOCK_LOGIN_RC', '0')))
    if cmd[:2] == ['cat', '/data/broker-events.jsonl']:
        p = M / 'events'
        if p.exists():
            out(p.read_text())
        # Default: one scheduled refresh an hour ago, continuous from the E1 session 100:150 to the file 100:200.
        import time
        out(json.dumps({'at': int(time.time()) - 3600, 'event': 'refresh', 'kind': 'scheduled', 'ok': True,
                        'before': '100:150', 'after': '100:200'}) + '\n')
    if cmd[0] == 'python3' and 'auth.json' in cmd[-1]:
        out(env('MOCK_IDENT', '100:200') + '\n')  # auth.json identity '<inode>:<mtime ns>'
    if cmd[0] == 'stat':
        import time
        mtime = env('MOCK_AUTH_MTIME', str(int(time.time()) - 60))  # rewritten a minute ago
        if cmd[-1].endswith('auth.json'):
            out(mtime if cmd[2] == '%Y' else env('MOCK_AUTH_STAT', '600 10001 1890') + ' ' + mtime)
        out(env('MOCK_HOME_STAT', '700 10001'))
    sys.exit(99)
if args[:2] == ['get', 'ns'] and 'codex-broker' in args:
    if (M / 'ns-exists').exists():
        out('namespace/codex-broker')
    out('') if ignore else notfound()
if 'rollout' in args:
    sys.exit(1 if env('MOCK_ROLLOUT_FAIL') else 0)
if 'delete' in args and 'serviceaccount' in args:
    if env('MOCK_SA_DELETE_FAIL'):
        sys.exit(1)
    (M / 'sa-deleted').touch()
    out('deleted\n')
if 'serviceaccount' in args:
    if (M / 'sa-deleted').exists():
        out('') if ignore else notfound()
    if '-o' in args and args[args.index('-o') + 1] == 'name':
        out('serviceaccount/runner-pool-supply')
    out('runner-pool-supply' if 'jsonpath={.metadata.name}' in line else 'sa-uid')
if 'resourcequota' in args:
    out(json.dumps({'status': {'hard': {'count/pods': '3'}, 'used': {'count/pods': '2'}}}))
if 'cilium-config' in args:
    if '-o' in args and args[args.index('-o') + 1] == 'json':
        changed = (M / 'cilium-changed').exists()
        out(json.dumps({'data': {'enable-l7-proxy': 'true', 'kube-proxy-replacement': 'false', 'x': str(changed)}}))
    out('enable-l7-proxy=true|kube-proxy-replacement=false')
if 'cnp' in args and 'runner-callback-draft' in args:
    out((M / 'live-cnp.json').read_text())
if 'cnp' in args and 'codex-broker' in args:
    out(env('MOCK_CNP_VALID', 'True'))
if 'codex-broker-0' in args:
    broker_pod(line)
if 'pods' in args and '-l' in args and 'anxious.io/managed=user-runner' in args:
    if '[?(@.name=="runner")]' not in line:
        # Production runner Pods also carry the shell executor (F inventory), whose status may come
        # first; an unselected index would read the executor image.
        executor = 'docker.io/library/anxious-s13-issue23-shell-executor@sha256:' + 'e' * 64
        out(''.join(l.rsplit('|', 1)[0] + '|' + executor + '\n' for l in (M / 'runners').read_text().splitlines()))
    out((M / 'runners').read_text())
if 'pods' in args:
    out('runner-x|uid|Running|0|runner@sha256:' + 'b' * 64 + '\n')
if args[:2] == ['get', 'nodes']:
    out('node1|True\n')
if 'storageclass' in args:
    out('local-path|rancher.io/local-path|Delete|WaitForFirstConsumer\n')
if any(a.startswith('role/') for a in args):
    items = [{'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': kind,
              'metadata': {'name': 'runner-pool-supply', **({'namespace': 'codex-runner'} if 'Cluster' not in kind else {}),
                           'uid': 'u', 'resourceVersion': '1'},
              **({'rules': [{'apiGroups': [''], 'resources': ['pods'], 'verbs': ['get']}]} if 'Binding' not in kind else
                 {'subjects': [{'kind': 'ServiceAccount', 'name': 'runner-pool-supply', 'namespace': 'codex-runner'}],
                  'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': kind.replace('Binding', ''), 'name': 'runner-pool-supply'}})}
             for kind in ('Role', 'RoleBinding', 'ClusterRole', 'ClusterRoleBinding')]
    out(json.dumps({'kind': 'List', 'items': items}))
if 'runner-pool-supply' in args and '{.metadata.name}|' in line:
    if (M / 'rbac-deleted').exists():
        out('') if ignore else notfound()
    emptied = (M / 'rbac-emptied').exists()
    out('runner-pool-supply|' + ('' if emptied else '{.rules}' in line and '[{"verbs":["get"]}]' or '[{"kind":"ServiceAccount"}]'))
if 'runner-pool-supply' in args and 'jsonpath={.rules}' in line:
    out('' if (M / 'rbac-emptied').exists() else '[{"verbs":["get"]}]')
if 'runner-pool-supply' in args and 'jsonpath={.subjects}' in line:
    out('' if (M / 'rbac-emptied').exists() else '[{"kind":"ServiceAccount","name":"runner-pool-supply"}]')
sys.stderr.write('stub: unhandled ' + hashlib.sha256(line.encode()).hexdigest()[:8] + ' ' + line + '\n')
sys.exit(99)
