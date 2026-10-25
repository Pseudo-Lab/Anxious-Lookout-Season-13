"""Read-only stop preflight, never a permit to drain, delete or release.

The host supply preflight that shared this module was removed with host supply (#60).
"""
from contextlib import contextmanager
import ipaddress
import json
from pathlib import Path
from uuid import UUID
from sqlalchemy import text
from app.runner_registry import Registry
from app.runner_termination_control import stop_for
from app.runner_termination_schema import validate
from ops.runner_one_shot import private_file
from app.runner_controller import Identity
from runner.lifecycle import Phase, require

CODES = frozenset('input_invalid registry_missing db_validate_failed state_ineligible active_requests pending_admissions '
                  'running_conversation binding_unavailable binding_mismatch namespace_unavailable pod_unavailable '
                  'pod_uid_mismatch pod_invalid pod_address_invalid no_stop stop_identity_mismatch stale_stop '
                  'node_unavailable node_mismatch receipt_unavailable receipt_mismatch ready_for_observation '
                  'retained_receipt_ready awaiting_pod_deletion'.split())
READY = {'no_stop', 'ready_for_observation', 'retained_receipt_ready', 'awaiting_pod_deletion'}


def diagnose_observer(observer, owner):
    """Read-only stop preflight. Drain and host death proof remain untested."""
    from datetime import datetime, timezone
    from app.runner_termination_control import drain_body
    stage = 'input_invalid'
    try:
        if not isinstance(owner, UUID): return stage
        stage = 'db_validate_failed'
        with ReadOnlyEngine(observer.engine).begin() as db:
            validate(db)
            state = Registry(db).read(owner)
            if state is None: return 'registry_missing'
            stop = stop_for(db,state)
            other = db.execute(text('SELECT 1 FROM runner_termination.stops WHERE owner_id=:owner AND completed_at IS NULL AND (state_id<>:state OR generation<>:generation)'),
                               {'owner':owner,'state':state.state_id,'generation':state.generation}).first()
            if other: return 'stale_stop'
            if stop is not None and stop['pod_uid'] != state.pod_uid: return 'stop_identity_mismatch'
            if state.active_requests: return 'active_requests'
            if Registry(db)._pending(owner): return 'pending_admissions'
            if db.execute(text("SELECT 1 FROM research.conversations WHERE owner_id=:owner AND state='running' LIMIT 1"),{'owner':owner}).first(): return 'running_conversation'
            if stop is None: return 'no_stop'
            if state.phase not in {Phase.READY,Phase.UNREACHABLE,Phase.STOPPING} or state.pod_uid is None: return 'state_ineligible'
            stage = 'binding_unavailable'
            with observer.pool.bound(owner) as runtime:
                if runtime.plan.identity != Identity.of(state): return 'binding_mismatch'
                stage = 'namespace_unavailable'
                runtime.namespace()
                stage = 'node_unavailable'
                node = observer.node(runtime)
                stage = 'node_mismatch'
                observer.host.node_matches(node,datetime.now(timezone.utc))
                if stop['node_uid'] is not None and (stop['node_name'],stop['node_uid'],stop['boot_id']) != (observer.host.node_name,observer.host.node_uid,observer.host.boot_id): return stage
                stage = 'pod_unavailable'
                obj = observer.pod(runtime)
                if stop['node_uid'] is None and (obj is None or obj['spec'].get('nodeName') != observer.host.node_name): return 'node_mismatch'
                expected = {**drain_body(stop),'version':1,'clean':True}
                if stop['receipt'] is not None and stop['receipt'] != expected: return 'receipt_mismatch'
                if obj is None:
                    stage = 'receipt_unavailable'
                    receipt = observer.retained_receipt(db,runtime,state)
                    return 'retained_receipt_ready' if receipt == expected else 'receipt_mismatch'
                if obj['metadata']['uid'] != str(state.pod_uid): return 'pod_uid_mismatch'
                if obj['metadata'].get('deletionTimestamp'):
                    return 'awaiting_pod_deletion' if stop['receipt'] is not None else 'receipt_unavailable'
                stage = 'pod_invalid'
                runtime.pods._pod(obj,runtime.plan)
                stage = 'pod_address_invalid'
                address = ipaddress.ip_address(obj['status']['podIP'])
                if address not in runtime.network or address.is_loopback or address.is_link_local: return stage
                return 'ready_for_observation'
    except Exception:
        return stage  # Never interpolate exception text, SQL, URLs or credentials.


class ReadOnlyEngine:
    def __init__(self, engine): self.engine = engine

    @contextmanager
    def begin(self):
        with self.engine.begin() as db:
            db.execute(text('SET TRANSACTION READ ONLY'))
            db.execute(text("SET LOCAL statement_timeout='10s'"))
            db.execute(text("SET LOCAL lock_timeout='3s'"))
            yield db


def diagnostic_command(args):
    from sqlalchemy import create_engine
    from app.settings import secret
    from app.runner_pool import Pool
    from ops.runner_stop_observer import Observer
    from ops.runner_termination import HostTermination
    import os
    engine, code = None, 'input_invalid'
    try:
        engine = create_engine(secret('OBSERVER_DATABASE_URL'),hide_parameters=True)
        config = json.loads(private_file(Path(args.inputs)))
        require(os.geteuid() == 0 and set(config) == {'pool','host','storageRoot'})
        observer = Observer(engine,Pool(ReadOnlyEngine(engine),config['pool']),HostTermination(**config['host']),config['storageRoot'])
        code = diagnose_observer(observer,args.owner)
    except Exception:
        pass
    finally:
        if engine is not None: engine.dispose()
    print(json.dumps({'owner':str(args.owner),'stage':'termination-diagnose','result':code}))
    return 0 if code in READY else 1


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', required=True, help='Private observer inputs')
    parser.add_argument('--owner', type=UUID, required=True)
    return diagnostic_command(parser.parse_args())


if __name__ == '__main__':
    raise SystemExit(main())
