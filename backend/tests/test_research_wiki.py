"""#105 research wiki over PostgreSQL 0010: history, index, evidence ownership, lint, public scope."""
import json

import pytest
from sqlalchemy import text

from app.migrate import migrate
from tests.test_research import create, headers, identity, material
from tests.test_policy_publication import publish

BIBLIOGRAPHY = {"type": "article-journal", "title": "A study", "author": [{"family": "Kim", "given": "A"}],
                "issued": {"date-parts": [[2024, 5]]}, "container-title": "Journal", "DOI": "10.1234/abc"}


@pytest.fixture(scope="module", autouse=True)
def wiki_schema(schema):
    migrate("0010_research_wiki")


def relate(client, auth, source, target, kind="supports", status=201, **extra):
    body = {"source": {"type": source.get("type", "material"), "id": source["id"]},
            "target": {"type": target.get("type", "material"), "id": target["id"]}, "kind": kind, "description": "why", **extra}
    reply = client.post("/api/research/relations", json=body, headers=headers(auth))
    assert reply.status_code == status, reply.text
    return reply.json()


def reason(reply):
    assert reply.status_code == 422, reply.text
    return reply.json()["error"]["details"]["reason"]


def test_summary_and_bibliography_are_versioned_and_carried(client, admin):
    _, auth = identity(client, admin)
    first = create(client, auth, summary=" 한 줄 ", bibliography=BIBLIOGRAPHY)
    assert (first["summary"], first["bibliography"]) == ("한 줄", BIBLIOGRAPHY)
    root = "/api/research/materials/" + first["id"]
    kept = client.patch(root, json={**material(title="Edited"), "expectedVersion": 1}, headers=headers(auth)).json()
    assert (kept["summary"], kept["bibliography"]) == ("한 줄", BIBLIOGRAPHY)
    cleared = client.patch(root, json={**material(), "summary": None, "bibliography": None, "expectedVersion": 2},
                           headers=headers(auth)).json()
    assert (cleared["summary"], cleared["bibliography"]) == (None, None)
    history = client.get(root + "/versions").json()["items"]
    assert [(item["number"], item["summary"], item["bibliography"]) for item in history] == [
        (3, None, None), (2, "한 줄", BIBLIOGRAPHY), (1, "한 줄", BIBLIOGRAPHY)]
    rejected = client.post("/api/research/materials", json=material(bibliography={**BIBLIOGRAPHY, "URL": "https://x.org"}),
                           headers=headers(auth))
    assert rejected.status_code == 422
    document = create(client, auth, "documents", summary="문서 요약")
    assert document["summary"] == "문서 요약" and "bibliography" not in document


def test_draft_commit_sets_or_keeps_document_summary(client, admin):
    _, auth = identity(client, admin)
    draft = client.post("/api/research/document-drafts", json={"title": "Doc", "content": "Body"}, headers=headers(auth)).json()
    committed = client.post(f"/api/research/document-drafts/{draft['id']}/commit",
                            json={"expectedDraftRevision": draft["revision"], "summary": "첫 요약"}, headers=headers(auth))
    assert committed.status_code == 200, committed.text
    document = committed.json()["document"]
    assert document["summary"] == "첫 요약"
    again = client.post("/api/research/document-drafts", json={"title": "Doc 2", "content": "Body 2", "documentId": document["id"],
                        "baseDocumentVersion": document["version"], "baseVersionId": document["latestVersion"]["id"]},
                        headers=headers(auth)).json()
    kept = client.post(f"/api/research/document-drafts/{again['id']}/commit",
                       json={"expectedDraftRevision": again["revision"], "expectedVersion": document["version"]}, headers=headers(auth))
    assert kept.status_code == 200, kept.text
    assert kept.json()["document"]["summary"] == "첫 요약"


def test_index_lists_summary_and_active_relation_count(client, admin):
    _, auth = identity(client, admin)
    a, b, c = create(client, auth, summary="A"), create(client, auth), create(client, auth)
    relate(client, auth, a, b)
    relate(client, auth, a, c, "same_topic")
    archived = relate(client, auth, b, c, "refutes")
    client.request("DELETE", "/api/research/relations/" + archived["id"], json={"expectedVersion": 1}, headers=headers(auth))
    listed = {item["id"]: item for item in client.get("/api/research/materials").json()["items"]}
    assert [(listed[x["id"]]["summary"], listed[x["id"]]["relationCount"]) for x in (a, b, c)] == [("A", 2), (None, 1), (None, 1)]
    assert "relationCount" not in client.get("/api/research/materials/" + a["id"]).json()


def test_relation_vocabulary_direction_and_reason(client, admin):
    _, auth = identity(client, admin)
    a, b = create(client, auth), create(client, auth)
    d = {**create(client, auth, "documents"), "type": "document"}
    same = relate(client, auth, a, b, "same_topic")
    assert (same["directed"], same["kindKnown"], same["evidence"]) == (False, True, [])
    assert relate(client, auth, d, a, "uses_data")["directed"] is True
    url = "/api/research/relations"
    body = {"source": {"type": "material", "id": a["id"]}, "target": {"type": "material", "id": b["id"]}, "description": "x"}
    assert reason(client.post(url, json={**body, "kind": "related"}, headers=headers(auth))) == "relation_kind_unknown"
    assert reason(client.post(url, json={**body, "kind": "supports", "directed": False}, headers=headers(auth))) == "relation_direction_mismatch"
    assert reason(client.post(url, json={**body, "kind": "supports", "description": "  "}, headers=headers(auth))) == "relation_reason_required"
    doc_body = {"source": {"type": "document", "id": d["id"]}, "target": {"type": "material", "id": b["id"]},
                "kind": "same_topic", "description": "x"}
    assert reason(client.post(url, json=doc_body, headers=headers(auth))) == "relation_direction_mismatch"
    change = client.patch(url + "/" + same["id"], json={"expectedVersion": 1, "kind": "supports", "description": "x"},
                          headers=headers(auth))
    assert reason(change) == "relation_direction_mismatch"


def test_legacy_kinds_read_as_unknown_and_must_move_on_edit(client, admin):
    owner, auth = identity(client, admin)
    a, b = create(client, auth), create(client, auth)
    with admin.begin() as db:
        legacy = db.execute(text("""INSERT INTO research.relations(owner_id,source_id,source_kind,target_id,target_kind,kind,description,directed)
                                    VALUES(:owner,:a,'material',:b,'material','참고','',true) RETURNING id"""),
                            {"owner": owner, "a": a["id"], "b": b["id"]}).scalar_one()
    root = f"/api/research/relations/{legacy}"
    read = client.get(root).json()
    assert (read["kind"], read["kindKnown"], read["evidence"], read["description"]) == ("참고", False, [], "")
    only_reason = client.patch(root, json={"expectedVersion": 1, "kind": "참고", "description": "근거"}, headers=headers(auth))
    assert reason(only_reason) == "relation_kind_unknown"
    moved = client.patch(root, json={"expectedVersion": 1, "kind": "extends", "description": "근거"}, headers=headers(auth))
    assert moved.status_code == 200 and moved.json()["kindKnown"] is True


def test_evidence_cites_only_own_endpoint_versions(client, admin):
    _, auth = identity(client, admin)
    a, b, c = create(client, auth), create(client, auth), create(client, auth)
    evidence = [{"itemId": b["id"], "versionId": b["latestVersion"]["id"], "locator": "p.3", "quote": "인용"}]
    saved = relate(client, auth, a, b, evidence=evidence)
    assert saved["evidence"] == [{**evidence[0], "versionNumber": 1}]
    url = "/api/research/relations"
    base = {"source": {"type": "material", "id": a["id"]}, "target": {"type": "material", "id": c["id"]},
            "kind": "extends", "description": "x"}
    outside = [{"itemId": b["id"], "versionId": b["latestVersion"]["id"], "locator": "p.1"}]
    assert reason(client.post(url, json={**base, "evidence": outside}, headers=headers(auth))) == "evidence_endpoint_mismatch"
    wrong_item = [{"itemId": c["id"], "versionId": b["latestVersion"]["id"], "locator": "p.1"}]
    assert reason(client.post(url, json={**base, "evidence": wrong_item}, headers=headers(auth))) == "evidence_version_not_found"
    _, other_auth = identity(client, admin, "333333")
    foreign = create(client, other_auth)
    _, auth2 = identity(client, admin, "555555")  # The session cookie now belongs to this owner.
    x, y = create(client, auth2), create(client, auth2)
    stolen = [{"itemId": y["id"], "versionId": foreign["latestVersion"]["id"], "locator": "p.1"}]
    body = {"source": {"type": "material", "id": x["id"]}, "target": {"type": "material", "id": y["id"]},
            "kind": "supports", "description": "x", "evidence": stolen}
    assert reason(client.post(url, json=body, headers=headers(auth2))) == "evidence_version_not_found"
    many = [{"itemId": y["id"], "versionId": y["latestVersion"]["id"], "quote": str(n)} for n in range(21)]
    assert reason(client.post(url, json={**body, "evidence": many}, headers=headers(auth2))) == "evidence_limit_exceeded"
    kept = relate(client, auth2, x, y, evidence=many[:2])
    root = url + "/" + kept["id"]
    patched = client.patch(root, json={"expectedVersion": 1, "kind": "supports", "description": "y"}, headers=headers(auth2)).json()
    assert len(patched["evidence"]) == 2
    cleared = client.patch(root, json={"expectedVersion": 2, "kind": "supports", "description": "y", "evidence": []},
                           headers=headers(auth2)).json()
    assert cleared["evidence"] == []


def test_public_snapshot_carries_no_wiki_fields(client, admin):
    _, auth = identity(client, admin)
    m = create(client, auth, summary="비공개 요약", bibliography=BIBLIOGRAPHY)
    d = {**create(client, auth, "documents", summary="문서 요약"), "type": "document"}
    relate(client, auth, d, m, evidence=[{"itemId": m["id"], "versionId": m["latestVersion"]["id"], "quote": "q"}])
    preview = client.get("/api/research/documents/" + d["id"] + "/publication-preview").json()
    assert all("summary" not in item and "bibliography" not in item for item in preview["materials"])
    publish(client, auth, d)
    public = json.dumps(client.get("/api/public/documents/" + d["id"]).json(), ensure_ascii=False)
    for secret in ("비공개 요약", "문서 요약", "A study", '"q"', "bibliography", "evidence"):
        assert secret not in public


def test_lint_and_relation_kinds_endpoint(client, admin):
    _, auth = identity(client, admin)
    old, new, lone = create(client, auth), create(client, auth), create(client, auth, bibliography=BIBLIOGRAPHY)
    d = {**create(client, auth, "documents"), "type": "document"}
    relate(client, auth, new, old, "updates")
    relate(client, auth, d, old, "supports")
    kinds = client.get("/api/research/relation-kinds")
    assert kinds.status_code == 200 and kinds.headers["cache-control"] == "no-store"
    assert [item["key"] for item in kinds.json()["items"]][-1] == "same_topic"
    from app.research import Store
    from app.research_lint import lint
    with client.app.state.sessions.begin() as db:
        owner = db.execute(text("SELECT owner_id FROM research.items WHERE id=:id"), {"id": lone["id"]}).scalar_one()
        result = lint(Store(db, owner))
    assert result["counts"]["orphan_material"] == 1 and result["counts"]["cites_superseded"] == 1
    assert result["counts"]["missing_bibliography"] == 2  # old and new have none; lone is complete
