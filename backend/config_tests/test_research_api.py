"""Synthetic principals, filesystem and HTTP doubles; no DB/model/kernel proof."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import fcntl
import io
import json
import ssl
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.codex_policy import CodexFailure
from app.research import ResearchError
from app import research_credentials as credentials
from app import research_receipts as receipts
from config_tests.research_result_fixtures import save_result
from runner import research_cli as cli
from runner.app import Native
from runner.auth import read_private
from runner.research_api import GrantFile, strict_json
from runner.stream import Sanitizer, Holdback, MARKER
from runner.stream_prefix import HoldPolicy


def principal(scope=None):
    scope = scope or {'page': {'kind': 'general'}, 'context': None}
    row = SimpleNamespace(id=uuid4(), request_id=uuid4(), owner_id=uuid4(), archived=False,
                          state='running', native_record={})
    token, grant = credentials.issue(row.id, row.request_id, scope, datetime.now(timezone.utc) + timedelta(hours=2), owner=row.owner_id)
    metadata = {'requestId': str(row.request_id), 'scope': scope, 'researchGrant': grant}
    row.native_record = {'platformRequests': [metadata]}
    return row, token, metadata


def test_turn_principal_is_purpose_separated_hash_only_and_never_pat():
    import hashlib
    row, token, metadata = principal()
    assert len(token) == 124 and credentials.locate(token) == (row.id, row.request_id)
    assert token not in json.dumps(row.native_record)
    assert metadata['researchGrant']['hash'] != hashlib.sha256(token.encode()).hexdigest()
    assert credentials.resolve(row, token, metadata).conversation == row.id
    with pytest.raises(ResearchError):
        credentials.locate('pat.' + token)


def test_enable_refuses_non_https443_before_routes_or_credentials(monkeypatch):
    from app.research import register_research
    monkeypatch.setenv('RESEARCH_AGENT_API_ENABLE', 'true')
    for origin in ('http://example.invalid', 'https://example.invalid:8443'):
        with pytest.raises(ValueError, match='HTTPS443'):
            register_research(SimpleNamespace(state=SimpleNamespace()), None,
                              SimpleNamespace(origin=origin), None, None)


@pytest.mark.parametrize('damage', ['owner', 'request', 'conversation', 'hash', 'purpose', 'scope', 'archived', 'idle', 'public', 'revoke'])
def test_changed_or_revoked_principal_cannot_use_cached_owner_or_scope(damage):
    row, token, metadata = principal()
    if damage == 'owner': row.owner_id = uuid4()
    if damage == 'request': row.request_id = uuid4()
    if damage == 'conversation': row.id = uuid4()
    if damage == 'hash': metadata['researchGrant']['hash'] = '0' * 64
    if damage == 'purpose': metadata['researchGrant']['purpose'] = 'runner-service'
    if damage == 'scope': metadata['scope']['page']['kind'] = 'document_workspace'
    if damage == 'archived': row.archived = True
    if damage == 'idle': row.state = 'idle'
    if damage == 'public': metadata['scope']['page']['kind'] = 'public_document'
    if damage == 'revoke':
        credentials.revoke(row)
        metadata = row.native_record['platformRequests'][0]
    with pytest.raises(ResearchError): credentials.resolve(row, token, metadata)


def test_hard_ttl_is_not_extended_by_active_state_and_login_can_shorten_it():
    row, token, metadata = principal()
    expiry = datetime.fromisoformat(metadata['researchGrant']['expiresAt'].replace('Z', '+00:00'))
    with pytest.raises(ResearchError) as caught:
        credentials.resolve(row, token, metadata, expiry)
    assert caught.value.code == 'research_token_expired' and caught.value.status == 401
    now = datetime.now(timezone.utc)
    _, grant = credentials.issue(row.id, row.request_id, metadata['scope'], now + timedelta(seconds=5), now, owner=row.owner_id)
    assert datetime.fromisoformat(grant['expiresAt'].replace('Z', '+00:00')) == now + timedelta(seconds=5)


def test_public_and_bad_token_inputs_cannot_mint_or_persist_a_grant():
    row, token, _ = principal()
    for kind in ('public_document', 'public_post'):
        with pytest.raises(ResearchError):
            credentials.issue(row.id, row.request_id, {'page': {'kind': kind}}, datetime.now(timezone.utc), owner=row.owner_id)
    value = {'nested': [{'message': 'echo ' + token}], token: 'key'}
    assert token not in json.dumps(credentials.redact(value))
    with pytest.raises(ResearchError): credentials.reject_token_input(value)


def grant_fixture(tmp_path, monkeypatch):
    monkeypatch.setenv('RUNNER_RESEARCH_API_ENABLE', 'true')
    control = tmp_path / 'control'; control.mkdir(mode=0o700)
    root = tmp_path / 'state'; root.mkdir(mode=0o700)
    row, token, metadata = principal()
    auth = SimpleNamespace(service=True, external=True, secrets=set(), control=control)
    events = []
    auth.remember_secrets = lambda: events.append(set(auth.secrets))
    file = GrantFile(root, auth)
    body = SimpleNamespace(requestId=row.request_id, scope=metadata['scope'], researchGrant={
        'token': token, 'sessionId': str(row.id), 'requestId': str(row.request_id),
        'apiOrigin': 'https://research.example.invalid', 'basePath': '',
        'expiresAt': metadata['researchGrant']['expiresAt']})
    return row, body, auth, file, events


def test_file_publish_follows_journal_and_clear_replaces_inode_without_adoption(tmp_path, monkeypatch):
    row, body, auth, file, events = grant_fixture(tmp_path, monkeypatch)
    file.prepare(str(row.id), body)
    assert events == [{body.researchGrant['token']}]
    assert auth.research_api_redaction and file.path.stat().st_mode & 0o777 == 0o600
    with file.path.open() as old:
        old_inode = file.path.stat().st_ino
        file.clear()
        assert file.path.stat().st_ino != old_inode
        assert json.load(old)['token'] == body.researchGrant['token']
    assert read_private(file.path) == {'available': False}
    assert GrantFile(file.directory.parent, auth).current is None
    # Host filesystem atomic replace is not bwrap/old namespace isolation proof.


@pytest.mark.parametrize('reason', ['model/rerouted', 'turn/completed', 'account/chatgptAuthTokens/refresh'])
def test_reader_refusal_cannot_be_reopened_by_late_turn_binding(tmp_path, monkeypatch, reason):
    row, body, _, file, _ = grant_fixture(tmp_path, monkeypatch)
    file.prepare(str(row.id), body)
    file.note({'method': reason, 'params': {}})
    file.bind({'session': str(row.id), 'request': str(row.request_id), 'thread': 'th', 'turnId': 'tu'})
    assert file.active(str(row.id), str(row.request_id)) is None


def test_duplicate_native_fields_fail_closed():
    with pytest.raises(ValueError): strict_json('{"method":"turn/started","method":"turn/completed"}')


def test_reader_model_refusal_survives_later_started_and_eof(tmp_path, monkeypatch):
    import queue
    row, body, _, file, _ = grant_fixture(tmp_path, monkeypatch)
    file.prepare(str(row.id), body)
    native = Native.__new__(Native)
    native.research_api, native.blocked_reason, native.real_verified = file, None, True
    native.messages = queue.Queue()
    native.process = SimpleNamespace(stdout=iter([
        json.dumps({'method': 'model/rerouted', 'params': {}}),
        json.dumps({'method': 'turn/started', 'params': {'turn': {'id': 'turn', 'model': 'gpt-6.1-sol'}}})]))
    native.reader()
    assert native.blocked_reason == 'model_unavailable' and native.reader_model_refused
    assert not native.real_verified and file.revoked


def test_worker_persists_observed_refusal_even_when_queue_failure_preempts_receive(monkeypatch):
    from config_tests.test_runner_stream_app import bare_native, scripted_turn, SESSION
    native = bare_native(monkeypatch=monkeypatch)
    native.reader_model_refused = True
    persisted = []
    native.block_model = lambda: persisted.append('blocked')
    native.publish_record = lambda session, entry, record: persisted.append(record)
    native.quarantine_history = lambda *args: persisted.append('quarantined')
    scripted_turn(native, monkeypatch, 'completed', [{'type': 'agentMessage', 'id': 'bad', 'text': 'unverified'}])
    assert native.mapping[SESSION]['state'] == 'failed'
    assert 'blocked' in persisted and {'turns': []} in persisted and 'quarantined' in persisted


def test_native_status_is_local_and_rechecks_after_auth_without_protocol_rpc(tmp_path, monkeypatch):
    row, body, auth, file, _ = grant_fixture(tmp_path, monkeypatch)
    file.prepare(str(row.id), body)
    file.bind({'session': str(row.id), 'request': str(row.request_id), 'thread': 'th', 'turnId': 'tu'})
    native = Native.__new__(Native)
    native.research_api, native.auth = file, auth
    native.state_lock = threading.RLock()
    native.closing = native.closed = False; native.blocked_reason = None
    native.process = SimpleNamespace(poll=lambda: None)
    native.active_session = str(row.id)
    native.mapping = {str(row.id): {'request': str(row.request_id), 'state': 'running', 'thread': 'th', 'turn_id': 'tu'}}
    native.rpc = lambda *args: pytest.fail('status must never call Native RPC')
    auth.research_authorized = lambda: True
    assert native.research_authorize(str(row.id), str(row.request_id))['nativeTurnId'] == 'tu'
    assert native.research_authorize(str(uuid4()), str(row.request_id)) is None
    def cancelled():
        file.revoke()
        return True
    auth.research_authorized = cancelled
    assert native.research_authorize(str(row.id), str(row.request_id)) is None


def test_receipt_uses_validated_api_result_and_never_arguments_or_forged_saved():
    arguments = {'title': 'Document', 'content': 'private raw text', 'mutationId': str(uuid4())}
    result = save_result(arguments=arguments)
    value = receipts.receipt('research_document_save', result, arguments, uuid4())
    assert value['saved'] == result['saved'] and 'private raw text' not in json.dumps(value)
    forged = deepcopy(result); forged['saved']['versionId'] = str(uuid4())
    with pytest.raises(ResearchError): receipts.receipt('research_document_save', forged, arguments, uuid4())
    row = SimpleNamespace(native_record={'turns': [{'id': 'old'}]})
    receipts.append(row, value)
    assert row.native_record['turns'] == [{'id': 'old'}]
    row.native_record['researchCalls'] = [value] * receipts.MAX_CALLS
    with pytest.raises(ResearchError): receipts.append(row, value)


def test_cli_https_fixed_route_verified_tls_and_no_token_argv(tmp_path, monkeypatch, capsys):
    row, body, _, file, _ = grant_fixture(tmp_path, monkeypatch)
    file.prepare(str(row.id), body)
    token = body.researchGrant['token']
    monkeypatch.setattr(cli, 'GRANT', str(file.path))
    monkeypatch.setattr(cli.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(b'{"type":"document"}')))
    seen = []
    class Connection:
        def __init__(self, host, port, timeout, context):
            assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
            seen.append((host, port))
        def request(self, method, path, body, headers):
            seen.append((method, path))
            assert headers['Authorization'] == 'Bearer ' + token
        def getresponse(self):
            return SimpleNamespace(status=401, read=lambda size: json.dumps({'error': {
                'code': 'research_token_expired', 'message': 'expired ' + body.researchGrant['token']}}).encode())
        def close(self): pass
    monkeypatch.setattr(cli.http.client, 'HTTPSConnection', Connection)
    assert cli.main(['research_list']) == 1
    assert seen == [('research.example.invalid', 443), ('POST', '/api/research/agent/v1/research_list')]
    output = capsys.readouterr().out
    assert body.researchGrant['token'] not in output and 'research_token_expired' in output


def test_cli_platform_trust_ignores_shell_certificate_environment(tmp_path, monkeypatch):
    monkeypatch.setenv('SSL_CERT_FILE', str(tmp_path / 'missing-ca.pem'))
    monkeypatch.setenv('SSL_CERT_DIR', str(tmp_path / 'missing-directory'))
    keylog = tmp_path / 'keys'
    monkeypatch.setenv('SSLKEYLOGFILE', str(keylog))
    context = cli.tls_context()
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    assert context.get_ca_certs() and not keylog.exists()


def test_service_auth_status_does_not_wait_on_private_store_lock(tmp_path):
    from config_tests.test_service_access import setup
    auth, home, envelope = setup(tmp_path)
    auth.install(envelope); auth.attach(home)
    assert auth.research_authorized()
    with (auth.control / 'service-access.lock').open('r+') as locked:
        fcntl.flock(locked, fcntl.LOCK_EX)
        start = time.monotonic()
        assert not auth.research_authorized() and time.monotonic() - start < .5
    auth.close()


def test_retired_research_syntax_is_held_and_redacted_even_without_prefix_coverage(monkeypatch):
    from runner import stream_retired
    _, token, _ = principal()
    monkeypatch.setattr(stream_retired, 'enabled', lambda: True)
    auth = SimpleNamespace(service=True, research_api_redaction=True,
        journal=SimpleNamespace(spans=lambda text: []), sanitize=lambda value: value,
        stream_generation=lambda: (1, 0, HoldPolicy.none()))
    sanitizer = Sanitizer(auth)
    hold = sanitizer.generation()[2]
    pending = ''; output = ''; joined = False
    text = 'before ' + token + ' after'
    for character in text:
        pending += character
        emitted, cut, joined = Holdback.plan(pending, sanitizer.spans(pending), hold, False, joined)
        output += emitted; pending = pending[cut:]
    emitted, _, _ = Holdback.plan(pending, sanitizer.spans(pending), hold, True, joined)
    assert output + emitted == 'before ' + MARKER + ' after'
    assert token not in sanitizer.whole(text)
