"""New remote opt-in and prefixed auth over the real API/DB, synthetic provider."""
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from app.codex_policy import personal_origin_enabled
from app.main import create_app, SESSION_COOKIE

ORIGIN = "https://trial.example.invalid"
BASE = "/codex-trial"


def opt_in(monkeypatch):
    for key, value in {"APP_ENV": "personal-test", "CODEX_PERSONAL_ENABLE": "true",
                       "CODEX_EXECUTION_SCOPE": "personal-private", "CODEX_PERSONAL_REMOTE_ORIGIN": ORIGIN}.items():
        monkeypatch.setenv(key, value)


def settings(client):
    return replace(client.app.state.settings, origin=ORIGIN, base_path=BASE, secure_cookie=True,
                   oauth_mode="github", trusted_proxy_cidrs=("10.42.0.0/16",),
                   authorize_url="https://github.com/login/oauth/authorize", token_url="https://github.com/login/oauth/access_token",
                   user_url="https://api.github.com/user")


@pytest.mark.parametrize("key,value", [("APP_ENV", "production"), ("CODEX_PERSONAL_ENABLE", "false"),
    ("CODEX_EXECUTION_SCOPE", "hosted"), ("CODEX_PERSONAL_REMOTE_ORIGIN", ""),
    ("CODEX_PERSONAL_REMOTE_ORIGIN", "https://other.invalid"), ("CODEX_PERSONAL_REMOTE_ORIGIN", ORIGIN + "/codex-trial")])
def test_remote_opt_in_requires_exact_private_context(client, monkeypatch, key, value):
    opt_in(monkeypatch)
    value_settings = settings(client)
    assert personal_origin_enabled(value_settings) is True
    monkeypatch.setenv(key, value)
    assert personal_origin_enabled(value_settings) is False


@pytest.mark.parametrize("change", [{"origin": "http://trial.example.invalid"}, {"base_path": ""},
    {"secure_cookie": False}, {"trusted_proxy_cidrs": ()}])
def test_remote_context_still_rejects_unsafe_transport(client, monkeypatch, change):
    opt_in(monkeypatch)
    assert personal_origin_enabled(replace(settings(client), **change)) is False


def test_existing_loopback_enable_remains_explicit(client, monkeypatch):
    opt_in(monkeypatch)
    monkeypatch.delenv("CODEX_PERSONAL_REMOTE_ORIGIN")
    assert personal_origin_enabled(client.app.state.settings) is True
    monkeypatch.setenv("CODEX_PERSONAL_ENABLE", "false")
    assert personal_origin_enabled(client.app.state.settings) is False


def test_prefixed_github_success_cookie_csrf_and_failure(client, monkeypatch):
    def provider(request):
        if request.url.path == "/login/oauth/access_token":
            assert parse_qs(request.content.decode())["redirect_uri"] == [ORIGIN + BASE + "/api/auth/github/callback"]
            return httpx.Response(200, json={"access_token": "disposable-trial-token"})
        return httpx.Response(200, json={"id": 770077, "login": "synthetic-trial"})
    real_client = httpx.Client
    monkeypatch.setattr("app.main.httpx.Client", lambda **kwargs: real_client(transport=httpx.MockTransport(provider), **kwargs))
    with TestClient(create_app(settings(client)), base_url=ORIGIN, follow_redirects=False) as browser:
        start = browser.get(BASE + "/api/auth/github/start")
        assert start.status_code == 302
        query = parse_qs(urlsplit(start.headers["location"]).query)
        assert query["redirect_uri"] == [ORIGIN + BASE + "/api/auth/github/callback"]
        assert "Secure" in start.headers["set-cookie"] and "Path=/codex-trial/api/auth/github" in start.headers["set-cookie"]
        callback = browser.get(BASE + "/api/auth/github/callback", params={"state": query["state"][0], "code": "disposable-code"})
        assert callback.status_code == 303 and callback.headers["location"] == BASE + "/"
        cookie = next(c for c in callback.headers.get_list("set-cookie") if c.startswith(SESSION_COOKIE + "="))
        assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/codex-trial/api;" in cookie
        me = browser.get(BASE + "/api/auth/me").json()
        assert me["user"]["role"] == "commenter" and me["user"]["isApproved"] is False
        assert browser.get("/api/auth/me").status_code == 404
        assert browser.post(BASE + "/api/auth/logout", headers={"Origin": "https://other.invalid"}).status_code == 403
        assert browser.post(BASE + "/api/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": "wrong"}).status_code == 403
        assert browser.post(BASE + "/api/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": me["csrfToken"]}).status_code == 204
        failed = browser.get(BASE + "/api/auth/github/callback?state=forged&code=forged")
        assert failed.status_code == 303 and failed.headers["location"] == BASE + "/auth/login/?auth_error=invalid_state"


@pytest.mark.parametrize("peer,proto,host,expected", [("10.42.0.11", "https", "trial.example.invalid", "invalid_state"),
    ("10.42.0.11", "http", "trial.example.invalid", "server_error"),
    ("93.184.216.34", "https", "trial.example.invalid", "server_error"),
    ("10.42.0.11", "https", "other.invalid", "server_error")])
def test_prefixed_http_upstream_trusts_only_selected_peer(client, peer, proto, host, expected):
    with TestClient(create_app(settings(client)), base_url="http://trial.example.invalid", client=(peer, 9000), follow_redirects=False) as browser:
        result = browser.get(BASE + "/api/auth/github/callback?state=forged&code=forged", headers={"X-Forwarded-Proto": proto, "Host": host})
        assert result.status_code == 303 and result.headers["location"] == BASE + "/auth/login/?auth_error=" + expected
        assert SESSION_COOKIE + "=" not in result.headers.get("set-cookie", "")
