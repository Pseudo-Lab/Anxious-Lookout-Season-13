"""Actual TLS/Traefik/ASGI requests; no host ports, cluster, provider or secrets."""
import http.client
import json
import ssl
import sys
import time
from pathlib import Path

context = ssl.create_default_context(cafile="/fixture/cert.pem")


def request(path, method="GET", *, tls=True, source="127.0.0.1", headers=None):
    options = {"timeout": 3, "source_address": (source, 0)}
    if tls:
        client = http.client.HTTPSConnection("127.0.0.1", 8443, context=context, **options)
    else:
        client = http.client.HTTPConnection("127.0.0.1", 8000, **options)
    try:
        client.request(method, path, headers={"Host": "trial.example.invalid", **(headers or {})})
        response = client.getresponse()
        return response.status, {name.lower(): value for name, value in response.getheaders()}, response.read()
    finally:
        client.close()


def count():
    log = Path("/tmp/trial-upstream.jsonl")
    return len(log.read_text().splitlines()) if log.exists() else 0


deadline = time.monotonic() + 20
while True:
    try:
        if request("/codex-trial/version.json")[0] == 200:
            break
    except (OSError, http.client.HTTPException):
        pass
    if time.monotonic() >= deadline:
        raise RuntimeError("Synthetic TLS proxy not ready")
    time.sleep(0.1)

passed = 0
if "--access-only" in sys.argv:
    for source, expected in (("127.0.0.1", 200), ("127.0.0.2", 403)):
        for forged in ({}, {"X-Forwarded-For": "127.0.0.1"}, {"X-Real-IP": "127.0.0.1"}):
            before = count()
            status, _, body = request("/codex-trial/version.json", source=source, headers=forged)
            assert status == expected and count() == before + (1 if expected == 200 else 0)
            if expected == 200:
                assert json.loads(body)["port"] == 8082
            passed += 1
    for method in ("GET", "POST"):
        for path in ("/codex-trial", "/codex-trial/", "/codex-trial/api/version", "/codex-trial/api/auth/github/start",
                     "/codex-trial/api/auth/github/callback", "/codex-trial/apis", "/codex-trial/_next/static/mock.js",
                     "/codex-trial/version.json/other"):
            before = count()
            assert request(path, method)[0] == 404 and count() == before
            passed += 1
    print(f"Restricted version-only TLS access probe: {passed} cases passed; no API/UI/provider route exposed")
    sys.exit(0)
for path, port in [("/codex-trial/", 8082), ("/codex-trial/api", 8081),
    ("/codex-trial/api/version", 8081), ("/codex-trial/api/research/sessions", 8081),
    ("/codex-trial/apis", 8082), ("/codex-trial/_next/static/synthetic.js", 8082),
    ("/codex-trial/version.json", 8082), ("/codex-trial/research/", 8082),
    ("/codex-trial/api/materials?query=synthetic%2F%25%00%3B%3F%23%5C", 8081)]:
    before = count()
    status, _, body = request(path)
    record = json.loads(body)
    assert status == 200 and record["port"] == port and record["path"] == path.split("?", 1)[0] and count() == before + 1, (path, status, record)
    if "?" in path:
        assert record["query"] == path.split("?", 1)[1]
    assert record["proto"] == "https" and record["host"] == "trial.example.invalid"
    passed += 1
before = count()
status, headers, _ = request("/codex-trial")
assert status == 308 and headers["location"] == "/codex-trial/" and count() == before + 1
passed += 1  # Redirect is mock Next behavior; front60/162 proves actual Next separately.
for extra_headers in ({}, {"X-Forwarded-Proto": "http", "X-Forwarded-Host": "other.invalid"}):
    query = "state=synthetic%2F%25%00%3B%3F%23%5C&code=synthetic%2500"
    before = count()
    status, headers, _ = request("/codex-trial/api/auth/github/callback?" + query, headers=extra_headers)
    assert status == 303 and headers["location"] == "/codex-trial/auth/login/?auth_error=invalid_state", (status, headers)
    assert "Secure" in headers["set-cookie"] and "Path=/codex-trial/api/auth/github" in headers["set-cookie"] and count() == before + 1
    passed += 1

plain = ["/", "/api", "/api/auth/github/callback", "/codex-trials/", "/codex-trial-other/",
         "/codex-trial/api/internal", "/codex-trial/api/internal/research/tools",
         "/codex-trial/api/%69nternal/research/tools", "/codex-trial/_fixture", "/codex-trial/_fixture/github/authorize"]
encoded = ["/codex-trial/api/internal%2Fresearch/tools", "/codex-trial/api/internal%2fresearch%2ftools",
    "/codex-trial/api%2Finternal/research/tools", "/codex-trial/api/%69nternal%2Fresearch/tools",
    "/codex-trial/api/internal%252Fresearch/tools", "/codex-trial/api/internal%5Cresearch/tools",
    "/codex-trial/api/internal%5cresearch/tools", "/codex-trial/api/internal%00/research/tools",
    "/codex-trial/api/internal%3Bresearch/tools", "/codex-trial/api/internal%3bresearch/tools",
    "/codex-trial/api/internal%3Fresearch/tools", "/codex-trial/api/internal%23research/tools",
    "/codex-trial/_fixture%2Fgithub/authorize", "/codex-trial/_fixture%252Fgithub/authorize",
    "/codex-trial%2Fapi/internal/research/tools", "/codex-trial%252Fapi/internal/research/tools",
    "/%63odex-trial/api/internal%2Fresearch/tools"]
for method in ("GET", "POST"):
    for path in plain + encoded:
        before = count()
        status, _, body = request(path, method)
        # Encoded separator in the prefix can miss trial routers entirely:404.
        # Once a trial route matches, its middleware returns400. Both stop upstream.
        assert status in ({404} if path in plain else {400, 404}) and count() == before, (method, path, status, body)
        passed += 1
for path in ("/codex-trial/", "/codex-trial/api/auth/github/callback?state=forged&code=forged"):
    for forged in ({}, {"X-Forwarded-For": "127.0.0.1"}, {"X-Real-IP": "127.0.0.1"}):
        before = count()
        status, _, _ = request(path, source="127.0.0.2", headers=forged)
        assert status == 403 and count() == before
        passed += 1
before = count()
assert request("/codex-trial/api/version", headers={"Host": "other.invalid"})[0] == 404 and count() == before
passed += 1
# Existing topology controls deliberately allow encoded separators: trial local
# rejection must not alter shared entrypoint behavior or their upstream paths.
for tls, path in [(False, "/"), (False, "/api/version"), (False, "/api/internal%2Fresearch/tools"),
                  (True, "/android-agent/"), (True, "/android-agent/file%2Fdownload")]:
    before = count()
    status, _, body = request(path, tls=tls)
    record = json.loads(body)
    assert status == 200 and record["port"] == 8083 and count() == before + 1, (path, status, record)
    passed += 1
print(f"Exact digest TLS proxy / route-local middleware / ASGI: {passed} cases passed; blocked upstreamCalls=0")
