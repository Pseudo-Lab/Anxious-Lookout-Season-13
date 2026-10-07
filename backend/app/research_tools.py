"""Native dynamic tools. Authority is supplied by the broker, never tool input."""
from pydantic import Field

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


TOOLS = {
    "research_list": (Listing, "List your own materials or documents (type material/document)."),
    "research_get": (Lookup, "Read your own material/document/relation by type and id."),
    "research_versions": (Versions, "List immutable snapshots of your own material/document."),
    "research_version_get": (VersionLookup, "Read a complete immutable material/document snapshot."),
    "research_relations": (Versions, "List both endpoints and directions of your own item's relations."),
    "research_material_save": (SaveMaterial, "Save collected web text, original source URL, collection timestamp and full/excerpt/summary type. mutationId is a UUID reused only for an identical retry."),
    "research_document_save": (SaveDocument, "Save a private Markdown document. Preserve citations in its text. mutationId is a UUID reused for identical retries."),
    "research_material_update": (EditMaterial, "Append a material snapshot using expectedVersion; preserve previous content."),
    "research_document_update": (EditDocument, "Append a document snapshot using expectedVersion; preserve previous content."),
    "research_relation_save": (SaveRelation, "Connect your active material/material, document/material or document/document. Undirected relations use directed=false."),
    "research_relation_update": (EditRelation, "Change an owned relation kind/description with expectedVersion."),
    "research_archive": (Archive, "Non-destructively archive your material/document/relation; preserve history."),
}


def definitions():
    return [{"type": "function", "name": name, "description": description, "inputSchema": model.model_json_schema()}
            for name, (model, description) in TOOLS.items()]


def dispatch(store, name, arguments):
    if name not in TOOLS:
        fail("validation_error", "Unknown storage tool", 422)
    body = TOOLS[name][0].model_validate(arguments)
    kind = getattr(body, "type", None)
    if kind is not None and kind not in {"material", "document", "relation"}:
        fail("validation_error", "Invalid resource type", 422)
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
            return store.create_item("document", body)
        if name == "research_material_update":
            return store.change_item(body.id, "material", body)
        if name == "research_document_update":
            return store.change_item(body.id, "document", body)
        if name == "research_relation_save":
            return store.create_relation(body)
        if name == "research_relation_update":
            return store.change_relation(body.id, body)
        if kind == "relation":
            return store.change_relation(body.id, body, archive=True)
        return store.archive_item(body.id, kind, body)
    result, _ = store.mutate(body.mutationId, "tool:" + name, arguments, action)
    return result
