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


def test_a_callback_transport_error_logs_its_class_only(native, turn, lines, monkeypatch, caplog):
    callback(monkeypatch, raises=httpx.ConnectTimeout(SECRET))
    native.reply_tool(call(), turn)
    line, = lines()
    assert (line['stage'], line['success'], line['status'], line['code'], line['exception']) == (
        'callback', False, None, 'not_ready', 'ConnectTimeout')
    assert SECRET not in caplog.text


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
        'callback_body', 502, 'not_ready', 'JSONDecodeError')


def test_a_turn_mismatch_and_a_scope_refusal(native, turn, lines, monkeypatch):
    native.reply_tool(call(turnId='other'), turn)
    monkeypatch.setattr(runner_app, 'tool_allowed', lambda scope, name, arguments: False)
    native.reply_tool(call(), turn)
    first, second = lines()
    assert (first['stage'], first['code'], first['status']) == ('turn_mismatch', 'native_turn_mismatch', None)
    assert (second['stage'], second['code'], second['status']) == ('scope', 'tool_not_allowed', None)


def test_odd_names_and_codes_are_bucketed(native, turn, lines, monkeypatch, caplog):
    callback(monkeypatch, 400, {'error': {'code': 'Has Spaces ' + SECRET, 'message': 'x'}})
    native.reply_tool(call(tool='Research List ' + SECRET), turn)
    line, = lines()
    assert line['tool'] == 'other' and line['code'] == 'other'
    assert SECRET not in caplog.text


def test_the_logger_never_raises():
    log_tool_reply(None, None, {}, 1.0)
    log_tool_reply({'params': 'odd'}, {'request': None}, {'status': 'x', 'code': 5, 'exception': 7}, 2.0)
