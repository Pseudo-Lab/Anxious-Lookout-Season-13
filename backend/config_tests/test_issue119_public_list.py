"""#119 A7/E2/O10: public list q/author/total, post-authors, post-tags q. No DB: statements are compiled or captured."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from app import post_publications as pp
from app.research import ResearchError


def sql(statement):
    return str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={'literal_binds': True}))


def bound(statement):
    compiled = statement.compile(dialect=postgresql.dialect())
    return str(compiled), compiled.params


def test_q_matches_title_or_body_text_with_escaped_wildcards():
    rendered, params = bound(pp.live_filters(q='50%_off'))
    assert "snapshot ->> %(snapshot_1)s" in rendered and rendered.count('ILIKE') == 2 and ' OR ' in rendered
    assert {params['snapshot_1'], params['snapshot_2']} == {'title', 'textProjection'}
    assert params['param_1'] == params['param_2'] == '50/%/_off' and "ESCAPE '/'" in rendered


def test_author_is_exact_login_and_filters_combine():
    rendered, params = bound(pp.live_filters(tag='AI', q='x', author='octo'))
    assert '@>' in rendered and 'JOIN research.post_publication_heads' in rendered
    assert ['author', 'login', 'octo'] == [value for value in params.values() if value in ('author', 'login', 'octo')]
    assert rendered.count('ILIKE') == 2


def test_no_filters_is_every_live_release():
    rendered = sql(pp.live_filters())
    assert 'WHERE' not in rendered and 'JOIN research.post_publication_heads' in rendered


@pytest.mark.parametrize('kwargs', [{'q': ''}, {'q': 'a' * 201}, {'q': 'a\x00'}, {'author': ''}, {'author': 'a' * 101},
                                    {'author': 'a\x00'}, {'tag': ''}, {'tag': 'a' * 51}])
def test_invalid_filters_are_422(kwargs):
    with pytest.raises(ResearchError) as caught:
        pp.live_filters(**kwargs)
    assert caught.value.args[0] == 'validation_error' and caught.value.args[2] == 422


def test_total_counts_the_filtered_set_without_order_or_limit():
    rendered = sql(pp.count(pp.live_filters(author='octo').order_by(pp.PostPublication.id)))
    assert rendered.startswith('SELECT count(*) AS count_1') and 'ORDER BY' not in rendered and 'LIMIT' not in rendered
    assert "= 'octo'" in rendered


class FakeDB:
    def __init__(self):
        self.statements = []

    def scalars(self, statement):
        self.statements.append(statement)
        return SimpleNamespace(all=lambda: [])

    def scalar(self, statement):
        self.statements.append(statement)
        return 7

    def execute(self, statement, params=None):
        self.statements.append((statement, params))
        rows = [SimpleNamespace(login='alice', _mapping={'count': 2}), SimpleNamespace(login='bob', _mapping={'count': 1})]
        tag_rows = [SimpleNamespace(tag='AI', _mapping={'count': 3})]
        return SimpleNamespace(all=lambda: tag_rows if params else rows)


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(pp, 'ready', lambda db: None)
    db = FakeDB()

    @contextmanager
    def sessions():
        yield db
    app = FastAPI()
    app.add_exception_handler(ResearchError, lambda request, error: __import__('fastapi').responses.JSONResponse(
        {'error': {'code': error.args[0]}}, status_code=error.args[2]))
    pp.register(app, None, sessions, SimpleNamespace(base_path=''), None, None)
    return TestClient(app), db


def test_listing_returns_total_and_keeps_public_cache_headers(api):
    client, db = api
    reply = client.get('/api/public/posts', params={'q': '검색', 'author': 'octo'})
    assert reply.status_code == 200 and reply.json() == {'items': [], 'nextCursor': None, 'total': 7}
    assert reply.headers['Cache-Control'] == 'public, max-age=0, must-revalidate' and reply.headers['ETag']
    assert "= 'octo'" in sql(db.statements[0]) and 'count(*)' in sql(db.statements[1])


def test_listing_rejects_bad_author(api):
    client, _ = api
    assert client.get('/api/public/posts', params={'author': 'a' * 101}).status_code == 422


def test_post_authors_lists_login_counts(api):
    client, db = api
    reply = client.get('/api/public/post-authors')
    assert reply.status_code == 200 and reply.json() == {'items': [{'login': 'alice', 'count': 2}, {'login': 'bob', 'count': 1}]}
    statement, params = db.statements[0]
    assert 'post_publication_heads' in str(statement) and params is None
    assert reply.headers['Cache-Control'] == 'public, max-age=0, must-revalidate'


def test_post_tags_passes_q_and_stays_unchanged_without_it(api):
    client, db = api
    assert client.get('/api/public/post-tags', params={'q': 'ai'}).json()['items'] == [{'tag': 'AI', 'count': 3}]
    statement, params = db.statements[-1]
    assert params['q'] == 'ai' and 'strpos(lower(tag), lower(CAST(:q AS text)))' in str(statement)
    client.get('/api/public/post-tags')
    assert db.statements[-1][1]['q'] is None


@pytest.mark.parametrize('q', ['', 'a' * 51])
def test_post_tags_rejects_bad_q(api, q):
    client, _ = api
    assert client.get('/api/public/post-tags', params={'q': q}).status_code == 422
