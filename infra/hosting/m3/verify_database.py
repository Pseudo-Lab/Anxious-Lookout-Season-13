"""Read-only existing DB checks; no migration, role/session changes or value output."""
import json
import os
import sys
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool
from app.settings import secret
from app.schema_contract import validate_v1
from app.research_schema_contract import validate_research


def check(stage):
    if stage not in {"before", "after"}:
        raise ValueError()
    admin, api = make_url(secret("ADMIN_DATABASE_URL")), make_url(secret("DATABASE_URL"))
    aliases = {"postgres", "postgres.m2-hosting.svc", "postgres.m2-hosting.svc.cluster.local"}
    if (admin.drivername != "postgresql+psycopg" or api.drivername != "postgresql+psycopg"
            # SQLAlchemy query keys override authority connection fields. No URL
            # query (including service/options/hostaddr or SSL options) is supported.
            or admin.query or api.query
            or admin.host not in aliases or api.host not in aliases
            or admin.database != "hosting" or api.database != "hosting"
            or admin.username != "postgres" or api.username != "anxious_api"
            or admin.port not in {None, 5432} or api.port not in {None, 5432}
            or not admin.password or api.password != secret("API_DATABASE_PASSWORD") or len(api.password) < 16):
        raise ValueError()
    for role, url in (("admin", admin), ("api", api)):
        engine = create_engine(url, poolclass=NullPool, hide_parameters=True, connect_args={"connect_timeout": 3})
        try:
            with engine.connect() as conn:
                conn.exec_driver_sql("SET TRANSACTION READ ONLY")
                conn.exec_driver_sql("SET LOCAL statement_timeout = 10000")
                identity = conn.execute(text("SELECT current_database(), current_user, session_user")).one()
                expected_user = "postgres" if role == "admin" else "anxious_api"
                if tuple(identity) != ("hosting", expected_user, expected_user):
                    raise ValueError()
                validate_v1(conn)
                if conn.execute(text("SELECT version_num FROM auth.alembic_version")).scalar_one() != "0001_auth":
                    raise ValueError()
                if role == "admin":
                    if not conn.execute(text("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='anxious_api' AND NOT rolsuper)")).scalar_one():
                        raise ValueError()
                    if stage == "before" and conn.execute(text("SELECT to_regnamespace('research') IS NULL")).scalar_one() is not True:
                        raise ValueError()
                if stage == "after" and validate_research(conn) != "0004_publication":
                    raise ValueError()
                conn.rollback()
        finally:
            engine.dispose()
    return {"status": "ok", "stage": stage, "auth": "0001_auth",
            "research": None if stage == "before" else "0004_publication", "nativeEnabled": False, "credentialsPrinted": False}


if __name__ == "__main__":
    try:
        print(json.dumps(check(sys.argv[1])))
    except Exception:
        print("Existing M3 DB verification refused; no SQL/credential detail printed", file=sys.stderr)
        sys.exit(1)
