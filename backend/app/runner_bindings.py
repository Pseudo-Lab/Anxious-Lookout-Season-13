"""Immutable owner/state plans; publication is controller-only, API is read-only."""
from dataclasses import replace
import json
from pathlib import Path
from uuid import UUID

from sqlalchemy import inspect, text

from .runner_controller import Identity
from .runner_registry import Registry
from .user_runner_runtime import plan_from
from runner.lifecycle import require


FIELDS = {'owner', 'state', 'generation', 'image', 'native', 'control', 'transport', 'callback', 'serviceAccount'}


def decode(value):
    require(type(value) is dict and set(value) == FIELDS)
    for kind in ('native', 'control', 'transport'):
        require(type(value[kind]) is dict and set(value[kind]) == (
            {'name', 'uid'} if kind == 'transport' else {'name', 'uid', 'volume'}))
    require(value['generation'] == 1 and type(value['generation']) is int)
    return plan_from(value)


def validate(db):
    """Reject DDL/privilege drift before reads or publication."""
    from .research_schema_contract import table_contracts, canonical
    tables = {'bindings', 'resources'}
    require(set(inspect(db).get_table_names(schema='runner_bindings')) == tables)
    expected = json.loads((Path(__file__).resolve().parent.parent/'db/016_runner_bindings_contract.json').read_text())
    actual = table_contracts(db, tables, schema='runner_bindings')
    require({k: canonical(v) for k, v in actual.items()} == {k: canonical(v) for k, v in expected.items()})
    require(db.execute(text("SELECT has_schema_privilege('anxious_api','runner_bindings','USAGE')")).scalar_one())
    require(not db.execute(text("SELECT has_schema_privilege('anxious_api','runner_bindings','CREATE')")).scalar_one())
    for table in tables:
        for privilege in ('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER'):
            granted = db.execute(text("SELECT has_table_privilege('anxious_api',:table,:privilege)"),
                                 {'table': 'runner_bindings.'+table, 'privilege': privilege}).scalar_one()
            require(granted == (privilege == 'SELECT'))
        for column in actual[table]['columns']:
            for privilege in ('INSERT', 'UPDATE', 'REFERENCES'):
                require(not db.execute(text("SELECT has_column_privilege('anxious_api',:table,:column,:privilege)"),
                                       {'table': 'runner_bindings.'+table, 'column': column,
                                        'privilege': privilege}).scalar_one())
    require(not db.execute(text("""SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='runner_bindings' AND (c.relkind NOT IN ('r','i') OR c.relrowsecurity OR c.relforcerowsecurity)""")).first())
    require(not db.execute(text("""SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='runner_bindings' AND NOT t.tgisinternal""")).first())
    require(not db.execute(text("""SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='runner_bindings'""")).first())
    require(not db.execute(text("""SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace,
        LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a
        WHERE n.nspname='runner_bindings' AND c.relkind='r' AND a.grantee=0""")).first())
    require(not db.execute(text("""SELECT 1 FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace, LATERAL aclexplode(a.attacl) acl
        WHERE n.nspname='runner_bindings' AND acl.grantee=0""")).first())
    require(not db.execute(text("""SELECT 1 FROM pg_namespace n, LATERAL aclexplode(n.nspacl) acl
        WHERE n.nspname='runner_bindings' AND acl.grantee=0""")).first())
    require(not db.execute(text("""SELECT 1 FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace
        WHERE n.nspname='runner_bindings' AND (c.condeferrable OR NOT c.convalidated OR
        (c.contype='f' AND (c.confupdtype <> 'a' OR c.confdeltype <> 'a' OR c.confmatchtype <> 's')))""")).first())


class Bindings:
    def __init__(self, db):
        self.db = db

    def resolve(self, state, namespace, namespace_uid):
        row = self.db.execute(text('SELECT * FROM runner_bindings.bindings WHERE owner_id=:owner'),
                              {'owner': state.owner}).mappings().one_or_none()
        if row is None:
            return None
        require(row['state_id'] == state.state_id and row['namespace'] == namespace
                and row['namespace_uid'] == namespace_uid)
        plan = decode(row['plan'])
        require(plan.identity.owner == state.owner and plan.identity.state_id == state.state_id)
        return replace(plan, identity=Identity.of(state))

    def publish(self, value, namespace, namespace_uid, pv_uids):
        """Caller independently validates live refs/storage before publication.

        First publication pins all references globally; repeat accepts only exact
        identity. API credentials cannot call this. No registry or files changed.
        """
        from .runner_kube import name
        plan = decode(value)
        name(namespace)
        require(isinstance(namespace_uid, UUID) and set(pv_uids) == {'native', 'control'}
                and all(isinstance(uid, UUID) for uid in pv_uids.values()))
        registry = Registry(self.db)
        state = registry._current(plan.identity.owner)
        require(state.state_id == plan.identity.state_id)
        refs = [('pvc', ref.name, ref.uid) for ref in (plan.native, plan.control)]
        refs += [('pv', plan.native.volume, pv_uids['native']), ('pv', plan.control.volume, pv_uids['control']),
                 ('secret', plan.transport.name, plan.transport.uid)]
        current = self.resolve(state, namespace, namespace_uid)
        if current is not None:
            require(replace(current, identity=plan.identity) == plan)
            existing = self.db.execute(text('SELECT kind,name,uid FROM runner_bindings.resources WHERE owner_id=:owner'),
                                       {'owner': state.owner}).all()
            require(set(existing) == set(refs))
            return False
        self.db.execute(text('''INSERT INTO runner_bindings.bindings(owner_id,state_id,namespace,namespace_uid,plan)
            VALUES(:owner,:state,:namespace,:uid,CAST(:plan AS jsonb))'''),
            {'owner': state.owner, 'state': state.state_id, 'namespace': namespace,
             'uid': namespace_uid, 'plan': json.dumps(value)})
        for kind, resource, uid in refs:
            self.db.execute(text('INSERT INTO runner_bindings.resources VALUES(:owner,:kind,:name,:uid)'),
                            {'owner': state.owner, 'kind': kind, 'name': resource, 'uid': uid})
        return True
