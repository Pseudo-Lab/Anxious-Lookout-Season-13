"""Durable stop/deferred-entry bookkeeping; external proof belongs to observer.

API can insert only intent columns. All callers serialize on the registry owner
lock; observer completion consumes authenticated, session-bound entry intents.
"""
from dataclasses import replace
from functools import wraps
from datetime import datetime, timezone
import json
from uuid import UUID, uuid4
from sqlalchemy import text
from .runner_registry import Registry
from .runner_termination_schema import installed, validate
from runner.lifecycle import Phase, Fence, change, require


def atomic(operation):
    @wraps(operation)
    def run(db, *args, **kwargs):
        with db.begin_nested():
            return operation(db, *args, **kwargs)
    return run


def stop_for(db, state):
    if state is None or not installed(db):
        return None
    validate(db)
    return db.execute(text('''SELECT * FROM runner_termination.stops
        WHERE owner_id=:owner AND state_id=:state AND generation=:generation AND completed_at IS NULL'''),
        {'owner':state.owner,'state':state.state_id,'generation':state.generation}).mappings().one_or_none()


@atomic
def request_stop(db, owner):
    validate(db)
    registry = Registry(db)
    state = registry._current(owner)
    require(state.pod_uid is not None and state.phase in {Phase.READY,Phase.UNREACHABLE,Phase.STOPPING})
    prior = stop_for(db, state)
    if prior is not None:
        require(prior['pod_uid'] == state.pod_uid)
        return prior
    db.execute(text('''INSERT INTO runner_termination.stops
        (owner_id,state_id,generation,pod_uid,stop_id,requested_revision)
        VALUES(:owner,:state,:generation,:pod,:stop,:revision)'''),
        {'owner':owner,'state':state.state_id,'generation':state.generation,'pod':state.pod_uid,
         'stop':uuid4(),'revision':state.revision})
    return stop_for(db, state)


@atomic
def queue_entry(db, owner, key, session_hash):
    validate(db)
    require(isinstance(key,UUID) and isinstance(session_hash,str))
    registry = Registry(db)
    registry._eligible(owner)
    state = registry._current(owner)
    require(state.phase == Phase.STOPPING or stop_for(db,state) is not None)
    login = db.execute(text('''SELECT 1 FROM auth.sessions WHERE token_hash=:session
        AND account_id=:owner AND expires_at>now()'''),{'session':session_hash,'owner':owner}).first()
    require(login is not None)
    # Old normal-entry keys cannot be reused to request another generation.
    prior = db.execute(text('SELECT 1 FROM runner_runtime.entry_receipts WHERE owner_id=:owner AND key=:key'),
                       {'owner':owner,'key':key}).first()
    if prior is None:
        db.execute(text('''INSERT INTO runner_termination.entries(owner_id,key,state_id,generation,session_hash)
            VALUES(:owner,:key,:state,:generation,:session) ON CONFLICT(owner_id,key) DO NOTHING'''),
            {'owner':owner,'key':key,'state':state.state_id,'generation':state.generation,'session':session_hash})
    return state


def drain_body(stop):
    return {'stopId':str(stop['stop_id']),'ownerId':str(stop['owner_id']),
            'stateId':str(stop['state_id']),'podUid':str(stop['pod_uid']),'generation':stop['generation']}


def no_pending_work(db, state):
    require(Registry(db)._pending(state.owner) == 0)
    # Preserve uncertain conversations; never synthesize a completion or error.
    require(not db.execute(text("""SELECT 1 FROM research.conversations
        WHERE owner_id=:owner AND state='running' LIMIT 1"""), {'owner':state.owner}).first())


@atomic
def accept_drain(db, owner, stop_id, receipt):
    validate(db)
    registry = Registry(db)
    state = registry._current(owner)
    stop = stop_for(db,state)
    require(stop is not None and stop['stop_id'] == stop_id and stop['pod_uid'] == state.pod_uid)
    require(type(receipt) is dict and receipt.get('clean') is True
            and type(receipt.get('version')) is int and type(receipt.get('generation')) is int
            and receipt == {**drain_body(stop),'version':1,'clean':True})
    no_pending_work(db,state)
    require(state.phase in {Phase.READY,Phase.UNREACHABLE,Phase.STOPPING})
    if stop['receipt'] is not None:
        require(stop['receipt'] == receipt and state.phase == Phase.STOPPING)
        return state
    # Native drain establishes current quiescence, not Pod death. Retain UID/slot.
    result = change(state,phase=Phase.STOPPING,active_requests=0,ambiguous=False)
    registry._save(state,result)
    db.execute(text('''UPDATE runner_termination.stops SET receipt=CAST(:receipt AS jsonb)
        WHERE stop_id=:stop'''), {'stop':stop_id,'receipt':json.dumps(receipt)})
    return result


@atomic
def release(db, owner, stop_id, proof, *, allow_entry):
    validate(db)
    require(type(allow_entry) is bool)
    registry = Registry(db)
    # Order membership before owner, matching enter/prepare/dispatch.
    membership = db.execute(text('SELECT role,is_approved FROM auth.accounts WHERE id=:owner FOR SHARE'),
                            {'owner':owner}).one()
    state = registry._current(owner)
    stop = db.execute(text('SELECT * FROM runner_termination.stops WHERE stop_id=:stop AND owner_id=:owner'),
                      {'stop':stop_id,'owner':owner}).mappings().one()
    if stop['completed_at'] is not None:
        return state  # Evidence already consumed; never release a later UID.
    require(state.phase == Phase.STOPPING and state.state_id == stop['state_id']
            and state.generation == stop['generation'] and state.pod_uid == stop['pod_uid'])
    require(stop['receipt'] == {**drain_body(stop),'version':1,'clean':True})
    require(proof['podUid'] == str(state.pod_uid) and proof['nodeName'] == stop['node_name']
            and proof['nodeUid'] == str(stop['node_uid']) and proof['bootId'] == str(stop['boot_id']))
    when = datetime.fromisoformat(proof['verifiedAt'])
    require(when.tzinfo is not None and 0 <= (datetime.now(timezone.utc)-when).total_seconds() <= 30)
    no_pending_work(db,state)
    result = registry.stopped(Fence.of(state),terminated_pod_uid=state.pod_uid,verified=True)
    db.execute(text('UPDATE runner_termination.stops SET proof=CAST(:proof AS jsonb),completed_at=now() WHERE stop_id=:stop'),
               {'stop':stop_id,'proof':json.dumps(proof)})
    # Lock child intents before reading sessions. Logout's cascading DELETE then
    # cannot commit between session validation and this release/entry commit.
    entries = db.execute(text('''SELECT * FROM runner_termination.entries WHERE owner_id=:owner
        AND state_id=:state AND generation=:generation AND consumed_at IS NULL ORDER BY key FOR UPDATE'''),
        {'owner':owner,'state':state.state_id,'generation':state.generation}).mappings().all()
    if allow_entry and membership.is_approved and membership.role in {'editor','admin'}:
        for entry in entries:
            login = db.execute(text('''SELECT 1 FROM auth.sessions WHERE token_hash=:session
                AND account_id=:owner AND expires_at>now()'''),{'session':entry['session_hash'],'owner':owner}).first()
            if login:
                result, _ = registry.enter(owner,entry['key'],lambda:datetime.now(timezone.utc))
                db.execute(text('UPDATE runner_termination.entries SET consumed_at=now() WHERE owner_id=:owner AND key=:key'),
                           {'owner':owner,'key':entry['key']})
    return result
