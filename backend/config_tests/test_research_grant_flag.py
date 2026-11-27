"""#101 decision D2 without a database: a turn's research grant goes only to a runner Pod rendered with the
research flag; otherwise it is sent without the grant, logged with a fixed reason and the credential revoked."""
from contextlib import contextmanager
import ipaddress
import logging
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app import conversations
from app.runner_kube import STREAM_FLAGS
from app.user_runner_runtime import BoundRunner, RESEARCH_FLAG, without_unsupported_grant
from runner.lifecycle import Phase


def pod(flag=None, uid=None):
    env = [{'name': 'APP_ENV', 'value': 'operator-service'}]
    if flag is not None:
        env.append({'name': RESEARCH_FLAG, 'value': flag})
    return {'metadata': {'uid': str(uid or uuid4())}, 'spec': {'containers': [{'name': 'runner', 'env': env}]},
            'status': {'phase': 'Running', 'podIP': '10.42.0.9'}}


GRANT = {'token': 'secret-token', 'sessionId': 's', 'requestId': 'r'}
TURN = {'requestId': 'r', 'text': 'hello', 'researchGrant': GRANT}


def test_flag_name_is_the_rendered_research_flag():
    assert RESEARCH_FLAG in [target for _, target in STREAM_FLAGS]


@pytest.mark.parametrize('flag,omitted', [('true', False), (None, True), ('false', True)])
def test_turn_grant_follows_the_pods_research_flag(flag, omitted):
    body, dropped = without_unsupported_grant('POST', '/sessions/x/turn', dict(TURN), pod(flag))
    assert dropped is omitted
    assert body == ({k: v for k, v in TURN.items() if k != 'researchGrant'} if omitted else TURN)


@pytest.mark.parametrize('method,path,body', [('GET', '/sessions/x/turn', TURN), ('POST', '/sessions/x/interrupt', TURN),
                                              ('POST', '/sessions/x/turn', {'requestId': 'r'}), ('POST', '/sessions/x/turn', None)])
def test_other_calls_are_untouched(method, path, body):
    assert without_unsupported_grant(method, path, body, pod()) == (body, False)


def test_bound_call_sends_the_turn_without_the_grant_and_marks_the_result(monkeypatch):
    owner, state_id, pod_uid = uuid4(), uuid4(), uuid4()
    sent = []
    class Transport:
        def __init__(self, url, token, owner_, state):
            assert url == 'http://10.42.0.9:8080'
        def call(self, method, path, body):
            sent.append(body)
            return {'ownerId': str(owner), 'stateId': str(state_id), 'podUid': str(pod_uid), 'state': 'running'}
    monkeypatch.setattr(conversations, 'Runner', Transport)
    monkeypatch.setattr('app.research_api_budget.checkout', lambda: None)
    monkeypatch.setattr('app.user_runner_runtime.Registry', lambda db: SimpleNamespace(_eligible=lambda owner_: None))
    monkeypatch.setattr('app.runner_termination_control.stop_for', lambda db, state: None)
    live = pod(None, pod_uid)
    @contextmanager
    def begin():
        yield object()
    state = SimpleNamespace(pod_uid=pod_uid, phase=Phase.READY, state_id=state_id)
    runtime = SimpleNamespace(namespace=lambda: None, engine=SimpleNamespace(begin=begin), state=lambda db, owner_: state,
                              pods=SimpleNamespace(_request=lambda *args, **kwargs: live, _pod=lambda obj, plan: SimpleNamespace(uid=pod_uid)),
                              plan=SimpleNamespace(identity=SimpleNamespace(name='runner-x')),
                              network=ipaddress.ip_network('10.42.0.0/16'), token='t'*40)
    result = BoundRunner(runtime, owner).submit(uuid4(), dict(TURN))
    assert sent == [{k: v for k, v in TURN.items() if k != 'researchGrant'}]
    assert result['researchGrantOmitted'] is True
    live['spec']['containers'][0]['env'].append({'name': RESEARCH_FLAG, 'value': 'true'})
    result = BoundRunner(runtime, owner).submit(uuid4(), dict(TURN))
    assert sent[-1] == TURN and 'researchGrantOmitted' not in result


class Sessions:
    def __init__(self, row, fail=False):
        self.row, self.fail = row, fail

    @contextmanager
    def begin(self):
        if self.fail:
            raise RuntimeError('database down')
        yield SimpleNamespace(scalar=lambda query: self.row)


@pytest.mark.parametrize('case', ['same-request', 'newer-request', 'gone', 'db-down'])
def test_omitted_grant_is_logged_with_a_fixed_reason_and_revoked(monkeypatch, caplog, case):
    owner, identity, request = uuid4(), uuid4(), uuid4()
    row = None if case == 'gone' else SimpleNamespace(request_id=request if case == 'same-request' else uuid4())
    revoked = []
    monkeypatch.setattr(conversations.credentials, 'revoke', lambda row_: revoked.append(row_))
    with caplog.at_level(logging.INFO, logger='uvicorn.error'):
        conversations.record_omitted_grant(Sessions(row, fail=case == 'db-down'), owner, identity, request)
    messages = [record.getMessage() for record in caplog.records]
    assert 'research_grant_omitted {"reason": "runner_flag_absent"}' in messages
    assert revoked == ([row] if case == 'same-request' else [])
    assert ('research_grant_omitted {"reason": "revoke_failed"}' in messages) is (case == 'db-down')
    assert all('secret' not in message for message in messages)


@pytest.mark.parametrize('accepted,recorded', [({'state': 'running', 'researchGrantOmitted': True}, True),
                                               ({'state': 'running'}, False), (None, False)])
def test_submit_turn_records_an_omitted_grant(monkeypatch, accepted, recorded):
    calls = []
    monkeypatch.setattr(conversations, 'record_omitted_grant', lambda *args: calls.append(args))
    runner = SimpleNamespace(submit=lambda identity, payload: accepted)
    owner, identity, request = uuid4(), uuid4(), uuid4()
    assert conversations.submit_turn('sessions', runner, owner, identity, request, {'text': 'x'}) == accepted
    assert calls == ([('sessions', owner, identity, request)] if recorded else [])


def test_the_background_launch_sends_turns_through_submit_turn():
    import inspect
    source = inspect.getsource(conversations.register_conversations)
    assert 'submit_turn(sessions, runner, owner, identity, request_id, payload)' in source
    assert 'runner.submit(' not in source
