import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.database import check_ready
from app.migrate import migrate


@pytest.fixture
def migration_db(monkeypatch):
    original = make_url(os.environ["ADMIN_DATABASE_URL"])
    name = "m2_migration_" + uuid.uuid4().hex
    control = create_engine(original, isolation_level="AUTOCOMMIT", hide_parameters=True)
    with control.connect() as conn:
        conn.exec_driver_sql(f'CREATE DATABASE "{name}"')
    url = original.set(database=name)
    engine = create_engine(url, hide_parameters=True)
    monkeypatch.setenv("ADMIN_DATABASE_URL", url.render_as_string(hide_password=False))
    yield engine, url
    engine.dispose()
    with control.connect() as conn:
        conn.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
    control.dispose()


def legacy(engine):
    with engine.begin() as conn:
        conn.connection.driver_connection.execute(Path("/app/db/001_auth.sql").read_text(), prepare=False)
        account = conn.execute(text("INSERT INTO auth.accounts(github_id,login,role,is_approved) VALUES('333333','legacy','editor',true) RETURNING id")).scalar_one()
        conn.execute(text("INSERT INTO auth.sessions(token_hash,account_id,csrf_token,expires_at) VALUES(:token,:account,'legacy-csrf',now()+interval '1 hour')"), {"token": "a" * 64, "account": account})
        conn.execute(text("INSERT INTO auth.oauth_transactions VALUES(:state,:binding,'legacy-encrypted',now()+interval '1 minute')"), {"state": "b" * 64, "binding": "c" * 64})
        conn.execute(text("INSERT INTO auth.permission_audit(actor,account_id,reason,before,after) VALUES('verified-legacy-operator',:account,'preserve','{}','{}')"), {"account": account})
    return account


def test_empty_database_and_repeat_preserve_role_password(migration_db, monkeypatch):
    engine, url = migration_db
    with engine.connect() as conn:
        before = conn.execute(text("SELECT md5(rolpassword) FROM pg_authid WHERE rolname='anxious_api'")).scalar_one()
    migrate()
    monkeypatch.setenv("API_DATABASE_PASSWORD", "different-disposable-password")
    migrate()
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM auth.alembic_version")).scalar_one() == "0001_auth"
        assert conn.execute(text("SELECT md5(rolpassword) FROM pg_authid WHERE rolname='anxious_api'")).scalar_one() == before
    api_url = make_url(os.environ["DATABASE_URL"]).set(database=url.database)
    api_engine = create_engine(api_url, hide_parameters=True)
    check_ready(api_engine)
    api_engine.dispose()


def test_verified_v1_adoption_preserves_all_data(migration_db):
    engine, _ = migration_db
    account = legacy(engine)
    migrate()
    with engine.connect() as conn:
        row = conn.execute(text("SELECT id,role,is_approved FROM auth.accounts")).one()
        assert row == (account, "editor", True)
        for table in ("sessions", "oauth_transactions", "permission_audit"):
            assert conn.execute(text(f"SELECT count(*) FROM auth.{table}")).scalar_one() == 1
        assert conn.execute(text("SELECT version_num FROM auth.alembic_version")).scalar_one() == "0001_auth"


def test_incompatible_v1_never_stamped_or_repaired(migration_db):
    engine, _ = migration_db
    account = legacy(engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE auth.accounts DROP COLUMN is_approved"))
    with pytest.raises(RuntimeError):
        migrate()
    with engine.connect() as conn:
        assert conn.execute(text("SELECT to_regclass('auth.alembic_version')")).scalar_one() is None
        assert conn.execute(text("SELECT id FROM auth.accounts")).scalar_one() == account


@pytest.mark.parametrize("alteration", [
    "ALTER TABLE auth.accounts ALTER COLUMN id DROP DEFAULT",
    "ALTER TABLE auth.accounts DROP CONSTRAINT accounts_github_id_check",
    "ALTER TABLE auth.schema_version DROP CONSTRAINT schema_version_singleton_check",
])
def test_incompatible_v1_contract_never_adopted(migration_db, alteration):
    engine, _ = migration_db
    account = legacy(engine)
    with engine.begin() as conn:
        conn.execute(text(alteration))
    with pytest.raises(RuntimeError):
        migrate()
    with engine.connect() as conn:
        assert conn.execute(text("SELECT to_regclass('auth.alembic_version')")).scalar_one() is None
        assert conn.execute(text("SELECT id FROM auth.accounts")).scalar_one() == account


def test_failed_revision_rolls_back_and_can_retry(migration_db, monkeypatch):
    engine, _ = migration_db
    import app.migrate as runner
    real_upgrade = runner.command.upgrade
    def fail_revision(config, revision):
        config.attributes["connection"].execute(text("CREATE TABLE auth.must_rollback(id integer)"))
        raise RuntimeError("Injected disposable revision failure")
    monkeypatch.setattr(runner.command, "upgrade", fail_revision)
    with pytest.raises(RuntimeError):
        migrate()
    with engine.connect() as conn:
        assert conn.execute(text("SELECT to_regclass('auth.must_rollback')")).scalar_one() is None
    monkeypatch.setattr(runner.command, "upgrade", real_upgrade)
    migrate()


def test_concurrent_revision_serialized(migration_db):
    with ThreadPoolExecutor(max_workers=2) as workers:
        list(workers.map(lambda _: migrate(), range(2)))
    engine, _ = migration_db
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM auth.alembic_version")).scalar_one() == 1


def test_unknown_revision_keeps_existing_v1(migration_db):
    engine, _ = migration_db
    account = legacy(engine)
    with pytest.raises(ValueError):
        migrate("unreviewed-head")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT id FROM auth.accounts")).scalar_one() == account


def test_application_cannot_write_alembic_version(migration_db):
    engine, url = migration_db
    migrate()
    api = create_engine(make_url(os.environ["DATABASE_URL"]).set(database=url.database), hide_parameters=True)
    from sqlalchemy.exc import DBAPIError
    with pytest.raises(DBAPIError) as denied:
        with api.begin() as conn:
            conn.execute(text("UPDATE auth.alembic_version SET version_num='forged'"))
    assert denied.value.orig.sqlstate == "42501"
    api.dispose()


def test_readiness_requires_exact_alembic_revision(migration_db):
    engine, _ = migration_db
    legacy(engine)
    with pytest.raises(Exception):
        check_ready(engine)
    migrate()
    check_ready(engine)
    with engine.begin() as conn:
        conn.execute(text("UPDATE auth.alembic_version SET version_num='unreviewed'"))
    with pytest.raises(RuntimeError):
        check_ready(engine)
    with pytest.raises(Exception):
        migrate()
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM auth.alembic_version")).scalar_one() == "unreviewed"
