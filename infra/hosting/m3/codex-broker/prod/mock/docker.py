#!/usr/bin/env python3
"""Stub docker for test_steps.sh: local images in /mock/docker-images as '<id> <release sha>' lines.
`run` emulates what the provisioner image does for B5 and D: /app/release.json, plan_digest, and
ops.runner_binding_image report/apply against /mock/d-registry and /mock/d-plan.json."""
import hashlib
import json
import os
import sys
from pathlib import Path

M = Path('/mock')
args = sys.argv[1:]
env = os.environ.get
images = dict(line.split() for line in (M / 'docker-images').read_text().splitlines() if line.strip())


def digest(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def refused():
    sys.stderr.write('Binding image change refused; preserve plan, runtime and reviewed evidence\n')
    sys.exit(1)


if args[:2] == ['image', 'inspect']:
    if args[-1] in images:
        print(args[-1])
        sys.exit(0)
    sys.stderr.write('Error: No such image\n')
    sys.exit(1)
if args[:1] != ['run']:
    sys.exit(99)
i = args.index('--entrypoint')
entry, image, rest = args[i + 1], args[i + 2], args[i + 3:]
if image not in images or '--pull=never' not in args:
    sys.exit(125)
if entry == 'cat' and rest == ['/app/release.json']:
    print(json.dumps({'sha': images[image], 'builtAt': '2026-11-01T00:00:00Z'}))
    sys.exit(0)
if entry == 'python' and rest[:2] == ['-I', '-c'] and 'plan_digest' in rest[2]:
    assert '--network' in args and args[args.index('--network') + 1] == 'none'
    print(digest(json.load(sys.stdin)))
    sys.exit(0)
if entry == 'python' and 'ops.runner_binding_image' in rest:
    action = rest[rest.index('ops.runner_binding_image') + 1]
    mounts = {}
    for k, a in enumerate(args):
        if a == '--mount':
            o = dict(x.split('=', 1) if '=' in x else (x, True) for x in args[k + 1].split(','))
            mounts[o['dst']] = (Path(o['src']), 'readonly' in o)
    assert '--network=host' in args and mounts['/private'][1] and mounts['/inputs'][1]
    assert 'ADMIN_DATABASE_URL_FILE=/private/admin-dsn' in args and (mounts['/private'][0] / 'admin-dsn').is_file()
    if env('MOCK_TOOL_FAIL'):
        refused()
    config = json.loads((mounts['/inputs'][0] / 'binding-image.json').read_text())
    state, phase, generation, revision, pod = (M / 'd-registry').read_text().strip().split('|')
    plan = json.loads((M / 'd-plan.json').read_text())
    if [config['state'], config['generation'], config['revision'], phase, pod] != [state, int(generation), int(revision), 'idle_stopped', '-'] \
            or digest(plan) != config['oldPlanSha256'] or set(config) != {'owner', 'state', 'generation', 'revision',
                                                                         'oldPlanSha256', 'newImage', 'executorImage'}:
        refused()
    updated = {**plan, 'image': config['newImage'], 'executor': {**plan['executor'], 'image': config['executorImage']}}
    result = {'owner': config['owner'], 'state': state, 'generation': int(generation), 'revision': int(revision),
              'oldImage': plan['image'], 'newImage': config['newImage'], 'oldPlanSha256': digest(plan),
              'newPlanSha256': digest(updated), 'oldExecutorImage': plan['executor']['image'],
              'newExecutorImage': config['executorImage']}
    report = mounts['/reports'][0] / 'binding-image.json'
    if action == 'report':
        if report.exists() or mounts['/reports'][1]:
            refused()
        report.write_text(json.dumps(result))
        report.chmod(0o600)
    else:
        if not mounts['/reports'][1] or json.loads(report.read_text()) != result:
            refused()
        (M / 'd-plan.json').write_text(json.dumps(updated))
        if env('MOCK_TOOL_AMBIGUOUS'):  # committed, then the connection dropped
            sys.exit(1)
    print(json.dumps({'owner': config['owner'], 'stage': 'binding-image-' + action, 'result': 'verified'}))
    sys.exit(0)
sys.exit(99)
