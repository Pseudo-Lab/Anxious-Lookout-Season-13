"""Transactional PostgreSQL adapter for the per-owner lifecycle kernel.

Caller supplies an existing transaction, never a URL/credential or Kube client.
API authorization/session/CSRF must precede entry replay. Verified flags remain
adapter preconditions, not evidence of native quiescence/child termination.
"""
from dataclasses import replace
from datetime import datetime
from functools import wraps
import hashlib
from uuid import UUID, uuid4

from sqlalchemy import text

from runner.lifecycle import (State, Phase, Fence, Completion, Refused, require,
                              entry, capacity, observed, completed_request,
                              admitted_request, idle_stop, stopped, fenced)


def atomic(method):
    @wraps(method)
    def run(self, *args, **kwargs):
        require(self.db.in_transaction())
        # A caller catching Refused inside its outer transaction cannot commit
        # half an admission/state/outbox mutation.
        with self.db.begin_nested():
            return method(self, *args, **kwargs)
    return run


class Registry:
    def __init__(self, transaction):
        self.db = transaction

    def _eligible(self, owner):
        require(isinstance(owner, UUID))
        result = self.db.execute(text('SELECT role,is_approved FROM auth.accounts WHERE id=:owner FOR SHARE'),
                                 {'owner': owner}).one_or_none()
        require(result is not None and result.is_approved is True and result.role in {'editor', 'admin'})

    def _lock(self, owner):
        require(isinstance(owner, UUID))
        key = int.from_bytes(hashlib.sha256(('runner-registry-v1:' + str(owner)).encode()).digest()[:8], 'big', signed=True)
        self.db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': key})

    def read(self, owner):
        require(isinstance(owner, UUID))
        row = self.db.execute(text('SELECT * FROM runner_runtime.registry WHERE owner_id=:owner'),
                              {'owner': owner}).mappings().one_or_none()
        if row is None:
            return None
        return State(row['owner_id'], row['state_id'], row['last_activity'], Phase(row['phase']),
                     row['generation'], row['revision'], row['pod_uid'], row['active_requests'], row['ambiguous'])

    def _current(self, owner):
        self._lock(owner)
        state = self.read(owner)
        require(state is not None)
        return state

    def _save(self, previous, current, work=None):
        if current != previous:
            result = self.db.execute(text('''UPDATE runner_runtime.registry
                SET phase=:phase,generation=:generation,revision=:revision,pod_uid=:pod,
                    active_requests=:active,ambiguous=:ambiguous,last_activity=:activity
                WHERE owner_id=:owner AND state_id=:state AND generation=:old_generation AND revision=:old_revision'''),
                {'phase': current.phase.value, 'generation': current.generation, 'revision': current.revision,
                 'pod': current.pod_uid, 'active': current.active_requests, 'ambiguous': current.ambiguous,
                 'activity': current.last_activity, 'owner': previous.owner, 'state': previous.state_id,
                 'old_generation': previous.generation, 'old_revision': previous.revision})
            require(result.rowcount == 1)
        if work:
            require(work.fence == Fence.of(current))
            self.db.execute(text('''INSERT INTO runner_runtime.outbox
                (owner_id,state_id,generation,revision,pod_uid,kind)
                VALUES(:owner,:state,:generation,:revision,:pod,:kind) ON CONFLICT DO NOTHING'''),
                {'owner': current.owner, 'state': current.state_id, 'generation': current.generation,
                 'revision': current.revision, 'pod': current.pod_uid, 'kind': work.kind})
        # Completion history stays in the indexed admission table, not State.
        return replace(current, completions=())

    @atomic
    def enter(self, owner, key, now):
        self._eligible(owner)  # Membership lock precedes owner lock and replay.
        require(isinstance(key, UUID))
        self._lock(owner)
        # HTTP supplies a clock rather than an arrival timestamp. Sample after
        # serialization so a delayed older request cannot precede a newer row's
        # activity time. Explicit timestamps remain available to offline callers;
        # the kernel still refuses actual clock reversal without clamping.
        now = now() if callable(now) else now
        require(isinstance(now, datetime) and now.tzinfo is not None)
        state = self.read(owner)
        if state is None:
            state = State(owner, uuid4(), now)
            self.db.execute(text('''INSERT INTO runner_runtime.registry
                (owner_id,state_id,phase,generation,revision,active_requests,ambiguous,last_activity)
                VALUES(:owner,:state,'not_prepared',0,0,0,false,:now)'''),
                {'owner': owner, 'state': state.state_id, 'now': now})
        replayed = self.db.execute(text('SELECT 1 FROM runner_runtime.entry_receipts WHERE owner_id=:owner AND key=:key'),
                                   {'owner': owner, 'key': key}).first() is not None
        decision = entry(state, eligible=True, replayed=replayed, now=now)
        result = self._save(state, decision.state, decision.work)
        if not replayed:
            self.db.execute(text('''INSERT INTO runner_runtime.entry_receipts VALUES(:owner,:key,:generation,:now)'''),
                            {'owner': owner, 'key': key, 'generation': result.generation, 'now': now})
        return result, replayed

    @atomic
    def capacity(self, fence, *, admitted):
        self._eligible(fence.owner)
        current = self._current(fence.owner)
        decision = capacity(current, fence, eligible=True, admitted=admitted)
        return self._save(current, decision.state, decision.work)

    @atomic
    def admit(self, fence, request, now):
        self._eligible(fence.owner)
        require(isinstance(request, UUID))
        current = self._current(fence.owner)
        fenced(current, fence)
        require(not self.db.execute(text('SELECT 1 FROM runner_runtime.admissions WHERE owner_id=:owner AND request_id=:request'),
                                    {'owner': current.owner, 'request': request}).first())
        result = admitted_request(current, fence, now=now)
        self.db.execute(text('''INSERT INTO runner_runtime.admissions
            (owner_id,request_id,state_id,generation,pod_uid,admitted_at)
            VALUES(:owner,:request,:state,:generation,:pod,:now)'''),
            {'owner': current.owner, 'request': request, 'state': current.state_id,
             'generation': current.generation, 'pod': current.pod_uid, 'now': now})
        return self._save(current, result)

    @atomic
    def complete(self, fence, *, completion, verified, now):
        require(isinstance(completion, Completion) and type(verified) is bool and verified)
        current = self._current(fence.owner)
        fenced(current, fence)
        receipt = self.db.execute(text('''SELECT * FROM runner_runtime.admissions
            WHERE owner_id=:owner AND request_id=:request FOR UPDATE'''),
            {'owner': current.owner, 'request': completion.request_id}).mappings().one_or_none()
        require(receipt is not None and receipt['state_id'] == current.state_id
                and receipt['generation'] == current.generation and receipt['pod_uid'] == current.pod_uid
                and completion.completed_at >= receipt['admitted_at'])
        previous = () if receipt['completed_at'] is None else (Completion(completion.request_id, receipt['completed_at']),)
        result = completed_request(replace(current, completions=previous), fence,
                                   completion=completion, completion_verified=True, now=now)
        if receipt['completed_at'] is None:
            self.db.execute(text('''UPDATE runner_runtime.admissions SET completed_at=:completed
                WHERE owner_id=:owner AND request_id=:request AND completed_at IS NULL'''),
                {'owner': current.owner, 'request': completion.request_id, 'completed': completion.completed_at})
        return self._save(current, replace(result, completions=()))

    def _pending(self, owner):
        return self.db.execute(text('''SELECT count(*) FROM runner_runtime.admissions
            WHERE owner_id=:owner AND completed_at IS NULL'''), {'owner': owner}).scalar_one()

    @atomic
    def observe(self, fence, *, pod_uid, ready, active_requests, ambiguous, verified, now):
        require(type(verified) is bool and verified)
        require(type(active_requests) is int and active_requests >= 0 and type(ambiguous) is bool)
        current = self._current(fence.owner)
        pending = self._pending(current.owner)
        result = observed(current, fence, pod_uid=pod_uid, ready=ready,
                          active_requests=max(active_requests, pending),
                          ambiguous=ambiguous or pending > active_requests, now=now)
        return self._save(current, result)

    @atomic
    def idle(self, fence, *, now, verified):
        current = self._current(fence.owner)
        require(self._pending(current.owner) == 0)
        decision = idle_stop(current, fence, now=now, quiescence_verified=verified)
        return self._save(current, decision.state, decision.work)

    @atomic
    def stopped(self, fence, *, terminated_pod_uid, verified):
        current = self._current(fence.owner)
        require(self._pending(current.owner) == 0)
        decision = stopped(current, fence, terminated_pod_uid=terminated_pod_uid, termination_verified=verified)
        return self._save(current, decision.state, decision.work)
