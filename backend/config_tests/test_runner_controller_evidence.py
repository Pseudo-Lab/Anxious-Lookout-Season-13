"""#61 pure parts of the in-cluster controller: dead-runner detection and Node evidence (no DB)."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import json

import httpx
import pytest

from config_tests.test_runner_kube import response
from ops.runner_controller_service import NodeEvidence, Wake, terminal_evidence
from runner.lifecycle import Refused

NOW = datetime(2026, 10, 26, tzinfo=timezone.utc)


def pod(phase='Running', runner=None, executor=None):
    statuses = []
    for name, state in (('runner', runner), ('shell-executor', executor)):
        if state is not None:
            statuses.append({'name': name, 'state': state})
    return {'status': {'phase': phase, 'containerStatuses': statuses}}


RUNNING = {'running': {'startedAt': '2026-10-26T00:00:00Z'}}
EXITED = {'terminated': {'exitCode': 0, 'reason': 'Completed'}}


def test_live_runner_is_not_terminal():
    assert terminal_evidence(pod(runner=RUNNING, executor=RUNNING)) is None
    assert terminal_evidence(pod('Pending')) is None


def test_reboot_failed_pod_is_terminal_with_container_states():
    # The #58 A reboot case: phase Failed, runner Completed, executor exit 255.
    evidence = terminal_evidence(pod('Failed', runner=EXITED, executor={'terminated': {'exitCode': 255, 'reason': 'Unknown'}}))
    assert evidence == {'podPhase': 'Failed', 'containers': {'runner': {'exitCode': 0, 'reason': 'Completed'},
                                                             'shell-executor': {'exitCode': 255, 'reason': 'Unknown'}}}


def test_runner_exit_with_live_executor_is_terminal():
    # The #59 probe case: Pod still Running, runner container terminated, executor alive.
    evidence = terminal_evidence(pod('Running', runner=EXITED, executor=RUNNING))
    assert evidence['podPhase'] == 'Running' and evidence['containers']['shell-executor'] == {'state': 'running'}


def test_absent_pod_is_terminal():
    assert terminal_evidence(None) == {'podAbsent': True}


def node(ready='True', heartbeat=NOW - timedelta(seconds=30), boot=None, uid=None, name='anxious-lookout', deleting=False):
    metadata = {'name': name, 'uid': str(uid or uuid4())}
    if deleting:
        metadata['deletionTimestamp'] = '2026-10-26T00:00:00Z'
    return {'kind': 'Node', 'metadata': metadata,
            'status': {'nodeInfo': {'bootID': str(boot or uuid4())},
                       'conditions': [{'type': 'Ready', 'status': ready,
                                       'lastHeartbeatTime': heartbeat.isoformat().replace('+00:00', 'Z')}]}}


def client(value):
    return httpx.Client(base_url='https://kube.invalid', transport=httpx.MockTransport(
        lambda request: response(200, value) if request.url.path == '/api/v1/nodes/anxious-lookout'
        else response(404, {})))


def test_node_identity_comes_from_the_api():
    boot, uid = uuid4(), uuid4()
    assert NodeEvidence('anxious-lookout').identity(client(node(boot=boot, uid=uid)), NOW) == {
        'nodeName': 'anxious-lookout', 'nodeUid': str(uid), 'bootId': str(boot)}


@pytest.mark.parametrize('value', [node(ready='False'), node(heartbeat=NOW - timedelta(minutes=11)),
                                   node(name='other'), node(deleting=True)])
def test_unready_stale_foreign_or_deleting_node_is_no_evidence(value):
    with pytest.raises(Refused):
        NodeEvidence('anxious-lookout').identity(client(value), NOW)


def watcher(replies):
    def handler(request):
        status, lines = replies.pop(0)
        return httpx.Response(status, content=''.join(json.dumps(line)+'\n' for line in lines).encode())
    return httpx.Client(base_url='https://kube.invalid', transport=httpx.MockTransport(handler))


@pytest.mark.parametrize('status', [401, 403, 500])
def test_watch_http_errors_back_off_without_waking(status):
    wake, sleeps = Wake(), []
    error = {'kind': 'Status', 'code': status}
    wake.watch(watcher([(status, [error])]*3), 'codex-runner', sleep=sleeps.append, rounds=3)
    assert sleeps == [1, 2, 4] and not wake.event.is_set()


def test_watch_error_event_and_empty_stream_back_off_and_pod_events_reset():
    wake, sleeps = Wake(), []
    gone = {'type': 'ERROR', 'object': {'kind': 'Status', 'code': 410}}
    replies = [(200, [gone]), (200, []), (200, [{'type': 'MODIFIED', 'object': {}}]), (200, [gone])]
    wake.watch(watcher(replies[:2]), 'codex-runner', sleep=sleeps.append, rounds=2)
    assert sleeps == [1, 2] and not wake.event.is_set()
    wake.watch(watcher(replies[2:]), 'codex-runner', sleep=sleeps.append, rounds=2)
    assert wake.event.is_set() and sleeps == [1, 2, 1]  # The Pod event reset the backoff.
