"""#101 (7) research flag and (1) rolling-replacement target in the rollout renderer. Offline."""
from copy import deepcopy
import json

import pytest
from ops import runner_rollout as rollout
from test_runner_rollout import TEMPLATES, baseline, maps, values  # noqa: F401 (pytest fixtures)

OWNER = 'ff4b718f-de46-4c10-9195-c993eff9ded9'


def rendered(values, baseline, maps):
    files = rollout.render(values, baseline, TEMPLATES, maps)
    api, = json.loads(files['api-candidate.json'])['spec']['template']['spec']['containers']
    controller, = [i for i in json.loads(files['resources.json'])['items']
                   if i['kind'] == 'Deployment' and i['metadata']['name'] == 'runner-controller']
    container, = controller['spec']['template']['spec']['containers']
    keys = set(rollout.RESEARCH_KEYS) | set(rollout.ROLLOUT_KEYS)
    pick = lambda env: {e['name']: e.get('value') for e in env if e['name'] in keys}
    return files, pick(api['env']), pick(container['env'])


def inherited(baseline, maps):
    """A live api that already carries research and rollout keys, inline and through its ConfigMap."""
    base, configmaps = deepcopy(baseline), deepcopy(maps)
    base['spec']['template']['spec']['containers'][0]['env'] += [
        {'name': 'RESEARCH_AGENT_API_ENABLE', 'value': 'true'}, {'name': 'RUNNER_TARGET_IMAGE', 'value': 'stale'}]
    configmaps[0]['data']['CODEX_RUNNER_RESEARCH_API_ENABLE'] = 'true'
    return base, configmaps


def test_research_is_required(values):
    del values['research']
    with pytest.raises(ValueError):
        rollout.checked(values)


@pytest.mark.parametrize('bad', [{}, {'enable': 'true'}, {'enable': True, 'extra': 1}, True])
def test_research_input_is_exact(values, bad):
    values['research'] = bad
    with pytest.raises(ValueError):
        rollout.checked(values)


@pytest.mark.parametrize('enable', [True, False])
def test_research_flag_is_rendered_on_both_deployments_and_inherited_values_dropped(values, baseline, maps, enable):
    values['research'] = {'enable': enable}
    base, configmaps = inherited(baseline, maps)
    _, api, controller = rendered(values, base, configmaps)
    value = 'true' if enable else 'false'
    assert api == {'RESEARCH_AGENT_API_ENABLE': value, 'CODEX_RUNNER_RESEARCH_API_ENABLE': value}
    assert controller == {'CODEX_RUNNER_RESEARCH_API_ENABLE': value}  # The runner key is equal on both.


def test_without_rollout_the_controller_has_no_target(values, baseline, maps):
    files, api, controller = rendered(values, baseline, maps)
    assert not set(controller) & set(rollout.ROLLOUT_KEYS) and not set(api) & set(rollout.ROLLOUT_KEYS)


def test_rollout_target_is_the_image_new_owners_are_prepared_with(values, baseline, maps):
    values['rollout'] = {}
    base, configmaps = inherited(baseline, maps)
    files, api, controller = rendered(values, base, configmaps)
    assert controller['RUNNER_TARGET_IMAGE'] == values['runnerImage'] == json.loads(files['controller-inputs.json'])['image']
    assert not set(api) & set(rollout.ROLLOUT_KEYS)  # The stale inherited api key is dropped.
    values['rollout'] = {'maxParallel': 2, 'turnDeadlineSeconds': 900, 'canarySeconds': 300, 'canaryOwners': [OWNER]}
    _, _, controller = rendered(values, baseline, maps)
    assert {k: v for k, v in controller.items() if k in rollout.ROLLOUT_KEYS} == {
        'RUNNER_TARGET_IMAGE': values['runnerImage'], 'ROLLOUT_MAX_PARALLEL': '2', 'ROLLOUT_TURN_DEADLINE_SECONDS': '900',
        'ROLLOUT_CANARY_SECONDS': '300', 'ROLLOUT_CANARY_OWNERS': OWNER}


@pytest.mark.parametrize('bad', [{'maxParallel': 4}, {'maxParallel': 0}, {'turnDeadlineSeconds': 59}, {'canarySeconds': '600'},
                                 {'canaryOwners': ['not-a-uuid']}, {'canaryOwners': [OWNER, OWNER]}, {'unknown': 1}, []])
def test_rollout_input_is_bounded(values, bad):
    values['rollout'] = bad
    with pytest.raises(ValueError):
        rollout.checked(values)


def edit(files, name, change):
    value = json.loads(files[name])
    change(value)
    return {**files, name: json.dumps(value)}


def controller_env(value):
    deployment, = [i for i in value['items'] if i['kind'] == 'Deployment' and i['metadata']['name'] == 'runner-controller']
    return deployment['spec']['template']['spec']['containers'][0]['env']


@pytest.mark.parametrize('damage', ['api-research', 'api-grant-flag', 'controller-research', 'inputs-image', 'api-target'])
def test_preflight_refuses_a_disagreeing_artifact(values, baseline, maps, damage):
    values['rollout'] = {}
    values['research'] = {'enable': True}
    files = rollout.render(values, baseline, TEMPLATES, maps)
    other = values['runnerImage'][:-1] + ('0' if values['runnerImage'][-1] != '0' else '1')
    if damage in {'api-research', 'api-grant-flag'}:
        name = 'CODEX_RUNNER_RESEARCH_API_ENABLE' if damage == 'api-research' else 'RESEARCH_AGENT_API_ENABLE'
        def change(value):
            for e in value['spec']['template']['spec']['containers'][0]['env']:
                if e['name'] == name:
                    e['value'] = 'false'
        files = edit(files, 'api-candidate.json', change)
        patch = json.loads(files['api-patch.json'])
        patch[2]['value'] = json.loads(files['api-candidate.json'])['spec']['template']['spec']
        files['api-patch.json'] = json.dumps(patch)
    elif damage == 'controller-research':
        files = edit(files, 'resources.json', lambda value: [e.update(value='false') for e in controller_env(value)
                                                            if e['name'] == 'CODEX_RUNNER_RESEARCH_API_ENABLE'])
    elif damage == 'inputs-image':
        files = edit(files, 'controller-inputs.json', lambda value: value.update(image=other))
    else:
        def change(value):
            value['spec']['template']['spec']['containers'][0]['env'].append({'name': 'RUNNER_TARGET_IMAGE', 'value': other})
        files = edit(files, 'api-candidate.json', change)
        patch = json.loads(files['api-patch.json'])
        patch[2]['value'] = json.loads(files['api-candidate.json'])['spec']['template']['spec']
        files['api-patch.json'] = json.dumps(patch)
    with pytest.raises(ValueError):
        rollout.preflight(files, values)
