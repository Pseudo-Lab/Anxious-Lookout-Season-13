"""#95 route 1 (design r4.2): broker opt-in attestation, the operator's one-shot runner.stream_anchor, and the
`assumed` release of the T0 rows (A1 / A2). Real ServiceAccessAuth installs and broker Credentials; synthetic JWTs."""
import base64
import json
import os
from pathlib import Path
import re
import time
from uuid import uuid4

import httpx
import pytest

from app.codex_policy import CodexFailure
from broker import credentials as broker_credentials
from broker.credentials import Credentials, iat_of
from runner import stream_anchor, stream_prefix, stream_retired
from runner.broker_client import BrokerClient, FIELDS
from runner.stream_prefix import policy
from test_codex_broker import FakeServer, IDENTITY, BODY, StubVerifier, call, write_auth
from test_service_access import ACCOUNT, setup

NOW = int(time.time())
HOUR = 3600
G = stream_retired.G


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


@pytest.mark.parametrize('extra, ok', [({'refreshedForRequest': True}, True), ({'refreshedForRequest': False}, True),
                                       ({}, False), ({'refreshedForRequest': 'true'}, False)])
def test_an_attest_fetch_requires_the_boolean_field(tmp_path, extra, ok):
    client, seen = client_with(tmp_path, {})
    client.transport = httpx.MockTransport(lambda request: (seen.append(json.loads(request.content)),
                                                            httpx.Response(200, json=envelope_for(client, **extra)))[1])
    if ok:
        assert client.fetch(refresh=True, attest=True)['refreshedForRequest'] is extra['refreshedForRequest']
    else:
        with pytest.raises(CodexFailure):
            client.fetch(refresh=True, attest=True)
    assert seen[0]['attest'] is True


def test_no_runner_module_but_the_one_shot_asks_for_attestation():
    root = Path(stream_anchor.__file__).parent
    callers = sorted(path.name for path in root.glob('*.py') if re.search(r'attest\s*=\s*True', path.read_text()))
    assert callers == ['stream_anchor.py']


# --- the one-shot and the assumed release ----------------------------------------------------------------------------

def install(auth, envelope, value):
    claims = json.loads(base64.urlsafe_b64decode(value.split('.')[1] + '=='))
    auth.install({**envelope, 'accessToken': value, 'expiresAt': claims['exp'], 'snapshotId': str(uuid4())})
    return value


@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.setattr(stream_retired, '_observed', {})
    monkeypatch.setattr(stream_retired, '_cache', {})
    monkeypatch.setattr(stream_retired, '_poisoned', set())
    monkeypatch.setattr(stream_prefix, '_write', lambda path, payload: (_ for _ in ()).throw(OSError(28, 'full')))
    auth, home, envelope = setup(tmp_path)
    monkeypatch.setenv('STREAM_RETIRED', 'false')
    old = [install(auth, envelope, token(NOW + i * HOUR, NOW + (i + 2) * HOUR)) for i in range(2)]  # before #95
    monkeypatch.setenv('STREAM_RETIRED', 'true')
    current = install(auth, envelope, token(NOW + 2 * HOUR, NOW + 4 * HOUR))  # T0
    account_file = tmp_path / 'operator-account'
    descriptor = os.open(account_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        stream.write(ACCOUNT)
    environ = {'CODEX_AUTH_MODE': 'access-service', 'RUNNER_ACCOUNT_ID': auth.owner, 'RUNNER_STATE_ID': auth.state_id,
               'CODEX_ACCESS_CONTROL_DIR': str(auth.control), 'CODEX_OPERATOR_ACCOUNT_FILE': str(account_file),
               'BROKER_URL': 'http://broker.invalid/v1/access', 'BROKER_TOKEN_FILE': str(tmp_path / 'unused')}
    return auth, envelope, old, current, environ


class FakeBroker:
    def __init__(self, envelope=None, failure=None, during=None):
        self.envelope, self.failure, self.during, self.calls = envelope, failure, during, []

    def __call__(self, url, token_file, owner, state_id):
        return self

    def fetch(self, refresh=False, attest=False):
        self.calls.append((refresh, attest))
        if self.during:
            self.during()
        if self.failure:
            raise self.failure
        return self.envelope


def answer(iat, attested=True):
    return {'accessToken': token(iat, iat + 2 * HOUR), 'refreshedForRequest': attested}


def run(environ, broker, capsys):
    code = stream_anchor.main(environ, client_factory=broker)
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    return code, line


def test_t0_is_assumable_when_every_non_current_row_is_a_retired_token(owner):
    auth = owner[0]
    payload, _ = stream_retired.load(auth.control, auth.journal)
    assert payload['t0Assumable'] is True and payload['anchor'] is None


def test_an_anchor_then_the_assumed_release(owner, capsys):
    auth, envelope, old, current, environ = owner
    anchor_iat = NOW + 3 * HOUR
    broker = FakeBroker(answer(anchor_iat))
    code, line = run(environ, broker, capsys)
    assert (code, line) == (0, {'result': 'anchored', 'reason': None}) and broker.calls == [(True, True)]
    bound = anchor_iat + stream_retired.ATTEST_TOLERANCE + G
    equal = install(auth, envelope, token(bound, bound + HOUR))         # equal: not yet
    retired = stream_retired.excluded(auth.control, auth.journal, frozenset())
    assert retired[1] != 'assumed' and not retired[3]
    latest = install(auth, envelope, token(bound + 1, bound + 1 + HOUR))  # strictly after: released
    retired = stream_retired.excluded(auth.control, auth.journal, frozenset())
    assert retired[1] == 'assumed'
    rows = {(len(value), auth.journal.digest(value)) for value in old + [current]}
    assert rows == retired[3]
    hold = policy(auth.journal, auth.control, list(auth.secrets), list(auth.latest_secrets), retired)
    assert hold.retired_excluded_assumed == len(rows)
    # the only retired row still holding is `equal`: recorded after T0, retired, but not yet expired (proven path)
    assert (len(equal), auth.journal.digest(equal)) not in retired[0]
    assert hold.legacy_retired == len(equal)
    assert hold.legacy_current == len(latest)       # the current token still holds
    for value in old:
        assert value not in auth.journal.text('x ' + value + ' y')  # redaction unchanged


@pytest.mark.parametrize('broker, reason', [
    (FakeBroker(failure=CodexFailure('unavailable')), 'request_failed'),
    (FakeBroker(answer(NOW + 3 * HOUR, attested=False)), 'not_attested'),
    (FakeBroker({'accessToken': 'not-a-jwt', 'refreshedForRequest': True}), 'claims_refused'),
])
def test_a_failed_run_requests_once_counts_the_attempt_and_stays_pending(owner, capsys, broker, reason):
    auth, envelope, old, current, environ = owner
    code, line = run(environ, broker, capsys)
    assert (code, line) == (4, {'result': 'pending', 'reason': reason}) and len(broker.calls) == 1
    payload, _ = stream_retired.load(auth.control, auth.journal)
    assert payload['anchor'] is None and payload['anchorAttempts'] == 1


def test_no_record_or_an_existing_anchor_requests_nothing(owner, capsys):
    auth, envelope, old, current, environ = owner
    run(environ, FakeBroker(answer(NOW + 3 * HOUR)), capsys)
    again = FakeBroker(answer(NOW + 4 * HOUR))
    assert run(environ, again, capsys) == (0, {'result': 'already', 'reason': None}) and again.calls == []
    (auth.control / stream_retired.FILE).unlink()
    gone = FakeBroker(answer(NOW + 4 * HOUR))
    assert run(environ, gone, capsys) == (3, {'result': 'refused', 'reason': 'no_record'}) and gone.calls == []


def test_a_sidecar_lost_during_the_request_turns_the_owner_off(owner, capsys):
    """review r0: a lost record after observation began is a permanent off (no new T0), so no anchor can land."""
    auth, envelope, old, current, environ = owner

    def lose():
        (auth.control / stream_retired.FILE).unlink()
        install(auth, envelope, token(NOW + 5 * HOUR, NOW + 6 * HOUR))
    broker = FakeBroker(answer(NOW + 3 * HOUR), during=lose)
    assert run(environ, broker, capsys) == (4, {'result': 'pending', 'reason': 'off'})
    assert stream_retired.load(auth.control, auth.journal)[0] is None
    assert stream_retired.excluded(auth.control, auth.journal, frozenset())[1] == 'off'


def test_an_epoch_change_during_the_request_never_receives_the_anchor(owner, capsys):
    auth, envelope, old, current, environ = owner

    def new_epoch():
        payload, _ = stream_retired.load(auth.control, auth.journal)
        stream_retired._write(auth.control, auth.journal, dict(payload, epoch='f' * 32))
    broker = FakeBroker(answer(NOW + 3 * HOUR), during=new_epoch)
    assert run(environ, broker, capsys) == (4, {'result': 'pending', 'reason': 'epoch_changed'})
    assert stream_retired.load(auth.control, auth.journal)[0]['anchor'] is None


def test_a_lock_failure_is_a_fixed_answer_and_no_request(owner, capsys):
    auth, envelope, old, current, environ = owner
    lock = auth.control / stream_retired.LOCK
    lock.unlink(missing_ok=True)
    lock.symlink_to(auth.control / 'elsewhere')
    broker = FakeBroker(answer(NOW + 3 * HOUR))
    assert run(environ, broker, capsys) == (3, {'result': 'refused', 'reason': 'lock_failed'}) and broker.calls == []


def test_a_changed_email_makes_t0_not_assumable_and_nothing_is_released(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(stream_retired, '_observed', {})
    monkeypatch.setattr(stream_retired, '_cache', {})
    monkeypatch.setattr(stream_retired, '_poisoned', set())
    auth, home, envelope = setup(tmp_path)
    monkeypatch.setenv('STREAM_RETIRED', 'false')
    install(auth, envelope, token(NOW, NOW + 2 * HOUR, email='first-address@example.invalid'))
    monkeypatch.setenv('STREAM_RETIRED', 'true')
    install(auth, envelope, token(NOW + HOUR, NOW + 3 * HOUR, email='second-address@example.invalid'))
    payload, _ = stream_retired.load(auth.control, auth.journal)
    assert payload['t0Assumable'] is False  # N = old token + old e-mail = 2, R = 1


def test_a_migrated_store_is_not_assumable(owner, monkeypatch):
    auth = owner[0]
    with auth.store.transaction() as db:
        db.execute("INSERT INTO migration VALUES(1, ?, ?)", ('a' * 64, 'b' * 64))
    _, rows = auth.journal.stream_rows(stream_prefix.P)
    current = {(len(v), auth.journal.digest(v)) for v in auth.latest_secrets if len(v) > stream_prefix.P}
    assert stream_retired.assumable(auth.journal, rows, current) is False


def test_the_same_token_under_a_new_snapshot_id_counts_once(owner):
    auth, envelope, old, current, environ = owner
    auth.install({**envelope, 'accessToken': current, 'snapshotId': str(uuid4()),
                  'expiresAt': json.loads(base64.urlsafe_b64decode(current.split('.')[1] + '=='))['exp']})
    _, rows = auth.journal.stream_rows(stream_prefix.P)
    protected = {(len(v), auth.journal.digest(v)) for v in auth.latest_secrets if len(v) > stream_prefix.P}
    assert stream_retired.assumable(auth.journal, rows, protected) is True  # DISTINCT fingerprints


def test_the_one_shot_refuses_outside_service_mode(owner, capsys):
    environ = dict(owner[4], CODEX_AUTH_MODE='access-only')
    broker = FakeBroker(answer(NOW))
    assert run(environ, broker, capsys) == (3, {'result': 'refused', 'reason': 'configuration'}) and broker.calls == []
