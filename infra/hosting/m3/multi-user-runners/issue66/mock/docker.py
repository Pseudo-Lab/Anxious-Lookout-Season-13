#!/usr/bin/env python3
"""Stub docker for test_issue66.sh. Images: /mock/docker-images (one ID per line). `run` emulates the #66
worker image: the observer and its diagnostics as built from observer.sh, and ops.runner_decommission."""
import json
import os
import sys
from pathlib import Path

M = Path('/mock')
args = sys.argv[1:]
env = os.environ.get
OWNER = '3cb453bf-d870-40dc-b7e1-3a58aa0cbaf2'
POD_UID = '880bf6cd-70ea-4625-9185-c1604c8f8466'
images = set((M / 'docker-images').read_text().split())


def load(name, default=None):
    p = M / name
    return json.loads(p.read_text()) if p.exists() else default


def save(name, value):
    (M / name).write_text(json.dumps(value))


def say(value, rc=0):
    print(json.dumps(value))
    sys.exit(rc)


if args[:2] == ['image', 'inspect']:
    if args[-1] in images:
        print(args[-1]); sys.exit(0)
    sys.stderr.write('Error: No such image\n'); sys.exit(1)
if args[:2] == ['rm', '-f']:
    sys.exit(0)
assert args[0] == 'run' and '--rm' in args and '--pull=never' in args
with open(M / 'docker.log', 'a') as log:
    log.write(' '.join(args) + '\n')
db, pod = load('db.json', {}), load('pod.json')

if '--entrypoint=python' in args:  # built from observer.sh
    i = args.index('--entrypoint=python')
    image, rest = args[i + 1], args[i + 2:]
    assert image in images, 'observer.sh image not in store'
    assert '--cidfile=/run/issue66-legacy/container-id' in args and not any('issue16-observer/container-id' in a for a in args)
    assert '--idle' not in rest and '--cursor-file' not in rest and '--limit' not in rest
    assert rest[-6:] == ['--inputs', '/private/inputs.json', '--owner', OWNER, '--legacy-pod-uid', POD_UID]
    Path('/run/issue66-legacy/container-id').write_text('c' * 64)
    if rest[:3] == ['-B', '-m', 'ops.runner_observer_diagnostics']:
        code = env('MOCK_DIAG') or ('ready_for_observation' if pod else 'retained_receipt_ready')
        say({'owner': OWNER, 'stage': 'termination-diagnose', 'result': code},
            0 if code in {'ready_for_observation', 'retained_receipt_ready', 'awaiting_pod_deletion', 'no_stop'} else 1)
    assert rest[:3] == ['-B', '/rollout/worker.py', 'observer']
    if env('MOCK_OBSERVE_HANG'):
        sys.exit(124)
    if env('MOCK_OBSERVE') == 'refused':
        say({'owner': OWNER, 'stage': 'termination', 'result': 'reconciliation_required'}, 1)
    if pod and not pod.get('deleting'):
        pod['deleting'] = '2026-11-01T00:00:00Z'; save('pod.json', pod)
        db['stop'] = 'stop s|9|%s|boot|receipt=true|clean=true|completed=-' % POD_UID
    elif pod is None:
        state = db['registry'].split('|')
        db['registry'] = '|'.join([state[0], 'idle_stopped', state[2], str(int(state[3]) + 1), '-'])
    save('db.json', db)
    say({'owner': OWNER, 'stage': 'termination', 'result': 'observed'})

i = args.index('--entrypoint')
assert args[i + 1] == 'python' and args[i + 2] in images and '--network=host' in args
rest = args[i + 3:]
assert rest[:3] == ['-B', '-m', 'ops.runner_decommission'] and rest[4:] == ['--inputs', '/inputs/fence.json', '--export', '/reports/export.json']
action = rest[3]
mounts = {}
for k, a in enumerate(args):
    if a == '--mount':
        o = dict(x.split('=', 1) if '=' in x else (x, True) for x in args[k + 1].split(','))
        mounts[o['dst']] = (Path(o['src']), 'readonly' in o)
assert mounts['/private'][1] and mounts['/inputs'][1] and 'ADMIN_DATABASE_URL_FILE=/private/admin-dsn' in args
fence = json.loads((mounts['/inputs'][0] / 'fence.json').read_text())
export = mounts['/reports'][0] / 'export.json'
stage = 'decommission-' + action
if env('MOCK_TOOL') == 'refuse':
    say({'stage': stage, 'result': 'refused', 'code': 'pending_work', 'observed': {'unconsumedEntries': 1}}, 3)
if action == 'report':
    assert not mounts['/reports'][1] and not export.exists()
    reg = (db.get('registry') or '').split('|')
    if reg[1:2] != ['idle_stopped'] or [fence['state'], str(fence['generation']), str(fence['revision'])] != reg[:1] + reg[2:4]:
        say({'stage': stage, 'result': 'refused', 'code': 'not_idle_stopped', 'observed': {'phase': reg[1:2]}}, 3)
    hexed = fence['state'].replace('-', '')
    value = {'version': 1, 'owner': OWNER, 'state': fence['state'], 'generation': fence['generation'],
             'revision': fence['revision'], 'phase': 'idle_stopped', 'namespace': 'codex-runner',
             'resources': load('export-resources.json'),
             'host': {'storage': env('MOCK_EXPORT_STORAGE') or '/var/lib/anxious-lookout/user-runners/' + hexed,
                      'output': '/var/lib/anxious-lookout/issue16-d4/provision-output/' + OWNER.replace('-', '')}}
    export.write_text(json.dumps(value)); export.chmod(0o600)
    say({'owner': OWNER, 'stage': stage, 'result': 'reported'})
assert mounts['/reports'][1] and export.is_file()
if not db.get('registry'):
    say({'owner': OWNER, 'stage': stage, 'result': 'already_applied'})
db['registry'], db['rows'] = None, 0
save('db.json', db)
if env('MOCK_TOOL') == 'ambiguous':
    sys.exit(1)
say({'owner': OWNER, 'stage': stage, 'result': 'deleted'})
