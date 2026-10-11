import copy
import json
from pathlib import Path
import subprocess

import pytest

import access_dns_proxy_counter as counter
import access_dns_contrast_exec as launch
import access_dns_contrast_runtime as runtime
import access_dns_contrast_scope as scope
from test_access_web_prepare import package
from test_access_runner_dns_prepare import runner_snapshot
from test_access_runner_dns_control_prepare import service_list
from test_access_dns_contrast import policy_data, holder_context, realized_context, result, runtime_context, NONCE


@pytest.fixture
def samples(runner_snapshot, service_list, policy_data):
    values = realized_context(runner_snapshot, service_list, policy_data, 'l7')
    values[6]['status']['identity'] = {'id': 12345}
    values[8][0]['status']['identity'] = {'id': 12345}
    values[7]['status']['containerStatuses'] = [{'name': 'cilium-agent', 'restartCount': 0,
        'state': {'running': {'startedAt': '2026-10-11T00:00:00Z'}}, 'containerID': 'containerd://agent', 'imageID': 'quay.io/cilium@sha256:fixture'}]
    observed = scope.realized(*values)
    job, pod, _, _, receipt = holder_context(runner_snapshot, service_list, policy_data, 'l7')
    bound = counter.binding(job, pod, values[6], values[7], values[2], receipt, observed, NONCE)
    before = copy.deepcopy(values[8])
    for row in before[0]['status']['policy']['proxy-statistics']:
        row['statistics'] = {'requests': {}, 'responses': {}}
    after = copy.deepcopy(before)
    for row in after[0]['status']['policy']['proxy-statistics']:
        row['statistics'] = {'requests': {'received': 2, 'forwarded': 2}, 'responses': {'received': 1, 'forwarded': 1}}
    endpoint_id = bound['pin']['endpointId']
    before_clock = {'mode': 'before', 'endpointId': endpoint_id, 'timeNamespace': 'time:[123]',
                    'beginUpperCs': 150, 'endUpperCs': 190, 'childExit': 0, 'complete': True}
    after_clock = dict(before_clock, mode='after', beginUpperCs=310, endUpperCs=320)
    output = result()
    output['dnsContrastContext'] = {'phase': 'l7', 'jobUid': bound['jobUid'], 'podUid': bound['pin']['podUid'], 'nonce': NONCE,
        'policyUid': bound['policyUid'], 'policyRv': bound['policyRv'], 'policyRevision': bound['policyRevision'], 'startTicks': bound['holderStartTicks']}
    output['counterBudget'] = {'timeNamespace': 'time:[123]', 'startNs': 1_000_000_000, 'enteredNs': 2_000_000_000,
                               'finishedNs': 3_000_000_000, 'afterCutoffNs': 39_000_000_000}
    return before, after, bound, copy.deepcopy(bound), before_clock, after_clock, output, 0


def stats(response):
    return response[0]['status']['policy']['proxy-statistics']


def test_two_unidentified_transport_entries_sum_and_reordering_is_invariant(samples):
    observed = counter.finalize(*samples)
    assert observed['delta']['requests'] == {'received': 4, 'forwarded': 4, 'denied': 0, 'error': 0}
    assert observed['delta']['responses']['received'] == 2
    assert observed['perEntryResetExcluded'] is False
    stats(samples[1]).reverse()
    assert counter.finalize(*samples) == observed
    assert samples[2]['pin']['podIp'] not in json.dumps(observed)


def test_zero_observation_is_not_missing_after_and_does_not_prove_nonreceipt(samples):
    unchanged = (samples[0], copy.deepcopy(samples[0]), *samples[2:])
    output = counter.finalize(*unchanged)
    assert output['delta']['requests']['received'] == 0
    assert output['proxyNotReceivedConclusion'] == 'unknown'
    with pytest.raises((ValueError, KeyError, TypeError)): counter.finalize(samples[0], None, *samples[2:])


@pytest.mark.parametrize('bad', [None, False, True, -1, 1.0, '1', counter.INT64+1])
def test_wrong_scalar_counts_are_unknown(samples, bad):
    stats(samples[1])[0]['statistics']['requests']['received'] = bad
    with pytest.raises(ValueError): counter.finalize(*samples)


@pytest.mark.parametrize('change', ['null_statistics', 'null_requests', 'extra_count', 'extra_entry', 'missing_entry', 'extra_field', 'missing_statistics', 'port', 'identity', 'revision'])
def test_unsupported_shape_is_not_empty_counter_zero(samples, change):
    row = stats(samples[1])[0]
    if change == 'null_statistics': row['statistics'] = None
    elif change == 'null_requests': row['statistics']['requests'] = None
    elif change == 'extra_count': row['statistics']['requests']['other'] = 0
    elif change == 'extra_entry': stats(samples[1]).append(copy.deepcopy(row))
    elif change == 'missing_entry': stats(samples[1]).pop()
    elif change == 'extra_field': row['transport'] = 'UDP'
    elif change == 'missing_statistics': del row['statistics']
    elif change == 'port': row['allocated-proxy-port'] += 1
    elif change == 'identity': samples[1][0]['status']['identity']['id'] += 1
    elif change == 'revision': samples[1][0]['status']['policy']['proxy-policy-revision'] += 1
    with pytest.raises((ValueError, KeyError)): counter.finalize(*samples)


def test_int64_entries_and_larger_exact_integer_aggregate_are_distinct(samples):
    for response in samples[:2]:
        for row in stats(response): row['statistics']['requests'] = {'received': counter.INT64}
    assert counter.snapshot(samples[0], samples[2])[1][0] == counter.TOTAL_MAX
    assert counter.finalize(*samples)['delta']['requests']['received'] == 0


def test_observable_decrease_without_any_monotone_pairing_is_refused(samples):
    for row in stats(samples[0]): row['statistics']['requests'] = {'received': 10, 'forwarded': 10}
    stats(samples[1])[0]['statistics']['requests'] = {'received': 30, 'forwarded': 30}
    stats(samples[1])[1]['statistics']['requests'] = {'received': 1, 'forwarded': 1}
    with pytest.raises(ValueError): counter.finalize(*samples)  # Aggregate grew, but no valid pairing exists.


def test_unidentified_transport_can_hide_real_per_entry_reset_even_with_unique_pairing(samples):
    stats(samples[0])[0]['statistics']['requests'] = {'received': 5, 'forwarded': 5}
    stats(samples[0])[1]['statistics']['requests'] = {}
    stats(samples[1])[0]['statistics']['requests'] = {'received': 4, 'forwarded': 4}
    stats(samples[1])[1]['statistics']['requests'] = {'received': 10, 'forwarded': 10}
    observed = counter.finalize(*samples)
    assert observed['delta']['requests']['received'] == 9
    assert observed['perEntryResetExcluded'] is False  # Counter0's reset can hide behind the other transport.


@pytest.mark.parametrize('key', ['containerId', 'restartCount', 'holderStartTicks', 'endpointIdentity', 'policyRv', 'policyRevision', 'allocatedPort', 'agentInstance'])
def test_context_replacement_or_reset_never_becomes_normal_delta(samples, key):
    samples[3][key] = 'changed'
    with pytest.raises(ValueError): counter.finalize(*samples)


@pytest.mark.parametrize('change', ['transport', 'query_rejected', 'partial', 'wrong_phase', 'after_failed', 'before_late', 'after_early', 'after_late', 'namespace', 'late_exec'])
def test_collection_failure_and_wrong_order_never_become_delta_zero(samples, change):
    values = list(copy.deepcopy(samples))
    if change == 'transport': values[7] = 1
    elif change == 'query_rejected': values[6] = {'dnsContrastExec': 'unknown'}
    elif change == 'partial': del values[6]['callbackService']['absoluteTcp']
    elif change == 'wrong_phase': values[6]['dnsContrastContext']['phase'] = 'l4'
    elif change == 'after_failed': values[5]['complete'] = False
    elif change == 'before_late': values[4]['endUpperCs'] = 210
    elif change == 'after_early': values[5]['beginUpperCs'] = 300  # Lower bound2.99s is before body finish3s.
    elif change == 'after_late': values[5]['endUpperCs'] = 3900
    elif change == 'namespace': values[5]['timeNamespace'] = 'time:[456]'
    elif change == 'late_exec': values[6]['counterBudget'].update(enteredNs=19_000_000_000, finishedNs=20_000_000_000)
    with pytest.raises((ValueError, KeyError)): counter.finalize(*values)


@pytest.mark.parametrize('elapsed,first,allowed', [(17_999_999_999, True, True), (18_000_000_000, True, False),
    (27_999_999_999, False, True), (28_000_000_000, False, False)])
def test_counter_admission_and_finish_reserve_after_budget(elapsed, first, allowed):
    if allowed: runtime.window(1, elapsed+1, first, counter=True)
    else:
        with pytest.raises(ValueError): runtime.window(1, elapsed+1, first, counter=True)


def test_counter_program_is_same_l7_exec_with_tighter_profile(runner_snapshot, service_list, policy_data, samples):
    job, pod, _, _, receipt = holder_context(runner_snapshot, service_list, policy_data, 'l7')
    bound = samples[2]
    original_scope = {key: bound[key] for key in ('phase', 'pin', 'policyUid', 'policyRv', 'policyRevision', 'allocatedPort', 'realizedRowsMatch')}
    body = policy_data[2]['spec']['template']['spec']['containers'][0]['command'][3]
    command = launch.command(job, pod, receipt, original_scope, 'l7', NONCE, body,
        {'bound': bound, 'clock': samples[4], 'endpoint': samples[0]})
    assert command[:3] == ['python', '-B', '-c']
    assert "'counterMode': True" in command[3] and "'counterTimeNamespace': 'time:[123]'" in command[3]


def test_remote_counter_namespace_and_before_end_fail_before_claim(monkeypatch, tmp_path):
    expected = runtime_context(monkeypatch, tmp_path)
    expected.update(counterMode=True, counterTimeNamespace='time:[unmatched]', counterBeforeEndNs=1)
    with pytest.raises(ValueError): runtime.run_once(expected, {})
    assert not list(tmp_path.iterdir())


def test_clock_capture_executes_only_one_numeric_get_and_late_after_skips_it(tmp_path, monkeypatch):
    fake = tmp_path/'cilium-dbg'; called = tmp_path/'called'
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >"'+str(called)+'"\nprintf "[]\\n"\n')
    fake.chmod(0o700)
    monkeypatch.setenv('PATH', str(tmp_path)+':'+__import__('os').environ['PATH'])
    script = Path(__file__).with_name('access_dns_counter_capture.sh')
    before = subprocess.run(['sh', str(script), 'before', '321', '-', '0'], capture_output=True, timeout=6)
    assert before.returncode == 0 and before.stdout == b'[]\n'
    receipt = json.loads(before.stderr)
    assert receipt['endpointId'] == 321 and receipt['complete'] and receipt['endUpperCs'] >= receipt['beginUpperCs']
    assert called.read_text() == 'endpoint get 321 -o json\n'
    called.unlink()
    late = subprocess.run(['sh', str(script), 'after', '321', receipt['timeNamespace'], '1'], capture_output=True, timeout=2)
    assert late.returncode != 0 and not called.exists()
    assert json.loads(late.stderr)['captureAttempted'] is False


def test_slow_after_capture_is_unknown_and_no_second_native_get(tmp_path, monkeypatch):
    fake = tmp_path/'cilium-dbg'; called = tmp_path/'called'
    fake.write_text('#!/bin/sh\nprintf "once\\n" >>"'+str(called)+'"\nsleep 8\n')
    fake.chmod(0o700)
    monkeypatch.setenv('PATH', str(tmp_path)+':'+__import__('os').environ['PATH'])
    script = Path(__file__).with_name('access_dns_counter_capture.sh')
    namespace = str(Path('/proc/self/ns/time').readlink())
    current_cs = int(float(Path('/proc/uptime').read_text().split()[0])*100)
    capture = subprocess.run(['sh', str(script), 'after', '321', namespace, str(current_cs+1000)],
                             capture_output=True, timeout=7)
    assert capture.returncode != 0 and capture.stdout == b''
    receipt = json.loads(capture.stderr)
    assert receipt['complete'] is False and receipt['childExit'] == 124
    assert called.read_text() == 'once\n'
