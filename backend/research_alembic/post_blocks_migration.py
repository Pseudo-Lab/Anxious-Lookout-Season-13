"""0007 data conversion; called inside Alembic's single operator transaction."""
import hashlib
import json
import os
from sqlalchemy import text
from app.post_block_markdown import convert
from app.post_blocks import canonicalize, digest, image_ids, text_projection

TABLES=(('post_versions','payload'),('post_drafts','payload'),('post_proposals','payload'),('post_publications','snapshot'))


def migrate_content(connection):
    # Block new writes across all retained stores for the whole conversion.
    connection.execute(text('LOCK TABLE research.posts, research.post_images, research.post_versions, research.post_drafts, research.post_proposals, research.post_publications, research.post_publication_heads, research.items IN SHARE ROW EXCLUSIVE MODE'))
    for table,column in TABLES:
        rows=connection.execute(text(f'SELECT * FROM research.{table} ORDER BY id')).mappings().all()
        for row in rows:
            payload=dict(row[column]); source=payload.get('content')
            prior=connection.execute(text('SELECT * FROM research.post_markdown_backups WHERE storage_table=:t AND row_id=:id'),{'t':table,'id':row['id']}).mappings().one_or_none()
            if prior:
                if not isinstance(source,dict) or digest(canonicalize(source))!=prior['canonical_sha256'] or hashlib.sha256(prior['markdown'].encode()).hexdigest()!=prior['markdown_sha256']:
                    raise RuntimeError('Post migration preservation mismatch')
                continue
            if not isinstance(source,str): raise RuntimeError('Unmarked post migration content')
            seed=row['version_id'] if table=='post_publications' else row['id']
            document,warnings=convert(source, str(seed), os.environ.get('APP_BASE_PATH',''))
            refs=set(image_ids(document))
            cover=payload.get('coverImageId')
            if table=='post_publications' and payload.get('coverImageUrl'):
                prefix=os.environ.get('APP_BASE_PATH','').rstrip('/')+'/api/media/post-images/'
                if not payload['coverImageUrl'].startswith(prefix): raise RuntimeError('Noncanonical retained cover image')
                cover=payload['coverImageUrl'][len(prefix):]
            if cover: refs.add(cover)
            if refs!={str(x) for x in row['image_ids']}: raise RuntimeError('Post image reference mismatch')
            if len(refs)>50: raise RuntimeError('Post image limit exceeded')
            owner=row.get('owner_id')
            if row.get('post_id'):
                owner=connection.execute(text('SELECT owner_id FROM research.posts WHERE id=:id'),{'id':row['post_id']}).scalar_one()
            documents=set(payload.get('documentIds',[]))
            materials=set(payload.get('materialIds') or [])
            for item in payload.get('documents',[]): documents.add(item['id'])
            for item in payload.get('materials',[]): materials.add(item['id'])
            for item in payload.get('materialSources',[]):
                materials.add(item['materialId']); documents.update(item['documentIds'])
            for kind,identities in (('document',documents),('material',materials)):
                for identity in identities:
                    item=connection.execute(text('SELECT owner_id,kind FROM research.items WHERE id=CAST(:id AS uuid)'),{'id':identity}).one_or_none()
                    if item is None or item.owner_id!=owner or item.kind!=kind:
                        raise RuntimeError('Invalid retained research reference')
            for identity in refs:
                image=connection.execute(text('SELECT owner_id,deleted FROM research.post_images WHERE id=CAST(:id AS uuid)'),{'id':identity}).one_or_none()
                if image is None or image.deleted or image.owner_id!=owner: raise RuntimeError('Invalid retained image owner')
            connection.execute(text('INSERT INTO research.post_markdown_backups(storage_table,row_id,markdown,markdown_sha256,canonical_sha256) VALUES(:t,:id,:markdown,:old,:new)'),{'t':table,'id':row['id'],'markdown':source,'old':hashlib.sha256(source.encode()).hexdigest(),'new':digest(document)})
            payload.update(content=document,textProjection=text_projection(document),conversionWarnings=warnings)
            connection.execute(text(f'UPDATE research.{table} SET {column}=CAST(:p AS jsonb) WHERE id=:id'),{'id':row['id'],'p':json.dumps(payload,ensure_ascii=False)})


def verify_backups(connection):
    """Validate private original/digest pairs on restored operator candidates."""
    for saved in connection.execute(text('SELECT * FROM research.post_markdown_backups')).mappings():
        if hashlib.sha256(saved['markdown'].encode()).hexdigest()!=saved['markdown_sha256']:
            raise RuntimeError('Preserved Markdown digest mismatch')
        seed=saved['row_id']
        if saved['storage_table']=='post_publications':
            seed=connection.execute(text('SELECT version_id FROM research.post_publications WHERE id=:id'),{'id':saved['row_id']}).scalar_one()
        original,_=convert(saved['markdown'],str(seed),os.environ.get('APP_BASE_PATH',''))
        if digest(original)!=saved['canonical_sha256']: raise RuntimeError('Preserved conversion digest mismatch')
