"""Unit transport/auth fences with a fake Native and mock HTTP; not C1/C2 evidence."""
import io
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from runner.research_bridge import ResearchBridge
from runner.research_mcp import answer, read_grant, read_message, serve


@pytest.fixture
def bridge(tmp_path):
    native = SimpleNamespace(mapping={}, closing=False, blocked_reason=None,
        active_session=None, completed={}, auth=None, callback_url='http://api-internal:8081/api/internal/research/tools',
        check_binding=lambda: None)
    adapter = ResearchBridge(native, tmp_path)
    yield adapter
    adapter.close()


def arm(bridge, scope=None):
    session, request = str(uuid4()), str(uuid4())
    bridge.prepare(session, request, scope or {'page': {'kind': 'general'}})
    turn = {'session': session, 'request': request, 'thread': 'native-thread', 'turnId': 'native-turn',
            'token': 'private-callback-token-which-must-never-reach-the-child',
            'timing': {'toolCalls': 0, 'toolMs': 0}}
    bridge.native.active_session = session
    bridge.native.mapping[session] = {'state': 'running', 'request': request, 'thread': turn['thread']}
    bridge.bind(turn)
    return turn, read_grant(bridge.grant)


def call(grant, **changes):
    return {'operation': 'call', 'capability': grant['capability'], 'callId': 'call-1',
            'name': 'research_list', 'arguments': {'type': 'document'}, **changes}


def test_child_grant_has_no_callback_or_user_credential(bridge):
    turn, grant = arm(bridge)
    assert set(grant) == {'socket', 'capability'}
    assert turn['token'] not in json.dumps(grant)
    assert turn['token'] not in json.dumps(bridge.config())


def test_old_generation_cannot_borrow_the_next_request(bridge):
    _, old = arm(bridge)
    old_path = bridge.grant
    _, new = arm(bridge)
    assert new['capability'] != old['capability'] and not old_path.exists()
    assert bridge.dispatch(call(old))['error']['code'] == 'native_turn_mismatch'


@pytest.mark.parametrize('change', ['idle', 'different_request', 'different_thread', 'completed', 'closing'])
def test_active_native_binding_is_required_before_callback(bridge, monkeypatch, change):
    turn, grant = arm(bridge)
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: pytest.fail('Refused call reached HTTP'))
    entry = bridge.native.mapping[turn['session']]
    if change == 'idle': entry['state'] = 'idle'
    if change == 'different_request': entry['request'] = str(uuid4())
    if change == 'different_thread': entry['thread'] = 'another-native-thread'
    if change == 'completed': bridge.native.completed[(turn['thread'], turn['turnId'])] = {}
    if change == 'closing': bridge.native.closing = True
    assert bridge.dispatch(call(grant))['error']['code'] == 'native_turn_mismatch'


def test_public_catalog_and_call_never_grant_owner_access(bridge, monkeypatch):
    _, grant = arm(bridge, {'page': {'kind': 'public_document'}, 'context': {'materials': []}})
    listing = bridge.dispatch({'operation': 'catalog', 'capability': grant['capability']})
    assert [tool['name'] for tool in listing['tools']] == ['research_page_context']
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: pytest.fail('Public owner call reached HTTP'))
    assert bridge.dispatch(call(grant))['error']['code'] == 'tool_not_allowed'


def test_valid_call_uses_runner_authority_and_fixed_internal_destination(bridge, monkeypatch):
    turn, grant = arm(bridge)
    received = []
    client_type = httpx.Client
    def receive(request):
        received.append(request)
        return httpx.Response(200, json={'items': [], 'nextCursor': None})
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: client_type(
        **kwargs, transport=httpx.MockTransport(receive)))
    assert bridge.dispatch(call(grant)) == {'items': [], 'nextCursor': None}
    assert len(received) == 1
    body = json.loads(received[0].content)
    assert body['sessionId'] == turn['session'] and body['requestId'] == turn['request']
    assert str(received[0].url) == bridge.native.callback_url
    assert received[0].headers['authorization'] == 'Bearer ' + turn['token']
    assert turn['timing']['toolCalls'] == 1


def test_retained_benchmark_disables_calls_even_with_a_valid_active_capability(bridge, monkeypatch):
    _, grant = arm(bridge)
    bridge.calls_disabled = True
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: pytest.fail('Benchmark called the API'))
    assert bridge.dispatch(call(grant))['error']['code'] == 'tool_not_allowed'
    assert bridge.dispatch({'operation': 'catalog', 'capability': grant['capability']})['tools']


def test_stdio_initialization_unsupported_method_and_notifications_are_bounded():
    messages = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2024-11-05'}},
                {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                {'jsonrpc': '2.0', 'id': 2, 'method': 'unknown'}]
    source = io.BytesIO(b''.join(json.dumps(message).encode() + b'\n' for message in messages))
    destination = io.BytesIO()
    serve({}, source, destination)
    responses = [json.loads(line) for line in destination.getvalue().splitlines()]
    assert len(responses) == 2
    assert responses[0]['result']['serverInfo']['name'] == 'anxious-research'
    assert responses[1]['error']['code'] == -32601
    with pytest.raises(ValueError): read_message(io.BytesIO(b'{}'))


def test_real_stdio_child_adapter_reaches_the_protected_socket(bridge):
    # Real local stdio/socket adapter; API/native/provider layers remain fake.
    _, grant = arm(bridge)
    catalog = answer({'id': 1, 'method': 'tools/list'}, grant)
    assert any(tool['name'] == 'research_post_save' for tool in catalog['tools'])
    bridge.revoke()
    result = answer({'id': 2, 'method': 'tools/call', 'params': {'name': 'research_list', 'arguments': {}}}, grant)
    assert result['isError'] is True
    assert result['structuredContent']['error']['code'] == 'native_turn_mismatch'
