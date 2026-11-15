"""#94 (2): `runner_tool_reply`, one log line per dynamic tool reply (stage, success, status, code; no content)."""
import json
import logging
import queue
from pathlib import Path

import httpx
import pytest

from runner import app as runner_app
from runner.app import Native, log_tool_reply

SECRET = 'SECRET-ARGUMENT-AND-RESULT-TEXT'


@pytest.fixture
def native(monkeypatch):
    value = Native.__new__(Native)
    value.messages, value.completed, value.next_id = queue.Queue(), {}, 7
    value.auth, value.blocked_reason, value.external = None, None, False
    value.work = Path('/state/workspace')
    value.sent = []
    value.send = value.sent.append
    value.callback_url = 'http://api.invalid:8081/api/internal/research/tools'
    value.check_binding = lambda: None
    monkeypatch.setattr(runner_app, 'tool_allowed', lambda scope, name, arguments: True)
    return value


@pytest.fixture
def turn():
    return {'thread': 'thread', 'turnId': 'turn', 'session': 's', 'request': 'req-1', 'token': 't' * 40,
            'scope': {'page': {'kind': 'document_workspace'}}, 'timing': {'toolCalls': 0, 'toolMs': 0.0}}


@pytest.fixture
def lines(caplog):
    caplog.set_level(logging.INFO, logger='uvicorn.error')

    def read():
        return [json.loads(r.getMessage().partition(' ')[2]) for r in caplog.records
                if r.getMessage().startswith('runner_tool_reply ')]
    return read


def call(tool='research_list', **changes):
    params = {'threadId': 'thread', 'turnId': 'turn', 'callId': 'call-1', 'tool': tool,
              'arguments': {'query': SECRET}}
    params.update(changes)
    return {'id': 11, 'method': 'item/tool/call', 'params': params}


def callback(monkeypatch, status=200, body=None, raises=None):
    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers, json):
            if raises:
                raise raises
            return httpx.Response(status, json=body if body is not None else {'items': [SECRET]},
                                  request=httpx.Request('POST', url))
    monkeypatch.setattr(runner_app.httpx, 'Client', Client)


def test_a_successful_call_logs_success_and_status(native, turn, lines, monkeypatch, caplog):
    callback(monkeypatch)
    native.reply_tool(call(), turn)
    line, = lines()
    assert {k: line[k] for k in ('requestId', 'tool', 'stage', 'success', 'status', 'code', 'exception')} == {
        'requestId': 'req-1', 'tool': 'research_list', 'stage': 'callback_done', 'success': True, 'status': 200,
        'code': None, 'exception': None}
    assert type(line['ms']) is float and turn['timing']['toolCalls'] == 1
    assert SECRET not in caplog.text and 't' * 40 not in caplog.text
    reply, = native.sent
    assert reply['result']['success'] is True  # the reply itself is unchanged


@pytest.mark.parametrize('status, code', [(403, 'tool_not_allowed'), (404, 'not_found'), (403, 'forbidden'),
                                          (503, 'not_ready'), (400, 'validation_error')])
def test_an_api_error_logs_its_status_and_code(native, turn, lines, monkeypatch, caplog, status, code):
    callback(monkeypatch, status, {'error': {'code': code, 'message': SECRET}})
    native.reply_tool(call(), turn)
    line, = lines()
    assert (line['stage'], line['success'], line['status'], line['code']) == ('callback_done', False, status, code)
    assert SECRET not in caplog.text
    assert native.sent[0]['result']['success'] is False


@pytest.mark.parametrize('error, kind', [(httpx.ConnectTimeout(SECRET), 'timeout'),
                                         (httpx.ReadTimeout(SECRET), 'timeout'),
                                         (httpx.ConnectError(SECRET), 'transport'),
                                         (RuntimeError(SECRET), 'other')])
def test_a_callback_error_logs_a_fixed_category_only(native, turn, lines, monkeypatch, caplog, error, kind):
    callback(monkeypatch, raises=error)
    native.reply_tool(call(), turn)
    line, = lines()
    assert (line['stage'], line['success'], line['status'], line['code'], line['exception']) == (
        'callback', False, None, 'not_ready', kind)
    assert SECRET not in caplog.text and type(error).__name__ not in caplog.text


def test_a_non_json_body_is_seen_as_the_body_stage(native, turn, lines, monkeypatch):
    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers, json):
            return httpx.Response(502, text='<html>' + SECRET, request=httpx.Request('POST', url))
    monkeypatch.setattr(runner_app.httpx, 'Client', Client)
    native.reply_tool(call(), turn)
    line, = lines()
    assert (line['stage'], line['status'], line['code'], line['exception']) == (
        'callback_body', 502, 'not_ready', 'invalid_json')


def test_a_turn_mismatch_and_a_scope_refusal(native, turn, lines, monkeypatch):
    native.reply_tool(call(turnId='other'), turn)
    monkeypatch.setattr(runner_app, 'tool_allowed', lambda scope, name, arguments: False)
    native.reply_tool(call(), turn)
    first, second = lines()
    assert (first['stage'], first['code'], first['status']) == ('turn_mismatch', 'native_turn_mismatch', None)
    assert (second['stage'], second['code'], second['status']) == ('scope', 'tool_not_allowed', None)


@pytest.mark.parametrize('tool, code', [
    ('Research List ' + SECRET, 'Has Spaces ' + SECRET),
    # review r0 major: identifier-shaped secrets (lowercase, digits, underscore) are not let through either
    ('private_credential_abc123', 'private_credential_abc123'),
    ('research_list_x', 'not_found_x'),
    ('sk_live_0123456789abcdef', 'token_9f8e7d6c5b4a'),
])
def test_names_and_codes_outside_the_fixed_lists_are_other(native, turn, lines, monkeypatch, caplog, tool, code):
    callback(monkeypatch, 400, {'error': {'code': code, 'message': 'x'}})
    native.reply_tool(call(tool=tool), turn)
    line, = lines()
    assert line['tool'] == 'other' and line['code'] == 'other'
    assert tool not in caplog.text and code not in caplog.text


def test_scope_refused_names_are_other_too(native, turn, lines, monkeypatch, caplog):
    monkeypatch.setattr(runner_app, 'tool_allowed', lambda scope, name, arguments: False)
    native.reply_tool(call(tool='private_credential_abc123'), turn)
    line, = lines()
    assert (line['tool'], line['stage'], line['code']) == ('other', 'scope', 'tool_not_allowed')
    assert 'private_credential_abc123' not in caplog.text


def test_the_fixed_lists_match_their_sources():
    from app.codex_policy import REASONS
    from app.research_tools import TOOLS
    assert runner_app.TOOL_NAMES == set(TOOLS) | {'runner_shell_authorize'}
    assert REASONS <= runner_app.REPLY_CODES


def test_the_logger_never_raises():
    log_tool_reply(None, None, {}, 1.0)
    log_tool_reply({'params': 'odd'}, {'request': None}, {'status': 'x', 'code': 5, 'exception': 7, 'stage': []}, 2.0)
