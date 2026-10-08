import hashlib
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.main import create_app, session_digest
from app.migrate import migrate
from tests.test_research import identity, headers, create, material


@pytest.fixture(scope="module", autouse=True)
def publication_schema(schema):
    migrate("0004_publication")


def connect(client, auth, source, target):
    result = client.post("/api/research/relations", json={"source": {"type": source["type"], "id": source["id"]},
                        "target": {"type": target["type"], "id": target["id"]}, "kind": "references", "directed": True}, headers=headers(auth))
    assert result.status_code == 201, result.text
    return result.json()


def publish(client, auth, document):
    root = "/api/research/documents/" + document["id"]
    shown = client.get(root + "/publication-preview").json()
    body = {key: shown[key] for key in ("versionId", "expectedVersion", "previewToken")}
    reply = client.post(root + "/publications", json=body, headers=headers(auth))
    assert reply.status_code == 201, reply.text
    return reply.json()


def test_direct_reference_snapshot_latest_only_and_withdrawal_preserve_private(client, admin):
    _, auth = identity(client, admin)
    doc = create(client, auth, "documents", content="Original [source](https://example.com)")
    a, b, indirect = create(client, auth), create(client, auth, content="second source"), create(client, auth, content="private indirect")
    connect(client, auth, doc, a)
    connect(client, auth, doc, b)
    connect(client, auth, a, indirect)
    first = publish(client, auth, doc)
    assert {entry["id"] for entry in first["materials"]} == {a["id"], b["id"]}
    assert all(entry["content"] and entry["sourceUrl"] and entry["contentKind"] for entry in first["materials"])
    root = "/api/research/documents/" + doc["id"]
    public = "/api/public/documents/" + doc["id"]
    with TestClient(create_app(), base_url=auth["Origin"]) as anonymous:
        assert anonymous.get(public).json() == first
        assert anonymous.get(public + "/materials/" + indirect["id"]).status_code == 404
        assert anonymous.get(root).status_code == 401
        assert "ownerId" not in anonymous.get(public).text
    assert client.patch(root, json={"title": "Updated draft", "content": "New content", "expectedVersion": 2}, headers=headers(auth)).status_code == 200
    assert client.patch("/api/research/materials/" + a["id"], json={**material(content="New source"), "expectedVersion": 1}, headers=headers(auth)).status_code == 200
    assert client.get(public).json() == first
    second = publish(client, auth, doc)
    assert client.get(public).json() == second
    assert client.get("/api/public/releases/" + first["id"]).status_code == 404
    assert client.get(public + "/materials/" + a["id"], params={"publicationId": first["id"]}).status_code == 404
    assert client.get("/api/public/releases/" + second["id"]).status_code == 200
    assert client.get(root).json()["publication"]["materials"][0]["versionId"]
    before_versions = client.get(root + "/versions").json()
    assert client.request("DELETE", root + "/publication", json={"expectedVersion": 4}, headers=headers(auth)).status_code == 200
    assert client.get(public).status_code == 404 and client.get("/api/public/releases/" + second["id"]).status_code == 404
    assert client.get(root + "/versions").json() == before_versions
    assert len(client.get(root + "/relations").json()["items"]) == 2


def test_preview_detects_reference_changes_and_archived_target_is_visible_and_removable(client, admin):
    _, auth = identity(client, admin)
    doc, ref = create(client, auth, "documents"), create(client, auth)
    relation = connect(client, auth, doc, ref)
    root = "/api/research/documents/" + doc["id"]
    shown = client.get(root + "/publication-preview").json()
    stale = {key: shown[key] for key in ("versionId", "expectedVersion", "previewToken")}
    assert client.patch("/api/research/materials/" + ref["id"], json={**material(content="Changed reference"), "expectedVersion": 1}, headers=headers(auth)).status_code == 200
    assert client.post(root + "/publications", json=stale, headers=headers(auth)).status_code == 409
    assert client.request("DELETE", "/api/research/materials/" + ref["id"], json={"expectedVersion": 2}, headers=headers(auth)).status_code == 200
    shown = client.get(root + "/publication-preview").json()
    assert not shown["publishable"] and shown["materials"][0]["archived"] is True
    assert client.post(root + "/publications", json={key: shown[key] for key in ("versionId", "expectedVersion", "previewToken")}, headers=headers(auth)).status_code == 422
    assert client.request("DELETE", "/api/research/relations/" + relation["id"], json={"expectedVersion": 1}, headers=headers(auth)).status_code == 200
    assert publish(client, auth, doc)["materials"] == []


def test_page_question_context_is_private_survives_withdrawal_and_retrieves_full_reference(client, admin):
    publisher, auth = identity(client, admin)
    doc, ref = create(client, auth, "documents"), create(client, auth, content="Full preserved reference")
    connect(client, auth, doc, ref)
    published = publish(client, auth, doc)
    publisher_cookie = dict(client.cookies)
    visitor, visitor_auth = identity(client, admin, "222222")
    session = client.post("/api/research/sessions", json={"title": "Visitor question", "publicDocumentId": doc["id"]}, headers=headers(visitor_auth))
    assert session.status_code == 201, session.text
    session = session.json()
    assert session["context"]["publicationId"] == published["id"]
    with TestClient(create_app(), base_url=auth["Origin"], cookies=publisher_cookie) as owner:
        assert owner.get("/api/research/sessions/" + session["id"]).status_code == 404
        assert owner.request("DELETE", "/api/research/documents/" + doc["id"] + "/publication", json={"expectedVersion": 2}, headers=headers(auth)).status_code == 200
    assert client.get("/api/research/sessions/" + session["id"]).status_code == 200
    assert client.post("/api/research/sessions", json={"title": "New lookup", "publicDocumentId": doc["id"]}, headers=headers(visitor_auth)).status_code == 404
    secret, request_id = uuid.uuid4().hex + uuid.uuid4().hex, uuid.uuid4()
    login_hash = session_digest(client.cookies.get("anxious_session"), visitor_auth["Origin"])
    with admin.begin() as db:
        db.execute(text("UPDATE research.conversations SET state='running',request_id=:req,tool_token_hash=:hash,tool_expires_at=now()+interval '1 minute',login_hash=:login WHERE id=:id"),
                   {"id": session["id"], "req": request_id, "hash": hashlib.sha256(secret.encode()).hexdigest(), "login": login_hash})
    body = {"sessionId": session["id"], "requestId": str(request_id), "name": "research_page_context", "arguments": {"materialId": ref["id"]}}
    result = client.post("/api/internal/research/tools", json=body, headers={"Authorization": "Bearer " + secret})
    assert result.status_code == 200 and result.json()["content"] == "Full preserved reference"
    body["arguments"] = {"materialId": str(uuid.uuid4())}
    assert client.post("/api/internal/research/tools", json=body, headers={"Authorization": "Bearer " + secret}).status_code == 404


def test_commenter_cannot_use_private_or_question_features_even_with_legacy_config(client, admin):
    _, auth = identity(client, admin, role="commenter")
    client.app.state.research_policy = "approved"
    assert client.get("/api/research/materials").status_code == 403
    assert client.post("/api/research/sessions", json={"title": "Forbidden"}, headers=headers(auth)).status_code == 403
    assert client.get("/api/admin/accounts").status_code == 403


def test_admin_membership_is_audited_concurrent_safe_and_revokes_target_browser_tool(client, admin):
    manager, manager_auth = identity(client, admin, role="admin")
    manager_cookie = dict(client.cookies)
    target, target_auth = identity(client, admin, "222222")
    target_cookie = dict(client.cookies)
    session = client.post("/api/research/sessions", json={"title": "Target"}, headers=headers(target_auth)).json()
    secret, request_id = uuid.uuid4().hex + uuid.uuid4().hex, uuid.uuid4()
    with admin.begin() as db:
        db.execute(text("UPDATE research.conversations SET state='running',request_id=:req,tool_token_hash=:hash,tool_expires_at=now()+interval '1 minute',login_hash=:login WHERE id=:id"),
                   {"id": session["id"], "req": request_id, "hash": hashlib.sha256(secret.encode()).hexdigest(), "login": session_digest(client.cookies.get("anxious_session"), target_auth["Origin"])})
    with TestClient(create_app(), base_url=manager_auth["Origin"], cookies=manager_cookie) as manager_client:
        accounts = manager_client.get("/api/admin/accounts").json()["items"]
        found = next(value for value in accounts if value["accountId"] == str(target))
        payload = {"role": "commenter", "isApproved": False, "reason": "Revoke fixture editor", "expectedVersion": found["version"]}
        path, key = "/api/admin/accounts/" + str(target) + "/membership", str(uuid.uuid4())
        result = manager_client.put(path, json=payload, headers=headers(manager_auth, key))
        assert result.status_code == 200, result.text
        assert manager_client.put(path, json=payload, headers=headers(manager_auth, key)).json() == result.json()
        assert manager_client.put(path, json=payload, headers=headers(manager_auth)).status_code == 409
        assert manager_client.put("/api/admin/accounts/" + str(manager) + "/membership", json=payload, headers=headers(manager_auth)).status_code == 403
    assert client.get("/api/auth/me").status_code == 401
    tool = {"sessionId": session["id"], "requestId": str(request_id), "name": "research_list", "arguments": {"type": "document"}}
    assert client.post("/api/internal/research/tools", json=tool, headers={"Authorization": "Bearer " + secret}).status_code == 403
    with admin.connect() as db:
        assert db.execute(text("SELECT count(*) FROM auth.permission_audit WHERE account_id=:id"), {"id": target}).scalar_one() == 1
        assert db.execute(text("SELECT native_record FROM research.conversations WHERE id=:id"), {"id": session["id"]}).scalar_one() == {}


def test_db_membership_requires_raw_live_admin_cookie_and_publications_are_immutable(client, admin):
    actor, auth = identity(client, admin, role="admin")
    target, _ = identity(client, admin, "222222", approved=False, role="commenter")
    with admin.connect() as db:
        actor_hash = db.execute(text("SELECT token_hash FROM auth.sessions WHERE account_id=:id"), {"id": actor}).scalar_one()
        version = db.execute(text("SELECT updated_at FROM auth.accounts WHERE id=:id"), {"id": target}).scalar_one()
    with client.app.state.engine.begin() as db:
        result = db.execute(text("SELECT research.set_membership(:proof,:origin,:id,true,'editor',:version,'forged proof')"),
                            {"proof": actor_hash, "origin": auth["Origin"], "id": target, "version": version}).scalar_one()
        assert result == "forbidden"
    for statement in ("UPDATE research.publications SET snapshot='{}'", "DELETE FROM research.publications", "UPDATE auth.accounts SET role='admin'"):
        with pytest.raises(DBAPIError) as denied:
            with client.app.state.engine.begin() as db:
                db.execute(text(statement))
        assert denied.value.orig.sqlstate == "42501"


def test_admin_grants_existing_pending_member_and_concurrent_change_has_one_audit(client, admin):
    manager, auth = identity(client, admin, role="admin")
    cookies = dict(client.cookies)
    target, target_auth = identity(client, admin, "222222", approved=False, role="commenter")
    with TestClient(create_app(), base_url=auth["Origin"], cookies=cookies) as actor:
        listing = actor.get("/api/admin/accounts?limit=1").json()
        assert listing["nextCursor"]
        detail = next(value for value in actor.get("/api/admin/accounts").json()["items"] if value["accountId"] == str(target))
        body = {"role": "editor", "isApproved": True, "reason": "Approved fixture identity", "expectedVersion": detail["version"]}
        path = "/api/admin/accounts/" + str(target) + "/membership"
        assert actor.put(path, json={**body, "role": "admin"}, headers=headers(auth)).status_code == 403
        assert actor.put(path, json=body).status_code == 403
        assert actor.put(path, json=body, headers={"Origin": auth["Origin"]}).status_code == 403
        def grant(_):
            with TestClient(create_app(), base_url=auth["Origin"], cookies=cookies) as other:
                return other.put(path, json=body, headers=headers(auth))
        with ThreadPoolExecutor(max_workers=2) as pool:
            replies = list(pool.map(grant, range(2)))
        assert sorted(reply.status_code for reply in replies) == [200, 409]
    assert client.get("/api/auth/me").status_code == 401
    with admin.connect() as db:
        assert db.execute(text("SELECT role,is_approved FROM auth.accounts WHERE id=:id"), {"id": target}).one() == ("editor", True)
        assert db.execute(text("SELECT count(*) FROM auth.permission_audit WHERE account_id=:id"), {"id": target}).scalar_one() == 1


def test_concurrent_publications_from_same_preview_one_wins_and_replay_is_private(client, admin):
    _, auth = identity(client, admin)
    doc = create(client, auth, "documents")
    path = "/api/research/documents/" + doc["id"]
    shown = client.get(path + "/publication-preview").json()
    body = {key: shown[key] for key in ("versionId", "expectedVersion", "previewToken")}
    cookies = dict(client.cookies)
    def send(_):
        with TestClient(create_app(), base_url=auth["Origin"], cookies=cookies) as other:
            return other.post(path + "/publications", json=body, headers=headers(auth))
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(send, range(2)))
    assert sorted(reply.status_code for reply in replies) == [201, 409]
    assert client.get("/api/public/documents").json()["items"][0]["documentId"] == doc["id"]


@pytest.mark.parametrize("fault,repair", [
    ("REVOKE EXECUTE", "GRANT EXECUTE"),
    ("PUBLIC EXECUTE", "PRIVATE EXECUTE"),
    ("SECURITY INVOKER", "SECURITY DEFINER"),
    ("SET search_path=public", "SET search_path=pg_catalog"),
])
def test_missing_publication_grant_or_tampered_membership_authority_fails_closed(client, admin, fault, repair):
    _, auth = identity(client, admin)
    function = "research.set_membership(text,text,uuid,boolean,text,timestamptz,text)"
    def statement(action):
        if action in {"REVOKE EXECUTE", "GRANT EXECUTE"}:
            return action + " ON FUNCTION " + function + (" FROM " if action.startswith("REVOKE") else " TO ") + "anxious_api"
        if action in {"PUBLIC EXECUTE", "PRIVATE EXECUTE"}:
            return ("GRANT EXECUTE ON FUNCTION " + function + " TO PUBLIC") if action.startswith("PUBLIC") else ("REVOKE EXECUTE ON FUNCTION " + function + " FROM PUBLIC")
        return "ALTER FUNCTION " + function + " " + action
    with admin.begin() as db:
        db.execute(text(statement(fault)))
    try:
        assert client.get("/api/public/documents").status_code == 503
        with pytest.raises(RuntimeError):
            migrate("0004_publication")
    finally:
        with admin.begin() as db:
            db.execute(text(statement(repair)))
    assert client.get("/api/research/health").status_code == 200


def test_membership_revocation_waits_for_admitted_tool_then_denies_later_calls(client, admin, monkeypatch):
    from app.research import Store
    _, auth = identity(client, admin, role="admin")
    manager_cookie = dict(client.cookies)
    target, target_auth = identity(client, admin, "222222")
    session = client.post("/api/research/sessions", json={"title": "Concurrent revocation"}, headers=headers(target_auth)).json()
    secret, request_id = uuid.uuid4().hex + uuid.uuid4().hex, uuid.uuid4()
    login = session_digest(client.cookies.get("anxious_session"), target_auth["Origin"])
    with admin.begin() as db:
        version = db.execute(text("SELECT updated_at FROM auth.accounts WHERE id=:id"), {"id": target}).scalar_one().isoformat()
        db.execute(text("UPDATE research.conversations SET state='running',request_id=:req,tool_token_hash=:hash,tool_expires_at=now()+interval '1 minute',login_hash=:login WHERE id=:id"),
                   {"id": session["id"], "req": request_id, "hash": hashlib.sha256(secret.encode()).hexdigest(), "login": login})
    admitted, release = Event(), Event()
    original = Store.create_item
    def hold(store, kind, body):
        admitted.set()
        assert release.wait(5)
        return original(store, kind, body)
    monkeypatch.setattr(Store, "create_item", hold)
    tool_body = {"sessionId": session["id"], "requestId": str(request_id), "name": "research_document_save",
                 "arguments": {"title": "Admitted before revocation", "content": "Committed once", "mutationId": str(uuid.uuid4())}}
    def call_tool():
        with TestClient(create_app(), base_url=auth["Origin"]) as caller:
            return caller.post("/api/internal/research/tools", json=tool_body, headers={"Authorization": "Bearer " + secret})
    def revoke():
        with TestClient(create_app(), base_url=auth["Origin"], cookies=manager_cookie) as actor:
            return actor.put("/api/admin/accounts/" + str(target) + "/membership", json={"role": "commenter", "isApproved": False, "reason": "Concurrent fixture revoke", "expectedVersion": version}, headers=headers(auth))
    with ThreadPoolExecutor(max_workers=2) as pool:
        tool = pool.submit(call_tool)
        assert admitted.wait(5)
        change = pool.submit(revoke)
        try:
            deadline = time.monotonic() + 4
            blocked = False
            while time.monotonic() < deadline:
                with admin.connect() as db:
                    blocked = db.execute(text("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE query LIKE '%SELECT research.set_membership%' AND cardinality(pg_blocking_pids(pid))>0)")).scalar_one()
                if blocked:
                    break
                time.sleep(0.01)
            assert blocked and not change.done()
        finally:
            release.set()
        assert tool.result(timeout=5).status_code == 200
        assert change.result(timeout=5).status_code == 200
    assert call_tool().status_code == 403
    with admin.connect() as db:
        assert db.execute(text("SELECT count(*) FROM research.items WHERE owner_id=:id AND kind='document'"), {"id": target}).scalar_one() == 1
