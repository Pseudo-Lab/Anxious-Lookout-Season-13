"""The read-only probe runs against a disposable Docker DB only."""
import os

import pytest
from sqlalchemy import text

from common_chatbot_probe import api_check
from app.migrate import migrate
from tests.conftest import schema, admin, client
from tests.test_conversations import create_session
from tests.test_research import identity


@pytest.fixture(scope="module", autouse=True)
def research_schema(schema):
    migrate("0004_publication")


def test_api_probe_counts_running_and_unknown_without_changing_conversations_or_sessions(client, admin, monkeypatch):
    _, auth = identity(client, admin)
    first, second = create_session(client, auth), create_session(client, auth)
    with admin.begin() as db:
        db.execute(text("UPDATE research.conversations SET state='running',request_id=gen_random_uuid() WHERE id=:id"), {"id": first["id"]})
        db.execute(text("UPDATE research.conversations SET state='failed',error_code='codex_unavailable',request_id=gen_random_uuid() WHERE id=:id"), {"id": second["id"]})
    with admin.connect() as db:
        before = db.execute(text("SELECT id,state,request_id,native_record FROM research.conversations ORDER BY id")).all()
        logins = db.execute(text("SELECT count(*) FROM auth.sessions")).scalar_one()
    monkeypatch.setenv("CODEX_PERSONAL_ENABLE", "false")
    result = api_check()
    assert result == {"admissionHeld": True, "schemaReady": True, "apiRunning": 1, "ambiguous": 1}
    with admin.connect() as db:
        assert db.execute(text("SELECT id,state,request_id,native_record FROM research.conversations ORDER BY id")).all() == before
        assert db.execute(text("SELECT count(*) FROM auth.sessions")).scalar_one() == logins


def test_probe_refuses_enabled_admission_before_database_access(monkeypatch):
    monkeypatch.setenv("CODEX_PERSONAL_ENABLE", "true")
    monkeypatch.setenv("DATABASE_URL", "not-a-database-url")
    with pytest.raises(ValueError): api_check()
