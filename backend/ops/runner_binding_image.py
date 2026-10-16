"""Explicit admin-only image CAS for a proved-stopped immutable binding.

All API/controllers/workers must be stopped by the operator first. This changes
only plan.image, or explicitly opts v1 into v2 executor configuration; no
runtime/resource/history reset, adoption or live Pod patch.
"""
import hashlib
import json
from pathlib import Path
from uuid import UUID
from sqlalchemy import text
from app.runner_bindings import validate, decode
from app.runner_registry import Registry
from app.runner_termination_control import no_pending_work
from app.runner_termination_schema import validate as validate_termination
from runner.lifecycle import Fence, Phase, require


def plan_digest(plan):
    return hashlib.sha256(json.dumps(plan,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def upgrade(db, fence, expected_plan_digest, new_image, *, apply=False, executor=None):
    validate(db); validate_termination(db)
    registry = Registry(db)
    registry._eligible(fence.owner)
    state = registry._current(fence.owner)
    require(Fence.of(state) == fence and state.phase == Phase.STOPPED and state.pod_uid is None
            and not state.ambiguous and state.active_requests == 0)
    no_pending_work(db,state)
    # Old settled ensure receipts are history; no new creator may remain queued.
    require(not db.execute(text('SELECT 1 FROM runner_runtime.outbox WHERE owner_id=:owner AND NOT settled'),
                           {'owner':state.owner}).first())
    require(not db.execute(text('SELECT 1 FROM runner_termination.stops WHERE owner_id=:owner AND completed_at IS NULL'),
                           {'owner':state.owner}).first())
    require(not db.execute(text('SELECT 1 FROM runner_termination.entries WHERE owner_id=:owner AND consumed_at IS NULL'),
                           {'owner':state.owner}).first())
    proof = db.execute(text('''SELECT proof FROM runner_termination.stops WHERE owner_id=:owner
        AND state_id=:state AND generation=:generation AND completed_at IS NOT NULL'''),
        {'owner':state.owner,'state':state.state_id,'generation':state.generation}).scalar_one_or_none()
    require(type(proof) is dict and bool(proof))
    row = db.execute(text('SELECT * FROM runner_bindings.bindings WHERE owner_id=:owner FOR UPDATE'),
                     {'owner':state.owner}).mappings().one()
    old = row['plan']
    require(row['state_id'] == state.state_id and plan_digest(old) == expected_plan_digest)
    old_plan = decode(old)
    require(old_plan.identity.owner == state.owner and old_plan.identity.state_id == state.state_id)
    updated = {**old,'image':new_image}
    if executor is not None:
        # Explicit opt-in only. Retain every durable identity/reference and the
        # original thread files; never downgrade or silently replace a v2 plan.
        require('version' not in old and 'executor' not in old)
        updated.update(version=2, executor=executor)
    decode(updated)  # Same strict immutable digest/image and identity contract.
    require(updated != old)
    report = {'owner':str(state.owner),'state':str(state.state_id),'generation':state.generation,
              'revision':state.revision,'oldImage':old['image'],'newImage':new_image,
              'oldPlanSha256':plan_digest(old),'newPlanSha256':plan_digest(updated)}
    if executor is not None:
        report.update(newPlanVersion=2, executor=executor)
    if apply:
        result = db.execute(text('''UPDATE runner_bindings.bindings SET plan=CAST(:new AS jsonb)
            WHERE owner_id=:owner AND state_id=:state AND plan=CAST(:old AS jsonb)'''),
            {'owner':state.owner,'state':state.state_id,'new':json.dumps(updated),'old':json.dumps(old)})
        require(result.rowcount == 1)
    return report


def main():
    import argparse
    import os
    from sqlalchemy import create_engine
    from app.settings import secret
    from runner.auth import read_private, write_private
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('report','apply'))
    parser.add_argument('--inputs',required=True)
    parser.add_argument('--report',required=True)
    args = parser.parse_args()
    engine = None
    try:
        require(os.geteuid() == 0)
        config = read_private(Path(args.inputs))
        fields = {'owner','state','generation','revision','oldPlanSha256','newImage'}
        require(set(config) in (fields, fields | {'executor'}))
        if 'executor' in config:
            require(type(config['executor']) is dict)
        fence = Fence(UUID(config['owner']),UUID(config['state']),config['generation'],config['revision'],None)
        report = Path(args.report)
        approved = read_private(report) if args.action == 'apply' else None
        if args.action == 'report': require(not report.exists() and not report.is_symlink())
        engine = create_engine(secret('ADMIN_DATABASE_URL'),hide_parameters=True)
        with engine.begin() as db:
            result = upgrade(db,fence,config['oldPlanSha256'],config['newImage'],executor=config.get('executor'))
            if args.action == 'apply':
                require(result == approved)
                upgrade(db,fence,config['oldPlanSha256'],config['newImage'],apply=True,executor=config.get('executor'))
            else: write_private(report,result)
        print(json.dumps({'owner':str(fence.owner),'stage':'binding-image-'+args.action,'result':'verified'}))
    except Exception:
        parser.exit(1,'Binding image change refused; preserve plan, runtime and reviewed evidence\n')
    finally:
        if engine is not None: engine.dispose()


if __name__ == '__main__':
    main()
