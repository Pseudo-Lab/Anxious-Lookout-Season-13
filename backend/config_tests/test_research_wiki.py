"""#105 research wiki: vocabulary, bibliography, summary, evidence, lint, rules. No DB."""
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app import relation_kinds
from app.bibliography import missing_fields
from app.research import (DocumentInput, MaterialChange, MaterialInput, RelationChange, RelationInput,
                          ResearchError, Store)
from app.research_lint import CODES, check
from app.research_results import success
from app.wiki_rules import rules
from config_tests.research_result_fixtures import relation_result, save_result

ROOT = Path(__file__).resolve().parents[1]
MATERIAL = {'title': 'Paper', 'content': '## 핵심 질문\n…', 'sourceUrl': 'https://example.org/paper',
            'collectedAt': '2026-11-18T00:00:00Z', 'contentKind': 'excerpt'}
BIBLIOGRAPHY = {'type': 'article-journal', 'title': 'A study', 'author': [{'family': 'Kim', 'given': 'A'}, {'literal': 'WHO'}],
                'issued': {'date-parts': [[2024, 2, 29]]}, 'container-title': 'Journal', 'DOI': '10.1234/abc.5'}


def reason(call):
    with pytest.raises(ResearchError) as caught:
        call()
    assert caught.value.code == 'validation_error' and caught.value.status == 422
    return caught.value.details['reason']


# Vocabulary -----------------------------------------------------------------

def test_vocabulary_is_the_issue_table():
    catalog = relation_kinds.catalog()
    assert [kind['key'] for kind in catalog['items']] == ['supports', 'confirms', 'extends', 'disputes', 'refutes',
        'qualifies', 'uses_method', 'uses_data', 'updates', 'same_topic']
    assert [kind['key'] for kind in catalog['items'] if not kind['directed']] == ['same_topic']
    assert {kind['key'] for kind in catalog['items'] if kind['polarity'] == 'negative'} == {'disputes', 'refutes'}
    assert {kind['key'] for kind in catalog['items'] if kind['polarity'] == 'positive'} == {'supports', 'confirms', 'extends'}
    assert [group['key'] for group in catalog['polarityGroups']] == ['positive', 'negative', 'neutral']
    assert catalog['legacyLabel'] == '기타'
    json.dumps(catalog)


def test_schema_advertises_vocabulary_and_evidence_limit():
    schema = RelationInput.model_json_schema()
    assert schema['properties']['kind']['enum'] == list(relation_kinds.KEYS)
    assert schema['properties']['evidence']['maxItems'] == 20
    assert RelationChange.model_json_schema()['properties']['kind']['enum'] == list(relation_kinds.KEYS)


# Summary and bibliography ---------------------------------------------------

def test_summary_is_one_trimmed_line_of_at_most_300_code_points():
    assert DocumentInput(title='t', content='c', summary='  한 줄  ').summary == '한 줄'
    assert DocumentInput(title='t', content='c', summary='가' * 300).summary == '가' * 300
    assert DocumentInput(title='t', content='c', summary=None).summary is None
    for bad in ('', '   ', '가' * 301, 'a\nb', 'a\rb'):
        with pytest.raises(ValidationError):
            DocumentInput(title='t', content='c', summary=bad)


def test_bibliography_subset_round_trips_by_csl_names():
    body = MaterialInput(**MATERIAL, bibliography=BIBLIOGRAPHY)
    assert body.bibliography.stored() == BIBLIOGRAPHY
    assert MaterialInput(**MATERIAL, bibliography={'type': 'webpage'}).bibliography.stored() == {'type': 'webpage'}
    assert 'container-title' in MaterialInput.model_json_schema()['$defs']['Bibliography']['properties']


@pytest.mark.parametrize('change', [
    {'URL': 'https://example.org'}, {'accessed': {'date-parts': [[2024]]}}, {'container_title': 'x'}, {'note': 'x'},
    {'type': 'blog'}, {'DOI': 'https://doi.org/10.1/x'}, {'issued': {'date-parts': [[2023, 2, 29]]}},
    {'issued': {'date-parts': [[2024], [2025]]}}, {'issued': {'date-parts': [['2024']]}}, {'author': []},
    {'author': [{'given': 'A'}]}, {'author': [{'literal': 'WHO', 'family': 'W'}]}, {'title': '  '}, {'volume': 'x' * 51},
])
def test_bibliography_rejects_unknown_or_malformed(change):
    with pytest.raises(ValidationError):
        MaterialInput(**MATERIAL, bibliography={**BIBLIOGRAPHY, **change})


def test_documents_have_no_bibliography():
    with pytest.raises(ValidationError):
        DocumentInput(title='t', content='c', bibliography={'type': 'webpage'})


class Versions:
    """Fake session for Store.add_snapshot: records the inserted version."""
    def __init__(self):
        self.added = []
    def add(self, row):
        self.added.append(row)
    def flush(self):
        pass


@pytest.mark.parametrize('sent,expected', [
    ({}, ('prior', {'type': 'book'})),
    ({'summary': None, 'bibliography': None}, (None, None)),
    ({'summary': ' new ', 'bibliography': {'type': 'webpage'}}, ('new', {'type': 'webpage'})),
])
def test_omitted_summary_and_bibliography_carry_the_latest_version(sent, expected):
    store = Store(Versions(), uuid4())
    row = SimpleNamespace(id=uuid4(), kind='material', content_number=2)
    previous = SimpleNamespace(summary='prior', bibliography={'type': 'book'})
    store.add_snapshot(row, MaterialChange(**MATERIAL, expectedVersion=1, **sent), previous)
    version = store.db.added[0]
    assert (version.summary, version.bibliography) == expected


def test_missing_bibliography_binds_as_sql_null_not_json_null():
    # review r1 blocker: JSON null violated versions_wiki_check on every save without a bibliography.
    from sqlalchemy.dialects.postgresql import JSONB, psycopg
    from app.research_models import ContentVersion
    dialect = psycopg.dialect()  # The app's driver (postgresql+psycopg).
    bind = lambda kind: kind.dialect_impl(dialect).bind_processor(dialect)
    mapped = bind(ContentVersion.__table__.c.bibliography.type)
    assert mapped(None) is None
    assert mapped({'type': 'book'}).obj == {'type': 'book'}
    assert bind(JSONB())(None).obj is None  # The default mapping binds Jsonb(None): JSON null.


def test_new_items_start_without_summary_or_bibliography():
    store = Store(Versions(), uuid4())
    store.add_snapshot(SimpleNamespace(id=uuid4(), kind='document', content_number=1), DocumentInput(title='t', content='c'))
    assert (store.db.added[0].summary, store.db.added[0].bibliography) == (None, None)


def test_draft_commit_summary_is_optional_and_proposals_keep_it():
    from app.writing import DraftCommit, ProposalAccept
    assert 'summary' not in DraftCommit(expectedDraftRevision=1).model_fields_set
    assert DraftCommit(expectedDraftRevision=1, summary=' s ').summary == 's'
    base = {'title': 't', 'content': 'c', 'expectedVersion': 1, 'expectedProposalVersion': 1,
            'resolvedBaseVersionId': str(uuid4())}
    ProposalAccept(**base)
    with pytest.raises(ValidationError):
        ProposalAccept(**base, summary='s')


# Relations ------------------------------------------------------------------

def relation(kind='supports', **extra):
    return {'source': {'type': 'material', 'id': str(uuid4())}, 'target': {'type': 'material', 'id': str(uuid4())},
            'kind': kind, 'description': 'why', **extra}


def test_relation_rules_give_reasons():
    store = Store(None, uuid4())
    assert store.relation_rules('supports', 'why') is True
    assert store.relation_rules('same_topic', 'why') is False
    assert reason(lambda: store.relation_rules('cites', 'why')) == 'relation_kind_unknown'
    assert reason(lambda: store.relation_rules('supports', ' \n ')) == 'relation_reason_required'


class Graph:
    """Fake session for Store.create_relation/evidence_values (no SQL is run)."""
    def __init__(self, versions=()):
        self.versions = {version.id: version for version in versions}
    def execute(self, *_):
        pass
    def scalar(self, query):
        values = {getattr(clause.left, 'key', None): getattr(clause.right, 'value', None)
                  for clause in query.whereclause.clauses}
        version = self.versions.get(values.get('id'))
        return version if version and version.item_id == values.get('item_id') else None


@pytest.mark.parametrize('body,expected', [
    (relation(directed=False), 'relation_direction_mismatch'),
    (relation('same_topic', directed=True), 'relation_direction_mismatch'),
    ({**relation('same_topic'), 'source': {'type': 'document', 'id': str(uuid4())}}, 'relation_direction_mismatch'),
    ({**relation(), 'target': {'type': 'document', 'id': str(uuid4())}}, 'relation_direction_mismatch'),
    (relation('cites'), 'relation_kind_unknown'),
    (relation(description=''), 'relation_reason_required'),
    ({key: value for key, value in relation().items() if key != 'description'}, 'relation_reason_required'),
])
def test_create_relation_rejects_before_touching_items(body, expected):
    store = Store(Graph(), uuid4())
    store.owned_item = lambda *_, **__: pytest.fail('items are not read for a rejected relation')
    assert reason(lambda: store.create_relation(RelationInput(**body))) == expected


def test_evidence_must_cite_an_owned_version_of_an_endpoint():
    source, target, other = uuid4(), uuid4(), uuid4()
    version = SimpleNamespace(id=uuid4(), item_id=target, number=3)
    store = Store(Graph([version]), uuid4())
    entry = {'itemId': str(target), 'versionId': str(version.id), 'locator': 'p.12'}
    parse = lambda *entries: RelationInput(**relation(evidence=list(entries))).evidence
    assert store.evidence_values(parse(entry), {source, target}) == [
        {'itemId': str(target), 'versionId': str(version.id), 'versionNumber': 3, 'locator': 'p.12', 'quote': None}]
    assert reason(lambda: store.evidence_values(parse({**entry, 'itemId': str(other)}), {source, target})) == 'evidence_endpoint_mismatch'
    assert reason(lambda: store.evidence_values(parse({**entry, 'itemId': str(source)}), {source, target})) == 'evidence_version_not_found'
    assert reason(lambda: store.evidence_values(parse({**entry, 'versionId': str(uuid4())}), {source, target})) == 'evidence_version_not_found'
    many = [{**entry, 'quote': str(index)} for index in range(21)]
    assert reason(lambda: store.evidence_values(parse(*many), {source, target})) == 'evidence_limit_exceeded'


@pytest.mark.parametrize('entry', [
    {'locator': None, 'quote': None}, {'locator': '  '}, {'locator': 'x' * 101}, {'quote': 'x' * 501}, {'versionId': 'v'},
])
def test_evidence_entry_shape(entry):
    base = {'itemId': str(uuid4()), 'versionId': str(uuid4()), 'locator': 'p.1'}
    # Malformed ids fail like Endpoint ids: 422 validation_error without a reason.
    with pytest.raises((ValidationError, ResearchError)):
        RelationInput(**relation(evidence=[{**base, **entry}]))


def test_evidence_lengths_count_code_points_and_duplicates_are_rejected():
    entry = {'itemId': str(uuid4()), 'versionId': str(uuid4()), 'quote': '😀' * 500}
    assert RelationInput(**relation(evidence=[entry])).evidence[0].quote == '😀' * 500
    with pytest.raises(ValidationError):
        RelationInput(**relation(evidence=[entry, entry]))


def test_relation_change_evidence_omitted_keeps_null_rejected():
    change = {'expectedVersion': 1, 'kind': 'supports', 'description': 'why'}
    assert RelationChange(**change).evidence is None
    assert RelationChange(**change, evidence=[]).evidence == []
    with pytest.raises(ValidationError):
        RelationChange(**change, evidence=None)


class Locked:
    def __init__(self, row):
        self.row = row
    def execute(self, *_):
        pass
    def scalar(self, _):
        return self.row
    def flush(self):
        pass


@pytest.mark.parametrize('directed,kind,expected', [
    (True, 'same_topic', 'relation_direction_mismatch'), (False, 'supports', 'relation_direction_mismatch'),
    (True, '참고', 'relation_kind_unknown'), (True, 'supports', None), (False, 'same_topic', None),
])
def test_legacy_relation_update_needs_a_vocabulary_kind_of_its_direction(directed, kind, expected):
    row = SimpleNamespace(id=uuid4(), source_id=uuid4(), target_id=uuid4(), kind='참고', description='',
                          directed=directed, version=1, evidence=[], archived=False)
    store = Store(Locked(row), uuid4())
    store.owned_item = lambda *_, **__: SimpleNamespace(kind='material')
    store.serialize_relation = lambda value: value
    body = RelationChange(expectedVersion=1, kind=kind, description='why')
    if expected:
        assert reason(lambda: store.change_relation(row.id, body)) == expected
        assert (row.version, row.kind) == (1, '참고')
    else:
        store.change_relation(row.id, body)
        assert (row.version, row.kind, row.description) == (2, kind, 'why')


def test_relation_response_preserves_legacy_kind():
    value = relation_result({'kind': ' 참고 ', 'description': '', 'directed': True})
    assert (value['kind'], value['kindKnown'], value['evidence']) == ('참고', False, [])
    assert relation_result({'kind': 'refutes', 'description': 'x'})['kindKnown'] is True


# Receipt validation ----------------------------------------------------------

def test_receipts_accept_new_results_and_reject_mismatches():
    target = str(uuid4())
    arguments = {**relation(), 'mutationId': str(uuid4()),
                 'evidence': [{'itemId': target, 'versionId': str(uuid4()), 'quote': 'q'}]}
    arguments['target']['id'] = target
    result = relation_result(arguments)
    assert success('research_relation_save', result, arguments)
    assert not success('research_relation_save', {**result, 'kindKnown': False}, arguments)
    assert not success('research_relation_save', {**result, 'evidence': []}, arguments)
    assert not success('research_relation_save', {**result, 'directed': False}, arguments)
    material = {**MATERIAL, 'summary': 's', 'bibliography': {'type': 'book'}, 'mutationId': str(uuid4())}
    saved = save_result('material', material)
    assert success('research_material_save', saved, material)
    assert not success('research_material_save', {**saved, 'summary': 'other'}, material)


def test_receipts_accept_results_stored_before_105():
    arguments = {**relation('참고'), 'directed': True, 'mutationId': str(uuid4())}
    stored = {key: value for key, value in relation_result(arguments).items() if key not in {'kindKnown', 'evidence'}}
    assert success('research_relation_save', stored, arguments)
    material = {**MATERIAL, 'mutationId': str(uuid4())}
    old = {key: value for key, value in save_result('material', material).items() if key not in {'summary', 'bibliography'}}
    assert success('research_material_save', old, material)


# Lint ------------------------------------------------------------------------

def graph():
    ids = {name: uuid4() for name in ('old', 'new', 'doc', 'lone', 'web', 'a', 'b')}
    kinds = {'doc': 'document'}
    items = [SimpleNamespace(id=identity, kind=kinds.get(name, 'material')) for name, identity in ids.items()]
    bibliography = {'old': {'type': 'article-journal', 'title': 't', 'author': [{'literal': 'x'}],
                            'issued': {'date-parts': [[2020]]}},
                    'new': {**BIBLIOGRAPHY}, 'web': {'type': 'webpage'}, 'a': BIBLIOGRAPHY, 'b': BIBLIOGRAPHY}
    latest = {identity: SimpleNamespace(title=name, bibliography=bibliography.get(name)) for name, identity in ids.items()}
    def link(source, target, kind, description='why'):
        return SimpleNamespace(id=uuid4(), source_id=ids[source], target_id=ids[target], kind=kind,
                               source_kind=kinds.get(source, 'material'), target_kind=kinds.get(target, 'material'),
                               description=description)
    relations = [link('new', 'old', 'updates'), link('doc', 'old', 'supports'), link('doc', 'new', 'uses_data', ' '),
                 link('a', 'b', 'supports'), link('b', 'a', 'refutes'), link('a', 'web', 'same_topic')]
    return ids, items, latest, relations


def test_lint_finds_each_deterministic_problem():
    ids, items, latest, relations = graph()
    result = check(items, latest, relations, 200)
    assert result['counts'] == {'orphan_material': 1, 'missing_bibliography': 3, 'relation_without_reason': 1,
                                'support_refute_conflict': 1, 'cites_superseded': 1}
    by = {code: [finding for finding in result['items'] if finding['code'] == code] for code in CODES}
    assert by['orphan_material'][0]['item']['id'] == str(ids['lone'])
    assert {finding['item']['title']: finding['missingFields'] for finding in by['missing_bibliography']} == {
        'lone': ['bibliography'], 'old': ['identifier'], 'web': ['title', 'author', 'issued']}
    assert by['relation_without_reason'][0]['relation']['kind'] == 'uses_data'
    assert {item['title'] for item in by['support_refute_conflict'][0]['items']} == {'a', 'b'}
    superseded = by['cites_superseded'][0]
    assert (superseded['document']['title'], superseded['material']['title'], [ref['title'] for ref in superseded['supersededBy']]) == ('doc', 'old', ['new'])
    assert [finding['code'] for finding in result['items']] == sorted((finding['code'] for finding in result['items']), key=CODES.index)
    assert result['truncated'] is False


def test_lint_truncates_but_counts_everything():
    _, items, latest, relations = graph()
    result = check(items, latest, relations, 2)
    assert len(result['items']) == 2 and result['truncated'] is True and sum(result['counts'].values()) == 7


def test_identifier_expected_only_for_identifier_kinds():
    complete = {'title': 't', 'author': [{'literal': 'x'}], 'issued': {'date-parts': [[2020]]}}
    for kind in ('article-journal', 'paper-conference', 'article', 'book', 'chapter', 'report', 'dataset'):
        assert missing_fields({'type': kind, **complete}) == ['identifier']
        assert missing_fields({'type': kind, **complete, 'number': 'arXiv:2401.1'}) == []
    for kind in ('webpage', 'post-weblog', 'article-newspaper', 'article-magazine', 'motion_picture', 'interview',
                 'thesis', 'software', 'legislation', 'document'):
        assert missing_fields({'type': kind, **complete}) == []


# Rules, schema and wiring ----------------------------------------------------

def test_both_skills_carry_the_generated_rules_verbatim():
    text = rules()
    assert relation_kinds.markdown_table() in text
    for path in ('runner/skills/anxious-research/SKILL.md', 'client/anxious-research-pat/SKILL.md'):
        assert text in (ROOT / path).read_text(), path


def test_lint_is_a_read_operation_everywhere():
    from app.research_receipts import MUTATIONS
    from app.research_pats import READS
    from app.research_tools import TOOLS
    from app.page_scope import OWNER_TOOLS
    assert 'research_lint' in TOOLS and 'research_lint' not in MUTATIONS
    assert 'research_lint' in READS and 'research_lint' in OWNER_TOOLS
    for path in ('runner/research_cli.py', 'client/anxious-research-pat/scripts/pat-client.py'):
        assert "'research_lint'" in (ROOT / path).read_text()


def test_schema_revision_order_and_contract():
    from app.research_schema_contract import CURRENT, REVISIONS, since
    from app.migrate import RESEARCH_REVISIONS
    assert CURRENT == '0010_research_wiki' and REVISIONS == RESEARCH_REVISIONS
    assert since('0010_research_wiki', '0009_research_pats') and not since('0009_research_pats', '0010_research_wiki')
    assert not since('9999_unknown', '0002_research')
    base = json.loads((ROOT / 'db/002_research_contract.json').read_text())
    additions = json.loads((ROOT / 'db/research_0010_wiki_contract.json').read_text())
    assert set(additions) <= set(base)
    for name, changes in additions.items():
        assert not set(changes['columns']) & set(base[name]['columns'])
    migration = (ROOT / 'research_alembic/versions/0010_research_wiki.py').read_text()
    assert "revision = '0010_research_wiki'" in migration and "down_revision = '0009_research_pats'" in migration
