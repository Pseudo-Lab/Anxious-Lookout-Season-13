"""Offline synthetic durability/scale checks; no Native or provider calls."""
import hashlib
import json
import multiprocessing
import os
import sqlite3
from uuid import uuid4

import pytest

from app.codex_policy import CodexFailure
from ops.service_access_migrate import migrate
from runner.auth import write_private
from runner.projection import ProjectionJournal
from runner.service_access import ServiceAccessAuth, LEDGER, LEGACY, fingerprint
from config_tests.test_service_access import setup, refreshed, ACCOUNT


def restart(auth, envelope):
    result = ServiceAccessAuth(auth.control, auth.owner, auth.state_id, ACCOUNT)
    result.install(envelope)
    return result


def test_known_retired_token_relabel_refused_unchanged_and_after_restart(tmp_path):
    auth, home, old = setup(tmp_path)
    auth.install(old); auth.attach(home)
    new = refreshed(old); auth.install(new)
    before, snapshot = (auth.control / LEDGER).read_bytes(), dict(auth.snapshot)
    with pytest.raises(CodexFailure): auth.install({**old, 'snapshotId': str(uuid4())})
    assert (auth.control / LEDGER).read_bytes() == before and auth.snapshot == snapshot
    # Same current credential can be legitimately re-supplied under a new ID.
    same = {**new, 'snapshotId': str(uuid4())}
    auth.install(same); auth.check(); auth.close()
    again = restart(auth, same)
    before = (auth.control / LEDGER).read_bytes()
    with pytest.raises(CodexFailure): again.install({**old, 'snapshotId': str(uuid4())})
    assert (auth.control / LEDGER).read_bytes() == before
    assert again.snapshot['accessToken'] == new['accessToken']
    again.close()


def test_indexed_history_beyond_old_reader_limit_retains_every_receipt_and_snapshot(tmp_path):
    auth, _, envelope = setup(tmp_path); auth.install(envelope)
    requests, snapshots = [str(uuid4()) for _ in range(25000)], [str(uuid4()) for _ in range(25000)]
    retired = fingerprint('synthetic-retired-token')
    with auth.store.transaction() as db:
        db.executemany('INSERT INTO requests VALUES(?)', ((request,) for request in requests))
        db.executemany('INSERT INTO snapshots VALUES(?,?)', ((identity, retired) for identity in snapshots))
        value = auth._state(db); value['requests'] = len(requests); auth._save(db, value)
    assert (auth.control / LEDGER).stat().st_size > 1_000_000
    extra = str(uuid4()); auth.consume(extra); auth.check()
    assert len(json.dumps(auth.state())) < 4096 and auth.state()['requests'] == 25001
    auth.close(); again = restart(auth, envelope)
    with again.store.transaction() as db:
        assert {row[0] for row in db.execute('SELECT id FROM requests')} == set(requests + [extra])
        assert {row[0] for row in db.execute('SELECT id FROM snapshots')} == set(snapshots + [envelope['snapshotId']])
    for request in (requests[0], requests[-1], extra):
        with pytest.raises(CodexFailure): again.consume(request)
    again.consume(str(uuid4())); again.check(); again.close()


def test_more_than_512_rotations_keep_old_redaction_projection_and_dedup(tmp_path):
    auth, home, envelope = setup(tmp_path); auth.install(envelope); auth.attach(home)
    request = str(uuid4()); auth.consume(request)
    key, context = auth.journal.key, auth.state()['trialId']
    first, middle = envelope['accessToken'], None
    for index in range(600):
        envelope = refreshed(envelope); auth.install(envelope)
        if index == 300: middle = envelope['accessToken']
    assert auth.journal.key == key and auth.state()['trialId'] == context and auth.has_request(request)
    assert auth.sanitize([first, middle, envelope['accessToken']]) == ['[redacted]'] * 3
    plaintext = b''.join(path.read_bytes() for path in auth.control.iterdir() if path.is_file())
    assert first.encode() not in plaintext and envelope['accessToken'].encode() not in plaintext
    auth.close(); again = restart(auth, envelope); again.attach(home)
    assert again.journal.key == key and again.sanitize(first) == '[redacted]'
    assert again.has_request(request)
    again.close()


def writer(control, owner, state, envelope, requests, start, result):
    auth = ServiceAccessAuth(control, owner, state, ACCOUNT)
    auth.install(envelope); start.wait()
    outcomes = []
    for request in requests:
        try:
            auth.consume(request); outcomes.append(True)
        except CodexFailure:
            outcomes.append(False)
    result.put(outcomes)


def test_process_writers_dedup_atomically_without_lost_admissions(tmp_path):
    auth, _, envelope = setup(tmp_path); auth.install(envelope)
    ctx = multiprocessing.get_context('fork')
    start, results = ctx.Event(), ctx.Queue()
    shared = str(uuid4()); unique = [[str(uuid4()) for _ in range(20)] for _ in range(6)]
    processes = [ctx.Process(target=writer, args=(auth.control, auth.owner, auth.state_id,
                 envelope, [shared] + batch, start, results)) for batch in unique]
    for process in processes: process.start()
    start.set()
    outcomes = [results.get(timeout=30) for _ in processes]
    for process in processes:
        process.join(30); assert process.exitcode == 0
    assert sum(outcome[0] for outcome in outcomes) == 1
    assert all(all(outcome[1:]) for outcome in outcomes)
    assert auth.state()['requests'] == 121
    assert all(auth.has_request(request) for batch in unique for request in batch)
    again = restart(auth, envelope)
    with pytest.raises(CodexFailure): again.consume(shared)
    again.close()


def die_in_transaction(control, owner, state, request):
    auth = ServiceAccessAuth(control, owner, state, ACCOUNT)
    with auth.store.transaction() as db:
        db.execute('INSERT INTO requests VALUES(?)', (request,))
        value = auth._state(db); value['requests'] += 1; auth._save(db, value)
        # Force cache spill so this exercises a real hot rollback journal.
        db.execute('PRAGMA cache_size=1')
        db.executemany('INSERT INTO requests VALUES(?)', ((str(uuid4()),) for _ in range(4000)))
        os._exit(73)


def test_hot_journal_restart_rolls_back_partial_receipt_and_keeps_committed(tmp_path):
    auth, _, envelope = setup(tmp_path); auth.install(envelope)
    committed, partial = str(uuid4()), str(uuid4()); auth.consume(committed)
    process = multiprocessing.get_context('fork').Process(target=die_in_transaction,
              args=(auth.control, auth.owner, auth.state_id, partial))
    process.start(); process.join(30); assert process.exitcode == 73
    journal = auth.control / (LEDGER + '-journal')
    assert journal.exists() and journal.stat().st_mode & 0o077 == 0
    again = restart(auth, envelope)
    assert again.has_request(committed) and not again.has_request(partial) and again.state()['requests'] == 1
    again.consume(partial); again.check(); again.close()


def test_failed_transaction_leaves_snapshot_and_receipts_unchanged(tmp_path, monkeypatch):
    auth, _, envelope = setup(tmp_path); auth.install(envelope)
    before, snapshot = (auth.control / LEDGER).read_bytes(), dict(auth.snapshot)
    save = auth._save
    def failure(db, value):
        save(db, value)
        raise CodexFailure('policy_refused')
    monkeypatch.setattr(auth, '_save', failure)
    with pytest.raises(CodexFailure): auth.install(refreshed(envelope))
    assert (auth.control / LEDGER).read_bytes() == before and auth.snapshot == snapshot
    request = str(uuid4())
    with pytest.raises(CodexFailure): auth.consume(request)
    assert (auth.control / LEDGER).read_bytes() == before and not auth.has_request(request)


def legacy(auth, envelope, count=1):
    context = str(uuid4())
    journal = ProjectionJournal.create(auth.owner, context)
    journal.observe({envelope['accessToken'], ACCOUNT})
    request_ids = [str(uuid4()) for _ in range(count)]
    value = {'version': 2, 'mode': 'operator-access-snapshot', 'owner': auth.owner, 'stateId': auth.state_id,
             'accountId': ACCOUNT, 'trialId': context, 'requests': [{'id': identity} for identity in request_ids],
             'snapshots': {envelope['snapshotId']: fingerprint(envelope['accessToken'])},
             'snapshotId': envelope['snapshotId'], 'snapshotFingerprint': fingerprint(envelope['accessToken']),
             'rejectedFingerprints': [], 'deadline': envelope['expiresAt'] - 60, 'blocked': None, 'modelBlocked': False}
    write_private(auth.control / LEGACY, value)
    write_private(auth.control / 'access-projection.json', journal.payload())
    return request_ids, journal


def test_explicit_migration_above_old_reader_limit_preserves_originals_key_context_and_dedup(tmp_path):
    auth, home, envelope = setup(tmp_path)
    requests, old_journal = legacy(auth, envelope, count=25000)
    originals = {name: (auth.control / name).read_bytes() for name in (LEGACY, 'access-projection.json')}
    assert len(originals[LEGACY]) > 1_000_000
    with pytest.raises(CodexFailure): auth.install(envelope)  # No silent migration.
    migrate(auth); auth.install(envelope); auth.attach(home)
    assert auth.journal.key == old_journal.key and auth.state()['trialId'] == old_journal.trial
    assert auth.sanitize(envelope['accessToken']) == '[redacted]' and auth.state()['requests'] == len(requests)
    assert auth.journal.projection_mac('s', 't', {'turns': []}) == old_journal.projection_mac('s', 't', {'turns': []})
    for request in (requests[0], requests[-1]):
        with pytest.raises(CodexFailure): auth.consume(request)
    new = refreshed(envelope); auth.install(new)
    auth.consume(str(uuid4())); auth.close()
    before = (auth.control / LEDGER).read_bytes()
    migrate(auth)  # Re-run must not reset current state to the retained old JSON.
    assert (auth.control / LEDGER).read_bytes() == before
    for name, content in originals.items(): assert (auth.control / name).read_bytes() == content
    again = restart(auth, new)
    assert again.state()['requests'] == 25001 and again.sanitize(envelope['accessToken']) == '[redacted]'
    again.close()


def die_in_migration(control, owner, state):
    auth = ServiceAccessAuth(control, owner, state, ACCOUNT)
    auth._save = lambda *_: os._exit(74)
    migrate(auth)


def test_partial_migration_restart_reexec_preserves_inputs_and_publishes_once(tmp_path):
    auth, _, envelope = setup(tmp_path); requests, _ = legacy(auth, envelope, count=1500)
    before = (auth.control / LEGACY).read_bytes()
    process = multiprocessing.get_context('fork').Process(target=die_in_migration,
              args=(auth.control, auth.owner, auth.state_id))
    process.start(); process.join(30); assert process.exitcode == 74
    assert not (auth.control / LEDGER).exists() and (auth.control / LEGACY).read_bytes() == before
    assert list(auth.control.glob('.service-stage-*'))  # Orphans retained, never silently adopted.
    migrate(auth); migrate(auth); auth.install(envelope)
    assert auth.state()['requests'] == len(requests) and auth.has_request(requests[-1])
    assert (auth.control / LEGACY).read_bytes() == before


@pytest.mark.parametrize('target', [LEDGER, LEDGER + '-journal', 'service-access.lock'])
@pytest.mark.parametrize('damage', ['symlink', 'hardlink', 'public'])
def test_unsafe_database_or_sidecar_files_refuse_without_touching_target(tmp_path, target, damage):
    auth, _, envelope = setup(tmp_path); auth.install(envelope)
    victim = tmp_path / 'protected'; victim.write_bytes(b'protected'); victim.chmod(0o600)
    path = auth.control / target
    path.unlink(missing_ok=True)
    if damage == 'symlink': path.symlink_to(victim)
    elif damage == 'hardlink': os.link(victim, path)
    else: path.write_bytes(b'public'); path.chmod(0o644)
    with pytest.raises(CodexFailure): auth.install(envelope)
    assert victim.read_bytes() == b'protected'


def test_current_stale_process_cannot_consume_after_other_writer_rotated(tmp_path):
    auth, _, envelope = setup(tmp_path); auth.install(envelope)
    other = restart(auth, envelope); other.install(refreshed(envelope))
    before = (auth.control / LEDGER).read_bytes()
    with pytest.raises(CodexFailure): auth.consume(str(uuid4()))
    with pytest.raises(CodexFailure): auth.block('auth_expired')
    assert (auth.control / LEDGER).read_bytes() == before
    other.check(); other.close()


def test_commit_failure_does_not_ack_admission_or_publish_new_snapshot(tmp_path, monkeypatch):
    auth, _, envelope = setup(tmp_path); auth.install(envelope)
    before, snapshot = (auth.control / LEDGER).read_bytes(), dict(auth.snapshot)
    from runner import service_store
    connect = service_store.sqlite3.connect
    class RefuseCommit:
        def __init__(self, connection): self.connection = connection
        def execute(self, statement, *args):
            if statement == 'COMMIT': raise sqlite3.OperationalError('synthetic storage failure')
            return self.connection.execute(statement, *args)
        def close(self): self.connection.close()
    monkeypatch.setattr(service_store.sqlite3, 'connect', lambda *args, **kwargs: RefuseCommit(connect(*args, **kwargs)))
    request = str(uuid4())
    with pytest.raises(CodexFailure): auth.consume(request)
    with pytest.raises(CodexFailure): auth.install(refreshed(envelope))
    assert (auth.control / LEDGER).read_bytes() == before and auth.snapshot == snapshot
    monkeypatch.setattr(service_store.sqlite3, 'connect', connect)
    assert not auth.has_request(request) and auth.state()['requests'] == 0
    auth.consume(request); auth.check()


def test_fresh_service_does_not_adopt_preexisting_broker_with_new_projection_key(tmp_path):
    auth, home, envelope = setup(tmp_path)
    broker = home.parent / 'broker.json'; write_private(broker, {'protected': 'native history'})
    before = broker.read_bytes()
    auth.install(envelope)
    with pytest.raises(CodexFailure): auth.attach(home)
    assert broker.read_bytes() == before and auth.state()['nativeAttached'] is False
    auth.close()


def test_migration_retains_retired_redaction_rejection_and_cannot_reimport_changed_original(tmp_path):
    auth, _, envelope = setup(tmp_path); legacy(auth, envelope)
    old = refreshed(envelope)
    ledger_path, projection_path = auth.control / LEGACY, auth.control / 'access-projection.json'
    ledger = json.loads(ledger_path.read_text())
    ledger['snapshots'][old['snapshotId']] = fingerprint(old['accessToken'])
    ledger['rejectedFingerprints'] = [fingerprint(old['accessToken'])]
    write_private(ledger_path, ledger)
    journal = ProjectionJournal(json.loads(projection_path.read_text()), auth.owner, ledger['trialId'])
    journal.observe({old['accessToken']}); write_private(projection_path, journal.payload())
    migrate(auth); auth.install(envelope)
    assert auth.sanitize(old['accessToken']) == '[redacted]'
    before, snapshot = (auth.control / LEDGER).read_bytes(), dict(auth.snapshot)
    with pytest.raises(CodexFailure): auth.install({**old, 'snapshotId': str(uuid4())})
    assert (auth.control / LEDGER).read_bytes() == before and auth.snapshot == snapshot
    ledger['requests'].append({'id': str(uuid4())}); write_private(ledger_path, ledger)
    with pytest.raises(CodexFailure): migrate(auth)
    assert (auth.control / LEDGER).read_bytes() == before


def test_corrupt_or_duplicate_migration_input_never_publishes_or_erases_original(tmp_path):
    auth, _, envelope = setup(tmp_path); legacy(auth, envelope)
    ledger_path = auth.control / LEGACY
    ledger = json.loads(ledger_path.read_text()); ledger['requests'] *= 2
    write_private(ledger_path, ledger); before = ledger_path.read_bytes()
    with pytest.raises(CodexFailure): migrate(auth)
    assert ledger_path.read_bytes() == before and not (auth.control / LEDGER).exists()


def test_unknown_wal_files_refuse_without_deleting_recovery_evidence(tmp_path):
    auth, _, envelope = setup(tmp_path); auth.install(envelope)
    sidecar = auth.control / (LEDGER + '-wal'); sidecar.write_bytes(b'protected WAL'); sidecar.chmod(0o600)
    before = (auth.control / LEDGER).read_bytes()
    with pytest.raises(CodexFailure): auth.install(envelope)
    assert sidecar.read_bytes() == b'protected WAL' and (auth.control / LEDGER).read_bytes() == before


def test_offline_control_backup_restores_receipts_current_snapshot_and_historic_redaction(tmp_path):
    import shutil
    auth, _, old = setup(tmp_path / 'original'); auth.install(old)
    request = str(uuid4()); auth.consume(request)
    current = refreshed(old); auth.install(current)
    key, context = auth.journal.key, auth.state()['trialId']
    auth.close()  # Synthetic auth object only; no Native/process exists in this test.
    copied = tmp_path / 'offline-control-copy'; shutil.copytree(auth.control, copied)
    restored = ServiceAccessAuth(copied, auth.owner, auth.state_id, ACCOUNT)
    with restored.store.transaction() as db: restored.audit(db)
    restored.install(current)
    assert restored.journal.key == key and restored.state()['trialId'] == context
    assert restored.has_request(request) and restored.sanitize(old['accessToken']) == '[redacted]'
    with pytest.raises(CodexFailure): restored.consume(request)
    with pytest.raises(CodexFailure): restored.install({**old, 'snapshotId': str(uuid4())})
    restored.close()


def test_migration_refuses_held_runtime_lease_without_publishing(tmp_path):
    import fcntl
    auth, _, envelope = setup(tmp_path); legacy(auth, envelope)
    lease_path = auth.control / 'lease.lock'; lease_path.touch(mode=0o600)
    with lease_path.open('r+') as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(CodexFailure): migrate(auth)
    assert not (auth.control / LEDGER).exists()
    migrate(auth); auth.install(envelope)


def test_migration_cli_and_offline_check_do_not_disclose_input_values(tmp_path):
    import subprocess
    import sys
    auth, _, envelope = setup(tmp_path); legacy(auth, envelope)
    reference = tmp_path / 'account-ref'; reference.write_text(ACCOUNT); reference.chmod(0o600)
    command = [sys.executable, '-B', '-m', 'ops.service_access_migrate', '--control', str(auth.control),
               '--owner', auth.owner, '--state-id', auth.state_id, '--account-file', str(reference)]
    for suffix in ([], ['--check'], []):
        result = subprocess.run(command + suffix, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0 and 'PASS' in result.stdout
        assert ACCOUNT not in result.stdout + result.stderr and envelope['accessToken'] not in result.stdout + result.stderr
    reference.write_text('different-provider'); reference.chmod(0o600)
    result = subprocess.run(command + ['--check'], capture_output=True, text=True, timeout=30)
    assert result.returncode == 1 and 'refused' in result.stderr
    assert 'different-provider' not in result.stderr and ACCOUNT not in result.stderr
