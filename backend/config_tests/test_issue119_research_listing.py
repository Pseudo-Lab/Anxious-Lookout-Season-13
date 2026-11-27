"""#119 I2/I3/J2: research list q/sort/total without a DB (statements are captured and compiled)."""
import base64
import json
import re
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app import research_listing as listing
from app.research import ResearchError, encode_cursor

OWNER = UUID('00000000-0000-4000-8000-000000000001')


def compiled(statement):
    value = statement.compile(dialect=postgresql.dialect())
    return str(value), value.params


class FakeDB:
    def __init__(self, rows=(), total=0):
        self.rows, self.total, self.statements = list(rows), total, []

    def scalar(self, statement):
        self.statements.append(statement)
        return self.total

    def execute(self, statement):
        self.statements.append(statement)
        return SimpleNamespace(all=lambda: self.rows)


def item(n, **values):
    return SimpleNamespace(id=UUID(int=n), updated_at=datetime(2026, 11, n, tzinfo=timezone.utc), **values)


def row(n, key=None):
    entity = item(n)
    return type('Row', (tuple,), {'sort_key': key})((entity, SimpleNamespace(title=f't{n}')))


def test_q_searches_latest_title_and_material_url_with_escaping():
    text, params = compiled(listing.statement(OWNER, 'material', False, '50%'))
    assert text.count('ILIKE') == 2 and 'research.versions.title' in text and 'research.versions.source_url' in text
    assert set(v for k, v in params.items() if k.startswith('title_') or k.startswith('source_url_')) == {'50/%'}
    assert 'research.versions.number = research.items.content_number' in text
    text, _ = compiled(listing.statement(OWNER, 'document', False, 'x'))
    assert text.count('ILIKE') == 1 and 'source_url' not in text.split('WHERE')[1]


@pytest.mark.parametrize('q', ['', 'a' * 201, 'a\x00'])
def test_invalid_q_is_422(q):
    with pytest.raises(ResearchError) as caught:
        listing.statement(OWNER, 'material', False, q)
    assert caught.value.args[2] == 422


@pytest.mark.parametrize('kind,sort', [('material', 'oldest'), ('document', 'domain'), ('material', '')])
def test_invalid_sort_is_422(kind, sort):
    with pytest.raises(ResearchError) as caught:
        listing.page(FakeDB(), OWNER, kind, 30, None, False, None, sort)
    assert caught.value.args[2] == 422


def test_updated_sort_keeps_the_original_cursor_and_adds_total():
    rows = [row(3), row(2), row(1)]
    db = FakeDB(rows, total=9)
    page, following, total = listing.page(db, OWNER, 'material', 2, None, False, None, 'updated')
    assert total == 9 and len(page) == 2 and following == encode_cursor(rows[1][0])
    count, main = db.statements
    assert compiled(count)[0].startswith('SELECT count(*)')
    text, _ = compiled(main)
    assert 'ORDER BY research.items.updated_at DESC, research.items.id DESC' in text


@pytest.mark.parametrize('sort,order', [('created', 'research.items.created_at DESC, research.items.id DESC'),
                                        ('title', 'ORDER BY research.versions.title COLLATE "C" ASC, research.items.id ASC')])
def test_other_sorts_order_and_encode_their_own_cursor(sort, order):
    key = datetime(2026, 11, 2, tzinfo=timezone.utc) if sort == 'created' else 'Beta'
    rows = [row(1, key), row(2, key), row(3, key)]
    db = FakeDB(rows, total=3)
    page, following, _ = listing.page(db, OWNER, 'material', 2, None, False, None, sort)
    assert order in compiled(db.statements[1])[0]
    decoded = json.loads(base64.urlsafe_b64decode(following + '=' * (-len(following) % 4)))
    assert decoded[0] == sort and decoded[2] == str(UUID(int=2))
    assert decoded[1] == ('2026-11-02T00:00:00Z' if sort == 'created' else 'Beta')
    db = FakeDB([], total=3)
    listing.page(db, OWNER, 'material', 2, following, False, None, sort)
    text, params = compiled(db.statements[1])
    sign = '<' if sort == 'created' else '>'
    assert f'research.items.id {sign} ' in text and UUID(int=2) in params.values()


def test_domain_sort_uses_lowercased_host_and_puts_missing_urls_last():
    db = FakeDB([], total=0)
    listing.page(db, OWNER, 'material', 30, None, False, None, 'domain')
    text, params = compiled(db.statements[1])
    assert 'coalesce(lower(SUBSTRING(research.versions.source_url FROM ' in text and 'COLLATE "C" ASC' in text
    assert listing.HOST in params.values() and listing.LAST in params.values()


@pytest.mark.parametrize('url,host', [('https://www.Example.com/a?b', 'www.Example.com'), ('http://user:pw@host.kr:8080/x', 'host.kr'),
                                      ('https://news.site#frag', 'news.site')])
def test_host_pattern(url, host):
    assert re.match(listing.HOST, url).group(1) == host


def test_cursor_is_bound_to_its_sort():
    legacy = encode_cursor(item(1))
    for sort in ('created', 'title', 'domain'):
        with pytest.raises(ResearchError):
            listing.decode(legacy, sort)
    title = listing.encode('title', 'x', uuid4())
    with pytest.raises(ResearchError):
        listing.decode(title, 'domain')
    with pytest.raises(ResearchError):
        listing.page(FakeDB(), OWNER, 'material', 30, title, False, None, 'updated')
    created = listing.encode('created', datetime(2026, 11, 1, tzinfo=timezone.utc), UUID(int=5))
    assert listing.decode(created, 'created') == (datetime(2026, 11, 1, tzinfo=timezone.utc), UUID(int=5))
    last = listing.encode('domain', listing.LAST, UUID(int=6))
    assert listing.decode(last, 'domain') == (listing.LAST, UUID(int=6))


@pytest.mark.parametrize('cursor', ['', '!!!', 'a' * 1025])
def test_garbage_cursor_is_422(cursor):
    with pytest.raises(ResearchError):
        listing.decode(cursor or 'W10', 'title')
