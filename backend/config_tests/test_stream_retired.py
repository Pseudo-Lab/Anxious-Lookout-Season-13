"""#95 B1 (independent scope, review r3): retired service access tokens leave the stream holdback only when proven
retired and expired by provider times, recorded at install. Real ServiceAccessAuth installs; synthetic JWTs."""
import base64
import json
import logging
import os
import time
from uuid import uuid4

import pytest

from runner import stream_prefix, stream_retired
from runner.stream_prefix import P, policy
from test_service_access import ACCOUNT, setup

NOW = int(time.time())
HOUR = 3600


def token(iat, exp, email='synthetic-operator@example.invalid', pad='x'):
    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
    payload = {'exp': exp, 'nonce': str(uuid4()) + pad * 40, 'email': email,
               'https://api.openai.com/auth': {'chatgpt_account_id': ACCOUNT}}
    if iat is not None:
        payload['iat'] = iat
    return encode({'alg': 'RS256'}) + '.' + encode(payload) + '.synthetic-signature'


def install(auth, envelope, value):
    claims = json.loads(base64.urlsafe_b64decode(value.split('.')[1] + '=='))
    update = {**envelope, 'accessToken': value, 'expiresAt': claims['exp'], 'snapshotId': str(uuid4())}
    auth.install(update)
    return value


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv('STREAM_RETIRED', 'true')
    monkeypatch.setattr(stream_retired, '_observed', {})
    monkeypatch.setattr(stream_retired, '_cache', {})
    # the prefix write fails: the tokens stay uncovered, so the retired rows really hold the stream (production #93)
    monkeypatch.setattr(stream_prefix, '_write', lambda path, payload: (_ for _ in ()).throw(OSError(28, 'full')))
    auth, home, envelope = setup(tmp_path)
    return auth, envelope


def hold(auth):
    retired = stream_retired.excluded(auth.control, auth.journal, frozenset())
    return policy(auth.journal, auth.control, list(auth.secrets), list(auth.latest_secrets), retired)


def chain(auth, envelope, lifetime=2 * HOUR):
    """T0 install, then two tokens each issued after the previous one expired (provider time)."""
    first = install(auth, envelope, token(NOW, NOW + lifetime))
    second = install(auth, envelope, token(NOW + lifetime + 700, NOW + 2 * lifetime + 700))
    third = install(auth, envelope, token(NOW + 2 * lifetime + 1400, NOW + 3 * lifetime + 1400))
    return first, second, third


def test_a_retired_token_proven_expired_by_provider_time_leaves_the_holdback(service):
    auth, envelope = service
    first, second, third = chain(auth, envelope)
    hold_ = hold(auth)
    # the first token is a T0 row (pending, never proven); the second is proven: retired and expired before the
    # third was issued; the third is current
    assert hold_.retired_state == 'proven' and hold_.retired_excluded == 1
    assert hold_.legacy_retired == len(first) and hold_.legacy == max(len(first), len(third))
    assert hold_.legacy_current == len(third)


def test_the_switch_off_changes_nothing(service, monkeypatch):
    auth, envelope = service
    first, second, third = chain(auth, envelope)
    on = hold(auth)
    monkeypatch.setenv('STREAM_RETIRED', 'false')
    off = policy(auth.journal, auth.control, list(auth.secrets), list(auth.latest_secrets),
                 stream_retired.excluded(auth.control, auth.journal, frozenset()))
    plain = policy(auth.journal, auth.control, list(auth.secrets), list(auth.latest_secrets))
    assert off.retired_state == 'off' and off.legacy == plain.legacy and off.legacy_retired == plain.legacy_retired
    assert plain.legacy_retired == max(len(first), len(second))  # without #95 both retired rows hold
    assert on.legacy_retired == len(first)                        # with it only the T0 row still does
    assert off.state == plain.state                               # the switch off leaves the policy identity as before


def test_redaction_is_unchanged(service):
    auth, envelope = service
    first, second, third = chain(auth, envelope)
    for value in (first, second, third):
        text = 'before ' + value + ' after'
        assert value not in auth.journal.text(text)  # the retired, excluded token is still redacted


def test_t0_rows_are_never_proven_by_later_records(service):
    auth, envelope = service
    first = install(auth, envelope, token(NOW, NOW + HOUR))
    # the same value can never come back (the ledger refuses a retired credential), so later records can only be
    # for other values; the T0 row stays out of `proven` whatever comes after
    for step in range(1, 4):
        install(auth, envelope, token(NOW + step * (HOUR + 700), NOW + (step + 1) * HOUR + step * 700))
    payload, _ = stream_retired.load(auth.control, auth.journal)
    t0 = {tuple(item) for item in payload['t0Rows']}
    row = (len(first), auth.journal.digest(first))
    assert row in t0
    assert row not in stream_retired.excluded(auth.control, auth.journal, frozenset())[0]


def test_an_identifier_record_forbids_exclusion(service):
    """review r0 counterexample: an e-mail claim equal to a token value."""
    auth, envelope = service
    install(auth, envelope, token(NOW, NOW + HOUR))                                     # T0
    second = install(auth, envelope, token(NOW + HOUR + 700, NOW + 2 * HOUR + 700))      # recorded as token
    install(auth, envelope, token(NOW + 2 * HOUR + 1400, NOW + 3 * HOUR + 1400, email=second))  # ... and identifier
    install(auth, envelope, token(NOW + 3 * HOUR + 2100, NOW + 4 * HOUR + 2100))
    row = (len(second), auth.journal.digest(second))
    assert row not in stream_retired.excluded(auth.control, auth.journal, frozenset())[0]


@pytest.mark.parametrize('iat', [None, 'x', True, 1.5])
def test_a_missing_or_odd_iat_is_unproven(service, iat):
    auth, envelope = service
    chain(auth, envelope)
    install(auth, envelope, token(iat, NOW + 10 * HOUR))
    hold_ = hold(auth)
    assert hold_.retired_state == 'unproven' and hold_.retired_excluded == 0


def test_an_iat_going_back_is_unproven_until_a_later_token_exceeds_the_high_water(service):
    auth, envelope = service
    first, second, third = chain(auth, envelope)
    install(auth, envelope, token(NOW + HOUR, NOW + 10 * HOUR))   # lower than the third token's iat
    assert hold(auth).retired_state == 'unproven'
    install(auth, envelope, token(NOW + 20 * HOUR, NOW + 21 * HOUR))
    assert hold(auth).retired_state == 'proven'


def test_iat_after_exp_is_an_anomaly(service):
    auth, envelope = service
    chain(auth, envelope)
    install(auth, envelope, token(NOW + 30 * HOUR, NOW + 20 * HOUR))
    assert hold(auth).retired_state == 'unproven'


def test_a_lifetime_over_the_limit_turns_the_owner_off_for_good(service):
    auth, envelope = service
    chain(auth, envelope)
    long = stream_retired.LIFETIME_LIMIT + HOUR
    install(auth, envelope, token(NOW + 10 * HOUR, NOW + 10 * HOUR + long))
    assert hold(auth).retired_state == 'off'
    install(auth, envelope, token(NOW + 10 * HOUR + long + 700, NOW + 11 * HOUR + long + 700))
    assert hold(auth).retired_state == 'off' and hold(auth).retired_excluded == 0


def test_the_local_clock_is_never_used(service, monkeypatch):
    auth, envelope = service
    chain(auth, envelope)
    before = stream_retired.excluded(auth.control, auth.journal, frozenset())[0]
    monkeypatch.setattr(time, 'time', lambda: NOW + 365 * 86400)   # forward a year
    monkeypatch.setattr(stream_retired, '_cache', {})
    assert stream_retired.excluded(auth.control, auth.journal, frozenset())[0] == before
    monkeypatch.setattr(time, 'time', lambda: 0)                      # and back
    monkeypatch.setattr(stream_retired, '_cache', {})
    assert stream_retired.excluded(auth.control, auth.journal, frozenset())[0] == before


@pytest.mark.parametrize('damage', ['tamper', 'foreign', 'symlink', 'shared', 'garbage'])
def test_a_damaged_or_foreign_record_excludes_nothing(service, damage, tmp_path):
    auth, envelope = service
    chain(auth, envelope)
    path = auth.control / stream_retired.FILE
    payload = json.loads(path.read_text())
    if damage == 'tamper':
        payload['maxIat'] += 10 ** 6
        path.write_text(json.dumps(payload))
    elif damage == 'foreign':
        payload['owner'] = str(uuid4())
        path.write_text(json.dumps(payload))
    elif damage == 'symlink':
        target = auth.control / 'elsewhere.json'
        path.rename(target)
        path.symlink_to(target)
    elif damage == 'shared':
        os.chmod(path, 0o644)
    else:
        path.write_text('{')
    stream_retired._cache.clear()
    hold_ = hold(auth)
    assert hold_.retired_state == 'unproven' and hold_.retired_excluded == 0


def test_a_failed_record_write_never_creates_an_exclusion(service, monkeypatch):
    auth, envelope = service
    install(auth, envelope, token(NOW, NOW + HOUR))
    monkeypatch.setattr(stream_retired, '_write', lambda *a: (_ for _ in ()).throw(OSError(28, 'full')))
    install(auth, envelope, token(NOW + HOUR + 700, NOW + 2 * HOUR + 700))
    install(auth, envelope, token(NOW + 2 * HOUR + 1400, NOW + 3 * HOUR + 1400))
    assert hold(auth).retired_excluded == 0  # the second token was never recorded


def test_current_and_latest_literals_are_never_excluded(service):
    auth, envelope = service
    first, second, third = chain(auth, envelope)
    retired = (frozenset({(len(third), auth.journal.digest(third)), (len(second), auth.journal.digest(second))}),
               'proven', ('x',))
    hold_ = policy(auth.journal, auth.control, list(auth.secrets), list(auth.latest_secrets), retired)
    assert hold_.legacy_current == len(third) and hold_.legacy >= len(third)


def test_a_change_of_the_record_changes_the_policy_identity(service):
    auth, envelope = service
    first, second, third = chain(auth, envelope)
    before = hold(auth)
    install(auth, envelope, token(NOW + 30 * HOUR, NOW + 31 * HOUR))
    assert hold(auth) != before


def test_logs_hold_states_and_counts_only_and_repeat_nothing(service, caplog):
    caplog.set_level(logging.INFO, logger='uvicorn.error')
    auth, envelope = service
    values = chain(auth, envelope)
    lines = [json.loads(r.getMessage().partition(' ')[2]) for r in caplog.records
             if r.getMessage().startswith('runner_stream_retired ')]
    assert lines and set(lines[-1]) == {'state', 't0Rows', 'records', 'proven', 'iatAnomaly', 'disabled',
                                        'lifetimeHoursMax', 'lifetimeCount', 'assumed', 't0Assumable', 'anchor',
                                        'anchorAttempts'}
    assert lines[-1]['lifetimeHoursMax'] == 2 and lines[-1]['lifetimeCount'] == 3
    assert (lines[-1]['assumed'], lines[-1]['anchor'], lines[-1]['anchorAttempts']) == (0, False, 0)
    for value in values:
        assert value[:P] not in caplog.text and auth.journal.digest(value) not in caplog.text
    count = len(lines)
    for _ in range(20):
        hold(auth)
    assert len([r for r in caplog.records if r.getMessage().startswith('runner_stream_retired ')]) == count
