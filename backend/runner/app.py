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
from copy import deepcopy
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.codex_policy import MODEL, CodexFailure, ERRORS, error_reason
from .auth import PersonalAuth, read_private, write_private
from .projection import PROJECTION_VERSION


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
    PROJECTION_VERSION = PROJECTION_VERSION
    def __init__(self, root, model, binary, callback_url, api_key_file=None, auth=None):
        if model != MODEL or api_key_file:
            raise CodexFailure("policy_refused")
        self.auth, self.blocked_reason, self.real_verified = auth, None, False
        self.fixture = binary == "/usr/local/bin/codex-fixture"
        if self.fixture and os.getenv("APP_ENV") != "test":
            raise CodexFailure("policy_refused")
        if auth and not self.fixture:
            if os.getenv("APP_ENV") != "personal-test" or os.getenv("CODEX_EXECUTION_SCOPE") != "personal-private":
                raise CodexFailure("policy_refused")
            from .install_codex import EXPECTED_SHA256
            if binary != "/usr/local/bin/codex":
                raise CodexFailure("policy_refused")
            try:
                with open(binary, "rb") as executable:
                    if hashlib.file_digest(executable, "sha256").hexdigest() != EXPECTED_SHA256:
                        raise CodexFailure("policy_refused")
            except OSError:
                raise CodexFailure("policy_refused") from None
        self.root, self.model, self.callback_url = root, model, callback_url
        self.lock, self.messages, self.next_id = threading.Lock(), queue.Queue(), 1
        self.state_lock = threading.RLock()
        self.completed = {}
        self.active_session = None
        self.mapping_file = root / "broker.json"
        self.mapping = json.loads(self.mapping_file.read_text()) if self.mapping_file.exists() else {}
        if auth and any(entry.get("projectionVersion") != self.PROJECTION_VERSION for entry in self.mapping.values()):
            raise CodexFailure("policy_refused")  # Legacy unsafe projections need private reconciliation, never automatic trust.
        self.closing, self.closed = False, False
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        home, codex_home, work = root / "home", root / "codex", root / "workspace"
        for path in (home, codex_home, work):
            path.mkdir(exist_ok=True, mode=0o700)
        if any((path / "config.toml").exists() for path in (codex_home, work / ".codex")):
            raise CodexFailure("policy_refused")
        if not auth and (codex_home / "auth.json").exists():
            raise CodexFailure("policy_refused")
        if auth:
            auth.attach(codex_home)
            try:
                for session, entry in self.mapping.items():
                    self.verify_projection(session, entry)
            except Exception:
                auth.close(clean=False)
                raise CodexFailure("policy_refused") from None
        if (root / "model-block.json").exists() or (auth and auth.state().get("modelBlocked")):
            self.blocked_reason = "model_unavailable"
        environment = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": str(home), "CODEX_HOME": str(codex_home)}
        self.work = work
        self.process = subprocess.Popen([binary, "app-server", "--listen", "stdio://",
                    "-c", 'cli_auth_credentials_store="file"' if auth else 'cli_auth_credentials_store="ephemeral"',
                    "-c", 'model="gpt-6.1-sol"', "-c", "features.shell_tool=false",
                    "-c", 'model_provider="openai"',
                    "-c", "features.multi_agent=false", "-c", "features.multi_agent_v2=false",
                    "-c", "features.unbounded_connection_retries=false", "-c", "features.unified_exec=false",
                    "-c", "features.apps=false", "-c", "features.plugins=false", "-c", "features.remote_plugin=false",
                    "-c", "features.hooks=false", "-c", "features.code_mode_host=false",
                    "-c", "features.daemon_auto_start=false",
                    "-c", "features.browser_use=false", "-c", "features.computer_use=false",
                    "-c", "features.image_generation=false", "-c", 'web_search="disabled"',
                    "-c", 'forced_login_method="chatgpt"'],
                    env=environment, cwd=work, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, text=True, bufsize=1)
        threading.Thread(target=self.reader, daemon=True).start()
        try:
            self.rpc("initialize", {"clientInfo": {"name": "anxious_research", "version": "1"}, "capabilities": {"experimentalApi": True}})
            self.send({"method": "initialized"})
            if auth:
                account = self.rpc("account/read", {"refreshToken": False}).get("account")
                if not account or account.get("type") != "chatgpt":
                    raise CodexFailure("auth_revoked")
                if account.get("email"):
                    auth.secrets.add(account["email"])
        except CodexFailure:
            self.close()
            raise
        except Exception:
            self.close()
            raise RuntimeError("Native initialization or authentication is unavailable") from None
        # Interrupted dispatches are reconciled from originals, never replayed.
        for entry in self.mapping.values():
            if entry.get("state") == "running":
                entry["state"] = "failed"
        self.save()

    def availability(self):
        if self.blocked_reason:
            return self.blocked_reason
        if self.process.poll() is not None:
            return "unavailable"
        if not self.auth and not self.fixture:
            return "not_configured"
        if self.auth:
            try:
                self.auth.check()
            except CodexFailure as failure:
                return failure.reason
        return None

    def verify_projection(self, session, entry):
        if self.auth and (entry.get("record", {}).get("turns") or "modelMismatchBaseline" in entry.get("record", {})):
            expected = self.auth.projection_mac(session, entry.get("thread"), entry["record"])
            actual = entry.get("projectionMac")
            if entry.get("projectionVersion") != self.PROJECTION_VERSION or not isinstance(actual, str) or not re.fullmatch(r"[0-9a-f]{64}", actual) or not hmac.compare_digest(expected, actual):
                raise CodexFailure("policy_refused")

    def store_record(self, session, entry, response, verified_turn_id=None):
        # Polling verifies under this same lock. Publish record and MAC as one
        # state change, including when a worker projects its completed turn.
        with self.state_lock:
            self._store_record(session, entry, response, verified_turn_id)

    def _store_record(self, session, entry, response, verified_turn_id):
        record = self.record(response)
        mismatches = entry.setdefault("modelMismatchTurns", [])
        for previous in entry.get("record", {}).get("turns", []):
            if previous.get("modelMismatch") is True and previous.get("id") not in mismatches:
                mismatches.append(previous["id"])
        previous_record = entry.get("record", {})
        baseline = previous_record.get("modelMismatchBaseline")
        if baseline is None and entry.get("error") == "codex_model_unavailable":
            # A model event can abort turn/start before its reply supplies an
            # identity. Freeze the previously projected turns once; quarantine
            # every new original turn, including ones appearing on later reads
            # or after restart. An early/stale event never authorizes a tool or
            # selects an old good turn for exclusion.
            baseline = [previous.get("id") for previous in previous_record.get("turns", [])]
        if baseline is not None:
            # Quarantine outlives the request's error and operator release.
            # Only a completed, correlated new turn can join the signed allow
            # list; a history read must never bless an unidentified old result.
            verified = list(previous_record.get("modelVerifiedTurns", []))
            if verified_turn_id and verified_turn_id not in mismatches and verified_turn_id not in verified:
                verified.append(verified_turn_id)
            record["modelMismatchBaseline"] = baseline  # Private metadata is covered by the projection MAC.
            record["modelVerifiedTurns"] = verified
            for current in record["turns"]:
                if current.get("id") not in baseline and current.get("id") not in verified and current.get("id") not in mismatches:
                    mismatches.append(current.get("id"))
        for turn in record["turns"]:
            if turn.get("id") in mismatches:
                turn["modelMismatch"] = True
                turn["items"] = [{**item, "modelMismatch": True} for item in turn.get("items", []) if item.get("type") != "agentMessage"]
        self.publish_record(session, entry, record)

    def publish_record(self, session, entry, record):
        entry["record"] = record
        entry["projectionVersion"] = self.PROJECTION_VERSION
        if self.auth:
            entry["projectionMac"] = self.auth.projection_mac(session, entry.get("thread"), record)

    def quarantine_history(self, session, entry):
        # Capture the already safe boundary before any fallible history RPC.
        # A later operator release cannot erase it if that RPC never succeeds.
        with self.state_lock:
            record = deepcopy(entry.get("record", {"turns": []}))
            if self.auth:
                record = self.auth.sanitize(record)
            record.setdefault("modelMismatchBaseline", [turn.get("id") for turn in record.get("turns", [])])
            record.setdefault("modelVerifiedTurns", [])
            self.publish_record(session, entry, record)

    def record(self, response):
        if self.auth:
            self.auth.refresh_projection_secrets()
        # Retired literal fingerprints persist outside the home and remain
        # available when partial or edited original turns are reprojected.
        turns = deepcopy(response.get("turns", []))
        record = {"turns": turns}
        # Turn errors retain safe codes, never provider message/additionalDetails.
        for turn in record["turns"]:
            if turn.get("error"):
                turn["error"] = {"code": ERRORS.get(error_reason(turn["error"]), "codex_failed")}
        return self.auth.sanitize(record) if self.auth else record

    def block_model(self):
        with self.state_lock:
            self.blocked_reason = "model_unavailable"
            self.real_verified = False
            write_private(self.root / "model-block.json", {"reason": "model_unavailable"})
            if self.auth:
                self.auth.block_model()

    def ensure_model(self, response, required=False):
        if (required and response.get("model") != MODEL) or ("model" in response and response["model"] != MODEL):
            self.block_model()
            raise CodexFailure("model_unavailable")

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
        if message.get("method") == "model/rerouted":
            self.block_model()
            params = message.get("params", {})
            entry = self.mapping.get(self.active_session)
            if turn and entry and params.get("threadId") == turn["thread"] and not turn.get("turnId"):
                entry["turn_id"] = params.get("turnId")
            self.next_id += 1
            self.send({"id": self.next_id - 1, "method": "turn/interrupt", "params": {"threadId": params.get("threadId"), "turnId": params.get("turnId")}})
            raise CodexFailure("model_unavailable")
        if message.get("method") == "account/updated" and self.auth:
            if message.get("params", {}).get("authMode") != "chatgpt":
                self.blocked_reason = "auth_revoked"
                raise CodexFailure("auth_revoked")
        if message.get("method") in {"turn/started", "turn/updated"}:
            self.ensure_model(message.get("params", {}).get("turn", {}))
        if message.get("method") == "turn/completed":
            params = message.get("params", {})
            self.ensure_model(params.get("turn", {}))
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
        failure = None
        valid = (not self.blocked_reason and turn and turn.get("turnId") and params.get("threadId") == turn["thread"]
                 and params.get("turnId") == turn["turnId"] and isinstance(params.get("callId"), str)
                 and params["callId"] and (turn["thread"], turn["turnId"]) not in self.completed)
        if not valid:
            success, result = False, {"error": {"code": "native_turn_mismatch", "message": "Storage tool is outside the active native turn"}}
        else:
            try:
                if self.auth:
                    self.auth.permitted_tool(params["arguments"])
                with httpx.Client(timeout=15, trust_env=False, follow_redirects=False) as client:
                    reply = client.post(self.callback_url, headers={"Authorization": "Bearer " + turn["token"]},
                                        json={"sessionId": turn["session"], "requestId": turn["request"],
                                              "name": params["tool"], "arguments": params["arguments"]})
                success, result = reply.is_success, reply.json()
            except CodexFailure as rejected:
                failure = rejected
                self.blocked_reason = rejected.reason
                success, result = False, {"error": {"code": str(rejected), "message": "Storage tool permission is unavailable"}}
            except Exception:
                success, result = False, {"error": {"code": "not_ready", "message": "Storage tool did not confirm success"}}
        if self.auth:
            result = self.auth.sanitize(result)
        self.send({"id": message["id"], "result": {"success": success, "contentItems": [{"type": "inputText", "text": json.dumps(result, ensure_ascii=True)}]}})
        if failure:
            raise failure

    def rpc(self, method, params, turn=None):
        identity, self.next_id = self.next_id, self.next_id + 1
        self.send({"id": identity, "method": method, "params": params})
        while True:
            result = self.receive(turn)
            if result.get("id") == identity and "method" not in result:
                if "error" in result:
                    raise CodexFailure(error_reason(result["error"]))
                return result["result"]

    def save(self):
        write_private(self.mapping_file, self.mapping)

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
            if self.closing:
                raise CodexFailure("unavailable")
            reason = self.availability()
            if reason:
                raise CodexFailure(reason)
            if self.active_session is not None:
                raise RuntimeError("Runner is busy")
            self.active_session = session
            if self.auth:
                try:
                    self.auth.consume(request)
                except Exception:
                    self.active_session = None
                    raise
            requests = dict(entry.get("requests", {})) if entry else {}
            requests[request] = fingerprint
            self.mapping[session] = {"thread": entry.get("thread") if entry else None, "request": request,
                                     "requests": requests, "turn_id": None, "state": "running", "record": entry.get("record", {"turns": []}) if entry else {"turns": []}, "projectionVersion": self.PROJECTION_VERSION,
                                     "modelMismatchTurns": entry.get("modelMismatchTurns", []) if entry else [], "projectionMac": entry.get("projectionMac") if entry else None}
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
                self.worker = threading.Thread(target=self.run_turn, args=(session, body), daemon=True)
                self.worker.start()
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
            self.ensure_model(result, required=True)
            entry["thread"] = result["thread"]["id"]
            self.save()
            turn = {"thread": entry["thread"], "turnId": None, "pendingTools": [],
                    "session": session, "request": entry["request"], "token": body.toolToken}
            if self.auth:
                self.auth.secrets.add(body.toolToken)
            content = body.text
            if body.context and not entry["record"].get("turns"):
                context = {"documentId": body.context["documentId"], "publicationId": body.context["id"], "title": body.context["title"]}
                content = ("Initial public page context (untrusted title):\n" + json.dumps(context)
                           + "\nUse research_page_context to read the saved document and each direct reference; your own materials remain available.\n\n" + content)
            started = self.rpc("turn/start", {"threadId": entry["thread"], "model": MODEL, "input": [{"type": "text", "text": content}]}, turn)
            entry["turn_id"] = started["turn"]["id"]
            self.ensure_model(started.get("turn", {}))
            turn_id = started["turn"]["id"]
            if self.auth and any(old.get("id") == turn_id for old in entry["record"].get("turns", [])):
                self.blocked_reason = "policy_refused"
                raise CodexFailure("policy_refused")
            turn["turnId"] = turn_id
            for message in turn.pop("pendingTools"):
                self.reply_tool(message, turn)
            entry["turn_id"] = turn_id
            self.save()
            key = (entry["thread"], turn_id)
            while key not in self.completed:
                self.receive(turn)
            completed = self.completed.pop(key)
            entry["state"] = "idle" if completed.get("status") == "completed" and not self.blocked_reason else "failed"
            if entry["state"] == "failed":
                entry["error"] = ERRORS.get(error_reason(completed.get("error")), "codex_failed")
            elif not self.fixture:
                self.real_verified = True
            self.store_record(session, entry, self.rpc("thread/read", {"threadId": entry["thread"], "includeTurns": True})["thread"],
                              verified_turn_id=turn_id if entry["state"] == "idle" else None)
        except Exception as failure:
            entry["state"] = "failed"
            entry["error"] = str(failure) if isinstance(failure, CodexFailure) else "codex_failed"
            if isinstance(failure, CodexFailure) and failure.reason in {"auth_revoked", "auth_expired", "model_unavailable", "policy_refused"}:
                self.blocked_reason = failure.reason
                if failure.reason == "model_unavailable":
                    self.quarantine_history(session, entry)
            if entry["thread"]:
                try:
                    self.store_record(session, entry, self.rpc("thread/read", {"threadId": entry["thread"], "includeTurns": True})["thread"])
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
            self.verify_projection(session, entry)
            refresh = self.active_session is None and entry.get("state") != "running" and entry.get("thread")
        if refresh and self.lock.acquire(blocking=False):
            try:
                raw = self.rpc("thread/read", {"threadId": entry["thread"], "includeTurns": True})["thread"]
                with self.state_lock:
                    # A turn can be admitted while this RPC is in flight. Its
                    # new reservation/cache must not be overwritten by the read.
                    if self.mapping.get(session) is entry and self.active_session is None:
                        self.store_record(session, entry, raw)
                        self.save()
            finally:
                self.lock.release()
        # Return the private conversation cache, not paths/config/auth metadata.
        with self.state_lock:
            entry = self.mapping[session]
            state = "running" if self.active_session == session else entry["state"]
            return {"requestId": entry["request"], "turnId": entry.get("turn_id"), "state": state,
                    "record": {"turns": entry["record"].get("turns", [])}, "errorCode": entry.get("error"), "model": MODEL}

    def close(self):
        if self.closed:
            return
        self.closing = True
        active_at_start = self.active_session is not None
        alive_at_start = self.process.poll() is None
        forced = False
        try:
            if alive_at_start:
                try:
                    self.process.stdin.close()  # Quiescent EOF is expected graceful shutdown.
                except (BrokenPipeError, OSError):
                    forced = True
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                forced = True
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            worker = getattr(self, "worker", None)
            if worker:
                worker.join(timeout=20)
        finally:
            if self.auth:
                clean = alive_at_start and not forced and not active_at_start and self.active_session is None and self.process.poll() == 0
                self.auth.close(clean=clean)
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                if stream:
                    try:
                        stream.close()
                    except (BrokenPipeError, OSError):
                        pass
            self.closed = True


def create_app():
    owner = str(uuid.UUID(os.environ["RUNNER_ACCOUNT_ID"]))
    token = Path(os.environ["RUNNER_TOKEN_FILE"]).read_text().strip()
    model = os.getenv("CODEX_MODEL", MODEL)
    if len(token) < 32 or model != MODEL or os.getenv("CODEX_API_KEY_FILE"):
        raise ValueError("Explicit private runner configuration is required")
    root = Path(os.environ.get("RUNNER_STATE_DIR", "/state"))

    @asynccontextmanager
    async def lifespan(app):
        bind_owner(root, owner)
        with volume_lease(root):
            auth = None
            app.state.native, app.state.auth_failure = None, None
            try:
                mode = os.getenv("CODEX_AUTH_MODE", "none")
                if mode == "personal-cache":
                    if os.getenv("CODEX_EXECUTION_SCOPE") != "personal-private" or os.getenv("APP_ENV") != "personal-test":
                        raise CodexFailure("policy_refused")
                    auth = PersonalAuth(os.environ["CODEX_PERSONAL_CONTROL_DIR"], owner)
                elif mode != "none":
                    raise CodexFailure("policy_refused")
                app.state.native = Native(root, model, os.environ.get("CODEX_BIN", "/usr/local/bin/codex"),
                                          os.environ["RESEARCH_TOOL_CALLBACK_URL"], auth=auth)
            except Exception as failure:
                app.state.auth_failure = failure.reason if isinstance(failure, CodexFailure) else "unavailable"
                if auth:
                    auth.close()
            try:
                yield
            finally:
                if app.state.native:
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
        native = app.state.native
        if not native:
            return {"ownerId": owner, "verification": "unverified", "available": False, "reason": app.state.auth_failure, "model": MODEL}
        reason = native.availability()
        return {"ownerId": owner, "verification": "fixture" if native.fixture else "real" if native.real_verified else "unverified", "available": reason is None, "reason": reason, "model": MODEL}

    @app.post("/sessions/{identity}/turn", status_code=202)
    def turn(identity: uuid.UUID, body: Turn):
        if not app.state.native:
            return JSONResponse({"ownerId": owner, "error": str(CodexFailure(app.state.auth_failure))}, status_code=503)
        try:
            app.state.native.accept(str(identity), body)
        except CodexFailure as failure:
            return JSONResponse({"ownerId": owner, "error": str(failure)}, status_code=503)
        except RuntimeError:
            return JSONResponse({"ownerId": owner, "error": "conflict"}, status_code=409)
        return {"ownerId": owner, "state": "running", "requestId": str(body.requestId)}

    @app.get("/sessions/{identity}")
    def read(identity: uuid.UUID):
        if not app.state.native:
            return JSONResponse({"ownerId": owner, "error": str(CodexFailure(app.state.auth_failure))}, status_code=503)
        result = app.state.native.read(str(identity))
        if result is None:
            return JSONResponse({"ownerId": owner, "error": "not_found"}, status_code=404)
        return {"ownerId": owner, **result}

    return app
