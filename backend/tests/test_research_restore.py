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
