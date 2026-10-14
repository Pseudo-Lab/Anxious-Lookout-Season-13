"""Explicit additive runtime registry migration; never startup DDL or downgrade."""
from pathlib import Path
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import NullPool
from .settings import secret
from .schema_contract import validate_v1
from .runner_schema import validate


def migrate():
    url = secret('ADMIN_DATABASE_URL')
    if not url:
        raise ValueError('Migration credentials required')
    engine = create_engine(url, poolclass=NullPool, hide_parameters=True, connect_args={'connect_timeout': 3})
    try:
        with engine.begin() as connection:
            connection.execute(text('SELECT pg_advisory_xact_lock(44010)'))
            validate_v1(connection)
            if 'runner_runtime' in inspect(connection).get_schema_names():
                validate(connection)  # Existing unknown/partial schemas are not repaired/adopted.
            else:
                source = Path(__file__).resolve().parent.parent / 'db' / '010_runner_runtime.sql'
                connection.connection.driver_connection.execute(source.read_text())
                validate(connection)
    finally:
        engine.dispose()


if __name__ == '__main__':
    import sys
    try:
        migrate()
    except Exception:
        print('Runner runtime migration refused; credentials/SQL are not printed', file=sys.stderr)
        raise SystemExit(1)
    print('PASS additive runner runtime schema; no Pod/provider/credential actions')
