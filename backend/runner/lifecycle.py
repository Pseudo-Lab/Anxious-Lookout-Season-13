"""Credential-free per-owner lifecycle decisions, not a running controller.

The future registry must serialize each owner and persist state/intent/outbox in
one transaction. `replayed` comes from its durable receipt check. Fences are read
before external observations and checked against the current locked row afterward.
This module does not prove DB uniqueness, Kubernetes identity, process quiescence
or actual termination; an adapter must verify those before applying any Work.
It has no storage deletion, credential, network, global-capacity or model actions.
"""
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

IDLE_PERIOD = timedelta(minutes=30)


class Refused(ValueError):
    pass


class Phase(StrEnum):
    NEW = "not_prepared"
    PREPARING = "provisioning"
    WAITING = "capacity_wait"
    READY = "ready"
    UNREACHABLE = "runner_unreachable"
    FAILED = "runner_failed"
    STOPPING = "stopping"
    STOPPED = "idle_stopped"


def require(condition):
    if not condition:
        raise Refused("Runner lifecycle decision refused")


@dataclass(frozen=True)
class State:
    owner: UUID
    state_id: UUID
    last_activity: datetime
    phase: Phase = Phase.NEW
    generation: int = 0
    revision: int = 0
    pod_uid: UUID | None = None
    active_requests: int = 0
    ambiguous: bool = False

    def __post_init__(self):
        require(isinstance(self.owner, UUID) and isinstance(self.state_id, UUID))
        require(isinstance(self.phase, Phase))
        require(isinstance(self.last_activity, datetime) and self.last_activity.tzinfo is not None
                and self.last_activity.utcoffset() is not None)
        require(all(type(value) is int and value >= 0 for value in
                    (self.generation, self.revision, self.active_requests)))
        require(type(self.ambiguous) is bool)
        require(self.pod_uid is None or isinstance(self.pod_uid, UUID))
        require(self.phase != Phase.NEW or (self.generation == 0 and self.pod_uid is None))
        require(self.phase == Phase.NEW or self.generation > 0)
        if self.phase in {Phase.READY, Phase.UNREACHABLE, Phase.STOPPING}:
            require(self.pod_uid is not None)
        if self.active_requests or self.ambiguous:
            require(self.pod_uid is not None)
        if self.phase in {Phase.NEW, Phase.STOPPED, Phase.STOPPING, Phase.WAITING}:
            require(self.active_requests == 0 and not self.ambiguous)
        if self.phase in {Phase.STOPPED, Phase.WAITING}:
            require(self.pod_uid is None)


@dataclass(frozen=True)
class Fence:
    owner: UUID
    state_id: UUID
    generation: int
    revision: int
    pod_uid: UUID | None

    def __post_init__(self):
        require(isinstance(self.owner, UUID) and isinstance(self.state_id, UUID))
        require(all(type(value) is int and value >= 0 for value in (self.generation, self.revision)))
        require(self.pod_uid is None or isinstance(self.pod_uid, UUID))

    @classmethod
    def of(cls, state):
        return cls(state.owner, state.state_id, state.generation, state.revision, state.pod_uid)


@dataclass(frozen=True)
class Work:
    kind: str
    fence: Fence


@dataclass(frozen=True)
class Decision:
    state: State
    work: Work | None = None


def now_check(state, now):
    require(isinstance(now, datetime) and now.tzinfo is not None and now.utcoffset() is not None)
    require(now >= state.last_activity)  # Clock reversal cannot cause an early reclaim.


def fenced(state, fence):
    require(isinstance(fence, Fence) and fence == Fence.of(state))


def change(state, **fields):
    return replace(state, revision=state.revision + 1, **fields)


def entry(state, *, eligible, replayed, now):
    """Coalesce durable preparation intents; never create a second live Pod."""
    require(type(eligible) is bool and eligible)
    require(type(replayed) is bool)
    now_check(state, now)
    if replayed or state.phase in {Phase.PREPARING, Phase.WAITING}:
        return Decision(state)
    require(state.phase != Phase.STOPPING)
    if state.pod_uid is not None:
        require(state.phase in {Phase.READY, Phase.UNREACHABLE})
        return Decision(change(state, last_activity=now))
    require(state.active_requests == 0 and not state.ambiguous)
    result = change(state, generation=state.generation + 1, phase=Phase.PREPARING,
                    last_activity=now)
    return Decision(result, Work("ensure", Fence.of(result)))


def capacity(state, fence, *, eligible, admitted):
    """Caller supplies a reviewed slot decision; no default/global limit here."""
    fenced(state, fence)
    require(type(eligible) is bool and eligible and type(admitted) is bool)
    require(state.phase in {Phase.PREPARING, Phase.WAITING} and state.pod_uid is None)
    phase = Phase.PREPARING if admitted else Phase.WAITING
    result = state if state.phase == phase else change(state, phase=phase)
    return Decision(result, Work("ensure", Fence.of(result)) if admitted else None)


def observed(state, fence, *, pod_uid, ready, active_requests, ambiguous, now):
    """Apply already validated native evidence; stale evidence cannot erase busy."""
    fenced(state, fence)
    now_check(state, now)
    require(state.phase in {Phase.PREPARING, Phase.READY, Phase.UNREACHABLE})
    require(isinstance(pod_uid, UUID) and (state.pod_uid is None or state.pod_uid == pod_uid))
    require(type(ready) is bool and type(ambiguous) is bool)
    require(type(active_requests) is int and active_requests >= 0)
    activity = now if (state.active_requests or state.ambiguous) and not (active_requests or ambiguous) else state.last_activity
    return change(state, pod_uid=pod_uid, phase=Phase.READY if ready else Phase.UNREACHABLE,
                  active_requests=active_requests, ambiguous=ambiguous, last_activity=activity)


def admitted_request(state, fence, *, now):
    fenced(state, fence)
    now_check(state, now)
    require(state.phase == Phase.READY and not state.ambiguous)
    return change(state, active_requests=state.active_requests + 1, last_activity=now)


def idle_stop(state, fence, *, now, quiescence_verified):
    """Candidate stop only after current external quiescence verification."""
    fenced(state, fence)
    now_check(state, now)
    require(type(quiescence_verified) is bool and quiescence_verified)
    require(state.phase == Phase.READY and state.active_requests == 0 and not state.ambiguous)
    require(now - state.last_activity >= IDLE_PERIOD)
    result = change(state, phase=Phase.STOPPING)
    return Decision(result, Work("stop-pod-only", Fence.of(result)))


def stopped(state, fence, *, terminated_pod_uid, termination_verified):
    """API object absence alone is insufficient; adapter must confirm termination."""
    fenced(state, fence)
    require(type(termination_verified) is bool and termination_verified)
    require(state.phase == Phase.STOPPING and terminated_pod_uid == state.pod_uid)
    return Decision(change(state, phase=Phase.STOPPED, pod_uid=None))
