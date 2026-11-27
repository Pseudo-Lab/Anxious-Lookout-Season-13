"""Explicit administrator migration for #101 rollout state and the binding-image write path.

Requires the runner runtime (010), binding and termination (016) schemas and a superuser administrator
(the writer role must not become a member of a CREATEROLE creator; PostgreSQL 16+ records one). Installs 101_runner_rollout.sql once and
validates it; the observer's rights are a separate reviewed grant file (101_runner_rollout_grants.sql).
"""
from pathlib import Path
from sqlalchemy import create_engine, text
from app.settings import secret
from app.runner_bindings import validate as bindings_schema
from app.runner_schema import validate as runtime_schema
from app.runner_termination_schema import validate as termination_schema
from runner.lifecycle import require
from app.runner_rollout_schema import installed, validate


def migrate():
    engine = create_engine(secret('ADMIN_DATABASE_URL'), hide_parameters=True)
    try:
        with engine.begin() as db:
            db.execute(text('SELECT pg_advisory_xact_lock(44101)'))
            require(db.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname=current_user")).scalar_one() is True)
            runtime_schema(db)
            bindings_schema(db)
            termination_schema(db)
            if not installed(db):
                db.connection.driver_connection.execute(
                    (Path(__file__).resolve().parent.parent/'db/101_runner_rollout.sql').read_text())
            validate(db)
            bindings_schema(db)  # The bindings contract is unchanged by the new role and function.
    finally:
        engine.dispose()


if __name__ == '__main__':
    try:
        migrate()
    except Exception:
        raise SystemExit('Rollout migration refused; preserve state') from None
    print('PASS rollout schema; no binding or lifecycle mutation')
