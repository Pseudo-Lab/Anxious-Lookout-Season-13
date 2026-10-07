"""Real Next runtime + Traefik + FastAPI + mock provider over the private test net."""
import re
from urllib.parse import urlsplit

import httpx

with httpx.Client(base_url="http://gateway:8080", follow_redirects=False, trust_env=False, timeout=10) as browser:
    for path in ("/", "/auth/login/", "/status/", "/posts/", "/write/", "/admin/"):
        assert browser.get(path).status_code == 200, path
    html = browser.get("/").text
    assets = set(re.findall(r'(?:src|href)="(/_next/static/[^" ]+)"', html))
    assert assets
    for asset in assets:
        response = browser.get(asset.replace("&amp;", "&"))
        assert response.status_code == 200, asset
        assert "immutable" in response.headers.get("cache-control", "")
    assert browser.get("/version.json").headers["cache-control"] == "no-store"
    assert browser.get("/api/health").status_code == 200
    assert browser.get("/api/unknown").status_code == 404
    assert browser.get("/api/unknown").headers["content-type"].startswith("application/json")
    for path in ("/apis", "/api-docs", "/missing-web-page"):
        response = browser.get(path, follow_redirects=True)
        assert response.status_code == 404 and "text/html" in response.headers["content-type"], path
    assert browser.get("/api/auth/me").status_code == 401
    start = browser.get("/api/auth/github/start")
    with httpx.Client(trust_env=False) as provider:
        callback = provider.get(start.headers["location"]).headers["location"]
    callback_url = urlsplit(callback)
    assert browser.get(callback_url.path + "?" + callback_url.query).status_code == 303
    me = browser.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["user"]["accountId"] != me.json()["user"]["githubId"]
    csrf = me.json()["csrfToken"]
    response = browser.post("/api/auth/logout", headers={"Origin": "http://127.0.0.1:8080", "X-CSRF-Token": csrf})
    assert response.status_code == 204
    assert browser.get("/api/auth/me").status_code == 401
    print(f"PASS real Traefik/Next/FastAPI/PG routing/assets ({len(assets)}), mock login/session/logout")
