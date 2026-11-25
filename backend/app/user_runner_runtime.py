"""Single reviewed operator binding. No discovery, provider refresh or auto-retry.

The private preparation output binds one owner/state to retained volumes. A stopped
instance keeps its reservation and evidence; #11 owns capacity release/re-entry.
"""
from datetime import datetime, timezone
import ipaddress
import os
from pathlib import Path
from uuid import UUID

from sqlalchemy import text

from .codex_policy import MODEL, REASONS
from .runner_controller import Controller, Identity
from .runner_kube import KubePods, Plan, Reference
from .runner_registry import Registry
from .runner_limits import load_limits, RunnerLimits
from .runner_schema import validate
from runner.lifecycle import Fence, Phase, require
from runner.auth import read_private


def clock():
    return datetime.now(timezone.utc)


def plan_from(value):
    # #68: a stored v2 plan's `executor` is read (runner_bindings.decode keeps its strict shape)
    # but ignored. Every Pod is the native shell runner; the shell-executor sidecar is never rendered.
    return Plan(Identity(UUID(value['owner']), UUID(value['state']), value['generation']), value['image'],
                Reference(value['native']['name'], UUID(value['native']['uid']), value['native']['volume']),
                Reference(value['control']['name'], UUID(value['control']['uid']), value['control']['volume']),
                Reference(value['transport']['name'], UUID(value['transport']['uid'])),
                value['callback'], value['serviceAccount'])


RESEARCH_FLAG = 'RUNNER_RESEARCH_API_ENABLE'


def without_unsupported_grant(method, path, body, obj):
    """#101 decision D2: a turn's research grant goes only to a runner Pod rendered with the research flag.

    A Pod created before the flag was turned on refuses any grant with policy_refused, failing the whole
    turn. `obj` is the live Pod this very call is bound to (its UID was just checked), so the turn is sent
    without the grant instead; the caller revokes the issued credential. Returns (body, omitted)."""
    if method != 'POST' or not path.endswith('/turn') or type(body) is not dict or 'researchGrant' not in body:
        return body, False
    containers = [c for c in obj['spec']['containers'] if c.get('name') == 'runner']
    require(len(containers) == 1)
    if any(item.get('name') == RESEARCH_FLAG and item.get('value') == 'true' for item in containers[0].get('env') or []):
        return body, False
    return {key: value for key, value in body.items() if key != 'researchGrant'}, True


class Runtime:
    def __init__(self, engine, pods, plan, transport_token, namespace_uid, pod_cidr, max_pods, *, idle_seconds=1800):
        self.limits = RunnerLimits(max_pods, idle_seconds)
        require(len(transport_token) >= 32 and not any(c.isspace() for c in transport_token))
        self.engine, self.pods, self.plan = engine, pods, plan
        self.token, self.namespace_uid = transport_token, namespace_uid
        self.network = ipaddress.ip_network(pod_cidr)
        self.controller = Controller(engine, pods, clock, max_pods=max_pods)

    def namespace(self):
        # Cluster-scoped namespace metadata, verified through the same TLS client.
        from ops.runner_one_shot import request
        self.pods.refresh_bearer()
        obj = request(self.pods.client, 'GET', '/api/v1/namespaces/'+self.pods.namespace)
        require(obj.get('kind') == 'Namespace' and obj['metadata']['uid'] == str(self.namespace_uid)
                and not obj['metadata'].get('deletionTimestamp'))

    def state(self, db, owner):
        require(owner == self.plan.identity.owner)
        validate(db)
        state = Registry(db)._current(owner)
        require(Identity.of(state) == self.plan.identity)
        return state

    def ensure(self, owner):
        self.namespace()
        with self.engine.begin() as db:
            state = self.state(db, owner)
        if state.pod_uid is None and state.phase in {Phase.PREPARING, Phase.WAITING}:
            self.controller.ensure(Fence.of(state))

    def runner(self, owner):
        require(owner == self.plan.identity.owner)
        return BoundRunner(self, owner)

    def stop(self, owner):
        """Durable dispatch fence first, then UID-preconditioned ordinary deletion.

        No volume/Secret deletion, force delete, slot release or recreation. Failed
        DELETE is retryable against the SAME persisted UID, including after logout.
        """
        from .runner_termination_schema import installed
        from .runner_termination_control import request_stop
        with self.engine.begin() as db:
            if installed(db):
                state = self.state(db,owner)
                if state.phase in {Phase.FAILED,Phase.STOPPED}:
                    return
                request_stop(db,owner)
                return
        self.namespace()
        with self.engine.begin() as db:
            db.execute(text("SET LOCAL lock_timeout='3s'"))
            db.execute(text("SET LOCAL statement_timeout='5s'"))
            state = self.state(db, owner)
            obj = self.pods._request('GET', 'pods', self.plan.identity.name, missing=True)
            if obj is not None:
                # Terminating object is a previously sent deletion, not reusable.
                if obj['metadata'].get('deletionTimestamp'):
                    require(state.phase == Phase.FAILED and state.pod_uid is not None
                            and obj['metadata']['uid'] == str(state.pod_uid))
                    return
                pod = self.pods._pod(obj, self.plan)
                require(state.pod_uid is None or pod.uid == state.pod_uid)
                uid = pod.uid
            else:
                uid = state.pod_uid
            db.execute(text("""UPDATE runner_runtime.registry SET phase='runner_failed',
                pod_uid=:uid,revision=revision+1 WHERE owner_id=:owner"""), {'uid': uid, 'owner': owner})
        if obj is not None:
            self.pods._request('DELETE', 'pods', self.plan.identity.name,
                body={'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {'uid': str(uid)}})

    def close(self):
        self.pods.close()


class BoundRunner:
    def __init__(self, runtime, owner):
        self.runtime, self.owner = runtime, owner

    def call(self, method, path, body=None):
        runtime = self.runtime
        runtime.namespace()
        # Serialize dispatch with stop; never use a stale cached Pod address.
        from .research_api_budget import checkout
        checkout()
        with runtime.engine.begin() as db:
            Registry(db)._eligible(self.owner)
            state = runtime.state(db, self.owner)
            require(state.pod_uid is not None and state.phase in {Phase.READY, Phase.UNREACHABLE})
            if method != 'GET':
                from .runner_termination_control import stop_for
                require(stop_for(db,state) is None)
            obj = runtime.pods._request('GET', 'pods', runtime.plan.identity.name)
            pod = runtime.pods._pod(obj, runtime.plan)
            require(pod.uid == state.pod_uid and obj.get('status', {}).get('phase') == 'Running')
            address = ipaddress.ip_address(obj['status']['podIP'])
            require(address in runtime.network and not address.is_loopback and not address.is_link_local)
            host = '['+str(address)+']' if address.version == 6 else str(address)
            body, omitted = without_unsupported_grant(method, path, body, obj)
            from .conversations import Runner
            result = Runner('http://'+host+':8080', runtime.token, self.owner, str(state.state_id)).call(method, path, body)
            require(result.get('podUid') == str(state.pod_uid))
            if omitted:
                result = {**result, 'researchGrantOmitted': True}
            if path.endswith('/research-authorize'):
                # This generation is trusted transport evidence from the locked
                # registry, bound to the live authenticated Pod UID, not input
                # from the sandbox/HTTP caller or an inferred Pod env default.
                result = {**result, 'generation': state.generation}
            return result

    def interrupt(self, identity, request_id):
        # #90 Stop: an ordinary POST call, so the same check -> dispatch transaction as every runner write.
        return self.call('POST', '/sessions/'+str(identity)+'/interrupt', {'requestId': str(request_id)})

    def research_authorize(self, identity, request_id):
        return self.call('POST', '/sessions/'+str(identity)+'/research-authorize', {'requestId': str(request_id)})

    def research_binding(self, db, result):
        from .research_api import current_binding
        state = current_binding(db, self.owner, result)
        require(Identity.of(state) == self.runtime.plan.identity)

    def stream_target(self, identity):
        """#90: the same Pod checks as a GET call, read inside one registry transaction. Only the address is
        returned: the API's event stream opens after the transaction (a GET never needed the stop fence), and the
        runner's hello re-proves owner, state and Pod UID on that connection. call() is unchanged from main."""
        runtime = self.runtime
        runtime.namespace()
        with runtime.engine.begin() as db:
            Registry(db)._eligible(self.owner)
            state = runtime.state(db, self.owner)
            require(state.pod_uid is not None and state.phase in {Phase.READY, Phase.UNREACHABLE})
            obj = runtime.pods._request('GET', 'pods', runtime.plan.identity.name)
            pod = runtime.pods._pod(obj, runtime.plan)
            require(pod.uid == state.pod_uid and obj.get('status', {}).get('phase') == 'Running')
            address = ipaddress.ip_address(obj['status']['podIP'])
            require(address in runtime.network and not address.is_loopback and not address.is_link_local)
            host = '['+str(address)+']' if address.version == 6 else str(address)
            from .conversations import Runner
            target = Runner('http://'+host+':8080', runtime.token, self.owner, str(state.state_id)).stream_target(identity)
            return {**target, 'podUid': str(state.pod_uid)}

    def health(self):
        value = self.call('GET', '/health')
        require(value.get('model') == MODEL and type(value.get('available')) is bool
                and value.get('verification') in {'unverified', 'real'}
                and value.get('reason') in REASONS | {None})
        return value

    def submit(self, identity, body):
        return self.call('POST', '/sessions/'+str(identity)+'/turn', body)

    def read(self, identity):
        return self.call('GET', '/sessions/'+str(identity))


def load(engine, owner):
    filename = os.getenv('CODEX_USER_RUNNER_CONFIG_FILE', '')
    pool_filename = os.getenv('CODEX_USER_RUNNER_POOL_CONFIG_FILE', '')
    require(not (filename and pool_filename))
    if pool_filename:
        from .runner_pool import load as load_pool
        return load_pool(engine, pool_filename)
    if not filename:
        return None
    value = read_private(Path(filename))
    require(value['version'] == 1)
    plan = plan_from(value['plan'])
    require(plan.identity.owner == owner)
    limits = load_limits()
    # Kubelet's projected files intentionally use atomic symlinks and fsGroup.
    # This fixed mount is separate from both the private binding and runner Pod.
    def bearer_provider():
        return Path('/run/runner-kube/token').read_text().strip()
    bearer = bearer_provider()
    token = read_private(Path(value['transportTokenFile']), json_format=False).strip()
    pods = KubePods(value['api'], value['namespace'], bearer, value['caFile'], [plan])
    pods.bearer_provider = bearer_provider
    return Runtime(engine, pods, plan, token, UUID(value['namespaceUid']), value['podCIDR'],
                   limits.max_pods, idle_seconds=limits.idle_seconds)
