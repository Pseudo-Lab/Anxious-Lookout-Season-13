"""#101 decision D3 at the SQL source, no database: the only binding write is move_image's image-only
UPDATE, the writer role is its owner, and no reviewed grant file opens another path."""
from pathlib import Path
import re

from app.runner_rollout_schema import function_body

DB = Path(__file__).resolve().parent.parent/'db'
WRITER = 'anxious_runner_binding_writer'


def statements(name):
    """Statements of one SQL file, without `--` comments and with whitespace collapsed."""
    source = re.sub(r'--[^\n]*', '', (DB/name).read_text())
    return [' '.join(part.split()) for part in source.split(';') if part.strip()]


def test_move_image_writes_only_the_image_of_one_bound_plan():
    body = ' '.join(re.sub(r'--[^\n]*', '', function_body()).split())
    updates = re.findall(r'UPDATE (\S+)', body)
    assert updates == ['runner_bindings.bindings']
    # One assignment: the whole old plan with only its `image` key replaced (jsonb_set never creates a key).
    assert re.findall(r'SET (.*?) WHERE', body) == ["plan = jsonb_set(b.plan, '{image}', to_jsonb(p_image), false)"]
    assert 'b.plan = p_old' in body and 'b.owner_id = p_owner' in body and 'b.state_id = p_state' in body
    assert 'r.pod_uid IS NULL' in body and 'o.settled' in body
    # Review blocker 1: a stopped owner needs the completed, non-empty stop proof of its current generation.
    assert ("s.generation = r.generation AND s.completed_at IS NOT NULL AND jsonb_typeof(s.proof) = 'object' "
            "AND s.proof <> '{}'::jsonb") in body
    # Review blocker 2: snapshot isolation is refused before anything else.
    assert body.index("current_setting('transaction_isolation') <> 'read committed'") < body.index('IF p_owner IS NULL')
    # The registry owner lock is taken before the check-and-update (same key as Registry._lock; the
    # disposable-DB test compares the two keys).
    lock = body.index('pg_catalog.pg_advisory_xact_lock(')
    assert lock < body.index('UPDATE runner_bindings.bindings')
    assert "'runner-registry-v1:' || p_owner::text" in body and '1, 16))::bit(64)::bigint' in body
    registry = (DB.parent/'app/runner_registry.py').read_text()
    assert "('runner-registry-v1:' + str(owner)).encode()).digest()[:8], 'big', signed=True" in registry
    for forbidden in ('DELETE', 'INSERT', 'EXECUTE ', 'format(', 'TRUNCATE'):
        assert forbidden not in body


def test_function_is_definer_with_fixed_path_and_owned_by_the_nologin_writer():
    ddl = ' '.join(re.sub(r'--[^\n]*', '', (DB/'101_runner_rollout.sql').read_text()).split())
    assert 'CREATE ROLE '+WRITER+' NOLOGIN;' in ddl
    assert 'SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$' in ddl
    assert 'ALTER FUNCTION runner_rollout.move_image(uuid,uuid,jsonb,text) OWNER TO '+WRITER in ddl
    assert 'REVOKE ALL ON FUNCTION runner_rollout.move_image(uuid,uuid,jsonb,text) FROM PUBLIC' in ddl


def test_no_grant_file_gives_binding_writes_to_anyone_but_the_writer():
    for path in sorted(DB.glob('*.sql')):
        for statement in statements(path.name):
            if statement.startswith('GRANT') and 'runner_bindings.bindings' in statement:
                privileges = statement.split(' ON ')[0]
                if re.search(r'\b(UPDATE|INSERT|DELETE|TRUNCATE)\b', privileges):
                    grantee = statement.rsplit(' TO ', 1)[1]
                    # 016 controller publication (INSERT) predates #101; UPDATE is the writer's alone.
                    assert ('UPDATE' not in privileges or grantee == WRITER), (path.name, statement)


def test_observer_rollout_grants_hold_no_binding_privilege():
    grants = statements('101_runner_rollout_grants.sql')
    assert all('runner_bindings' not in statement for statement in grants)
    assert all(statement.endswith(' TO anxious_runner_observer') for statement in grants)
    assert 'GRANT EXECUTE ON FUNCTION runner_rollout.move_image(uuid,uuid,jsonb,text) TO anxious_runner_observer' in grants
