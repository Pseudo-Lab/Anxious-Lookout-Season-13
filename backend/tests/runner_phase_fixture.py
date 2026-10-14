"""Disposable HTTP integration phase control. Test image only; never operations."""
import argparse
import json
from datetime import datetime, timezone
import os
from uuid import UUID, uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.runner_registry import Registry
from app.runner_schema import validate
from runner.lifecycle import require

PHASES = {'preparing': 'provisioning', 'waiting': 'capacity_wait', 'unreachable': 'runner_unreachable',
          'ready': 'ready', 'stopped': 'idle_stopped', 'failed': 'runner_failed',
          'not_prepared': 'not_prepared', 'stopping': 'stopping'}
PHASES.update({value: value for value in tuple(PHASES.values())})


def fixture_engine():
    require(os.getenv('APP_ENV') == 'test' and os.getenv('OAUTH_MODE') == 'mock'
            and os.getenv('RUNNER_HTTP_FIXTURE') == 'true')
    raw = os.environ['ADMIN_DATABASE_URL']
    url = make_url(raw)
    # libpq query parameters override URL authority fields. Reject all options
    # before constructing an engine, including alternate drivers/ports.
    require(not url.query and url.drivername == 'postgresql+psycopg'
            and url.port in {None, 5432})
    require(url.host == 'db' and url.database in {'hosting_test', 'runner_runtime_test'}
            and url.username == 'postgres' and url.password in {'disposable-test-admin', 'disposable-runner-admin'})
    return create_engine(url, hide_parameters=True)


def set_phase(engine, owner, phase, *, creation_reserved=False):
    require(isinstance(owner, UUID) and phase in PHASES)
    phase = PHASES[phase]
    require(not creation_reserved or phase == 'provisioning')
    with engine.begin() as db:
        require(db.execute(text('SELECT current_database()')).scalar_one() in {'hosting_test', 'runner_runtime_test'})
        validate(db)
        registry = Registry(db)
        registry._eligible(owner)
        registry._lock(owner)
        state = registry.read(owner)
        if phase == 'not_prepared':
            require(state is None)  # Never delete an existing registry or its history.
            return
        require(state is None or (state.active_requests == 0 and registry._pending(owner) == 0))
        # Never reset an actual/uncertain create permission, even in this fixture.
        require(not db.execute(text("SELECT 1 FROM runner_runtime.outbox WHERE owner_id=:owner AND settled LIMIT 1"),
                               {'owner': owner}).first())
        if state is None:
            state, _ = registry.enter(owner, uuid4(), datetime.now(timezone.utc))
        needs_pod = phase in {'ready', 'runner_unreachable', 'stopping'}
        pod = (state.pod_uid or uuid4()) if needs_pod else None
        db.execute(text('''UPDATE runner_runtime.registry SET phase=:phase,pod_uid=:pod,
            active_requests=0,ambiguous=:ambiguous,revision=revision+1 WHERE owner_id=:owner'''),
            {'phase': phase, 'pod': pod, 'ambiguous': phase == 'runner_unreachable', 'owner': owner})
        if creation_reserved:
            result = db.execute(text('''UPDATE runner_runtime.outbox SET settled=true
                WHERE owner_id=:owner AND state_id=:state AND generation=:generation
                AND kind='ensure' AND NOT settled'''),
                {'owner': owner, 'state': state.state_id, 'generation': state.generation})
            require(result.rowcount > 0)
        # Synthetic phase only. Preserve state UUID, activity, conversations,
        # entry receipts and outbox. No Pod/Native/controller/provider is invoked.


def inspect(engine, owner):
    require(isinstance(owner, UUID))
    with engine.begin() as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        validate(db)
        registry = Registry(db)
        state = registry.read(owner)
        entries = db.execute(text('SELECT count(*) FROM runner_runtime.entry_receipts WHERE owner_id=:owner'),
                             {'owner': owner}).scalar_one()
        pending = db.execute(text("SELECT count(*) FROM runner_runtime.outbox WHERE owner_id=:owner AND kind='ensure' AND NOT settled"),
                             {'owner': owner}).scalar_one()
        return {'phase': state.phase.value if state else 'not_prepared',
                'generation': state.generation if state else None,
                'podBound': bool(state and state.pod_uid),
                'creationReserved': registry.creation_reserved(state),
                'entryReceipts': entries, 'pendingEnsure': pending,
                'lastActivity': state.last_activity.isoformat() if state else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner', '--account', type=UUID, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--phase', choices=sorted(PHASES))
    mode.add_argument('--inspect', action='store_true')
    parser.add_argument('--creation-reserved', action='store_true')
    args = parser.parse_args()
    engine = None
    try:
        engine = fixture_engine()
        require(not args.inspect or not args.creation_reserved)
        if args.inspect:
            result = inspect(engine, args.owner)
        else:
            set_phase(engine, args.owner, args.phase, creation_reserved=args.creation_reserved)
            result = {'syntheticPhaseSet': True, 'actualExecution': False}
    except Exception:
        parser.exit(1, 'Disposable runner fixture refused; no private values printed\n')
    finally:
        if engine is not None:
            engine.dispose()
    print(json.dumps(result, separators=(',', ':')))


if __name__ == '__main__':
    main()
