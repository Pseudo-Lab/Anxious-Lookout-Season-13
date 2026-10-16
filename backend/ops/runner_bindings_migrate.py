"""Explicit additive #16 migration; requires deployment/admin credentials."""
from pathlib import Path
from sqlalchemy import create_engine, inspect, text
from app.settings import secret
from app.runner_schema import validate as validate_runtime
from app.runner_bindings import validate


def migrate():
    engine = create_engine(secret('ADMIN_DATABASE_URL'), hide_parameters=True)
    try:
        with engine.begin() as db:
            db.execute(text('SELECT pg_advisory_xact_lock(44016)'))
            validate_runtime(db)
            if 'runner_bindings' not in inspect(db).get_schema_names():
                db.connection.driver_connection.execute((Path(__file__).resolve().parent.parent/'db/016_runner_bindings.sql').read_text())
            validate(db)
    finally:
        engine.dispose()


if __name__ == '__main__':
    try:
        migrate()
    except Exception:
        raise SystemExit('Binding migration refused; no SQL or credentials printed') from None
    print('PASS immutable binding schema; no runtime resources modified')
