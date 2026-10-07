"""Docker-only runtime release checks with a private disposable mock session."""
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit

import httpx
from sqlalchemy import create_engine, text

from app.admin import change_permissions
from app.schema_contract import EXPECTED, PRIMARY
from app.settings import secret

state_file = Path("/evidence/state.json")


def snapshot():
    engine = create_engine(secret("ADMIN_DATABASE_URL"), hide_parameters=True)
    try:
        with engine.connect() as conn:
            rows = {table: [list(row) for row in conn.execute(text(f"SELECT * FROM auth.{table} ORDER BY {PRIMARY[table]}"))] for table in EXPECTED}
            rows["alembic_version"] = conn.execute(text("SELECT version_num FROM auth.alembic_version")).scalar_one()
            return json.loads(json.dumps(rows, default=str))
    finally:
        engine.dispose()


def login(client):
    start = client.get("/api/auth/github/start")
    assert start.status_code == 302
    with httpx.Client(trust_env=False) as provider:
        callback = provider.get(start.headers["location"]).headers["location"]
    url = urlsplit(callback)
    assert client.get(url.path + "?" + url.query).status_code == 303


mode = sys.argv[1]
with httpx.Client(base_url="http://gateway:8080", trust_env=False, follow_redirects=False, timeout=10) as client:
    deadline = time.monotonic() + 60
    while True:
        try:
            if client.get("/api/health", timeout=2).status_code == 200 and client.get("/version.json", timeout=2).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        if time.monotonic() > deadline:
            raise RuntimeError("Disposable release runtime readiness deadline exceeded")
        time.sleep(0.25)
    if mode == "seed":
        login(client)
        before = client.get("/api/auth/me").json()
        assert before["user"]["role"] == "commenter" and not before["user"]["isApproved"]
        change_permissions(secret("ADMIN_DATABASE_URL"), "123456", True, "admin", "release-fixture", "isolated preservation check")
        assert client.get("/api/auth/me").status_code == 401
        login(client)
        identity = client.get("/api/auth/me").json()
        assert identity["user"]["role"] == "admin" and identity["user"]["isApproved"]
        assert identity["user"]["accountId"] == before["user"]["accountId"]
        state_file.write_text(json.dumps({"cookie": client.cookies.get("anxious_session"), "identity": identity, "snapshot": snapshot()}))
        os.chmod(state_file, 0o600)
        print("PASS seeded audited approved admin and private disposable session")
    else:
        state = json.loads(state_file.read_text())
        client.cookies.set("anxious_session", state["cookie"], path="/api")
        me = client.get("/api/auth/me")
        assert me.status_code == 200 and me.json() == state["identity"]
        assert snapshot() == state["snapshot"], "Authentication rows/revision changed during image-only switch"
        if mode == "verify":
            for path, sha, built in (("/api/version", sys.argv[2], sys.argv[3]), ("/version.json", sys.argv[4], sys.argv[5])):
                response = client.get(path)
                assert response.status_code == 200 and response.json() == {"sha": sha, "builtAt": built}
                assert response.headers["cache-control"] == "no-store"
            page = client.get("/")
            assert page.status_code == 200
            assets = set(re.findall(r'(?:src|href)="(/_next/static/[^" ]+)"', page.text))
            assert assets
            for asset in assets:
                response = client.get(asset.replace("&amp;", "&"))
                assert response.status_code == 200 and "immutable" in response.headers["cache-control"]
            assert client.get("/missing-release-page", follow_redirects=True).status_code == 404
            assert client.get("/api/missing-release-page").status_code == 404
            print(f"PASS release HTTP SHA/time, {len(assets)} assets, errors and unchanged admin UUID/role/approval/audit/session/revision")
        elif mode == "finish":
            result = client.post("/api/auth/logout", headers={"Origin": "http://127.0.0.1:8080", "X-CSRF-Token": state["identity"]["csrfToken"]})
            assert result.status_code == 204 and client.get("/api/auth/me").status_code == 401
            print("PASS retained session logout after original-image rollback")
        else:
            raise ValueError("Unsupported check mode")
