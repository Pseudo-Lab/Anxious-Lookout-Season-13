"""Broker attestation remains compatible, but #99 removes every runner attestation/anchor caller."""
import base64
import json
from pathlib import Path
import re
import time
from uuid import uuid4

import httpx
import pytest

from app.codex_policy import CodexFailure
from broker.credentials import Credentials, iat_of
from runner.broker_client import BrokerClient, FIELDS
from test_codex_broker import FakeServer, IDENTITY, BODY, StubVerifier, call, write_auth
from test_service_access import ACCOUNT

NOW = int(time.time())
HOUR = 3600


def token(iat, exp, email='synthetic-operator@example.invalid'):
    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
    payload = {'exp': exp, 'nonce': str(uuid4()) + 'x' * 40, 'email': email,
               'https://api.openai.com/auth': {'chatgpt_account_id': ACCOUNT}}
    if iat is not None:
        payload['iat'] = iat
    return encode({'alg': 'RS256'}) + '.' + encode(payload) + '.synthetic-signature'


# --- broker: Credentials.attest -------------------------------------------------------------------------------------

@pytest.fixture
def home(tmp_path):
    path = tmp_path / 'codex'
    path.mkdir(mode=0o700)
    return path


def attest_with(home, old, new, received_at, clock=None):
    write_auth(home, old)
    server = FakeServer(lambda home: write_auth(home, new))
    credentials = Credentials(home, ACCOUNT, server=server, clock=clock or (lambda: NOW))
    return credentials, server, credentials.attest(received_at)


@pytest.mark.parametrize('old_iat, new_iat, attested', [
    (NOW - 500, NOW, True),
    (NOW - 500, NOW - 300, True),            # exactly the tolerance: passes (the runner adds 300 s to its bound)
    (NOW - 500, NOW - 301, False),           # one second earlier: not attested
    (NOW, NOW, False),                       # not newer than the previous token
    (NOW + 10, NOW, False),                  # older than the previous token
    (None, NOW, False),                      # the previous token has no iat
    (NOW - 500, None, False),                # the new token has no iat
])
def test_the_four_conditions(home, old_iat, new_iat, attested):
    old, new = token(old_iat, NOW + 7200), token(new_iat, NOW + 7200)
    credentials, server, result = attest_with(home, old, new, received_at=NOW)
    assert result[0] == new and result[3] is attested and len(server.calls) == 1


def test_within_the_global_cooldown_no_refresh_runs_and_nothing_is_attested(home):
    credentials, server, first = attest_with(home, token(NOW - 500, NOW + 7200), token(NOW, NOW + 7200), NOW)
    assert first[3] is True
    again = credentials.attest(NOW + 30)
    assert again[3] is False and len(server.calls) == 1  # no second forced refresh within 60 s
    assert credentials.access(force=True)[0] == first[0] and len(server.calls) == 1  # the 401 path shares it


def test_a_provider_keeping_the_token_is_a_failure_not_an_attestation(home):
    same = token(NOW - 500, NOW + 7200)
    write_auth(home, same)
    credentials = Credentials(home, ACCOUNT, server=FakeServer(lambda home: None), clock=lambda: NOW)
    with pytest.raises(CodexFailure):
        credentials.attest(NOW)


def test_iat_of_takes_integers_only():
    assert iat_of(token(5, NOW + 10)) == 5
    assert iat_of(token(True, NOW + 10)) is None and iat_of(token(1.5, NOW + 10)) is None
    assert iat_of('not-a-jwt') is None


# --- broker app: opt-in only ------------------------------------------------------------------------------------------

class AttestCredentials:
    def __init__(self, attested=True):
        self.calls, self.attested = [], attested
        self.clock = lambda: NOW

    def access(self, force=False):
        self.calls.append(('access', force))
        return 'synthetic-access-token-value', ACCOUNT, NOW + 7200

    def attest(self, received_at):
        self.calls.append(('attest', received_at))
        return 'synthetic-access-token-value', ACCOUNT, NOW + 7200, self.attested

    def close(self):
        pass


def test_only_an_attest_request_gets_the_field_and_others_are_unchanged():
    credentials = AttestCredentials()
    plain = call(StubVerifier(IDENTITY), credentials, {**BODY, 'refresh': True}).json()
    assert set(plain) == FIELDS  # an old runner's exact-set check still passes
    attested = call(StubVerifier(IDENTITY), credentials, {**BODY, 'refresh': True, 'attest': True}).json()
    assert set(attested) == FIELDS | {'refreshedForRequest'} and attested['refreshedForRequest'] is True
    assert [kind for kind, _ in credentials.calls] == ['access', 'attest']


@pytest.mark.parametrize('body', [{**BODY, 'attest': True}, {**BODY, 'refresh': False, 'attest': True},
                                  {**BODY, 'refresh': True, 'attest': 'yes'}, {**BODY, 'refresh': True, 'attest': 1}])
def test_attest_needs_refresh_and_a_boolean(body):
    credentials = AttestCredentials()
    response = call(StubVerifier(IDENTITY), credentials, body)
    assert (response.status_code, response.json()) == (400, {'error': 'invalid_request'}) and credentials.calls == []


# --- runner: BrokerClient and "the runner never attests" -------------------------------------------------------------

def client_with(tmp_path, answer):
    seen = []
    token_file = tmp_path / 'broker-token'
    token_file.write_text('projected')

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=answer)
    client = BrokerClient('http://codex-broker.codex-broker.svc:8080', str(token_file), str(uuid4()), str(uuid4()),
                          transport=httpx.MockTransport(handler))
    return client, seen


def envelope_for(client, **extra):
    return {'version': 2, 'ownerId': client.owner, 'stateId': client.state_id, 'accountId': ACCOUNT,
            'accessToken': 'synthetic', 'expiresAt': NOW + 7200, 'snapshotId': str(uuid4()), **extra}


def test_a_normal_pull_never_sends_attest_and_refuses_the_extra_field(tmp_path):
    client, seen = client_with(tmp_path, {})
    client.transport = httpx.MockTransport(lambda request: (seen.append(json.loads(request.content)),
                                                            httpx.Response(200, json=envelope_for(client)))[1])
    client.fetch()
    client.fetch(refresh=True)
    assert seen == [{'ownerId': client.owner, 'stateId': client.state_id, 'refresh': False},
                    {'ownerId': client.owner, 'stateId': client.state_id, 'refresh': True}]
    client.transport = httpx.MockTransport(lambda request: httpx.Response(
        200, json=envelope_for(client, refreshedForRequest=True)))
    with pytest.raises(CodexFailure):
        client.fetch(refresh=True)  # an unexpected field is still refused on the normal path


def test_no_runner_module_asks_for_attestation():
    import runner
    root = Path(runner.__file__).parent
    assert not (root / 'stream_anchor.py').exists()
    assert not any(re.search(r'attest\s*=\s*True', path.read_text()) for path in root.glob('*.py'))
