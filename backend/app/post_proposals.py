"""Codex can propose; only an authenticated human action creates a post version."""
from datetime import datetime, timezone
from uuid import UUID
from fastapi import Request
from pydantic import Field, model_validator
from sqlalchemy import select
from . import posts
from .post_models import PostProposal
from .research import Input, fail, parse_id, stamp
from .writing import ProposalExpected, check_draft, finish_draft, conflict


class DraftPair(Input):
    draftId: str | None = None
    expectedDraftRevision: int | None = Field(default=None, ge=1)

    @model_validator(mode='after')
    def pair(self):
        if (self.draftId is None) != (self.expectedDraftRevision is None):
            raise ValueError('Draft identity and revision must be paired')
        return self


class Propose(posts.PostText, DraftPair):
    mutationId: str
    postId: str | None = None
    expectedVersion: int | None = Field(default=None, ge=1)

    @model_validator(mode='after')
    def post_pair(self):
        if (self.postId is None) != (self.expectedVersion is None):
            raise ValueError('Post identity and version must be paired')
        return self


class Accept(posts.PostText, DraftPair, ProposalExpected):
    expectedVersion: int | None = Field(default=None, ge=1)
    resolvedBaseVersionId: str | None = None


class DraftLookup(Input):
    postId: str | None = None
    draftId: str | None = None

    @model_validator(mode='after')
    def one(self):
        if (self.postId is None) == (self.draftId is None):
            raise ValueError('Provide exactly one draftId or postId')
        return self


def value(row, content=True):
    payload = posts.stored_payload(row.payload)
    if not content: payload.pop('content', None)
    return {**payload, 'id': str(row.id), 'postId': str(row.post_id) if row.post_id else None,
        'version': row.version, 'basePostVersion': row.base_post_version,
        'baseVersionId': str(row.base_version_id) if row.base_version_id else None,
        'baseDraftId': str(row.base_draft_id) if row.base_draft_id else None, 'baseDraftRevision': row.base_draft_revision,
        'origin': 'codex', 'status': row.status, 'acceptedPostId': str(row.accepted_post_id) if row.accepted_post_id else None,
        'acceptedVersionId': str(row.accepted_version_id) if row.accepted_version_id else None,
        'createdAt': stamp(row.created_at), 'updatedAt': stamp(row.updated_at)}


def owned(store, identity):
    row = store.db.scalar(select(PostProposal).where(PostProposal.id == parse_id(identity), PostProposal.owner_id == store.owner).with_for_update())
    if row is None: fail('not_found', 'Not found', 404)
    return row


def create(store, body):
    posts.ready(store); store.graph_lock()
    payload = posts.payload(body)
    image_ids = posts.validate_content(store, payload, commit=True)
    post = posts.owned(store, body.postId, active=True) if body.postId else None
    if post:
        posts.expect(post, body.expectedVersion)
    posts.choose_slug(payload, post)
    if body.draftId:
        draft = posts.draft(store, body.draftId)
        check_draft(draft, body.expectedDraftRevision)
        if draft.post_id != (post.id if post else None): conflict('draft_revision')
    row = PostProposal(owner_id=store.owner, post_id=post.id if post else None,
        base_post_version=post.version if post else None, base_version_id=posts.head(store, post).id if post else None,
        base_draft_id=parse_id(body.draftId) if body.draftId else None, base_draft_revision=body.expectedDraftRevision, payload=payload, image_ids=image_ids)
    store.db.add(row); store.db.flush()
    return value(row)


def resolve(store, identity, body, discard=False):
    posts.ready(store); store.graph_lock()
    row = owned(store, identity)
    if row.status != 'pending' or row.version != body.expectedProposalVersion: conflict('proposal_closed')
    draft = None
    if discard:
        row.status = 'discarded'
    else:
        post = posts.owned(store, row.post_id, active=True) if row.post_id else None
        if post:
            posts.expect(post, body.expectedVersion)
            if body.resolvedBaseVersionId is None or posts.head(store, post).id != parse_id(body.resolvedBaseVersionId): conflict('base_changed')
            draft = posts.current_draft(store, post.id)
        elif body.expectedVersion is not None or body.resolvedBaseVersionId is not None:
            fail('validation_error', 'New post proposal has no base', 422)
        elif row.base_draft_id:
            draft = posts.draft(store, row.base_draft_id)
        elif body.draftId:
            draft = posts.draft(store, body.draftId)
        if draft:
            if body.draftId is None or draft.id != parse_id(body.draftId) or draft.post_id != (post.id if post else None): conflict('draft_revision')
            check_draft(draft, body.expectedDraftRevision)
        elif body.draftId:
            conflict('draft_closed')
        post = posts.save_version(store, posts.payload(body), post)
        if draft:
            if draft.post_id is None:
                draft.post_id, draft.base_post_version, draft.base_version_id = post.id, post.version, posts.head(store, post).id
            finish_draft(draft, 'committed')
        row.status, row.accepted_post_id, row.accepted_version_id = 'accepted', post.id, posts.head(store, post).id
    row.version += 1
    row.updated_at = datetime.now(timezone.utc)
    store.db.flush()
    return value(row) if discard else {'proposal': value(row), 'post': posts.post_value(store, post),
                                      'draft': posts.draft_value(draft) if draft else None}


def read_draft(store, body):
    posts.ready(store)
    if body.postId:
        post = posts.owned(store, body.postId)
        row = posts.current_draft(store, post.id)
    else:
        row = posts.draft(store, body.draftId)
    return {'draft': posts.draft_value(row) if row else None}


def register(app, authorized, settings, response, write):
    root = settings.base_path + '/api/research/post-proposals'

    @app.get(root)
    def proposals(request: Request, postId: UUID | None = None, limit: int = 30, cursor: str | None = None):
        with authorized(request) as store:
            posts.ready(store)
            query = select(PostProposal).where(PostProposal.owner_id == store.owner)
            if postId:
                posts.owned(store, postId)
                query = query.where(PostProposal.post_id == postId)
            return response(posts.listing(store, PostProposal, query, lambda row: value(row, False), limit, cursor))

    @app.get(root + '/{identity}')
    def get(identity: UUID, request: Request):
        with authorized(request) as store:
            posts.ready(store)
            return response(value(owned(store, identity)))

    @app.post(root + '/{identity}/accept')
    def accept(identity: UUID, body: dict, request: Request):
        with authorized(request, True) as store:
            posts.ready(store)
            return write(store, request, Accept, body, lambda parsed: resolve(store, identity, parsed))

    @app.post(root + '/{identity}/discard')
    def discard(identity: UUID, body: dict, request: Request):
        with authorized(request, True) as store:
            posts.ready(store)
            return write(store, request, ProposalExpected, body, lambda parsed: resolve(store, identity, parsed, True))
