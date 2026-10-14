"""Exact reviewed runtime schema/grants, separate from existing auth/research."""
import json
from pathlib import Path
from sqlalchemy import inspect, text
from .research_schema_contract import table_contracts, canonical

TABLES = {'schema_version', 'registry', 'entry_receipts', 'admissions', 'outbox'}
UPDATES = {'registry': {'phase', 'generation', 'revision', 'pod_uid', 'active_requests', 'ambiguous', 'last_activity'},
           'admissions': {'completed_at'}, 'outbox': {'settled'}}


def validate(connection):
    if set(inspect(connection).get_table_names(schema='runner_runtime')) != TABLES:
        raise RuntimeError('Unknown/incomplete runner runtime schema')
    expected = json.loads((Path(__file__).resolve().parent.parent / 'db' / '010_runner_runtime_contract.json').read_text())
    actual = table_contracts(connection, TABLES, schema='runner_runtime')
    if {name: canonical(contract) for name, contract in actual.items()} != {name: canonical(contract) for name, contract in expected.items()}:
        raise RuntimeError('Runner runtime structure mismatch')
    if connection.execute(text('SELECT singleton,version FROM runner_runtime.schema_version')).all() != [(True, 1)]:
        raise RuntimeError('Runner runtime version mismatch')
    if not connection.execute(text("SELECT has_schema_privilege('anxious_api','runner_runtime','USAGE')")).scalar_one() or connection.execute(text("SELECT has_schema_privilege('anxious_api','runner_runtime','CREATE')")).scalar_one():
        raise RuntimeError('Runner runtime schema privilege mismatch')
    for name, contract in actual.items():
        for privilege in ('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER'):
            required = privilege == 'SELECT' or (privilege == 'INSERT' and name != 'schema_version')
            granted = connection.execute(text("SELECT has_table_privilege('anxious_api',:table,:privilege)"),
                                         {'table': 'runner_runtime.' + name, 'privilege': privilege}).scalar_one()
            if granted != required:
                raise RuntimeError('Runner runtime table privilege mismatch')
        for column in contract['columns']:
            granted = connection.execute(text("SELECT has_column_privilege('anxious_api',:table,:column,'UPDATE')"),
                                         {'table': 'runner_runtime.' + name, 'column': column}).scalar_one()
            if granted != (column in UPDATES.get(name, set())):
                raise RuntimeError('Runner runtime immutable column privilege mismatch')
    if connection.execute(text('''SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace,
        LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) acl
        WHERE n.nspname='runner_runtime' AND c.relkind='r' AND acl.grantee=0''')).scalar_one():
        raise RuntimeError('Runner runtime must not have public table grants')
    if connection.execute(text('''SELECT count(*) FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace, LATERAL aclexplode(a.attacl) acl
        WHERE n.nspname='runner_runtime' AND acl.grantee=0''')).scalar_one():
        raise RuntimeError('Runner runtime must not have public column grants')
    if connection.execute(text('''SELECT count(*) FROM pg_namespace n,
        LATERAL aclexplode(n.nspacl) acl WHERE n.nspname='runner_runtime' AND acl.grantee=0''')).scalar_one():
        raise RuntimeError('Runner runtime must not have public schema grants')
    if connection.execute(text("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='runner_runtime' AND c.relkind NOT IN ('r','i')")).scalar_one() or connection.execute(text("SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='runner_runtime'")).scalar_one():
        raise RuntimeError('Runner runtime has unknown executable/view/sequence objects')
    if connection.execute(text("SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='runner_runtime' AND NOT t.tgisinternal")).scalar_one():
        raise RuntimeError('Runner runtime has unknown triggers')
    if connection.execute(text("SELECT count(*) FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace WHERE n.nspname='runner_runtime' AND (NOT c.convalidated OR c.condeferrable)")).scalar_one():
        raise RuntimeError('Runner runtime constraints must be immediate/valid')
    if connection.execute(text("SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='runner_runtime' AND (NOT i.indisvalid OR NOT i.indisready OR NOT i.indimmediate)")).scalar_one():
        raise RuntimeError('Runner runtime indexes must be immediate/valid')
