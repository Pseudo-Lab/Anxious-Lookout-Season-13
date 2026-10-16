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
from .research import Expected, Input, Store, ResearchError, fail, page_query, encode_cursor, parse_id, stamp
from .research_models import Conversation
from .research_tools import definitions, dispatch
from .codex_policy import MODEL, REASONS, ERRORS, RUNNER_PREFLIGHT_ERRORS, personal_origin_enabled
from .page_scope import Page, resolve_scope, scope_summary, request_metadata, tool_allowed


class NewConversation(Input):
    title: str = Field(min_length=1, max_length=300)
    publicDocumentId: str | None = None


class Message(Expected):
    text: str = Field(min_length=1, max_length=50000)
    page: Page | None = None


class ToolRequest(Input):
    sessionId: str
    requestId: str
    name: str
    arguments: dict


class Runner:
    """Trusted mapping selected using the service account, never a client URL."""
    def __init__(self, url, token, owner, state_id=None):
        self.url, self.token, self.owner, self.state_id = url, token, owner, state_id

    def call(self, method, path, body=None):
        with httpx.Client(timeout=5, trust_env=False, follow_redirects=False) as client:
            headers = {"Authorization": "Bearer " + self.token}
            if self.state_id:
                headers["X-Runner-State-Id"] = self.state_id
            reply = client.request(method, self.url + path, json=body, headers=headers)
            reply.raise_for_status()
            result = reply.json()
            if not isinstance(result, dict) or result.get("ownerId") != str(self.owner):
                raise ValueError("Runner ownership mismatch")
            if self.state_id and result.get("stateId") != self.state_id:
                raise ValueError("Runner state binding mismatch")
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
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate runner configuration key")
            result[key] = value
        return result
    entries = json.loads(Path(filename).read_text(), object_pairs_hook=unique_keys)
    if not isinstance(entries, dict):
        raise ValueError("Invalid runner configuration")
    result, destinations, states = {}, set(), set()
    for identity, entry in entries.items():
        owner = uuid.UUID(identity)
        if str(owner) != identity or owner in result:
            raise ValueError("Each account requires a canonical isolated owner identity")
        if not isinstance(entry, dict) or entry.keys() - {"url", "tokenFile", "stateId"}:
            raise ValueError("Invalid runner configuration")
        state_id = entry.get("stateId")
        if state_id is None and os.getenv("APP_ENV") != "test":
            raise ValueError("A verified durable state binding is required")
        if state_id is not None:
            if not isinstance(state_id, str) or str(uuid.UUID(state_id)) != state_id or state_id in states:
                raise ValueError("Each account requires a canonical isolated state identity")
            states.add(state_id)
        url = entry["url"].rstrip("/")
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ValueError("Each account requires an isolated runner URL")
        destination = (parsed.scheme.lower(), parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == "https" else 80))
        if destination in destinations:
            raise ValueError("Each account requires an isolated runner URL")
        token = Path(entry["tokenFile"]).read_text().strip()
        if len(token) < 32 or any(r.token == token for r in result.values()):
            raise ValueError("Each runner requires a separate secret")
        destinations.add(destination)
        result[owner] = Runner(url, token, owner, state_id)
    return result


def owned(store, identity, lock=False):
    statement = select(Conversation).where(Conversation.id == parse_id(identity),
                Conversation.owner_id == store.owner, Conversation.archived.is_(False))
    row = store.db.scalar(statement.with_for_update() if lock else statement)
    if row is None:
        fail("not_found", "Not found", 404)
    return row


def summary(row):
    metadata = request_metadata(row)
    return {"id": str(row.id), "title": row.title, "state": row.state, "version": row.version,
            "createdAt": stamp(row.created_at), "updatedAt": stamp(row.updated_at),
            **scope_summary(metadata["scope"] if metadata else None),
            "context": {"documentId": row.context["documentId"], "publicationId": row.context["id"], "title": row.context["title"]} if row.context else None}


def native_items(row):
    return [item for turn in row.native_record.get("turns", []) for item in turn.get("items", [])]


def display_items(row):
    result = []
    unrecorded = row.native_record.get("unrecordedInputs", [])
    turns = row.native_record.get("turns", [])
    scopes = row.native_record.get("platformRequests", [])
    def applied(request_id):
        metadata = request_metadata(row, request_id)
        return scope_summary(metadata["scope"]) if metadata else {}
    def add_unrecorded(index):
        for item in unrecorded:
            if item["beforeTurn"] == index or (index == len(turns) and item["beforeTurn"] > index):
                result.append({"id": item["id"], "type": "message", "role": "user", "text": item["text"],
                               "source": "platform", "status": "not_recorded", **applied(item["requestId"])})
    for index, turn in enumerate(turns):
        add_unrecorded(index)
        metadata = next((entry for entry in scopes if entry.get("turnId") == turn.get("id")), None)
        for item in turn.get("items", []):
            projected = display_native_item(item)
            if metadata and item.get("type") == "userMessage":
                for value in projected:
                    value.update(text=metadata["text"], **scope_summary(metadata["scope"]))
            result.extend(projected)
    add_unrecorded(len(turns))
    if row.pending_text:
        result.append({"id": "pending-" + str(row.request_id), "type": "message", "role": "user", "text": row.pending_text,
                       "source": "platform", "status": "pending" if row.state == "running" else "not_recorded", **applied(row.request_id)})
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
            if kind == 'mcpToolCall' and item.get('server') == 'shell' and item.get('tool') == 'shell':
                native_result = item.get('result') or {}
                parts = native_result.get('content', []) if isinstance(native_result, dict) else []
                for part in parts:
                    try:
                        output = json.loads(part.get('text', ''))
                        if isinstance(output, dict) and output.get('status') in {'completed', 'failed', 'timed_out', 'rejected'}:
                            result[-1].update(output=output, status=output['status'])
                            if isinstance(output.get('error'), dict):
                                result[-1]['error'] = output['error']
                    except (ValueError, AttributeError):
                        pass
            # Native calls begin without a result, serialized as explicit null;
            # interrupted/old history can retain that state permanently.
            for part in item.get("contentItems") or []:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    try:
                        output = json.loads(part["text"])
                        if isinstance(output, dict) and output.get("error", {}).get("code") == "tool_not_allowed":
                            result[-1].update(status="rejected", error={"code": "tool_not_allowed"})
                    except (ValueError, AttributeError):
                        pass
        if item.get("modelMismatch") is True:
            for projected in result:
                projected["modelMismatch"] = True
    return result


def register_conversations(app, authorized, sessions, settings, response, write, current_session):
    prefix = settings.base_path + "/api/research"
    app.state.research_runners = runners_from_file()
    app.state.codex_verification = "unverified"
    personal = os.getenv("CODEX_PERSONAL_ACCOUNT_ID", "")
    app.state.codex_personal_owner = uuid.UUID(personal) if personal else None
    app.state.codex_personal_enabled = personal_origin_enabled(settings)
    from .user_runners import register, preflight, selected, runtime_runner
    runner_status = register(app, sessions, settings, current_session)

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
            extra = {}
            if 'accessExpiresAt' in health:
                expiry = health['accessExpiresAt']
                if expiry is not None:
                    if not isinstance(expiry, str) or len(expiry) > 40 or datetime.fromisoformat(expiry.replace('Z', '+00:00')).tzinfo is None:
                        raise ValueError()
                extra['accessExpiresAt'] = expiry
            if 'remainingAdmissions' in health:
                remaining = health['remainingAdmissions']
                if remaining is not None and (type(remaining) is not int or not 0 <= remaining <= 3):
                    raise ValueError()
                extra['remainingAdmissions'] = remaining
            return {**result, **extra, "available": available, "reason": None if available else reason or "unavailable", "verification": verified}
        except (httpx.HTTPError, ValueError, KeyError):
            return {**result, "reason": "unavailable"}

    def require_schema(store):
        if store.db.execute(text("SELECT version_num FROM research.alembic_version")).scalar_one() not in {"0003_sessions", "0004_publication"}:
            fail("not_ready", "Conversation schema is not ready", 503)

    def runner_for(owner, store=None):
        if app.state.user_runners_enabled and not selected(app, owner):
            fail('codex_not_enabled_for_account', 'Runner is not enabled for this account', 503)
        if selected(app, owner):
            if store is not None:
                from .user_runners import registry_status, checked_registry
                lifecycle = registry_status(checked_registry(store.db), owner)
                if lifecycle['reason'] != 'runner_unreachable':
                    preflight(store)
            if app.state.user_runner_runtime:
                return runtime_runner(app, owner)
            if store is not None:
                preflight(store)
            fail("codex_runner_unreachable", "Codex transport is not ready", 503)
        runner = app.state.research_runners.get(owner)
        status = connection_status(owner)
        if not status["available"]:
            fail(ERRORS.get(status["reason"], "codex_unavailable"), "Codex cannot accept this request", 503)
        return runner

    def refresh(store, row):
        if row.state != "running":
            return
        if app.state.user_runners_enabled and not selected(app, store.owner):
            return
        if selected(app, store.owner):
            # Without the bound service transport, preserve accepted uncertainty;
            # legacy static mappings cannot reconcile new runtimes.
            if not app.state.user_runner_runtime:
                return
            runner = app.state.user_runner_runtime.runner(store.owner)
        else:
            runner = app.state.research_runners.get(store.owner)
        try:
            if not runner:
                raise ValueError()
            result = runner.read(row.id)
            if result.get("requestId") != str(row.request_id) or result.get("state") not in {"running", "idle", "failed"}:
                raise ValueError()
            thread_id = result.get("threadId")
            if thread_id is not None and (not isinstance(thread_id, str) or not 1 <= len(thread_id) <= 200):
                raise ValueError()
            expected_thread = row.native_record.get("nativeThreadId")
            if expected_thread is not None and thread_id != expected_thread:
                raise ValueError()
        except (httpx.HTTPError, ValueError, KeyError):
            # Tool-token expiry is not proof of turn termination, including
            # during feature-off recovery. Keep accepted uncertainty intact.
            return
        record = result.get("record")
        if not isinstance(record, dict) or not isinstance(record.get("turns", []), list):
            fail("codex_unavailable", "Codex history is unavailable", 503)
        # Native snapshots cannot overwrite platform-retained failed inputs.
        scopes = row.native_record.get("platformRequests", [])
        turn_id = result.get("turnId")
        if isinstance(turn_id, str) and any(turn.get("id") == turn_id for turn in record.get("turns", [])):
            scopes = [{**entry, "turnId": turn_id} if entry["requestId"] == str(row.request_id) and not entry.get("turnId") else entry for entry in scopes]
        row.native_record = {**record, "unrecordedInputs": row.native_record.get("unrecordedInputs", []), "platformRequests": scopes}
        if thread_id is not None:
            row.native_record["nativeThreadId"] = thread_id  # Server-only mapping, excluded from browser summaries/items.
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
        submitting = False
        try:
            with sessions() as db:
                row = db.scalar(select(Conversation).where(Conversation.id == identity, Conversation.owner_id == owner))
                if not row or row.state != "running" or row.request_id != request_id:
                    return
                metadata = request_metadata(row, request_id)
                payload = {"requestId": str(request_id), "text": row.pending_text,
                           "tools": definitions(), "toolToken": token,
                           "scope": metadata["scope"] if metadata else {"page": {"kind": "general"}, "context": None}}
                if row.native_record.get("nativeThreadId") is not None:
                    payload["expectedThreadId"] = row.native_record["nativeThreadId"]
            runner = runner_for(owner)
            submitting = True
            runner.submit(identity, payload)
        except Exception as exc:
            with sessions.begin() as db:
                row = db.scalar(select(Conversation).where(Conversation.id == identity, Conversation.owner_id == owner).with_for_update())
                if row and row.request_id == request_id and row.state == "running":
                    definite = isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {400, 401, 403, 404, 409, 413, 422, 429}
                    safe_codes = {*ERRORS.values(), *RUNNER_PREFLIGHT_ERRORS.values(), "codex_unavailable"}
                    if not submitting:
                        # No submit call was made. A local preflight failure is
                        # definitive even if its health probe had a network error.
                        code = exc.code if isinstance(exc, ResearchError) and exc.code in safe_codes else "codex_unavailable"
                        row.state, row.error_code = "failed", code
                        row.version += 1
                        row.updated_at = datetime.now(timezone.utc)
                        row.tool_token_hash, row.tool_expires_at = None, None
                    elif definite:
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
        if app.state.user_runners_enabled:
            return runner_status(request)
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
                scope = resolve_scope(store, parsed.page)
                if row.native_record.get("turns") and not row.native_record.get("nativeThreadId") and app.state.codex_verification != "fixture":
                    fail("codex_policy_refused", "Existing conversation binding requires reconciliation", 503)
                runner_for(store.owner, store)
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
                row.native_record = {**row.native_record, "platformRequests": row.native_record.get("platformRequests", []) +
                                     [{"requestId": str(row.request_id), "text": parsed.text, "scope": scope}]}
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
            metadata = request_metadata(row)
            scope = metadata["scope"] if metadata else None
            if body.name == 'runner_shell_authorize':
                from .page_scope import shell_allowed
                if body.arguments != {} or not shell_allowed(scope):
                    fail('tool_not_allowed', 'Shell is unavailable in this request mode', 403)
                return response({'authorized': True})
            if not tool_allowed(scope, body.name, body.arguments):
                fail("tool_not_allowed", "Tool is unavailable in this request's research mode", 403)
            store = Store(db, row.owner_id)
            return response(dispatch(store, body.name, body.arguments, context=scope.get("context")))
