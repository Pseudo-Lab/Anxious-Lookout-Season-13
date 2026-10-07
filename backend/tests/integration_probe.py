"""Disposable same-origin API fixture probe; run only in Docker."""
import json
import os
import sys
import uuid
from pathlib import Path
from urllib.parse import urljoin

import httpx


def save(path, value):
    if path.is_symlink():
        raise RuntimeError("Fixture state cannot be a symlink")
    descriptor = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as file:
        json.dump(value, file)


def run(mode, origin, filename):
    path = Path(filename)
    state = json.loads(path.read_text()) if path.exists() else {}
    with httpx.Client(base_url=origin, timeout=10, trust_env=False, follow_redirects=False) as client:
        for cookie in state.get("cookies", []):
            client.cookies.set(cookie["name"], cookie["value"], domain=cookie["domain"], path=cookie["path"])
        if mode == "login":
            result = client.get("/api/auth/github/start")
            for _ in range(5):
                assert result.status_code in {302, 303}, result.status_code
                location = result.headers["location"]
                if location == "/":
                    break
                result = client.get(urljoin(origin, location))
            else:
                raise RuntimeError("Fixture callback did not finish")
            me = client.get("/api/auth/me")
            assert me.status_code == 200
            state["me"] = me.json()
            state["cookies"] = [{"name": cookie.name, "value": cookie.value, "domain": cookie.domain, "path": cookie.path}
                                for cookie in client.cookies.jar]
            save(path, state)
            user = state["me"]["user"]
            print(f"FIXTURE githubId={user['githubId']} approved={user['isApproved']}")
            return
        me = client.get("/api/auth/me")
        assert me.status_code == 200 and me.json()["user"]["isApproved"] is True
        auth = {"Origin": origin, "X-CSRF-Token": me.json()["csrfToken"]}
        def write(method, endpoint, body, key=None):
            return client.request(method, endpoint, json=body, headers={**auth, "Idempotency-Key": key or str(uuid.uuid4())})
        if mode == "check":
            assert client.get("/api/research/health").status_code == 200
            body = {"title": "Fixture material", "sourceUrl": "https://example.com/source", "collectedAt": "2026-10-07T01:02:03Z",
                    "contentKind": "summary", "content": "<script>inert()</script>\n[link](https://example.com)"}
            key = str(uuid.uuid4())
            result = write("POST", "/api/research/materials", body, key)
            assert result.status_code == 201, result.text
            material = result.json()
            assert write("POST", "/api/research/materials", body, key).json() == material
            root = "/api/research/materials/" + material["id"]
            assert client.get(root).json()["content"] == body["content"]
            changed = write("PATCH", root, {**body, "title": "Changed", "expectedVersion": 1})
            assert changed.status_code == 200 and changed.json()["version"] == 2
            assert write("PATCH", root, {**body, "expectedVersion": 1}).status_code == 409
            assert len(client.get(root + "/versions").json()["items"]) == 2
            document = write("POST", "/api/research/documents", {"title": "Fixture draft", "content": "Markdown"}).json()
            relation = write("POST", "/api/research/relations", {"source": {"type": "document", "id": document["id"]},
                             "target": {"type": "material", "id": material["id"]}, "kind": "references", "directed": True})
            assert relation.status_code == 201
            assert client.get("/api/research/documents/" + document["id"] + "/relations").json()["items"][0]["direction"] == "outgoing"
            pending = write("POST", "/api/research/documents/" + document["id"] + "/publications", {})
            assert pending.status_code == 503 and pending.json()["error"]["code"] == "policy_pending"
            session = write("POST", "/api/research/sessions", {"title": "Unavailable native fixture"})
            assert session.status_code == 201
            session_root = "/api/research/sessions/" + session.json()["id"]
            unavailable = write("POST", session_root + "/messages", {"text": "No provider credential", "expectedVersion": 1})
            assert unavailable.status_code == 503 and unavailable.json()["error"]["code"] == "codex_unavailable"
            assert client.get(session_root).json()["state"] == "idle"
            state["materialId"], state["sessionId"] = material["id"], session.json()["id"]
            save(path, state)
            print("PASS runtime API fixture: storage/version/relation/retry/conflict/policy/session-unavailable")
        elif mode == "foreign":
            foreign = json.loads(Path(sys.argv[4]).read_text())
            assert client.get("/api/research/materials/" + foreign["materialId"]).status_code == 404
            assert client.get("/api/research/sessions/" + foreign["sessionId"]).status_code == 404
            assert client.get("/api/research/materials").json()["items"] == []
            assert client.post("/api/auth/logout", headers=auth).status_code == 204
            assert client.get("/api/research/materials").status_code == 401
            print("PASS distinct-browser fixture: foreign IDs 404 and logout 401")
        else:
            raise RuntimeError("Unknown fixture mode")


if __name__ == "__main__":
    run(*sys.argv[1:4])
