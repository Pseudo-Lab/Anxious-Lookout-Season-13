"""Exercise the exact pinned proxy/ASGI decode boundary, with upstream counters."""
import http.client
import json
import time
from pathlib import Path


def request(path, method="GET"):
    client = http.client.HTTPConnection("127.0.0.1", 8090, timeout=3)
    try:
        client.request(method, path)
        response = client.getresponse()
        return response.status, response.read()
    finally:
        client.close()


def count():
    log = Path("/tmp/proxy-upstream.jsonl")
    return len(log.read_text().splitlines()) if log.exists() else 0


deadline = time.monotonic() + 15
while True:
    try:
        if request("/")[0] == 200:
            break
    except (OSError, http.client.HTTPException):
        pass
    if time.monotonic() >= deadline:
        raise RuntimeError("Synthetic proxy did not become ready")
    time.sleep(0.1)

callback_query = "state=synthetic%2F%25%3B%3F%23%5C&code=synthetic%2f%2500"
allowed = [("/", 8082), ("/research", 8082), ("/api", 8081), ("/api/auth/github/start", 8081),
           ("/api/auth/github/callback", 8081), ("/api/research/sessions", 8081),
           ("/api/auth/github/callback?" + callback_query, 8081),
           ("/api/research/materials?query=synthetic%2F%25%3B%3F%23%5C", 8081)]
blocked_plain = ["/api/internal/research/tools", "/api/%69nternal/research/tools", "/_fixture/github/authorize"]
blocked_encoded = ["/api/internal%2Fresearch/tools", "/api/internal%2fresearch%2ftools",
    "/api%2Finternal/research/tools", "/api%2finternal%2Fresearch%2ftools",
    "/api/%69nternal%2Fresearch/tools", "/api/internal%252Fresearch/tools",
    "/api/internal%252fresearch%252ftools", "/api/internal%5Cresearch/tools",
    "/api/internal%5cresearch/tools", "/api/internal%00/research/tools",
    "/api/internal%3Bresearch/tools", "/api/internal%3bresearch/tools",
    "/api/internal%3Fresearch/tools", "/api/internal%3fresearch/tools",
    "/api/internal%23research/tools"]
passed = 0
for path, port in allowed:
    before = count()
    status, body = request(path)
    result = json.loads(body) if status == 200 else {}
    assert status == 200 and result.get("port") == port and count() == before + 1, (path, status, result)
    if "?" in path:
        assert result["query"] == path.split("?", 1)[1]
    print(json.dumps({"path": path, "status": status, "upstream": port, "ok": True}), flush=True)
    passed += 1
for path, expected in [(path, 404) for path in blocked_plain] + [(path, 400) for path in blocked_encoded]:
    before = count()
    status, body = request(path)
    assert status == expected and count() == before, (path, status, body)
    print(json.dumps({"path": path, "status": status, "upstreamCalls": 0, "ok": True}), flush=True)
    passed += 1
for path in ("/api/internal/research/tools", "/api/internal%2Fresearch/tools"):
    before = count()
    status, _ = request(path, method="POST")
    assert status == (400 if "%" in path else 404) and count() == before
    passed += 1
print(f"Pinned proxy/ASGI boundary: {passed} cases passed; no external network or actual services", flush=True)
