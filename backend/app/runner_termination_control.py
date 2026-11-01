"""Durable stop/deferred-entry bookkeeping; external proof belongs to the runner controller.

API can insert only intent columns. All callers serialize on the registry owner
lock; observer completion consumes authenticated, session-bound entry intents.
"""
from dataclasses import replace
from functools import wraps
from datetime import datetime, timezone
import json
from uuid import UUID, uuid4
from sqlalchemy import text
from .runner_registry import Registry, notify
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
    notify(db, owner)
    return stop_for(db, state)


@atomic
def request_idle_stop(db, owner, *, idle_seconds, native_idle):
    """Recheck idle under the owner lock; only record intent, never claim death."""
    require(type(idle_seconds) is int and idle_seconds > 0)
    validate(db)
    state = Registry(db)._current(owner)
    prior = stop_for(db, state)
    if prior is not None:
        return prior
    require(native_idle is True)
    require(state.phase in {Phase.READY, Phase.UNREACHABLE} and state.pod_uid is not None
            and state.active_requests == 0 and (not state.ambiguous or state.phase == Phase.UNREACHABLE))
    no_pending_work(db, state)
    now = db.execute(text('SELECT clock_timestamp()')).scalar_one()
    require(0 <= idle_seconds <= (now-state.last_activity).total_seconds())
    return request_stop(db, owner)


@atomic
def queue_entry(db, owner, key, session_hash):
    validate(db)
    require(isinstance(key,UUID) and isinstance(session_hash,str))
    registry = Registry(db)
    registry._eligible(owner)
    state = registry._current(owner)
    require(state.phase == Phase.STOPPING or stop_for(db,state) is not None)
    login = db.execute(text('''SELECT 1 FROM auth.sessions WHERE token_hash=:session
        AND account_id=:owner AND expires_at>clock_timestamp()'''),{'session':session_hash,'owner':owner}).first()
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


def terminal_receipt(stop, evidence):
    """The runner process is already gone (node reboot, container exit): no drain is possible (#61)."""
    require(type(evidence) is dict and set(evidence) <= {'podPhase', 'containers', 'podAbsent'})
    return {**drain_body(stop), 'version': 2, 'terminal': evidence}


def unavailable_receipt(stop, unavailable):
    """The runner's own drain answer when its native never started (#61 R1): no work was accepted."""
    # Any startup/supply failure except "never supplied" (that one drains with a version 1 receipt).
    require(type(unavailable) is dict and set(unavailable) == {'reason', 'clean'}
            and type(unavailable['reason']) is str and 0 < len(unavailable['reason']) <= 63
            and unavailable['reason'] != 'not_configured' and type(unavailable['clean']) is bool)
    return {**drain_body(stop), 'version': 3, 'unavailable': {'reason': unavailable['reason'], 'clean': unavailable['clean']}}


OPERATOR_HEALTH = {'available', 'reason', 'verification'}


def operator_receipt(stop, operator):
    """An operator's reclaim of a blocked runner that refuses its drain (#61 R2), with what the
    controller observed at that moment. The operator accepts losing an uncertain turn."""
    require(type(operator) is dict and set(operator) == {'health', 'drainStatus', 'at'})
    health = operator['health']
    require(type(health) is dict and set(health) == OPERATOR_HEALTH and health['available'] is False
            and type(health['reason']) is str and 0 < len(health['reason']) <= 63 and health['reason'] != 'unavailable'
            and health['verification'] in {'unverified', 'real', 'fixture'})
    require(operator['drainStatus'] in {409, 503} and type(operator['drainStatus']) is int)
    at = datetime.fromisoformat(operator['at'])
    require(at.tzinfo is not None)
    return {**drain_body(stop), 'version': 4,
            'operator': {'health': dict(health), 'drainStatus': operator['drainStatus'], 'at': operator['at']}}


def receipt_valid(stop):
    receipt = stop['receipt']
    if receipt == {**drain_body(stop), 'version': 1, 'clean': True}:
        return True
    builders = {2: ('terminal', terminal_receipt), 3: ('unavailable', unavailable_receipt), 4: ('operator', operator_receipt)}
    if type(receipt) is not dict or type(receipt.get('version')) is not int or receipt['version'] not in builders:
        return False
    key, build = builders[receipt['version']]
    try:
        return receipt == build(stop, receipt.get(key))
    except Exception:
        return False


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


def _accept_closed(db, owner, stop_id, make, replay):
    """Record a receipt for a runner that cannot drain normally; the caller has closed its work."""
    validate(db)
    registry = Registry(db)
    state = registry._current(owner)
    stop = stop_for(db, state)
    require(stop is not None and stop['stop_id'] == stop_id and stop['pod_uid'] == state.pod_uid)
    receipt = make(stop)
    no_pending_work(db, state)
    require(state.phase in {Phase.READY, Phase.UNREACHABLE, Phase.STOPPING})
    if stop['receipt'] is not None:
        require(replay(stop, receipt) and state.phase == Phase.STOPPING)
        return state
    result = change(state, phase=Phase.STOPPING, active_requests=0, ambiguous=False)
    registry._save(state, result)
    db.execute(text('UPDATE runner_termination.stops SET receipt=CAST(:receipt AS jsonb) WHERE stop_id=:stop'),
               {'stop': stop_id, 'receipt': json.dumps(receipt)})
    return result


@atomic
def accept_terminal(db, owner, stop_id, evidence):
    """Record a dead runner Pod as the stop receipt before the controller deletes it."""
    return _accept_closed(db, owner, stop_id, lambda stop: terminal_receipt(stop, evidence),
                          lambda stop, receipt: receipt_valid(stop))


def health_failure_matches(health, stop, reason):
    """/health of this exact runner says native never started, for the same reason."""
    return (type(health) is dict and health.get('ownerId') == str(stop['owner_id'])
            and health.get('stateId') == str(stop['state_id']) and health.get('podUid') == str(stop['pod_uid'])
            and health.get('available') is False and type(reason) is str and health.get('failure') == reason)


@atomic
def accept_unavailable(db, owner, stop_id, receipt, health):
    """#61 R1: accept a version 3 receipt only if /health names the same failure."""
    def make(stop):
        require(type(receipt) is dict and receipt == unavailable_receipt(stop, receipt.get('unavailable')))
        require(health_failure_matches(health, stop, receipt['unavailable']['reason']))
        return receipt
    return _accept_closed(db, owner, stop_id, make, lambda stop, value: stop['receipt'] == value)


@atomic
def accept_operator(db, owner, stop_id, operator):
    """#61 R2: an operator reclaim; never replaces an existing receipt."""
    return _accept_closed(db, owner, stop_id, lambda stop: operator_receipt(stop, operator),
                          lambda stop, value: False)


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
    require(receipt_valid(stop))  # Clean drain (v1), terminal Pod (v2), unavailable (v3) or operator (v4), #61.
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
                AND account_id=:owner AND expires_at>clock_timestamp()'''),{'session':entry['session_hash'],'owner':owner}).first()
            if login:
                result, _ = registry.enter(owner,entry['key'],lambda:datetime.now(timezone.utc))
                db.execute(text('UPDATE runner_termination.entries SET consumed_at=now() WHERE owner_id=:owner AND key=:key'),
                           {'owner':owner,'key':entry['key']})
    return result
