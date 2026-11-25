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
# The contract JSON records an expression index only as columns [null]; compare the definitions.
INDEXES = {
    'owners_pkey': 'CREATE UNIQUE INDEX owners_pkey ON runner_rollout.owners USING btree (target_id, owner_id)',
    'targets_one_current': "CREATE UNIQUE INDEX targets_one_current ON runner_rollout.targets USING btree ((true)) WHERE (state <> 'superseded'::text)",
    'targets_pkey': 'CREATE UNIQUE INDEX targets_pkey ON runner_rollout.targets USING btree (target_id)',
}
# Application roles and the binding writes each may hold (effective: grants, ownership, membership).
BINDING_WRITES = {'anxious_api': set(), 'anxious_runner_observer': set(), 'anxious_runner_controller': {'INSERT'}}


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
    require(dict(db.execute(text("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname='runner_rollout'")).all())
            == INDEXES)
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
    # The table owner above is excluded from the grant list, so check the application roles' effective
    # rights directly: has_*_privilege counts ownership and inherited membership, so an ownership drift to
    # (or a membership in) the owner shows up here. A role that is not installed has no rights.
    installed_roles = set(db.execute(text('SELECT rolname FROM pg_roles WHERE rolname = ANY(:roles)'),
                                     {'roles': sorted(BINDING_WRITES)}).scalars())
    for role in sorted(installed_roles):
        for privilege in ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER'):
            granted = db.execute(text('SELECT has_table_privilege(:role,:t,:p)'),
                                 {'role': role, 't': bindings, 'p': privilege}).scalar_one()
            require(granted == (privilege in BINDING_WRITES[role]))
        for column in ('owner_id', 'state_id', 'namespace', 'namespace_uid', 'plan', 'published_at'):
            require(not db.execute(text("SELECT has_column_privilege(:role,:t,:c,'UPDATE')"),
                                   {'role': role, 't': bindings, 'c': column}).scalar_one())
    validate_owner(db)


def validate_owner(db):
    """#101 review (major, round 2): the bindings table belongs to an approved administrator (a superuser,
    as ops.runner_rollout_migrate requires), and no login or capability role reaches the owner, the writer,
    a superuser, or any UPDATE/DELETE/TRUNCATE right on bindings, through any membership: 'MEMBER' counts
    direct and indirect membership whether its privileges are inherited or need SET ROLE, and every role a
    principal can become is checked for its own effective rights."""
    owner_is_superuser = db.execute(text("""SELECT r.rolsuper FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_roles r ON r.oid=c.relowner WHERE n.nspname='runner_bindings' AND c.relname='bindings'""")).scalar_one()
    require(owner_is_superuser is True)
    reaching = db.execute(text("""
        WITH principals AS (
            SELECT oid FROM pg_roles WHERE (rolcanlogin AND NOT rolsuper)
                OR rolname IN ('anxious_api','anxious_runner_observer','anxious_runner_controller')),
        reach AS (
            SELECT p.oid AS principal, r.oid AS role, r.rolname, r.rolsuper FROM principals p
            JOIN pg_roles r ON r.oid = p.oid OR pg_has_role(p.oid, r.oid, 'MEMBER')),
        bindings AS (
            SELECT c.oid, c.relowner FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='runner_bindings' AND c.relname='bindings')
        SELECT reach.principal::regrole::text, reach.rolname FROM reach, bindings b
        WHERE reach.rolsuper OR reach.role = b.relowner OR reach.rolname = :writer
           OR has_table_privilege(reach.role, b.oid, 'UPDATE') OR has_table_privilege(reach.role, b.oid, 'DELETE')
           OR has_table_privilege(reach.role, b.oid, 'TRUNCATE')
           OR EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = b.oid AND a.attnum > 0 AND NOT a.attisdropped
                      AND has_column_privilege(reach.role, b.oid, a.attnum, 'UPDATE'))"""), {'writer': WRITER}).all()
    require(reaching == [])
