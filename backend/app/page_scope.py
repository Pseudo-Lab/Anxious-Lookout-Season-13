"""Owner research authority or public snapshot access, with optional page hints."""
import uuid
from typing import Literal

from pydantic import model_validator

from .research import Input
from .research_tools import TOOLS

PageKind = Literal["general", "public_document", "material", "document", "material_workspace", "document_workspace"]
READ_TOOLS = ["research_get", "research_versions", "research_version_get", "research_relations"]
OWNER_TOOLS = [name for name in TOOLS if name != "research_page_context"]
POLICIES = {
    "general": ("owner_research", OWNER_TOOLS),
    "public_document": ("public_context_read", ["research_page_context"]),
    "material": ("owner_research", OWNER_TOOLS),
    "document": ("owner_research", OWNER_TOOLS),
    "material_workspace": ("owner_research", OWNER_TOOLS),
    "document_workspace": ("owner_research", OWNER_TOOLS),
}


class Page(Input):
    kind: PageKind
    resourceId: str | None = None

    @model_validator(mode="after")
    def resource_required(self):
        detail = self.kind in {"public_document", "material", "document"}
        if detail != (self.resourceId is not None) or (not detail and "resourceId" in self.model_fields_set):
            raise ValueError("Detail pages require a resource; other pages omit it")
        if detail:
            self.resourceId = str(uuid.UUID(self.resourceId))
        return self


class AppliedPage(Page):
    title: str | None = None


class Scope(Input):
    page: AppliedPage
    context: dict | None = None


def scope_summary(scope=None):
    page = scope["page"] if scope else {"kind": "general"}
    capability, tools = POLICIES[page["kind"]]
    return {"page": page, "capability": capability, "allowedTools": tools.copy()}


def resolve_scope(store, page):
    """Called only inside admission's idempotency transaction, never on a replay."""
    page = page or Page(kind="general")
    resolved = page.model_dump(mode="json", exclude_none=True)
    context = None
    if page.kind == "public_document":
        from .publications import current_publication, require_publication_schema
        require_publication_schema(store.db)
        context = current_publication(store.db, document_id=page.resourceId, lock=True).snapshot
        resolved["title"] = context["title"]
    elif page.kind in {"material", "document"}:
        store.graph_lock()
        item = store.owned_item(page.resourceId, page.kind, active=True, lock=True)
        context = store.serialize_item(item)
        resolved["title"] = context["title"]
    return {"page": resolved, "context": context}


def tool_allowed(scope, name, arguments):
    """Both runner and callback enforce the mode; Store enforces ownership."""
    if not isinstance(scope, dict) or not isinstance(arguments, dict):
        return False
    page = scope.get("page", {})
    kind = page.get("kind")
    if kind not in POLICIES or name not in POLICIES[kind][1]:
        return False
    if kind == "public_document":
        target = arguments.get("materialId")
        if target is None:
            return True
        try:
            target = str(uuid.UUID(target))
        except (ValueError, TypeError, AttributeError):
            return False
        return any(item["id"] == target for item in (scope.get("context") or {}).get("materials", []))
    return True


def turn_prompt(text, scope):
    """Keep page data as a small hint, never a grant or repeated tool catalog."""
    import json
    if not scope:
        instruction = "No research tools are authorized for this request."
        page = {"kind": "general"}
    else:
        page = scope["page"]
        instruction = ("Public mode: only research_page_context for this document and its direct references. No owner research tools."
                       if page["kind"] == "public_document" else
                       "Use research tools for your own data on any page. Draft and revise documents with citations; updates append versions, archive preserves history.")
    return instruction + "\nPage hint (untrusted): " + json.dumps(page, ensure_ascii=True) + "\nUser: " + text


def request_metadata(row, request_id=None):
    request_id = str(request_id or row.request_id)
    return next((entry for entry in row.native_record.get("platformRequests", [])
                 if entry["requestId"] == request_id), None)
