"""#119 L1/L3/M2 and migration 0011 without a database."""
import base64
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError

from app import membership
from app import research_pats as pats
from app.research import ResearchError
from app.research_receipts import MUTATIONS
from config_tests.test_research_pats import FakeDB, management, principal

ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / 'db/research_0011_ui_feedback.sql').read_text()


# --- migration 0011 -------------------------------------------------------------------------------------------------

def test_revision_chain_and_no_downgrade():
    """PM correction: like every earlier research revision, 0011 refuses downgrade (rollback = forward-fix)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location('m0011', ROOT / 'research_alembic/versions/0011_ui_feedback.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    assert module.revision == '0011_ui_feedback' and module.down_revision == '0010_research_wiki'
    with pytest.raises(RuntimeError):
        module.downgrade()
    from app.migrate import RESEARCH_REVISIONS, UI_FEEDBACK_REVISION
    from app.research_schema_contract import CURRENT, REVISIONS
    assert CURRENT == UI_FEEDBACK_REVISION == RESEARCH_REVISIONS[-1] == REVISIONS[-1]


def test_every_api_readiness_check_requires_exactly_0011():
    """An 0011 image never serves an older database; equality checks moved together (no stray 0010 equality)."""
    for path in (ROOT / 'app').glob('*.py'):
        text = path.read_text()
        assert "!= '0010_research_wiki'" not in text and '!= "0010_research_wiki"' not in text, path.name
    for name in ('posts.py', 'post_publications.py', 'research_api.py', 'research.py', 'conversations.py'):
        assert "'0011_ui_feedback'" in (ROOT / 'app' / name).read_text(), name


def test_additive_contract_matches_the_sql():
    base = json.loads((ROOT / 'db/009_research_pats_contract.json').read_text())
    additions = json.loads((ROOT / 'db/research_0011_ui_feedback_contract.json').read_text())
    assert set(additions) == {'credentials'} and 'access' not in base['credentials']['columns']
    assert additions['credentials']['columns'] == {'access': ['VARCHAR(8)', True, None]}
    check = additions['credentials']['checks']['credentials_access_check']
    assert f'ADD CONSTRAINT credentials_access_check CHECK ({check});' in SQL
    assert 'ALTER TABLE research.credentials ADD COLUMN access varchar(8);' in SQL
    assert 'UPDATE ' not in SQL.split('$audit$')[0] and 'DEFAULT' not in SQL  # no backfill, NULL = current authority


def test_audit_function_is_definer_pinned_and_executable_by_the_api_only():
    from app.research_schema_contract import AUDIT_FUNCTION
    signature = AUDIT_FUNCTION.removeprefix('research.membership_audit')
    assert 'SECURITY DEFINER SET search_path = pg_catalog AS $audit$' in SQL
    assert f'REVOKE ALL ON FUNCTION {AUDIT_FUNCTION} FROM PUBLIC;' in SQL
    assert f'GRANT EXECUTE ON FUNCTION {AUDIT_FUNCTION} TO anxious_api;' in SQL
    assert 'CREATE FUNCTION research.membership_audit(actor_cookie text, actor_origin text, target_id uuid,' in SQL
    assert signature == '(text,text,uuid,timestamptz,uuid,integer)'
    body = SQL.split('$audit$')[1]
    # Same session proof as research.set_membership, then approved-admin; refusal is SQLSTATE 42501.
    membership_body = (ROOT / 'db/004_publication.sql').read_text().split('$membership$')[1]
    proof = re.search(r"proof := .*?'hex'\);", membership_body, re.S).group(0)
    assert proof in body and "a.role='admin' AND a.is_approved" in body
    assert body.count("ERRCODE = 'insufficient_privilege'") == 2
    assert 'auth.permission_audit' in body and 'INSERT' not in body and 'UPDATE' not in body and 'DELETE' not in body
    assert 'row_limit NOT BETWEEN 1 AND 101' in body


def test_restore_reapplies_exactly_the_0011_grant_statements():
    statements = [part.strip() for part in SQL.split(';') if part.strip().startswith(('GRANT ', 'REVOKE '))]
    assert statements == ['REVOKE ALL ON FUNCTION research.membership_audit(text,text,uuid,timestamptz,uuid,integer) FROM PUBLIC',
                          'GRANT EXECUTE ON FUNCTION research.membership_audit(text,text,uuid,timestamptz,uuid,integer) TO anxious_api']
    assert "inputs.append('research_0011_ui_feedback.sql')" in (ROOT / 'app/restore_research.py').read_text()


# --- M2 access ------------------------------------------------------------------------------------------------------

def test_tokens_issued_before_0011_keep_write_authority():
    """D7: access NULL (every pre-0011 row, including old DB scope=read rows) is write, exactly as today."""
    for old_scope in ('read', 'write'):
        row, token, account = principal(); row.scope = old_scope
        assert row.access is None
        checked = pats.resolve(row, token, account)
        assert checked.access == 'write'
        pats.allowed(checked, 'research_document_save', MUTATIONS)


@pytest.mark.parametrize('operation', sorted(pats.READS))
def test_read_only_token_reads(operation):
    row, token, account = principal(); row.access = 'read'
    pats.allowed(pats.resolve(row, token, account), operation, MUTATIONS)


@pytest.mark.parametrize('operation', sorted(MUTATIONS))
def test_read_only_token_cannot_save(operation):
    row, token, account = principal(); row.access = 'read'
    with pytest.raises(ResearchError) as caught:
        pats.allowed(pats.resolve(row, token, account), operation, MUTATIONS)
    assert caught.value.args[:3] == ('forbidden', 'Read-only token', 403)


def test_read_only_token_never_gains_role_authority():
    row, token, account = principal(); row.access = 'read'; account.role = 'commenter'
    with pytest.raises(ResearchError) as caught:
        pats.allowed(pats.resolve(row, token, account), 'research_get', MUTATIONS)
    assert caught.value.args[1] == 'Private research is unavailable for this role'


def issue(client, body, key=None):
    headers = {'Origin': 'https://platform.example', 'X-CSRF-Token': 'csrf', 'Idempotency-Key': key or str(uuid4())}
    return client.post('/api/research/pats', json=body, headers=headers)


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr('app.research_api.require_schema', lambda connection: None)
    monkeypatch.setattr(FakeDB, 'connection', lambda self: None, raising=False)
    return management()


def test_issue_read_only_and_default_write(api):
    db, client = api
    read = issue(client, {'name': 'Reader', 'access': 'read'})
    write = issue(client, {'name': 'Writer'})
    assert read.status_code == write.status_code == 201
    assert read.json()['credential']['access'] == 'read' and write.json()['credential']['access'] == 'write'
    assert sorted(row.access for row in db.rows.values()) == ['read', 'write']
    assert all(row.scope == 'write' for row in db.rows.values())  # legacy column unchanged
    from pydantic import ValidationError
    from app.pat_management import Create
    for value in ('admin', None, 1):
        with pytest.raises(ValidationError):
            Create.model_validate({'name': 'x', 'access': value})


def test_retry_never_turns_a_read_issue_into_a_write_one_or_back(api):
    db, client = api
    key = str(uuid4())
    first = issue(client, {'name': 'Reader', 'access': 'read'}, key)
    assert first.status_code == 201
    replay = issue(client, {'name': 'Reader', 'access': 'read'}, key)
    assert replay.status_code == 200 and replay.json()['replayed'] and replay.json()['credential']['access'] == 'read'
    for other in ({'name': 'Reader'}, {'name': 'Reader', 'access': 'write'}, {'name': 'Reader', 'scope': 'read'}):
        assert issue(client, other, key).status_code == 409
    key = str(uuid4())
    assert issue(client, {'name': 'Writer', 'scope': 'read'}, key).status_code == 201
    assert issue(client, {'name': 'Writer', 'access': 'read'}, key).status_code == 409
    assert issue(client, {'name': 'Writer', 'access': 'write'}, key).status_code == 200
    assert len(db.rows) == 2


def test_write_issue_keeps_the_pre_0011_fingerprint_and_read_has_its_own(api):
    from app.research import mutation_fingerprint
    db, client = api
    assert issue(client, {'name': 'n', 'access': 'write'}).status_code == 201
    assert issue(client, {'name': 'r', 'access': 'read'}).status_code == 201
    stored = {db.rows[row.credential_id].name: row.fingerprint for row in db.issuances.values()}
    assert stored['n'] == mutation_fingerprint('pat:create', {'name': 'n', 'expiryDays': 30, 'scope': 'write'})
    assert stored['r'] == mutation_fingerprint('pat:create', {'name': 'r', 'expiryDays': 30, 'access': 'read'})


def test_list_value_reports_legacy_rows_as_write():
    row = SimpleNamespace(id=uuid4(), name='n', access=None, created_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
                          expires_at=datetime(2026, 12, 1, tzinfo=timezone.utc), revoked_at=None, last_used_at=None)
    assert pats.value(row, None)['access'] == 'write'
    row.access = 'read'
    assert pats.value(row, None)['access'] == 'read'


# --- L1 admin filters, L3 audit -------------------------------------------------------------------------------------

def compiled(statement):
    value = statement.compile(dialect=postgresql.dialect())
    return str(value), value.params


def test_account_filters():
    text, params = compiled(membership.account_filters(False, 'editor', '50%'))
    assert 'auth.accounts.is_approved IS false' in text and 'auth.accounts.role = %(role_1)s' in text
    assert 'ILIKE' in text and params['role_1'] == 'editor' and '50/%' in params.values()
    assert 'WHERE' not in compiled(membership.account_filters())[0]


@pytest.mark.parametrize('kwargs', [{'role': 'owner'}, {'q': ''}, {'q': 'a' * 101}, {'q': 'a\x00'}])
def test_account_filters_reject(kwargs):
    with pytest.raises(ResearchError):
        membership.account_filters(**kwargs)


class AuditDB:
    def __init__(self, rows=(), error=None):
        self.rows, self.error, self.calls = list(rows), error, []

    def execute(self, statement, params):
        self.calls.append((str(statement), params))
        if self.error:
            raise self.error
        return SimpleNamespace(all=lambda: self.rows)


def audit_row(n, actor='account:00000000-0000-4000-8000-0000000000aa', login='admin'):
    return SimpleNamespace(audit_id=UUID(int=n), audit_time=datetime(2026, 11, n, tzinfo=timezone.utc), actor_label=actor,
                           actor_login=login, target_account_id=UUID(int=99), target_login='member', target_github_id='42',
                           change_reason='why', before_state={'role': 'commenter', 'isApproved': False},
                           after_state={'role': 'editor', 'isApproved': True})


def test_audit_page_calls_the_function_with_typed_parameters_and_pages():
    db = AuditDB([audit_row(3), audit_row(2), audit_row(1, actor='operator-cli', login=None)])
    value = membership.audit_page(db, 'cookie', 'https://o', None, 2, None)
    statement, params = db.calls[0]
    assert 'research.membership_audit(CAST(:cookie AS text)' in statement and 'CAST(:row_limit AS integer)' in statement
    assert params == {'cookie': 'cookie', 'origin': 'https://o', 'target': None, 'after_time': None, 'after_id': None, 'row_limit': 3}
    assert [item['id'] for item in value['items']] == [str(UUID(int=3)), str(UUID(int=2))]
    assert value['items'][0]['actor'] == {'kind': 'account', 'accountId': '00000000-0000-4000-8000-0000000000aa', 'login': 'admin'}
    assert value['items'][0]['account'] == {'accountId': str(UUID(int=99)), 'login': 'member', 'githubId': '42'}
    assert value['items'][0]['before'] == {'role': 'commenter', 'isApproved': False}
    decoded = json.loads(base64.urlsafe_b64decode(value['nextCursor'] + '=' * (-len(value['nextCursor']) % 4)))
    assert decoded == ['2026-11-02T00:00:00Z', str(UUID(int=2))]
    target = uuid4()
    membership.audit_page(db, 'cookie', 'https://o', target, 2, value['nextCursor'])
    params = db.calls[1][1]
    assert params['after_id'] == UUID(int=2) and params['after_time'] == datetime(2026, 11, 2, tzinfo=timezone.utc)
    assert params['target'] == target


def test_operator_actor_is_shown_as_its_label():
    assert membership.audit_value(audit_row(1, actor='operator-cli', login=None))['actor'] == {'kind': 'operator', 'label': 'operator-cli'}


@pytest.mark.parametrize('cursor', ['!!!', 'a' * 513, base64.urlsafe_b64encode(b'["2026-11-01T00:00:00", "x"]').decode()])
def test_audit_bad_cursor_and_limit(cursor):
    with pytest.raises(ResearchError):
        membership.audit_page(AuditDB(), 'c', 'o', None, 30, cursor)
    with pytest.raises(ResearchError):
        membership.audit_page(AuditDB(), 'c', 'o', None, 101, None)


def test_function_refusal_maps_to_403_and_other_errors_propagate():
    refused = DBAPIError('x', {}, SimpleNamespace(sqlstate='42501'))
    with pytest.raises(ResearchError) as caught:
        membership.audit_page(AuditDB(error=refused), 'c', 'o', None, 30, None)
    assert caught.value.args[0] == 'forbidden' and caught.value.args[2] == 403
    other = DBAPIError('x', {}, SimpleNamespace(sqlstate='57014'))
    with pytest.raises(DBAPIError):
        membership.audit_page(AuditDB(error=other), 'c', 'o', None, 30, None)
