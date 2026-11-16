"""Real stdio/Unix child, fake native catalogue/events and mock API HTTP.

No actual Codex/model, DB, deployment or native approval evidence is produced.
"""
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import threading
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from runner.research_authority import AuthorityError
from runner.research_fence import BINDING, MutationFence
from runner.research_persistent import PersistentResearchBridge
from app.research_tools import definitions


@pytest.fixture
def adapter(tmp_path):
    tmp_path.chmod(0o700)
    fence = MutationFence(tmp_path, str(uuid4()), str(uuid4()))
    native = SimpleNamespace(auth=None, mapping={}, closing=False, blocked_reason=None,
        active_session=None, process=SimpleNamespace(pid=os.getpid()),
        callback_url='http://api-internal:8081/api/internal/research/tools', check_binding=lambda: None)
    native.rpc = lambda *_: {'data': [{'name': 'research', 'runtimeStatus': 'connected',
        'toolsError': None, 'tools': {entry['name']: entry for entry in definitions()}}], 'nextCursor': None}
    native.reset_thread_tools = lambda: pytest.fail('Unexpected app-server reset in warm same-thread turn')
    bridge = PersistentResearchBridge(native, tmp_path, fence=fence)
    bridge.process_started()
    child = subprocess.Popen([sys.executable, '-I', '-B', str(Path('/source/runner/research_mcp_persistent.py')),
                              str(bridge.bootstrap.path)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    def send(message):
        child.stdin.write(json.dumps(message).encode() + b'\n')
        child.stdin.flush()
    def receive():
        assert select.select([child.stdout], [], [], 3)[0], 'MCP fixture child did not answer'
        data = child.stdout.readline()
        assert data, 'MCP fixture child exited without answer'
        return json.loads(data)
    send({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize'})
    assert receive()['result']['serverInfo']['version'] == '2'
    assert bridge.registered.wait(2)
    send({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})
    assert {entry['name'] for entry in receive()['result']['tools']} == {entry['name'] for entry in definitions()}
    yield SimpleNamespace(bridge=bridge, native=native, child=child, send=send, receive=receive)
    bridge.close()
    if child.poll() is None:
        child.terminate()
    child.wait(timeout=3)
    for stream in (child.stdin, child.stdout, child.stderr): stream.close()


def arm(fixture, turn_id='native-turn', session=None):
    session, request = session or str(uuid4()), str(uuid4())
    fixture.native.active_session = session
    fixture.native.mapping[session] = {'request': request, 'thread': 'native-thread', 'state': 'running'}
    scope = {'page': {'kind': 'general'}}
    fixture.bridge.prepare(session, request, scope)
    turn = {'thread': 'native-thread', 'turnId': None, 'session': session, 'request': request,
            'token': 'private-original-api-token', 'scope': scope, 'timing': {'toolCalls': 0, 'toolMs': 0}}
    fixture.bridge.before_model(turn)
    turn['turnId'] = turn_id
    fixture.bridge.bind(turn)
    return turn


def frame(turn, name='research_list', arguments=None, item='native-call'):
    return {'jsonrpc': '2.0', 'id': 51, 'method': 'tools/call', 'params': {'name': name,
        'arguments': arguments if arguments is not None else {'type': 'document'},
        '_meta': {'callId': item, 'threadId': turn['thread'],
                  'x-codex-turn-metadata': {'thread_id': turn['thread'], 'turn_id': turn['turnId']}}}}


def event(turn, message):
    params = message['params']
    return {'method': 'item/started', 'params': {'threadId': turn['thread'], 'turnId': turn['turnId'],
        'item': {'id': params['_meta']['callId'], 'type': 'mcpToolCall', 'server': 'research',
                 'tool': params['name'], 'arguments': params['arguments']}}}


def mock_api(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handler)))


def invoke(fixture, turn, message):
    fixture.bridge.note(event(turn, message))
    fixture.send(message)
    return fixture.receive()['result']['structuredContent']


def completed(fixture, turn):
    fixture.bridge.note({'method': 'turn/completed', 'params': {'threadId': turn['thread'],
                        'turn': {'id': turn['turnId'], 'status': 'completed'}}})
    fixture.bridge.revoke()
    fixture.native.active_session = None
    fixture.native.mapping[turn['session']]['state'] = 'failed'


def test_registered_stdio_frame_uses_original_runner_authority(adapter, monkeypatch):
    turn, received = arm(adapter), []
    def handler(request):
        received.append(request)
        return httpx.Response(200, json={'items': []})
    mock_api(monkeypatch, handler)
    assert invoke(adapter, turn, frame(turn)) == {'items': []}
    assert len(received) == 1
    body = json.loads(received[0].content)
    assert body['sessionId'] == turn['session'] and body['requestId'] == turn['request']
    assert received[0].headers['Authorization'] == 'Bearer ' + turn['token']
    assert 'private-original-api-token' not in json.dumps(adapter.bridge.config())
    assert turn['timing']['toolCalls'] == 1


def test_child_frame_before_native_event_waits_without_blocking_reader(adapter, monkeypatch):
    turn, received = arm(adapter), []
    mock_api(monkeypatch, lambda request: (received.append(request) or httpx.Response(200, json={'items': []})))
    message = frame(turn)
    adapter.send(message)
    assert not select.select([adapter.child.stdout], [], [], .02)[0]
    assert received == []
    adapter.bridge.note(event(turn, message))
    assert adapter.receive()['result']['structuredContent'] == {'items': []}
    assert len(received) == 1


@pytest.mark.parametrize('bad', ['missing_meta', 'wrong_thread', 'wrong_turn', 'missing_callId'])
def test_original_metadata_missing_or_mismatch_never_reaches_api(adapter, monkeypatch, bad):
    turn = arm(adapter)
    monkeypatch.setattr(httpx, 'Client', lambda **_: pytest.fail('Invalid metadata reached HTTP'))
    message = frame(turn)
    adapter.bridge.note(event(turn, message))
    if bad == 'missing_meta': message['params'].pop('_meta')
    if bad == 'wrong_thread': message['params']['_meta']['threadId'] = 'other'
    if bad == 'wrong_turn': message['params']['_meta']['x-codex-turn-metadata']['turn_id'] = 'old'
    if bad == 'missing_callId': message['params']['_meta'].pop('callId')
    adapter.send(message)
    assert adapter.receive()['result']['isError']


def test_confirmed_saves_allow_multiple_mutations_in_same_turn(adapter, monkeypatch):
    turn, received = arm(adapter), []
    def handler(request):
        received.append(request)
        assert adapter.bridge.fence.pending()['phase'] == 'execute_intent'
        return httpx.Response(200, json={'saved': {'id': str(uuid4())}})
    mock_api(monkeypatch, handler)
    pid = adapter.child.pid
    for index in range(3):
        result = invoke(adapter, turn, frame(turn, name='research_document_save',
            arguments={'title': 'document', 'content': '# saved', 'mutationId': str(uuid4())}, item=f'call-{index}'))
        assert 'saved' in result
        assert adapter.bridge.fence.pending() is None
    assert len(received) == 3 and adapter.child.pid == pid
    assert adapter.bridge.bootstrap.phase == 'active'


def test_next_warm_turn_reuses_peer_but_rejects_old_frame(adapter, monkeypatch):
    first = arm(adapter)
    received = []
    mock_api(monkeypatch, lambda request: (received.append(request) or httpx.Response(200, json={'items': []})))
    old = frame(first)
    assert invoke(adapter, first, old) == {'items': []}
    peer, child_pid = adapter.bridge.bootstrap.peer, adapter.child.pid
    completed(adapter, first)
    second = arm(adapter, turn_id='next-native-turn', session=first['session'])
    adapter.send(old)
    assert adapter.receive()['result']['isError']
    assert invoke(adapter, second, frame(second)) == {'items': []}
    assert adapter.bridge.bootstrap.peer is peer and adapter.child.pid == child_pid
    assert len(received) == 2
    assert json.loads(received[-1].content)['requestId'] == second['request']


def test_previous_terminal_receipt_never_becomes_new_requests_storage_status(adapter, monkeypatch):
    first = arm(adapter)
    mock_api(monkeypatch, lambda _: httpx.Response(200, json={'saved': {'id': str(uuid4())}}))
    invoke(adapter, first, frame(first, 'research_document_save', {'mutationId': str(uuid4())}))
    assert adapter.bridge.snapshot(first['session'])['researchFence']['phase'] == 'terminal'
    completed(adapter, first)
    second = arm(adapter, turn_id='next-turn', session=first['session'])
    assert adapter.bridge.snapshot(second['session']) == {}


@pytest.mark.parametrize('failure', ['timeout', '5xx', 'invalid_json', 'array'])
def test_uncertain_save_blocks_next_mutation_and_turn_until_matching_settlement(adapter, monkeypatch, failure):
    turn, sent = arm(adapter), []
    def handler(request):
        sent.append(request)
        if failure == 'timeout': raise httpx.ReadTimeout('injected timeout')
        if failure == '5xx': return httpx.Response(503, json={'error': {'code': 'not_ready'}})
        if failure == 'invalid_json': return httpx.Response(200, content=b'{')
        return httpx.Response(200, json=[])
    mock_api(monkeypatch, handler)
    def save(index):
        return frame(turn, 'research_document_save', {'mutationId': str(uuid4()), 'title': 'private'}, item=f'save-{index}')
    invoke(adapter, turn, save(1))
    pending = adapter.bridge.fence.pending()
    assert pending and not adapter.bridge.admission_ready()
    assert invoke(adapter, turn, save(2))['error']['code'] == 'not_ready'
    assert len(sent) == 1
    with pytest.raises(AuthorityError, match='settlement_not_drained'):
        adapter.bridge.settle(turn['session'], {key: pending[key] for key in BINDING}, 'not_applied')
    completed(adapter, turn)
    assert adapter.bridge.snapshot(turn['session'])['researchDrained'] is True
    assert not adapter.bridge.admission_ready()
    adapter.bridge.settle(turn['session'], {key: pending[key] for key in BINDING}, 'not_applied')
    assert adapter.bridge.fence.pending() is None and adapter.bridge.admission_ready()
    assert adapter.native.mapping[turn['session']]['state'] == 'failed'


def test_intent_write_failure_sends_nothing_and_blocks_admission(adapter, monkeypatch):
    turn = arm(adapter)
    monkeypatch.setattr(httpx, 'Client', lambda **_: pytest.fail('Unpersisted intent reached HTTP'))
    monkeypatch.setattr(adapter.bridge.fence, 'begin', lambda *_: (_ for _ in ()).throw(OSError('fsync failed')))
    result = invoke(adapter, turn, frame(turn, 'research_document_save', {'mutationId': str(uuid4())}))
    assert result['error']['code'] == 'not_ready'
    completed(adapter, turn)
    assert not adapter.bridge.admission_ready()


def test_complete_or_interrupt_before_claim_has_dispatch_zero(adapter, monkeypatch):
    turn = arm(adapter)
    monkeypatch.setattr(httpx, 'Client', lambda **_: pytest.fail('Revoked epoch reached HTTP'))
    message = frame(turn)
    adapter.bridge.note(event(turn, message))
    adapter.bridge.revoke()
    adapter.send(message)
    assert adapter.receive()['result']['isError']


def test_native_catalogue_not_proved_means_no_active_epoch(adapter):
    adapter.native.rpc = lambda *_: {'data': [], 'nextCursor': None}
    with pytest.raises(Exception): arm(adapter)
    assert adapter.bridge.epoch is None


def test_registered_peer_exit_revokes_and_interrupts_only_its_original_turn(adapter):
    turn = arm(adapter)
    aborted, evidence = threading.Event(), []
    def abort(*original):
        evidence.append(original)
        aborted.set()
    adapter.native.abort_research_turn = abort
    adapter.child.terminate()
    adapter.child.wait(timeout=3)
    assert aborted.wait(2)
    assert evidence == [(turn['session'], turn['request'], turn['thread'], turn['turnId'])]
    assert adapter.bridge.epoch.revoked
