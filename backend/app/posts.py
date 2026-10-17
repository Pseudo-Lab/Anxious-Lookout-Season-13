"""Owner-only post writing with retained snapshots and explicit CAS commits."""
import re
from datetime import datetime, timezone
from uuid import UUID
from fastapi import Request
from pydantic import Field, field_validator, model_validator
from sqlalchemy import select, text

from .models import Account
from .post_models import Post, PostDraft, PostVersion
from .research import Input, Expected, fail, parse_id, stamp, page_query, encode_cursor
from .research_models import Item, ContentVersion
from .writing import DraftExpected, DraftCommit, check_draft, finish_draft, conflict


class PostText(Input):
    title: str = Field(max_length=300)
    content: str = Field(max_length=200000)
    description: str = Field(max_length=1000)
    slug: str = Field(max_length=100)
    tags: list[str] = Field(max_length=20)
    coverImageId: str | None
    documentIds: list[str] = Field(max_length=50)

    @field_validator('slug')
    @classmethod
    def slug_format(cls, value):
        if value and not re.fullmatch(r'[a-z0-9-]+', value):
            raise ValueError('Use lowercase slug letters, numbers and hyphens')
        return value

    @field_validator('tags')
    @classmethod
    def valid_tags(cls, values):
        result = []
        for value in values:
            value = value.strip()
            if not 1 <= len(value) <= 50 or '\x00' in value:
                raise ValueError('Tags must have 1–50 valid characters')
            value.encode('utf-8')
            if value not in result: result.append(value)
        return result

    @field_validator('documentIds')
    @classmethod
    def documents(cls, values):
        values = [str(UUID(value)) for value in values]
        if len(set(values)) != len(values):
            raise ValueError('Duplicate document references')
        return values

    @field_validator('coverImageId')
    @classmethod
    def image(cls, value):
        return str(UUID(value)) if value is not None else None


class CreateDraft(PostText):
    postId: str | None = None
    basePostVersion: int | None = Field(default=None, ge=1)
    baseVersionId: str | None = None

    @model_validator(mode='after')
    def base(self):
        if self.postId is None:
            if self.basePostVersion is not None or self.baseVersionId is not None:
                raise ValueError('New post has no base')
        elif self.basePostVersion is None or self.baseVersionId is None:
            raise ValueError('Existing post requires both base fields')
        return self


class ChangeDraft(PostText, DraftExpected):
    pass


def ready(store):
    if store.db.execute(text('SELECT version_num FROM research.alembic_version')).scalar_one() != '0006_posts':
        fail('not_ready', 'Posts schema is not ready', 503)


def owned(store, identity, active=False):
    # Mutations already hold the owner's graph lock. Reads must not lock a post
    # before its draft while commit locks draft first (opposite lock ordering).
    row = store.db.scalar(select(Post).where(Post.id == parse_id(identity), Post.owner_id == store.owner))
    if row is None: fail('not_found', 'Not found', 404)
    if active and row.archived: conflict('archived')
    return row


def head(store, row):
    return store.db.scalar(select(PostVersion).where(PostVersion.post_id == row.id, PostVersion.number == row.content_number))


def expect(row, expected):
    if row.version != expected: conflict('post_version')


def version_value(row, content=True):
    payload = dict(row.payload)
    if not content: payload.pop('content', None)
    return {**payload, 'id': str(row.id), 'postId': str(row.post_id), 'number': row.number, 'createdAt': stamp(row.created_at)}


def post_value(store, row, content=True):
    account = store.db.get(Account, store.owner)
    return {'id': str(row.id), 'version': row.version, 'archived': row.archived,
            'createdAt': stamp(row.created_at), 'updatedAt': stamp(row.updated_at),
            'author': {'login': account.login}, 'latestVersion': version_value(head(store, row), content), 'publication': None}


def draft_value(row, content=True):
    payload = dict(row.payload)
    if not content: payload.pop('content', None)
    return {**payload, 'id': str(row.id), 'postId': str(row.post_id) if row.post_id else None,
            'revision': row.revision, 'basePostVersion': row.base_post_version,
            'baseVersionId': str(row.base_version_id) if row.base_version_id else None,
            'state': row.state, 'createdAt': stamp(row.created_at), 'updatedAt': stamp(row.updated_at)}


def draft(store, identity):
    row = store.db.scalar(select(PostDraft).where(PostDraft.id == parse_id(identity), PostDraft.owner_id == store.owner).with_for_update())
    if row is None: fail('not_found', 'Not found', 404)
    return row


def current_draft(store, identity):
    return store.db.scalar(select(PostDraft).where(PostDraft.post_id == identity, PostDraft.owner_id == store.owner,
        PostDraft.state == 'active'))


def payload(body):
    return PostText.model_validate({key: getattr(body, key) for key in PostText.model_fields}).model_dump()


def validate_content(store, value, commit=False):
    if commit and any(not value[key].strip() for key in ('title', 'content', 'slug')):
        fail('validation_error', 'Title, content and slug are required to save a version', 422)
    if commit and not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', value['slug']):
        fail('validation_error', 'A saved slug requires words joined with single hyphens', 422)
    for identity in value['documentIds']:
        row = store.owned_item(identity, 'document', lock=True)
        if row.archived: fail('validation_error', 'Remove archived document references before saving', 422)
    # No image URL becomes public before the separate image capability is ready.
    if value['coverImageId'] is not None or '/api/media/post-images/' in value['content']:
        fail('not_ready', 'Post image capability is not ready', 503)


def create_draft(store, body):
    ready(store); store.graph_lock()
    value = payload(body)
    validate_content(store, value)
    if body.postId:
        row = owned(store, body.postId, active=True)
        if current_draft(store, row.id): conflict('active_draft_exists')
        expect(row, body.basePostVersion)
        if head(store, row).id != parse_id(body.baseVersionId): conflict('base_changed')
        if value['slug'] != row.slug: fail('validation_error', 'A saved post slug cannot change', 422)
    row = PostDraft(owner_id=store.owner, post_id=parse_id(body.postId) if body.postId else None,
        base_post_version=body.basePostVersion, base_version_id=parse_id(body.baseVersionId) if body.baseVersionId else None,
        payload=value)
    store.db.add(row); store.db.flush()
    return draft_value(row)


def change_draft(store, identity, body, discard=False):
    ready(store); store.graph_lock()
    row = draft(store, identity)
    check_draft(row, body.expectedDraftRevision)
    if discard:
        finish_draft(row, 'discarded')
    else:
        value = payload(body)
        if row.post_id:
            post = owned(store, row.post_id, active=True)
            if value['slug'] != post.slug: fail('validation_error', 'A saved post slug cannot change', 422)
        validate_content(store, value)
        row.payload = value
        row.revision += 1
        row.updated_at = datetime.now(timezone.utc)
    store.db.flush()
    return draft_value(row)


def save_version(store, value, post=None):
    validate_content(store, value, commit=True)
    now = datetime.now(timezone.utc)
    if post is None:
        # Slug namespace crosses owners. Serialize reservation without leaking an owner.
        store.db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:slug, 28006))'), {'slug': value['slug']})
        if store.db.scalar(select(Post.id).where(Post.slug == value['slug'])): conflict('slug_taken')
        post = Post(owner_id=store.owner, slug=value['slug'], created_at=now, updated_at=now)
        store.db.add(post); store.db.flush()
    else:
        if post.slug != value['slug']: fail('validation_error', 'A saved post slug cannot change', 422)
        post.version += 1
        post.content_number += 1
        post.updated_at = now
    snapshot = PostVersion(post_id=post.id, number=post.content_number, payload=value, created_at=now, updated_at=now)
    store.db.add(snapshot); store.db.flush()
    return post


def commit_draft(store, identity, body):
    ready(store); store.graph_lock()
    row = draft(store, identity)
    check_draft(row, body.expectedDraftRevision)
    post = None
    if row.post_id:
        post = owned(store, row.post_id, active=True)
        expect(post, body.expectedVersion)
        latest = head(store, post)
        if post.version != row.base_post_version or latest.id != row.base_version_id:
            if body.resolvedBaseVersionId is None or parse_id(body.resolvedBaseVersionId) != latest.id: conflict('base_changed')
        elif body.resolvedBaseVersionId is not None and parse_id(body.resolvedBaseVersionId) != latest.id:
            conflict('base_changed')
    elif body.expectedVersion is not None or body.resolvedBaseVersionId is not None:
        fail('validation_error', 'New post has no base', 422)
    post = save_version(store, row.payload, post)
    if row.post_id is None:
        row.post_id, row.base_post_version, row.base_version_id = post.id, post.version, head(store, post).id
    finish_draft(row, 'committed'); store.db.flush()
    return {'draft': draft_value(row), 'post': post_value(store, post)}


def archive(store, identity, body):
    ready(store); store.graph_lock()
    row = owned(store, identity, active=True)
    expect(row, body.expectedVersion)
    row.archived = True
    row.version += 1
    row.updated_at = datetime.now(timezone.utc)
    store.db.flush()
    return post_value(store, row)


def listing(store, model, query, serialize, limit, cursor):
    rows = store.db.scalars(page_query(query, model, limit, cursor)).all()
    return {'items': [serialize(row) for row in rows[:limit]],
            'nextCursor': encode_cursor(rows[limit-1]) if len(rows)>limit else None}


def search(query, column, value):
    if value is not None:
        if len(value) > 200 or '\x00' in value: fail('validation_error', 'Invalid search query', 422)
        query = query.where(column.icontains(value, autoescape=True))
    return query


def register_posts(app, authorized, settings, response, write):
    root = settings.base_path + '/api/research'

    @app.get(root + '/posts')
    def posts(request: Request, limit: int = 30, cursor: str | None = None, q: str | None = None):
        with authorized(request) as store:
            ready(store)
            query = select(Post).join(PostVersion, (PostVersion.post_id == Post.id) & (PostVersion.number == Post.content_number)).where(
                Post.owner_id == store.owner, Post.archived.is_(False))
            query = search(query, PostVersion.payload['title'].astext, q)
            return response(listing(store, Post, query, lambda row: post_value(store, row, False), limit, cursor))

    @app.get(root + '/posts/{identity}')
    def get(identity: UUID, request: Request):
        with authorized(request) as store:
            ready(store)
            return response(post_value(store, owned(store, identity)))

    @app.delete(root + '/posts/{identity}')
    def remove(identity: UUID, body: dict, request: Request):
        with authorized(request, True) as store:
            ready(store)
            return write(store, request, Expected, body, lambda parsed: archive(store, identity, parsed))

    @app.get(root + '/posts/{identity}/versions')
    def versions(identity: UUID, request: Request, limit: int = 30, cursor: str | None = None):
        with authorized(request) as store:
            ready(store); owned(store, identity)
            query = select(PostVersion).where(PostVersion.post_id == identity)
            return response(listing(store, PostVersion, query, lambda row: version_value(row, False), limit, cursor))

    @app.get(root + '/posts/{identity}/versions/{version_id}')
    def version(identity: UUID, version_id: UUID, request: Request):
        with authorized(request) as store:
            ready(store); owned(store, identity)
            row = store.db.scalar(select(PostVersion).where(PostVersion.id == version_id, PostVersion.post_id == identity))
            if row is None: fail('not_found', 'Not found', 404)
            return response(version_value(row))

    @app.get(root + '/post-document-options')
    def options(request: Request, limit: int = 30, cursor: str | None = None, q: str | None = None):
        with authorized(request) as store:
            ready(store)
            query = select(Item).join(ContentVersion, (ContentVersion.item_id == Item.id) & (ContentVersion.number == Item.content_number)).where(
                Item.owner_id == store.owner, Item.kind == 'document', Item.archived.is_(False))
            query = search(query, ContentVersion.title, q)
            def value(row):
                snapshot = store.db.scalar(select(ContentVersion).where(ContentVersion.item_id == row.id, ContentVersion.number == row.content_number))
                return {'id': str(row.id), 'title': snapshot.title, 'version': row.version, 'latestVersionId': str(snapshot.id)}
            return response(listing(store, Item, query, value, limit, cursor))

    @app.post(root + '/post-drafts')
    def create(body: dict, request: Request):
        with authorized(request, True) as store:
            ready(store)
            return write(store, request, CreateDraft, body, lambda parsed: create_draft(store, parsed), 201)

    @app.get(root + '/post-drafts')
    def drafts(request: Request, limit: int = 30, cursor: str | None = None):
        with authorized(request) as store:
            ready(store)
            query = select(PostDraft).where(PostDraft.owner_id == store.owner, PostDraft.state == 'active')
            return response(listing(store, PostDraft, query, lambda row: draft_value(row, False), limit, cursor))

    @app.get(root + '/post-drafts/{identity}')
    def get_draft(identity: UUID, request: Request):
        with authorized(request) as store:
            ready(store)
            return response(draft_value(draft(store, identity)))

    @app.get(root + '/posts/{identity}/draft')
    def for_post(identity: UUID, request: Request):
        with authorized(request) as store:
            ready(store); owned(store, identity)
            row = current_draft(store, identity)
            return response({'draft': draft_value(row) if row else None})

    @app.put(root + '/post-drafts/{identity}')
    def update(identity: UUID, body: dict, request: Request):
        with authorized(request, True) as store:
            ready(store)
            return write(store, request, ChangeDraft, body, lambda parsed: change_draft(store, identity, parsed))

    @app.delete(root + '/post-drafts/{identity}')
    def discard(identity: UUID, body: dict, request: Request):
        with authorized(request, True) as store:
            ready(store)
            return write(store, request, DraftExpected, body, lambda parsed: change_draft(store, identity, parsed, True))

    @app.post(root + '/post-drafts/{identity}/commit')
    def commit(identity: UUID, body: dict, request: Request):
        with authorized(request, True) as store:
            ready(store)
            return write(store, request, DraftCommit, body, lambda parsed: commit_draft(store, identity, parsed))
