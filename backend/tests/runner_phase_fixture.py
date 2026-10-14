"""Disposable HTTP integration phase control. Test image only; never operations."""
import argparse
from datetime import datetime, timezone
import os
from uuid import UUID, uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.runner_registry import Registry
from app.runner_schema import validate
from runner.lifecycle import require

PHASES = {'preparing': 'provisioning', 'waiting': 'capacity_wait', 'unreachable': 'runner_unreachable',
          'ready': 'ready', 'stopped': 'idle_stopped', 'failed': 'runner_failed'}


def fixture_engine():
    require(os.getenv('APP_ENV') == 'test' and os.getenv('OAUTH_MODE') == 'mock'
            and os.getenv('RUNNER_HTTP_FIXTURE') == 'true')
    raw = os.environ['ADMIN_DATABASE_URL']
    url = make_url(raw)
    require(url.host == 'db' and url.database in {'hosting_test', 'runner_runtime_test'}
            and url.username == 'postgres' and url.password in {'disposable-test-admin', 'disposable-runner-admin'})
    return create_engine(raw, hide_parameters=True)


def set_phase(engine, owner, phase):
    require(isinstance(owner, UUID) and phase in PHASES)
    with engine.begin() as db:
        require(db.execute(text('SELECT current_database()')).scalar_one() in {'hosting_test', 'runner_runtime_test'})
        validate(db)
        registry = Registry(db)
        registry._eligible(owner)
        registry._lock(owner)
        state = registry.read(owner)
        require(state is None or (state.active_requests == 0 and registry._pending(owner) == 0))
        # Never reset an actual/uncertain create permission, even in this fixture.
        require(not db.execute(text("SELECT 1 FROM runner_runtime.outbox WHERE owner_id=:owner AND settled LIMIT 1"),
                               {'owner': owner}).first())
        if state is None:
            state, _ = registry.enter(owner, uuid4(), datetime.now(timezone.utc))
        needs_pod = phase in {'ready', 'unreachable'}
        pod = state.pod_uid or uuid4() if needs_pod else None
        db.execute(text('''UPDATE runner_runtime.registry SET phase=:phase,pod_uid=:pod,
            active_requests=0,ambiguous=:ambiguous,revision=revision+1 WHERE owner_id=:owner'''),
            {'phase': PHASES[phase], 'pod': pod, 'ambiguous': phase == 'unreachable', 'owner': owner})
        # Synthetic phase only. Preserve state UUID, activity, conversations,
        # entry receipts and outbox. No Pod/Native/controller/provider is invoked.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner', type=UUID, required=True)
    parser.add_argument('--phase', choices=sorted(PHASES), required=True)
    args = parser.parse_args()
    engine = None
    try:
        engine = fixture_engine()
        set_phase(engine, args.owner, args.phase)
    except Exception:
        parser.exit(1, 'Disposable runner fixture refused; no private values printed\n')
    finally:
        if engine is not None:
            engine.dispose()
    print('PASS synthetic runner phase set; no actual execution')


if __name__ == '__main__':
    main()
