"""Network-none synthetic Uvicorn upstreams; no application/storage/auth state."""
import json
import threading
import time
from pathlib import Path

import uvicorn

log = Path("/tmp/proxy-upstream.jsonl")
lock = threading.Lock()


async def app(scope, receive, send):
    if scope["type"] != "http":
        return
    record = {"port": scope["server"][1], "path": scope["path"],
              "rawPath": scope["raw_path"].decode(), "query": scope["query_string"].decode()}
    with lock, log.open("a") as output:
        output.write(json.dumps(record) + "\n")
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": json.dumps(record).encode()})


for port in (8081, 8082):
    threading.Thread(target=uvicorn.run, args=(app,), kwargs={"host": "127.0.0.1", "port": port,
        "log_level": "error", "access_log": False, "lifespan": "off"}, daemon=True).start()
time.sleep(300)  # Bound orphan lifetime; orchestration always removes the containers.
