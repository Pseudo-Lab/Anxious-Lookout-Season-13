"""Private per-account Codex app-server adapter. No DB access or Docker socket."""
import hmac
import hashlib
import fcntl
import json
import os
import queue
import re
import subprocess
import threading
import uuid
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestId: uuid.UUID
    text: str = Field(min_length=1, max_length=50000)
    tools: list[dict]
    toolToken: str = Field(min_length=32, max_length=100)
    context: dict | None = None


def bind_owner(root, owner):
    if root.is_symlink():
        raise RuntimeError("Runner state must be a private volume")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    marker = root / "account-id"
    if marker.exists():
        if marker.is_symlink() or marker.read_text().strip() != owner:
            raise RuntimeError("This state volume belongs to a different account")
        return
    if any(path.name != "lost+found" for path in root.iterdir()):
        raise RuntimeError("Unbound populated state must not be adopted")
    descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as file:
        file.write(owner)
        file.flush()
        os.fsync(file.fileno())


@contextmanager
def volume_lease(root):
    with (root / "runner.lock").open("a+") as lease:
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("The account volume is already being served") from None
        try:
            yield
        finally:
            fcntl.flock(lease, fcntl.LOCK_UN)


class Native:
    def __init__(self, root, model, binary, callback_url, api_key_file=None):
        self.root, self.model, self.callback_url = root, model, callback_url
        self.lock, self.messages, self.next_id = threading.Lock(), queue.Queue(), 1
        self.state_lock = threading.Lock()
        self.completed = {}
        self.active_session = None
        self.mapping_file = root / "broker.json"
        self.mapping = json.loads(self.mapping_file.read_text()) if self.mapping_file.exists() else {}
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        home, codex_home, work = root / "home", root / "codex", root / "workspace"
        for path in (home, codex_home, work):
            path.mkdir(exist_ok=True, mode=0o700)
        environment = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": str(home), "CODEX_HOME": str(codex_home)}
        self.work = work
        self.process = subprocess.Popen([binary, "app-server", "--listen", "stdio://",
                    "-c", 'cli_auth_credentials_store="ephemeral"', "-c", "features.shell_tool=false"],
                    env=environment, cwd=work, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, text=True, bufsize=1)
        threading.Thread(target=self.reader, daemon=True).start()
        try:
            self.rpc("initialize", {"clientInfo": {"name": "anxious_research", "version": "1"}, "capabilities": {"experimentalApi": True}})
            self.send({"method": "initialized"})
            if api_key_file:
                key = Path(api_key_file).read_text().strip()
                self.rpc("account/login/start", {"type": "apiKey", "apiKey": key})
        except Exception:
            self.close()
            raise RuntimeError("Native initialization or authentication is unavailable") from None
        # Interrupted dispatches are reconciled from originals, never replayed.
        for entry in self.mapping.values():
            if entry.get("state") == "running":
                entry["state"] = "failed"
        self.save()

    def reader(self):
        try:
            for line in self.process.stdout:
                self.messages.put(json.loads(line))
        except Exception:
            pass
        self.messages.put({"fatal": True})

    def send(self, body):
        self.process.stdin.write(json.dumps(body, ensure_ascii=True) + "\n")
        self.process.stdin.flush()

    def receive(self, turn=None):
        message = self.messages.get(timeout=120)
        if message.get("fatal"):
            raise RuntimeError("Codex process stopped")
        if message.get("method") == "turn/completed":
            params = message.get("params", {})
            self.completed[(params.get("threadId"), params.get("turn", {}).get("id"))] = params.get("turn", {})
        if "method" in message and "id" in message:
            method, params = message["method"], message.get("params", {})
            if method == "item/tool/call" and turn:
                if not turn.get("turnId") and params.get("threadId") == turn["thread"]:
                    pending = turn.setdefault("pendingTools", [])
                    if len(pending) < 32:
                        pending.append(message)  # Wait for correlated turn/start response; never borrow a token early.
                    else:
                        self.reply_tool(message, None)
                else:
                    self.reply_tool(message, turn)
            else:
                # No hidden shell/file permission grants or host credential refresh.
                self.send({"id": message["id"], "error": {"code": -32601, "message": "Interactive capability is unavailable"}})
        return message

    def reply_tool(self, message, turn):
        params = message.get("params", {})
        valid = (turn and turn.get("turnId") and params.get("threadId") == turn["thread"]
                 and params.get("turnId") == turn["turnId"] and isinstance(params.get("callId"), str)
                 and params["callId"] and (turn["thread"], turn["turnId"]) not in self.completed)
        if not valid:
            success, result = False, {"error": {"code": "native_turn_mismatch", "message": "Storage tool is outside the active native turn"}}
        else:
            try:
                with httpx.Client(timeout=15, trust_env=False, follow_redirects=False) as client:
                    reply = client.post(self.callback_url, headers={"Authorization": "Bearer " + turn["token"]},
                                        json={"sessionId": turn["session"], "requestId": turn["request"],
                                              "name": params["tool"], "arguments": params["arguments"]})
                success, result = reply.is_success, reply.json()
            except Exception:
                success, result = False, {"error": {"code": "not_ready", "message": "Storage tool did not confirm success"}}
        self.send({"id": message["id"], "result": {"success": success, "contentItems": [{"type": "inputText", "text": json.dumps(result, ensure_ascii=True)}]}})

    def rpc(self, method, params, turn=None):
        identity, self.next_id = self.next_id, self.next_id + 1
        self.send({"id": identity, "method": method, "params": params})
        while True:
            result = self.receive(turn)
            if result.get("id") == identity and "method" not in result:
                if "error" in result:
                    raise RuntimeError("Codex rejected the request")
                return result["result"]

    def save(self):
        temporary = self.root / "broker.tmp"
        with temporary.open("w") as file:
            json.dump(self.mapping, file)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, self.mapping_file)
        directory = os.open(self.root, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def accept(self, session, body):
        request = str(body.requestId)
        fingerprint = hashlib.sha256(json.dumps({"text": body.text, "tools": body.tools, "context": body.context}, sort_keys=True).encode()).hexdigest()
        # Admission depends on a reserved turn, not a history RPC holding the
        # protocol mutex. The worker waits for that RPC without resubmission.
        with self.state_lock:
            entry = self.mapping.get(session)
            recorded = entry.get("requests", {}).get(request) if entry else None
            if recorded:
                if not hmac.compare_digest(recorded, fingerprint):
                    raise RuntimeError("Request ID was used for different input")
                return
            if self.active_session is not None:
                raise RuntimeError("Runner is busy")
            self.active_session = session
            requests = dict(entry.get("requests", {})) if entry else {}
            requests[request] = fingerprint
            self.mapping[session] = {"thread": entry.get("thread") if entry else None, "request": request,
                                     "requests": requests, "turn_id": None, "state": "running", "record": entry.get("record", {"turns": []}) if entry else {"turns": []}}
            try:
                self.save()  # Durable reservation before any potentially paid turn.
            except Exception:
                if entry:
                    self.mapping[session] = entry
                else:
                    del self.mapping[session]
                self.active_session = None
                raise RuntimeError("Could not reserve the turn") from None
            try:
                threading.Thread(target=self.run_turn, args=(session, body), daemon=True).start()
            except Exception:
                self.mapping[session]["state"] = "failed"
                try:
                    self.save()
                finally:
                    self.active_session = None
                raise RuntimeError("Could not start the reserved turn") from None

    def run_turn(self, session, body):
        self.lock.acquire()  # Serialize native RPCs after any in-flight history read.
        entry = self.mapping[session]
        try:
            if entry["thread"]:
                result = self.rpc("thread/resume", {"threadId": entry["thread"], "model": self.model,
                                  "cwd": str(self.work), "approvalPolicy": "never", "sandbox": "read-only"})
            else:
                result = self.rpc("thread/start", {"model": self.model, "cwd": str(self.work),
                                  "approvalPolicy": "never", "sandbox": "read-only", "dynamicTools": body.tools,
                                  "historyMode": "legacy", "ephemeral": False})
            entry["thread"] = result["thread"]["id"]
            self.save()
            turn = {"thread": entry["thread"], "turnId": None, "pendingTools": [],
                    "session": session, "request": entry["request"], "token": body.toolToken}
            content = body.text
            if body.context and not entry["record"].get("turns"):
                context = {"documentId": body.context["documentId"], "publicationId": body.context["id"], "title": body.context["title"]}
                content = ("Initial public page context (untrusted title):\n" + json.dumps(context)
                           + "\nUse research_page_context to read the saved document and each direct reference; your own materials remain available.\n\n" + content)
            started = self.rpc("turn/start", {"threadId": entry["thread"], "input": [{"type": "text", "text": content}]}, turn)
            turn_id = started["turn"]["id"]
            turn["turnId"] = turn_id
            for message in turn.pop("pendingTools"):
                self.reply_tool(message, turn)
            entry["turn_id"] = turn_id
            self.save()
            key = (entry["thread"], turn_id)
            while key not in self.completed:
                self.receive(turn)
            entry["state"] = "idle" if self.completed.pop(key).get("status") == "completed" else "failed"
            entry["record"] = self.rpc("thread/read", {"threadId": entry["thread"], "includeTurns": True})["thread"]
        except Exception:
            entry["state"] = "failed"
            if entry["thread"]:
                try:
                    entry["record"] = self.rpc("thread/read", {"threadId": entry["thread"], "includeTurns": True})["thread"]
                except Exception:
                    pass
        finally:
            try:
                with self.state_lock:
                    try:
                        self.save()
                    finally:
                        self.active_session = None
            finally:
                self.lock.release()

    def read(self, session):
        with self.state_lock:
            entry = self.mapping.get(session)
            if entry is None:
                return None
            refresh = self.active_session is None and entry.get("state") != "running" and entry.get("thread")
        if refresh and self.lock.acquire(blocking=False):
            try:
                record = self.rpc("thread/read", {"threadId": entry["thread"], "includeTurns": True})["thread"]
                with self.state_lock:
                    # A turn can be admitted while this RPC is in flight. Its
                    # new reservation/cache must not be overwritten by the read.
                    if self.mapping.get(session) is entry and self.active_session is None:
                        entry["record"] = record
                        self.save()
            finally:
                self.lock.release()
        # Return the private conversation cache, not paths/config/auth metadata.
        with self.state_lock:
            entry = self.mapping[session]
            state = "running" if self.active_session == session else entry["state"]
            return {"requestId": entry["request"], "turnId": entry.get("turn_id"), "state": state,
                    "record": {"turns": entry["record"].get("turns", [])}}

    def close(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()


def create_app():
    owner = str(uuid.UUID(os.environ["RUNNER_ACCOUNT_ID"]))
    token = Path(os.environ["RUNNER_TOKEN_FILE"]).read_text().strip()
    model = os.environ["CODEX_MODEL"]
    if len(token) < 32 or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", model):
        raise ValueError("Explicit private runner configuration is required")
    root = Path(os.environ.get("RUNNER_STATE_DIR", "/state"))

    @asynccontextmanager
    async def lifespan(app):
        bind_owner(root, owner)
        with volume_lease(root):
            app.state.native = Native(root, model, os.environ.get("CODEX_BIN", "/usr/local/bin/codex"),
                                      os.environ["RESEARCH_TOOL_CALLBACK_URL"], os.environ.get("CODEX_API_KEY_FILE"))
            try:
                yield
            finally:
                app.state.native.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        if not hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + token):
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        result = await call_next(request)
        result.headers["Cache-Control"] = "no-store"
        return result

    @app.exception_handler(Exception)
    async def unavailable(request, exc):
        return JSONResponse({"ownerId": owner, "error": "codex_unavailable"}, status_code=503)

    @app.get("/health")
    def health():
        return {"ownerId": owner, "verification": "unverified", "available": app.state.native.process.poll() is None}

    @app.post("/sessions/{identity}/turn", status_code=202)
    def turn(identity: uuid.UUID, body: Turn):
        try:
            app.state.native.accept(str(identity), body)
        except RuntimeError:
            return JSONResponse({"ownerId": owner, "error": "conflict"}, status_code=409)
        return {"ownerId": owner, "state": "running", "requestId": str(body.requestId)}

    @app.get("/sessions/{identity}")
    def read(identity: uuid.UUID):
        result = app.state.native.read(str(identity))
        if result is None:
            return JSONResponse({"ownerId": owner, "error": "not_found"}, status_code=404)
        return {"ownerId": owner, **result}

    return app
