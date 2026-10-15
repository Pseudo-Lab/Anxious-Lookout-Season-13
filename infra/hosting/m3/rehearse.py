"""Isolated dump upgrade only; retain all restored auth rows and role password."""
import hashlib
import json
import os
import sys
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool
from psycopg import sql
from app.migrate import migrate
from app.schema_contract import EXPECTED, validate_v1
from app.research_schema_contract import validate_research


def auth_snapshot(conn):
    rows = {}
    for name in EXPECTED:
        digest = hashlib.sha256()
        values = conn.execute(text("SELECT row_to_json(t)::text FROM auth."+name+" t ORDER BY row_to_json(t)::text")).scalars()
        count = 0
        for value in values:
            digest.update(len(value.encode()).to_bytes(8,"big")+value.encode());count+=1
        rows[name] = (count,digest.hexdigest())
    return rows


def run():
    url=os.environ["ADMIN_DATABASE_URL"]
    if not url.startswith("postgresql+psycopg://postgres@127.0.0.1/") or not url.endswith("/restore_m3"):
        raise ValueError("Only offline restore_m3 loopback allowed")
    engine=create_engine(url,poolclass=NullPool,hide_parameters=True,connect_args={"connect_timeout":3})
    try:
        with engine.begin() as conn:
            if conn.execute(text("SELECT current_database()")).scalar_one()!="restore_m3": raise ValueError()
            validate_v1(conn)
            if conn.execute(text("SELECT version_num FROM auth.alembic_version")).scalar_one()!="0001_auth": raise ValueError()
            if conn.execute(text("SELECT to_regnamespace('research') IS NULL")).scalar_one() is not True: raise ValueError()
            # pg_dump --no-owner/privileges excludes role/grant definitions. Rebuild
            # only the frozen AUTH grants on this disconnected clone before testing.
            if conn.execute(text("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='anxious_api')")).scalar_one(): raise ValueError()
            conn.connection.driver_connection.execute(sql.SQL("CREATE ROLE anxious_api LOGIN PASSWORD {}").format(sql.Literal(os.environ["API_DATABASE_PASSWORD"])))
            for statement in Path("/app/db/001_auth.sql").read_text().split(";"):
                statement=statement.strip()
                if statement.startswith(("GRANT ","REVOKE ")): conn.exec_driver_sql(statement)
            before=auth_snapshot(conn)
            password=conn.execute(text("SELECT rolpassword FROM pg_authid WHERE rolname='anxious_api'")).scalar_one()
        migrate("0004_publication")
        with engine.connect() as conn:
            validate_v1(conn)
            if validate_research(conn)!="0004_publication" or auth_snapshot(conn)!=before: raise ValueError()
            if conn.execute(text("SELECT rolpassword FROM pg_authid WHERE rolname='anxious_api'")).scalar_one()!=password: raise ValueError()
        return {"status":"ok","scope":"isolated restore_m3 only","research":"0004_publication",
            "restoredAuthRowsPreserved":True,"rolePasswordPreserved":True,"nativeEnabled":False,"privateRowsPrinted":False}
    finally: engine.dispose()


if __name__=="__main__":
    try: print(json.dumps(run()))
    except Exception:
        print("Isolated M3 rehearsal refused; preserve private diagnostics; no values printed",file=sys.stderr);sys.exit(1)
