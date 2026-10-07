"""Owner-scoped storage shared by HTTP handlers and Codex tools."""
import base64
import hashlib
import hmac
import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import and_, or_, select, text
from sqlalchemy.exc import IntegrityError

from .models import Account
from .research_models import ContentVersion, Idempotency, Item, Relation

RESEARCH_REVISION = "0002_research"


class ResearchError(Exception):
    def __init__(self, code, message, status):
        self.code, self.message, self.status = code, message, status


def fail(code, message, status):
    raise ResearchError(code, message, status)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("*", mode="before")
    @classmethod
    def postgres_text(cls, value):
        if isinstance(value, str):
            if "\x00" in value:
                raise ValueError("NUL is not a valid stored text character")
            try:
                value.encode("utf-8")
            except UnicodeEncodeError:
                raise ValueError("Valid UTF-8 text is required") from None
        return value


class DocumentInput(Input):
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=200000)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value):
        if not value.strip():
            raise ValueError("Title is required")
        return value.strip()


class MaterialInput(DocumentInput):
    sourceUrl: str = Field(min_length=1, max_length=4096)
    collectedAt: str
    contentKind: Literal["full", "excerpt", "summary"]

    @field_validator("sourceUrl")
    @classmethod
    def source(cls, value):
        try:
            parsed = urlsplit(value)
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                    or parsed.username or parsed.password or any(c.isspace() for c in value)):
                raise ValueError()
            _ = parsed.port
        except ValueError:
            raise ValueError("An absolute HTTP(S) source URL is required") from None
        return value

    @field_validator("collectedAt")
    @classmethod
    def timestamp(cls, value):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError()
            normalized = parsed.astimezone(timezone.utc).isoformat()
        except (ValueError, OverflowError):
            raise ValueError("A timezone-aware collection timestamp is required") from None
        return normalized


class Expected(Input):
    expectedVersion: int = Field(ge=1)


class DocumentChange(DocumentInput, Expected):
    pass


class MaterialChange(MaterialInput, Expected):
    pass


class Endpoint(Input):
    type: Literal["material", "document"]
    id: str

    @field_validator("id")
    @classmethod
    def identifier(cls, value):
        return str(parse_id(value))


class RelationInput(Input):
    source: Endpoint
    target: Endpoint
    kind: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=4000)
    directed: bool

    @field_validator("kind")
    @classmethod
    def clean_kind(cls, value):
        if not value.strip():
            raise ValueError("Relation kind is required")
        return value.strip()


class RelationChange(Expected):
    kind: str = Field(min_length=1, max_length=80)
    description: str = Field(max_length=4000)

    _clean_kind = field_validator("kind")(RelationInput.clean_kind.__func__)


def parse_id(value):
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        fail("validation_error", "A UUID is required", 422)


def stamp(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def encode_cursor(row):
    return base64.urlsafe_b64encode(json.dumps([stamp(row.updated_at), str(row.id)]).encode()).decode().rstrip("=")


def page_query(statement, model, limit, cursor):
    if not 1 <= limit <= 100:
        fail("validation_error", "Limit must be between 1 and 100", 422)
    if cursor:
        try:
            if len(cursor) > 512:
                raise ValueError()
            values = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
            if not isinstance(values, list) or len(values) != 2 or not all(isinstance(value, str) for value in values):
                raise ValueError()
            time, identity = values
            time = datetime.fromisoformat(time.replace("Z", "+00:00"))
            if time.tzinfo is None:
                raise ValueError()
            time = time.astimezone(timezone.utc)
            identity = uuid.UUID(identity)
        except (ValueError, TypeError, UnicodeError, OverflowError):
            fail("validation_error", "Invalid cursor", 422)
        statement = statement.where(or_(model.updated_at < time, and_(model.updated_at == time, model.id < identity)))
    return statement.order_by(model.updated_at.desc(), model.id.desc()).limit(limit + 1)


class Store:
    def __init__(self, db, owner):
        self.db, self.owner = db, owner

    def owned_item(self, identity, kind=None, active=False, lock=False):
        query = select(Item).where(Item.id == parse_id(identity), Item.owner_id == self.owner)
        if kind:
            query = query.where(Item.kind == kind)
        if active:
            query = query.where(Item.archived.is_(False))
        row = self.db.scalar(query.with_for_update() if lock else query)
        if row is None:
            fail("not_found", "Not found", 404)
        return row

    def content_version(self, identity, item):
        row = self.db.scalar(select(ContentVersion).where(
            ContentVersion.id == parse_id(identity), ContentVersion.item_id == item.id,
            ContentVersion.owner_id == self.owner))
        if row is None:
            fail("not_found", "Not found", 404)
        return row

    def serialize_version(self, row, content=True):
        result = {"id": str(row.id), "itemId": str(row.item_id), "number": row.number,
                  "title": row.title, "createdAt": stamp(row.created_at)}
        if content:
            result["content"] = row.content
        if row.kind == "material":
            result.update(sourceUrl=row.source_url, collectedAt=stamp(row.collected_at), contentKind=row.content_kind)
        return result

    def serialize_item(self, row, content=True):
        latest = self.db.scalar(select(ContentVersion).where(ContentVersion.item_id == row.id,
                                 ContentVersion.owner_id == self.owner, ContentVersion.number == row.content_number))
        result = {"id": str(row.id), "type": row.kind, "title": latest.title,
                  "createdAt": stamp(row.created_at), "updatedAt": stamp(row.updated_at),
                  "version": row.version, "archived": row.archived,
                  "latestVersion": {"id": str(latest.id), "number": latest.number, "createdAt": stamp(latest.created_at)}}
        if content:
            result["content"] = latest.content
        if row.kind == "material":
            result.update(sourceUrl=latest.source_url, collectedAt=stamp(latest.collected_at), contentKind=latest.content_kind)
        else:
            result["publication"] = None  # Publication policy remains pending.
        return result

    def add_snapshot(self, row, body):
        self.db.add(ContentVersion(item_id=row.id, owner_id=self.owner, kind=row.kind,
                                  number=row.content_number, title=body.title, content=body.content,
                                  source_url=getattr(body, "sourceUrl", None),
                                  collected_at=datetime.fromisoformat(body.collectedAt) if row.kind == "material" else None,
                                  content_kind=getattr(body, "contentKind", None)))
        self.db.flush()

    def create_item(self, kind, body):
        row = Item(owner_id=self.owner, kind=kind)
        self.db.add(row)
        self.db.flush()
        self.add_snapshot(row, body)
        return self.serialize_item(row)

    def change_item(self, identity, kind, body):
        row = self.owned_item(identity, kind, active=True, lock=True)
        self.expect(row, body.expectedVersion)
        row.version += 1
        row.content_number += 1
        row.updated_at = datetime.now(timezone.utc)
        self.add_snapshot(row, body)
        return self.serialize_item(row)

    @staticmethod
    def expect(row, expected):
        if row.version != expected:
            fail("conflict", "The resource changed; reload before retrying", 409)

    def archive_item(self, identity, kind, body):
        row = self.owned_item(identity, kind, active=True, lock=True)
        self.expect(row, body.expectedVersion)
        row.archived, row.version, row.updated_at = True, row.version + 1, datetime.now(timezone.utc)
        self.db.flush()
        return {"id": str(row.id), "archived": True, "version": row.version}

    def items(self, kind, limit=30, cursor=None, archived=False):
        query = select(Item).where(Item.owner_id == self.owner, Item.kind == kind, Item.archived == archived)
        rows = self.db.scalars(page_query(query, Item, limit, cursor)).all()
        return {"items": [self.serialize_item(row, content=False) for row in rows[:limit]],
                "nextCursor": encode_cursor(rows[limit - 1]) if len(rows) > limit else None}

    def versions(self, identity, kind, limit=30, cursor=None):
        item = self.owned_item(identity, kind)
        if not 1 <= limit <= 100:
            fail("validation_error", "Invalid limit", 422)
        query = select(ContentVersion).where(ContentVersion.item_id == item.id, ContentVersion.owner_id == self.owner)
        if cursor:
            try:
                number = int(cursor)
                if number < 1:
                    raise ValueError()
            except ValueError:
                fail("validation_error", "Invalid cursor", 422)
            query = query.where(ContentVersion.number < number)
        rows = self.db.scalars(query.order_by(ContentVersion.number.desc()).limit(limit + 1)).all()
        return {"items": [self.serialize_version(row, content=False) for row in rows[:limit]],
                "nextCursor": str(rows[limit - 1].number) if len(rows) > limit else None}

    def owned_relation(self, identity, lock=False):
        query = select(Relation).where(Relation.id == parse_id(identity), Relation.owner_id == self.owner, Relation.archived.is_(False))
        row = self.db.scalar(query.with_for_update() if lock else query)
        if row is None:
            fail("not_found", "Not found", 404)
        # Relations to archived items are inaccessible in the active graph.
        self.owned_item(row.source_id, active=True)
        self.owned_item(row.target_id, active=True)
        return row

    def serialize_relation(self, row, relative=None):
        source, target = self.owned_item(row.source_id), self.owned_item(row.target_id)
        result = {"id": str(row.id), "source": {"type": source.kind, "id": str(source.id), "title": self.serialize_item(source, False)["title"]},
                  "target": {"type": target.kind, "id": str(target.id), "title": self.serialize_item(target, False)["title"]},
                  "kind": row.kind, "description": row.description, "directed": row.directed,
                  "version": row.version, "createdAt": stamp(row.created_at), "updatedAt": stamp(row.updated_at)}
        if relative is not None:
            result["direction"] = "bidirectional" if not row.directed else "outgoing" if row.source_id == relative else "incoming"
        return result

    def create_relation(self, body):
        if body.source.id == body.target.id:
            fail("validation_error", "Self relations are not allowed", 422)
        if body.source.type != body.target.type and not (body.source.type == "document" and body.target.type == "material" and body.directed):
            fail("validation_error", "Invalid relation direction or target types", 422)
        # Deterministic item-lock order serializes archive vs relation creation.
        endpoints = sorted([body.source, body.target], key=lambda value: value.id)
        for endpoint in endpoints:
            self.owned_item(endpoint.id, endpoint.type, active=True, lock=True)
        source, target = (body.source, body.target) if body.directed else endpoints
        row = Relation(owner_id=self.owner, source_id=parse_id(source.id), source_kind=source.type,
                       target_id=parse_id(target.id), target_kind=target.type,
                       kind=body.kind, description=body.description, directed=body.directed)
        self.db.add(row)
        self.db.flush()
        return self.serialize_relation(row)

    def change_relation(self, identity, body, archive=False):
        row = self.owned_relation(identity, lock=True)
        self.expect(row, body.expectedVersion)
        row.version += 1
        row.updated_at = datetime.now(timezone.utc)
        if archive:
            row.archived = True
        else:
            row.kind, row.description = body.kind, body.description
        self.db.flush()
        return {"id": str(row.id), "archived": True, "version": row.version} if archive else self.serialize_relation(row)

    def relations(self, identity, kind, limit=30, cursor=None):
        item = self.owned_item(identity, kind, active=True)
        active = select(Item.id).where(Item.owner_id == self.owner, Item.archived.is_(False))
        query = select(Relation).where(Relation.owner_id == self.owner, Relation.archived.is_(False),
                    or_(Relation.source_id == item.id, Relation.target_id == item.id),
                    Relation.source_id.in_(active), Relation.target_id.in_(active))
        rows = self.db.scalars(page_query(query, Relation, limit, cursor)).all()
        return {"items": [self.serialize_relation(row, item.id) for row in rows[:limit]],
                "nextCursor": encode_cursor(rows[limit - 1]) if len(rows) > limit else None}

    def mutate(self, key, operation, payload, action, status=200):
        identity = parse_id(key)
        fingerprint = hashlib.sha256(json.dumps([operation, payload], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        lock = int.from_bytes(hashlib.sha256((str(self.owner) + str(identity)).encode()).digest()[:8], "big", signed=True)
        self.db.execute(text("SELECT pg_advisory_xact_lock(:lock)"), {"lock": lock})
        stored = self.db.get(Idempotency, (self.owner, identity))
        if stored:
            if not hmac.compare_digest(stored.fingerprint, fingerprint):
                fail("idempotency_conflict", "This key was used for a different request", 409)
            return stored.response, stored.status
        result = action()
        self.db.add(Idempotency(owner_id=self.owner, key=identity, fingerprint=fingerprint, status=status, response=result))
        self.db.flush()
        return result, status


def register_research(app, sessions, settings, current_session, error):
    policy = os.getenv("RESEARCH_ACCESS_POLICY", "pending")
    if policy not in {"pending", "approved", "editors"}:
        raise ValueError("Invalid research access policy")
    app.state.research_policy = policy
    prefix = settings.base_path + "/api/research"

    @app.exception_handler(ResearchError)
    async def domain_error(request, exc):
        return error(exc.code, exc.message, exc.status)

    @app.exception_handler(ValidationError)
    async def validation_error(request, exc):
        # Never include the rejected content or auth inputs in an error response.
        return error("validation_error", "Invalid request fields", 422)

    @app.exception_handler(IntegrityError)
    async def integrity_error(request, exc):
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", "")
        if constraint == "research_relation_unique":
            return error("duplicate", "This relation already exists", 409)
        return error("not_ready", "The change could not be saved", 503)

    @contextmanager
    def authorized(request, mutate=False):
        # Authorization and CSRF checks precede any cached idempotency response.
        if not request.cookies.get("anxious_session"):
            fail("unauthenticated", "Authentication required", 401)
        with sessions.begin() as db:
            login = current_session(db, request)
            if not login:
                fail("unauthenticated", "Authentication required", 401)
            account = db.get(Account, login.account_id)
            if not account or not account.is_approved:
                fail("forbidden", "Research access is not permitted", 403)
            if app.state.research_policy == "pending":
                fail("policy_pending", "Research access policy is pending", 503)
            if app.state.research_policy == "editors" and account.role not in {"editor", "admin"}:
                fail("forbidden", "Research access is not permitted", 403)
            if mutate:
                if request.headers.get("origin") != settings.origin:
                    fail("origin_not_allowed", "Origin not allowed", 403)
                if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), login.csrf_token):
                    fail("csrf_invalid", "Invalid CSRF token", 403)
            from .research_schema_contract import validate_research
            try:
                validate_research(db.connection())
            except RuntimeError:
                fail("not_ready", "Research schema is not ready", 503)
            yield Store(db, account.id)

    app.state.research_authorized = authorized

    @app.get(prefix + "/health")
    def research_health():
        from .research_schema_contract import validate_research
        with sessions() as db:
            try:
                validate_research(db.connection())
            except RuntimeError:
                fail("not_ready", "Research schema is not ready", 503)
        return JSONResponse({"status": "ok"}, headers={"Cache-Control": "no-store"})

    def response(result, status=200):
        return JSONResponse(result, status_code=status, headers={"Cache-Control": "no-store"})

    def write(store, request, model, body, action, status=200):
        parsed = model.model_validate(body)
        key = request.headers.get("idempotency-key")
        if not key:
            fail("validation_error", "Idempotency-Key is required", 422)
        result, code = store.mutate(key, request.method + " " + request.url.path, body, lambda: action(parsed), status)
        return response(result, code)

    def item_routes(kind):
        def list_items(request: Request, limit: int = 30, cursor: str | None = None, archived: bool = False):
            with authorized(request) as store:
                return response(store.items(kind, limit, cursor, archived))

        def detail(identity: uuid.UUID, request: Request):
            with authorized(request) as store:
                return response(store.serialize_item(store.owned_item(identity, kind)))

        def create(body: dict, request: Request):
            with authorized(request, True) as store:
                return write(store, request, MaterialInput if kind == "material" else DocumentInput, body,
                             lambda parsed: store.create_item(kind, parsed), 201)

        def change(identity: uuid.UUID, body: dict, request: Request):
            with authorized(request, True) as store:
                return write(store, request, MaterialChange if kind == "material" else DocumentChange, body,
                             lambda parsed: store.change_item(identity, kind, parsed))

        def archive(identity: uuid.UUID, body: dict, request: Request):
            with authorized(request, True) as store:
                return write(store, request, Expected, body, lambda parsed: store.archive_item(identity, kind, parsed))

        def versions(identity: uuid.UUID, request: Request, limit: int = 30, cursor: str | None = None):
            with authorized(request) as store:
                return response(store.versions(identity, kind, limit, cursor))

        def version(identity: uuid.UUID, version_id: uuid.UUID, request: Request):
            with authorized(request) as store:
                return response(store.serialize_version(store.content_version(version_id, store.owned_item(identity, kind))))

        def relations(identity: uuid.UUID, request: Request, limit: int = 30, cursor: str | None = None):
            with authorized(request) as store:
                return response(store.relations(identity, kind, limit, cursor))

        root = prefix + ("/materials" if kind == "material" else "/documents")
        for path, method, handler in [(root, "GET", list_items), (root, "POST", create),
              (root + "/{identity}", "GET", detail), (root + "/{identity}", "PATCH", change),
              (root + "/{identity}", "DELETE", archive), (root + "/{identity}/versions", "GET", versions),
              (root + "/{identity}/versions/{version_id}", "GET", version), (root + "/{identity}/relations", "GET", relations)]:
            app.add_api_route(path, handler, methods=[method])

    item_routes("material")
    item_routes("document")

    @app.post(prefix + "/relations")
    def create_relation(body: dict, request: Request):
        with authorized(request, True) as store:
            return write(store, request, RelationInput, body, store.create_relation, 201)

    @app.get(prefix + "/relations/{identity}")
    def relation_detail(identity: uuid.UUID, request: Request):
        with authorized(request) as store:
            return response(store.serialize_relation(store.owned_relation(identity)))

    @app.patch(prefix + "/relations/{identity}")
    def change_relation(identity: uuid.UUID, body: dict, request: Request):
        with authorized(request, True) as store:
            return write(store, request, RelationChange, body, lambda parsed: store.change_relation(identity, parsed))

    @app.delete(prefix + "/relations/{identity}")
    def archive_relation(identity: uuid.UUID, body: dict, request: Request):
        with authorized(request, True) as store:
            return write(store, request, Expected, body, lambda parsed: store.change_relation(identity, parsed, archive=True))

    @app.post(prefix + "/documents/{identity}/publications")
    @app.delete(prefix + "/documents/{identity}/publication")
    def publication_pending(identity: uuid.UUID, request: Request):
        with authorized(request, True) as store:
            store.owned_item(identity, "document", active=True)
            fail("policy_pending", "Publication policy is pending", 503)

    @app.get(prefix + "/codex/status")
    def codex_status(request: Request):
        with authorized(request):
            return response({"available": False, "reason": "not_configured", "verification": "unverified"})
