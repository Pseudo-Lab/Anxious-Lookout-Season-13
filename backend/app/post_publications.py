"""Current public heads over immutable post snapshots; never expose private bodies."""
import base64
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from fastapi import Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select, text
from . import posts
from .models import Account
from .post_models import Post, PostVersion, PostPublication, PostPublicationHead
from .research_models import ContentVersion, Publication, PublicationHead
from .research import Expected, fail, parse_id, stamp, page_query, encode_cursor
from .publications import Publish
from .writing import conflict


def ready(db):
    from .research_schema_contract import validate_research
    try:
        if validate_research(db.connection()) != '0006_posts': raise RuntimeError()
    except RuntimeError:
        fail('not_ready', 'Post publication schema is not ready', 503)


def current(db, post_id=None, slug=None):
    query = select(PostPublication).join(PostPublicationHead, PostPublicationHead.publication_id == PostPublication.id)
    if post_id is not None: query = query.where(PostPublication.post_id == parse_id(post_id))
    if slug is not None: query = query.where(PostPublication.snapshot['slug'].astext == slug)
    return db.scalar(query)


def info(db, post_id):
    row = current(db, post_id=post_id)
    return {key: row.snapshot[key] for key in ('id', 'versionId', 'publishedAt', 'slug')} if row else None


def preview(store, identity, version_id=None, base_path=''):
    posts.ready(store); store.graph_lock()
    post = posts.owned(store, identity, active=True)
    version = store.db.scalar(select(PostVersion).where(PostVersion.post_id == post.id,
        PostVersion.id == parse_id(version_id))) if version_id else posts.head(store, post)
    if version is None: fail('not_found', 'Not found', 404)
    documents, evidence = [], []
    publishable = True
    for identity in version.payload['documentIds']:
        document = store.owned_item(identity, 'document', lock=True)
        content = store.db.scalar(select(ContentVersion).where(ContentVersion.item_id == document.id,
            ContentVersion.number == document.content_number))
        publication = store.db.scalar(select(Publication).join(PublicationHead, PublicationHead.publication_id == Publication.id).where(Publication.document_id == document.id))
        documents.append({'id': str(document.id), 'title': content.title,
                          'publicUrl': base_path+'/public/document/?id='+str(document.id) if publication else None})
        evidence.append([str(document.id), document.version, document.archived, str(publication.id) if publication else None])
        publishable = publishable and not document.archived
    value = posts.stored_payload(version.payload)
    from .post_materials import current as material_version
    materials = []
    for identity in value['materialIds']:
        material = store.owned_item(identity, 'material', lock=True)
        content = material_version(store, material)
        materials.append({'id': str(material.id), 'title': content.title, 'sourceUrl': content.source_url})
        evidence.append(['material', str(material.id), material.version, material.archived])
        publishable = publishable and not material.archived
    image_ids = posts.validate_content(store, {**value, 'documentIds': [], 'materialIds': []}, commit=True)
    previous = current(store.db, post_id=post.id)
    account = store.db.get(Account, store.owner)
    author = {'login': account.login, 'name': account.login}
    from .post_images import value as image_value
    from .post_models import PostImage
    images = [image_value(store.db.get(PostImage, identity), base_path) for identity in image_ids]
    state = {'post': str(post.id), 'version': str(version.id), 'cas': post.version, 'documents': documents,
             'evidence': evidence, 'author': author, 'images': images, 'materials': materials}
    token = hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    return {**value, 'postId': str(post.id), 'versionId': str(version.id), 'expectedVersion': post.version,
            'previewToken': token, 'publishable': publishable, 'author': author, 'documents': documents,
            'images': images, 'materials': materials, 'currentPublication': public_value(previous) if previous else None}


def publish(store, identity, body, base_path):
    shown = preview(store, identity, body.versionId, base_path)
    post = posts.owned(store, identity, active=True)
    posts.expect(post, body.expectedVersion)
    if not hmac.compare_digest(body.previewToken, shown['previewToken']): conflict('preview_changed')
    if not shown['publishable']: fail('validation_error', 'Remove archived document or material references before publishing', 422)
    identity, now = uuid.uuid4(), datetime.now(timezone.utc)
    snapshot = {key: shown[key] for key in ('postId', 'versionId', 'slug', 'title', 'description', 'content', 'tags', 'author', 'documents', 'materials')}
    from .post_images import url
    snapshot.update(id=str(identity), coverImageUrl=url(shown['coverImageId'], base_path) if shown['coverImageId'] else None, publishedAt=stamp(now))
    row = PostPublication(id=identity, post_id=post.id, version_id=parse_id(body.versionId), snapshot=snapshot, image_ids=[parse_id(image['id']) for image in shown['images']],
                          created_at=now, updated_at=now)
    store.db.add(row); store.db.flush()
    pointer = store.db.get(PostPublicationHead, post.id)
    if pointer: pointer.publication_id = row.id
    else: store.db.add(PostPublicationHead(post_id=post.id, publication_id=row.id))
    post.version += 1
    post.updated_at = now
    store.db.flush()
    return snapshot


def withdraw(store, identity, body):
    posts.ready(store); store.graph_lock()
    post = posts.owned(store, identity)
    posts.expect(post, body.expectedVersion)
    pointer = store.db.get(PostPublicationHead, post.id)
    if pointer is None or pointer.publication_id is None: fail('not_found', 'No current publication', 404)
    pointer.publication_id = None
    post.version += 1
    post.updated_at = datetime.now(timezone.utc)
    store.db.flush()
    return {'id': str(post.id), 'version': post.version, 'publication': None}


def public_value(row):
    return {**row.snapshot, 'materials': row.snapshot.get('materials', [])}


def public_response(request, value):
    response = JSONResponse(value)
    etag = '"'+hashlib.sha256(response.body).hexdigest()+'"'
    headers = {'ETag': etag, 'Cache-Control': 'public, max-age=0, must-revalidate'}
    if request.headers.get('if-none-match') == etag:
        return Response(status_code=304, headers=headers)
    response.headers.update(headers)
    return response


def register(app, authorized, sessions, settings, response, write):
    private = settings.base_path + '/api/research/posts'
    public = settings.base_path + '/api/public'

    @app.get(private + '/{identity}/publication-preview')
    def get_preview(identity: uuid.UUID, request: Request, versionId: uuid.UUID | None = None):
        with authorized(request) as store:
            return response(preview(store, identity, versionId, settings.base_path))

    @app.post(private + '/{identity}/publications')
    def create(identity: uuid.UUID, body: dict, request: Request):
        with authorized(request, True) as store:
            posts.ready(store)
            return write(store, request, Publish, body, lambda parsed: publish(store, identity, parsed, settings.base_path), 201)

    @app.delete(private + '/{identity}/publication')
    def remove(identity: uuid.UUID, body: dict, request: Request):
        with authorized(request, True) as store:
            posts.ready(store)
            return write(store, request, Expected, body, lambda parsed: withdraw(store, identity, parsed))

    @app.get(public + '/posts')
    def listing(request: Request, tag: str | None = None, q: str | None = None, limit: int = 30, cursor: str | None = None):
        with sessions() as db:
            ready(db)
            query = select(PostPublication).join(PostPublicationHead, PostPublicationHead.publication_id == PostPublication.id)
            if tag is not None:
                if not 1 <= len(tag) <= 50 or '\x00' in tag: fail('validation_error', 'Invalid tag', 422)
                query = query.where(PostPublication.snapshot['tags'].contains([tag]))
            query = posts.search(query, PostPublication.snapshot['title'].astext, q)
            rows = db.scalars(page_query(query, PostPublication, limit, cursor)).all()
            value = {'items': [{key: value for key, value in public_value(row).items() if key != 'content'} for row in rows[:limit]],
                     'nextCursor': encode_cursor(rows[limit-1]) if len(rows)>limit else None}
            return public_response(request, value)

    @app.get(public + '/posts/{slug}')
    def detail(slug: str, request: Request):
        with sessions() as db:
            ready(db)
            row = current(db, slug=slug)
            if row is None: fail('not_found', 'Not found', 404)
            return public_response(request, public_value(row))

    @app.get(public + '/post-tags')
    def tags(request: Request, limit: int = 30, cursor: str | None = None):
        if not 1 <= limit <= 100: fail('validation_error', 'Invalid limit', 422)
        after = None
        if cursor is not None:
            try:
                if len(cursor)>512: raise ValueError()
                after = json.loads(base64.urlsafe_b64decode(cursor+'='*(-len(cursor)%4)))
                if not isinstance(after, str) or not 1 <= len(after) <= 50 or '\x00' in after: raise ValueError()
            except (ValueError, UnicodeError, TypeError):
                fail('validation_error', 'Invalid tag cursor', 422)
        with sessions() as db:
            ready(db)
            rows = db.execute(text('''SELECT tag,count(*) AS count FROM research.post_publications p
                JOIN research.post_publication_heads h ON h.publication_id=p.id
                CROSS JOIN LATERAL jsonb_array_elements_text(p.snapshot->'tags') AS tags(tag)
                WHERE (CAST(:after AS text) IS NULL OR tag COLLATE "C" > :after)
                GROUP BY tag ORDER BY tag COLLATE "C" LIMIT :limit'''), {'after': after, 'limit': limit+1}).all()
            cursor = base64.urlsafe_b64encode(json.dumps(rows[limit-1].tag).encode()).decode().rstrip('=') if len(rows)>limit else None
            return public_response(request, {'items': [{'tag': row.tag, 'count': row._mapping['count']} for row in rows[:limit]], 'nextCursor': cursor})
