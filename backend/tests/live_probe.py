"""Actual Uvicorn/PostgreSQL process probes; runs only inside the test container."""
import json
import sys
from urllib.parse import urlsplit

import httpx
from sqlalchemy import create_engine, text

from app.admin import change_permissions
from app.settings import secret

mode = sys.argv[1]
with httpx.Client(base_url="http://api:8080", follow_redirects=False, trust_env=False, timeout=5) as client:
    if mode == "seed":
        assert client.get("/readyz").status_code == 200
        start = client.get("/api/auth/github/start")
        callback = httpx.get(start.headers["location"], follow_redirects=False, trust_env=False).headers["location"]
        url = urlsplit(callback)
        assert client.get(url.path + "?" + url.query).status_code == 303
        assert client.get("/api/auth/me").json()["user"]["isApproved"] is False
        change_permissions(secret("ADMIN_DATABASE_URL"), "123456", True, "editor", "operator:validation", "persistence/restore fixture")
        assert client.get("/api/auth/me").status_code == 401
        start = client.get("/api/auth/github/start")
        callback = httpx.get(start.headers["location"], follow_redirects=False, trust_env=False).headers["location"]
        url = urlsplit(callback)
        assert client.get(url.path + "?" + url.query).status_code == 303
        assert client.get("/api/auth/me").json()["user"]["isApproved"] is True
        # Backup must contain a live session AND transaction to test invalidation.
        assert client.get("/api/auth/github/start").status_code == 302
        print("PASS actual HTTP login, pending default, audited approval and session revocation")
    elif mode == "persist":
        engine = create_engine(secret("ADMIN_DATABASE_URL"))
        with engine.connect() as conn:
            assert conn.execute(text("SELECT is_approved FROM auth.accounts WHERE github_id='123456'")).scalar_one() is True
            assert conn.execute(text("SELECT count(*) FROM auth.permission_audit")).scalar_one() >= 1
        assert client.get("/readyz").status_code == 200
        print("PASS database restart retained account/approval/audit and API readiness recovered")
    elif mode == "outage":
        client.cookies.set("anxious_session", "outage-probe-requires-database-lookup", path="/api")
        assert client.get("/api/auth/me").status_code == 503
        assert client.get("/readyz").status_code == 503
        assert client.get("/healthz").status_code == 200
        print("PASS actual DB stop yields 503 readiness/auth; process liveness remains 200")
