"""Current-only immutable public bundles; references come from direct relations."""
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone

from fastapi import Request
from pydantic import Field
from sqlalchemy import select

from .models import Account
from .research import Expected, Input, fail, parse_id, stamp, page_query, encode_cursor
from .research_models import ContentVersion, Item, Publication, PublicationHead, Relation


class Publish(Expected):
    versionId: str
    previewToken: str = Field(min_length=64, max_length=64)


def require_publication_schema(db):
    from .research_schema_contract import validate_research
    try:
        marker = validate_research(db.connection())
    except RuntimeError:
        fail("not_ready", "Publication schema or authority is not ready", 503)
    if marker != "0004_publication":
        fail("not_ready", "Publication capability is not ready", 503)


def current_publication(db, document_id=None, publication_id=None, lock=False):
    query = select(Publication).join(PublicationHead, PublicationHead.publication_id == Publication.id)
    if document_id is not None:
        query = query.where(Publication.document_id == parse_id(document_id))
    if publication_id is not None:
        query = query.where(Publication.id == parse_id(publication_id))
    if lock:
        query = query.with_for_update(read=True, of=PublicationHead)
    result = db.scalar(query)
    if not result:
        fail("not_found", "Not found", 404)
    return result


def owned_publication_info(store, item):
    # Storage-only0002/0003 remains readable before the explicit new migration.
    from sqlalchemy import text
    marker = store.db.execute(text("SELECT version_num FROM research.alembic_version")).scalar_one()
    if marker != "0004_publication":
        return None
    row = store.db.scalar(select(Publication).join(PublicationHead, PublicationHead.publication_id == Publication.id)
                           .where(Publication.document_id == item.id, Publication.owner_id == store.owner))
    if not row:
        return None
    materials = [{"id": value["id"], "versionId": value["versionId"]} for value in row.snapshot["materials"]]
    return {"id": str(row.id), "state": "published", "versionId": str(row.version_id),
            "publishedAt": row.snapshot["publishedAt"], "materialIds": [value["id"] for value in materials], "materials": materials}


def preview(store, identity, version_id=None):
    require_publication_schema(store.db)
    store.graph_lock()
    item = store.owned_item(identity, "document", active=True, lock=True)
    document = store.content_version(version_id, item) if version_id else store.db.scalar(select(ContentVersion).where(
        ContentVersion.item_id == item.id, ContentVersion.number == item.content_number, ContentVersion.owner_id == store.owner))
    relations = store.db.scalars(select(Relation).where(Relation.owner_id == store.owner, Relation.source_id == item.id,
        Relation.source_kind == "document", Relation.target_kind == "material", Relation.archived.is_(False)).order_by(Relation.id)).all()
    targets = {}
    for relation in relations:
        targets.setdefault(relation.target_id, []).append({"id": str(relation.id), "version": relation.version})
    materials = []
    for target_id in sorted(targets):
        target = store.owned_item(target_id, "material", lock=True)
        content = store.db.scalar(select(ContentVersion).where(ContentVersion.item_id == target.id,
                                 ContentVersion.owner_id == store.owner, ContentVersion.number == target.content_number))
        value = store.serialize_version(content)
        value.update(id=str(target.id), versionId=str(content.id), archived=target.archived, relations=targets[target.id])
        value.pop("itemId")
        materials.append(value)
    state = {"owner": str(store.owner), "document": str(item.id), "version": str(document.id), "materials": materials}
    token = hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    return {"documentId": str(item.id), "versionId": str(document.id), "title": document.title, "content": document.content,
            "expectedVersion": item.version, "previewToken": token, "publishable": all(not value["archived"] for value in materials), "materials": materials}


def publish(store, identity, body):
    shown = preview(store, identity, body.versionId)
    item = store.owned_item(identity, "document", active=True, lock=True)
    store.expect(item, body.expectedVersion)
    if not hmac.compare_digest(shown["previewToken"], body.previewToken):
        fail("conflict", "Publication references changed; preview again", 409)
    if not shown["publishable"]:
        fail("validation_error", "Remove archived reference relations before publishing", 422)
    identity = uuid.uuid4()
    when = datetime.now(timezone.utc)
    materials = [{key: value for key, value in material.items() if key not in {"relations", "archived", "createdAt"}}
                 for material in shown["materials"]]
    snapshot = {"id": str(identity), "documentId": str(item.id), "versionId": shown["versionId"], "title": shown["title"],
                "content": shown["content"], "publishedAt": stamp(when), "materials": materials,
                "author": {"login": store.db.get(Account, store.owner).login}}
    row = Publication(id=identity, document_id=item.id, owner_id=store.owner,
                      version_id=parse_id(shown["versionId"]), snapshot=snapshot, created_at=when, updated_at=when)
    store.db.add(row)
    store.db.flush()
    head = store.db.get(PublicationHead, item.id)
    if head:
        head.publication_id = identity
    else:
        store.db.add(PublicationHead(document_id=item.id, owner_id=store.owner, publication_id=identity))
    item.version += 1
    item.updated_at = when
    store.db.flush()
    return snapshot


def withdraw(store, identity, body):
    require_publication_schema(store.db)
    store.graph_lock()
    item = store.owned_item(identity, "document", lock=True)
    store.expect(item, body.expectedVersion)
    head = store.db.get(PublicationHead, item.id)
    if not head or head.publication_id is None:
        fail("not_found", "No current publication", 404)
    head.publication_id = None
    item.version += 1
    item.updated_at = datetime.now(timezone.utc)
    store.db.flush()
    return {"id": str(item.id), "publication": None, "version": item.version}


def register_publications(app, authorized, sessions, settings, response, write):
    private = settings.base_path + "/api/research/documents"
    public = settings.base_path + "/api/public"

    @app.get(private + "/{identity}/publication-preview")
    def publication_preview(identity: uuid.UUID, request: Request, versionId: uuid.UUID | None = None):
        with authorized(request) as store:
            return response(preview(store, identity, versionId))

    @app.post(private + "/{identity}/publications")
    def publication_create(identity: uuid.UUID, body: dict, request: Request):
        with authorized(request, True) as store:
            return write(store, request, Publish, body, lambda parsed: publish(store, identity, parsed), 201)

    @app.delete(private + "/{identity}/publication")
    def publication_withdraw(identity: uuid.UUID, body: dict, request: Request):
        with authorized(request, True) as store:
            return write(store, request, Expected, body, lambda parsed: withdraw(store, identity, parsed))

    @app.get(public + "/documents")
    def public_list(limit: int = 30, cursor: str | None = None):
        with sessions() as db:
            require_publication_schema(db)
            query = select(Publication).join(PublicationHead, PublicationHead.publication_id == Publication.id)
            rows = db.scalars(page_query(query, Publication, limit, cursor)).all()
            return response({"items": [{key: row.snapshot[key] for key in ("id", "documentId", "title", "publishedAt", "author")}
                                       for row in rows[:limit]], "nextCursor": encode_cursor(rows[limit - 1]) if len(rows) > limit else None})

    @app.get(public + "/documents/{identity}")
    def public_document(identity: uuid.UUID):
        with sessions() as db:
            require_publication_schema(db)
            return response(current_publication(db, document_id=identity).snapshot)

    @app.get(public + "/releases/{identity}")
    def public_release(identity: uuid.UUID):
        with sessions() as db:
            require_publication_schema(db)
            return response(current_publication(db, publication_id=identity).snapshot)

    @app.get(public + "/documents/{identity}/materials/{material_id}")
    def public_material(identity: uuid.UUID, material_id: uuid.UUID, publicationId: uuid.UUID | None = None):
        with sessions() as db:
            require_publication_schema(db)
            bundle = current_publication(db, document_id=identity, publication_id=publicationId).snapshot
            for material in bundle["materials"]:
                if material["id"] == str(material_id):
                    return response(material)
            fail("not_found", "Not found", 404)
