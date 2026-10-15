"""Pinned Traefik TLS/path/redirect/ASGI counterexamples; synthetic inputs only."""
import http.client
import json
import ssl
import sys
import time
from pathlib import Path

HOST = "93.184.216.34"
context = ssl.create_default_context(cafile="/fixture/cert.pem")


def request(path, method="GET", tls=True, headers=None):
    client = (http.client.HTTPSConnection("127.0.0.1", 8443, context=context, timeout=3)
              if tls else http.client.HTTPConnection("127.0.0.1", 8000, timeout=3))
    try:
        client.request(method, path, headers={"Host": HOST, **(headers or {})})
        response = client.getresponse()
        return response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
    finally:
        client.close()


def count():
    log = Path("/tmp/root-upstream.jsonl")
    return len(log.read_text().splitlines()) if log.exists() else 0


deadline = time.monotonic() + 20
while True:
    try:
        if request("/version.json" if "--tls-only" in sys.argv else "/")[0] == 200:
            break
    except (OSError, http.client.HTTPException):
        pass
    if time.monotonic() >= deadline:
        raise RuntimeError("Synthetic root TLS proxy not ready")
    time.sleep(.1)

passed = 0
if "--tls-only" in sys.argv:
    for method in ("GET", "HEAD"):
        before = count()
        status, _, body = request("/version.json", method)
        assert status == 200 and count() == before + 1
        if method == "GET":
            assert json.loads(body)["port"] == 8082
        passed += 1
    for method, path in (("POST", "/version.json"), ("GET", "/"), ("GET", "/research/"),
        ("GET", "/version.json/child"), ("GET", "/api/version"),
        ("GET", "/api/auth/github/start"), ("GET", "/api/auth/github/callback?state=x&code=x")):
        before = count()
        assert request(path, method)[0] == 404 and count() == before
        passed += 1
    for path in ("/", "/api/version", "/android-agent/", "/.well-known/acme-challenge/synthetic"):
        status, _, body = request(path, tls=False)
        assert status == 200 and json.loads(body)["port"] == 8083
        passed += 1
    for path in ("/android-agent/", "/.well-known/acme-challenge/synthetic"):
        status, _, body = request(path)
        assert status == 200 and json.loads(body)["port"] == 8083
        passed += 1
    print(f"PASS TLS-version-only probe: {passed} cases; original HTTP/control routes retained, no API/UI admission")
    sys.exit(0)
for path, port in (("/", 8082), ("/research/", 8082), ("/version.json", 8082),
    ("/_next/static/synthetic.js", 8082), ("/api", 8081), ("/api/version", 8081),
    ("/api/research/sessions?q=synthetic%2F%25%00%3B%3F%23%5C", 8081), ("/apis", 8082)):
    before = count()
    status, _, body = request(path)
    result = json.loads(body)
    assert status == 200 and result["port"] == port and result["proto"] == "https"
    assert result["path"] == path.split("?", 1)[0] and count() == before + 1
    passed += 1
for extra in ({}, {"X-Forwarded-Proto": "http", "X-Forwarded-Host": "other.invalid"}):
    before = count()
    status, headers, _ = request("/api/auth/github/callback?state=synthetic%2F%25&code=synthetic%2500", headers=extra)
    assert status == 303 and headers["location"] == "/auth/login/?auth_error=invalid_state"
    assert "Secure" in headers["set-cookie"] and "Path=/api/auth/github" in headers["set-cookie"]
    assert count() == before + 1
    passed += 1
plain = ["/api/internal", "/api/internal/research/tools", "/api/%69nternal/research/tools",
         "/_fixture", "/_fixture/github/start", "/%5ffixture/github/start"]
encoded = ["/api/internal%2Fresearch/tools", "/api/internal%2fresearch%2ftools",
    "/api%2Finternal/research/tools", "/api/internal%252Fresearch/tools",
    "/api/internal%5Cresearch/tools", "/api/internal%00/research/tools",
    "/api/internal%3Bresearch/tools", "/api/internal%3Fresearch/tools",
    "/api/internal%23research/tools", "/_fixture%2Fgithub/start", "/_fixture%252Fgithub/start"]
for method in ("GET", "POST"):
    for path in plain + encoded:
        before = count()
        status, _, _ = request(path, method)
        assert status in ({404} if path in plain else {400, 404}) and count() == before, (method, path, status)
        passed += 1
for tls in (False, True):
    for path in ("/android-agent", "/android-agent/", "/android-agent/connection-api",
                 "/.well-known/acme-challenge/synthetic"):
        status, _, body = request(path, tls=tls)
        assert status == 200 and json.loads(body)["port"] == 8083
        passed += 1
for method in ("GET", "HEAD"):
    before = count()
    status, headers, _ = request("/research/?synthetic=1", method, tls=False)
    # Both observed temporary responses are safe for the admitted GET/HEAD only.
    # Permanent redirects and any upstream delivery remain forbidden.
    assert status in {302, 307} and headers["location"] == "https://" + HOST + "/research/?synthetic=1", (method, status, headers)
    assert count() == before  # Redirect middleware cannot deliver HTTP root/API content.
    passed += 1
for method, path in (("POST", "/"), ("POST", "/research/"), ("GET", "/api"),
                     ("GET", "/api/auth/github/start"), ("GET", "/api/auth/github/callback?state=x&code=x"),
                     ("POST", "/api/research/sessions"), ("GET", "/_fixture/start")):
    before = count()
    assert request(path, method, tls=False)[0] == 404 and count() == before
    passed += 1
before = count()
assert request("/", headers={"Host": "other.invalid"})[0] == 404 and count() == before
passed += 1
print(f"PASS root TLS/path/redirect/baked callback transport: {passed} cases; no DB/native/provider")
