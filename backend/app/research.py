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
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import and_, func, or_, select, text, union_all
from sqlalchemy.exc import IntegrityError

from . import bibliography as csl
from . import relation_kinds
from .models import Account
from .research_models import ContentVersion, Idempotency, Item, Relation

EVIDENCE_LIMIT = 20

RESEARCH_REVISION = "0002_research"


class ResearchError(Exception):
    def __init__(self, code, message, status, details=None):
        self.code, self.message, self.status = code, message, status
        self.details = details


def fail(code, message, status, details=None):
    raise ResearchError(code, message, status, details)


def mutation_fingerprint(operation, payload):
    return hashlib.sha256(json.dumps([operation, payload], sort_keys=True,
        separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def mutation_lock(owner, identity):
    return int.from_bytes(hashlib.sha256((str(owner) + str(identity)).encode()).digest()[:8],
                          'big', signed=True)


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


def nonblank(value):
    if value is not None and not value.strip():
        raise ValueError("Text must not be blank")
    return value


def clean_summary(value):
    """One-line summary: trimmed, 1–300 code points, no line breaks; None clears."""
    if value is None:
        return None
    value = value.strip()
    if not value or len(value) > 300 or "\n" in value or "\r" in value:
        raise ValueError("A one-line summary of at most 300 characters is required")
    return value


class Summarized(Input):
    # Omitted on a new version = carry the latest version's value (see Store.add_snapshot).
    summary: str | None = Field(default=None, max_length=4000)

    @field_validator("summary")
    @classmethod
    def one_line(cls, value):
        return clean_summary(value)


class CslName(Input):
    family: str | None = Field(default=None, min_length=1, max_length=200)
    given: str | None = Field(default=None, min_length=1, max_length=200)
    literal: str | None = Field(default=None, min_length=1, max_length=300)

    @field_validator("family", "given", "literal")
    @classmethod
    def filled(cls, value):
        return nonblank(value)

    @model_validator(mode="after")
    def form(self):
        if self.literal is not None:
            if self.family is not None or self.given is not None:
                raise ValueError("literal names cannot have family/given parts")
        elif self.family is None:
            raise ValueError("A family or literal name is required")
        return self


class CslDate(Input):
    date_parts: list[list[int]] = Field(alias="date-parts", min_length=1, max_length=1)

    @field_validator("date_parts")
    @classmethod
    def real_date(cls, value):
        parts = value[0]
        if not 1 <= len(parts) <= 3 or not 1 <= parts[0] <= 9999:
            raise ValueError("date-parts must be [[year, month?, day?]]")
        try:
            datetime(parts[0], parts[1] if len(parts) > 1 else 1, parts[2] if len(parts) > 2 else 1)
        except ValueError:
            raise ValueError("date-parts must be a real date") from None
        return value


class Bibliography(Input):
    """CSL-JSON subset (contract #105 §4.1). URL/accessed come from sourceUrl/collectedAt."""
    type: Literal[csl.TYPES]
    title: str | None = Field(default=None, min_length=1, max_length=1000)
    author: list[CslName] | None = Field(default=None, min_length=1, max_length=100)
    issued: CslDate | None = None
    container_title: str | None = Field(default=None, alias="container-title", min_length=1, max_length=1000)
    publisher: str | None = Field(default=None, min_length=1, max_length=500)
    volume: str | None = Field(default=None, min_length=1, max_length=50)
    issue: str | None = Field(default=None, min_length=1, max_length=50)
    page: str | None = Field(default=None, min_length=1, max_length=50)
    number: str | None = Field(default=None, min_length=1, max_length=100)
    DOI: str | None = Field(default=None, max_length=256, pattern=r"^10\.\d{4,9}/\S+$")
    ISBN: str | None = Field(default=None, min_length=1, max_length=32)
    ISSN: str | None = Field(default=None, min_length=1, max_length=32)

    @field_validator("title", "container_title", "publisher", "volume", "issue", "page", "number", "ISBN", "ISSN")
    @classmethod
    def filled(cls, value):
        return nonblank(value)

    def stored(self):
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


class DocumentInput(Summarized):
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
    bibliography: Bibliography | None = None

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


class EvidenceInput(Input):
    """Where the relation's reason was checked: a version of its source or target."""
    itemId: str
    versionId: str
    locator: str | None = Field(default=None, min_length=1, max_length=100)
    quote: str | None = Field(default=None, min_length=1, max_length=500)

    @field_validator("itemId", "versionId")
    @classmethod
    def identifier(cls, value):
        return str(parse_id(value))

    @field_validator("locator", "quote")
    @classmethod
    def filled(cls, value):
        return nonblank(value)

    @model_validator(mode="after")
    def located(self):
        if self.locator is None and self.quote is None:
            raise ValueError("Evidence needs a locator or a quote")
        return self


def distinct_evidence(entries):
    keys = [(entry.itemId, entry.versionId, entry.locator, entry.quote) for entry in entries]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate evidence entries")
    return entries


# Vocabulary and the 20-entry limit are checked by Store (with details.reason);
# the schema still advertises them to agents.
KIND_FIELD = dict(min_length=1, max_length=80, json_schema_extra={"enum": list(relation_kinds.KEYS)})
EVIDENCE_SCHEMA = {"maxItems": EVIDENCE_LIMIT}


class RelationInput(Input):
    source: Endpoint
    target: Endpoint
    kind: str = Field(**KIND_FIELD)
    # Blank (including omitted) is rejected by Store as relation_reason_required.
    description: str = Field(default="", max_length=4000)
    directed: bool | None = None  # Derived from kind; when sent it must match.
    evidence: list[EvidenceInput] = Field(default_factory=list, json_schema_extra=EVIDENCE_SCHEMA)

    @field_validator("kind")
    @classmethod
    def clean_kind(cls, value):
        if not value.strip():
            raise ValueError("Relation kind is required")
        return value.strip()

    @field_validator("evidence")
    @classmethod
    def distinct(cls, value):
        return distinct_evidence(value)


class RelationChange(Expected):
    kind: str = Field(**KIND_FIELD)
    description: str = Field(max_length=4000)
    evidence: list[EvidenceInput] | None = Field(default=None, json_schema_extra=EVIDENCE_SCHEMA)  # omitted = keep

    _clean_kind = field_validator("kind")(RelationInput.clean_kind.__func__)

    @field_validator("evidence")
    @classmethod
    def distinct(cls, value):
        if value is None:
            raise ValueError("Omit evidence to keep it; send [] to clear it")
        return distinct_evidence(value)


def parse_id(value):
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        fail("validation_error", "A UUID is required", 422)


def stamp(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def encode_cursor(row, *, time_field="updated_at"):
    return base64.urlsafe_b64encode(json.dumps([stamp(getattr(row, time_field)), str(row.id)]).encode()).decode().rstrip("=")


def page_query(statement, model, limit, cursor, *, time_field="updated_at"):
    if not 1 <= limit <= 100:
        fail("validation_error", "Limit must be between 1 and 100", 422)
    time_column = getattr(model, time_field)
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
        statement = statement.where(or_(time_column < time, and_(time_column == time, model.id < identity)))
    return statement.order_by(time_column.desc(), model.id.desc()).limit(limit + 1)


class Store:
    def __init__(self, db, owner, *, author=None):
        from .version_authors import VersionAuthor
        self.db, self.owner = db, owner
        self.author = author or VersionAuthor()

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
        result["summary"] = row.summary
        if row.kind == "material":
            result.update(sourceUrl=row.source_url, collectedAt=stamp(row.collected_at), contentKind=row.content_kind,
                          bibliography=row.bibliography)
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
        result["summary"] = latest.summary
        if row.kind == "material":
            result.update(sourceUrl=latest.source_url, collectedAt=stamp(latest.collected_at), contentKind=latest.content_kind,
                          bibliography=latest.bibliography)
        else:
            from .publications import owned_publication_info
            result["publication"] = owned_publication_info(self, row)
        return result

    def add_snapshot(self, row, body, previous=None):
        """Append a version. Omitted summary/bibliography carry `previous` (the latest version)."""
        given = body.model_fields_set
        summary = body.summary if "summary" in given else getattr(previous, "summary", None)
        bibliography = None
        if row.kind == "material":
            if "bibliography" in given:
                bibliography = body.bibliography.stored() if body.bibliography is not None else None
            else:
                bibliography = getattr(previous, "bibliography", None)
        self.db.add(ContentVersion(item_id=row.id, owner_id=self.owner, kind=row.kind,
                                  **self.author.columns(),
                                  number=row.content_number, title=body.title, content=body.content,
                                  source_url=getattr(body, "sourceUrl", None),
                                  collected_at=datetime.fromisoformat(body.collectedAt) if row.kind == "material" else None,
                                  content_kind=getattr(body, "contentKind", None),
                                  summary=summary, bibliography=bibliography))
        self.db.flush()

    def latest_version(self, row):
        return self.db.scalar(select(ContentVersion).where(ContentVersion.item_id == row.id,
                              ContentVersion.owner_id == self.owner, ContentVersion.number == row.content_number))

    def create_item(self, kind, body):
        self.graph_lock()
        row = Item(owner_id=self.owner, kind=kind)
        self.db.add(row)
        self.db.flush()
        self.add_snapshot(row, body)
        return self.serialize_item(row)

    def change_item(self, identity, kind, body):
        self.graph_lock()
        row = self.owned_item(identity, kind, active=True, lock=True)
        if row.version != body.expectedVersion:
            fail('conflict', 'The resource changed; reload before retrying', 409,
                 {'reason': 'resource_version', 'current': self.serialize_item(row)})
        previous = self.latest_version(row)
        row.version += 1
        row.content_number += 1
        row.updated_at = datetime.now(timezone.utc)
        self.add_snapshot(row, body, previous)
        return self.serialize_item(row)

    @staticmethod
    def expect(row, expected):
        if row.version != expected:
            fail("conflict", "The resource changed; reload before retrying", 409)

    def archive_item(self, identity, kind, body):
        self.graph_lock()
        row = self.owned_item(identity, kind, active=True, lock=True)
        self.expect(row, body.expectedVersion)
        row.archived, row.version, row.updated_at = True, row.version + 1, datetime.now(timezone.utc)
        self.db.flush()
        return {"id": str(row.id), "archived": True, "version": row.version}

    def items(self, kind, limit=30, cursor=None, archived=False, q=None, sort="updated"):
        from .research_listing import page
        rows, following, total = page(self.db, self.owner, kind, limit, cursor, archived, q, sort)
        counts = self.relation_counts([row.id for row, _ in rows])
        return {"items": [{**self.serialize_item(row, content=False), "relationCount": counts.get(row.id, 0)}
                          for row, _ in rows],
                "nextCursor": following, "total": total}

    def active_relations(self):
        """Owner's active relations whose endpoints are both active (the visible graph)."""
        active = select(Item.id).where(Item.owner_id == self.owner, Item.archived.is_(False))
        return [Relation.owner_id == self.owner, Relation.archived.is_(False),
                Relation.source_id.in_(active), Relation.target_id.in_(active)]

    def relation_counts(self, identities):
        if not identities:
            return {}
        visible = self.active_relations()
        ends = union_all(select(Relation.source_id.label("item")).where(*visible, Relation.source_id.in_(identities)),
                         select(Relation.target_id.label("item")).where(*visible, Relation.target_id.in_(identities))).subquery()
        return dict(self.db.execute(select(ends.c.item, func.count()).group_by(ends.c.item)).all())

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

    def owned_relation(self, identity, lock=False, archived_targets=False):
        query = select(Relation).where(Relation.id == parse_id(identity), Relation.owner_id == self.owner, Relation.archived.is_(False))
        row = self.db.scalar(query.with_for_update() if lock else query)
        if row is None:
            fail("not_found", "Not found", 404)
        # Relations to archived items are inaccessible in the active graph.
        self.owned_item(row.source_id, active=not archived_targets)
        self.owned_item(row.target_id, active=not archived_targets)
        return row

    def serialize_relation(self, row, relative=None):
        source, target = self.owned_item(row.source_id), self.owned_item(row.target_id)
        result = {"id": str(row.id), "source": {"type": source.kind, "id": str(source.id), "title": self.serialize_item(source, False)["title"]},
                  "target": {"type": target.kind, "id": str(target.id), "title": self.serialize_item(target, False)["title"]},
                  "kind": row.kind, "kindKnown": relation_kinds.known(row.kind),
                  "description": row.description, "directed": row.directed, "evidence": list(row.evidence or []),
                  "version": row.version, "createdAt": stamp(row.created_at), "updatedAt": stamp(row.updated_at)}
        if relative is not None:
            result["direction"] = "bidirectional" if not row.directed else "outgoing" if row.source_id == relative else "incoming"
        return result

    @staticmethod
    def reject(reason, message):
        fail("validation_error", message, 422, {"reason": reason})

    def relation_rules(self, kind, description):
        """New saves/updates use the fixed vocabulary and a non-blank reason. Returns kind's direction."""
        if not relation_kinds.known(kind):
            self.reject("relation_kind_unknown", "Choose a relation kind from the vocabulary")
        if not description.strip():
            self.reject("relation_reason_required", "Describe why the items are related")
        return relation_kinds.DIRECTED[kind]

    def evidence_values(self, entries, endpoints):
        """Evidence may cite only versions of the relation's own (owned) endpoints."""
        if len(entries) > EVIDENCE_LIMIT:
            self.reject("evidence_limit_exceeded", f"At most {EVIDENCE_LIMIT} evidence entries are allowed")
        values = []
        for entry in entries:
            item = parse_id(entry.itemId)
            if item not in endpoints:
                self.reject("evidence_endpoint_mismatch", "Evidence must cite the relation's source or target")
            version = self.db.scalar(select(ContentVersion).where(ContentVersion.id == parse_id(entry.versionId),
                                     ContentVersion.item_id == item, ContentVersion.owner_id == self.owner))
            if version is None:
                self.reject("evidence_version_not_found", "Evidence must cite a version of that item")
            values.append({"itemId": entry.itemId, "versionId": entry.versionId, "versionNumber": version.number,
                           "locator": entry.locator, "quote": entry.quote})
        return values

    def create_relation(self, body):
        self.graph_lock()
        directed = self.relation_rules(body.kind, body.description)
        if body.directed is not None and body.directed is not directed:
            self.reject("relation_direction_mismatch", "The relation kind decides its direction")
        if body.source.id == body.target.id:
            fail("validation_error", "Self relations are not allowed", 422)
        if body.source.type != body.target.type and not (body.source.type == "document" and body.target.type == "material" and directed):
            self.reject("relation_direction_mismatch", "Document-material relations go from the document with a directed kind")
        # Deterministic item-lock order serializes archive vs relation creation.
        endpoints = sorted([body.source, body.target], key=lambda value: value.id)
        for endpoint in endpoints:
            self.owned_item(endpoint.id, endpoint.type, active=True, lock=True)
        evidence = self.evidence_values(body.evidence, {parse_id(body.source.id), parse_id(body.target.id)})
        source, target = (body.source, body.target) if directed else endpoints
        row = Relation(owner_id=self.owner, source_id=parse_id(source.id), source_kind=source.type,
                       target_id=parse_id(target.id), target_kind=target.type,
                       kind=body.kind, description=body.description, directed=directed, evidence=evidence)
        self.db.add(row)
        self.db.flush()
        return self.serialize_relation(row)

    def change_relation(self, identity, body, archive=False):
        self.graph_lock()
        row = self.owned_relation(identity, lock=True, archived_targets=archive)
        self.expect(row, body.expectedVersion)
        if not archive:
            # Legacy free-text relations must move to the vocabulary on any edit;
            # a relation's direction and endpoints never change.
            if self.relation_rules(body.kind, body.description) is not row.directed:
                self.reject("relation_direction_mismatch", "Choose a kind with this relation's direction")
            if body.evidence is not None:
                row.evidence = self.evidence_values(body.evidence, {row.source_id, row.target_id})
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

    def graph_lock(self):
        value = int.from_bytes(hashlib.sha256((str(self.owner) + "research-graph-v1").encode()).digest()[:8], "big", signed=True)
        self.db.execute(text("SELECT pg_advisory_xact_lock(:lock)"), {"lock": value})

    def mutate(self, key, operation, payload, action, status=200):
        identity = parse_id(key)
        fingerprint = mutation_fingerprint(operation, payload)
        lock = mutation_lock(self.owner, identity)
        self.db.execute(text("SELECT pg_advisory_xact_lock(:lock)"), {"lock": lock})
        stored = self.db.get(Idempotency, (self.owner, identity))
        if stored:
            if not hmac.compare_digest(stored.fingerprint, fingerprint):
                fail("idempotency_conflict", "This key was used for a different request", 409)
            from .version_authors import without_version_authors
            return without_version_authors(stored.response), stored.status
        result = action()
        self.db.add(Idempotency(owner_id=self.owner, key=identity, fingerprint=fingerprint, status=status, response=result))
        self.db.flush()
        return result, status


def register_research(app, sessions, settings, current_session, error):
    from .research_credentials import enabled, redact
    app.state.research_agent_api_enabled = enabled()
    if app.state.research_agent_api_enabled:
        origin = urlsplit(settings.origin)
        if origin.scheme != 'https' or origin.port not in {None, 443}:
            raise ValueError('Research agent API requires public HTTPS443')
    policy = os.getenv("RESEARCH_ACCESS_POLICY", "editors")
    if policy not in {"pending", "approved", "editors"}:
        raise ValueError("Invalid research access policy")
    app.state.research_policy = policy
    prefix = settings.base_path + "/api/research"

    @app.exception_handler(ResearchError)
    async def domain_error(request, exc):
        if exc.details is not None:
            from .version_authors import without_version_authors
            return JSONResponse(redact(without_version_authors({'error': {'code': exc.code, 'message': exc.message, 'details': exc.details}})),
                                status_code=exc.status, headers={'Cache-Control': 'no-store'})
        result = error(exc.code, exc.message, exc.status)
        if exc.status == 429:
            result.headers['Retry-After'] = '60'
        return result

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
    def authorized(request, mutate=False, membership=False):
        # Authorization and CSRF checks precede any cached idempotency response.
        if not request.cookies.get("anxious_session"):
            fail("unauthenticated", "Authentication required", 401)
        with sessions.begin() as db:
            login = current_session(db, request)
            if not login:
                fail("unauthenticated", "Authentication required", 401)
            # Order membership revocation against already-admitted operations.
            # Membership writes acquire the actor exclusively from the outset,
            # avoiding a SHARE→UPDATE lock conversion between admin requests.
            account = db.scalar(select(Account).where(Account.id == login.account_id).with_for_update(read=not membership))
            if not current_session(db, request):
                fail("unauthenticated", "Authentication required", 401)
            if not account or not account.is_approved:
                fail("forbidden", "Research access is not permitted", 403)
            if app.state.research_policy == "pending":
                fail("policy_pending", "Research access policy is pending", 503)
            if account.role not in {"editor", "admin"}:
                fail("forbidden", "Research access is not permitted", 403)
            if mutate:
                if request.headers.get("origin") != settings.origin:
                    fail("origin_not_allowed", "Origin not allowed", 403)
                if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), login.csrf_token):
                    fail("csrf_invalid", "Invalid CSRF token", 403)
            from .research_schema_contract import validate_research
            try:
                if validate_research(db.connection()) != '0011_ui_feedback':
                    raise RuntimeError('Version provenance schema is required')
            except RuntimeError:
                fail("not_ready", "Research schema is not ready", 503)
            yield Store(db, account.id)

    app.state.research_authorized = authorized

    @app.get(prefix + "/relation-kinds")
    def relation_kind_catalog():
        # Static vocabulary (#105 W2): no session, DB or research flag involved.
        return JSONResponse(relation_kinds.catalog(), headers={"Cache-Control": "no-store"})

    @app.get(prefix + "/health")
    def research_health():
        from .research_schema_contract import validate_research
        with sessions() as db:
            try:
                if validate_research(db.connection()) != '0011_ui_feedback':
                    raise RuntimeError('Version provenance schema is required')
            except RuntimeError:
                fail("not_ready", "Research schema is not ready", 503)
        return JSONResponse({"status": "ok"}, headers={"Cache-Control": "no-store"})

    def response(result, status=200):
        from .version_authors import without_version_authors
        return JSONResponse(redact(without_version_authors(result)), status_code=status, headers={"Cache-Control": "no-store"})

    def write(store, request, model, body, action, status=200):
        from .research_credentials import reject_token_input
        reject_token_input(body)
        post_route = any(segment in request.url.path for segment in ("/post-drafts", "/post-proposals", "/posts/"))
        if post_route:
            from .posts import validate_retry_encoding
            validate_retry_encoding(body, model)
        # Post wire shapes changed in 0007. Exact successful retries must reach
        # the owner-scoped fingerprint check before validating a new write.
        parsed = None if post_route else model.model_validate(body)
        key = request.headers.get("idempotency-key")
        if not key:
            fail("validation_error", "Idempotency-Key is required", 422)
        def apply_new_write():
            if post_route:
                from .posts import PostText
                from .writing import DraftCommit
                if issubclass(model, (PostText, DraftCommit)) and request.headers.get('x-post-contract-revision') != '1.1':
                    fail('post_contract_upgrade_required', 'Reload with a compatible post editor before saving', 409,
                         {'requiredRevision': '1.1'})
            return action(model.model_validate(body) if post_route else parsed)
        result, code = store.mutate(key, request.method + " " + request.url.path, body,
                                    apply_new_write, status)
        if post_route:
            from .posts import replay_response
            result = replay_response(result)
        return response(result, code)

    def item_routes(kind):
        def list_items(request: Request, limit: int = 30, cursor: str | None = None, archived: bool = False,
                       q: str | None = None, sort: str = "updated"):
            with authorized(request) as store:
                return response(store.items(kind, limit, cursor, archived, q, sort))

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

    from .writing import register_writing
    register_writing(app, authorized, settings, response, write)
    from .posts import register_posts
    register_posts(app, authorized, settings, response, write)
    from .post_materials import register as register_post_materials
    register_post_materials(app, authorized, settings, response)
    from .post_images import register as register_images
    register_images(app, authorized, sessions, settings, response, write)
    from .post_proposals import register as register_post_proposals
    register_post_proposals(app, authorized, settings, response, write)
    from .document_proposals import register as register_document_proposals
    register_document_proposals(app, authorized, settings, response, write)
    from .post_publications import register as register_post_publications
    register_post_publications(app, authorized, sessions, settings, response, write)
    from .publications import register_publications
    register_publications(app, authorized, sessions, settings, response, write)
    from .membership import register_membership
    register_membership(app, authorized, settings, response, write)
    from .conversations import register_conversations
    register_conversations(app, authorized, sessions, settings, response, write, current_session)
    from .pat_management import register as register_pats
    register_pats(app, sessions, settings, current_session, response)
    from .url_metadata import register as register_url_metadata
    register_url_metadata(app, authorized, settings, response)
    from .research_rate import install as install_research_rate
    install_research_rate(app, sessions, settings)
    from .pat_distribution import register as register_pat_distribution
    register_pat_distribution(app, settings, response)
