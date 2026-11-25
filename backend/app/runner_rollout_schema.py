"""#101 rollout schema and the binding-image write path: exact reviewed DDL, grants and function.

Decision D3: a binding plan changes only in `image`, and only through runner_rollout.move_image, which
the NOLOGIN anxious_runner_binding_writer role owns. This validator refuses any other UPDATE path on
runner_bindings.bindings, a changed function body or a grant to the API or PUBLIC.
"""
import json
from pathlib import Path
import re

from sqlalchemy import inspect, text

from .research_schema_contract import table_contracts, canonical
from runner.lifecycle import require

SCHEMA = 'runner_rollout'
TABLES = {'targets', 'owners'}
WRITER = 'anxious_runner_binding_writer'
FUNCTION = 'runner_rollout.move_image(uuid,uuid,jsonb,text)'
DDL = Path(__file__).resolve().parent.parent/'db/101_runner_rollout.sql'
CONTRACT = Path(__file__).resolve().parent.parent/'db/101_runner_rollout_contract.json'
FOREIGN_KEYS = {'owners_target_id_fkey', 'owners_owner_id_fkey'}


def installed(db):
    return db.execute(text("SELECT to_regnamespace('runner_rollout') IS NOT NULL")).scalar_one()


def function_body():
    """The reviewed plpgsql body, exactly as written in the DDL file."""
    match = re.search(r'CREATE FUNCTION runner_rollout\.move_image\(.*?\$\$(.*?)\$\$;', DDL.read_text(), re.S)
    require(match is not None)
    return match.group(1)


def validate(db):
    if hasattr(db, 'get_bind'):
        db = db.connection()
    require(set(inspect(db).get_table_names(schema=SCHEMA)) == TABLES)
    actual = table_contracts(db, TABLES, schema=SCHEMA)
    expected = json.loads(CONTRACT.read_text())
    require({k: canonical(v) for k, v in actual.items()} == {k: canonical(v) for k, v in expected.items()})
    scalar = lambda sql, **params: db.execute(text(sql), params).scalar_one()
    # The API has no part in rollouts.
    require(not scalar("SELECT has_schema_privilege('anxious_api','runner_rollout','USAGE')"))
    require(not scalar("SELECT has_schema_privilege('anxious_api','runner_rollout','CREATE')"))
    for table in TABLES:
        for privilege in ('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER'):
            require(not scalar("SELECT has_table_privilege('anxious_api',:t,:p)", t=SCHEMA+'.'+table, p=privilege))
    require(not scalar("SELECT has_function_privilege('anxious_api',:f,'EXECUTE')", f=FUNCTION))
    # No PUBLIC grant on the schema, its tables, columns or function.
    for sql in ("""SELECT count(*) FROM pg_namespace n, LATERAL aclexplode(n.nspacl) a
                   WHERE n.nspname='runner_rollout' AND a.grantee=0""",
                """SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace,
                   LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a
                   WHERE n.nspname='runner_rollout' AND c.relkind='r' AND a.grantee=0""",
                """SELECT count(*) FROM pg_attribute t JOIN pg_class c ON c.oid=t.attrelid
                   JOIN pg_namespace n ON n.oid=c.relnamespace, LATERAL aclexplode(t.attacl) a
                   WHERE n.nspname='runner_rollout' AND a.grantee=0""",
                """SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace,
                   LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
                   WHERE n.nspname='runner_rollout' AND a.grantee=0"""):
        require(scalar(sql) == 0)
    require(scalar("""SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='runner_rollout' AND (c.relkind NOT IN ('r','i') OR c.relrowsecurity OR c.relforcerowsecurity)""") == 0)
    require(scalar("""SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='runner_rollout' AND NOT t.tgisinternal""") == 0)
    require(scalar("""SELECT count(*) FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace
        WHERE n.nspname='runner_rollout' AND (c.condeferrable OR NOT c.convalidated)""") == 0)
    foreign = db.execute(text("""SELECT c.conname,c.confupdtype::text,c.confdeltype::text,c.confmatchtype::text
        FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace
        WHERE n.nspname='runner_rollout' AND c.contype='f'""")).all()
    require({row[0] for row in foreign} == FOREIGN_KEYS and len(foreign) == len(FOREIGN_KEYS)
            and all(tuple(row[1:]) == ('a', 'a', 's') for row in foreign))
    require(scalar("""SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='runner_rollout'
        AND (NOT i.indisvalid OR NOT i.indisready OR NOT i.indimmediate)""") == 0)
    validate_writer(db)


def validate_writer(db):
    """move_image is the reviewed body, SECURITY DEFINER with a fixed search_path, owned by the
    NOLOGIN writer, and nobody else (table owner aside) can UPDATE a binding plan."""
    functions = db.execute(text("""SELECT p.oid::regprocedure::text AS signature, p.prosecdef, p.proconfig,
            p.prosrc, r.rolname AS owner, p.prokind, l.lanname
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_roles r ON r.oid=p.proowner
        JOIN pg_language l ON l.oid=p.prolang WHERE n.nspname='runner_rollout'""")).mappings().all()
    require(len(functions) == 1)
    move, = functions
    require(move['signature'] == 'runner_rollout.move_image(uuid,uuid,jsonb,text)' and move['prokind'] == 'f'
            and move['lanname'] == 'plpgsql' and move['prosecdef'] is True and move['owner'] == WRITER
            and list(move['proconfig'] or []) == ['search_path=pg_catalog, pg_temp']
            and move['prosrc'] == function_body())
    writer = db.execute(text("""SELECT rolcanlogin, rolsuper, rolinherit, rolcreaterole, rolcreatedb, rolbypassrls
        FROM pg_roles WHERE rolname=:role"""), {'role': WRITER}).one()
    require(tuple(writer) == (False, False, True, False, False, False))
    # The writer is no member of another role, and no role is a member of it.
    require(db.execute(text("""SELECT count(*) FROM pg_auth_members m JOIN pg_roles r ON r.oid IN (m.roleid, m.member)
        WHERE r.rolname=:role"""), {'role': WRITER}).scalar_one() == 0)
    bindings = 'runner_bindings.bindings'
    for privilege in ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER'):
        require(not db.execute(text("SELECT has_table_privilege(:role,:t,:p)"),
                               {'role': WRITER, 't': bindings, 'p': privilege}).scalar_one())
    for column in ('owner_id', 'state_id', 'namespace', 'namespace_uid', 'plan', 'published_at'):
        granted = db.execute(text("SELECT has_column_privilege(:role,:t,:c,'UPDATE')"),
                             {'role': WRITER, 't': bindings, 'c': column}).scalar_one()
        require(granted == (column == 'plan'))
    # Every UPDATE grant on the bindings table or its columns, other than the table owner's own.
    updaters = db.execute(text("""SELECT DISTINCT r.rolname FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace,
        LATERAL (SELECT a.grantee, a.privilege_type FROM aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a
                 UNION ALL SELECT a.grantee, a.privilege_type FROM pg_attribute t, aclexplode(t.attacl) a
                 WHERE t.attrelid=c.oid) g
        JOIN pg_roles r ON r.oid=g.grantee
        WHERE n.nspname='runner_bindings' AND c.relname='bindings' AND g.privilege_type='UPDATE'
        AND g.grantee<>c.relowner""")).scalars().all()
    require(set(updaters) == {WRITER})
