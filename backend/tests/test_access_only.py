import base64
import json
import time
import uuid
from pathlib import Path

import pytest

from app.codex_policy import CodexFailure, error_reason
from ops.access_snapshot import selected_file
from runner.access import AccessAuth, claims
from runner.access import SupplySocket
from runner.auth import read_private, write_private
from runner.binding import initialize_binding


def jwt(account='synthetic-account', expiry=None):
    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
    return encode({'alg': 'RS256'})+'.'+encode({'exp': expiry or int(time.time())+3600,
                'https://api.openai.com/auth': {'chatgpt_account_id': account}})+'.synthetic-signature'


def setup(tmp_path):
    control, root = tmp_path/'control', tmp_path/'native'
    control.mkdir(mode=0o700)
    owner, state = str(uuid.uuid4()), str(uuid.uuid4())
    initialize_binding(root, owner, state)
    home = root/'codex'
    home.mkdir(mode=0o700)
    token = jwt()
    envelope = {'version': 1, 'ownerId': owner, 'stateId': state, 'accountId': 'synthetic-account',
                'accessToken': token, 'expiresAt': claims(token)[0]['exp'], 'windowId': str(uuid.uuid4()),
                'newWindow': True, 'maxDispatches': 3}
    return AccessAuth(control, owner, state), home, envelope


def test_access_window_rearm_preserves_lifetime_dedup_projection_and_account(tmp_path):
    auth, home, envelope = setup(tmp_path)
    auth.install(envelope)
    auth.attach(home)
    trial, key = auth.state()['trialId'], auth.journal.key
    requests = [str(uuid.uuid4()) for _ in range(3)]
    for request in requests:
        auth.consume(request)
    with pytest.raises(CodexFailure, match='codex_budget_exhausted'):
        auth.check()
    auth.permitted_tool({'type': 'document'})  # third reserved turn may complete
    auth.install({**envelope, 'newWindow': False})
    with pytest.raises(CodexFailure, match='codex_budget_exhausted'):
        auth.check()
    auth.install({**envelope, 'windowId': str(uuid.uuid4())})
    assert auth.status()['remainingAdmissions'] == 3
    with pytest.raises(CodexFailure):
        auth.consume(requests[0])
    assert auth.state()['trialId'] == trial and auth.journal.key == key
    with pytest.raises(CodexFailure):
        auth.install(envelope)  # old window replay never rearms
    other = jwt('another-account')
    with pytest.raises(CodexFailure):
        auth.install({**envelope, 'windowId': str(uuid.uuid4()), 'accountId': 'another-account', 'accessToken': other})
    assert envelope['accessToken'] not in ''.join(p.read_text() for p in auth.control.iterdir() if p.is_file())
    assert auth.sanitize({'secret': envelope['accessToken']}) == {'secret': '[redacted]'}
    assert not (home/'auth.json').exists()
    auth.close()


def test_expiry_refusal_survives_checks_and_requires_operator_new_window(tmp_path):
    auth, home, envelope = setup(tmp_path)
    auth.install(envelope)
    auth.attach(home)
    value = auth.state()
    value.update(deadline=int(time.time())-1, windowStartedAt=int(time.time())-901)
    write_private(auth.control/'access-ledger.json', value)
    with pytest.raises(CodexFailure, match='codex_auth_expired'):
        auth.permitted_tool({})
    with pytest.raises(CodexFailure, match='codex_auth_expired'):
        auth.install({**envelope, 'newWindow': False})
    auth.install({**envelope, 'windowId': str(uuid.uuid4())})
    auth.check()
    auth.block('auth_revoked')
    with pytest.raises(CodexFailure, match='codex_auth_revoked'):
        auth.check()
    auth.close()
    # No cached credential after restart. Durable history/redaction survives supply.
    restarted = AccessAuth(auth.control, auth.owner, auth.state_id)
    with pytest.raises(CodexFailure):
        restarted.check()
    restarted.install({**envelope, 'newWindow': False, 'windowId': auth.state()['windowId']})
    restarted.attach(home)
    assert restarted.sanitize(envelope['accessToken']) == '[redacted]'
    restarted.close()


def test_source_exact_private_file_only_and_no_refresh_export(tmp_path):
    path = tmp_path/'selected.json'
    token = jwt()
    original = {'auth_mode': 'chatgpt', 'tokens': {'account_id': 'synthetic-account', 'access_token': token,
                 'id_token': 'SYNTHETIC_ID', 'refresh_token': 'SYNTHETIC_REFRESH'}}
    path.write_text(json.dumps(original)); path.chmod(0o600)
    before = path.read_bytes()
    result = selected_file(str(path))
    assert result == (token, 'synthetic-account', claims(token)[0]['exp'])
    assert 'SYNTHETIC_REFRESH' not in str(result) and 'SYNTHETIC_ID' not in str(result)
    assert path.read_bytes() == before
    alias = tmp_path/'alias'; alias.symlink_to(path)
    with pytest.raises(CodexFailure):
        selected_file(str(alias))
    path.chmod(0o644)
    with pytest.raises(CodexFailure):
        selected_file(str(path))
    path.chmod(0o600)
    original['tokens']['access_token'] = jwt(expiry=int(time.time())-1)
    path.write_text(json.dumps(original))
    with pytest.raises(CodexFailure, match='codex_auth_expired'):
        selected_file(str(path))
    original['tokens']['access_token'] = token
    original['tokens']['account_id'] = 'mismatch'
    path.write_text(json.dumps(original))
    with pytest.raises(CodexFailure):
        selected_file(str(path))


@pytest.mark.parametrize('change', [{'ownerId': str(uuid.uuid4())}, {'stateId': str(uuid.uuid4())},
                                  {'maxDispatches': 4}, {'newWindow': 'true'}, {'refreshToken': 'no'}])
def test_bad_envelope_never_creates_ledger_or_native_auth(tmp_path, change):
    auth, home, envelope = setup(tmp_path)
    with pytest.raises(CodexFailure):
        auth.install({**envelope, **change})
    assert not (auth.control/'access-ledger.json').exists() and not (home/'auth.json').exists()


def test_provider_rate_limit_distinct_from_local_budget():
    assert error_reason({'codexErrorInfo': 'usageLimitExceeded'}) == 'provider_rate_limited'
    assert error_reason({'code': 'rate_limit_exceeded'}) == 'provider_rate_limited'


def test_private_stdin_socket_client_receipt_never_prints_input(tmp_path):
    import subprocess
    import sys
    control = tmp_path/'control'
    control.mkdir(mode=0o700)
    seen = []
    receiver = SupplySocket(control, seen.append)
    envelope = {'syntheticPrivate': 'SYNTHETIC_PRIVATE_VALUE'}
    try:
        result = subprocess.run([sys.executable, '-B', '-m', 'runner.access_supply', str(receiver.path)],
                                input=json.dumps(envelope), text=True, capture_output=True, timeout=10, cwd=Path(__file__).resolve().parents[1])
        assert result.returncode == 0 and result.stdout.strip() == '{"accepted":true}' and result.stderr == ''
        assert seen == [envelope]
    finally:
        receiver.close()
