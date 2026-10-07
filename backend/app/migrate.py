import argparse
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import NullPool

from .schema_contract import AUTH_REVISION, validate_v1
from .settings import secret

RESEARCH_REVISION = "0002_research"
SESSION_REVISION = "0003_sessions"


def migrate(revision=AUTH_REVISION):
    url = secret("ADMIN_DATABASE_URL")
    password = secret("API_DATABASE_PASSWORD")
    if not url or len(password) < 16 or revision not in {AUTH_REVISION, RESEARCH_REVISION, SESSION_REVISION}:
        raise ValueError("Migration credentials are required")
    engine = create_engine(url, poolclass=NullPool, hide_parameters=True, connect_args={"connect_timeout": 3})
    try:
        with engine.begin() as connection:
            connection.execute(text("SELECT pg_advisory_xact_lock(44004)"))
            tables = set(inspect(connection).get_table_names(schema="auth"))
            if tables - {"alembic_version"}:
                validate_v1(connection)  # Never blindly stamp an existing schema.
            existing = connection.execute(text("SELECT 1 FROM pg_roles WHERE rolname='anxious_api'")).first()
            if not existing:
                connection.connection.driver_connection.execute(sql.SQL("CREATE ROLE anxious_api LOGIN PASSWORD {}").format(sql.Literal(password)))
            connection.execute(text("CREATE SCHEMA IF NOT EXISTS auth"))
            cfg = Config()
            cfg.set_main_option("script_location", str(Path(__file__).resolve().parent.parent / "alembic"))
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, AUTH_REVISION)
            validate_v1(connection)
            if connection.execute(text("SELECT version_num FROM auth.alembic_version")).scalar_one() != AUTH_REVISION:
                raise RuntimeError("Authentication Alembic revision mismatch")
            connection.execute(text("REVOKE ALL ON auth.alembic_version FROM PUBLIC"))
            connection.execute(text("GRANT SELECT ON auth.alembic_version TO anxious_api"))
            if revision in {RESEARCH_REVISION, SESSION_REVISION}:
                research_tables = set(inspect(connection).get_table_names(schema="research"))
                if research_tables and "alembic_version" not in research_tables:
                    raise RuntimeError("Unversioned research schema cannot be adopted")
                if research_tables:
                    marker = connection.execute(text("SELECT version_num FROM research.alembic_version")).scalar_one()
                    if marker not in {RESEARCH_REVISION, SESSION_REVISION}:
                        raise RuntimeError("Unknown research revision")
                    if marker == SESSION_REVISION and revision == RESEARCH_REVISION:
                        revision = SESSION_REVISION  # Ensure the minimum revision; never downgrade a known follow-up.
                    from .research_schema_contract import validate_research
                    validate_research(connection)  # Reject incomplete existing schema/grants; never repair silently.
                connection.execute(text("CREATE SCHEMA IF NOT EXISTS research"))
                cfg.set_main_option("script_location", str(Path(__file__).resolve().parent.parent / "research_alembic"))
                command.upgrade(cfg, revision)
                connection.execute(text("REVOKE ALL ON research.alembic_version FROM PUBLIC"))
                connection.execute(text("GRANT SELECT ON research.alembic_version TO anxious_api"))
                from .research_schema_contract import validate_research
                validate_research(connection)
    finally:
        engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reviewed, serialized Alembic authentication upgrade; no automatic downgrade")
    parser.add_argument("--revision", default=AUTH_REVISION)
    args = parser.parse_args()
    try:
        migrate(args.revision)
    except Exception:
        print("Authentication migration failed; credentials and SQL are not printed", file=sys.stderr)
        sys.exit(1)
    print("Reviewed Alembic revision complete")
