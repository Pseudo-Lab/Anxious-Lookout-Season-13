"""Finite new-owner storage preparation. Root host storage identity only.

No provider auth. Registry intents are created by authenticated HTTP; this worker allocates
storage and publishes the verified binding, then notifies the in-cluster runner controller,
which creates the Pod (#61). Partial preparation is never silently recreated/reset. CSI
provisioning replaces this worker in #63.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
from uuid import UUID

from sqlalchemy import bindparam, create_engine, text

from app.runner_bindings import Bindings, validate
from app.runner_registry import Registry, notify
from app.runner_kube import KubePods
from app.settings import secret
from app.user_runners import feature_enabled, selected_account, selected_allowlist
from ops.user_runner_prepare import prepare
from ops.runner_binding_import import import_binding, retained_file
from ops.runner_storage import allocate
from runner.auth import read_private
from runner.lifecycle import Phase, require


def allowed(owner):
    if not feature_enabled():
        return False
    singleton, allowlist = selected_account(), selected_allowlist()
    return owner in allowlist if allowlist is not None else singleton is None or owner == singleton


class Worker:
    def __init__(self, engine, inputs, output_root, *, preparer=prepare, allocator=allocate, port_factory=KubePods):
        require(os.geteuid() == 0)
        self.engine, self.inputs, self.output_root = engine, inputs, Path(output_root)
        require(self.output_root.is_absolute() and self.output_root == self.output_root.resolve()
                and self.output_root.is_dir() and self.output_root.stat().st_uid == 0
                and not self.output_root.stat().st_mode & 0o077)
        self.preparer, self.allocator, self.port_factory = preparer, allocator, port_factory

    def pending(self, *, limit=10):
        require(type(limit) is int and 1 <= limit <= 100)
        if not feature_enabled():
            return []
        singleton, allowlist = selected_account(), selected_allowlist()
        owners = allowlist if allowlist is not None else (None if singleton is None else (singleton,))
        query = '''SELECT r.owner_id FROM runner_runtime.registry r
                JOIN auth.accounts a ON a.id=r.owner_id
                WHERE r.phase IN ('provisioning','capacity_wait') AND r.pod_uid IS NULL
                AND a.is_approved AND a.role IN ('editor','admin')
                AND NOT EXISTS (SELECT 1 FROM runner_bindings.bindings b WHERE b.owner_id=r.owner_id)'''
        params = {'limit': limit}
        if owners is not None:
            query += ' AND r.owner_id IN :owners'
            params['owners'] = tuple(owners)
        statement = text(query + ' ORDER BY r.owner_id LIMIT :limit')
        if owners is not None:
            statement = statement.bindparams(bindparam('owners', expanding=True))
        with self.engine.begin() as db:
            validate(db)
            return db.execute(statement, params).scalars().all()

    def process(self, owner):
        require(isinstance(owner, UUID) and allowed(owner))
        lock_path = self.output_root/('.lock-'+owner.hex)
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'r+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                return self._process(owner)
            except Exception:
                output = self.output_root/owner.hex
                if output.exists() and not (output/'runtime.json').exists():
                    with self.engine.begin() as db:
                        registry = Registry(db)
                        state = registry._current(owner)
                        if state.pod_uid is None and state.phase == Phase.PREPARING:
                            db.execute(text("""UPDATE runner_runtime.registry SET phase='runner_failed',
                                revision=revision+1 WHERE owner_id=:owner"""), {'owner':owner})
                raise

    def _process(self, owner):
        require(allowed(owner))
        # Read eligibility/state first. The helper rechecks and holds membership
        # and owner locks across external preparation; the private output-directory
        # permit prevents a second worker from repeating uncertain CREATEs.
        with self.engine.begin() as db:
            registry = Registry(db)
            registry._eligible(owner)
            state = registry._current(owner)
            require(state.phase in {Phase.PREPARING, Phase.WAITING} and state.pod_uid is None)
            existing = Bindings(db).resolve(state, self.inputs['namespace'], UUID(self.inputs['namespaceUid']))
        output = self.output_root/owner.hex
        if existing is None:
            # Exclusive per-owner filesystem permit in prepare prevents competing
            # workers, including after an interrupted external CREATE. Existing
            # partial output is not replayed; only complete bindings are audited.
            if not output.exists():
                self.preparer({**self.inputs, 'owner':str(owner)}, output, from_intent=True,
                              storage_allocator=self.allocator)
            config = json.loads(retained_file(output/'runtime.json'))
            config['transportTokenFile'] = str(output/'transport-token')
            bearer = read_private(Path(self.inputs['kubeTokenFile']), json_format=False).strip()
            pods = self.port_factory(self.inputs['api'], self.inputs['namespace'], bearer, self.inputs['caFile'], [])
            try:
                import_binding(self.engine, pods, config, self.inputs['hostRoot'],
                               self.inputs['providerAccountId'], bounded=True)
            finally:
                pods.close()
        # The in-cluster controller creates the Pod from the published binding only (#61).
        with self.engine.begin() as db:
            state = Registry(db)._current(owner)
            require(Bindings(db).resolve(state, self.inputs['namespace'], UUID(self.inputs['namespaceUid'])) is not None)
            notify(db, owner)
        return 'published'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', required=True)
    parser.add_argument('--outputs', required=True)
    parser.add_argument('--limit', type=int, default=10)
    args = parser.parse_args()
    engine = create_engine(secret('CONTROLLER_DATABASE_URL'), hide_parameters=True)
    try:
        # prepare uses DATABASE_URL, never the web process's public configuration.
        require(secret('DATABASE_URL') == secret('CONTROLLER_DATABASE_URL'))
        worker = Worker(engine, read_private(Path(args.inputs)), args.outputs)
        for owner in worker.pending(limit=args.limit):
            if not allowed(owner): continue
            try:
                worker.process(owner)
                print(json.dumps({'owner':str(owner),'stage':'provision','result':'processed'}))
            except Exception:
                # Fixed diagnostic only. Keep partial files and lifecycle evidence.
                print(json.dumps({'owner':str(owner),'stage':'provision','result':'reconciliation_required'}))
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
