"""Private immutable image files; current publication heads grant public reads."""
import asyncio
import hashlib
import io
import os
from pathlib import Path
import stat
import tempfile
import threading
import uuid
import warnings
from fastapi import Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from markdown_it import MarkdownIt
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select, func
from .post_models import PostImage, PostDraft, PostProposal, PostVersion, PostPublication, PostPublicationHead
from .research import Input, ResearchError, fail, stamp, parse_id
from . import posts

LIMIT = 5 * 1024 * 1024
QUOTA = 100 * 1024 * 1024
FORMATS = {'image/png': 'PNG', 'image/jpeg': 'JPEG', 'image/webp': 'WEBP'}
DECODERS = threading.BoundedSemaphore(2)


def root():
    path = Path(os.environ.get('POST_IMAGE_ROOT', '/var/lib/posts/images/data'))
    try:
        if not path.is_absolute() or path.resolve() != path: raise OSError()
        # Parent must already be the mounted volume; never create a fallback tree.
        path.mkdir(mode=0o700, exist_ok=True)
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid(): raise OSError()
        # CSI fsGroup may widen mode bits on a restored volume; tighten the
        # API-owned gate before any access rather than exposing retained files.
        if info.st_mode & 0o077: path.chmod(0o700)
        return path
    except OSError:
        fail('not_ready', 'Image storage is not ready', 503)


def url(identity, base_path=''):
    return base_path + '/api/media/post-images/' + str(identity)


def base_path():
    return os.environ.get('APP_BASE_PATH', '').rstrip('/')


def value(row, prefix=''):
    return {'id': str(row.id), 'url': url(row.id, prefix), 'mimeType': row.mime_type,
            'size': row.size, 'width': row.width, 'height': row.height, 'createdAt': stamp(row.created_at)}


def references(payload, prefix=None):
    prefix = base_path() if prefix is None else prefix
    found = set()
    if payload['coverImageId']: found.add(parse_id(payload['coverImageId']))
    def walk(tokens):
        for token in tokens:
            if token.type == 'image':
                source = token.attrGet('src') or ''
                start = prefix + '/api/media/post-images/'
                if source.startswith(start):
                    try:
                        identity = uuid.UUID(source[len(start):])
                        if source == url(identity, prefix): found.add(identity)
                    except ValueError: pass
            if token.children: walk(token.children)
    walk(MarkdownIt('commonmark').parse(payload['content']))
    return sorted(found, key=str)


def read_bytes(row):
    try:
        fd = os.open(root() / str(row.id), os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode): raise OSError()
            data = stream.read(LIMIT + 1)
        if len(data) != row.size or hashlib.sha256(data).hexdigest() != row.sha256: raise OSError()
        return data
    except OSError:
        fail('not_ready', 'Image bytes are unavailable', 503)


def validate(store, payload):
    identities = references(payload)
    for identity in identities:
        row = store.db.get(PostImage, identity)
        if row is None or row.owner_id != store.owner or row.deleted:
            fail('not_found', 'Image not found', 404)
        read_bytes(row)
    return identities


def normalize(data, mime):
    if mime not in FORMATS: fail('unsupported_media_type', 'Use PNG, JPEG or WebP', 415)
    if len(data) > LIMIT: fail('payload_too_large', 'Image exceeds size limit', 413)
    if not data: fail('validation_error', 'Empty image', 422)
    with DECODERS, warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        try:
            with Image.open(io.BytesIO(data)) as original:
                w, h = original.size
                if original.format != FORMATS[mime] or getattr(original, 'n_frames', 1) != 1:
                    raise ValueError()
                if not (0 < w <= 8192 and 0 < h <= 8192 and w*h <= 16000000): raise ValueError()
                original.load()
                mode = 'RGB' if mime == 'image/jpeg' else 'RGBA'
                # A new pixel-only image drops EXIF, ICC, comments and text chunks.
                clean = Image.new(mode, (w, h))
                clean.paste(original.convert(mode))
                output = io.BytesIO()
                clean.save(output, format=FORMATS[mime])
                result = output.getvalue()
                if len(result) > LIMIT: fail('payload_too_large', 'Encoded image exceeds size limit', 413)
                return result, w, h
        except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            fail('validation_error', 'Invalid or oversized image', 422)


def upload(store, data, mime, prefix):
    posts.ready(store); store.graph_lock()
    data, width, height = normalize(data, mime)
    used = store.db.scalar(select(func.coalesce(func.sum(PostImage.size), 0)).where(PostImage.owner_id == store.owner, PostImage.purged.is_(False)))
    if used + len(data) > QUOTA: fail('storage_quota_exceeded', 'Image storage quota exceeded', 413)
    row = PostImage(id=uuid.uuid4(), owner_id=store.owner, mime_type=mime, size=len(data), width=width, height=height,
                    sha256=hashlib.sha256(data).hexdigest())
    directory = root()
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix='.upload-', dir=directory)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.link(temporary, directory / str(row.id))  # never overwrite an existing immutable file
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory_fd)
        finally: os.close(directory_fd)
    except OSError:
        fail('not_ready', 'Image storage is not ready', 503)
    finally:
        if temporary is not None: os.unlink(temporary)
    store.db.add(row); store.db.flush()
    return value(row, prefix)


def remove(store, identity):
    posts.ready(store); store.graph_lock()
    row = store.db.get(PostImage, identity)
    if row is None or row.owner_id != store.owner: fail('not_found', 'Not found', 404)
    for model in (PostDraft, PostProposal, PostVersion, PostPublication):
        if store.db.scalar(select(model.id).where(model.image_ids.contains([identity])).limit(1)):
            posts.conflict('image_in_use')
    row.deleted = True
    store.db.flush()
    return {'id': str(identity), 'deleted': True}


def purge(store, identity):
    store.graph_lock()
    row = store.db.get(PostImage, identity)
    if row and row.owner_id == store.owner and row.deleted and not row.purged:
        try:
            (root() / str(identity)).unlink(missing_ok=True)
            row.purged = True
            store.db.flush()
        except (OSError, ResearchError):
            pass  # Tombstone retains quota until a later delete retries physical cleanup.


def register(app, authorized, sessions, settings, response, write):
    private = settings.base_path + '/api/research/post-images'

    @app.post(private)
    async def create(request: Request):
        def admit():
            with authorized(request, True) as store:
                posts.ready(store)
                key = request.headers.get('idempotency-key')
                if not key: fail('validation_error', 'Idempotency-Key is required', 422)
                parse_id(key)
        await run_in_threadpool(admit)
        body = bytearray()
        try:
            async with asyncio.timeout(30):
                async for chunk in request.stream():
                    if len(body) + len(chunk) > LIMIT: fail('payload_too_large', 'Image exceeds size limit', 413)
                    body.extend(chunk)
        except TimeoutError:
            fail('validation_error', 'Upload timed out', 408)
        data = bytes(body)
        mime = request.headers.get('content-type', '')
        def save():
            with authorized(request, True) as store:
                result, status = store.mutate(request.headers['idempotency-key'], 'POST '+private,
                    {'sha256': hashlib.sha256(data).hexdigest(), 'mime': mime},
                    lambda: upload(store, data, mime, settings.base_path), 201)
                return response(result, status)
        return await run_in_threadpool(save)

    @app.get(private)
    def listing(request: Request, limit: int = 30, cursor: str | None = None):
        with authorized(request) as store:
            posts.ready(store)
            query = select(PostImage).where(PostImage.owner_id == store.owner, PostImage.deleted.is_(False))
            return response(posts.listing(store, PostImage, query, lambda row: value(row, settings.base_path), limit, cursor))

    @app.delete(private + '/{identity}')
    def delete(identity: uuid.UUID, body: dict, request: Request):
        with authorized(request, True) as store:
            result = write(store, request, Input, body, lambda parsed: remove(store, identity))
        # Tombstone commits before unlink. Retry also performs cleanup on idempotent replay.
        with authorized(request, True) as store: purge(store, identity)
        return result

    @app.get(settings.base_path + '/api/media/post-images/{identity}')
    def media(identity: uuid.UUID, request: Request):
        from .post_publications import ready
        with sessions() as db:
            ready(db)
            row = db.get(PostImage, identity)
            if row is None or row.deleted: fail('not_found', 'Not found', 404)
            public = db.scalar(select(PostPublication.id).join(PostPublicationHead,
                PostPublicationHead.publication_id == PostPublication.id).where(PostPublication.image_ids.contains([identity])).limit(1)) is not None
            if not public:
                try:
                    with authorized(request) as store:
                        if row.owner_id != store.owner: fail('not_found', 'Not found', 404)
                except ResearchError as exc:
                    if exc.status in {401, 403}: fail('not_found', 'Not found', 404)
                    raise
            data = read_bytes(row)
            etag = '"'+row.sha256+'"'
            headers = {'Cache-Control': 'public, max-age=0, must-revalidate' if public else 'no-store',
                       'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': "default-src 'none'; sandbox",
                       'Content-Disposition': 'inline', 'ETag': etag}
            if public and request.headers.get('if-none-match') == etag: return Response(status_code=304, headers=headers)
            return Response(data, media_type=row.mime_type, headers=headers)
