"""Validate a restored candidate DB, reapply reviewed grants, revoke old logins."""
import argparse
import re
import sys
from pathlib import Path

from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from .research_schema_contract import validate_research
from .schema_contract import validate_v1
from .settings import secret


def restore_candidate(url, password, expected_database):
    # Deliberately accept only explicitly named restore candidates, never the
    # ordinary live DB name or the implicit ADMIN_DATABASE_URL environment.
    if not url or len(password) < 16 or not re.fullmatch(r"restore_[a-z0-9_]+", expected_database):
        raise ValueError("Explicit isolated restore candidate inputs are required")
    engine = create_engine(url, poolclass=NullPool, hide_parameters=True, connect_args={"connect_timeout": 3})
    try:
        with engine.begin() as connection:
            if connection.execute(text("SELECT current_database()")).scalar_one() != expected_database:
                raise RuntimeError("Restore database does not match the explicit candidate")
            connection.execute(text("SELECT pg_advisory_xact_lock(44004)"))
            validate_v1(connection)
            if connection.execute(text("SELECT version_num FROM auth.alembic_version")).scalar_one() != "0001_auth":
                raise RuntimeError("Restore authentication revision is unsupported")
            marker = validate_research(connection, check_privileges=False)
            existing = connection.execute(text("SELECT 1 FROM pg_roles WHERE rolname='anxious_api'")).first()
            if not existing:
                connection.connection.driver_connection.execute(sql.SQL("CREATE ROLE anxious_api LOGIN PASSWORD {}").format(sql.Literal(password)))
            frozen = Path(__file__).resolve().parent.parent / "db"
            inputs = ["001_auth.sql", "002_research.sql"]
            if marker == "0003_sessions":
                inputs.append("003_sessions.sql")
            for filename in inputs:
                for statement in (frozen / filename).read_text().split(";"):
                    statement = statement.strip()
                    if statement.startswith(("GRANT ", "REVOKE ")):
                        connection.execute(text(statement))
            for schema in ("auth", "research"):
                connection.execute(text(f"REVOKE ALL ON {schema}.alembic_version FROM PUBLIC"))
                connection.execute(text(f"GRANT SELECT ON {schema}.alembic_version TO anxious_api"))
            validate_research(connection)
            # Restore revokes browser and tool grants without deleting any
            # personal conversations, versions, relations or original homes.
            connection.execute(text("DELETE FROM auth.sessions"))
            connection.execute(text("DELETE FROM auth.oauth_transactions"))
            if marker == "0003_sessions":
                connection.execute(text("""
                    UPDATE research.conversations
                    SET tool_token_hash=NULL, tool_expires_at=NULL, login_hash=NULL,
                        state=CASE WHEN state='running' THEN 'failed' ELSE state END,
                        error_code=CASE WHEN state='running' THEN 'codex_interrupted' ELSE error_code END,
                        version=CASE WHEN state='running' THEN version+1 ELSE version END
                """))
            return marker
    finally:
        engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Explicit new isolated restored candidate; no schema reset/downgrade")
    parser.add_argument("--database", required=True)
    parser.add_argument("--confirm-isolated-candidate", action="store_true", required=True)
    args = parser.parse_args()
    try:
        marker = restore_candidate(secret("RESTORE_DATABASE_URL"), secret("API_DATABASE_PASSWORD"), args.database)
    except Exception:
        print("Isolated research restore validation failed; SQL and credentials are not printed", file=sys.stderr)
        sys.exit(1)
    print(f"PASS isolated candidate {marker}; reviewed grants reapplied, prior logins/tool grants revoked, private records retained")
