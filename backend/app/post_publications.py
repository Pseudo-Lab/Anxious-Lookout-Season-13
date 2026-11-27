"""Current public heads over immutable post snapshots; never expose private bodies."""
import base64
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from fastapi import Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import DateTime, and_, cast, func, or_, select, text
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
        if validate_research(db.connection()) != '0010_research_wiki': raise RuntimeError()
    except RuntimeError:
        fail('not_ready', 'Post publication schema is not ready', 503)


def current(db, post_id=None, slug=None):
    query = select(PostPublication).join(PostPublicationHead, PostPublicationHead.publication_id == PostPublication.id)
    if post_id is not None: query = query.where(PostPublication.post_id == parse_id(post_id))
    if slug is not None: query = query.where(PostPublication.snapshot['slug'].astext == slug)
    return db.scalar(query)


def info(db, post_id):
    row = current(db, post_id=post_id)
    if row is None: return None
    return {**{key: row.snapshot[key] for key in ('id', 'versionId', 'publishedAt', 'slug')}, 'updatedAt': updated_at(row.snapshot),
            'documents': [{'id': value['id'], 'title': value['title'], 'public': value['publicUrl'] is not None, 'publicUrl': value['publicUrl']}
                          for value in documents(row.snapshot, live_documents(db, [row]))]}


def live_documents(db, rows):
    """#119 F4: ids of the documents, referenced by these releases, that have a live research publication now."""
    ids = {parse_id(value['id']) for row in rows for value in row.snapshot.get('documents', [])}
    if not ids: return set()
    return {str(value) for value in db.scalars(select(PublicationHead.document_id).where(
        PublicationHead.document_id.in_(ids), PublicationHead.publication_id.is_not(None)))}


def documents(snapshot, live):
    """Every referenced document with its link as of now; a private or withdrawn one has publicUrl None."""
    from .post_images import base_path
    return [{'id': value['id'], 'title': value['title'],
             'publicUrl': base_path() + '/public/document/?id=' + value['id'] if value['id'] in live else None}
            for value in snapshot.get('documents', [])]


def updated_at(snapshot):
    """Snapshots written before #80 carry only publishedAt, which was then this release's own time."""
    return snapshot.get('updatedAt', snapshot['publishedAt'])


def release_stamps(previous, now):
    """Republishing over a live release keeps its first publishedAt; after a withdraw there is no
    previous release, so the next publish starts a new publishedAt (#80)."""
    when = stamp(now)
    return {'publishedAt': previous['publishedAt'] if previous else when, 'updatedAt': when}


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
            'images': images, 'materials': materials, 'currentPublication': public_value(previous, store.db) if previous else None}


def publish(store, identity, body, base_path):
    shown = preview(store, identity, body.versionId, base_path)
    post = posts.owned(store, identity, active=True)
    posts.expect(post, body.expectedVersion)
    if not hmac.compare_digest(body.previewToken, shown['previewToken']): conflict('preview_changed')
    if not shown['publishable']: fail('validation_error', 'Remove archived document or material references before publishing', 422)
    identity, now = uuid.uuid4(), datetime.now(timezone.utc)
    snapshot = {key: shown[key] for key in ('postId', 'versionId', 'slug', 'title', 'description', 'content', 'textProjection', 'tags', 'author', 'documents', 'materials')}
    from .post_images import url
    snapshot.update(id=str(identity), coverImageUrl=url(shown['coverImageId'], base_path) if shown['coverImageId'] else None,
                    **release_stamps(shown['currentPublication'], now))
    row = PostPublication(id=identity, post_id=post.id, version_id=parse_id(body.versionId), snapshot=snapshot, image_ids=[parse_id(image['id']) for image in shown['images']],
                          created_at=now, updated_at=now)
    store.db.add(row); store.db.flush()
    pointer = store.db.get(PostPublicationHead, post.id)
    if pointer: pointer.publication_id = row.id
    else: store.db.add(PostPublicationHead(post_id=post.id, publication_id=row.id))
    post.version += 1
    post.updated_at = now
    store.db.flush()
    return public_value(row, store.db)


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


def public_value(row, db, live=None):
    """`live` = live_documents() for a batch of rows; read here when not given. Documents that are not public now are
    left out entirely (#119 F4): readers never get a private document's title. The stored snapshot is not changed."""
    from .post_blocks import markdown_projection, format_summary, excerpt
    from .post_images import base_path
    live = live_documents(db, [row]) if live is None else live
    return {**row.snapshot, 'materials': row.snapshot.get('materials', []), 'updatedAt': updated_at(row.snapshot),
            'documents': [value for value in documents(row.snapshot, live) if value['publicUrl'] is not None],
            'excerpt': excerpt(row.snapshot['content']),
            'markdownProjection': markdown_projection(row.snapshot['content'], base_path()),
            'formatSummary': format_summary(row.snapshot['content'])}


def public_response(request, value):
    response = JSONResponse(value)
    etag = '"'+hashlib.sha256(response.body).hexdigest()+'"'
    headers = {'ETag': etag, 'Cache-Control': 'public, max-age=0, must-revalidate'}
    if request.headers.get('if-none-match') == etag:
        return Response(status_code=304, headers=headers)
    response.headers.update(headers)
    return response


def live():
    return select(PostPublication).join(PostPublicationHead, PostPublicationHead.publication_id == PostPublication.id)


def live_filters(tag=None, q=None, author=None):
    """#119 A7: the public list's filters. `q` matches title or body text; `author` is the release's author login."""
    query = live()
    if tag is not None:
        if not 1 <= len(tag) <= 50 or '\x00' in tag: fail('validation_error', 'Invalid tag', 422)
        query = query.where(PostPublication.snapshot['tags'].contains([tag]))
    if q is not None:
        if not 1 <= len(q) <= 200 or '\x00' in q: fail('validation_error', 'Invalid search query', 422)
        query = query.where(or_(PostPublication.snapshot['title'].astext.icontains(q, autoescape=True),
                                PostPublication.snapshot['textProjection'].astext.icontains(q, autoescape=True)))
    if author is not None:
        if not 1 <= len(author) <= 100 or '\x00' in author: fail('validation_error', 'Invalid author', 422)
        query = query.where(PostPublication.snapshot['author']['login'].astext == author)
    return query


SAME_TAG_LIMIT = 5


def published_at():
    return cast(PostPublication.snapshot['publishedAt'].astext, DateTime(timezone=True))


def related_queries(row):
    """#119 F2: neighbours by first publication time (ties by release id), so a republish does not reorder them;
    up to SAME_TAG_LIMIT other live releases sharing a tag, newest first. sameTag is None without tags."""
    mine = datetime.fromisoformat(row.snapshot['publishedAt'].replace('Z', '+00:00'))
    other = live().where(PostPublication.id != row.id)
    previous = other.where(or_(published_at() < mine, and_(published_at() == mine, PostPublication.id < row.id))) \
        .order_by(published_at().desc(), PostPublication.id.desc()).limit(1)
    following = other.where(or_(published_at() > mine, and_(published_at() == mine, PostPublication.id > row.id))) \
        .order_by(published_at().asc(), PostPublication.id.asc()).limit(1)
    tags = row.snapshot.get('tags') or []
    same = other.where(or_(*[PostPublication.snapshot['tags'].contains([tag]) for tag in tags])) \
        .order_by(published_at().desc(), PostPublication.id.desc()).limit(SAME_TAG_LIMIT) if tags else None
    return previous, following, same


def post_ref(row):
    if row is None: return None
    return {'slug': row.snapshot['slug'], 'title': row.snapshot['title'], 'publishedAt': row.snapshot['publishedAt'],
            'tags': row.snapshot.get('tags', []), 'author': row.snapshot.get('author')}


def count(query):
    return select(func.count()).select_from(query.order_by(None).subquery())


def author_counts():
    return text('''SELECT p.snapshot->'author'->>'login' AS login, count(*) AS count FROM research.post_publications p
        JOIN research.post_publication_heads h ON h.publication_id=p.id
        WHERE p.snapshot->'author'->>'login' IS NOT NULL
        GROUP BY p.snapshot->'author'->>'login' ORDER BY (p.snapshot->'author'->>'login') COLLATE "C"''')
# COLLATE binds tighter than ->>: without the parentheses it applies to the 'login' key literal (review blocker).


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
    def listing(request: Request, tag: str | None = None, q: str | None = None, author: str | None = None,
                limit: int = 30, cursor: str | None = None):
        with sessions() as db:
            ready(db)
            query = live_filters(tag, q, author)
            rows = db.scalars(page_query(query, PostPublication, limit, cursor)).all()
            live = live_documents(db, rows[:limit])
            value = {'items': [{key: value for key, value in public_value(row, db, live).items() if key != 'content'} for row in rows[:limit]],
                     'nextCursor': encode_cursor(rows[limit-1]) if len(rows)>limit else None,
                     'total': db.scalar(count(query))}
            return public_response(request, value)

    @app.get(public + '/post-authors')
    def authors(request: Request):
        with sessions() as db:
            ready(db)
            rows = db.execute(author_counts()).all()
            return public_response(request, {'items': [{'login': row.login, 'count': row._mapping['count']} for row in rows]})

    @app.get(public + '/posts/{slug}')
    def detail(slug: str, request: Request):
        with sessions() as db:
            ready(db)
            row = current(db, slug=slug)
            if row is None: fail('not_found', 'Not found', 404)
            return public_response(request, public_value(row, db))

    @app.get(public + '/posts/{slug}/related')
    def neighbours(slug: str, request: Request):
        with sessions() as db:
            ready(db)
            row = current(db, slug=slug)
            if row is None: fail('not_found', 'Not found', 404)
            previous, following, same = related_queries(row)
            return public_response(request, {'previous': post_ref(db.scalar(previous)), 'next': post_ref(db.scalar(following)),
                                             'sameTag': [post_ref(value) for value in db.scalars(same).all()] if same is not None else []})

    @app.get(public + '/post-tags')
    def tags(request: Request, limit: int = 30, cursor: str | None = None, q: str | None = None):
        if not 1 <= limit <= 100: fail('validation_error', 'Invalid limit', 422)
        if q is not None and (not 1 <= len(q) <= 50 or '\x00' in q): fail('validation_error', 'Invalid tag query', 422)
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
                  AND (CAST(:q AS text) IS NULL OR strpos(lower(tag), lower(CAST(:q AS text))) > 0)
                GROUP BY tag ORDER BY tag COLLATE "C" LIMIT :limit'''), {'after': after, 'q': q, 'limit': limit+1}).all()
            cursor = base64.urlsafe_b64encode(json.dumps(rows[limit-1].tag).encode()).decode().rstrip('=') if len(rows)>limit else None
            return public_response(request, {'items': [{'tag': row.tag, 'count': row._mapping['count']} for row in rows[:limit]], 'nextCursor': cursor})
