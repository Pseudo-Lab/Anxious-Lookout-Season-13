"""Evaluate rendered M2 rules with Traefik's real parser on an internal network."""
import time

import httpx

with httpx.Client(base_url="http://gateway:8080", timeout=3, trust_env=False) as client:
    deadline = time.monotonic() + 30
    while True:
        try:
            if client.get("/", headers={"Host": "93.184.216.34"}).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        if time.monotonic() > deadline:
            raise RuntimeError("Fixture gateway did not become ready")
        time.sleep(0.2)
    for host in ("93.184.216.34", "93.184.216.34:8081"):
        for path, service in (("/", "web"), ("/apis", "web"), ("/api", "api"), ("/api/health", "api"), ("/android-agent", "android"), ("/android-agent/assets/file", "android")):
            response = client.get(path, headers={"Host": host})
            assert response.status_code == 200, (host, path, response.status_code)
            assert response.json() == {"service": service, "path": path}
        # #62: the runner callback prefix is never routed to the API. Traefik matches encoded
        # separators undecoded, so each of these must still find no router (Traefik's 404).
        for path in ("/api/internal/research/tools", "/api/internal/", "/api/internal", "/api/%69nternal/research/tools",
                     "/api/internal%2Fresearch/tools", "/api/internal%2fresearch%2ftools", "/api/internal%5Cresearch/tools",
                     "/api/internal%3Bresearch/tools", "/api/internal%00/research/tools", "/api/internal%252Fresearch/tools",
                     "/api/internal;x/research/tools", "/api//internal/research/tools", "/api/./internal/research/tools",
                     "/api/x/../internal/research/tools", "/api/%2E/internal/research/tools", "/api/internalx"):
            response = client.post(path, headers={"Host": host})
            # Traefik's own 404 (no router), not an upstream's: a routed POST would be the fixture's 501.
            assert response.status_code == 404 and response.text.strip() == "404 page not found", \
                (host, path, response.status_code)
        # An encoded slash before "internal" is not under /api/ for Traefik: it goes to web, which has
        # no API proxy (and the API would not route it either). Never to the API.
        response = client.get("/api%2Finternal/research/tools", headers={"Host": host})
        assert response.status_code == 200 and response.json()["service"] == "web", (host, response.status_code)
        assert client.get("/api/interna", headers={"Host": host}).json() == {"service": "api", "path": "/api/interna"}
    # #62 second layer: the test-only leak.invalid route forwards everything to the real public-
    # listener gate (tests/route_gate_fixture.py) with the runner switch flag ON. Traefik's own proxy
    # headers and uvicorn's decoding must still keep every form of the callback out.
    gate_404 = {"error": {"code": "not_found", "message": "Not found"}}
    for path in ("/api/internal/research/tools", "/api/internal", "/api/%69nternal/research/tools",
                 "/api/internal%2Fresearch/tools", "/api/internal%2fresearch%2ftools", "/api%2Finternal%2Fresearch%2Ftools",
                 "/api/internal%5Cresearch/tools", "/api/internal%3Bresearch/tools", "/api/internal%00/research/tools",
                 "/api/internal%252Fresearch/tools", "/api//internal/research/tools", "/api/./internal/research/tools"):
        response = client.post(path, headers={"Host": "leak.invalid"}, content=b"{}")
        assert response.status_code == 404 and response.json() == gate_404, (path, response.status_code, response.text)
        assert response.headers.get("cache-control") == "no-store"
    response = client.get("/api/research/sessions", headers={"Host": "leak.invalid"})
    assert response.json() == {"service": "gated-api", "path": "/api/research/sessions"}
    for host in ("93.184.216.35", "m2.invalid"):
        assert client.get("/", headers={"Host": host}).status_code == 404
        assert client.get("/api/health", headers={"Host": host}).status_code == 404
        assert client.get("/android-agent/assets/file", headers={"Host": host}).json()["service"] == "android"
print("PASS real Traefik exact IP Host, API boundary, hostless-root denial and existing android route coexistence; "
      "#62 /api/internal closed at Traefik (encoded forms included) and at the real gate behind Traefik with the switch flag on; "
      "port admission remains API/entrypoint responsibility")
