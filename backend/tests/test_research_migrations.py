import pytest
from sqlalchemy import text

from app.migrate import migrate
from app.research_schema_contract import validate_research
from tests.test_migrations import legacy, migration_db, snapshot


def test_first_research_upgrade_preserves_populated_m2(migration_db):
    engine, _ = migration_db
    legacy(engine)
    migrate()  # Establish a populated pure M2 database before the first M3 upgrade.
    with engine.connect() as db:
        assert db.execute(text("SELECT to_regnamespace('research')")).scalar_one() is None
    before = snapshot(engine)
    migrate("0002_research")
    assert snapshot(engine) == before
    with engine.connect() as db:
        assert validate_research(db) == "0002_research"
        assert db.execute(text("SELECT version_num FROM auth.alembic_version")).scalar_one() == "0001_auth"


def test_failed_first_research_upgrade_retains_populated_m2(migration_db, monkeypatch):
    engine, _ = migration_db
    legacy(engine)
    migrate()
    before = snapshot(engine)
    import app.migrate as runner
    original = runner.command.upgrade
    def injected(config, revision):
        if revision == "0002_research":
            config.attributes["connection"].execute(text("CREATE TABLE research.must_rollback(id integer)"))
            raise RuntimeError("Injected isolated M3 migration failure")
        return original(config, revision)
    monkeypatch.setattr(runner.command, "upgrade", injected)
    with pytest.raises(RuntimeError):
        migrate("0002_research")
    assert snapshot(engine) == before
    with engine.connect() as db:
        assert db.execute(text("SELECT to_regnamespace('research')")).scalar_one() is None
    monkeypatch.setattr(runner.command, "upgrade", original)
    migrate("0002_research")
