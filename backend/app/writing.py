"""Owner drafts and immutable proposal content; explicit atomic publication to head."""
from datetime import datetime, timezone
from uuid import UUID
from pydantic import Field, model_validator
from sqlalchemy import select, text
from fastapi import Request

from .research import Input, DocumentInput, DocumentChange, fail, parse_id, stamp, page_query, encode_cursor
from .research_models import DocumentDraft, DocumentProposal, ContentVersion


class DraftText(Input):
    title: str = Field(max_length=300)
    content: str = Field(max_length=200000)


class DraftCreate(DraftText):
    documentId: str | None = None
    baseDocumentVersion: int | None = Field(default=None, ge=1)
    baseVersionId: str | None = None

    @model_validator(mode='after')
    def base(self):
        if self.documentId is None:
            if self.baseDocumentVersion is not None or self.baseVersionId is not None:
                raise ValueError('New drafts have no base')
        elif self.baseDocumentVersion is None or self.baseVersionId is None:
            raise ValueError('Existing documents require a base')
        return self


class DraftExpected(Input):
    expectedDraftRevision: int = Field(ge=1)


class DraftChange(DraftText, DraftExpected):
    pass


class DraftCommit(DraftExpected):
    expectedVersion: int | None = Field(default=None, ge=1)
    resolvedBaseVersionId: str | None = None


class ProposalExpected(Input):
    expectedProposalVersion: int = Field(ge=1)


class ProposalAccept(DocumentChange, ProposalExpected):
    resolvedBaseVersionId: str
    draftId: str | None = None
    expectedDraftRevision: int | None = Field(default=None, ge=1)

    @model_validator(mode='after')
    def paired(self):
        if (self.draftId is None) != (self.expectedDraftRevision is None):
            raise ValueError('Draft identity and revision are paired')
        return self


def ready(store):
    if store.db.execute(text('SELECT version_num FROM research.alembic_version')).scalar_one() not in {'0005_writing', '0006_posts', '0007_post_blocks'}:
        fail('not_ready', 'Writing schema is not ready', 503)


def conflict(reason):
    fail('conflict', 'The draft, proposal or document changed; compare before retrying', 409, {'reason': reason})


def expect_document(row, expected):
    if row.version != expected: conflict('document_version')


def active_document(store, identity):
    row = store.owned_item(identity, 'document', lock=True)
    if row.archived:
        conflict('archived')
    return row


def head(store, item):
    return store.db.scalar(select(ContentVersion).where(ContentVersion.item_id == item.id,
        ContentVersion.owner_id == store.owner, ContentVersion.number == item.content_number))


def draft_value(row, content=True):
    result = {'id': str(row.id), 'documentId': str(row.document_id) if row.document_id else None,
        'revision': row.revision, 'baseDocumentVersion': row.base_document_version,
        'baseVersionId': str(row.base_version_id) if row.base_version_id else None,
        'title': row.title, 'updatedAt': stamp(row.updated_at), 'state': row.state}
    if content: result['content'] = row.content
    return result


def proposal_value(row, content=True):
    result = {'id': str(row.id), 'documentId': str(row.document_id) if row.document_id else None, 'version': row.version,
        'baseDocumentVersion': row.base_document_version, 'baseVersionId': str(row.base_version_id) if row.base_version_id else None,
        'title': row.title, 'origin': 'codex', 'status': row.status,
        'acceptedVersionId': str(row.accepted_version_id) if row.accepted_version_id else None,
        'acceptedDocumentId': str(row.document_id) if row.status == 'accepted' else None,
        'createdAt': stamp(row.created_at), 'updatedAt': stamp(row.updated_at)}
    if content: result['content'] = row.content
    return result


def draft(store, identity):
    row = store.db.scalar(select(DocumentDraft).where(DocumentDraft.id == parse_id(identity),
        DocumentDraft.owner_id == store.owner).with_for_update())
    if row is None: fail('not_found', 'Not found', 404)
    return row


def current_draft(store, document_id):
    return store.db.scalar(select(DocumentDraft).where(DocumentDraft.document_id == document_id,
        DocumentDraft.owner_id == store.owner, DocumentDraft.state == 'active').with_for_update())


def check_draft(row, revision):
    if row.state != 'active': conflict('draft_closed')
    if row.revision != revision: conflict('draft_revision')


def finish_draft(row, state):
    row.state = state
    row.revision += 1
    row.updated_at = datetime.now(timezone.utc)


def create_draft(store, body):
    ready(store); store.graph_lock()
    if body.documentId is not None:
        item = active_document(store, body.documentId)
        if current_draft(store, item.id): conflict('active_draft_exists')
        expect_document(item, body.baseDocumentVersion)
        if head(store, item).id != parse_id(body.baseVersionId): conflict('base_changed')
    row = DocumentDraft(owner_id=store.owner, document_id=parse_id(body.documentId) if body.documentId else None,
        base_document_version=body.baseDocumentVersion, base_version_id=parse_id(body.baseVersionId) if body.baseVersionId else None,
        title=body.title, content=body.content)
    store.db.add(row); store.db.flush()
    return draft_value(row)


def change_draft(store, identity, body, discard=False):
    ready(store); store.graph_lock()
    row = draft(store, identity)
    check_draft(row, body.expectedDraftRevision)
    if discard:
        finish_draft(row, 'discarded')
    else:
        if row.document_id: active_document(store, row.document_id)
        row.title, row.content = body.title, body.content
        row.revision += 1
        row.updated_at = datetime.now(timezone.utc)
    store.db.flush()
    return draft_value(row)


def commit_draft(store, identity, body):
    ready(store); store.graph_lock()
    row = draft(store, identity)
    check_draft(row, body.expectedDraftRevision)
    content = DocumentInput(title=row.title, content=row.content)
    if row.document_id:
        item = active_document(store, row.document_id)
        expect_document(item, body.expectedVersion)
        latest = head(store, item)
        if item.version != row.base_document_version or latest.id != row.base_version_id:
            if body.resolvedBaseVersionId is None or parse_id(body.resolvedBaseVersionId) != latest.id: conflict('base_changed')
        elif body.resolvedBaseVersionId is not None and parse_id(body.resolvedBaseVersionId) != latest.id:
            conflict('base_changed')
        result = store.change_item(item.id, 'document', DocumentChange(**content.model_dump(), expectedVersion=item.version))
    else:
        if body.expectedVersion is not None or body.resolvedBaseVersionId is not None:
            fail('validation_error', 'New drafts have no document base', 422)
        result = store.create_item('document', content)
        row.document_id = parse_id(result['id'])
        row.base_document_version = result['version']
        row.base_version_id = parse_id(result['latestVersion']['id'])
    finish_draft(row, 'committed'); store.db.flush()
    return {'draft': draft_value(row), 'document': result}


def create_proposal(store, body):
    ready(store); store.graph_lock()
    item = active_document(store, body.id)
    expect_document(item, body.expectedVersion)
    row = DocumentProposal(owner_id=store.owner, document_id=item.id, base_document_version=item.version,
        base_version_id=head(store, item).id, title=body.title, content=body.content)
    store.db.add(row); store.db.flush()
    return proposal_value(row)


def proposal(store, document_id, identity):
    store.owned_item(document_id, 'document')
    row = store.db.scalar(select(DocumentProposal).where(DocumentProposal.id == parse_id(identity),
        DocumentProposal.document_id == parse_id(document_id), DocumentProposal.owner_id == store.owner).with_for_update())
    if row is None: fail('not_found', 'Not found', 404)
    return row


def resolve_proposal(store, document_id, identity, body, discard=False):
    ready(store); store.graph_lock()
    row = proposal(store, document_id, identity)
    if row.status != 'pending' or row.version != body.expectedProposalVersion: conflict('proposal_closed')
    resolved_draft = None
    if discard:
        row.status = 'discarded'
    else:
        item = active_document(store, document_id)
        expect_document(item, body.expectedVersion)
        if head(store, item).id != parse_id(body.resolvedBaseVersionId): conflict('base_changed')
        current = current_draft(store, item.id)
        if current:
            if body.draftId is None or parse_id(body.draftId) != current.id: conflict('draft_revision')
            check_draft(current, body.expectedDraftRevision)
            finish_draft(current, 'committed')
            resolved_draft = draft_value(current)
        elif body.draftId is not None:
            conflict('draft_closed')
        result = store.change_item(item.id, 'document', DocumentChange(
            title=body.title, content=body.content, expectedVersion=body.expectedVersion))
        row.status, row.accepted_version_id = 'accepted', parse_id(result['latestVersion']['id'])
    row.version += 1
    row.updated_at = datetime.now(timezone.utc)
    store.db.flush()
    value = proposal_value(row)
    return value if discard else {'proposal': value, 'document': result, 'draft': resolved_draft}


def listing(store, model, query, serialize, limit, cursor):
    rows = store.db.scalars(page_query(query, model, limit, cursor)).all()
    return {'items': [serialize(row, content=False) for row in rows[:limit]],
            'nextCursor': encode_cursor(rows[limit-1]) if len(rows)>limit else None}


def register_writing(app, authorized, settings, response, write):
    root = settings.base_path + '/api/research'

    @app.post(root + '/document-drafts')
    def create(body: dict, request: Request):
        with authorized(request, True) as store:
            ready(store)
            return write(store, request, DraftCreate, body, lambda parsed: create_draft(store, parsed), 201)

    @app.get(root + '/document-drafts')
    def drafts(request: Request, limit: int = 30, cursor: str | None = None):
        with authorized(request) as store:
            ready(store)
            query = select(DocumentDraft).where(DocumentDraft.owner_id == store.owner, DocumentDraft.state == 'active')
            return response(listing(store, DocumentDraft, query, draft_value, limit, cursor))

    @app.get(root + '/document-drafts/{identity}')
    def get(identity: UUID, request: Request):
        with authorized(request) as store:
            ready(store)
            return response(draft_value(draft(store, identity)))

    @app.get(root + '/documents/{identity}/draft')
    def for_document(identity: UUID, request: Request):
        with authorized(request) as store:
            ready(store)
            store.owned_item(identity, 'document')
            row = current_draft(store, identity)
            return response({'draft': draft_value(row) if row else None})

    @app.put(root + '/document-drafts/{identity}')
    def update(identity: UUID, body: dict, request: Request):
        with authorized(request, True) as store:
            ready(store)
            return write(store, request, DraftChange, body, lambda parsed: change_draft(store, identity, parsed))

    @app.delete(root + '/document-drafts/{identity}')
    def discard_draft(identity: UUID, body: dict, request: Request):
        with authorized(request, True) as store:
            ready(store)
            return write(store, request, DraftExpected, body, lambda parsed: change_draft(store, identity, parsed, True))

    @app.post(root + '/document-drafts/{identity}/commit')
    def commit(identity: UUID, body: dict, request: Request):
        with authorized(request, True) as store:
            ready(store)
            return write(store, request, DraftCommit, body, lambda parsed: commit_draft(store, identity, parsed))

    @app.get(root + '/documents/{identity}/proposals')
    def proposals(identity: UUID, request: Request, limit: int = 30, cursor: str | None = None):
        with authorized(request) as store:
            ready(store); store.owned_item(identity, 'document')
            query = select(DocumentProposal).where(DocumentProposal.owner_id == store.owner, DocumentProposal.document_id == identity)
            return response(listing(store, DocumentProposal, query, proposal_value, limit, cursor))

    @app.get(root + '/documents/{identity}/proposals/{proposal_id}')
    def get_proposal(identity: UUID, proposal_id: UUID, request: Request):
        with authorized(request) as store:
            ready(store)
            return response(proposal_value(proposal(store, identity, proposal_id)))

    @app.post(root + '/documents/{identity}/proposals/{proposal_id}/accept')
    def accept(identity: UUID, proposal_id: UUID, body: dict, request: Request):
        with authorized(request, True) as store:
            ready(store)
            return write(store, request, ProposalAccept, body, lambda parsed: resolve_proposal(store, identity, proposal_id, parsed))

    @app.post(root + '/documents/{identity}/proposals/{proposal_id}/discard')
    def discard(identity: UUID, proposal_id: UUID, body: dict, request: Request):
        with authorized(request, True) as store:
            ready(store)
            return write(store, request, ProposalExpected, body, lambda parsed: resolve_proposal(store, identity, proposal_id, parsed, True))
