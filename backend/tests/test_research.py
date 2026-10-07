import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError

from app.main import create_app, session_digest
from app.migrate import migrate


@pytest.fixture(scope="module", autouse=True)
def research_schema(schema):
    migrate("0002_research")


def identity(client, admin, number="111111", approved=True, role="commenter"):
    token, csrf = uuid.uuid4().hex, uuid.uuid4().hex
    with admin.begin() as db:
        owner = db.execute(text("INSERT INTO auth.accounts(github_id,login,is_approved,role) VALUES(:number,:login,:approved,:role) RETURNING id"),
                           {"number": number, "login": "fixture-" + number, "approved": approved, "role": role}).scalar_one()
        db.execute(text("INSERT INTO auth.sessions(token_hash,account_id,csrf_token,expires_at) VALUES(:token,:owner,:csrf,now()+interval '1 hour')"),
                   {"token": session_digest(token, client.app.state.settings.origin), "owner": owner, "csrf": csrf})
    client.cookies.set("anxious_session", token, path="/api")
    return owner, {"Origin": client.app.state.settings.origin, "X-CSRF-Token": csrf}


def headers(auth, key=None):
    return {**auth, "Idempotency-Key": key or str(uuid.uuid4())}


def material(**changes):
    return {"title": "Collected text", "sourceUrl": "https://example.com/article", "collectedAt": "2026-10-07T01:02:03Z",
            "contentKind": "excerpt", "content": "Quoted [source](https://example.com/article).", **changes}


def create(client, auth, kind="materials", **changes):
    body = material(**changes) if kind == "materials" else {"title": "Draft", "content": "# Markdown draft", **changes}
    response = client.post("/api/research/" + kind, json=body, headers=headers(auth))
    assert response.status_code == 201, response.text
    return response.json()


def test_material_and_document_snapshots_archive_and_history(client, admin):
    _, auth = identity(client, admin)
    for kind in ("materials", "documents"):
        first = create(client, auth, kind)
        root = "/api/research/" + kind + "/" + first["id"]
        body = material(title="Updated", content="New **body**") if kind == "materials" else {"title": "Updated", "content": "New **body**"}
        updated = client.patch(root, json={**body, "expectedVersion": 1}, headers=headers(auth))
        assert updated.status_code == 200, updated.text
        assert updated.json()["version"] == updated.json()["latestVersion"]["number"] == 2
        versions = client.get(root + "/versions?limit=1").json()
        assert versions["items"][0]["number"] == 2 and "content" not in versions["items"][0]
        assert client.get(root + "/versions?cursor=" + versions["nextCursor"]).json()["items"][0]["number"] == 1
        assert client.get(root + "/versions/" + first["latestVersion"]["id"]).json()["content"] == first["content"]
        archived = client.request("DELETE", root, json={"expectedVersion": 2}, headers=headers(auth))
        assert archived.json()["version"] == 3
        assert client.get(root).json()["archived"] is True
        assert client.get(root + "/versions").json()["items"][1]["number"] == 1
        assert client.get("/api/research/" + kind).json()["items"] == []
        assert client.get("/api/research/" + kind + "?archived=true").json()["items"][0]["id"] == first["id"]
        assert client.patch(root, json={**body, "expectedVersion": 3}, headers=headers(auth)).status_code == 404


def test_authority_csrf_and_idempotency_revalidate(client, admin):
    assert client.get("/api/research/materials").status_code == 401
    owner, auth = identity(client, admin, approved=False)
    assert client.get("/api/research/materials").status_code == 403
    with admin.begin() as db:
        db.execute(text("UPDATE auth.accounts SET is_approved=true WHERE id=:id"), {"id": owner})
    client.app.state.research_policy = "pending"
    assert client.get("/api/research/materials").json()["error"]["code"] == "policy_pending"
    client.app.state.research_policy = "editors"
    assert client.get("/api/research/materials").status_code == 403
    client.app.state.research_policy = "approved"
    root, body = "/api/research/materials", material()
    assert client.post(root, json=body).json()["error"]["code"] == "origin_not_allowed"
    assert client.post(root, json=body, headers={"Origin": auth["Origin"]}).json()["error"]["code"] == "csrf_invalid"
    assert client.post(root, json=body, headers=auth).status_code == 422
    key = str(uuid.uuid4())
    original = client.post(root, json=body, headers=headers(auth, key))
    retry = client.post(root, json=body, headers=headers(auth, key))
    assert original.status_code == retry.status_code == 201 and original.json() == retry.json()
    assert client.post(root, json={**body, "content": "other"}, headers=headers(auth, key)).json()["error"]["code"] == "idempotency_conflict"
    assert client.post(root, json=body, headers={**headers(auth, key), "X-CSRF-Token": "wrong"}).status_code == 403
    with admin.begin() as db:
        db.execute(text("UPDATE auth.accounts SET is_approved=false WHERE id=:id"), {"id": owner})
    assert client.post(root, json=body, headers=headers(auth, key)).status_code == 403


def test_foreign_ids_versions_and_relations_are_404(client, admin):
    _, first_auth = identity(client, admin)
    first = create(client, first_auth)
    another = create(client, first_auth)
    relation = client.post("/api/research/relations", json={"source": {"type": "material", "id": first["id"]},
                "target": {"type": "material", "id": another["id"]}, "kind": "related", "directed": False}, headers=headers(first_auth)).json()
    _, second_auth = identity(client, admin, "222222")
    second = create(client, second_auth)
    first_root = "/api/research/materials/" + first["id"]
    for path in (first_root, first_root + "/versions", first_root + "/relations", first_root + "/versions/" + first["latestVersion"]["id"], "/api/research/relations/" + relation["id"]):
        assert client.get(path).status_code == 404
    assert client.patch(first_root, json={**material(), "expectedVersion": 1}, headers=headers(second_auth)).status_code == 404
    assert client.get("/api/research/materials/" + second["id"] + "/versions/" + first["latestVersion"]["id"]).status_code == 404
    result = client.post("/api/research/relations", json={"source": {"type": "material", "id": second["id"]},
                         "target": {"type": "material", "id": first["id"]}, "kind": "related", "directed": False}, headers=headers(second_auth))
    assert result.status_code == 404
    assert client.get("/api/research/materials").json()["items"][0]["id"] == second["id"]


def test_relation_direction_dedup_edit_delete_and_archive(client, admin):
    _, auth = identity(client, admin)
    a, b, d = create(client, auth), create(client, auth), create(client, auth, "documents")
    source, target = {"type": "material", "id": a["id"]}, {"type": "material", "id": b["id"]}
    body = {"source": source, "target": target, "kind": "related", "description": "discovered later", "directed": False}
    created = client.post("/api/research/relations", json=body, headers=headers(auth))
    assert created.status_code == 201, created.text
    relation = created.json()
    reversed_body = {**body, "source": target, "target": source}
    assert client.post("/api/research/relations", json=reversed_body, headers=headers(auth)).json()["error"]["code"] == "duplicate"
    assert client.get("/api/research/materials/" + a["id"] + "/relations").json()["items"][0]["direction"] == "bidirectional"
    root = "/api/research/relations/" + relation["id"]
    change = {"expectedVersion": 1, "kind": "supports", "description": "new"}
    assert client.patch(root, json=change, headers=headers(auth)).json()["version"] == 2
    assert client.patch(root, json=change, headers=headers(auth)).status_code == 409
    assert client.request("DELETE", root, json={"expectedVersion": 2}, headers=headers(auth)).status_code == 200
    assert client.get(root).status_code == 404
    assert client.post("/api/research/relations", json=body, headers=headers(auth)).status_code == 201
    doc = {"type": "document", "id": d["id"]}
    directed = {"source": doc, "target": source, "kind": "references", "directed": True}
    assert client.post("/api/research/relations", json=directed, headers=headers(auth)).status_code == 201
    outgoing = client.get("/api/research/documents/" + d["id"] + "/relations").json()["items"][0]
    assert outgoing["direction"] == "outgoing"
    incoming = client.get("/api/research/materials/" + a["id"] + "/relations").json()["items"]
    assert any(row["direction"] == "incoming" for row in incoming)
    for invalid in ({**directed, "directed": False}, {**directed, "source": source, "target": doc}, {**body, "target": source}):
        assert client.post("/api/research/relations", json=invalid, headers=headers(auth)).status_code == 422
    assert client.request("DELETE", "/api/research/materials/" + a["id"], json={"expectedVersion": 1}, headers=headers(auth)).status_code == 200
    assert client.get("/api/research/documents/" + d["id"] + "/relations").json()["items"] == []


def test_pagination_validation_and_inert_content(client, admin):
    _, auth = identity(client, admin)
    raw = '<script>alert("x")</script>\n[Source](https://example.com)\n`code`'
    a, b = create(client, auth, content=raw), create(client, auth)
    assert client.get("/api/research/materials/" + a["id"]).json()["content"] == raw
    first = client.get("/api/research/materials?limit=1").json()
    second = client.get("/api/research/materials?limit=1&cursor=" + first["nextCursor"]).json()
    assert {first["items"][0]["id"], second["items"][0]["id"]} == {a["id"], b["id"]}
    assert second["nextCursor"] is None
    for path in ("/api/research/materials?limit=0", "/api/research/materials?cursor=bad", "/api/research/materials/not-uuid"):
        assert client.get(path).json()["error"]["code"] == "validation_error"
    for changes in ({"sourceUrl": "javascript:alert(1)"}, {"collectedAt": "2026-10-07"}, {"contentKind": "pdf"}, {"title": " "}, {"ownerId": str(uuid.uuid4())}):
        response = client.post("/api/research/materials", json=material(**changes), headers=headers(auth))
        assert response.status_code == 422
    doc = create(client, auth, "documents")
    result = client.post("/api/research/documents/" + doc["id"] + "/publications", json={}, headers=headers(auth))
    assert result.json()["error"]["code"] == "policy_pending"
    assert client.get("/api/research/codex/status").json() == {"available": False, "reason": "not_configured", "verification": "unverified"}


def test_concurrent_version_updates_and_same_key_creates(client, admin):
    _, auth = identity(client, admin)
    row = create(client, auth)
    cookies = dict(client.cookies)
    def send(body, path, key):
        with TestClient(create_app(), base_url=auth["Origin"], cookies=cookies, raise_server_exceptions=False) as other:
            return other.post(path, json=body, headers=headers(auth, key))
    key = str(uuid.uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda _: send(material(), "/api/research/materials", key), range(2)))
    assert [reply.status_code for reply in replies] == [201, 201]
    assert replies[0].json() == replies[1].json()
    def change(content):
        with TestClient(create_app(), base_url=auth["Origin"], cookies=cookies, raise_server_exceptions=False) as other:
            return other.patch("/api/research/materials/" + row["id"], json={**material(content=content), "expectedVersion": 1}, headers=headers(auth))
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(change, ["one", "two"]))
    assert sorted(reply.status_code for reply in replies) == [200, 409]
    assert len(client.get("/api/research/materials/" + row["id"] + "/versions").json()["items"]) == 2


def test_failed_save_rolls_back_and_same_key_can_retry(client, admin, monkeypatch):
    _, auth = identity(client, admin)
    from app.research import Store
    original = Store.add_snapshot
    def fail_after_insert(self, row, body):
        original(self, row, body)
        raise OperationalError("private SQL", None, Exception("private credential"))
    key = str(uuid.uuid4())
    monkeypatch.setattr(Store, "add_snapshot", fail_after_insert)
    failed = client.post("/api/research/materials", json=material(), headers=headers(auth, key))
    assert failed.status_code == 503 and "private" not in failed.text
    with admin.connect() as db:
        assert db.execute(text("SELECT count(*) FROM research.items")).scalar_one() == 0
        assert db.execute(text("SELECT count(*) FROM research.versions")).scalar_one() == 0
        assert db.execute(text("SELECT count(*) FROM research.idempotency")).scalar_one() == 0
    monkeypatch.setattr(Store, "add_snapshot", original)
    assert client.post("/api/research/materials", json=material(), headers=headers(auth, key)).status_code == 201


def test_database_enforces_cross_owner_and_immutable_versions(client, admin):
    first_owner, auth = identity(client, admin)
    first = create(client, auth)
    second_owner, second_auth = identity(client, admin, "222222")
    second = create(client, second_auth)
    with pytest.raises(DBAPIError) as invalid:
        with client.app.state.engine.begin() as db:
            db.execute(text("INSERT INTO research.relations(owner_id,source_id,source_kind,target_id,target_kind,kind,description,directed) VALUES(:owner,:source,'material',:target,'material','x','',true)"),
                       {"owner": first_owner, "source": first["id"], "target": second["id"]})
    assert invalid.value.orig.sqlstate == "23503"
    for statement in ("UPDATE research.versions SET content='tampered'", "DELETE FROM research.versions", "UPDATE research.idempotency SET status=200", "UPDATE research.alembic_version SET version_num='tampered'"):
        with pytest.raises(DBAPIError) as denied:
            with client.app.state.engine.begin() as db:
                db.execute(text(statement))
        assert denied.value.orig.sqlstate == "42501"


def test_m2_marker_and_data_preserved_by_followup(client, admin):
    owner, auth = identity(client, admin)
    row = create(client, auth)
    with admin.connect() as db:
        before = db.execute(text("SELECT * FROM auth.accounts WHERE id=:id"), {"id": owner}).one()
    migrate("0002_research")
    migrate()  # M2 rollback-compatible tooling leaves research data alone.
    with admin.connect() as db:
        assert db.execute(text("SELECT * FROM auth.accounts WHERE id=:id"), {"id": owner}).one() == before
        assert db.execute(text("SELECT version_num FROM auth.alembic_version")).scalar_one() == "0001_auth"
        assert db.execute(text("SELECT version_num FROM research.alembic_version")).scalar_one() == "0002_research"
    assert client.get("/readyz").status_code == 200
    assert client.get("/api/research/materials/" + row["id"]).status_code == 200
