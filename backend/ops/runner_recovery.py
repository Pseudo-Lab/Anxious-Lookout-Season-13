"""Read-only private runner reservation inventory. No release/reset/override action."""
import argparse
import json
from uuid import UUID

from sqlalchemy import create_engine, text

from app.runner_registry import Registry
from app.runner_schema import validate
from app.settings import secret
from runner.lifecycle import Phase, require


def inventory(engine, *, owner=None, after=None, limit=100):
    require(owner is None or isinstance(owner, UUID))
    require(after is None or isinstance(after, UUID))
    require(type(limit) is int and 1 <= limit <= 100)
    require(owner is None or after is None)
    with engine.begin() as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        validate(db)
        owners = db.execute(text('''SELECT owner_id FROM runner_runtime.registry
            WHERE (CAST(:owner AS uuid) IS NULL OR owner_id=CAST(:owner AS uuid))
            AND (CAST(:after AS uuid) IS NULL OR owner_id>CAST(:after AS uuid))
            ORDER BY owner_id LIMIT :limit'''), {'owner': owner, 'after': after, 'limit': limit}).scalars().all()
        records = []
        for identity in owners:
            registry = Registry(db)
            state = registry.read(identity)
            issued = db.execute(text('''SELECT count(*) FROM runner_runtime.outbox
                WHERE owner_id=:owner AND state_id=:state AND generation=:generation
                AND kind='ensure' AND settled'''),
                {'owner': state.owner, 'state': state.state_id, 'generation': state.generation}).scalar_one()
            pending = registry._pending(identity)
            held = state.phase != Phase.STOPPED and (state.pod_uid is not None or issued > 0)
            if state.phase == Phase.STOPPED:
                action = 'already_stopped'
            elif state.pod_uid is not None:
                action = 'native_evidence_required'
            elif issued:
                action = 'lookup_only'
            else:
                action = 'no_create_permission'
            records.append({'ownerId': str(state.owner), 'stateId': str(state.state_id),
                'generation': state.generation, 'revision': state.revision, 'phase': state.phase.value,
                'podUid': str(state.pod_uid) if state.pod_uid else None,
                'lastActivity': state.last_activity.isoformat(), 'activeRequests': state.active_requests,
                'ambiguous': state.ambiguous, 'uncompletedAdmissions': pending,
                'issuedEnsureReceipts': issued, 'capacityHeld': held, 'nextEvidence': action})
        return {'version': 1, 'releaseToolAvailable': False, 'records': records,
                'nextAfter': str(owners[-1]) if len(owners) == limit else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner', type=UUID)
    parser.add_argument('--after', type=UUID)
    parser.add_argument('--limit', type=int, default=100)
    args = parser.parse_args()
    engine = None
    try:
        url = secret('DATABASE_URL')
        require(bool(url))
        engine = create_engine(url, hide_parameters=True, connect_args={'connect_timeout': 3})
        result = inventory(engine, owner=args.owner, after=args.after, limit=args.limit)
    except Exception:
        parser.exit(1, 'Runner inventory refused; private connection and database details are not printed\n')
    finally:
        if engine is not None:
            engine.dispose()
    print(json.dumps(result, separators=(',', ':')))


if __name__ == '__main__':
    main()
