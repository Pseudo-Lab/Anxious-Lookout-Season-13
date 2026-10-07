from dataclasses import replace
from urllib.parse import parse_qs, urlsplit
import base64
import hashlib
import httpx

import pytest
from fastapi.testclient import TestClient

from app.main import SESSION_COOKIE, create_app
from app.settings import Settings

PUBLIC_ORIGIN = "http://93.184.216.34"


def test_public_ip_requires_exact_opt_in(monkeypatch):
    monkeypatch.setenv("AUTH_ORIGIN", PUBLIC_ORIGIN)
    with pytest.raises(ValueError):
        Settings.load()
    monkeypatch.setenv("ALLOW_PUBLIC_IP_HTTP", "true")
    settings = Settings.load()
    assert settings.secure_cookie is False
    assert settings.callback_url == PUBLIC_ORIGIN + "/api/auth/github/callback"
    for invalid in ("http://93.184.216.34:80", "http://93.184.216.34:08081", "http://m2.invalid", "http://10.0.0.1", "http://169.254.169.254", "http://203.0.113.10"):
        monkeypatch.setenv("AUTH_ORIGIN", invalid)
        with pytest.raises(ValueError):
            Settings.load()


def test_public_ip_nonsecure_cookie_and_wrong_origin(client):
    settings = replace(client.app.state.settings, origin=PUBLIC_ORIGIN, secure_cookie=False)
    with TestClient(create_app(settings), base_url=PUBLIC_ORIGIN, follow_redirects=False) as other:
        start = other.get("/api/auth/github/start")
        assert start.status_code == 302
        assert "Secure" not in start.headers["set-cookie"]
        assert "HttpOnly" in start.headers["set-cookie"] and "SameSite=lax" in start.headers["set-cookie"]
        query = parse_qs(urlsplit(start.headers["location"]).query)
        assert query["redirect_uri"] == [PUBLIC_ORIGIN + "/api/auth/github/callback"]


def test_https_transition_invalidates_http_origin_session(client):
    from tests.test_auth import login
    login(client)
    token = client.cookies.get(SESSION_COOKIE)
    https = replace(client.app.state.settings, origin="https://m2.example", secure_cookie=True)
    with TestClient(create_app(https), base_url="https://m2.example") as other:
        other.cookies.set(SESSION_COOKIE, token, path="/api")
        assert other.get("/api/auth/me").status_code == 401


def test_real_mode_public_http_mock_transport_roundtrip(client, monkeypatch):
    monkeypatch.setenv("AUTH_ORIGIN", PUBLIC_ORIGIN)
    monkeypatch.setenv("ALLOW_PUBLIC_IP_HTTP", "true")
    monkeypatch.setenv("OAUTH_MODE", "github")
    for name in ("GITHUB_AUTHORIZE_URL", "GITHUB_TOKEN_URL", "GITHUB_USER_URL"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings.load()
    expected = {}
    def provider(request):
        if request.url.path == "/login/oauth/access_token":
            form = parse_qs(request.content.decode())
            verifier = form["code_verifier"][0]
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            assert challenge == expected["challenge"]
            assert form["redirect_uri"] == [PUBLIC_ORIGIN + "/api/auth/github/callback"]
            return httpx.Response(200, json={"access_token": "disposable-provider-token"})
        return httpx.Response(200, json={"id": 444444, "login": "public-ip-fixture", "site_admin": True})
    real_client = httpx.Client
    monkeypatch.setattr("app.main.httpx.Client", lambda **kwargs: real_client(transport=httpx.MockTransport(provider), **kwargs))
    with TestClient(create_app(settings), base_url=PUBLIC_ORIGIN, follow_redirects=False) as browser:
        start = browser.get("/api/auth/github/start")
        query = parse_qs(urlsplit(start.headers["location"]).query)
        expected["challenge"] = query["code_challenge"][0]
        response = browser.get("/api/auth/github/callback", params={"state": query["state"][0], "code": "disposable-code"})
        assert response.status_code == 303 and response.headers["location"] == "/"
        assert "Secure" not in response.headers["set-cookie"]
        me = browser.get("/api/auth/me").json()
        assert me["user"]["role"] == "commenter" and me["user"]["isApproved"] is False
        wrong = browser.post("/api/auth/logout", headers={"Origin": "http://93.184.216.35", "X-CSRF-Token": me["csrfToken"]})
        assert wrong.status_code == 403 and wrong.json()["error"]["code"] == "origin_not_allowed"
        result = browser.post("/api/auth/logout", headers={"Origin": PUBLIC_ORIGIN, "X-CSRF-Token": me["csrfToken"]})
        assert result.status_code == 204
        assert browser.get("/api/auth/me").status_code == 401
