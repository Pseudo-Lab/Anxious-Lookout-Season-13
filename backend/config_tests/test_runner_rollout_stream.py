"""#94 (9): the rollout renderer keeps the #90 stream flags from its input. Offline; no cluster, Docker or host."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
from ops import runner_rollout as rollout
from test_runner_rollout import TEMPLATES, baseline, maps, values  # noqa: F401 (pytest fixtures)

KEYS = set(rollout.API_STREAM_KEYS)


def rendered(values, baseline, maps):
    files = rollout.render(values, baseline, TEMPLATES, maps)
    api, = json.loads(files['api-candidate.json'])['spec']['template']['spec']['containers']
    controller, = [i for i in json.loads(files['resources.json'])['items']
                   if i['kind'] == 'Deployment' and i['metadata']['name'] == 'runner-controller']
    container, = controller['spec']['template']['spec']['containers']
    pick = lambda env: {e['name']: e.get('value') for e in env if e['name'] in KEYS}
    return files, pick(api['env']), pick(container['env'])


def live(baseline, maps):
    """A baseline that already carries every stream key, inline and through its ConfigMap (the #93 state and more)."""
    base, configmaps = deepcopy(baseline), deepcopy(maps)
    env = base['spec']['template']['spec']['containers'][0]['env']
    env += [{'name': 'RESEARCH_STREAM_ENABLE', 'value': 'true'}, {'name': 'CODEX_RUNNER_STREAM_TEXT', 'value': 'true'},
            {'name': 'CODEX_RUNNER_STREAM_TRACE', 'value': 'false'}]
    configmaps[0]['data']['STREAM_TRACE'] = 'true'
    return base, configmaps


def test_absent_stream_input_is_all_off_and_drops_inherited_keys(values, baseline, maps):
    base, configmaps = live(baseline, maps)
    _, api, controller = rendered(values, base, configmaps)
    assert api == {} and controller == {}  # absent = off; the inherited "true" values are not kept


def test_all_false_renders_exactly_like_absent(values, baseline, maps):
    off = dict(values, stream={'enable': False, 'text': False, 'trace': False})
    assert rollout.render(off, baseline, TEMPLATES, maps) == rollout.render(values, baseline, TEMPLATES, maps)


def test_the_controller_render_without_stream_is_unchanged(values, baseline, maps):
    files, _, controller = rendered(values, baseline, maps)
    deployment, = [i for i in json.loads(files['resources.json'])['items']
                   if i['kind'] == 'Deployment' and i['metadata']['name'] == 'runner-controller']
    names = [e['name'] for e in deployment['spec']['template']['spec']['containers'][0]['env']]
    assert controller == {} and names[-2:] == ['CONTROLLER_DATABASE_URL_FILE', 'OBSERVER_DATABASE_URL_FILE']


@pytest.mark.parametrize('flags, api, controller', [
    # the #93 production state: api enable + text, controller text, no trace
    ({'enable': True, 'text': True, 'trace': False},
     {'RESEARCH_STREAM_ENABLE': 'true', 'CODEX_RUNNER_STREAM_TEXT': 'true'}, {'CODEX_RUNNER_STREAM_TEXT': 'true'}),
    ({'enable': True, 'text': True, 'trace': True},
     {'RESEARCH_STREAM_ENABLE': 'true', 'CODEX_RUNNER_STREAM_TEXT': 'true', 'CODEX_RUNNER_STREAM_TRACE': 'true',
      'STREAM_TRACE': 'true'}, {'CODEX_RUNNER_STREAM_TEXT': 'true', 'CODEX_RUNNER_STREAM_TRACE': 'true'}),
    ({'enable': False, 'text': True, 'trace': False},
     {'CODEX_RUNNER_STREAM_TEXT': 'true'}, {'CODEX_RUNNER_STREAM_TEXT': 'true'}),
    ({'enable': True, 'text': False, 'trace': False}, {'RESEARCH_STREAM_ENABLE': 'true'}, {}),
])
def test_stream_input_sets_both_deployments(values, baseline, maps, flags, api, controller):
    base, configmaps = live(baseline, maps)
    _, got_api, got_controller = rendered(dict(values, stream=flags), base, configmaps)
    assert (got_api, got_controller) == (api, controller)


def test_the_runner_keys_agree_between_api_and_controller(values, baseline, maps):
    for text in (False, True):
        for trace in (False, True):
            _, api, controller = rendered(dict(values, stream={'enable': True, 'text': text, 'trace': trace}),
                                          baseline, maps)
            runner = {k: v for k, v in api.items() if k.startswith('CODEX_RUNNER_STREAM_')}
            assert runner == controller


def test_other_inherited_env_is_unchanged(values, baseline, maps):
    plain = rollout.render(values, baseline, TEMPLATES, maps)
    base, configmaps = live(baseline, maps)
    lived = rollout.render(values, base, TEMPLATES, configmaps)
    strip = lambda files: [e for e in json.loads(files['api-candidate.json'])['spec']['template']['spec']
                           ['containers'][0]['env'] if e['name'] not in KEYS]
    assert strip(plain) == strip(lived)


@pytest.mark.parametrize('stream', [None, 'true', [], {}, {'enable': True, 'text': True},
                                    {'enable': True, 'text': True, 'trace': False, 'extra': False},
                                    {'enable': 'true', 'text': True, 'trace': False},
                                    {'enable': 1, 'text': True, 'trace': False},
                                    {'enable': True, 'text': None, 'trace': False}])
def test_malformed_stream_input_refuses(values, baseline, maps, stream):
    with pytest.raises(ValueError):
        rollout.render(dict(values, stream=stream), baseline, TEMPLATES, maps)


def test_an_unknown_extra_key_still_refuses(values, baseline, maps):
    with pytest.raises(ValueError):
        rollout.render(dict(values, streams={'enable': True, 'text': True, 'trace': True}), baseline, TEMPLATES, maps)


@pytest.mark.parametrize('where', ['api-extra', 'api-missing', 'controller-extra', 'controller-missing'])
def test_preflight_refuses_a_stream_mismatch(values, baseline, maps, where):
    v = dict(values, stream={'enable': True, 'text': True, 'trace': False})
    files = rollout.render(v, baseline, TEMPLATES, maps)
    if where.startswith('api'):
        candidate = json.loads(files['api-candidate.json'])
        env = candidate['spec']['template']['spec']['containers'][0]['env']
        if where == 'api-extra':
            env.append({'name': 'STREAM_TRACE', 'value': 'true'})
        else:
            env[:] = [e for e in env if e['name'] != 'RESEARCH_STREAM_ENABLE']
        files['api-candidate.json'] = json.dumps(candidate)
        patch = json.loads(files['api-patch.json'])
        patch[2]['value'] = candidate['spec']['template']['spec']
        files['api-patch.json'] = json.dumps(patch)
    else:
        resources = json.loads(files['resources.json'])
        deployment, = [i for i in resources['items']
                       if i['kind'] == 'Deployment' and i['metadata']['name'] == 'runner-controller']
        env = deployment['spec']['template']['spec']['containers'][0]['env']
        if where == 'controller-extra':
            env.append({'name': 'CODEX_RUNNER_STREAM_TRACE', 'value': 'true'})
        else:
            env[:] = [e for e in env if e['name'] != 'CODEX_RUNNER_STREAM_TEXT']
        files['resources.json'] = json.dumps(resources)
    with pytest.raises(ValueError):
        rollout.preflight(files, v)


@pytest.mark.parametrize('stream, line', [
    (None, {'stream': {'enable': False, 'text': False, 'trace': False}, 'streamInput': False}),
    ({'enable': True, 'text': True, 'trace': False},
     {'stream': {'enable': True, 'text': True, 'trace': False}, 'streamInput': True}),
])
def test_cli_prints_the_stream_flags(values, baseline, maps, tmp_path, stream, line):
    v = dict(values) if stream is None else dict(values, stream=stream)
    for name, value in [('values', v), ('deployment', baseline), ('configmaps', maps[0])]:
        (tmp_path/(name+'.json')).write_text(json.dumps(value))
    args = [sys.executable, '-B', '-m', 'ops.runner_rollout', '--values', str(tmp_path/'values.json'),
            '--deployment', str(tmp_path/'deployment.json'), '--configmaps', str(tmp_path/'configmaps.json'),
            '--templates', str(TEMPLATES), '--check', '--expected-module-sha256',
            hashlib.sha256(Path(rollout.__file__).read_bytes()).hexdigest()]
    done = subprocess.run(args, capture_output=True, text=True)
    assert done.returncode == 0
    lines = [json.loads(x) for x in done.stdout.splitlines() if x.startswith('{')]
    assert line in lines and done.stdout.rstrip().endswith('no deployment or service changes')
