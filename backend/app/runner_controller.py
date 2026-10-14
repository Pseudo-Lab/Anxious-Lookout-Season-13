"""Durable one-shot Pod creation coordinator; no cluster client or worker startup.

PodPort is a trusted, bounded, namespace-scoped adapter, not browser input. It
must never retry create_once internally, delete/recreate a Pod, or adopt foreign
state. Only this coordinator may create managed Pods. A lost create response is
reconciled by lookup, never another POST. Missing evidence holds the capacity slot.
"""
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy import text

from .runner_registry import Registry
from .runner_schema import validate
from runner.lifecycle import Fence, Phase, Refused, fenced, require


@dataclass(frozen=True)
class Identity:
    owner: UUID
    state_id: UUID
    generation: int

    def __post_init__(self):
        require(isinstance(self.owner, UUID) and isinstance(self.state_id, UUID))
        require(type(self.generation) is int and self.generation > 0)

    @classmethod
    def of(cls, state):
        return cls(state.owner, state.state_id, state.generation)

    @property
    def name(self):
        # Stable across generations: an old API object prevents replacement.
        return 'runner-' + self.owner.hex


@dataclass(frozen=True)
class Pod:
    identity: Identity
    uid: UUID

    def __post_init__(self):
        require(isinstance(self.identity, Identity) and isinstance(self.uid, UUID))


class PodPort(Protocol):
    def lookup(self, identity: Identity) -> Pod | None:
        """Authoritative named GET; errors/foreign identities must raise.

        None means absent API object, never previous process termination.
        """

    def create_once(self, identity: Identity) -> Pod:
        """Exactly one bounded create attempt; no retry on any error/conflict.

        Must use identity.name and bind immutable owner/state/generation metadata,
        private retained state and transport identity. Never delete any resource.
        """


@dataclass(frozen=True)
class Ticket:
    identity: Identity
    create: bool


class Controller:
    def __init__(self, engine, pods: PodPort, clock, *, max_pods=0):
        require(type(max_pods) is int and max_pods >= 0 and callable(clock))
        self.engine, self.pods, self.clock, self.max_pods = engine, pods, clock, max_pods

    def reserve(self, fence):
        """Commit the only create permission BEFORE external I/O.

        Existing ensure outbox rows are settled as dispatch receipts, not Pod
        success. Their retained settled bit forbids a second create in this
        generation even when the first worker dies before sending its request.
        """
        require(isinstance(fence, Fence))
        with self.engine.begin() as db:
            validate(db)
            registry = Registry(db)
            registry._eligible(fence.owner)
            current = registry._current(fence.owner)
            fenced(current, fence)
            require(current.phase in {Phase.PREPARING, Phase.WAITING})
            require(current.pod_uid is None)
            identity = Identity.of(current)
            issued = db.execute(text('''SELECT 1 FROM runner_runtime.outbox
                WHERE owner_id=:owner AND state_id=:state AND generation=:generation
                AND kind='ensure' AND settled LIMIT 1'''), self._params(identity)).first()
            if issued:
                return Ticket(identity, False)
            # Every creator uses the same DB lock. Count committed reservations,
            # including uncertain calls and Pods not yet visible in the API.
            db.execute(text('SELECT pg_advisory_xact_lock(44011)'))
            occupied = db.execute(text('''SELECT count(*) FROM runner_runtime.registry r
                WHERE r.phase <> 'idle_stopped' AND (r.pod_uid IS NOT NULL OR EXISTS (
                    SELECT 1 FROM runner_runtime.outbox o WHERE o.owner_id=r.owner_id
                    AND o.state_id=r.state_id AND o.generation=r.generation
                    AND o.kind='ensure' AND o.settled))''')).scalar_one()
            admitted = occupied < self.max_pods
            current = registry.capacity(Fence.of(current), admitted=admitted)
            if not admitted:
                return None
            result = db.execute(text('''UPDATE runner_runtime.outbox SET settled=true
                WHERE owner_id=:owner AND state_id=:state AND generation=:generation
                AND kind='ensure' AND NOT settled'''), self._params(identity))
            require(result.rowcount > 0)
            return Ticket(identity, True)

    @staticmethod
    def _params(identity):
        return {'owner': identity.owner, 'state': identity.state_id, 'generation': identity.generation}

    def ensure(self, fence):
        """Reserve, make at most one create attempt, then bind uncertain runtime.

        Exceptions propagate. Callers reread the registry before reconciliation;
        they must not replay a returned Ticket themselves. No readiness, activity,
        admission, completion, stop, deletion or provider proof is inferred here.
        """
        ticket = self.reserve(fence)
        if ticket is None:
            return None  # Durable capacity_wait; GET/polling does not call this.
        identity = ticket.identity
        with self.engine.begin() as db:
            validate(db)
            registry = Registry(db)
            # Recheck membership after durable reservation and before creation.
            # Lock order matches entry/capacity; the owner lock covers external I/O.
            registry._eligible(identity.owner)
            current = registry._current(identity.owner)
            require(Identity.of(current) == identity)
            if current.pod_uid is not None:
                return current  # Another reconciler already bound the response.
            require(current.phase == Phase.PREPARING)
            pod = self.pods.lookup(identity)
            if pod is None:
                if not ticket.create:
                    # Reservation may have reached Kubernetes or may not have
                    # left the process. Neither absence nor age authorizes retry.
                    raise Refused('Pod creation outcome requires reconciliation')
                pod = self.pods.create_once(identity)
            require(isinstance(pod, Pod) and pod.identity == identity)
            # Existence is not native readiness or quiescence. Fail closed until
            # the separate evidence/transport adapter verifies the bound runtime.
            return registry.observe(Fence.of(current), pod_uid=pod.uid, ready=False,
                                    active_requests=0, ambiguous=True, verified=True,
                                    now=self.clock())
