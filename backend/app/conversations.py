"""Owned conversation records and a broker for isolated per-account runners."""
import hashlib
import json
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import BackgroundTasks, Request
from pydantic import Field
from sqlalchemy import select, text

from .models import Account, LoginSession
from .research import Expected, Input, Store, fail, page_query, encode_cursor, parse_id, stamp
from .research_models import Conversation
from .research_tools import definitions, dispatch
from .codex_policy import MODEL, REASONS, ERRORS


class NewConversation(Input):
    title: str = Field(min_length=1, max_length=300)
    publicDocumentId: str | None = None


class Message(Expected):
    text: str = Field(min_length=1, max_length=50000)


class ToolRequest(Input):
    sessionId: str
    requestId: str
    name: str
    arguments: dict


class Runner:
    """Trusted mapping selected using the service account, never a client URL."""
    def __init__(self, url, token, owner):
        self.url, self.token, self.owner = url, token, owner

    def call(self, method, path, body=None):
        with httpx.Client(timeout=5, trust_env=False, follow_redirects=False) as client:
            reply = client.request(method, self.url + path, json=body, headers={"Authorization": "Bearer " + self.token})
            reply.raise_for_status()
            result = reply.json()
            if not isinstance(result, dict) or result.get("ownerId") != str(self.owner):
                raise ValueError("Runner ownership mismatch")
            return result

    def read(self, identity):
        return self.call("GET", "/sessions/" + str(identity))

    def health(self):
        return self.call("GET", "/health")

    def submit(self, identity, body):
        return self.call("POST", "/sessions/" + str(identity) + "/turn", body)


def runners_from_file():
    filename = os.getenv("CODEX_RUNNERS_FILE", "")
    if not filename:
        return {}
    entries = json.loads(Path(filename).read_text())
    if not isinstance(entries, dict):
        raise ValueError("Invalid runner configuration")
    result, destinations = {}, set()
    for identity, entry in entries.items():
        owner = uuid.UUID(identity)
        url = entry["url"].rstrip("/")
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password or url in destinations:
            raise ValueError("Each account requires an isolated runner URL")
        token = Path(entry["tokenFile"]).read_text().strip()
        if len(token) < 32 or any(r.token == token for r in result.values()):
            raise ValueError("Each runner requires a separate secret")
        destinations.add(url)
        result[owner] = Runner(url, token, owner)
    return result


def owned(store, identity, lock=False):
    statement = select(Conversation).where(Conversation.id == parse_id(identity),
                Conversation.owner_id == store.owner, Conversation.archived.is_(False))
    row = store.db.scalar(statement.with_for_update() if lock else statement)
    if row is None:
        fail("not_found", "Not found", 404)
    return row


def summary(row):
    return {"id": str(row.id), "title": row.title, "state": row.state, "version": row.version,
            "createdAt": stamp(row.created_at), "updatedAt": stamp(row.updated_at),
            "context": {"documentId": row.context["documentId"], "publicationId": row.context["id"], "title": row.context["title"]} if row.context else None}


def native_items(row):
    return [item for turn in row.native_record.get("turns", []) for item in turn.get("items", [])]


def display_items(row):
    result = []
    unrecorded = row.native_record.get("unrecordedInputs", [])
    turns = row.native_record.get("turns", [])
    def add_unrecorded(index):
        for item in unrecorded:
            if item["beforeTurn"] == index or (index == len(turns) and item["beforeTurn"] > index):
                result.append({"id": item["id"], "type": "message", "role": "user", "text": item["text"],
                               "source": "platform", "status": "not_recorded"})
    for index, turn in enumerate(turns):
        add_unrecorded(index)
        for item in turn.get("items", []):
            result.extend(display_native_item(item))
    add_unrecorded(len(turns))
    if row.pending_text:
        result.append({"id": "pending-" + str(row.request_id), "type": "message", "role": "user", "text": row.pending_text,
                       "source": "platform", "status": "pending" if row.state == "running" else "not_recorded"})
    return result


def display_native_item(item):
    result = []
    if item:
        kind, identity = item.get("type"), item.get("id")
        if kind == "userMessage":
            value = "\n".join(part.get("text", "") for part in item.get("content", []) if part.get("type") == "text")
            result.append({"id": identity, "type": "message", "role": "user", "text": value})
        elif kind == "agentMessage":
            result.append({"id": identity, "type": "message", "role": "assistant", "text": item.get("text", "")})
        elif kind in {"dynamicToolCall", "mcpToolCall"}:
            result.append({"id": identity, "type": "tool_call", "name": item.get("tool", ""),
                           "input": item.get("arguments"), "output": item.get("contentItems", item.get("result")),
                           "status": item.get("status", "unknown")})
    return result


def register_conversations(app, authorized, sessions, settings, response, write):
    prefix = settings.base_path + "/api/research"
    app.state.research_runners = runners_from_file()
    app.state.codex_verification = "unverified"
    personal = os.getenv("CODEX_PERSONAL_ACCOUNT_ID", "")
    app.state.codex_personal_owner = uuid.UUID(personal) if personal else None
    app.state.codex_personal_enabled = os.getenv("CODEX_PERSONAL_ENABLE") == "true" and os.getenv("APP_ENV") == "personal-test" and urlsplit(settings.origin).hostname in {"localhost", "127.0.0.1", "::1"}

    def connection_status(owner):
        result = {"available": False, "reason": "not_configured", "verification": "unverified", "model": MODEL}
        runner = app.state.research_runners.get(owner)
        fixture = app.state.codex_verification == "fixture"
        if not fixture and app.state.codex_personal_owner:
            if owner != app.state.codex_personal_owner:
                return {**result, "reason": "not_enabled_for_account"}
            if not app.state.codex_personal_enabled:
                return {**result, "reason": "policy_refused"}
        if not runner:
            return result
        if not fixture and not app.state.codex_personal_owner:
            return {**result, "reason": "policy_refused"}
        try:
            health = runner.health()
            if health.get("model") != MODEL:
                return {**result, "reason": "model_unavailable"}
            reason = health.get("reason")
            verified = health.get("verification")
            if (reason is not None and (not isinstance(reason, str) or reason not in REASONS)) or not isinstance(verified, str) or verified not in {"fixture", "unverified", "real"} or type(health.get("available")) is not bool:
                raise ValueError()
            if verified == "fixture" and not fixture:
                raise ValueError()
            available = health["available"] and reason is None
            return {**result, "available": available, "reason": None if available else reason or "unavailable", "verification": verified}
        except (httpx.HTTPError, ValueError, KeyError):
            return {**result, "reason": "unavailable"}

    def require_schema(store):
        if store.db.execute(text("SELECT version_num FROM research.alembic_version")).scalar_one() not in {"0003_sessions", "0004_publication"}:
            fail("not_ready", "Conversation schema is not ready", 503)

    def runner_for(owner):
        runner = app.state.research_runners.get(owner)
        status = connection_status(owner)
        if not status["available"]:
            fail(ERRORS.get(status["reason"], "codex_unavailable"), "Codex cannot accept this request", 503)
        return runner

    def refresh(store, row):
        if row.state != "running":
            return
        runner = app.state.research_runners.get(store.owner)
        try:
            if not runner:
                raise ValueError()
            result = runner.read(row.id)
            if result.get("requestId") != str(row.request_id) or result.get("state") not in {"running", "idle", "failed"}:
                raise ValueError()
        except (httpx.HTTPError, ValueError, KeyError):
            # A short outage must not pretend to cancel a still-running paid turn.
            if row.tool_expires_at and row.tool_expires_at <= datetime.now(timezone.utc):
                row.state, row.error_code = "failed", "codex_unavailable"
                row.version += 1
                row.tool_token_hash = None
            return
        record = result.get("record")
        if not isinstance(record, dict) or not isinstance(record.get("turns", []), list):
            fail("codex_unavailable", "Codex history is unavailable", 503)
        # Native snapshots cannot overwrite platform-retained failed inputs.
        row.native_record = {**record, "unrecordedInputs": row.native_record.get("unrecordedInputs", [])}
        if result["state"] != row.state:
            row.state = result["state"]
            safe_errors = {*ERRORS.values(), "codex_failed", "codex_rejected", "codex_unavailable"}
            row.error_code = result.get("errorCode") if row.state == "failed" and result.get("errorCode") in safe_errors else "codex_failed" if row.state == "failed" else None
            row.version += 1
            row.updated_at = datetime.now(timezone.utc)
            row.tool_token_hash = None
            # A failed later turn can repeat earlier text. Only its own native
            # turn, not an older matching message, proves the input was recorded.
            if result.get("turnId") and any(item.get("type") == "userMessage"
                   for turn in record.get("turns", []) if turn.get("id") == result["turnId"]
                   for item in turn.get("items", [])):
                row.pending_text = None
        store.db.flush()

    def launch(owner, identity, request_id, token):
        # A post-response background dispatch. A crash never auto-replays a turn.
        try:
            with sessions() as db:
                row = db.scalar(select(Conversation).where(Conversation.id == identity, Conversation.owner_id == owner))
                if not row or row.state != "running" or row.request_id != request_id:
                    return
                payload = {"requestId": str(request_id), "text": row.pending_text,
                           "tools": definitions(), "toolToken": token, "context": row.context}
            runner_for(owner).submit(identity, payload)
        except Exception as exc:
            with sessions.begin() as db:
                row = db.scalar(select(Conversation).where(Conversation.id == identity, Conversation.owner_id == owner).with_for_update())
                if row and row.request_id == request_id and row.state == "running":
                    definite = isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {400, 401, 403, 404, 409, 413, 422, 429}
                    if definite:
                        # Explicit rejection means this turn was not accepted.
                        row.state, row.error_code = "failed", "codex_rejected"
                        row.version += 1
                        row.updated_at = datetime.now(timezone.utc)
                        row.tool_token_hash, row.tool_expires_at = None, None
                    elif isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 503:
                        try:
                            code = exc.response.json().get("error")
                        except ValueError:
                            code = None
                        if code in ERRORS.values():
                            row.state, row.error_code = "failed", code
                            row.version += 1
                            row.tool_token_hash, row.tool_expires_at = None, None
                        else:
                            row.error_code = "codex_unavailable"
                    else:
                        # Ambiguous network/5xx failure must never be auto-replayed.
                        row.error_code = "codex_unavailable"

    @app.get(prefix + "/codex/status")
    def status(request: Request):
        with authorized(request) as store:
            return response(connection_status(store.owner))

    @app.get(prefix + "/sessions")
    def listing(request: Request, limit: int = 30, cursor: str | None = None):
        with authorized(request) as store:
            require_schema(store)
            query = select(Conversation).where(Conversation.owner_id == store.owner, Conversation.archived.is_(False))
            rows = store.db.scalars(page_query(query, Conversation, limit, cursor)).all()
            return response({"items": [summary(row) for row in rows[:limit]],
                             "nextCursor": encode_cursor(rows[limit - 1]) if len(rows) > limit else None})

    @app.post(prefix + "/sessions")
    def create(body: dict, request: Request):
        with authorized(request, True) as store:
            require_schema(store)
            def action(parsed):
                if not parsed.title.strip():
                    fail("validation_error", "Title is required", 422)
                context = None
                if parsed.publicDocumentId is not None:
                    parse_id(parsed.publicDocumentId)
                    from .publications import require_publication_schema, current_publication
                    require_publication_schema(store.db)
                    context = current_publication(store.db, document_id=parsed.publicDocumentId, lock=True).snapshot
                row = Conversation(owner_id=store.owner, title=parsed.title.strip(), context=context)
                store.db.add(row)
                store.db.flush()
                return summary(row)
            return write(store, request, NewConversation, body, action, 201)

    @app.get(prefix + "/sessions/{identity}")
    def detail(identity: uuid.UUID, request: Request):
        with authorized(request) as store:
            require_schema(store)
            row = owned(store, identity, lock=True)
            refresh(store, row)
            return response({**summary(row), "items": display_items(row),
                             "error": {"code": row.error_code, "message": "Codex did not confirm a successful turn"} if row.error_code else None})

    @app.get(prefix + "/sessions/{identity}/items/{item_id}")
    def item(identity: uuid.UUID, item_id: str, request: Request):
        with authorized(request) as store:
            require_schema(store)
            row = owned(store, identity, lock=True)
            refresh(store, row)
            for raw in native_items(row):
                if raw.get("id") == item_id and raw.get("type") in {"userMessage", "agentMessage", "dynamicToolCall", "mcpToolCall"}:
                    return response({"id": item_id, "format": "json", "raw": raw})
            for raw in row.native_record.get("unrecordedInputs", []):
                if raw["id"] == item_id:
                    return response({"id": item_id, "format": "json", "raw": {**raw, "type": "platformInput", "status": "not_recorded"}})
            if row.pending_text and item_id == "pending-" + str(row.request_id):
                return response({"id": item_id, "format": "json", "raw": {"type": "platformInput", "source": "platform",
                                 "text": row.pending_text, "status": "pending" if row.state == "running" else "not_recorded"}})
            fail("not_found", "Not found", 404)

    @app.post(prefix + "/sessions/{identity}/messages")
    def message(identity: uuid.UUID, body: dict, request: Request, background: BackgroundTasks):
        with authorized(request, True) as store:
            require_schema(store)
            def action(parsed):
                row = owned(store, identity, lock=True)
                refresh(store, row)
                Store.expect(row, parsed.expectedVersion)
                if row.state == "running":
                    fail("conflict", "A turn is already running", 409)
                runner_for(store.owner)
                if row.pending_text:
                    prior = {"id": "unrecorded-" + str(row.request_id), "requestId": str(row.request_id),
                             "text": row.pending_text, "beforeTurn": len(row.native_record.get("turns", [])),
                             "source": "platform", "errorCode": row.error_code or "codex_failed"}
                    row.native_record = {**row.native_record, "unrecordedInputs": row.native_record.get("unrecordedInputs", []) + [prior]}
                from .main import session_digest
                token = secrets.token_urlsafe(32)
                row.login_hash = session_digest(request.cookies["anxious_session"], settings.origin)
                row.tool_token_hash = hashlib.sha256(token.encode()).hexdigest()
                row.tool_expires_at = datetime.now(timezone.utc) + timedelta(minutes=30)
                row.pending_text, row.request_id, row.state = parsed.text, uuid.uuid4(), "running"
                row.version += 1
                row.updated_at, row.error_code = datetime.now(timezone.utc), None
                store.db.flush()
                background.add_task(launch, store.owner, row.id, row.request_id, token)
                return summary(row)
            return write(store, request, Message, body, action, 202)

    @app.delete(prefix + "/sessions/{identity}")
    def archive(identity: uuid.UUID, body: dict, request: Request):
        with authorized(request, True) as store:
            require_schema(store)
            def action(parsed):
                row = owned(store, identity, lock=True)
                refresh(store, row)
                Store.expect(row, parsed.expectedVersion)
                if row.state == "running":
                    fail("conflict", "Wait for the current turn before archiving", 409)
                row.archived, row.version, row.tool_token_hash = True, row.version + 1, None
                row.updated_at = datetime.now(timezone.utc)
                store.db.flush()
                return {"id": str(row.id), "archived": True, "version": row.version}
            return write(store, request, Expected, body, action)

    @app.post(settings.base_path + "/api/internal/research/tools")
    def tool(body: ToolRequest, request: Request):
        bearer = request.headers.get("authorization", "")
        if not bearer.startswith("Bearer ") or len(bearer) > 200:
            fail("unauthenticated", "Tool authentication required", 401)
        token_hash = hashlib.sha256(bearer[7:].encode()).hexdigest()
        with sessions.begin() as db:
            row = db.scalar(select(Conversation).where(Conversation.id == parse_id(body.sessionId),
                         Conversation.tool_token_hash == token_hash, Conversation.archived.is_(False),
                         Conversation.state == "running", Conversation.request_id == parse_id(body.requestId),
                         Conversation.tool_expires_at > datetime.now(timezone.utc)))
            if not row:
                fail("not_found", "Not found", 404)
            account = db.scalar(select(Account).where(Account.id == row.owner_id).with_for_update(read=True))
            login = db.scalar(select(LoginSession).where(LoginSession.token_hash == row.login_hash,
                        LoginSession.account_id == row.owner_id, LoginSession.expires_at > datetime.now(timezone.utc)))
            if not login or not account.is_approved or app.state.research_policy == "pending" or account.role not in {"editor", "admin"}:
                fail("forbidden", "Storage tool permission was revoked", 403)
            from .research_schema_contract import validate_research
            try:
                validate_research(db.connection())
            except RuntimeError:
                fail("not_ready", "Research schema is not ready", 503)
            return response(dispatch(Store(db, row.owner_id), body.name, body.arguments, context=row.context))
