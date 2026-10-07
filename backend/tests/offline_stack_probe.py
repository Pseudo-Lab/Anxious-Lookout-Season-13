"""Docker-only actual HTTP API/adapter/protocol fixture acceptance probe."""
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.main import session_digest
from tests.integration_probe import save
from tests.offline_services import require_offline


def seed(root):
    require_offline()
    if make_url(os.environ["ADMIN_DATABASE_URL"]).database != "hosting_test":
        raise RuntimeError("A disposable DB is required")
    engine = create_engine(os.environ["ADMIN_DATABASE_URL"], hide_parameters=True)
    result = {}
    with engine.begin() as db:
        if db.execute(text("SELECT count(*) FROM auth.accounts")).scalar_one():
            raise RuntimeError("Seed requires a fresh empty fixture DB")
        for label, github_id in (("a", "910001"), ("b", "910002")):
            owner = db.execute(text("INSERT INTO auth.accounts(github_id,login,is_approved,role) VALUES(:github,:login,true,'editor') RETURNING id"),
                               {"github": github_id, "login": "fixture-offline-" + label}).scalar_one()
            cookie, csrf = uuid.uuid4().hex, uuid.uuid4().hex
            db.execute(text("INSERT INTO auth.sessions(token_hash,account_id,csrf_token,expires_at) VALUES(:hash,:owner,:csrf,now()+interval '1 hour')"),
                       {"hash": session_digest(cookie, os.environ["AUTH_ORIGIN"]), "owner": owner, "csrf": csrf})
            save(root / f"{label}.json", {"owner": str(owner), "cookie": cookie})
            result[label] = str(owner)
    engine.dispose()
    save(root / "accounts.json", result)
    print(json.dumps(result))  # Public disposable account UUIDs only, never cookies/tokens.


def client_for(root, label, origin):
    saved = json.loads((root / f"{label}.json").read_text())
    client = httpx.Client(base_url=origin, trust_env=False, timeout=15, follow_redirects=False)
    client.cookies.set("anxious_session", saved["cookie"], path="/api")
    me = client.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["user"]["accountId"] == saved["owner"]
    return client, {"Origin": origin, "X-CSRF-Token": me.json()["csrfToken"]}


def post(client, auth, path, body, key=None):
    result = client.post(path, json=body, headers={**auth, "Idempotency-Key": key or str(uuid.uuid4())})
    assert result.status_code in {201, 202}, (result.status_code, result.json().get("error", {}).get("code"))
    return result.json()


def poll(client, path):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        reply = client.get(path)
        assert reply.status_code == 200
        result = reply.json()
        if result["state"] != "running":
            return result
        time.sleep(0.1)
    raise AssertionError("Offline fixture did not settle")


def exercise(root, origin):
    a, auth_a = client_for(root, "a", origin)
    b, auth_b = client_for(root, "b", origin)
    try:
        assert a.get("/api/research/codex/status").json()["verification"] == "fixture"
        assert a.get("/api/research/codex/status").json()["available"] is True
        session = post(a, auth_a, "/api/research/sessions", {"title": "Offline actual adapter"})
        path = "/api/research/sessions/" + session["id"]
        original, key = {"text": "Store an offline fixture document", "expectedVersion": 1}, str(uuid.uuid4())
        sent = post(a, auth_a, path + "/messages", original, key)
        detail = poll(a, path)
        assert detail["state"] == "idle"
        tool = next(item for item in detail["items"] if item["type"] == "tool_call")
        assert tool["input"]["content"] == "Complete fixture tool input" and tool["status"] == "completed"
        raw = a.get(path + "/items/" + tool["id"]).json()["raw"]
        assert raw["arguments"] == tool["input"] and raw["contentItems"] == tool["output"]
        documents = a.get("/api/research/documents").json()["items"]
        assert len(documents) == 1
        assert post(a, auth_a, path + "/messages", original, key) == sent
        assert len(a.get("/api/research/documents").json()["items"]) == 1
        assert b.get(path).status_code == 404
        assert b.get("/api/research/documents/" + documents[0]["id"]).status_code == 404
        assert b.get("/api/research/documents").json()["items"] == []
        a.close()
        a, auth_a = client_for(root, "a", origin)  # Browser/client reconnect.
        assert a.get(path).json()["items"] == detail["items"]
        assert post(a, auth_a, path + "/messages", original, key) == sent

        failed = post(a, auth_a, "/api/research/sessions", {"title": "Unrecorded input"})
        failed_path = "/api/research/sessions/" + failed["id"]
        post(a, auth_a, failed_path + "/messages", {"text": "/fixture/unrecorded", "expectedVersion": 1})
        unrecorded = poll(a, failed_path)
        assert unrecorded["state"] == "failed" and unrecorded["items"][0]["status"] == "not_recorded"
        post(a, auth_a, failed_path + "/messages", {"text": "An explicit followup", "expectedVersion": unrecorded["version"]})
        later = poll(a, failed_path)
        assert later["state"] == "idle" and later["items"][0]["text"] == "/fixture/unrecorded"
        assert later["items"][0]["source"] == "platform"

        rejected = post(a, auth_a, "/api/research/sessions", {"title": "Definite rejection"})
        rejected_path = "/api/research/sessions/" + rejected["id"]
        reject_body, reject_key = {"text": "/fixture/reject", "expectedVersion": 1}, str(uuid.uuid4())
        reject_sent = post(a, auth_a, rejected_path + "/messages", reject_body, reject_key)
        rejection = poll(a, rejected_path)
        assert rejection["state"] == "failed" and rejection["error"]["code"] == "codex_rejected"
        assert post(a, auth_a, rejected_path + "/messages", reject_body, reject_key) == reject_sent
        post(a, auth_a, rejected_path + "/messages", {"text": "Explicit after rejection", "expectedVersion": rejection["version"]})
        assert poll(a, rejected_path)["items"][0]["text"] == "/fixture/reject"

        busy = post(a, auth_a, "/api/research/sessions", {"title": "Hold fixture"})
        busy_path = "/api/research/sessions/" + busy["id"]
        post(a, auth_a, busy_path + "/messages", {"text": "/fixture/hold", "expectedVersion": 1})
        competing = post(a, auth_a, "/api/research/sessions", {"title": "Concurrent same-owner"})
        competing_path = "/api/research/sessions/" + competing["id"]
        post(a, auth_a, competing_path + "/messages", {"text": "Busy owner's second session", "expectedVersion": 1})
        assert poll(a, competing_path)["error"]["code"] == "codex_rejected"
        independent = post(b, auth_b, "/api/research/sessions", {"title": "Independent owner B"})
        independent_path = "/api/research/sessions/" + independent["id"]
        post(b, auth_b, independent_path + "/messages", {"text": "Owner B independent", "expectedVersion": 1})
        assert poll(b, independent_path)["state"] == "idle"
        assert poll(a, busy_path)["state"] == "idle"
        assert a.get(independent_path).status_code == 404
        save(root / "restart.json", {"path": path, "original": original, "key": key, "response": sent,
                                     "history": detail["items"], "beforeDocuments": len(a.get("/api/research/documents").json()["items"])})
        print("PASS actual offline API/adapter/tools: full IO, failed-input retention, rejected/busy, reconnect/replay, two-account isolation")
    finally:
        a.close()
        b.close()


def resumed(root, origin):
    a, auth = client_for(root, "a", origin)
    saved = json.loads((root / "restart.json").read_text())
    try:
        current = a.get(saved["path"]).json()
        assert current["items"] == saved["history"]
        assert post(a, auth, saved["path"] + "/messages", saved["original"], saved["key"]) == saved["response"]
        assert len(a.get("/api/research/documents").json()["items"]) == saved["beforeDocuments"]
        post(a, auth, saved["path"] + "/messages", {"text": "Explicit after adapter process restart", "expectedVersion": current["version"]})
        assert len([item for item in poll(a, saved["path"])["items"] if item["type"] == "tool_call"]) == 2
        print("PASS offline adapter/API process restart: retained full history, same-key no dispatch, explicit followup uses original fixture thread")
    finally:
        a.close()


if __name__ == "__main__":
    mode, directory = sys.argv[1:3]
    root = Path(directory)
    if mode == "seed":
        seed(root)
    elif mode == "exercise":
        exercise(root, sys.argv[3])
    elif mode == "resumed":
        resumed(root, sys.argv[3])
    else:
        raise RuntimeError("Unknown offline probe mode")
