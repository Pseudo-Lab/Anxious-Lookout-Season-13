"""Real disposable DB, synthetic independently supplied termination evidence."""
from datetime import datetime, timezone
import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from app.runner_registry import Registry
from app.runner_termination_control import request_stop, queue_entry, accept_drain, release, drain_body, stop_for
from app.runner_termination_schema import validate
from runner.lifecycle import Phase, Refused
from config_tests.test_runner_registry import schema, admin, owner, t0, ready, read
from config_tests.test_user_runner_http import research_schema, client, identity, headers
from config_tests.test_runner_bindings import binding_schema


def login(admin, owner):
    token = uuid4().hex*2
    with admin.begin() as db:
        db.execute(text("INSERT INTO auth.sessions VALUES(:token,:owner,'csrf',now()+interval '1 hour')"),
                   {'token':token,'owner':owner})
    return token


def drained(admin, owner, t0):
    state = ready(admin,owner,t0)
    with admin.begin() as db:
        stop = request_stop(db,owner)
        assert request_stop(db,owner)['stop_id'] == stop['stop_id']
        receipt = {**drain_body(stop),'version':1,'clean':True}
        result = accept_drain(db,owner,stop['stop_id'],receipt)
        assert accept_drain(db,owner,stop['stop_id'],receipt) == result
        node, boot = uuid4(), uuid4()
        db.execute(text("UPDATE runner_termination.stops SET node_name='node',node_uid=:node,boot_id=:boot WHERE stop_id=:stop"),
                   {'node':node,'boot':boot,'stop':stop['stop_id']})
    proof = {'podUid':str(state.pod_uid),'nodeName':'node','nodeUid':str(node),'bootId':str(boot),
             'verifiedAt':datetime.now(timezone.utc).isoformat()}
    return state, stop, proof


def test_deferred_entries_release_once_to_one_new_generation(admin, owner, t0):
    state, stop, proof = drained(admin,owner,t0)
    token = login(admin,owner)
    keys = [uuid4(),uuid4()]
    with admin.begin() as db:
        for key in keys:
            queue_entry(db,owner,key,token); queue_entry(db,owner,key,token)
    with admin.begin() as db: result = release(db,owner,stop['stop_id'],proof,allow_entry=True)
    assert result.phase == Phase.PREPARING and result.generation == state.generation+1
    assert result.state_id == state.state_id and result.pod_uid is None
    with admin.begin() as db:
        assert release(db,owner,stop['stop_id'],proof,allow_entry=True) == result
        assert db.execute(text('SELECT count(*) FROM runner_termination.entries WHERE consumed_at IS NOT NULL')).scalar_one() == 2
        assert db.execute(text("SELECT count(*) FROM runner_runtime.outbox WHERE generation=2 AND kind='ensure'")).scalar_one() == 1


@pytest.mark.parametrize('cancel', ['logout','expired','revoked','disabled'])
def test_cancelled_entry_never_restarts_but_verified_stop_releases(admin,owner,t0,cancel):
    state, stop, proof = drained(admin,owner,t0)
    token = login(admin,owner)
    with admin.begin() as db:
        queue_entry(db,owner,uuid4(),token)
        if cancel == 'logout': db.execute(text('DELETE FROM auth.sessions WHERE token_hash=:token'),{'token':token})
        if cancel == 'expired': db.execute(text("UPDATE auth.sessions SET expires_at=now()-interval '1 minute'"))
        if cancel == 'revoked': db.execute(text('UPDATE auth.accounts SET is_approved=false WHERE id=:owner'),{'owner':owner})
    with admin.begin() as db: result = release(db,owner,stop['stop_id'],proof,allow_entry=cancel != 'disabled')
    assert result.phase == Phase.STOPPED and result.generation == state.generation and result.pod_uid is None


@pytest.mark.parametrize('damage', ['podUid','nodeUid','bootId','nodeName','stale'])
def test_mismatched_or_stale_proof_never_releases(admin,owner,t0,damage):
    state, stop, proof = drained(admin,owner,t0)
    if damage == 'stale': proof['verifiedAt'] = '2000-01-01T00:00:00+00:00'
    else: proof[damage] = str(uuid4())
    with pytest.raises(Refused):
        with admin.begin() as db: release(db,owner,stop['stop_id'],proof,allow_entry=True)
    assert read(admin,owner).phase == Phase.STOPPING and read(admin,owner).pod_uid == state.pod_uid


@pytest.mark.parametrize('operation', ['queue', 'release'])
def test_session_expiring_during_transaction_cannot_start_generation(admin, owner, t0, operation):
    state, stop, proof = drained(admin, owner, t0)
    token = login(admin, owner)
    with admin.begin() as db:
        if operation == 'release':
            queue_entry(db, owner, uuid4(), token)
        db.execute(text("UPDATE auth.sessions SET expires_at=clock_timestamp()+interval '0.3 seconds' WHERE token_hash=:token"),
                   {'token': token})
        db.execute(text('SELECT pg_sleep(0.4)'))
        assert db.execute(text('SELECT expires_at>now() AND expires_at<clock_timestamp() FROM auth.sessions WHERE token_hash=:token'),
                          {'token': token}).scalar_one()
        proof['verifiedAt'] = datetime.now(timezone.utc).isoformat()
        if operation == 'queue':
            with pytest.raises(Refused):
                queue_entry(db, owner, uuid4(), token)
        result = release(db, owner, stop['stop_id'], proof, allow_entry=True)
        assert result.phase == Phase.STOPPED and result.generation == state.generation
        assert result.pod_uid is None
        assert db.execute(text("SELECT count(*) FROM runner_runtime.outbox WHERE owner_id=:owner AND generation=2 AND kind='ensure'"),
                          {'owner': owner}).scalar_one() == 0


def test_http_stopping_post_records_session_bound_intent_and_returns_202(client,admin):
    owner, auth = identity(client,admin)
    state = ready(admin,owner,datetime.now(timezone.utc))
    with admin.begin() as db: request_stop(db,owner)
    before = read(admin,owner)
    key = uuid4()
    for _ in range(2):
        response = client.post('/api/research/codex/runner',json={},headers=headers(auth,key))
        assert response.status_code == 202, response.text
        assert response.json()['reason'] == 'stopping'
    assert read(admin,owner) == before
    with admin.begin() as db:
        assert db.execute(text('SELECT count(*) FROM runner_termination.entries')).scalar_one() == 1
    assert client.get('/api/research/codex/status').json()['reason'] == 'stopping'


def test_api_cannot_insert_termination_proof_or_update_receipts(admin,owner,t0):
    state = ready(admin,owner,t0)
    api = create_engine(os.environ['DATABASE_URL'])
    try:
        with api.begin() as db:
            validate(db)
            stop = request_stop(db,owner)
        with pytest.raises(DBAPIError):
            with api.begin() as db:
                db.execute(text("UPDATE runner_termination.stops SET completed_at=now()"))
        with pytest.raises(DBAPIError):
            with api.begin() as db:
                db.execute(text('''INSERT INTO runner_termination.stops
                    (owner_id,state_id,generation,pod_uid,stop_id,requested_revision,proof)
                    VALUES(:owner,:state,2,:pod,:stop,0,'{}')'''),
                    {'owner':owner,'state':state.state_id,'pod':uuid4(),'stop':uuid4()})
    finally: api.dispose()


def test_pending_conversation_prevents_drain_acceptance(admin,owner,t0):
    state = ready(admin,owner,t0)
    with admin.begin() as db:
        stop = request_stop(db,owner)
        db.execute(text("INSERT INTO research.conversations(owner_id,title,state) VALUES(:owner,'synthetic','running')"),{'owner':owner})
    with pytest.raises(Refused):
        with admin.begin() as db: accept_drain(db,owner,stop['stop_id'],{**drain_body(stop),'version':1,'clean':True})
    assert read(admin,owner) == state


def test_concurrent_release_consumes_receipt_only_once(admin,owner,t0):
    from concurrent.futures import ThreadPoolExecutor
    state, stop, proof = drained(admin,owner,t0)
    token = login(admin,owner)
    with admin.begin() as db: queue_entry(db,owner,uuid4(),token)
    def commit(_):
        with admin.begin() as db: return release(db,owner,stop['stop_id'],proof,allow_entry=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(commit,range(2)))
    assert results[0] == results[1] and results[0].generation == state.generation+1


@pytest.mark.parametrize('grant', [
    'GRANT INSERT(proof) ON runner_termination.stops TO anxious_api',
    'GRANT UPDATE(receipt) ON runner_termination.stops TO anxious_api',
    'GRANT INSERT(consumed_at) ON runner_termination.entries TO anxious_api',
    'GRANT REFERENCES(session_hash) ON runner_termination.entries TO anxious_api'])
def test_api_evidence_privilege_drift_is_rejected(admin,grant):
    with admin.connect() as db:
        transaction = db.begin()
        try:
            db.execute(text(grant))
            db.execute(text('SET LOCAL ROLE anxious_api'))
            with pytest.raises(Refused): validate(db)
        finally: transaction.rollback()


def test_observer_capability_can_release_and_reenter_without_auth_mutation(admin,owner,t0):
    from pathlib import Path
    state, stop, proof = drained(admin,owner,t0)
    token = login(admin,owner)
    with admin.begin() as db:
        queue_entry(db,owner,uuid4(),token)
        if not db.execute(text("SELECT 1 FROM pg_roles WHERE rolname='anxious_runner_observer'")).first():
            db.connection.driver_connection.execute(Path('/source/db/016_runner_observer_grants.sql').read_text())
    with admin.begin() as db:
        db.execute(text('SET LOCAL ROLE anxious_runner_observer'))
        assert release(db,owner,stop['stop_id'],proof,allow_entry=True).phase == Phase.PREPARING
    with pytest.raises(DBAPIError):
        with admin.begin() as db:
            db.execute(text('SET LOCAL ROLE anxious_runner_observer'))
            db.execute(text('UPDATE auth.accounts SET is_approved=false'))
