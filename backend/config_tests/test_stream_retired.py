"""#99: unconditional service legacy exclusion with a verified current snapshot. Synthetic, no network/DB server."""
import base64
from contextlib import contextmanager
import json
import logging
import threading
import time
from uuid import uuid4

import pytest

from runner import stream_prefix
from runner.service_access import ServiceAccessAuth, ServiceJournal
from runner.stream import Holdback, SanitizeUnavailable, Sanitizer
from runner.stream_prefix import P, policy
from test_service_access import ACCOUNT, setup, refreshed
from test_runner_stream import new_hub, start_item, note, events


def long_token(expiry=None):
    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
    return '.'.join((encode({'alg': 'RS256'}), encode({
        'exp': expiry or int(time.time()) + 7200, 'padding': 'o' * 1600,
        'email': 'old-operator@example.invalid',
        'https://api.openai.com/auth': {'chatgpt_account_id': ACCOUNT}}), 'synthetic-signature'))


def envelope_with(envelope, token):
    return {**envelope, 'accessToken': token, 'snapshotId': str(uuid4())}


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv('STREAM_RETIRED', 'true')
    monkeypatch.setenv('RUNNER_STREAM_TEXT', 'true')
    auth, home, envelope = setup(tmp_path)
    old = long_token(envelope['expiresAt'])
    with monkeypatch.context() as patch:
        patch.setattr(stream_prefix, '_write', lambda *a: (_ for _ in ()).throw(OSError('full')))
        auth.install(envelope_with(envelope, old))
    auth.install(envelope)  # current covered, historical token/email remain legacy
    return auth, home, envelope, old


def hold(auth):
    return Sanitizer(auth).generation()[2]


def test_first_flush_after_install_excludes_legacy_without_iat_or_anchor(service):
    auth, _, envelope, old = service
    result = hold(auth)
    assert result.retired_state == 'unconditional' and result.retired_excluded == 2
    assert result.legacy == result.legacy_current == result.legacy_retired == 0
    assert result.start('ordinary text ' * 10) == len('ordinary text ' * 10) - (P - 1)
    assert auth.sanitize(old) == '[redacted]'
    assert not (auth.control / 'stream-retired.json').exists()


def test_new_pod_first_turn_keeps_exclusion_after_install(service):
    previous, home, envelope, old = service
    auth = ServiceAccessAuth(previous.control, previous.owner, previous.state_id, ACCOUNT)
    auth.install(envelope)
    auth.attach(home)
    assert hold(auth).legacy == 0
    hub, _ = new_hub(auth)
    hub.verified()
    start_item(hub)
    note(hub, 'item/agentMessage/delta', itemId='msg-1', delta='ordinary text ' * 10)
    hub.flush(hub.active, final=False)
    assert any(kind == 'item.delta' for _, kind, _ in events(hub))
    assert hub.active.stats['retiredExcludedRows'] == 2
    assert hub.active.stats['retiredFallbackMissing'] == hub.active.stats['retiredFallbackMismatch'] == 0
    auth.close()


@pytest.mark.parametrize('mode', ['empty', 'stale'])
def test_snapshot_claims_protect_current_even_if_current_latest_are_empty_or_stale(service, monkeypatch, mode):
    auth, _, envelope, old = service
    auth.secrets = set() if mode == 'empty' else {old}
    auth.latest_secrets = set() if mode == 'empty' else {old}
    (auth.control / stream_prefix.FILE).unlink()
    monkeypatch.setattr(stream_prefix, '_write', lambda *a: (_ for _ in ()).throw(OSError('full')))
    result = hold(auth)
    assert result.legacy_current == (len(envelope['accessToken']) if mode == 'empty' else len(old))
    assert result.legacy >= len(envelope['accessToken'])
    assert result.retired_excluded == (2 if mode == 'empty' else 1)
    for literal in (envelope['accessToken'], ACCOUNT, 'synthetic-operator@example.invalid'):
        # All split points of the still-current literals are held/redacted.
        for split in range(len(literal) + 1):
            buffer, emitted = Holdback(), []
            for chunk in (literal[:split], literal[split:]):
                buffer.feed(chunk)
                out, consumed, joined = Holdback.plan(buffer.raw, auth.journal.spans(buffer.raw), result, False, buffer.joined)
                buffer.commit(consumed, joined, len(buffer.raw))
                emitted.append(out)
            out, _, _ = Holdback.plan(buffer.raw, auth.journal.spans(buffer.raw), result, True, buffer.joined)
            assert ''.join(emitted) + out == '[redacted]'


def test_missing_snapshot_before_first_install_keeps_all_journal_rows(service):
    prior, _, _, old = service
    auth = ServiceAccessAuth(prior.control, prior.owner, prior.state_id, ACCOUNT)
    auth.journal = ServiceJournal(auth, auth.state())
    result = hold(auth)
    assert result.retired_state == 'snapshot_missing'
    assert result.retired_excluded == 0 and result.legacy == len(old)


@pytest.mark.parametrize('damage', ['missing', 'token', 'account', 'claims_cache'])
def test_snapshot_fallback_is_visible_in_summary(service, damage):
    auth, _, _, old = service
    if damage == 'missing':
        auth.snapshot = None
    elif damage == 'token':
        auth.snapshot = {**auth.snapshot, 'accessToken': old}
    elif damage == 'account':
        auth.snapshot = {**auth.snapshot, 'chatgptAccountId': 'other-account'}
    else:
        auth._stream_installed = None
    result = hold(auth)
    assert result.retired_excluded == 0 and result.legacy == len(old)
    assert result.retired_state == ('snapshot_missing' if damage == 'missing' else 'snapshot_mismatch')
    hub, _ = new_hub(auth)
    hub.verified()
    start_item(hub)
    note(hub, 'item/agentMessage/delta', itemId='msg-1', delta='ordinary text')
    hub.flush(hub.active, final=False)
    assert hub.active.stats['retiredFallbackMissing'] == int(damage == 'missing')
    assert hub.active.stats['retiredFallbackMismatch'] == int(damage != 'missing')
    assert not any(kind == 'item.delta' for _, kind, _ in events(hub))


def test_another_writer_install_makes_old_memory_fall_back(service):
    auth, _, envelope, old = service
    other = ServiceAccessAuth(auth.control, auth.owner, auth.state_id, ACCOUNT)
    updated = refreshed(envelope)
    other.install(updated)
    result = hold(auth)
    assert result.retired_state == 'snapshot_mismatch' and result.retired_excluded == 0
    assert result.legacy >= len(updated['accessToken']) or result.covered
    assert result.legacy == len(old)


@pytest.mark.parametrize('damage', ['row_mac', 'missing_db', 'context'])
def test_storage_or_context_failure_stops_text(service, damage):
    auth, _, _, _ = service
    if damage == 'row_mac':
        with auth.store.transaction() as db:
            db.execute('UPDATE literals SET mac=? WHERE length > ?', ('0' * 64, P))
    elif damage == 'missing_db':
        (auth.control / 'service-access.sqlite3').unlink()
    else:
        auth.journal.trial = str(uuid4())
    with pytest.raises(SanitizeUnavailable):
        Sanitizer(auth).generation()


def test_switch_off_matches_original_policy_identity_holdback_and_redaction(service, monkeypatch):
    auth, _, envelope, old = service
    monkeypatch.setenv('STREAM_RETIRED', 'false')
    off = hold(auth)
    original = policy(auth.journal, auth.control, list(auth.secrets), list(auth.latest_secrets))
    assert off == original and off.retired_state == 'off' and off.retired_excluded == 0
    assert off.legacy == len(old) and off.legacy_current == 0
    assert off.start('text ' * 1000) == original.start('text ' * 1000)
    assert auth.sanitize([old, envelope['accessToken']]) == ['[redacted]', '[redacted]']


def test_non_service_mode_does_not_exclude_even_when_switch_on(tmp_path, monkeypatch):
    from test_stream_prefix import journal_with, control_dir
    from test_stream_prefix_observe import FakeAuth
    monkeypatch.setenv('STREAM_RETIRED', 'true')
    old = long_token()
    journal = journal_with([old, 'synthetic-current-token'])
    auth = FakeAuth(journal, control_dir(tmp_path), {'synthetic-current-token'}, {'synthetic-current-token'})
    assert hold(auth).legacy == len(old) and hold(auth).retired_state == 'off'


def test_old_sidecar_and_markers_are_ignored_but_preserved(service):
    auth, _, _, _ = service
    sidecar = auth.control / 'stream-retired.json'
    sidecar.write_text('{old-invalid-sidecar')
    with auth.store.transaction() as db:
        auth.journal.remember(db, ['#95-retired-seen', '#95-retired-off!'])
    result = hold(auth)
    assert result.retired_excluded == 2 and result.legacy == 0
    assert sidecar.read_text() == '{old-invalid-sidecar'
    assert auth.sanitize('#95-retired-off!') == '[redacted]'


def test_current_covered_prefix_still_holds_partial_token(service):
    auth, _, envelope, _ = service
    token = envelope['accessToken']
    result = hold(auth)
    assert result.legacy == 0
    assert result.start('hello ' + token[:P + 5]) == len('hello ')


def test_redaction_still_finds_excluded_literals_and_final_command_summary(service):
    auth, _, envelope, old = service
    before = auth.journal.spans('before ' + old + ' after')
    assert hold(auth).retired_excluded == 2
    assert auth.journal.spans('before ' + old + ' after') == before
    assert auth.sanitize('before ' + old + ' after') == 'before [redacted] after'
    hub, _ = new_hub(auth)
    hub.verified()
    start_item(hub, 'cmd', 'commandExecution', command='echo ' + old)
    hub.flush(hub.active, final=True)
    raw = b''.join(frame for _, frame in hub.active.events)
    assert old.encode() not in raw and b'[redacted]' in raw


def test_install_changes_generation_even_if_prefix_and_literal_rows_are_identical(service):
    auth, _, envelope, _ = service
    before = Sanitizer(auth).generation()
    auth.install({**envelope, 'snapshotId': str(uuid4())})
    after = Sanitizer(auth).generation()
    assert before != after and before[1] == after[1]


def test_install_between_plan_and_validation_discards_the_flush(service, monkeypatch):
    auth, _, envelope, _ = service
    hub, _ = new_hub(auth)
    hub.verified()
    start_item(hub)
    note(hub, 'item/agentMessage/delta', itemId='msg-1', delta='ordinary text ' * 10)
    original, changed = hub.sanitizer.spans, []
    def rotate(text):
        if not changed:
            changed.append(True)
            auth.install({**envelope, 'snapshotId': str(uuid4())})
        return original(text)
    monkeypatch.setattr(hub.sanitizer, 'spans', rotate)
    hub.flush(hub.active, final=False)
    assert hub.active.stats['staleFlushes'] == 1
    assert not any(kind == 'item.delta' for _, kind, _ in events(hub))


def test_generation_waits_for_install_commit_and_memory_publication(service, monkeypatch):
    auth, _, envelope, _ = service
    committed, release, attempting, finished = (threading.Event() for _ in range(4))
    original = auth.store.transaction
    installer_id, result, errors = [], [], []
    @contextmanager
    def pause_after_commit(*args, **kwargs):
        with original(*args, **kwargs) as db:
            yield db
        if threading.get_ident() in installer_id:
            committed.set()
            assert release.wait(5)
    monkeypatch.setattr(auth.store, 'transaction', pause_after_commit)
    update = refreshed(envelope)
    def install():
        try:
            installer_id.append(threading.get_ident())
            auth.install(update)
        except BaseException as error:
            errors.append(error)
    def read():
        attempting.set()
        try:
            result.append(hold(auth))
        except BaseException as error:
            errors.append(error)
        finally:
            finished.set()
    installer = threading.Thread(target=install)
    installer.start()
    assert committed.wait(5)
    reader = threading.Thread(target=read)
    reader.start()
    assert attempting.wait(5)
    try:
        assert not finished.wait(.05)  # the install mutex spans durable commit through memory publication
    finally:
        release.set()
        installer.join(5)
        reader.join(5)
    assert not installer.is_alive() and not reader.is_alive() and not errors
    assert result[0].retired_state == 'unconditional' and result[0].legacy_current == 0
    assert auth.snapshot['accessToken'] == update['accessToken']


def test_one_transaction_per_generation_no_reparse_no_new_sidecar_no_value_logs(service, monkeypatch, caplog):
    auth, _, envelope, old = service
    caplog.set_level(logging.INFO, logger='uvicorn.error')
    original = auth.store.transaction
    calls = []
    @contextmanager
    def count(*args, **kwargs):
        calls.append(1)
        with original(*args, **kwargs) as db:
            yield db
    monkeypatch.setattr(auth.store, 'transaction', count)
    monkeypatch.setattr('runner.service_access.claims', lambda *a: (_ for _ in ()).throw(AssertionError('reparse')))
    for _ in range(10):
        assert hold(auth).retired_excluded == 2
    assert len(calls) == 10
    assert not (auth.control / 'stream-retired.json').exists()
    assert old not in caplog.text and envelope['accessToken'] not in caplog.text
    assert auth.journal.digest(old) not in caplog.text


def test_close_discards_install_claims_and_keeps_full_holdback(service):
    auth, _, _, old = service
    auth.close()
    assert auth._stream_installed is None and not auth.secrets and not auth.latest_secrets
    result = hold(auth)
    assert result.retired_state == 'snapshot_missing' and result.legacy == len(old)


def test_existing_turn_protection_does_not_depend_on_expiry_checks(service, monkeypatch):
    auth, _, _, _ = service
    before = hold(auth)
    monkeypatch.setattr(time, 'time', lambda: 0)
    assert hold(auth) == before
    monkeypatch.setattr(time, 'time', lambda: 10 ** 12)
    assert hold(auth) == before
