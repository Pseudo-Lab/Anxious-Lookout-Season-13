"""#119 B11: GET /api/auth/session answers a signed-out visitor with 200 and the same body as /auth/me when signed in.
No DB: `database()` keeps its lazy engine (never connected) and hands out a fake session factory."""
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.main as main
from config_tests.test_transport_middleware import ORIGIN, settings


class FakeDB:
    def __init__(self, login, account):
        self.login, self.account = login, account

    def scalar(self, statement):
        return self.login

    def get(self, model, identity):
        assert identity == self.account.id
        return self.account


def client(monkeypatch, login=None, account=None):
    real = main.database

    def database(url):
        engine, _ = real(url)

        @contextmanager
        def sessions():
            yield FakeDB(login, account)
        return engine, sessions
    monkeypatch.setattr(main, 'database', database)
    return TestClient(main.create_app(settings()), base_url=ORIGIN)


def test_signed_out_is_200_with_null_user_while_me_stays_401(monkeypatch):
    api = client(monkeypatch)
    reply = api.get('/api/auth/session')
    assert reply.status_code == 200 and reply.json() == {'user': None, 'csrfToken': None}
    assert reply.headers['Cache-Control'] == 'no-store'
    me = api.get('/api/auth/me')
    assert me.status_code == 401 and me.json()['error']['code'] == 'unauthenticated'


@pytest.mark.parametrize('cookie', ['', 'x' * 201])
def test_missing_or_oversized_cookie_is_signed_out(monkeypatch, cookie):
    account = SimpleNamespace(id=uuid4())
    api = client(monkeypatch, SimpleNamespace(account_id=account.id, csrf_token='csrf'), account)
    api.cookies.set(main.SESSION_COOKIE, cookie)
    assert api.get('/api/auth/session').json() == {'user': None, 'csrfToken': None}


def test_signed_in_body_equals_me(monkeypatch):
    account = SimpleNamespace(id=uuid4(), github_id='42', login='octo', role='editor', is_approved=True)
    api = client(monkeypatch, SimpleNamespace(account_id=account.id, csrf_token='csrf-value'), account)
    api.cookies.set(main.SESSION_COOKIE, 'token')
    session, me = api.get('/api/auth/session'), api.get('/api/auth/me')
    assert session.status_code == me.status_code == 200
    assert session.json() == me.json()
    assert session.json()['user']['login'] == 'octo' and session.json()['csrfToken'] == 'csrf-value'
    assert session.headers['Cache-Control'] == 'no-store'
