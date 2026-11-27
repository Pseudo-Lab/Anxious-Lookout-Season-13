"""#119 F2/F4 without a DB: related-post statements, reader/owner document projections."""
import re
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql

from app import post_publications as pp

A, B, C = '00000000-0000-4000-8000-00000000000a', '00000000-0000-4000-8000-00000000000b', '00000000-0000-4000-8000-00000000000c'
RELEASE = UUID('00000000-0000-4000-8000-0000000000ff')


def sql(statement):
    return str(statement.compile(dialect=postgresql.dialect()))


def release(**snapshot):
    value = {'id': str(RELEASE), 'versionId': str(RELEASE), 'slug': 's', 'title': 'T', 'publishedAt': '2026-11-01T00:00:00Z', 'tags': ['x', 'y'],
             'author': {'login': 'octo', 'name': 'octo'}, 'content': {'format': 'post-blocks', 'version': 1, 'blocks': []},
             'documents': [{'id': A, 'title': 'Private A', 'publicUrl': None},
                           {'id': B, 'title': 'Public B', 'publicUrl': '/old/public/document/?id='+B}]}
    value.update(snapshot)
    return SimpleNamespace(id=RELEASE, snapshot=value, post_id=None)


def test_neighbours_order_by_first_publication_cast_with_id_ties():
    previous, following, same = pp.related_queries(release())
    cast = 'CAST((research.post_publications.snapshot ->> %(snapshot_1)s::TEXT) AS TIMESTAMP WITH TIME ZONE)'
    for statement, sign, order in ((previous, '<', 'DESC'), (following, '>', 'ASC')):
        rendered = sql(statement)
        assert 'research.post_publications.id != %(id_1)s::UUID' in rendered
        assert cast + f' {sign} %(param_1)s::TIMESTAMP WITH TIME ZONE OR ' in rendered
        assert f'= %(param_2)s::TIMESTAMP WITH TIME ZONE AND research.post_publications.id {sign} %(id_2)s::UUID' in rendered
        assert f'AS TIMESTAMP WITH TIME ZONE) {order}, research.post_publications.id {order}' in rendered
        assert 'JOIN research.post_publication_heads' in rendered
        params = statement.compile(dialect=postgresql.dialect()).params
        assert {params[key] for key in ('snapshot_1', 'snapshot_2', 'snapshot_3')} == {'publishedAt'}
        assert params['id_1'] == params['id_2'] == RELEASE and params['param_3'] == 1
        assert params['param_1'] == params['param_2'] and params['param_1'].isoformat() == '2026-11-01T00:00:00+00:00'
    rendered = sql(same)
    assert rendered.count('@>') == 2 and ' OR ' in rendered
    limit = re.search(r'LIMIT %\((\w+)\)s', rendered).group(1)
    assert same.compile(dialect=postgresql.dialect()).params[limit] == pp.SAME_TAG_LIMIT == 5


def test_no_tags_means_no_same_tag_query():
    assert pp.related_queries(release(tags=[]))[2] is None


def test_post_ref_shape():
    assert pp.post_ref(None) is None
    assert pp.post_ref(release()) == {'slug': 's', 'title': 'T', 'publishedAt': '2026-11-01T00:00:00Z', 'tags': ['x', 'y'],
                                      'author': {'login': 'octo', 'name': 'octo'}}


@pytest.fixture
def base(monkeypatch):
    monkeypatch.setenv('APP_BASE_PATH', '/base/')


def test_reader_documents_keep_only_live_ones_with_a_current_link(base):
    value = pp.public_value(release(), None, {A})
    assert value['documents'] == [{'id': A, 'title': 'Private A', 'publicUrl': '/base/public/document/?id='+A}]
    assert 'Public B' not in repr(value)  # withdrawn since the post was published: title not sent


def test_reader_documents_empty_and_legacy_snapshot_without_documents(base):
    assert pp.public_value(release(), None, set())['documents'] == []
    legacy = release(); legacy.snapshot.pop('documents')
    assert pp.public_value(legacy, None, set())['documents'] == []


class FakeDB:
    def __init__(self, live):
        self.live, self.statements = live, []

    def scalars(self, statement):
        self.statements.append(statement)
        return [UUID(value) for value in self.live]

    def scalar(self, statement):
        return self.row


def test_live_documents_queries_heads_once_for_a_batch():
    db = FakeDB({B})
    rows = [release(), release(documents=[{'id': C, 'title': 'C', 'publicUrl': None}])]
    assert pp.live_documents(db, rows) == {B} and len(db.statements) == 1
    rendered = sql(db.statements[0])
    assert 'research.publication_heads.document_id IN' in rendered and 'publication_id IS NOT NULL' in rendered
    assert pp.live_documents(db, [release(documents=[])]) == set() and len(db.statements) == 1


def test_owner_info_lists_every_document_with_its_state(base):
    db = FakeDB({B}); db.row = release()
    value = pp.info(db, RELEASE)
    assert value['documents'] == [{'id': A, 'title': 'Private A', 'public': False, 'publicUrl': None},
                                  {'id': B, 'title': 'Public B', 'public': True, 'publicUrl': '/base/public/document/?id='+B}]
    db.row = None
    assert pp.info(db, RELEASE) is None


def test_post_ref_keeps_both_author_keys_for_seeded_and_legacy_snapshots():
    assert pp.post_ref(release(author={'login': None, 'name': '초조한 전망대'}))['author'] == {'login': None, 'name': '초조한 전망대'}
    legacy = release(); legacy.snapshot.pop('author')
    assert pp.post_ref(legacy)['author'] == {'login': None, 'name': None}
