"""In-cluster runner controller (#61): one resident process, no host privileges.

Replaces the host controller's Pod creation and the host observer. It wakes on Postgres
NOTIFY (`runner_registry.CHANNEL`), on Pod watch events and every 30 s, then:

- creates the Pod of every owner whose binding is published and who waits in
  provisioning/capacity_wait (`Controller.ensure`, the same capacity and create-once kernel);
- records idle stops (runner `/lifecycle/idle` evidence) and processes stops: drain, then a
  UID-preconditioned graceful DELETE (never force, never a grace override);
- treats a dead runner (Pod Failed/Succeeded, runner container terminated, or the Pod already
  gone) as terminal: its running conversations fail with `codex_runner_lost`, its open
  admissions close, the receipt records the observed state, and the Pod is deleted;
- releases a stop only on Kubernetes evidence: the Pod with that UID is 404 (kubelet finishes
  container and volume teardown before the API object goes away) and the Node is Ready.

Storage preparation and binding publication for a new owner stay a host oneshot until #63.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import select
import threading
import time
from uuid import UUID

import httpx
from sqlalchemy import text

from app.conversations import Runner
from app.runner_controller import Controller
from app.runner_limits import load_limits
from app.runner_registry import CHANNEL, Registry
from app.runner_termination_control import (accept_drain, accept_operator, accept_operator_available, accept_terminal,
                                            accept_unavailable, drain_body, health_failure_matches, no_pending_work,
                                            release, request_idle_stop, request_stop, stop_for, OPERATOR_HEALTH)
from app.runner_termination_schema import validate as validate_termination
from ops.runner_one_shot import request
from ops.runner_provision_worker import allowed
from ops.runner_replacement import Rollout, SqlStore, load_settings
from runner.lifecycle import Fence, Phase, Refused, require

TERMINAL_PHASES = {'Failed', 'Succeeded'}


class Refusal(Exception):
    """A reclaim precondition failed: a fixed code and the observed values (#66 convention)."""
    def __init__(self, code, **observed):
        super().__init__(code)
        self.code, self.observed = code, observed


def check(condition, code, **observed):
    if not condition:
        raise Refusal(code, **observed)


class Stuck(Refused):
    """#114: a step that cannot advance, with a fixed reason code for the controller log. It never
    carries exception text, URLs, tokens or conversation content."""
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


@contextmanager
def stage(reason):
    """Any failure inside becomes `Stuck(reason)`; an inner Stuck keeps its own reason."""
    try:
        yield
    except Stuck:
        raise
    except Exception:
        raise Stuck(reason) from None


def call(runner, name, method, path, body):
    """One runner call; a failure becomes a fixed reason: `<name>_status_<code>` for an HTTP answer
    (`drain_conflict` for a 409 drain), `<name>_unreachable` for a transport error, `<name>_invalid`
    for an answer that fails the ownership/state checks."""
    try:
        return runner.call(method, path, body)
    except httpx.HTTPStatusError as failure:
        status = failure.response.status_code
        raise Stuck('drain_conflict' if (name, status) == ('drain', 409) else name+'_status_'+str(status)) from None
    except httpx.HTTPError:
        raise Stuck(name+'_unreachable') from None
    except Exception:
        raise Stuck(name+'_invalid') from None


def reason_of(failure):
    return failure.reason if isinstance(failure, Stuck) else 'error:'+type(failure).__name__


def lock_running(db, owner):
    """#114: take the owner's running conversation rows BEFORE the registry owner lock, in every
    transaction that fails leftover turns. A conversation read in the API holds its row lock and asks the
    runner on a second connection that takes the owner lock; the other order here would make the two
    connections wait on each other, which PostgreSQL cannot detect as a deadlock. lock_timeout turns any
    wait that remains into a retried pass (`conversation_locked`) instead of a stuck controller."""
    with stage('conversation_locked'):
        db.execute(text("SET LOCAL lock_timeout='5s'"))
        db.execute(text("""SELECT 1 FROM research.conversations
            WHERE owner_id=:owner AND state='running' FOR UPDATE"""), {'owner': owner}).all()


def running_conversations(db, owner):
    return db.execute(text("""SELECT count(*) FROM research.conversations
        WHERE owner_id=:owner AND state='running'"""), {'owner': owner}).scalar_one()


# Step outcomes that are not failures but say why no stop was recorded (#114).
WAITING = {'runner_not_idle', 'stale_running_conversations'}
RESCAN_SECONDS = 30


def clock():
    return datetime.now(timezone.utc)


def terminal_evidence(pod):
    """Observed state of a dead runner Pod, or None while the runner process may be alive."""
    if pod is None:
        return {'podAbsent': True}
    phase = (pod.get('status') or {}).get('phase')
    statuses = (pod.get('status') or {}).get('containerStatuses') or []
    runner = [status for status in statuses if status.get('name') == 'runner']
    runner_dead = bool(runner) and 'terminated' in (runner[0].get('state') or {})
    if phase not in TERMINAL_PHASES and not runner_dead:
        return None
    containers = {}
    for status in statuses:
        state = status.get('state') or {}
        terminated = state.get('terminated') or {}
        containers[status.get('name', '')[:63]] = (
            {'exitCode': terminated.get('exitCode'), 'reason': str(terminated.get('reason', ''))[:63]}
            if terminated else {'state': next(iter(state), 'unknown')})
    return {'podPhase': phase, 'containers': containers}


class NodeEvidence:
    """Node identity and readiness from the API; replaces host boot_id/proc/CRI inputs."""
    def __init__(self, node_name):
        require(isinstance(node_name, str) and node_name and '/' not in node_name)
        self.node_name = node_name

    def identity(self, client, now):
        node = request(client, 'GET', '/api/v1/nodes/'+self.node_name)
        require(node.get('kind') == 'Node' and node['metadata']['name'] == self.node_name
                and not node['metadata'].get('deletionTimestamp'))
        ready = [item for item in node['status']['conditions'] if item['type'] == 'Ready']
        require(len(ready) == 1 and ready[0]['status'] == 'True')
        heartbeat = datetime.fromisoformat(ready[0]['lastHeartbeatTime'].replace('Z', '+00:00'))
        require(heartbeat.tzinfo is not None and 0 <= (now - heartbeat).total_seconds() <= 600)
        return {'nodeName': self.node_name, 'nodeUid': str(UUID(node['metadata']['uid'])),
                'bootId': str(UUID(node['status']['nodeInfo']['bootID']))}


class CheckedPods:
    """#101: the PodPort for one ensure, whose create re-reads the binding image just before the POST.

    Pool.bound resolved the plan before Controller.ensure took the registry owner lock. create_once runs
    inside that locked transaction, and runner_rollout.move_image takes the same lock, so a binding read
    here on a fresh connection is the one in force until the create commits: a plan resolved before a
    move is refused instead of creating a Pod whose image no longer matches its binding."""
    def __init__(self, pods, engine, plan):
        self.pods, self.engine, self.plan = pods, engine, plan

    def lookup(self, identity):
        return self.pods.lookup(identity)

    def create_once(self, identity):
        with self.engine.connect() as db:
            image = db.execute(text("SELECT plan->>'image' FROM runner_bindings.bindings WHERE owner_id=:owner"),
                               {'owner': identity.owner}).scalar_one_or_none()
        if image != self.plan.image:
            raise Refused('Binding image changed before creation')
        return self.pods.create_once(identity)


class RolloutCluster:
    """#101: the cluster side of a rollout pass, through the same observer pool and runner client."""
    def __init__(self, service):
        self.service = service

    @contextmanager
    def bound(self, view):
        with self.service.observer_pool.bound(view.owner) as runtime:
            runtime.namespace()
            yield runtime

    def pod(self, view):
        with self.bound(view) as runtime:
            return self.service.pod(runtime)

    def terminal(self, obj):
        return terminal_evidence(obj)

    def _runner(self, runtime, view, obj):
        return self.service.runner(self.service.address(runtime, obj), runtime.token, view.owner, str(view.state_id))

    def health(self, view, obj):
        with self.bound(view) as runtime:
            health = self._runner(runtime, view, obj).call('GET', '/health', None)
        require(health.get('podUid') == obj['metadata']['uid'])
        return health

    def idle(self, view, obj, seconds):
        with self.bound(view) as runtime:
            evidence = self._runner(runtime, view, obj).call('POST', '/lifecycle/idle', {'idleSeconds': seconds})
        expected = {'ownerId': str(view.owner), 'stateId': str(view.state_id), 'podUid': obj['metadata']['uid']}
        require(evidence in ({**expected, 'idle': True}, {**expected, 'idle': False}))
        return evidence['idle']

    def interrupt(self, view, obj, session, request):
        with self.bound(view) as runtime:
            self._runner(runtime, view, obj).call('POST', '/sessions/'+str(session)+'/interrupt', {'requestId': str(request)})


class Service:
    def __init__(self, controller_engine, observer_engine, controller_pool, observer_pool, node,
                 *, runner=Runner, now=clock, rollout=None):
        self.controller_engine, self.observer_engine = controller_engine, observer_engine
        self.controller_pool, self.observer_pool = controller_pool, observer_pool
        self.node, self.runner, self.now = node, runner, now
        self.not_idle = {}  # owner -> first time its runner refused idle with no running conversation
        self.rollout = rollout  # #101 ops.runner_replacement.Rollout, or None without a target

    # Creation -------------------------------------------------------------
    def waiting(self):
        """Owners whose binding is published and who wait for a Pod."""
        with self.controller_engine.begin() as db:
            return db.execute(text('''SELECT r.owner_id FROM runner_runtime.registry r
                JOIN auth.accounts a ON a.id=r.owner_id
                JOIN runner_bindings.bindings b ON b.owner_id=r.owner_id AND b.state_id=r.state_id
                WHERE r.phase IN ('provisioning','capacity_wait') AND r.pod_uid IS NULL
                AND a.is_approved AND a.role IN ('editor','admin') ORDER BY r.owner_id LIMIT 100''')).scalars().all()

    def ensure(self, owner):
        require(allowed(owner))
        rollout = getattr(self, 'rollout', None)  # Services built without __init__ (tests) have none.
        if rollout is not None:
            rollout.before_create(owner)  # #101: a waiting owner moves to the target before its plan is read.
        with self.controller_pool.bound(owner) as runtime:
            runtime.namespace()
            with self.controller_engine.begin() as db:
                state = Registry(db)._current(owner)
                # Before any reservation: a plan resolved ahead of a move waits for the next pass. The
                # locked recheck in CheckedPods only meets a mover running concurrently with this process
                # (an operator tool); it then refuses after the reservation, which is fail-closed.
                image = db.execute(text("SELECT plan->>'image' FROM runner_bindings.bindings WHERE owner_id=:owner"),
                                   {'owner': owner}).scalar_one_or_none()
            if image != runtime.plan.image:
                raise Stuck('binding_moved')
            pods = CheckedPods(runtime.pods, self.controller_engine, runtime.plan)
            return Controller(self.controller_engine, pods, self.now,
                              max_pods=load_limits().max_pods).ensure(Fence.of(state))

    # Stopping -------------------------------------------------------------
    def candidates(self, idle_seconds):
        with self.observer_engine.begin() as db:
            validate_termination(db)
            return db.execute(text('''SELECT owner_id FROM runner_termination.stops WHERE completed_at IS NULL
                UNION SELECT owner_id FROM runner_runtime.registry
                    WHERE phase IN ('ready','runner_unreachable','stopping') AND pod_uid IS NOT NULL
                ORDER BY owner_id LIMIT 100''')).scalars().all()

    def pod(self, runtime):
        return runtime.pods._request('GET', 'pods', runtime.plan.identity.name, missing=True)

    def address(self, runtime, obj):
        address = ipaddress.ip_address(obj['status']['podIP'])
        require(address in runtime.network and not address.is_loopback and not address.is_link_local)
        return 'http://' + ('['+str(address)+']' if address.version == 6 else str(address)) + ':8080'

    def idle(self, runtime, owner, state, obj, idle_seconds):
        """Record an idle stop when the live runner confirms quiescence. Returns None once the stop
        intent is recorded, else the outcome that says why not (#114: never an exception for a busy runner)."""
        if state.phase not in {Phase.READY, Phase.UNREACHABLE} or state.active_requests or (
                state.ambiguous and state.phase != Phase.UNREACHABLE):
            return 'running'
        if (self.now() - state.last_activity).total_seconds() < idle_seconds:
            return 'running'
        with self.observer_engine.begin() as db:
            running = running_conversations(db, owner)
        with stage('pod_address_invalid'):
            address = self.address(runtime, obj)
        evidence = call(self.runner(address, runtime.token, owner, str(state.state_id)),
                        'idle', 'POST', '/lifecycle/idle', {'idleSeconds': idle_seconds})
        expected = {'ownerId': str(owner), 'stateId': str(state.state_id), 'podUid': str(state.pod_uid)}
        if evidence == {**expected, 'idle': False}:
            if running:
                self.not_idle.pop(owner, None)
                return 'runner_not_idle'  # A turn is running: ordinary busy.
            # No running conversation, yet the runner keeps refusing: a turn that ended less than
            # idle_seconds ago is ordinary; twice that long is a runner that will not go idle.
            first = self.not_idle.setdefault(owner, self.now())
            if (self.now() - first).total_seconds() >= 2 * idle_seconds:
                raise Stuck('idle_refused')
            return 'runner_not_idle'
        self.not_idle.pop(owner, None)
        if evidence != {**expected, 'idle': True}:
            raise Stuck('idle_evidence_mismatch')
        if running:
            # The runner holds no turn but a conversation is still `running` in the database: the
            # API resolves it on the next read; an idle stop waits for that (no_pending_work).
            return 'stale_running_conversations'
        with self.observer_engine.begin() as db, stage('idle_stop_refused'):
            request_idle_stop(db, owner, idle_seconds=idle_seconds, native_idle=True)
        return None

    def close_dead_work(self, db, state):
        """A runner that cannot finish its turns: fail them visibly and close their admissions.
        Returns how many conversations it failed."""
        failed = db.execute(text('''UPDATE research.conversations SET state='failed', error_code='codex_runner_lost',
            version=version+1, updated_at=now(), tool_token_hash=NULL
            WHERE owner_id=:owner AND state='running' '''), {'owner': state.owner}).rowcount
        db.execute(text('''UPDATE runner_runtime.admissions SET completed_at=greatest(now(), admitted_at)
            WHERE owner_id=:owner AND state_id=:state AND generation=:generation AND completed_at IS NULL'''),
            {'owner': state.owner, 'state': state.state_id, 'generation': state.generation})
        return failed

    def step(self, owner, idle_seconds):
        """Advance one owner's stop by at most one destructive action. Returns a fixed outcome; a step
        that cannot advance raises Stuck with a fixed reason (#114)."""
        with self.observer_pool.bound(owner) as runtime:
            runtime.namespace()
            obj = self.pod(runtime)
            with self.observer_engine.begin() as db:
                state = Registry(db)._current(owner)
                stop = stop_for(db, state)
            if state.pod_uid is None:
                return 'no_pod'
            if obj is not None and obj['metadata']['uid'] != str(state.pod_uid):
                raise Stuck('pod_uid_mismatch')  # Never act on a replaced object.
            dead = terminal_evidence(obj)
            if stop is None and dead is None and (obj.get('status') or {}).get('phase') != 'Running':
                return 'starting'  # Pending: no address to ask yet, and nothing to stop.
            if stop is None:
                if dead is not None:
                    with self.observer_engine.begin() as db, stage('stop_refused'):
                        request_stop(db, owner)
                else:
                    waiting = self.idle(runtime, owner, state, obj, idle_seconds)
                    if waiting is not None:
                        return waiting
                with self.observer_engine.begin() as db:
                    stop = stop_for(db, Registry(db)._current(owner))
            if obj is not None and obj['metadata'].get('deletionTimestamp'):
                return 'awaiting_pod_deletion'  # Kubelet is stopping it; the watch wakes us.
            if obj is not None:
                if stop['receipt'] is None:
                    if dead is not None:
                        with self.observer_engine.begin() as db, stage('receipt_rejected'):
                            lock_running(db, owner)  # rows before the owner lock (#114)
                            state = Registry(db)._current(owner)
                            self.close_dead_work(db, state)
                            accept_terminal(db, owner, stop['stop_id'], dead)
                    else:
                        with stage('pod_address_invalid'):
                            address = self.address(runtime, obj)
                        runner = self.runner(address, runtime.token, owner, str(state.state_id))
                        health = call(runner, 'health', 'GET', '/health', None)  # Before the drain: R1 needs the runner's own failure.
                        receipt = call(runner, 'drain', 'POST', '/lifecycle/drain', drain_body(stop))
                        if type(receipt) is dict and receipt.get('version') == 3:
                            # #61 R1: native never started. Accept only if /health names the same failure.
                            if not health_failure_matches(health, stop, (receipt.get('unavailable') or {}).get('reason')):
                                return 'unavailable_reason_mismatch'
                            with self.observer_engine.begin() as db, stage('receipt_rejected'):
                                lock_running(db, owner)  # rows before the owner lock (#114)
                                state = Registry(db)._current(owner)
                                self.close_dead_work(db, state)
                                accept_unavailable(db, owner, stop['stop_id'], receipt, health)
                        else:
                            # #114 ①: the runner drained, so no turn is in flight in it. The API recorded each
                            # running turn's actual result before it fenced the runner (user_runners.stop_runtime);
                            # a conversation still `running` here is closed, in the same transaction that
                            # accepts this stop's own receipt. A refused receipt rolls the closing back.
                            with self.observer_engine.begin() as db, stage('receipt_rejected'):
                                lock_running(db, owner)  # rows before the owner lock (#114)
                                state = Registry(db)._current(owner)
                                self.close_dead_work(db, state)
                                accept_drain(db, owner, stop['stop_id'], receipt)
                with self.observer_engine.begin() as db, stage('node_not_ready'):
                    self.anchor(db, runtime, stop)
                # Receipt and dispatch fence are committed; ordinary graceful deletion of that UID only.
                with stage('pod_delete_failed'):
                    runtime.pods._request('DELETE', 'pods', runtime.plan.identity.name,
                        body={'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {'uid': str(state.pod_uid)}})
                return 'deleting'
            # The Pod with this UID is gone: kubelet has finished it, or it vanished before any drain.
            with self.observer_engine.begin() as db, stage('release_refused'):
                lock_running(db, owner)  # rows before the owner lock (#114)
                state = Registry(db)._current(owner)
                if stop['receipt'] is None:
                    self.close_dead_work(db, state)
                    accept_terminal(db, owner, stop['stop_id'], {'podAbsent': True})
                proof = self.proof(db, runtime, state, stop)
                release(db, owner, stop['stop_id'], proof, allow_entry=allowed(owner))
            return 'released'

    def anchor(self, db, runtime, stop):
        current = db.execute(text('SELECT node_name,node_uid,boot_id FROM runner_termination.stops WHERE stop_id=:stop FOR UPDATE'),
                             {'stop': stop['stop_id']}).mappings().one()
        if current['node_uid'] is None:
            node = self.node.identity(runtime.pods.client, self.now())
            db.execute(text('''UPDATE runner_termination.stops SET node_name=:name,node_uid=:uid,boot_id=:boot
                WHERE stop_id=:stop AND node_uid IS NULL'''),
                {'name': node['nodeName'], 'uid': node['nodeUid'], 'boot': node['bootId'], 'stop': stop['stop_id']})

    def proof(self, db, runtime, state, stop):
        require(self.pod(runtime) is None)
        node = self.node.identity(runtime.pods.client, self.now())
        require(self.pod(runtime) is None)  # Still gone after the Node check.
        # A reboot between deletion and this proof changes bootId; the Pod's absence is the evidence,
        # so the stop is re-anchored to the Node that reports it.
        db.execute(text('''UPDATE runner_termination.stops SET node_name=:name,node_uid=:uid,boot_id=:boot
            WHERE stop_id=:stop'''), {'name': node['nodeName'], 'uid': node['nodeUid'], 'boot': node['bootId'],
                                      'stop': stop['stop_id']})
        return {'podUid': str(state.pod_uid), **node, 'verifiedAt': self.now().isoformat(), 'evidence': 'pod-deleted'}

    # Operator reclaim (#61 R2) ---------------------------------------------
    def reclaim(self, owner, pod_uid, *, runner_available=False):
        """Reclaim a Running runner that is blocked and refuses its drain, on an operator's command.

        Every precondition is observed now, by this process. On success the version 4 receipt holds
        what was observed; the running loop then deletes that Pod UID, waits for 404 and releases,
        as on the dead-runner path. The operator accepts losing an uncertain turn.

        `runner_available` (#114, the operator's explicit `--runner-available`) is a separate path for
        a runner that reports itself available but refuses its drain with 409 while no conversation
        of the owner is running in the database (an older image blocks drain on a settled `failed`
        session). It records a version 5 receipt and fails no conversation; version 4 is unchanged.
        """
        require(isinstance(owner, UUID) and isinstance(pod_uid, UUID) and type(runner_available) is bool)
        with self.observer_pool.bound(owner) as runtime:
            runtime.namespace()
            obj = self.pod(runtime)
            with self.observer_engine.begin() as db:
                state = Registry(db)._current(owner)
                stop = stop_for(db, state)
            seen = {'phase': str(state.phase), 'registryPodUid': None if state.pod_uid is None else str(state.pod_uid)}
            check(state.pod_uid == pod_uid, 'pod_uid_mismatch', requested=str(pod_uid), **seen)
            check(state.phase in {Phase.READY, Phase.UNREACHABLE, Phase.STOPPING}, 'phase', **seen)
            live = None if obj is None else obj['metadata'].get('uid')
            check(live == str(pod_uid), 'pod_missing' if obj is None else 'pod_uid_mismatch', livePodUid=live, **seen)
            check(not obj['metadata'].get('deletionTimestamp'), 'pod_deleting',
                  deletionTimestamp=obj['metadata'].get('deletionTimestamp'))
            dead = terminal_evidence(obj)
            check(dead is None, 'runner_dead', evidence=dead)  # The loop's dead-runner path handles it.
            check(stop is None or stop['receipt'] is None, 'receipt_exists',
                  receiptVersion=None if stop is None else (stop['receipt'] or {}).get('version'))
            try:
                address = self.address(runtime, obj)
            except Exception:
                raise Refusal('pod_address_invalid', podPhase=(obj.get('status') or {}).get('phase')) from None
            runner = self.runner(address, runtime.token, owner, str(state.state_id))
            try:
                health = runner.call('GET', '/health', None)
            except httpx.HTTPStatusError as failure:
                raise Refusal('health_failed', status=failure.response.status_code) from None
            except Exception:
                raise Refusal('health_failed', status=None) from None
            observed = {key: health.get(key) for key in sorted(OPERATOR_HEALTH)}
            check(health.get('podUid') == str(pod_uid), 'health_identity', healthPodUid=health.get('podUid'))
            if runner_available:
                check(observed['available'] is True and observed['reason'] is None, 'runner_not_available', health=observed)
                check(observed['verification'] in {'unverified', 'real', 'fixture'}, 'health_invalid', health=observed)
                with self.observer_engine.begin() as db:
                    running = running_conversations(db, owner)
                # A running conversation may be a turn in flight: never with this flag.
                check(running == 0, 'running_conversations', runningConversations=running, health=observed)
            else:
                check(observed['available'] is False, 'runner_available', health=observed)
                check(observed['reason'] != 'unavailable', 'runner_stopping', health=observed)
                check(type(observed['reason']) is str and 0 < len(observed['reason']) <= 63
                      and observed['verification'] in {'unverified', 'real', 'fixture'}, 'health_invalid', health=observed)
            opened = stop is None
            with self.observer_engine.begin() as db:
                stop = request_stop(db, owner)  # Intent only; nothing is deleted on a refusal below.
            after = {'stopId': str(stop['stop_id']), 'stopOpenedByThisRun': opened, 'health': observed}
            try:
                receipt = runner.call('POST', '/lifecycle/drain', drain_body(stop))
            except httpx.HTTPStatusError as failure:
                status = failure.response.status_code
            except Exception:
                raise Refusal('drain_unreachable', **after) from None
            else:
                # The runner drained after all: the loop accepts that receipt on its normal path.
                raise Refusal('drain_accepted', receiptVersion=(receipt or {}).get('version'), **after)
            if runner_available:
                check(status == 409, 'drain_status', drainStatus=status, **after)
                operator = {'health': observed, 'drainStatus': 409, 'runningConversations': 0, 'at': self.now().isoformat()}
                with self.observer_engine.begin() as db:
                    state = Registry(db)._current(owner)
                    require(state.pod_uid == pod_uid)
                    running = running_conversations(db, owner)  # Again, in the accepting transaction.
                    check(running == 0, 'running_conversations', runningConversations=running, **after)
                    accept_operator_available(db, owner, stop['stop_id'], operator)
                return {'result': 'accepted', 'owner': str(owner), 'podUid': str(pod_uid), 'stopId': str(stop['stop_id']),
                        'stopOpenedByThisRun': opened, 'failedConversations': 0, 'receiptVersion': 5, 'operator': operator}
            check(status in {409, 503}, 'drain_status', drainStatus=status, **after)
            operator = {'health': observed, 'drainStatus': status, 'at': self.now().isoformat()}
            with self.observer_engine.begin() as db:
                lock_running(db, owner)  # rows before the owner lock (#114)
                state = Registry(db)._current(owner)
                require(state.pod_uid == pod_uid)
                lost = db.execute(text("""SELECT count(*) FROM research.conversations
                    WHERE owner_id=:owner AND state='running'"""), {'owner': owner}).scalar_one()
                self.close_dead_work(db, state)
                accept_operator(db, owner, stop['stop_id'], operator)
        return {'result': 'accepted', 'owner': str(owner), 'podUid': str(pod_uid), 'stopId': str(stop['stop_id']),
                'stopOpenedByThisRun': opened, 'failedConversations': lost, 'receiptVersion': 4, 'operator': operator}

    # Loop -----------------------------------------------------------------
    def reconcile(self):
        """One pass. Returns (owner, result, reason): reason is a fixed code (#114) for a step that
        could not advance (`reconciliation_required`) or for a runner that is not stopped yet
        (`running` with `runner_not_idle` / `stale_running_conversations`), else None."""
        outcomes = []
        idle_seconds = load_limits().idle_seconds
        for owner in self.candidates(idle_seconds):
            try:
                outcome = self.step(owner, idle_seconds)
            except Exception as failure:
                outcomes.append((owner, 'reconciliation_required', reason_of(failure)))
            else:
                outcomes.append((owner, 'running', outcome) if outcome in WAITING else (owner, outcome, None))
        rollout = getattr(self, 'rollout', None)  # Services built without __init__ (tests) have none.
        if rollout is not None:
            # #101: after the stop steps (a release this pass is seen) and before creation (a re-entered
            # owner moves first). A failing pass never blocks stops or creation.
            try:
                outcomes += [line for line in rollout.run() if line[2] != 'converged']
            except Exception as failure:
                outcomes.append((None, 'rollout', 'stuck:'+reason_of(failure)))
        for owner in self.waiting():
            if not allowed(owner):
                continue
            try:
                self.ensure(owner)
                outcomes.append((owner, 'ensured', None))
                ok = True
            except Exception as failure:
                outcomes.append((owner, 'reconciliation_required', 'ensure:'+reason_of(failure)))
                ok = reason_of(failure) == 'binding_moved'  # A retried pass, not a failed creation.
            if rollout is not None and rollout.settings.fingerprint is not None:
                try:
                    halted = rollout.ensure_result(owner, ok, self.binding_image(owner) == rollout.settings.fingerprint['image'])
                except Exception:
                    halted = None  # Bookkeeping only; the next pass counts again.
                if halted is not None:
                    outcomes.append((owner, 'rollout_halted', halted))
        return outcomes

    def binding_image(self, owner):
        with self.controller_engine.begin() as db:
            return db.execute(text("SELECT plan->>'image' FROM runner_bindings.bindings WHERE owner_id=:owner"),
                              {'owner': owner}).scalar_one_or_none()


class Wake:
    """One event set by NOTIFY, the Pod watch or the rescan timer."""
    def __init__(self):
        self.event = threading.Event()

    def listen(self, dsn):
        import psycopg
        while True:
            try:
                with psycopg.connect(dsn, autocommit=True) as connection:
                    connection.execute('LISTEN '+CHANNEL)
                    for _ in connection.notifies():
                        self.event.set()
            except Exception:
                time.sleep(5)

    def watch(self, client, namespace, *, sleep=time.sleep, rounds=None):
        """Only Pod change events wake reconcile. HTTP errors, Kubernetes ERROR events and
        streams that end without an event back off (1 s doubling to 60 s), so a revoked
        token or an API outage cannot turn into a busy loop against the API and DB."""
        path = ('/api/v1/namespaces/'+namespace+'/pods?watch=1&allowWatchBookmarks=false'
                '&labelSelector=anxious.io%2Fmanaged%3Duser-runner&timeoutSeconds=300')
        delay = 1
        while rounds is None or rounds > 0:
            if rounds is not None:
                rounds -= 1
            delivered = False
            try:
                with client.stream('GET', path, timeout=None) as response:
                    if response.status_code == 200:
                        for line in response.iter_lines():
                            if not line:
                                continue
                            kind = json.loads(line).get('type')
                            if kind not in {'ADDED', 'MODIFIED', 'DELETED'}:
                                break  # ERROR (e.g. 410 Gone) or unknown: reconnect after backoff.
                            delivered = True
                            self.event.set()
            except Exception:
                pass
            if delivered:
                delay = 1
            else:
                sleep(delay)
                delay = min(delay*2, 60)


def build():
    from sqlalchemy import create_engine
    from app.runner_pool import Pool
    from app.settings import secret
    sa = Path('/var/run/secrets/kubernetes.io/serviceaccount')
    api = 'https://'+os.environ['KUBERNETES_SERVICE_HOST']+':'+os.environ['KUBERNETES_SERVICE_PORT']
    config = {'version': 2, 'api': api, 'namespace': os.environ['RUNNER_NAMESPACE'],
              'namespaceUid': os.environ['RUNNER_NAMESPACE_UID'], 'podCIDR': os.environ['RUNNER_POD_CIDR'],
              'caFile': str(sa/'ca.crt'), 'kubeTokenFile': str(sa/'token')}
    controller_engine = create_engine(secret('CONTROLLER_DATABASE_URL'), hide_parameters=True)
    observer_engine = create_engine(secret('OBSERVER_DATABASE_URL'), hide_parameters=True)
    service = Service(controller_engine, observer_engine, Pool(controller_engine, config), Pool(observer_engine, config),
                      NodeEvidence(os.environ['RUNNER_NODE_NAME']))
    # #101: invalid rollout settings, or a target without the reviewed rollout schema, refuse startup.
    settings = load_settings()
    if settings.fingerprint is not None:
        store = SqlStore(observer_engine)
        store.validate()
        service.rollout = Rollout(store, RolloutCluster(service), settings, clock)
    return service, config, sa


def rollout_command(service, action):
    """`rollout status|halt|resume` (#101). halt: canary/rolling -> halted (reason `operator`); resume:
    halted or canary -> rolling (the operator accepts the canary). Returns (rc, report)."""
    store = service.rollout.store if service.rollout is not None else SqlStore(service.observer_engine)
    target = store.current()
    if target is None:
        return 3, {'stage': 'rollout', 'result': 'refused', 'code': 'no_target'}
    wanted = {'halt': ({'canary', 'rolling'}, 'halted', 'operator'), 'resume': ({'halted', 'canary'}, 'rolling', None)}
    if action in wanted:
        allowed_from, state, reason = wanted[action]
        if target.state not in allowed_from:
            return 3, {'stage': 'rollout', 'result': 'refused', 'code': 'state', 'state': target.state}
        changed = store.set_state(target, state, reason)
        if changed.state != state:
            return 3, {'stage': 'rollout', 'result': 'refused', 'code': 'state_changed', 'state': changed.state}
        target = changed
    return 0, {'stage': 'rollout', 'action': action, 'targetId': str(target.target_id), 'state': target.state,
               'reason': target.reason, 'reasonOwner': None if target.reason_owner is None else str(target.reason_owner),
               'fingerprint': target.fingerprint, 'owners': store.counts(target.target_id)}


def serve():
    import ssl
    from app.settings import secret
    service, config, sa = build()
    wake = Wake()
    threading.Thread(target=wake.listen, args=(secret('OBSERVER_DATABASE_URL').replace('postgresql+psycopg://', 'postgresql://'),),
                     daemon=True).start()
    token = lambda: (sa/'token').read_text().strip()
    class Bearer(httpx.Auth):
        def auth_flow(self, request):
            request.headers['Authorization'] = 'Bearer '+token()
            yield request
    watcher = httpx.Client(base_url=config['api'], auth=Bearer(), verify=ssl.create_default_context(cafile=str(sa/'ca.crt')),
                           trust_env=False, follow_redirects=False)
    threading.Thread(target=wake.watch, args=(watcher, config['namespace']), daemon=True).start()
    while True:
        wake.event.wait(RESCAN_SECONDS)
        wake.event.clear()
        for owner, outcome, reason in service.reconcile():
            print(json.dumps(log_line(owner, outcome, reason)), flush=True)


def log_line(owner, outcome, reason):
    line = {'owner': None if owner is None else str(owner), 'stage': 'controller', 'result': outcome}
    if reason is not None:
        line['reason'] = reason
    return line


def main(argv=None, *, factory=build):
    """No arguments: the resident controller. `reclaim --owner U --pod-uid U [--runner-available]`: the
    operator's R2 command, run with
    `kubectl -n runner-control exec deploy/runner-controller -- python -B -m ops.runner_controller_service ...`.
    `--runner-available` (#114) selects the version 5 path for an available runner that refuses its drain.
    rc 0 accepted, 3 refused (code and observed values printed), 1 any other failure (fixed text)."""
    import argparse
    parser = argparse.ArgumentParser(prog='ops.runner_controller_service')
    commands = parser.add_subparsers(dest='command')
    reclaim = commands.add_parser('reclaim')
    reclaim.add_argument('--owner', type=UUID, required=True)
    reclaim.add_argument('--pod-uid', type=UUID, required=True)
    reclaim.add_argument('--runner-available', action='store_true')
    rollout = commands.add_parser('rollout')  # #101: rc 0 done, 3 refused, 1 any other failure.
    rollout.add_argument('action', choices=('status', 'halt', 'resume'))
    args = parser.parse_args(argv)
    if args.command is None:
        return serve()
    if args.command == 'rollout':
        try:
            rc, report = rollout_command(factory()[0], args.action)
        except Exception:
            print(json.dumps({'stage': 'rollout', 'result': 'failed', 'error': 'unexpected error; status is read-only and safe to repeat'}), flush=True)
            return 1
        print(json.dumps(report, sort_keys=True, default=str), flush=True)
        return rc
    try:
        result = factory()[0].reclaim(args.owner, args.pod_uid, runner_available=args.runner_available)
    except Refusal as refusal:
        print(json.dumps({'stage': 'reclaim', 'result': 'refused', 'code': refusal.code, 'observed': refusal.observed},
                         sort_keys=True, default=str), flush=True)
        return 3
    except Exception:
        print(json.dumps({'stage': 'reclaim', 'result': 'failed', 'error': 'unexpected error; running reclaim again is safe and shows where it stops'}), flush=True)
        return 1
    print(json.dumps({'stage': 'reclaim', **result}, sort_keys=True), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
