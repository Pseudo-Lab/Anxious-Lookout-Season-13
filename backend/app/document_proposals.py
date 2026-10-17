"""D1: a new Codex document is a proposal, never an implicit document version."""
from datetime import datetime, timezone
from uuid import UUID
from fastapi import Request
from sqlalchemy import select
from . import writing, posts
from .post_proposals import DraftPair
from .research import DocumentInput, fail, parse_id
from .research_models import DocumentProposal


class Accept(DocumentInput, DraftPair, writing.ProposalExpected):
    pass


def create(store, body):
    posts.ready(store); store.graph_lock()
    row = DocumentProposal(owner_id=store.owner, title=body.title, content=body.content)
    store.db.add(row); store.db.flush()
    return writing.proposal_value(row)


def owned(store, identity):
    row = store.db.scalar(select(DocumentProposal).where(DocumentProposal.id == parse_id(identity),
        DocumentProposal.owner_id == store.owner).with_for_update())
    if row is None: fail('not_found', 'Not found', 404)
    return row


def resolve(store, identity, body, discard=False):
    posts.ready(store); store.graph_lock()
    row = owned(store, identity)
    if row.status != 'pending' or row.version != body.expectedProposalVersion: writing.conflict('proposal_closed')
    if row.document_id is not None:
        if not discard: fail('validation_error', 'Use the existing document proposal acceptance route', 422)
        return writing.resolve_proposal(store, row.document_id, identity, body, True)
    draft = None
    if discard:
        row.status = 'discarded'
    else:
        if body.draftId:
            draft = writing.draft(store, body.draftId)
            writing.check_draft(draft, body.expectedDraftRevision)
            if draft.document_id is not None: writing.conflict('draft_revision')
        result = store.create_item('document', DocumentInput(title=body.title, content=body.content))
        row.document_id, row.base_document_version, row.base_version_id = parse_id(result['id']), result['version'], parse_id(result['latestVersion']['id'])
        row.accepted_version_id, row.status = row.base_version_id, 'accepted'
        if draft:
            draft.document_id, draft.base_document_version, draft.base_version_id = row.document_id, row.base_document_version, row.base_version_id
            writing.finish_draft(draft, 'committed')
    row.version += 1
    row.updated_at = datetime.now(timezone.utc)
    store.db.flush()
    return writing.proposal_value(row) if discard else {'proposal': writing.proposal_value(row), 'document': result,
        'draft': writing.draft_value(draft) if draft else None}


def register(app, authorized, settings, response, write):
    root = settings.base_path + '/api/research/document-proposals'

    @app.get(root)
    def proposals(request: Request, limit: int = 30, cursor: str | None = None):
        with authorized(request) as store:
            posts.ready(store)
            query = select(DocumentProposal).where(DocumentProposal.owner_id == store.owner)
            return response(writing.listing(store, DocumentProposal, query, writing.proposal_value, limit, cursor))

    @app.get(root + '/{identity}')
    def get(identity: UUID, request: Request):
        with authorized(request) as store:
            posts.ready(store)
            return response(writing.proposal_value(owned(store, identity)))

    @app.post(root + '/{identity}/accept')
    def accept(identity: UUID, body: dict, request: Request):
        with authorized(request, True) as store:
            posts.ready(store)
            return write(store, request, Accept, body, lambda parsed: resolve(store, identity, parsed))

    @app.post(root + '/{identity}/discard')
    def discard(identity: UUID, body: dict, request: Request):
        with authorized(request, True) as store:
            posts.ready(store)
            return write(store, request, writing.ProposalExpected, body, lambda parsed: resolve(store, identity, parsed, True))
