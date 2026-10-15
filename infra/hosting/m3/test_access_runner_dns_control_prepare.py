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


@pytest.mark.parametrize('key', control.JOB_NAME_LABELS)
def test_generated_job_name_service_selector_is_refused(runner_snapshot, service_list, key):
    service_list['items'][0]['spec']['selector'] = {key: control.CONTROL_JOB}
    with pytest.raises(ValueError): control.emit(*runner_snapshot, direct.emit(*runner_snapshot), service_list)


@pytest.mark.parametrize('key', control.JOB_NAME_LABELS)
def test_other_job_name_selector_does_not_select_control(runner_snapshot, service_list, key):
    service_list['items'][0]['spec']['selector'] = {key: 'other-job'}
    assert control.emit(*runner_snapshot, direct.emit(*runner_snapshot), service_list)['metadata']['name'] == control.CONTROL_JOB


@pytest.mark.parametrize('key', control.JOB_UID_LABELS)
def test_unknowable_job_uid_selector_is_refused_even_with_other_condition(runner_snapshot, service_list, key):
    service_list['items'][0]['spec']['selector'] = {key: 'future-controller-uid', 'app': 'other-app'}
    with pytest.raises(ValueError): control.emit(*runner_snapshot, direct.emit(*runner_snapshot), service_list)


@pytest.mark.parametrize('key', control.JOB_NAME_LABELS + control.JOB_UID_LABELS)
def test_stale_controller_labels_in_template_are_not_overwritten(runner_snapshot, service_list, key):
    runner_snapshot[0]['runner']['spec']['template']['metadata']['labels'][key] = 'old-job'
    with pytest.raises(ValueError): control.emit(*runner_snapshot, direct.emit(*runner_snapshot), service_list)


@pytest.mark.parametrize('metadata', [
    {'continue': 'next-page-token', 'remainingItemCount': 1},
    *({'continue': value} for value in (None, False, 0, [], {})),
    *({'remainingItemCount': value} for value in (1, -1, None, False, 0.0, '0', [], {})),
])
def test_known_partial_or_malformed_pagination_is_refused(runner_snapshot, service_list, metadata):
    service_list['metadata'].update(metadata)
    with pytest.raises(ValueError): control.emit(*runner_snapshot, direct.emit(*runner_snapshot), service_list)


def test_explicit_complete_list_pagination_is_accepted(runner_snapshot, service_list):
    service_list['metadata'].update({'continue': '', 'remainingItemCount': 0})
    assert control.emit(*runner_snapshot, direct.emit(*runner_snapshot), service_list)['metadata']['name'] == control.CONTROL_JOB


def test_defaulted_and_actual_labels_can_be_rechecked_without_ignoring_admission_label(service_list):
    labels = {'app': control.CONTROL_APP, **{key: control.CONTROL_JOB for key in control.JOB_NAME_LABELS},
              **{key: 'actual-job-uid' for key in control.JOB_UID_LABELS}, 'admitted': 'value'}
    before = copy.deepcopy(labels)
    control.services_exclude_control(service_list, labels)
    assert labels == before
    service_list['items'][0]['spec']['selector'] = {'admitted': 'value'}
    with pytest.raises(ValueError): control.services_exclude_control(service_list, labels)


def test_observed_kubectl_aggregate_shape_with_empty_list_rv_stays_refused(runner_snapshot, service_list):
    # Observed services.before.json shape; item RV cannot substitute for list snapshot RV.
    service_list['metadata'] = {'resourceVersion': ''}
    service_list['items'][0]['metadata']['resourceVersion'] = 'item-fixture-rv'
    with pytest.raises(ValueError): control.emit(*runner_snapshot, direct.emit(*runner_snapshot), service_list)


@pytest.mark.parametrize('omit_type', [False, True])
def test_complete_typed_api_service_list_accepts_optional_nested_typemeta(runner_snapshot, service_list, omit_type):
    service_list['kind'] = 'ServiceList'
    if omit_type:
        for key in ('kind', 'apiVersion'): del service_list['items'][0][key]
    assert control.emit(*runner_snapshot, direct.emit(*runner_snapshot), service_list)['metadata']['name'] == control.CONTROL_JOB


@pytest.mark.parametrize('change', ['generic_omission', 'wrong_kind', 'wrong_api', 'null_kind', 'null_api', 'empty_rv', 'partial', 'error'])
def test_typed_api_context_never_accepts_conflict_partial_or_error(runner_snapshot, service_list, change):
    service_list['kind'] = 'ServiceList'
    if change == 'generic_omission':
        service_list['kind'] = 'List'
        del service_list['items'][0]['kind']
    elif change == 'wrong_kind': service_list['items'][0]['kind'] = 'Pod'
    elif change == 'wrong_api': service_list['items'][0]['apiVersion'] = 'apps/v1'
    elif change == 'null_kind': service_list['items'][0]['kind'] = None
    elif change == 'null_api': service_list['items'][0]['apiVersion'] = None
    elif change == 'empty_rv': service_list['metadata']['resourceVersion'] = ''
    elif change == 'partial': service_list['metadata'].update({'continue': 'next-page', 'remainingItemCount': 1})
    elif change == 'error': service_list = {'apiVersion': 'v1', 'kind': 'Status', 'status': 'Failure', 'code': 403}
    with pytest.raises(ValueError): control.emit(*runner_snapshot, direct.emit(*runner_snapshot), service_list)
