"""Existing API config DB connection/readonly schema stages; no Codex source read.

Uses only existing DATABASE_URL (as readyz does), not GitHub/session/Codex secrets.
Never prints DSN, host, error message, parameters, rows, or credential values.
"""
import json

from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.database import database
from app.schema_contract import AUTH_REVISION
from app.settings import secret
from access_network_diagnose import probe


def diagnose():
    report = {'sourceCredentialRead': False, 'dataModified': False, 'dbConnection': 'not_tested',
              'schemaContract': 'not_tested', 'accountTableAccessible': False}
    engine = None
    try:
        url = secret('DATABASE_URL')  # existing application's one configured DB credential
        target = make_url(url)
        network = probe(target.host, target.port or 5432)
        report['dbNetwork'] = network
        if network['dns'] != 'resolved' or network['tcp'] != 'connected':
            return report
        engine, _ = database(url)
        with engine.connect() as connection:
            with connection.begin():
                connection.execute(text('SET TRANSACTION READ ONLY'))
                connection.execute(text("SET LOCAL statement_timeout = '3s'"))
                connection.execute(text("SET LOCAL lock_timeout = '1s'"))
                report['dbConnection'] = 'connected'
                version = connection.execute(text('SELECT version FROM auth.schema_version WHERE singleton=true')).scalar_one()
                revision = connection.execute(text('SELECT version_num FROM auth.alembic_version')).scalar_one()
                report['schemaContract'] = 'matches' if version == 1 and revision == AUTH_REVISION else 'mismatch'
                connection.execute(text('SELECT id FROM auth.accounts LIMIT 0'))
                report['accountTableAccessible'] = True
    except Exception:
        if report['dbConnection'] == 'not_tested':
            report['dbConnection'] = 'failed'
        else:
            report['schemaContract'] = 'query_failed'
    finally:
        if engine:
            engine.dispose()
    return report


if __name__ == '__main__':
    import signal
    # Only the disposable stdin-exec helper, not the API's serving process.
    # Default signal action also terminates a blocked native driver/socket wait.
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    signal.alarm(20)
    print(json.dumps(diagnose()))
