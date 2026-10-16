"""Explicit administrator migration for durable stop and deferred entry intents."""
from pathlib import Path
from sqlalchemy import create_engine, text
from app.settings import secret
from app.runner_schema import validate as runtime_schema
from app.runner_termination_schema import installed, validate


def migrate():
    engine = create_engine(secret('ADMIN_DATABASE_URL'),hide_parameters=True)
    try:
        with engine.begin() as db:
            db.execute(text('SELECT pg_advisory_xact_lock(44016)'))
            runtime_schema(db)
            if not installed(db):
                db.connection.driver_connection.execute((Path(__file__).resolve().parent.parent/'db/016_runner_termination.sql').read_text())
            validate(db)
    finally:
        engine.dispose()


if __name__ == '__main__':
    try: migrate()
    except Exception: raise SystemExit('Termination migration refused; preserve state') from None
    print('PASS termination schema; no live lifecycle mutation')
