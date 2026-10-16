import os

import pytest
from sqlalchemy import text

from app.restore_research import restore_candidate


def test_restore_candidate_rejects_live_or_mismatched_target_without_mutation(admin):
    with admin.connect() as db:
        before = db.execute(text("SELECT version_num FROM auth.alembic_version")).scalar_one()
    with pytest.raises(ValueError):
        restore_candidate(os.environ["ADMIN_DATABASE_URL"], os.environ["API_DATABASE_PASSWORD"], "hosting_test")
    with pytest.raises(RuntimeError):
        restore_candidate(os.environ["ADMIN_DATABASE_URL"], os.environ["API_DATABASE_PASSWORD"], "restore_different_database")
    with admin.connect() as db:
        assert db.execute(text("SELECT version_num FROM auth.alembic_version")).scalar_one() == before


def test_writing_restore_retains_drafts_proposals_and_reapplies_grants(admin, monkeypatch):
    import uuid
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url
    from sqlalchemy.orm import Session
    from app.migrate import migrate
    from app.research import Store, DocumentInput
    from app.research_tools import EditDocument
    from app.writing import DraftCreate, create_draft, create_proposal
    from app.research_schema_contract import validate_research
    database = 'restore_writing_' + uuid.uuid4().hex
    url = make_url(os.environ['ADMIN_DATABASE_URL']).set(database=database)
    candidate = None
    with admin.connect().execution_options(isolation_level='AUTOCOMMIT') as control:
        control.execute(text('CREATE DATABASE ' + database))
    try:
        monkeypatch.setenv('ADMIN_DATABASE_URL', url.render_as_string(hide_password=False))
        migrate('0005_writing')
        candidate = create_engine(url, hide_parameters=True)
        with Session(candidate) as db, db.begin():
            owner = db.execute(text("INSERT INTO auth.accounts(github_id,login,is_approved,role) VALUES('333333','restore-test',true,'editor') RETURNING id")).scalar_one()
            store = Store(db, owner)
            doc = store.create_item('document', DocumentInput(title='Saved', content='Version'))
            draft = create_draft(store, DraftCreate(title='Draft', content='Retained draft', documentId=doc['id'],
                baseDocumentVersion=1, baseVersionId=doc['latestVersion']['id']))
            proposal = create_proposal(store, EditDocument(id=doc['id'], expectedVersion=1, title='Proposal',
                content='Retained proposal', mutationId=str(uuid.uuid4())))
        with candidate.begin() as db:
            db.execute(text('REVOKE UPDATE ON research.document_drafts FROM anxious_api'))
        assert restore_candidate(url.render_as_string(hide_password=False), os.environ['API_DATABASE_PASSWORD'], database) == '0005_writing'
        with candidate.connect() as db:
            assert validate_research(db) == '0005_writing'
            assert db.execute(text('SELECT content,state FROM research.document_drafts WHERE id=:id'), {'id': draft['id']}).one() == ('Retained draft', 'active')
            assert db.execute(text('SELECT content,status FROM research.document_proposals WHERE id=:id'), {'id': proposal['id']}).one() == ('Retained proposal', 'pending')
            assert db.execute(text('SELECT count(*) FROM research.versions')).scalar_one() == 1
    finally:
        if candidate is not None: candidate.dispose()
        with admin.connect().execution_options(isolation_level='AUTOCOMMIT') as control:
            control.execute(text('DROP DATABASE ' + database + ' WITH (FORCE)'))
