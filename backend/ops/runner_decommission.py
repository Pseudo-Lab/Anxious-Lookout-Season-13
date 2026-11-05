"""Remove one stopped owner's runner state from the database (#66). Admin-only, report then apply.

Runner state only: auth.accounts, sessions, conversations and posts stay. The owner must
be idle_stopped with a completed termination proof for its current generation, and no
Pod, open stop, unconsumed entry, pending admission or unsettled outbox may remain.

report writes an export of identities, counts and row digests (no secrets, no session
hashes); apply recomputes it under the owner lock and deletes only if it is identical.
Kubernetes objects and host files are removed by the node wrapper from the same export.
A refusal prints a code and the observed non-secret values, never exception text.
"""
from datetime import datetime
import hashlib
import json
from pathlib import Path
from uuid import UUID
from sqlalchemy import text
from app.runner_bindings import validate, decode
from app.runner_termination_schema import validate as validate_termination
from ops.runner_binding_image import plan_digest
from runner.lifecycle import require

STORAGE = '/var/lib/anxious-lookout/user-runners'
OUTPUTS = '/var/lib/anxious-lookout/issue16-d4/provision-output'
# Child tables first (foreign keys), registry last. (table, owner column filter, order)
TABLES = [('runner_bindings.resources', 'kind,name'),
          ('runner_bindings.bindings', 'owner_id'),
          ('runner_runtime.entry_receipts', 'key'),
          ('runner_runtime.admissions', 'request_id'),
          ('runner_runtime.outbox', 'generation,revision,kind'),
          ('runner_termination.entries', 'key'),
          ('runner_termination.stops', 'generation'),
          ('runner_runtime.registry', 'owner_id')]
# Tables that reference registry(state_id) and could hold another owner's row for this state.
BY_STATE = ['runner_bindings.bindings', 'runner_termination.entries', 'runner_termination.stops']


class Refusal(Exception):
    def __init__(self, code, **observed):
        super().__init__(code)
        self.code, self.observed = code, observed


def check(condition, code, **observed):
    if not condition:
        raise Refusal(code, **observed)


def plain(value):
    if isinstance(value, UUID): return str(value)
    if isinstance(value, datetime): return value.isoformat()
    if isinstance(value, dict): return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [plain(v) for v in value]
    return value


def digest(rows):
    """Row content digest; session hashes and receipts contribute only through it."""
    return hashlib.sha256(json.dumps([plain(dict(row)) for row in rows], sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def rows(db, table, order, owner):
    return db.execute(text('SELECT * FROM '+table+' WHERE owner_id=:owner ORDER BY '+order),
                      {'owner': owner}).mappings().all()


def counts(db, owner, state):
    result = {table: len(rows(db, table, order, owner)) for table, order in TABLES}
    for table in BY_STATE:
        result[table+'#state'] = db.execute(text('SELECT count(*) FROM '+table+' WHERE state_id=:state'),
                                            {'state': state}).scalar_one()
    return result


def export(db, fence):
    """Read-only checks and the export; apply runs the same function under locks."""
    owner, state_id = fence['owner'], fence['state']
    validate(db); validate_termination(db)
    account = db.execute(text('SELECT id FROM auth.accounts WHERE id=:owner FOR SHARE'), {'owner': owner}).first()
    check(account is not None, 'account_missing')
    state = db.execute(text('SELECT * FROM runner_runtime.registry WHERE owner_id=:owner FOR UPDATE'),
                       {'owner': owner}).mappings().first()
    check(state is not None, 'registry_missing')
    observed = {'state': str(state['state_id']), 'phase': state['phase'], 'generation': state['generation'],
                'revision': state['revision'], 'podUid': plain(state['pod_uid']),
                'activeRequests': state['active_requests'], 'ambiguous': state['ambiguous']}
    check((state['state_id'], state['generation'], state['revision']) == (state_id, fence['generation'], fence['revision']),
          'fence_changed', **observed)
    check(state['phase'] == 'idle_stopped' and state['pod_uid'] is None and state['active_requests'] == 0
          and not state['ambiguous'], 'not_idle_stopped', **observed)
    pending = {
        'openStops': 'SELECT count(*) FROM runner_termination.stops WHERE owner_id=:owner AND completed_at IS NULL',
        'unconsumedEntries': 'SELECT count(*) FROM runner_termination.entries WHERE owner_id=:owner AND consumed_at IS NULL',
        'pendingAdmissions': 'SELECT count(*) FROM runner_runtime.admissions WHERE owner_id=:owner AND completed_at IS NULL',
        'unsettledOutbox': 'SELECT count(*) FROM runner_runtime.outbox WHERE owner_id=:owner AND NOT settled',
        'runningConversations': "SELECT count(*) FROM research.conversations WHERE owner_id=:owner AND state='running'"}
    found = {key: db.execute(text(query), {'owner': owner}).scalar_one() for key, query in pending.items()}
    check(not any(found.values()), 'pending_work', **found)
    stop = db.execute(text('''SELECT * FROM runner_termination.stops WHERE owner_id=:owner AND state_id=:state
        AND generation=:generation'''), {'owner': owner, 'state': state_id, 'generation': fence['generation']}).mappings().first()
    check(stop is not None and stop['completed_at'] is not None and type(stop['proof']) is dict
          and stop['proof'].get('podUid') == str(stop['pod_uid']), 'termination_unproven',
          stop=stop is not None, completed=stop is not None and stop['completed_at'] is not None,
          proofPodUid=None if stop is None or type(stop['proof']) is not dict else stop['proof'].get('podUid'),
          podUid=None if stop is None else str(stop['pod_uid']))
    binding = db.execute(text('SELECT * FROM runner_bindings.bindings WHERE owner_id=:owner'),
                         {'owner': owner}).mappings().first()
    check(binding is not None and binding['state_id'] == state_id, 'binding_missing',
          bindingState=None if binding is None else str(binding['state_id']))
    plan = decode(binding['plan'])
    require(plan.identity.owner == owner and plan.identity.state_id == state_id)
    resources = sorted((r['kind'], r['name'], str(r['uid'])) for r in
                       rows(db, 'runner_bindings.resources', 'kind,name', owner))
    expected = {('pvc', plan.native.name, str(plan.native.uid)), ('pvc', plan.control.name, str(plan.control.uid)),
                ('secret', plan.transport.name, str(plan.transport.uid))}
    pvs = {(kind, name) for kind, name, _ in resources if kind == 'pv'}
    check(expected <= set(resources) and pvs == {('pv', plan.native.volume), ('pv', plan.control.volume)}
          and len(resources) == 5, 'resources_mismatch', resources=[list(r) for r in resources])
    by_owner = counts(db, owner, state_id)
    check(all(by_owner[table+'#state'] == by_owner[table] for table in BY_STATE), 'foreign_state_rows',
          **{key: value for key, value in by_owner.items()})
    return {'version': 1, 'owner': str(owner), 'state': str(state_id), 'generation': state['generation'],
            'revision': state['revision'], 'phase': state['phase'],
            'namespace': binding['namespace'], 'namespaceUid': str(binding['namespace_uid']),
            'planSha256': plan_digest(binding['plan']), 'runnerImage': plan.image,
            'executorImage': None if plan.executor is None else plan.executor.image,
            'podName': plan.identity.name,
            'resources': [{'kind': kind, 'name': name, 'uid': uid} for kind, name, uid in resources],
            'lastStop': {'stopId': str(stop['stop_id']), 'generation': stop['generation'], 'podUid': str(stop['pod_uid']),
                         'completedAt': stop['completed_at'].isoformat(), 'proofNodeName': stop['proof'].get('nodeName'),
                         'proofBootId': stop['proof'].get('bootId')},
            'rows': {table: {'count': by_owner[table], 'sha256': digest(rows(db, table, order, owner))}
                     for table, order in TABLES},
            'host': {'storage': STORAGE+'/'+state_id.hex, 'output': OUTPUTS+'/'+owner.hex}}


def absent(db, owner, state):
    """True only if no runner row of this owner or state remains (a completed apply)."""
    return not any(counts(db, owner, state).values())


def apply(db, fence, approved):
    if absent(db, fence['owner'], fence['state']):
        check(approved['owner'] == str(fence['owner']) and approved['state'] == str(fence['state']), 'export_mismatch')
        return 'already_applied'
    current = export(db, fence)
    changed = sorted(key for key in set(current) | set(approved) if current.get(key) != approved.get(key))
    check(not changed, 'export_changed', fields=changed,
          rows={t: current['rows'][t]['count'] for t, _ in TABLES} if 'rows' in changed else None)
    for table, _ in TABLES:
        result = db.execute(text('DELETE FROM '+table+' WHERE owner_id=:owner'), {'owner': fence['owner']})
        require(result.rowcount == approved['rows'][table]['count'])
    require(absent(db, fence['owner'], fence['state']))
    return 'deleted'


def fence_of(config):
    require(set(config) == {'owner', 'state', 'generation', 'revision'}
            and type(config['generation']) is int and type(config['revision']) is int)
    return {'owner': UUID(config['owner']), 'state': UUID(config['state']),
            'generation': config['generation'], 'revision': config['revision']}


def main():
    import argparse
    import os
    from sqlalchemy import create_engine
    from app.settings import secret
    from runner.auth import read_private, write_private
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('report', 'apply'))
    parser.add_argument('--inputs', required=True, help='Private {owner,state,generation,revision}')
    parser.add_argument('--export', required=True, help='report writes it; apply requires the reviewed one')
    args = parser.parse_args()
    engine, stage = None, 'decommission-'+args.action
    try:
        require(os.geteuid() == 0)
        fence = fence_of(read_private(Path(args.inputs)))
        path = Path(args.export)
        if args.action == 'report':
            require(not path.exists() and not path.is_symlink())
        else:
            approved = read_private(path)
            require(approved['version'] == 1 and approved['owner'] == str(fence['owner']))
        engine = create_engine(secret('ADMIN_DATABASE_URL'), hide_parameters=True)
        if args.action == 'report':
            with engine.connect() as db:
                try:
                    value = export(db, fence)
                finally:
                    db.rollback()  # Release the owner lock; report never writes.
            write_private(path, value)
            result = 'reported'
        else:
            with engine.begin() as db:
                result = apply(db, fence, approved)
        print(json.dumps({'owner': str(fence['owner']), 'stage': stage, 'result': result}))
    except Refusal as refusal:
        print(json.dumps({'stage': stage, 'result': 'refused', 'code': refusal.code, 'observed': refusal.observed},
                         default=str))
        raise SystemExit(3)
    except Exception:
        parser.exit(1, 'Decommission refused; nothing deleted by this attempt, preserve the export and evidence\n')
    finally:
        if engine is not None: engine.dispose()


if __name__ == '__main__':
    main()
