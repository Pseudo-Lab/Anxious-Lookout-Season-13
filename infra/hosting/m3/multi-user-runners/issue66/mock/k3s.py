#!/usr/bin/env python3
"""Stub `k3s kubectl` for test_issue66.sh. State: /mock/pod.json, /mock/objects.json ('<kind>/<name>': uid),
/mock/db.json; MOCK_* variables inject failures. Every DB statement is logged to /mock/psql.log."""
import json
import os
import sys
from pathlib import Path

M = Path('/mock')
args = sys.argv[2:]  # drop "kubectl"
env = os.environ.get
OWNER = '3cb453bf-d870-40dc-b7e1-3a58aa0cbaf2'


def load(name, default=None):
    p = M / name
    return json.loads(p.read_text()) if p.exists() else default


def out(text, rc=0):
    sys.stdout.write(text)
    sys.exit(rc)


if '-n' in args:  # namespace flag first, as the scripts write it
    k = args.index('-n'); ns, args = args[k + 1], args[:k] + args[k + 2:]
else:
    ns = None
pod = load('pod.json')
objects = load('objects.json', {})
db = load('db.json', {})

if 'exec' in args and 'postgres-0' in args:
    sql = args[args.index('-c') + 1]
    with open(M / 'psql.log', 'a') as log:
        log.write(sql + '\n')
    if env('MOCK_PSQL_FAIL'):
        sys.exit(1)
    if sql.startswith("select state_id||'|'||phase"):
        out(db['registry'] + '\n' if db.get('registry') else '')
    if sql.startswith("select 'registry '"):
        out('registry ' + db['registry'] + '\n' if db.get('registry') else '')
    if sql.startswith("select 'stop '"):
        out(db.get('stop', '') + '\n')
    if sql.startswith("select 'pending "):
        out('pending entries=0 admissions=0 running_conversations=0\n')
    if 'join auth.sessions' in sql:
        out(str(db.get('entries_valid', 0)) + '\n')
    if sql.startswith("select 'after: registry"):
        out('after: registry ' + (db.get('registry') or 'absent') + '\n')
    if sql.startswith('select (select count(*) from runner_runtime.registry'):
        out(str(db.get('rows', 0)) + '\n')
    if sql.startswith('select count(*) from auth.accounts'):
        out(str(db.get('account', 1)) + '\n')
    sys.exit(98)

if 'exec' in args:  # the runner /health from inside the Pod
    assert args[args.index('-c') + 1] == 'runner' and pod and not pod.get('deleting')
    if env('MOCK_HEALTH_FAIL'):
        sys.exit(1)
    out('health {"ownerId": "%s", "available": false, "reason": "auth_revoked"}\n' % OWNER)

if args[0] == 'get':
    kind, name = args[1], args[2] if len(args) > 2 and not args[2].startswith('-') else None
    if env('MOCK_GET_FAIL') == kind:
        sys.exit(1)
    if kind == 'pods':  # -l anxious.io/owner=...
        out('pod/runner-3cb453bfd87040dcb7e13a58aa0cbaf2\n' if pod else '')
    if kind == 'events':
        out('2026-11-01T00:00:00Z Normal Killing Stopping container runner\n')
    if kind == 'pod':
        if pod is None:
            out('') if '--ignore-not-found' in args else sys.exit(1)
        j = args[args.index('-o') + 1]
        if 'containerStatuses' in j:
            out('container runner ready=true restarts=0 state={"running":{}}\n')
        fields = [pod['uid'], pod.get('deleting', ''), pod.get('phase', 'Running'), pod.get('node', 'anxious-lookout')]
        out('|'.join(fields[:j.count('|') + 1]))
    key = kind + '/' + name
    out(objects.get(key, '') if '--ignore-not-found' in args else '')

if args[0] == 'delete' and args[1] == '--raw':
    path, body = args[2], json.loads(Path(args[args.index('-f') + 1]).read_text())
    parts = path.strip('/').split('/')
    kind = {'secrets': 'secret', 'persistentvolumeclaims': 'pvc', 'persistentvolumes': 'pv'}[parts[-2]]
    key = kind + '/' + parts[-1]
    assert (kind == 'pv') == (parts[2] == 'persistentvolumes') and (kind == 'pv' or parts[3] == 'codex-runner')
    with open(M / 'deletes.log', 'a') as log:
        log.write(key + ' ' + body['preconditions']['uid'] + '\n')
    if env('MOCK_DELETE_FAIL') == key:
        sys.stderr.write('Error from server (InternalError)\n'); sys.exit(1)
    if key not in objects:
        sys.stderr.write('Error from server (NotFound)\n'); sys.exit(1)
    if objects[key] != body['preconditions']['uid']:
        sys.stderr.write('Error from server (Conflict): UID precondition\n'); sys.exit(1)
    if env('MOCK_STUCK') != key:
        del objects[key]
        (M / 'objects.json').write_text(json.dumps(objects))
    out('{"kind":"Status"}')

if args[0] == 'wait':
    kind, name = [a for a in args if '/' in a and not a.startswith('--')][0].split('/')
    sys.exit(1 if kind + '/' + name in objects else 0)
sys.exit(99)
