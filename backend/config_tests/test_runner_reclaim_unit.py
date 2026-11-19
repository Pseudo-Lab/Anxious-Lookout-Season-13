"""#61 R1/R2 receipt shapes and the reclaim command line, without a database."""
import json
from uuid import uuid4

import pytest

from app.runner_termination_control import (drain_body, health_failure_matches, operator_available_receipt,
                                            operator_receipt, receipt_valid, terminal_receipt, unavailable_receipt)
from ops import runner_controller_service as service
from runner.lifecycle import Refused


@pytest.fixture
def stop():
    return {'stop_id': uuid4(), 'owner_id': uuid4(), 'state_id': uuid4(), 'pod_uid': uuid4(), 'generation': 3}


def operator(**changes):
    value = {'health': {'available': False, 'reason': 'auth_revoked', 'verification': 'unverified'},
             'drainStatus': 503, 'at': '2026-11-01T12:00:00+00:00'}
    return {**value, **changes}


def available(**changes):
    value = {'health': {'available': True, 'reason': None, 'verification': 'real'},
             'drainStatus': 409, 'runningConversations': 0, 'at': '2026-11-19T12:00:00+00:00'}
    return {**value, **changes}


def test_every_receipt_kind_is_valid_only_in_its_exact_shape(stop):
    kinds = [{**drain_body(stop), 'version': 1, 'clean': True},
             {**drain_body(stop), 'version': 1, 'clean': False},  # #114 ②: clean is evidence only
             terminal_receipt(stop, {'podAbsent': True}),
             unavailable_receipt(stop, {'reason': 'auth_revoked', 'clean': False}),
             operator_receipt(stop, operator()),
             operator_available_receipt(stop, available())]
    for receipt in kinds:
        assert receipt_valid({**stop, 'receipt': receipt})
        assert not receipt_valid({**stop, 'receipt': {**receipt, 'stopId': str(uuid4())}})
        assert not receipt_valid({**stop, 'receipt': {**receipt, 'extra': 1}})
    assert not receipt_valid({**stop, 'receipt': {**drain_body(stop), 'version': 1, 'clean': None}})
    assert not receipt_valid({**stop, 'receipt': {**drain_body(stop), 'version': 1}})
    assert not receipt_valid({**stop, 'receipt': {**drain_body(stop), 'version': 3, 'unavailable': {'reason': 'not_configured', 'clean': True}}})
    assert not receipt_valid({**stop, 'receipt': {**drain_body(stop), 'version': 5}})
    assert not receipt_valid({**stop, 'receipt': {**drain_body(stop), 'version': 6}})
    # A version 4 (blocked runner) observation never passes as version 5, nor the reverse.
    assert not receipt_valid({**stop, 'receipt': {**operator_receipt(stop, operator()), 'version': 5}})
    assert not receipt_valid({**stop, 'receipt': {**operator_available_receipt(stop, available()), 'version': 4}})
    assert not receipt_valid({**stop, 'receipt': None})


@pytest.mark.parametrize('value', [
    available(runningConversations=1),                                              # a turn may be in flight
    available(runningConversations=True), available(drainStatus=503), available(drainStatus=200),
    available(health={'available': False, 'reason': 'auth_revoked', 'verification': 'real'}),  # that is version 4
    available(health={'available': True, 'reason': 'auth_revoked', 'verification': 'real'}),
    available(health={'available': True, 'reason': None, 'verification': 'other'}),
    available(health={'available': True, 'reason': None}),
    available(at='2026-11-19T12:00:00'),
    {k: v for k, v in available().items() if k != 'runningConversations'},
    {**available(), 'note': 'free text'}, None])
def test_available_operator_receipt_holds_only_its_conditions(stop, value):
    with pytest.raises((Refused, TypeError, ValueError)):
        operator_available_receipt(stop, value)


@pytest.mark.parametrize('value', [
    {'reason': 'not_configured', 'clean': True},  # never supplied drains with version 1
    {'reason': '', 'clean': True}, {'reason': 'x'*64, 'clean': True}, {'reason': 'auth_revoked', 'clean': 1},
    {'reason': 'auth_revoked'}, {'reason': 'auth_revoked', 'clean': True, 'more': 1}, None])
def test_unavailable_receipt_refuses_bad_evidence(stop, value):
    with pytest.raises((Refused, TypeError)):
        unavailable_receipt(stop, value)


@pytest.mark.parametrize('value', [
    operator(drainStatus=200), operator(drainStatus=401), operator(drainStatus=True), operator(at='2026-11-01T12:00:00'),
    operator(health={'available': True, 'reason': None, 'verification': 'real'}),
    operator(health={'available': False, 'reason': 'unavailable', 'verification': 'unverified'}),  # already stopping
    operator(health={'available': False, 'reason': 'auth_revoked'}),
    operator(health={'available': False, 'reason': 'auth_revoked', 'verification': 'unverified', 'token': 'x'}),
    {**operator(), 'note': 'free text'}])
def test_operator_receipt_holds_only_the_observed_refusal(stop, value):
    with pytest.raises((Refused, TypeError, ValueError)):
        operator_receipt(stop, value)


def test_health_must_name_the_same_failure_for_this_runner(stop):
    health = {'ownerId': str(stop['owner_id']), 'stateId': str(stop['state_id']), 'podUid': str(stop['pod_uid']),
              'available': False, 'reason': 'auth_revoked', 'failure': 'auth_revoked'}
    assert health_failure_matches(health, stop, 'auth_revoked')
    assert health_failure_matches({**health, 'reason': 'unavailable'}, stop, 'auth_revoked')  # stopping after the drain
    assert not health_failure_matches(health, stop, 'policy_refused')
    assert not health_failure_matches({**health, 'available': True}, stop, 'auth_revoked')
    assert not health_failure_matches({**health, 'podUid': str(uuid4())}, stop, 'auth_revoked')
    assert not health_failure_matches({k: v for k, v in health.items() if k != 'failure'}, stop, 'auth_revoked')
    assert not health_failure_matches(None, stop, 'auth_revoked')


class Fake:
    def __init__(self, outcome): self.outcome, self.calls = outcome, []
    def reclaim(self, owner, pod_uid, *, runner_available):
        self.calls.append((owner, pod_uid))
        self.runner_available = runner_available
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def run(capsys, outcome, argv=None):
    fake = Fake(outcome)
    owner, pod = uuid4(), uuid4()
    rc = service.main(argv or ['reclaim', '--owner', str(owner), '--pod-uid', str(pod)], factory=lambda: (fake, None, None))
    return rc, capsys.readouterr().out, fake, (owner, pod)


def test_cli_accepted_prints_the_result(capsys):
    rc, out, fake, ids = run(capsys, {'result': 'accepted', 'stopId': 's'})
    assert rc == 0 and json.loads(out) == {'stage': 'reclaim', 'result': 'accepted', 'stopId': 's'} and fake.calls == [ids]
    assert fake.runner_available is False  # version 4 unless the operator names the flag


def test_cli_runner_available_flag_selects_the_version_5_path(capsys):
    owner, pod = uuid4(), uuid4()
    fake = Fake({'result': 'accepted', 'receiptVersion': 5})
    rc = service.main(['reclaim', '--owner', str(owner), '--pod-uid', str(pod), '--runner-available'],
                      factory=lambda: (fake, None, None))
    assert rc == 0 and fake.calls == [(owner, pod)] and fake.runner_available is True
    assert json.loads(capsys.readouterr().out)['receiptVersion'] == 5


def test_cli_refusal_prints_code_and_observed(capsys):
    rc, out, _, _ = run(capsys, service.Refusal('runner_available', health={'available': True}))
    assert rc == 3 and json.loads(out) == {'stage': 'reclaim', 'result': 'refused', 'code': 'runner_available',
                                           'observed': {'health': {'available': True}}}


def test_cli_other_failure_exposes_no_text(capsys):
    rc, out, _, _ = run(capsys, RuntimeError('postgresql://user:SECRET@db'))
    assert rc == 1 and 'SECRET' not in out and json.loads(out)['result'] == 'failed'


def test_cli_requires_both_uuids(capsys):
    with pytest.raises(SystemExit) as caught:
        service.main(['reclaim', '--owner', str(uuid4())], factory=lambda: pytest.fail('built'))
    assert caught.value.code == 2
    with pytest.raises(SystemExit):
        service.main(['reclaim', '--owner', 'nope', '--pod-uid', str(uuid4())], factory=lambda: pytest.fail('built'))
