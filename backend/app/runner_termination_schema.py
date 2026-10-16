"""Exact additive lifecycle control schema and API capability validation."""
import json
from pathlib import Path
from sqlalchemy import inspect, text
from .research_schema_contract import table_contracts, canonical
from runner.lifecycle import require

TABLES = {'stops', 'entries'}
INSERTS = {'stops': {'owner_id','state_id','generation','pod_uid','stop_id','requested_revision'},
           'entries': {'owner_id','key','state_id','generation','session_hash'}}


def installed(db):
    return db.execute(text("SELECT to_regnamespace('runner_termination') IS NOT NULL")).scalar_one()


def validate(db):
    if hasattr(db, 'get_bind'):
        db = db.connection()
    require(set(inspect(db).get_table_names(schema='runner_termination')) == TABLES)
    actual = table_contracts(db, TABLES, schema='runner_termination')
    expected = json.loads((Path(__file__).resolve().parent.parent/'db/016_runner_termination_contract.json').read_text())
    require({k:canonical(v) for k,v in actual.items()} == {k:canonical(v) for k,v in expected.items()})
    require(db.execute(text("SELECT has_schema_privilege('anxious_api','runner_termination','USAGE')")).scalar_one())
    require(not db.execute(text("SELECT has_schema_privilege('anxious_api','runner_termination','CREATE')")).scalar_one())
    for table in TABLES:
        for privilege in ('SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'):
            require(db.execute(text("SELECT has_table_privilege('anxious_api',:table,:privilege)"),
                {'table':'runner_termination.'+table,'privilege':privilege}).scalar_one() == (privilege == 'SELECT'))
        for column in actual[table]['columns']:
            for privilege in ('INSERT','UPDATE','REFERENCES'):
                require(db.execute(text("SELECT has_column_privilege('anxious_api',:table,:column,:privilege)"),
                    {'table':'runner_termination.'+table,'column':column,'privilege':privilege}).scalar_one()
                        == (privilege == 'INSERT' and column in INSERTS[table]))
    require(not db.execute(text("""SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='runner_termination' AND (c.relkind NOT IN ('r','i') OR c.relrowsecurity OR c.relforcerowsecurity)""")).first())
    require(not db.execute(text("""SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='runner_termination' AND NOT t.tgisinternal""")).first())
    require(not db.execute(text("SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='runner_termination'")).first())
    require(not db.execute(text("""SELECT 1 FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace
        WHERE n.nspname='runner_termination' AND (c.condeferrable OR NOT c.convalidated)""")).first())
    foreign = db.execute(text("""SELECT c.conname,c.confupdtype::text,c.confdeltype::text,c.confmatchtype::text,
        c.conenforced,c.conperiod,c.confdelsetcols FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace
        WHERE n.nspname='runner_termination' AND c.contype='f'""")).all()
    expected_names = {'stops_owner_id_fkey','stops_state_id_fkey','entries_owner_id_fkey',
                      'entries_state_id_fkey','entries_session_hash_fkey'}
    require(len(foreign) == len(expected_names) and {row[0] for row in foreign} == expected_names)
    for row in foreign:
        require(tuple(row[1:]) == ('a','c' if row[0] == 'entries_session_hash_fkey' else 'a','s',True,False,None))
    require(not db.execute(text("""SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='runner_termination'
        AND (NOT i.indisvalid OR NOT i.indisready OR NOT i.indimmediate)""")).first())
    require(not db.execute(text("""SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace,
        LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a
        WHERE n.nspname='runner_termination' AND c.relkind='r' AND a.grantee=0""")).first())
    require(not db.execute(text("""SELECT 1 FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace, LATERAL aclexplode(a.attacl) acl
        WHERE n.nspname='runner_termination' AND acl.grantee=0""")).first())
    require(not db.execute(text("SELECT 1 FROM pg_namespace n,LATERAL aclexplode(n.nspacl) acl WHERE n.nspname='runner_termination' AND acl.grantee=0")).first())
