from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from dataclasses import replace
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import httpx
import os
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.admin import change_permissions
from app.main import SESSION_COOKIE, TRANSACTION_COOKIE, create_app, digest, now
from app.settings import Settings


def provider_callback(client, **options):
    start = client.get("/api/auth/github/start")
    assert start.status_code == 302
    assert "HttpOnly" in start.headers["set-cookie"] and "SameSite=lax" in start.headers["set-cookie"]
    parsed = urlsplit(start.headers["location"])
    query = {k: v[0] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
    assert query["scope"] == "" and query["code_challenge_method"] == "S256"
    query.update(options)
    with httpx.Client(trust_env=False) as provider:
        response = provider.get(urlunsplit(parsed._replace(query=urlencode(query))))
    return response.headers["location"]


def login(client, **options):
    callback = provider_callback(client, **options)
    response = client.get(callback)
    assert response.status_code == 303 and response.headers["location"] == "/"
    return client.get("/api/auth/me").json(), callback


def test_pending_identity_and_token_boundaries(client, admin):
    assert client.get("/api/auth/me").status_code == 401
    data, _ = login(client)
    user = data["user"]
    assert user["role"] == "commenter" and user["isApproved"] is False
    assert user["githubId"] == "123456" and user["accountId"] != user["githubId"]
    assert "avatarUrl" not in user and "access_token" not in str(data)
    token = client.cookies.get(SESSION_COOKIE)
    with admin.connect() as conn:
        saved = conn.execute(text("SELECT token_hash FROM auth.sessions")).scalar_one()
        assert saved == digest(token) and saved != token
        assert conn.execute(text("SELECT count(*) FROM auth.oauth_transactions")).scalar_one() == 0
    assert client.get("/api/auth/me").headers["cache-control"] == "no-store"


def test_logout_csrf_origin_and_revocation(client):
    data, _ = login(client)
    token = client.cookies.get(SESSION_COOKIE)
    origin = client.app.state.settings.origin
    assert client.post("/api/auth/logout").json()["error"]["code"] == "origin_not_allowed"
    assert client.post("/api/auth/logout", headers={"Origin": origin}).json()["error"]["code"] == "csrf_invalid"
    assert client.get("/api/auth/me").status_code == 200
    result = client.post("/api/auth/logout", headers={"Origin": origin, "X-CSRF-Token": data["csrfToken"]})
    assert result.status_code == 204 and result.content == b""
    client.cookies.set(SESSION_COOKIE, token, path="/api")
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/logout", headers={"Origin": origin}).status_code == 204


def test_state_binding_expiry_and_reuse(client, admin):
    callback = provider_callback(client)
    binding = client.cookies.get(TRANSACTION_COOKIE)
    client.cookies.clear()
    assert "invalid_state" in client.get(callback).headers["location"]
    client.cookies.set(TRANSACTION_COOKIE, binding, path="/api/auth/github")
    with admin.begin() as conn:
        conn.execute(text("UPDATE auth.oauth_transactions SET expires_at = now() - interval '1 second'"))
    assert "auth_error=expired" in client.get(callback).headers["location"]
    assert "invalid_state" in client.get(callback).headers["location"]
    _, callback = login(client)
    client.cookies.set(TRANSACTION_COOKIE, binding, path="/api/auth/github")
    assert "invalid_state" in client.get(callback).headers["location"]


@pytest.mark.parametrize("case,code", [("denied", "access_denied"), ("token_error", "github_error"), ("user_error", "github_error")])
def test_provider_failure_does_not_create_session(client, admin, case, code):
    callback = provider_callback(client, case=case)
    response = client.get(callback)
    assert response.status_code == 303 and response.headers["location"] == "/auth/login/?auth_error=" + code
    with admin.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM auth.accounts")).scalar_one() == 0
        assert conn.execute(text("SELECT count(*) FROM auth.sessions")).scalar_one() == 0


def test_changed_login_does_not_merge_or_reset_permissions(client, admin):
    original, _ = login(client)
    change_permissions(admin.url.render_as_string(hide_password=False), "123456", True, "editor", "operator:test", "verified identity")
    assert client.get("/api/auth/me").status_code == 401
    updated, _ = login(client, login="renamed")
    assert updated["user"]["accountId"] == original["user"]["accountId"]
    assert updated["user"]["isApproved"] is True and updated["user"]["role"] == "editor"
    distinct, _ = login(client, github_id="654321", login="renamed")
    assert distinct["user"]["accountId"] != original["user"]["accountId"]
    assert distinct["user"]["isApproved"] is False
    change_permissions(admin.url.render_as_string(hide_password=False), "654321", False, "commenter", "operator:test", "revoke")
    assert client.get("/api/auth/me").status_code == 401
    with admin.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM auth.permission_audit")).scalar_one() == 2


def test_application_database_cannot_escalate_permissions(client):
    login(client)
    with pytest.raises(DBAPIError) as denied:
        with client.app.state.engine.begin() as conn:
            conn.execute(text("UPDATE auth.accounts SET role='admin', is_approved=true"))
    assert denied.value.orig.sqlstate == "42501"
    with pytest.raises(DBAPIError) as denied:
        with client.app.state.engine.begin() as conn:
            conn.execute(text("DELETE FROM auth.permission_audit"))
    assert denied.value.orig.sqlstate == "42501"
    assert client.get("/api/auth/me").json()["user"]["isApproved"] is False


def test_database_outage_is_not_logout_or_healthy(client, monkeypatch):
    login(client)
    from sqlalchemy.exc import OperationalError
    def unavailable(*args, **kwargs):
        raise OperationalError("redacted", None, Exception("unavailable"))
    monkeypatch.setattr(client.app.state.engine, "connect", unavailable)
    assert client.get("/api/auth/me").status_code == 503
    assert client.get("/readyz").status_code == 503
    assert client.get("/healthz").status_code == 200


def test_routes_metadata_and_head(client):
    assert client.get("/api/health").status_code == 200
    assert len(client.get("/api/version").json()["sha"]) == 40
    for path in ("/api", "/api/nope", "/api/health/", "/docs", "/openapi.json"):
        assert client.get(path).status_code == 404
    assert client.head("/api/health").status_code == 405
    assert client.head("/api/health").content == b""


def test_concurrent_callback_is_single_use(client, admin):
    callback = provider_callback(client)
    binding = client.cookies.get(TRANSACTION_COOKIE)
    def redeem():
        with TestClient(create_app(), base_url=client.app.state.settings.origin, follow_redirects=False) as other:
            other.cookies.set(TRANSACTION_COOKIE, binding, path="/api/auth/github")
            return other.get(callback).headers["location"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        locations = list(pool.map(lambda _: redeem(), range(2)))
    assert sorted(locations) == ["/", "/auth/login/?auth_error=invalid_state"]
    with admin.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM auth.sessions")).scalar_one() == 1


def test_expired_session(client, admin):
    login(client)
    with admin.begin() as conn:
        conn.execute(text("UPDATE auth.sessions SET expires_at = now() - interval '1 second'"))
    assert client.get("/api/auth/me").status_code == 401


def test_public_http_and_mock_mode_rejected(monkeypatch):
    monkeypatch.setenv("AUTH_ORIGIN", "http://m2.invalid")
    with pytest.raises(ValueError):
        Settings.load()
    monkeypatch.setenv("AUTH_ORIGIN", "http://127.0.0.1:28080")
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(ValueError):
        Settings.load()


def test_disabled_login_redirects_to_web_error(client):
    disabled = create_app(replace(client.app.state.settings, oauth_mode="disabled"))
    with TestClient(disabled, follow_redirects=False) as other:
        response = other.get("/api/auth/github/start")
        assert response.status_code == 303
        assert response.headers["location"] == "/auth/login/?auth_error=server_error"


def test_https_cookie_and_session_policy(client):
    secure = create_app(replace(client.app.state.settings, origin="https://m2.invalid", secure_cookie=True))
    with TestClient(secure, base_url="https://m2.invalid", follow_redirects=False) as other:
        response = other.get("/api/auth/github/start")
        assert "Secure" in response.headers["set-cookie"]
        assert "HttpOnly" in response.headers["set-cookie"]
        assert "SameSite=lax" in response.headers["set-cookie"]


def test_real_auth_does_not_accept_public_http_or_spoofed_proxy(client):
    settings = replace(client.app.state.settings, oauth_mode="github", origin="https://m2.invalid", secure_cookie=True)
    with TestClient(create_app(settings), base_url="http://m2.invalid", follow_redirects=False) as other:
        denied = other.get("/api/auth/github/start", headers={"X-Forwarded-Proto": "https"})
        assert denied.status_code == 303 and "auth_error=server_error" in denied.headers["location"]
        assert "anxious_oauth=" not in denied.headers.get("set-cookie", "") or "Max-Age=0" in denied.headers["set-cookie"]


def test_real_callback_wrong_host_never_issues_session(client):
    settings = replace(client.app.state.settings, oauth_mode="github", origin="https://m2.invalid", secure_cookie=True)
    with TestClient(create_app(settings), base_url="https://other.invalid", follow_redirects=False) as other:
        denied = other.get("/api/auth/github/callback?state=forged&code=forged")
        assert denied.status_code == 303 and "server_error" in denied.headers["location"]
        assert "anxious_session=" not in denied.headers.get("set-cookie", "")


@pytest.mark.parametrize("host", ["m2.invalid/extra", "m2.invalid?query", "user@m2.invalid"])
def test_malformed_auth_host_rejected(client, host):
    settings = replace(client.app.state.settings, oauth_mode="github", origin="https://m2.invalid", secure_cookie=True)
    with TestClient(create_app(settings), base_url="https://m2.invalid", follow_redirects=False) as other:
        response = other.get("/api/auth/github/start", headers={"Host": host})
        assert response.status_code == 303 and "server_error" in response.headers["location"]


def test_private_secret_files_override_environment_for_login(client, tmp_path, monkeypatch):
    for name in ("DATABASE_URL", "GITHUB_CLIENT_SECRET", "AUTH_TRANSACTION_KEY"):
        value = os.environ[name]
        file = tmp_path / name.lower()
        file.write_text(value)
        file.chmod(0o400)
        monkeypatch.setenv(name + "_FILE", str(file))
        monkeypatch.setenv(name, "wrong-environment-value")
    with TestClient(create_app(), base_url=client.app.state.settings.origin, follow_redirects=False) as other:
        data, _ = login(other)
        assert data["user"]["isApproved"] is False
