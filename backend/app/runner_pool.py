"""Read-only dynamic plan resolution. Provisioning is a separate worker's job."""
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

from .runner_bindings import Bindings, validate
from .runner_kube import KubePods
from .runner_registry import Registry
from .runner_schema import validate as validate_runtime
from .user_runner_runtime import Runtime
from runner.auth import read_private
from runner.lifecycle import Phase, require


class Pool:
    def __init__(self, engine, config, *, port_factory=KubePods):
        require(set(config) == {'version', 'api', 'namespace', 'namespaceUid', 'podCIDR', 'caFile', 'kubeTokenFile'}
                and type(config['version']) is int and config['version'] == 2)
        self.engine, self.config, self.port_factory = engine, config, port_factory
        self.namespace_uid = UUID(config['namespaceUid'])
        require(str(self.namespace_uid) == config['namespaceUid'])
        with engine.begin() as db:
            validate_runtime(db)
            validate(db)

    @contextmanager
    def bound(self, owner):
        with self.engine.begin() as db:
            validate(db)
            state = Registry(db).read(owner)
            require(state is not None)
            plan = Bindings(db).resolve(state, self.config['namespace'], self.namespace_uid)
            require(plan is not None)
        # A fresh port per operation avoids shared-plan/cache mutation across users.
        # Projected SA token is intentionally read with kubelet symlink semantics.
        bearer = lambda: Path(self.config['kubeTokenFile']).read_text().strip()
        pods = self.port_factory(self.config['api'], self.config['namespace'], bearer(), self.config['caFile'], [plan])
        pods.bearer_provider = bearer
        try:
            token = pods._references(plan)
            runtime = Runtime(self.engine, pods, plan, token, self.namespace_uid, self.config['podCIDR'], 0)
            yield runtime
        finally:
            pods.close()

    def runner(self, owner):
        require(isinstance(owner, UUID))
        return PooledRunner(self, owner)

    def ensure(self, owner):
        # HTTP already committed registry/outbox intent. Never provision from GET
        # or grant API storage/PV/create privileges. Worker consumes it separately.
        require(isinstance(owner, UUID))

    def stop(self, owner):
        with self.engine.begin() as db:
            state = Registry(db).read(owner)
            if state is None or state.phase == Phase.STOPPED:
                return
        # Pending/unbound creation remains fail-closed until lifecycle observer
        # handles cancellation and uncertain creation permits.
        with self.bound(owner) as runtime:
            runtime.stop(owner)

    def close(self):
        pass  # Per-operation clients close at scope exit; engine belongs to app.


class PooledRunner:
    def __init__(self, pool, owner):
        self.pool, self.owner = pool, owner

    def health(self):
        with self.pool.bound(self.owner) as runtime:
            return runtime.runner(self.owner).health()

    def submit(self, identity, body):
        with self.pool.bound(self.owner) as runtime:
            return runtime.runner(self.owner).submit(identity, body)

    def read(self, identity):
        with self.pool.bound(self.owner) as runtime:
            return runtime.runner(self.owner).read(identity)


def load(engine, path):
    return Pool(engine, read_private(Path(path)))
