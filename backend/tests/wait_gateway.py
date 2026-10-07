"""Bounded preflight; diagnostics contain only status or exception type."""
import sys
import time

import httpx

deadline = time.monotonic() + 30
last = {}
with httpx.Client(base_url="http://gateway:8080", timeout=1.5, trust_env=False) as client:
    while time.monotonic() < deadline:
        for name, path in (("api", "/api/health"), ("web", "/version.json")):
            try:
                response = client.get(path)
                last[name] = response.status_code
            except httpx.HTTPError as error:
                last[name] = type(error).__name__
        if last == {"api": 200, "web": 200}:
            print("PASS bounded gateway readiness: API and web 200")
            sys.exit(0)
        time.sleep(0.25)
print(f"Gateway readiness deadline exceeded: {last}; inspect owned api/web/db logs", file=sys.stderr)
sys.exit(1)
