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
    return Plan(Identity(UUID(value['owner']), UUID(value['state']), value['generation']), value['image'],
                Reference(value['native']['name'], UUID(value['native']['uid']), value['native']['volume']),
                Reference(value['control']['name'], UUID(value['control']['uid']), value['control']['volume']),
                Reference(value['transport']['name'], UUID(value['transport']['uid'])),
                value['callback'], value['serviceAccount'])


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
            result = Runner('http://'+host+':8080', runtime.token, self.owner, str(state.state_id)).call(method, path, body)
            require(result.get('podUid') == str(state.pod_uid))
            return result

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
