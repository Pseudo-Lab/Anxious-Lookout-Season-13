"""Synthetic ASGI counters plus actual API callback transport with no DB access."""
import json
import threading
import time
from pathlib import Path

import uvicorn
from cryptography.fernet import Fernet
from app.main import create_app
from app.settings import Settings

log = Path("/tmp/trial-upstream.jsonl")
lock = threading.Lock()
api = create_app(Settings(database_url="postgresql+psycopg://synthetic:synthetic@127.0.0.1:9/synthetic",
    origin="https://trial.example.invalid", oauth_mode="github", client_id="synthetic", client_secret="synthetic",
    transaction_key=Fernet.generate_key().decode(), authorize_url="https://github.com/login/oauth/authorize",
    token_url="https://github.com/login/oauth/access_token", user_url="https://api.github.com/user",
    base_path="/codex-trial", trusted_proxy_cidrs=("127.0.0.1/32",)))


async def app(scope, receive, send):
    if scope["type"] != "http":
        return
    headers = {name.decode(): value.decode() for name, value in scope["headers"]}
    record = {"port": scope["server"][1], "path": scope["path"], "rawPath": scope["raw_path"].decode(),
              "query": scope["query_string"].decode(), "proto": headers.get("x-forwarded-proto"),
              "host": headers.get("host")}
    with lock, log.open("a") as output:
        output.write(json.dumps(record) + "\n")
    if record["port"] == 8081 and scope["path"] == "/codex-trial/api/auth/github/callback":
        await api(scope, receive, send)  # Missing transaction cookie stops before DB/provider.
        return
    if record["port"] == 8082 and scope["path"] == "/codex-trial":
        await send({"type": "http.response.start", "status": 308, "headers": [(b"location", b"/codex-trial/")]})
        await send({"type": "http.response.body", "body": b""})
        return
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": json.dumps(record).encode()})


for port in (8081, 8082, 8083):
    threading.Thread(target=uvicorn.run, args=(app,), kwargs={"host": "127.0.0.1", "port": port,
        "log_level": "error", "access_log": False, "lifespan": "off", "proxy_headers": False}, daemon=True).start()
time.sleep(300)  # Bounded fixture lifetime; orchestration also removes on EXIT.
