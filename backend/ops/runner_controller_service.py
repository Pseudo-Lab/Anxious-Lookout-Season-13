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
from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import select
import threading
import time
from uuid import UUID

from sqlalchemy import text

from app.conversations import Runner
from app.runner_controller import Controller
from app.runner_limits import load_limits
from app.runner_registry import CHANNEL, Registry
from app.runner_termination_control import (accept_drain, accept_terminal, drain_body, no_pending_work,
                                            release, request_idle_stop, request_stop, stop_for)
from app.runner_termination_schema import validate as validate_termination
from ops.runner_one_shot import request
from ops.runner_provision_worker import allowed
from runner.lifecycle import Fence, Phase, require

TERMINAL_PHASES = {'Failed', 'Succeeded'}
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


class Service:
    def __init__(self, controller_engine, observer_engine, controller_pool, observer_pool, node,
                 *, runner=Runner, now=clock):
        self.controller_engine, self.observer_engine = controller_engine, observer_engine
        self.controller_pool, self.observer_pool = controller_pool, observer_pool
        self.node, self.runner, self.now = node, runner, now

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
        with self.controller_pool.bound(owner) as runtime:
            runtime.namespace()
            with self.controller_engine.begin() as db:
                state = Registry(db)._current(owner)
            return Controller(self.controller_engine, runtime.pods, self.now,
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
        """Record an idle stop when the live runner confirms quiescence."""
        if state.phase not in {Phase.READY, Phase.UNREACHABLE} or state.active_requests or (
                state.ambiguous and state.phase != Phase.UNREACHABLE):
            return False
        if (self.now() - state.last_activity).total_seconds() < idle_seconds:
            return False
        evidence = self.runner(self.address(runtime, obj), runtime.token, owner, str(state.state_id)).call(
            'POST', '/lifecycle/idle', {'idleSeconds': idle_seconds})
        require(evidence == {'ownerId': str(owner), 'stateId': str(state.state_id), 'podUid': str(state.pod_uid), 'idle': True})
        with self.observer_engine.begin() as db:
            request_idle_stop(db, owner, idle_seconds=idle_seconds, native_idle=True)
        return True

    def close_dead_work(self, db, state):
        """A dead runner cannot finish its turns: fail them visibly and close their admissions."""
        db.execute(text('''UPDATE research.conversations SET state='failed', error_code='codex_runner_lost',
            version=version+1, updated_at=now(), tool_token_hash=NULL
            WHERE owner_id=:owner AND state='running' '''), {'owner': state.owner})
        db.execute(text('''UPDATE runner_runtime.admissions SET completed_at=greatest(now(), admitted_at)
            WHERE owner_id=:owner AND state_id=:state AND generation=:generation AND completed_at IS NULL'''),
            {'owner': state.owner, 'state': state.state_id, 'generation': state.generation})

    def step(self, owner, idle_seconds):
        """Advance one owner's stop by at most one destructive action. Returns a fixed outcome."""
        with self.observer_pool.bound(owner) as runtime:
            runtime.namespace()
            obj = self.pod(runtime)
            with self.observer_engine.begin() as db:
                state = Registry(db)._current(owner)
                stop = stop_for(db, state)
            if state.pod_uid is None:
                return 'no_pod'
            if obj is not None:
                require(obj['metadata']['uid'] == str(state.pod_uid))  # Never act on a replaced object.
            dead = terminal_evidence(obj)
            if stop is None and dead is None and (obj.get('status') or {}).get('phase') != 'Running':
                return 'starting'  # Pending: no address to ask yet, and nothing to stop.
            if stop is None:
                if dead is not None:
                    with self.observer_engine.begin() as db:
                        request_stop(db, owner)
                elif not self.idle(runtime, owner, state, obj, idle_seconds):
                    return 'running'
                with self.observer_engine.begin() as db:
                    stop = stop_for(db, Registry(db)._current(owner))
            if obj is not None and obj['metadata'].get('deletionTimestamp'):
                return 'awaiting_pod_deletion'  # Kubelet is stopping it; the watch wakes us.
            if obj is not None:
                if stop['receipt'] is None:
                    if dead is not None:
                        with self.observer_engine.begin() as db:
                            state = Registry(db)._current(owner)
                            self.close_dead_work(db, state)
                            accept_terminal(db, owner, stop['stop_id'], dead)
                    else:
                        receipt = self.runner(self.address(runtime, obj), runtime.token, owner, str(state.state_id)).call(
                            'POST', '/lifecycle/drain', drain_body(stop))
                        with self.observer_engine.begin() as db:
                            accept_drain(db, owner, stop['stop_id'], receipt)
                with self.observer_engine.begin() as db:
                    self.anchor(db, runtime, stop)
                # Receipt and dispatch fence are committed; ordinary graceful deletion of that UID only.
                runtime.pods._request('DELETE', 'pods', runtime.plan.identity.name,
                    body={'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {'uid': str(state.pod_uid)}})
                return 'deleting'
            # The Pod with this UID is gone: kubelet has finished it, or it vanished before any drain.
            with self.observer_engine.begin() as db:
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

    # Loop -----------------------------------------------------------------
    def reconcile(self):
        outcomes = []
        idle_seconds = load_limits().idle_seconds
        for owner in self.candidates(idle_seconds):
            try:
                outcomes.append((owner, self.step(owner, idle_seconds)))
            except Exception:
                outcomes.append((owner, 'reconciliation_required'))
        for owner in self.waiting():
            if not allowed(owner):
                continue
            try:
                self.ensure(owner)
                outcomes.append((owner, 'ensured'))
            except Exception:
                outcomes.append((owner, 'reconciliation_required'))
        return outcomes


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


def main():
    from sqlalchemy import create_engine
    import httpx
    import ssl
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
    wake = Wake()
    threading.Thread(target=wake.listen, args=(secret('OBSERVER_DATABASE_URL').replace('postgresql+psycopg://', 'postgresql://'),),
                     daemon=True).start()
    token = lambda: (sa/'token').read_text().strip()
    class Bearer(httpx.Auth):
        def auth_flow(self, request):
            request.headers['Authorization'] = 'Bearer '+token()
            yield request
    watcher = httpx.Client(base_url=api, auth=Bearer(), verify=ssl.create_default_context(cafile=str(sa/'ca.crt')),
                           trust_env=False, follow_redirects=False)
    threading.Thread(target=wake.watch, args=(watcher, config['namespace']), daemon=True).start()
    while True:
        wake.event.wait(RESCAN_SECONDS)
        wake.event.clear()
        for owner, outcome in service.reconcile():
            print(json.dumps({'owner': str(owner), 'stage': 'controller', 'result': outcome}), flush=True)


if __name__ == '__main__':
    main()
