import copy

import pytest

import access_runner_dns_prepare as direct
import access_runner_dns_control_prepare as control
from test_access_web_prepare import package
from test_access_runner_dns_prepare import runner_snapshot


@pytest.fixture
def service_list():
    return {'apiVersion': 'v1', 'kind': 'List', 'metadata': {'resourceVersion': '1'}, 'items': [
        {'apiVersion': 'v1', 'kind': 'Service', 'metadata': {'namespace': 'codex-trial', 'name': 'runner', 'uid': 'svc-uid'},
         'spec': {'selector': {'app': 'access-web-runner'}}}]}


def changes(left, right, path=()):
    if isinstance(left, dict) and isinstance(right, dict) and left.keys() == right.keys():
        return [item for key in left for item in changes(left[key], right[key], path+(key,))]
    return [] if control.canonical(left) == control.canonical(right) else [path]


def test_only_app_and_job_name_change_other_labels_and_inputs_preserved(runner_snapshot, service_list):
    snapshot, inputs, images = runner_snapshot
    snapshot['runner']['spec']['template']['metadata']['labels']['retained'] = 'same'
    original_values = copy.deepcopy(runner_snapshot)
    baseline = direct.emit(*runner_snapshot)
    job = control.emit(*runner_snapshot, baseline, service_list)
    assert set(changes(baseline, job)) == {('metadata', 'name'), ('spec', 'template', 'metadata', 'labels', 'app')}
    assert job['metadata']['name'] == control.CONTROL_JOB
    assert job['spec']['template']['metadata']['labels'] == {'app': control.CONTROL_APP, 'retained': 'same'}
    assert runner_snapshot == original_values
    assert baseline == direct.emit(*runner_snapshot)


@pytest.mark.parametrize('change', ['image', 'command', 'dns', 'node', 'security', 'other_label', 'namespace', 'deadline', 'type'])
def test_recorded_render_drift_is_refused(runner_snapshot, service_list, change):
    baseline = direct.emit(*runner_snapshot)
    spec = baseline['spec']['template']['spec']
    if change == 'image': spec['containers'][0]['image'] = 'other'
    elif change == 'command': spec['containers'][0]['command'][3] += '\n# drift'
    elif change == 'dns': spec['dnsPolicy'] = 'Default'
    elif change == 'node': spec['nodeSelector']['kubernetes.io/hostname'] = 'other'
    elif change == 'security': spec['securityContext']['runAsUser'] = 0
    elif change == 'other_label': baseline['spec']['template']['metadata']['labels']['extra'] = 'other'
    elif change == 'namespace': baseline['metadata']['namespace'] = 'other'
    elif change == 'deadline': baseline['spec']['activeDeadlineSeconds'] = 60
    elif change == 'type': baseline['spec']['backoffLimit'] = False
    with pytest.raises(ValueError): control.emit(*runner_snapshot, baseline, service_list)


@pytest.mark.parametrize('selector', [{'app': control.CONTROL_APP}, {'retained': 'same'}, {'app': control.CONTROL_APP, 'retained': 'same'}])
def test_any_matching_service_is_refused(runner_snapshot, service_list, selector):
    runner_snapshot[0]['runner']['spec']['template']['metadata']['labels']['retained'] = 'same'
    service_list['items'][0]['spec']['selector'] = selector
    with pytest.raises(ValueError): control.emit(*runner_snapshot, direct.emit(*runner_snapshot), service_list)


@pytest.mark.parametrize('change', ['null_selector', 'bad_selector', 'namespace', 'uid', 'bad_uid', 'rv', 'duplicate', 'missing_items'])
def test_incomplete_or_unsupported_service_metadata_is_refused(runner_snapshot, service_list, change):
    service = service_list['items'][0]
    if change == 'null_selector': service['spec']['selector'] = None
    elif change == 'bad_selector': service['spec']['selector'] = {'app': True}
    elif change == 'namespace': service['metadata']['namespace'] = 'other'
    elif change == 'uid': del service['metadata']['uid']
    elif change == 'bad_uid': service['metadata']['uid'] = True
    elif change == 'rv': service_list['metadata']['resourceVersion'] = ''
    elif change == 'duplicate': service_list['items'].append(copy.deepcopy(service))
    elif change == 'missing_items': del service_list['items']
    with pytest.raises((ValueError, KeyError)): control.emit(*runner_snapshot, direct.emit(*runner_snapshot), service_list)


@pytest.mark.parametrize('selector', [{}, {'app': 'access-web-runner'}, {'app': control.CONTROL_APP, 'other': 'absent'}])
def test_selectorless_or_nonmatching_service_does_not_select_control(runner_snapshot, service_list, selector):
    service_list['items'][0]['spec']['selector'] = selector
    assert control.emit(*runner_snapshot, direct.emit(*runner_snapshot), service_list)['metadata']['name'] == control.CONTROL_JOB
