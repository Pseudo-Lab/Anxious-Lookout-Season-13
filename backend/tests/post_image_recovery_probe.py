"""Run prepare, external pg_dump/pg_restore, then verify in a disposable Docker DB."""
import io
import json
import os
from pathlib import Path
import shutil
import sys
import uuid
from PIL import Image
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from app.migrate import migrate
from app.research import Store
from app.posts import CreateDraft, create_draft, commit_draft
from app.writing import DraftCommit
from app.publications import Publish
from app.post_publications import preview, publish
from app.post_images import upload
from app.restore_research import restore_candidate
from ops.post_image_inventory import inventory
from tests.recovery_probe import signature


def run(mode):
    admin_url = make_url(os.environ['ADMIN_DATABASE_URL'])
    source_url = admin_url.set(database='restore_post_image_source')
    target_url = admin_url.set(database='restore_post_image_candidate')
    source = create_engine(source_url, hide_parameters=True)
    if mode == 'prepare':
        control = create_engine(admin_url, hide_parameters=True)
        with control.connect().execution_options(isolation_level='AUTOCOMMIT') as db:
            db.execute(text('CREATE DATABASE restore_post_image_source'))
            db.execute(text('CREATE DATABASE restore_post_image_candidate'))
        os.environ['ADMIN_DATABASE_URL'] = source_url.render_as_string(hide_password=False)
        os.environ['POST_IMAGE_ROOT'] = '/recovery/source'
        migrate('0007_post_blocks')
        with Session(source) as db, db.begin():
            owner = db.execute(text("INSERT INTO auth.accounts(github_id,login,is_approved,role) VALUES('999999','image-recovery',true,'editor') RETURNING id")).scalar_one()
            store = Store(db, owner)
            output = io.BytesIO(); Image.new('RGB', (20,20), 'blue').save(output, format='PNG')
            image = upload(store, output.getvalue(), 'image/png', '')
            from app.post_block_markdown import convert
            document,_=convert('![image]('+image['url']+')','recovery-fixture')
            draft = create_draft(store, CreateDraft(title='Restored', slug='image-restored', content=document, description='', tags=[], documentIds=[], coverImageId=image['id']))
            post = commit_draft(store, draft['id'], DraftCommit(expectedDraftRevision=1))['post']
            shown = preview(store, post['id'])
            publish(store, post['id'], Publish(**{key: shown[key] for key in ('expectedVersion','versionId','previewToken')}), '')
        with Session(source) as db:
            Path('/recovery/inventory.json').write_text(json.dumps(inventory(db)))
        # Filesystem backup at the same quiesced point as the database dump.
        shutil.copytree('/recovery/source', '/recovery/backup')
        print('Prepared committed image/version/draft/publication; writers stopped')
    elif mode == 'verify':
        target = create_engine(target_url, hide_parameters=True)
        with source.connect() as original, target.connect() as restored:
            assert signature(original) == signature(restored)
        assert restore_candidate(target_url, os.environ['API_DATABASE_PASSWORD'], 'restore_post_image_candidate') == '0007_post_blocks'
        shutil.copytree('/recovery/backup', '/recovery/restored')
        os.environ['POST_IMAGE_ROOT'] = '/recovery/restored'
        with Session(target) as db:
            assert inventory(db) == json.loads(Path('/recovery/inventory.json').read_text())
            assert len(inventory(db)['images']) == 1
            image_id = inventory(db)['images'][0]['id']
            (Path('/recovery/restored')/image_id).unlink()
            try: inventory(db)
            except Exception as exc:
                assert getattr(exc, 'code', None) == 'not_ready'
            else: raise AssertionError('Missing PVC bytes accepted')
        print('PASS real pg_dump/pg_restore + independent file backup/restore, row signatures, grants, digests, dangling/missing bytes fail closed')
    else: raise ValueError('prepare or verify')


if __name__ == '__main__': run(sys.argv[1])
