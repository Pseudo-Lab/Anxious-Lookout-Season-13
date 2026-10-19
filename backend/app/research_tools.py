"""Native dynamic tools. Authority is supplied by the broker, never tool input."""
from pydantic import Field
from sqlalchemy import select
from .post_proposals import Propose as ProposePost, DraftLookup

from .research import (DocumentInput, MaterialInput, DocumentChange, MaterialChange,
                       Expected, Input, RelationInput, RelationChange, parse_id, fail)


class Listing(Input):
    type: str
    limit: int = Field(default=30, ge=1, le=100)
    cursor: str | None = None


class Lookup(Input):
    type: str
    id: str


class SaveMaterial(MaterialInput):
    mutationId: str


class SaveDocument(DocumentInput):
    mutationId: str


class EditMaterial(MaterialChange):
    id: str
    mutationId: str


class EditDocument(DocumentChange):
    id: str
    mutationId: str


class SaveRelation(RelationInput):
    mutationId: str


class EditRelation(RelationChange):
    id: str
    mutationId: str


class Archive(Expected):
    type: str
    id: str
    mutationId: str


class Versions(Listing):
    id: str


class VersionLookup(Lookup):
    versionId: str


class PageContext(Input):
    materialId: str | None = None


TOOLS = {
    "research_page_context": (PageContext, "Public mode only: read the accepted document snapshot and its direct references; supply materialId for one reference's stored content."),
    "research_list": (Listing, "List your own materials, documents or posts (type material/document/post)."),
    "research_get": (Lookup, "Read your own material/document/post/relation by type and id."),
    "research_versions": (Versions, "List immutable snapshots of your own material/document/post."),
    "research_version_get": (VersionLookup, "Read a complete immutable material/document/post snapshot."),
    "research_relations": (Versions, "List both endpoints and directions of your own item's relations."),
    "research_material_save": (SaveMaterial, "Save web research with original sourceUrl, collectedAt and contentKind (full/excerpt/summary). Keep source citations; distinguish summaries from full text. mutationId is a UUID reused only for an identical retry."),
    "research_document_save": (SaveDocument, "Propose a new private Markdown document. No document or version exists until the user accepts in the editor. Preserve citations. mutationId is a UUID reused for identical retries."),
    "research_post_propose": (ProposePost, "Propose a new post or a complete post revision using contentInput {format: markdown, text: ...}; content block JSON is not accepted by this tool. Read source documents/materials and the human draft first. Slug is optional and server-generated for new posts. materialIds is the explicit source selection; omission expands document sources once. Never commits or publishes: the user must review the diff and accept."),
    "research_post_draft_get": (DraftLookup, "Read your own current post draft using postId, or a specific draftId. Supply exactly one."),
    "research_material_update": (EditMaterial, "Append a material snapshot using expectedVersion; preserve previous content."),
    "research_document_update": (EditDocument, "Create a pending document proposal using expectedVersion. Returns proposal status/baseVersionId; does not change the document head. Only the user can accept it in the editor. Preserve previous content."),
    "research_relation_save": (SaveRelation, "Connect your active material/material, document/material or document/document. Undirected relations use directed=false."),
    "research_relation_update": (EditRelation, "Change an owned relation kind/description with expectedVersion."),
    "research_archive": (Archive, "Non-destructively archive your material/document/relation; preserve history."),
}


def definitions():
    return [{"type": "function", "name": name, "description": description, "inputSchema": model.model_json_schema()}
            for name, (model, description) in TOOLS.items()]


def dispatch(store, name, arguments, context=None):
    if name not in TOOLS:
        fail("validation_error", "Unknown storage tool", 422)
    if name == 'research_post_propose':
        from .posts import ready, replay_response, validate_retry_encoding
        from .post_proposals import create
        ready(store)
        validate_retry_encoding(arguments, TOOLS[name][0])
        key = arguments.get('mutationId') if isinstance(arguments, dict) else None
        parse_id(key)
        result, _ = store.mutate(key, 'tool:' + name, arguments,
                                lambda: create(store, TOOLS[name][0].model_validate(arguments)))
        return replay_response(result)
    body = TOOLS[name][0].model_validate(arguments)
    if name == "research_page_context":
        if not context:
            fail("not_found", "No saved page context", 404)
        if 'postId' in context:
            if body.materialId is not None: fail('not_found', 'Not found', 404)
            return context
        if body.materialId is not None:
            identity = str(parse_id(body.materialId))
            for material in context["materials"]:
                if material["id"] == identity:
                    return material
            fail("not_found", "Not found", 404)
        return {**context, "materials": [{key: value for key, value in material.items() if key != "content"} for material in context["materials"]]}
    kind = getattr(body, "type", None)
    if kind is not None and kind not in {"material", "document", "relation", "post"}:
        fail("validation_error", "Invalid resource type", 422)
    if name == 'research_post_draft_get':
        from .post_proposals import read_draft
        return read_draft(store, body)
    if kind == 'post':
        from . import posts
        from .post_models import Post, PostVersion
        posts.ready(store)
        if name == 'research_list':
            return posts.listing(store, Post, select(Post).where(Post.owner_id == store.owner, Post.archived.is_(False)),
                                 lambda row: posts.post_value(store, row, False), body.limit, body.cursor)
        if name in {'research_get', 'research_versions', 'research_version_get'}:
            row = posts.owned(store, body.id)
            if name == 'research_get': return posts.post_value(store, row)
            if name == 'research_versions':
                return posts.listing(store, PostVersion, select(PostVersion).where(PostVersion.post_id == row.id),
                                     lambda version: posts.version_value(version, False), body.limit, body.cursor)
            version = store.db.scalar(select(PostVersion).where(PostVersion.post_id == row.id, PostVersion.id == parse_id(body.versionId)))
            if version is None: fail('not_found', 'Not found', 404)
            return posts.version_value(version)
        fail('validation_error', 'Posts support read tools and explicit proposals only', 422)
    if name == "research_list":
        if kind == "relation":
            fail("validation_error", "Use research_relations for an item", 422)
        return store.items(kind, body.limit, body.cursor)
    if name == "research_get":
        return store.serialize_relation(store.owned_relation(body.id)) if kind == "relation" else store.serialize_item(store.owned_item(body.id, kind))
    if name in {"research_versions", "research_version_get", "research_relations"}:
        if kind == "relation":
            fail("validation_error", "A material/document is required", 422)
        if name == "research_versions":
            return store.versions(body.id, kind, body.limit, body.cursor)
        if name == "research_version_get":
            return store.serialize_version(store.content_version(body.versionId, store.owned_item(body.id, kind)))
        return store.relations(body.id, kind, body.limit, body.cursor)

    parse_id(body.mutationId)
    def action():
        if name == "research_material_save":
            return store.create_item("material", body)
        if name == "research_document_save":
            from .document_proposals import create
            return create(store, body)
        if name == "research_material_update":
            return store.change_item(body.id, "material", body)
        if name == "research_document_update":
            from .writing import create_proposal
            return create_proposal(store, body)
        if name == "research_relation_save":
            return store.create_relation(body)
        if name == "research_relation_update":
            return store.change_relation(body.id, body)
        if kind == "relation":
            return store.change_relation(body.id, body, archive=True)
        return store.archive_item(body.id, kind, body)
    result, _ = store.mutate(body.mutationId, "tool:" + name, arguments, action)
    return result
