import os
import sys
from pathlib import Path

import psycopg
from psycopg import sql

from .settings import secret


def migrate():
    url = secret("ADMIN_DATABASE_URL").replace("postgresql+psycopg://", "postgresql://", 1)
    password = secret("API_DATABASE_PASSWORD")
    if not url or len(password) < 16:
        raise ValueError("Migration credentials are required")
    with psycopg.connect(url) as conn:
        # Serialize explicit migrations. Never run automatically in API startup.
        conn.execute("SELECT pg_advisory_xact_lock(44004)")
        existing = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'anxious_api'").fetchone()
        if not existing:
            conn.execute(sql.SQL("CREATE ROLE anxious_api LOGIN PASSWORD {}").format(sql.Literal(password)))
        # An existing role's password is never silently rotated by repeat migrations.
        schema = Path(os.getenv("MIGRATION_FILE", "/app/db/001_auth.sql")).read_text()
        conn.execute(schema, prepare=False)
        version = conn.execute("SELECT version FROM auth.schema_version WHERE singleton").fetchone()[0]
        if version != 1:
            raise ValueError("Unsupported migration version")


if __name__ == "__main__":
    try:
        migrate()
    except Exception:
        print("Authentication migration failed; credentials and SQL are not printed", file=sys.stderr)
        sys.exit(1)
    print("Authentication migration v1 complete")
