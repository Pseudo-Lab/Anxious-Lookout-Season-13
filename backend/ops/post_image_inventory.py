"""Verify DB/PVC consistency before backup and after isolated restore; never mutate."""
import argparse
import json
import os
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app.post_models import PostImage, PostDraft, PostVersion, PostProposal, PostPublication
from app.post_images import read_bytes, root
from app.research_schema_contract import validate_research


def inventory(db):
    if validate_research(db.connection()) != '0006_posts': raise RuntimeError('Posts schema required')
    rows = db.scalars(select(PostImage).order_by(PostImage.id)).all()
    active = {row.id for row in rows if not row.deleted}
    for model in (PostDraft, PostVersion, PostProposal, PostPublication):
        for ids in db.scalars(select(model.image_ids)):
            if not set(ids) <= active: raise RuntimeError('Dangling retained image reference')
    files = []
    for row in rows:
        if not row.deleted:
            read_bytes(row)
            files.append({'id': str(row.id), 'size': row.size, 'sha256': row.sha256})
    known = {str(row.id) for row in rows}
    # Orphans from failed transactions are private. Report but never auto-delete.
    orphans = sorted(path.name for path in root().iterdir() if path.name not in known)
    return {'images': files, 'orphans': orphans}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--expected', help='Compare active image inventory with a saved JSON manifest')
    args = parser.parse_args()
    from app.settings import secret
    engine = create_engine(secret('DATABASE_URL'), hide_parameters=True)
    with Session(engine) as db:
        result = inventory(db)
    if args.expected:
        with open(args.expected) as source: expected = json.load(source)
        if expected['images'] != result['images']: raise RuntimeError('Image inventory mismatch')
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == '__main__': main()
