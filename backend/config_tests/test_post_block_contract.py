"""Contract gates only; semantic expectations deliberately precede their implementation."""
import json
from pathlib import Path
import pytest
from pydantic import ValidationError
from app.post_block_contract import Document, export_schema

ROOT = Path(__file__).resolve().parents[1] / 'contracts/post-blocks-v1'
GOLDEN = json.loads((ROOT / 'golden.json').read_text())


def test_export_is_reproducible_and_closed():
    schema = export_schema()
    assert schema == json.loads((ROOT / 'input.schema.json').read_text())
    assert schema['$schema'].endswith('/2020-12/schema')
    assert schema['additionalProperties'] is False
    for definition in schema['$defs'].values():
        if definition.get('type') == 'object': assert definition['additionalProperties'] is False
    def walk(value):
        if isinstance(value, dict):
            if '$ref' in value:
                assert value['$ref'].startswith('#/$defs/')
                assert value['$ref'].split('/')[-1] in schema['$defs']
            for child in value.values(): walk(child)
        elif isinstance(value, list):
            for child in value: walk(child)
    walk(schema)


@pytest.mark.parametrize('case', GOLDEN['valid'], ids=lambda case: case['name'])
def test_positive_shapes_and_expected_canonical_shapes(case):
    Document.model_validate_json(json.dumps(case['input']))
    Document.model_validate_json(json.dumps(case['canonical']))


@pytest.mark.parametrize('case', [case for case in GOLDEN['invalid'] if case['stage'] == 'shape'], ids=lambda case: case['name'])
def test_negative_shapes(case):
    with pytest.raises(ValidationError): Document.model_validate_json(json.dumps(case['input']))
    assert case['path'].startswith('/content')


@pytest.mark.parametrize('version', [True, False, 1.0, '1', None, 0, 2])
def test_version_rejects_raw_non_integer_and_unknown_values(version):
    document = {**GOLDEN['valid'][0]['input'], 'version': version}
    with pytest.raises(ValidationError) as error:
        Document.model_validate_json(json.dumps(document))
    assert error.value.errors()[0]['loc'] == ('version',)


def test_integer_version_one_remains_valid():
    document = Document.model_validate_json(json.dumps(GOLDEN['valid'][0]['input']))
    assert type(document.version) is int
    assert document.version == 1


def test_local_array_and_integer_bounds():
    paragraph = GOLDEN['valid'][0]['input']['blocks'][0]
    document = {'format': 'post-blocks', 'version': 1, 'blocks': [paragraph] * 500}
    Document.model_validate_json(json.dumps(document))  # duplicate IDs are a semantic check
    document['blocks'].append(paragraph)
    with pytest.raises(ValidationError): Document.model_validate_json(json.dumps(document))
    heading = {'id': paragraph['id'], 'type': 'heading', 'level': 6, 'runs': []}
    for bad in (0, 7, '2', True):
        heading['level'] = bad
        with pytest.raises(ValidationError):
            Document.model_validate_json(json.dumps({'format': 'post-blocks', 'version': 1, 'blocks': [heading]}))


def test_fixture_semantic_and_diff_scope_is_explicit():
    semantic = {case['name'] for case in GOLDEN['invalid'] if case['stage'] == 'semantic'}
    assert {'duplicate-id', 'duplicate-mark', 'unsafe-link', 'ragged-table', 'second-row-header', 'tab-in-code'} <= semantic
    diff = GOLDEN['formatDiff'][0]
    assert diff['baseTextProjection'] == diff['candidateTextProjection']
    assert diff['formatChanges'][0]['changedProperties'] == ['align']
    assert GOLDEN['limits']['ids'] == 2000
    assert GOLDEN['limits']['totalCells'] == 1000
