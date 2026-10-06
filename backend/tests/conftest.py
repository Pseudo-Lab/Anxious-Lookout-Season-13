import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.main import create_app
from app.migrate import migrate


@pytest.fixture(scope="session", autouse=True)
def schema():
    migrate()


@pytest.fixture
def admin(schema):
    engine = create_engine(os.environ["ADMIN_DATABASE_URL"], hide_parameters=True)
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE auth.permission_audit, auth.sessions, auth.oauth_transactions, auth.accounts CASCADE"))
    yield engine
    engine.dispose()


@pytest.fixture
def client(admin):
    app = create_app()
    with TestClient(app, base_url=os.environ["AUTH_ORIGIN"], follow_redirects=False, raise_server_exceptions=False) as client:
        yield client
