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
    for host in ("93.184.216.35", "m2.invalid"):
        assert client.get("/", headers={"Host": host}).status_code == 404
        assert client.get("/api/health", headers={"Host": host}).status_code == 404
        assert client.get("/android-agent/assets/file", headers={"Host": host}).json()["service"] == "android"
print("PASS real Traefik exact IP Host, API boundary, hostless-root denial and existing android route coexistence; port admission remains API/entrypoint responsibility")
