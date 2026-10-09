"""Docker-only additive upgrade and actual offline page-tool acceptance."""
import json
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.database import check_ready
from app.main import create_app
from app.migrate import migrate
from tests.offline_stack_probe import seed, client_for, post, poll
from tests.recovery_probe import signature


def prepare(root):
    migrate("0003_sessions")
    seed(root)
    state = json.loads((root / "a.json").read_text())
    origin = os.environ["AUTH_ORIGIN"]
    with TestClient(create_app(), base_url=origin, cookies={"anxious_session": state["cookie"]}) as client:
        auth = {"Origin": origin, "X-CSRF-Token": client.get("/api/auth/me").json()["csrfToken"]}
        doc = post(client, auth, "/api/research/documents", {"title": "Before upgrade", "content": "Retained legacy private draft"})
        ref = post(client, auth, "/api/research/materials", {"title": "Before upgrade source", "content": "Legacy full source", "sourceUrl": "https://example.com/legacy", "collectedAt": "2026-10-08T00:00:00Z", "contentKind": "full"})
        post(client, auth, "/api/research/relations", {"source": {"type": "document", "id": doc["id"]}, "target": {"type": "material", "id": ref["id"]}, "directed": True, "kind": "references"})
        post(client, auth, "/api/research/sessions", {"title": "Preserved before upgrade"})
        result = client.patch("/api/research/documents/" + doc["id"], json={"expectedVersion": 1, "content": "Retained legacy revision", "title": "Before upgrade revision"}, headers={**auth, "Idempotency-Key": "bd8e3843-9899-4339-b6b3-db8d75ea7919"})
        assert result.status_code == 200
    engine = create_engine(os.environ["ADMIN_DATABASE_URL"], hide_parameters=True)
    with engine.connect() as db:
        original = signature(db)
    migrate("0004_publication")
    migrate("0003_sessions")  # Minimum capability is not a downgrade.
    with engine.connect() as db:
        after = signature(db)
        assert all(after[table] == digest for table, digest in original.items())
        assert db.execute(text("SELECT version_num FROM research.alembic_version")).scalar_one() == "0004_publication"
        assert db.execute(text("SELECT version_num FROM auth.alembic_version")).scalar_one() == "0001_auth"
    check_ready(engine)
    engine.dispose()
    print("PASS actual0003→0004 upgrade: legacy auth/private versions/relations/retries/conversations unchanged; auth marker/readiness retained; lower minimum does not downgrade")


def page(root, origin):
    publisher, auth = client_for(root, "a", origin)
    visitor, visitor_auth = client_for(root, "b", origin)
    # Internal gateway DNS is transport only; CSRF retains the browser origin.
    auth["Origin"] = visitor_auth["Origin"] = os.environ["AUTH_ORIGIN"]
    try:
        doc = post(publisher, auth, "/api/research/documents", {"title": "Published offline context", "content": "Complete public document context"})
        ref = post(publisher, auth, "/api/research/materials", {"title": "Public source", "content": "Complete public reference content", "sourceUrl": "https://example.com/page", "collectedAt": "2026-10-08T00:00:00Z", "contentKind": "full"})
        post(publisher, auth, "/api/research/relations", {"source": {"type": "document", "id": doc["id"]}, "target": {"type": "material", "id": ref["id"]}, "directed": True, "kind": "references"})
        private = "/api/research/documents/" + doc["id"]
        shown = publisher.get(private + "/publication-preview").json()
        released = post(publisher, auth, private + "/publications", {key: shown[key] for key in ("versionId", "expectedVersion", "previewToken")})
        session = post(visitor, visitor_auth, "/api/research/sessions", {"title": "Visitor owned question", "publicDocumentId": doc["id"]})
        path = "/api/research/sessions/" + session["id"]
        assert publisher.get(path).status_code == 404
        reply = publisher.request("DELETE", private + "/publication", json={"expectedVersion": 2}, headers={**auth, "Idempotency-Key": "e53f8390-185b-414e-be96-0e36b785a94c"})
        assert reply.status_code == 200
        assert visitor.get("/api/public/releases/" + released["id"]).status_code == 404
        post(visitor, visitor_auth, path + "/messages", {"text": "/fixture/page-context", "expectedVersion": 1})
        completed = poll(visitor, path)
        tool = next(item for item in completed["items"] if item["type"] == "tool_call")
        assert completed["state"] == "idle" and tool["status"] == "completed"
        assert tool["name"] == "research_page_context"
        assert "Complete public document context" in json.dumps(tool["output"])
        assert ref["id"] in json.dumps(tool["output"])
        assert completed["context"]["publicationId"] == released["id"]
        post(visitor, visitor_auth, path + "/messages", {"text": "/fixture/page-reference " + ref["id"], "expectedVersion": completed["version"]})
        completed = poll(visitor, path)
        tools = [item for item in completed["items"] if item["type"] == "tool_call"]
        assert completed["state"] == "idle" and tools[-1]["status"] == "completed"
        assert "Complete public reference content" in json.dumps(tools[-1]["output"])
        before = len(visitor.get("/api/research/documents").json()["items"])
        post(visitor, visitor_auth, path + "/messages", {"text": "Use my other owned storage tool", "expectedVersion": completed["version"]})
        following = poll(visitor, path)
        assert following["state"] == "idle"
        assert len(visitor.get("/api/research/documents").json()["items"]) == before + 1
        assert publisher.get(path).status_code == 404
        print("PASS actual API/Native/dynamic callback: visitor-owned saved page context retrieved after withdrawal; author404; full native tool IO retained; independent own storage tool still writes")
    finally:
        publisher.close()
        visitor.close()


if __name__ == "__main__":
    mode, directory = sys.argv[1:3]
    if mode == "prepare":
        prepare(Path(directory))
    elif mode == "page":
        page(Path(directory), sys.argv[3])
    else:
        raise RuntimeError("Unknown isolated policy probe mode")
