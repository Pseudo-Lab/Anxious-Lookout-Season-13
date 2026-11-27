"""#119 0011 on PostgreSQL: admin filters, membership audit definer boundary (L1/L3), read-only PATs (M2)."""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.main import create_app
from tests.test_research import identity, headers
from tests.test_research_pats import pat_client, issue, call  # noqa: F401  (fixture)

AUDIT = ("SELECT * FROM research.membership_audit(CAST(:cookie AS text),CAST(:origin AS text),CAST(:target AS uuid),"
         "CAST(:after_time AS timestamptz),CAST(:after_id AS uuid),CAST(:row_limit AS integer))")
COLUMNS = ['audit_id', 'audit_time', 'actor_label', 'actor_login', 'target_account_id', 'target_login', 'target_github_id',
           'change_reason', 'before_state', 'after_state']


def change(admin_client, auth, target, reason):
    found = next(v for v in admin_client.get('/api/admin/accounts').json()['items'] if v['accountId'] == str(target))
    payload = {'role': 'editor', 'isApproved': not found['isApproved'], 'reason': reason, 'expectedVersion': found['version']}
    reply = admin_client.put('/api/admin/accounts/'+str(target)+'/membership', json=payload, headers=headers(auth))
    assert reply.status_code == 200, reply.text


def as_api(client, cookie, origin, row_limit=10, target=None):
    with client.app.state.engine.begin() as db:  # anxious_api role, exactly as the API runs it
        return db.execute(text(AUDIT), {'cookie': cookie, 'origin': origin, 'target': target, 'after_time': None,
                                        'after_id': None, 'row_limit': row_limit})


def refused(client, cookie, origin, **kwargs):
    with pytest.raises(DBAPIError) as denied:
        as_api(client, cookie, origin, **kwargs).all()
    assert denied.value.orig.sqlstate == '42501'


def test_admin_filters_pending_first_role_and_login_search(client, admin):
    _, auth = identity(client, admin, number='300001', role='admin')
    identity(client, admin, number='300002', approved=False, role='commenter')
    identity(client, admin, number='300003', approved=True, role='editor')
    _, auth = identity(client, admin, number='300004', role='admin')
    pending = client.get('/api/admin/accounts', params={'approved': 'false'}).json()
    assert [v['login'] for v in pending['items']] == ['fixture-300002'] and pending['total'] == 1
    editors = client.get('/api/admin/accounts', params={'role': 'editor', 'approved': 'true'}).json()
    assert [v['login'] for v in editors['items']] == ['fixture-300003']
    assert client.get('/api/admin/accounts', params={'q': 'FIXTURE-30000'}).json()['total'] == 4
    assert client.get('/api/admin/accounts', params={'role': 'owner'}).status_code == 422


def test_audit_reads_through_the_definer_function_only_for_a_live_approved_admin(client, admin):
    manager, auth = identity(client, admin, number='310001', role='admin')
    manager_cookie, origin = client.cookies.get('anxious_session'), auth['Origin']
    target, _ = identity(client, admin, number='310002', approved=False, role='commenter')
    editor, _ = identity(client, admin, number='310003', role='editor')
    editor_cookie = client.cookies.get('anxious_session')
    with admin.begin() as db:
        # review r1: JSON goes in as a parameter (a literal `:false` would be parsed as a bind name by text()).
        state = '{"role": "commenter", "isApproved": false}'
        db.execute(text("INSERT INTO auth.permission_audit(actor,account_id,reason,before,after,created_at) VALUES"
                        "('operator-cli',:id,'operator fixture',CAST(:state AS jsonb),CAST(:state AS jsonb),now()-interval '1 hour')"),
                   {'id': target, 'state': state})
    with TestClient(create_app(), base_url=origin, cookies={'anxious_session': manager_cookie}) as manager_client:
        change(manager_client, auth, target, 'approve fixture')
        page = manager_client.get('/api/admin/audit', params={'limit': 1})
        assert page.status_code == 200 and page.headers['cache-control'] == 'no-store'
        first = page.json()['items'][0]
        assert first['actor'] == {'kind': 'account', 'accountId': str(manager), 'login': 'fixture-310001'}
        assert first['account'] == {'accountId': str(target), 'login': 'fixture-310002', 'githubId': '310002'}
        assert first['reason'] == 'approve fixture' and first['after'] == {'role': 'editor', 'isApproved': True}
        second = manager_client.get('/api/admin/audit', params={'limit': 1, 'cursor': page.json()['nextCursor']}).json()
        assert second['items'][0]['actor'] == {'kind': 'operator', 'label': 'operator-cli'} and second['nextCursor'] is None
        assert manager_client.get('/api/admin/audit', params={'accountId': str(editor)}).json()['items'] == []
    # Browser boundary: a non-admin session is refused by the API before the function.
    assert client.get('/api/admin/audit').status_code == 403
    # Function boundary (anxious_api role): only the declared columns, only for a live approved admin session.
    result = as_api(client, manager_cookie, origin)
    assert list(result.keys()) == COLUMNS and len(result.all()) == 2
    refused(client, editor_cookie, origin)                         # approved editor, not admin
    refused(client, manager_cookie, 'https://other.example')       # proof bound to the origin
    refused(client, 'x' * 201, origin)
    refused(client, manager_cookie, origin, row_limit=0)
    refused(client, manager_cookie, origin, row_limit=102)
    with admin.begin() as db:
        token_hash = db.execute(text('SELECT token_hash FROM auth.sessions WHERE account_id=:id'), {'id': manager}).scalar_one()
    refused(client, token_hash, origin)                            # a stored hash is not a cookie
    with admin.begin() as db:
        db.execute(text('UPDATE auth.accounts SET is_approved=false WHERE id=:id'), {'id': manager})
    refused(client, manager_cookie, origin)                        # admin approval lost
    with admin.begin() as db:
        db.execute(text('UPDATE auth.accounts SET is_approved=true WHERE id=:id'), {'id': manager})
        db.execute(text("UPDATE auth.sessions SET expires_at=now()-interval '1 second' WHERE account_id=:id"), {'id': manager})
    refused(client, manager_cookie, origin)                        # expired session
    with admin.begin() as db:
        db.execute(text('DELETE FROM auth.sessions WHERE account_id=:id'), {'id': manager})
    refused(client, manager_cookie, origin)                        # revoked (deleted) session


def test_api_role_has_no_table_grant_and_the_function_is_not_public(client, admin):
    for statement in ('SELECT * FROM auth.permission_audit', "INSERT INTO auth.permission_audit(actor,account_id,reason,before,after) "
                      "SELECT 'x',id,'x','{}','{}' FROM auth.accounts LIMIT 1"):
        with pytest.raises(DBAPIError) as denied:
            with client.app.state.engine.begin() as db:
                db.execute(text(statement))
        assert denied.value.orig.sqlstate == '42501'
    with admin.connect() as db:
        function = "research.membership_audit(text,text,uuid,timestamptz,uuid,integer)"
        assert db.execute(text("SELECT has_function_privilege('anxious_api',:f,'EXECUTE')"), {'f': function}).scalar_one()
        assert db.execute(text("SELECT count(*) FROM pg_proc p, LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a "
                               "WHERE p.oid=to_regprocedure(:f) AND a.grantee=0"), {'f': function}).scalar_one() == 0


def test_tampered_audit_function_fails_readiness_closed(client, admin):
    from app.research_schema_contract import validate_research
    function = 'research.membership_audit(text,text,uuid,timestamptz,uuid,integer)'
    assert client.get('/api/research/health').status_code == 200
    with admin.begin() as db:
        db.execute(text(f'GRANT EXECUTE ON FUNCTION {function} TO PUBLIC'))
    try:
        with admin.connect() as db:
            with pytest.raises(RuntimeError):
                validate_research(db)
        assert client.get('/api/research/health').status_code == 503
    finally:
        with admin.begin() as db:
            db.execute(text(f'REVOKE EXECUTE ON FUNCTION {function} FROM PUBLIC'))
    with admin.connect() as db:
        assert validate_research(db) == '0011_ui_feedback'
    assert client.get('/api/research/health').status_code == 200


def test_read_only_pat_reads_but_never_saves_and_old_tokens_keep_write(pat_client, admin):
    _, auth = identity(pat_client, admin)
    reader = pat_client.post('/api/research/pats', json={'name': 'Reader', 'access': 'read'}, headers=headers(auth))
    assert reader.status_code == 201 and reader.json()['credential']['access'] == 'read'
    token = reader.json()['token']
    assert call(pat_client, token).status_code == 200
    save = call(pat_client, token, 'research_document_save', {'mutationId': str(uuid.uuid4()), 'title': 'x', 'content': 'y'})
    assert save.status_code == 403 and save.json()['error']['code'] == 'forbidden'
    with admin.connect() as db:
        assert db.execute(text("SELECT count(*) FROM research.items")).scalar_one() == 0
    legacy = issue(pat_client, auth, scope='read', name='Before 0011')
    with admin.begin() as db:  # a row as issued before 0011 (#104 stored scope=read rows, no access column value)
        db.execute(text("UPDATE research.credentials SET access=NULL, scope='read' WHERE id=:id"), {'id': legacy['credential']['id']})
    listed = {v['id']: v['access'] for v in pat_client.get('/api/research/pats').json()['items']}
    assert listed[legacy['credential']['id']] == 'write'
    saved = call(pat_client, legacy['token'], 'research_document_save', {'mutationId': str(uuid.uuid4()), 'title': 'x', 'content': 'y'})
    assert saved.status_code == 200, saved.text
