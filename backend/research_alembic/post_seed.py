"""Frozen, one-time MDX data import. Never evaluates MDX or reads frontend files."""
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
from sqlalchemy import text


def seed(connection):
    root = Path(__file__).resolve().parents[1] / 'db/legacy-posts'
    for path in sorted(root.glob('*.mdx')):
        _, header, body = path.read_text().split('---', 2)
        meta = {key.strip(): json.loads(value.strip()) for key, value in (line.split(':', 1) for line in header.strip().splitlines())}
        slug = path.stem
        post_id = uuid5(NAMESPACE_URL, 'anxious-lookout/legacy-post/'+slug)
        version_id = uuid5(post_id, 'version-1')
        publication_id = uuid5(post_id, 'publication-1')
        when = datetime.fromisoformat(meta['date']).replace(tzinfo=timezone.utc)
        payload = dict(title=meta['title'], content=body.lstrip('\n'), description=meta['description'], slug=slug,
                       tags=meta['tags'], coverImageId=None, documentIds=[])
        snapshot = dict(id=str(publication_id), postId=str(post_id), versionId=str(version_id), slug=slug,
                        title=payload['title'], content=payload['content'], description=payload['description'], tags=payload['tags'],
                        coverImageUrl=None, author={'login': None, 'name': meta['author']},
                        publishedAt=when.isoformat().replace('+00:00', 'Z'), documents=[])
        connection.execute(text('INSERT INTO research.posts(id,legacy_import,slug,created_at,updated_at) VALUES(:id,true,:slug,:at,:at)'),
                           dict(id=post_id, slug=slug, at=when))
        connection.execute(text('INSERT INTO research.post_versions(id,post_id,number,payload,created_at,updated_at) VALUES(:id,:post,1,CAST(:payload AS jsonb),:at,:at)'),
                           dict(id=version_id, post=post_id, payload=json.dumps(payload, ensure_ascii=False), at=when))
        connection.execute(text('INSERT INTO research.post_publications(id,post_id,version_id,snapshot,created_at,updated_at) VALUES(:id,:post,:version,CAST(:snapshot AS jsonb),:at,:at)'),
                           dict(id=publication_id, post=post_id, version=version_id, snapshot=json.dumps(snapshot, ensure_ascii=False), at=when))
        connection.execute(text('INSERT INTO research.post_publication_heads(post_id,publication_id) VALUES(:post,:publication)'),
                           dict(post=post_id, publication=publication_id))
