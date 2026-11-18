"""#107 pure unit boundaries: agent post-blocks reads, saves, patches, loss refusal.

DB/HTTP transactions (CAS, idempotent replay of pre-#107 results, receipts) are
review's gate in backend/tests/test_issue107_agent_post_blocks.py.
"""
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.agent_saves import SavePost, save_post
from app import post_block_patch as patch
from app.post_block_patch import PatchFailure, refine
from app.post_blocks import (LEGACY_FORMAT_SUMMARY, LOSSY, canonicalize, format_summary,
                             markdown_losses, nodes)
from app.research import ResearchError


def uid():
    return str(uuid4())


def para(text, marks=(), **extra):
    return {'id': uid(), 'type': 'paragraph', 'runs': [{'text': text, 'marks': list(marks)}], **extra}


def cell(*texts, **extra):
    return {'id': uid(), 'blocks': [para(text) for text in texts], **extra}


def document(*blocks):
    return canonicalize({'format': 'post-blocks', 'version': 1, 'blocks': list(blocks)})


def formatted():
    """A human-formatted post: heading, colored paragraph, plain paragraph, list."""
    return document(
        {'id': uid(), 'type': 'heading', 'level': 2, 'runs': [{'text': 'Title', 'marks': []}]},
        para('Red words', [{'color': '#ff0000'}, 'bold']),
        para('Plain words'),
        {'id': uid(), 'type': 'list', 'ordered': False, 'items': [{'id': uid(), 'blocks': [para('only item')]}]})


def apply(base, operations):
    """What the API returns: apply() under the lock, refine() after the transaction."""
    try:
        return patch.apply(base, operations)
    except PatchFailure as failed:
        raise refine(failed)


def failure(action):
    with pytest.raises(ResearchError) as caught:
        action()
    return caught.value


# Loss detection / formatSummary (D3) --------------------------------------

def test_plain_markdown_post_is_not_lossy_and_summary_reports_actual_properties():
    plain = document(para('Plain'), {'id': uid(), 'type': 'table', 'rows': [
        {'id': uid(), 'header': True, 'cells': [cell('a'), cell('b')]},
        {'id': uid(), 'cells': [cell('c'), cell('d')]}]})
    assert markdown_losses(plain) == []
    assert format_summary(plain) == {'lossy': False, 'omittedProperties': [], 'message': ''}
    rich = formatted()
    assert markdown_losses(rich) == [{'id': rich['blocks'][1]['id'], 'properties': ['color']}]
    summary = format_summary(rich)
    assert summary['lossy'] is True and summary['omittedProperties'] == ['color'] and summary['message']
    assert LEGACY_FORMAT_SUMMARY['lossy'] is True and format_summary(rich) != LEGACY_FORMAT_SUMMARY


@pytest.mark.parametrize('name,block', [
    ('fontSize', lambda: para('x', [{'fontSize': 18}])),
    ('underline', lambda: para('x', ['underline'])),
    ('align', lambda: para('x', align='center')),
    ('widthPercent', lambda: {'id': uid(), 'type': 'image', 'imageId': uid(), 'widthPercent': 50}),
    ('caption', lambda: {'id': uid(), 'type': 'image', 'imageId': uid(), 'caption': [{'text': 'c', 'marks': []}]}),
    ('embed', lambda: {'id': uid(), 'type': 'embed', 'provider': 'youtube', 'resourceId': 'abcdefghijk'}),
    ('columns', lambda: {'id': uid(), 'type': 'columns', 'columns': [
        {'id': uid(), 'width': 1, 'blocks': [para('l')]}, {'id': uid(), 'width': 1, 'blocks': [para('r')]}]}),
    ('columnWidths', lambda: {'id': uid(), 'type': 'table', 'columnWidths': [1, 2], 'rows': [
        {'id': uid(), 'header': True, 'cells': [cell('a'), cell('b')]}]}),
    ('background', lambda: {'id': uid(), 'type': 'table', 'rows': [
        {'id': uid(), 'header': True, 'cells': [cell('a', background='#00ff00'), cell('b')]}]}),
    ('headerlessTableLayout', lambda: {'id': uid(), 'type': 'table', 'rows': [
        {'id': uid(), 'cells': [cell('a'), cell('b')]}]}),
    # Header tables too: Markdown joins a cell's paragraphs into one (review R2).
    ('cellParagraphLayout', lambda: {'id': uid(), 'type': 'table', 'rows': [
        {'id': uid(), 'header': True, 'cells': [cell('one', 'two'), cell('b')]}]}),
])
def test_each_lossy_property_is_detected(name, block):
    found = format_summary(document(block()))['omittedProperties']
    assert name in found and set(found) <= set(LOSSY)


# Save harness ---------------------------------------------------------------

def harness(monkeypatch, base=None):
    from app import posts
    post = SimpleNamespace(id=uuid4(), version=1) if base is not None else None
    writes = []
    monkeypatch.setattr(posts, 'ready', lambda store: None)
    monkeypatch.setattr(posts, 'owned', lambda *args, **kwargs: post)
    monkeypatch.setattr(posts, 'head', lambda store, row: SimpleNamespace(payload={'content': base}))
    def save_version(store, payload, row):
        writes.append(payload)
        return row or SimpleNamespace(id=uuid4(), version=1)
    monkeypatch.setattr(posts, 'save_version', save_version)
    version = {'id': uid(), 'number': 2, 'title': 'Article'}
    monkeypatch.setattr(posts, 'post_value', lambda store, row: {'id': str(row.id), 'version': 2, 'latestVersion': version})
    store = SimpleNamespace(owner=uuid4(), graph_lock=lambda: None)
    return store, post, writes


def body(content, post=None, **changes):
    target = {'postId': str(post.id), 'expectedVersion': 1} if post else {}
    return SavePost.model_validate({'title': 'Article', 'description': '', 'slug': '', 'tags': [], 'coverImageId': None,
        'documentIds': [], 'contentInput': content, 'mutationId': uid(), **target, **changes})


def test_markdown_save_refuses_real_formatting_loss_unless_allowed(monkeypatch):
    base = formatted()
    store, post, writes = harness(monkeypatch, base)
    error = failure(lambda: save_post(store, body({'format': 'markdown', 'text': 'Rewritten'}, post)))
    assert (error.code, error.status) == ('formatting_would_be_lost', 422) and not writes
    details = error.details
    assert details['path'] == '/contentInput' and details['code'] == 'formatting_would_be_lost'
    assert details['properties'] == ['color'] and details['blockCount'] == 1
    assert details['blocks'] == [{'id': base['blocks'][1]['id'], 'properties': ['color']}]
    assert 'post-block-patch' in details['hint'] and 'allowFormatLoss' in details['hint']
    result = save_post(store, body({'format': 'markdown', 'text': 'Rewritten', 'allowFormatLoss': True}, post))
    assert len(writes) == 1 and writes[0]['content']['blocks'][0]['runs'][0]['text'] == 'Rewritten'
    assert isinstance(result['formatChanges'], list) and result['saved']['resourceType'] == 'post'


def test_markdown_save_of_plain_post_and_new_post_is_not_checked(monkeypatch):
    store, post, writes = harness(monkeypatch, document(para('Plain')))
    save_post(store, body({'format': 'markdown', 'text': 'Changed'}, post))
    store, _, new_writes = harness(monkeypatch)
    result = save_post(store, body({'format': 'markdown', 'text': 'New'}))
    assert len(writes) == len(new_writes) == 1 and result['formatChanges'] is None


def test_formatting_loss_error_keeps_details_on_the_shared_api_failure_path():
    from app.research_api import operation_failure
    from app.research_receipts import ERRORS
    assert 'formatting_would_be_lost' in ERRORS
    error = ResearchError('formatting_would_be_lost', 'x', 422, {'path': '/contentInput', 'hint': 'use post-block-patch'})
    result, code = operation_failure(error)
    assert code == 422 and result['error']['code'] == 'formatting_would_be_lost'
    assert result['error']['details']['hint'] == 'use post-block-patch'


def test_post_blocks_save_keeps_ids_and_canonical_form(monkeypatch):
    base = formatted()
    store, post, writes = harness(monkeypatch, base)
    sent = deepcopy(base)
    sent['blocks'][2]['runs'][0]['text'] = 'Plain words, revised'
    sent['blocks'][1]['runs'][0]['marks'] = ['bold', {'color': '#ff0000'}]  # same marks, other order
    result = save_post(store, body({'format': 'post-blocks', 'document': sent}, post))
    stored = writes[0]
    assert stored['conversionWarnings'] == []
    assert [node['id'] for node in nodes(stored['content'])] == [node['id'] for node in nodes(base)]
    assert stored['content']['blocks'][1] == base['blocks'][1]
    assert [change['baseBlockId'] for change in result['formatChanges']] == [base['blocks'][2]['id']]


def test_new_post_from_blocks_has_null_format_changes(monkeypatch):
    store, _, writes = harness(monkeypatch)
    result = save_post(store, body({'format': 'post-blocks', 'document': formatted()}))
    assert result['formatChanges'] is None and writes[0]['content']['blocks'][1]['runs'][0]['marks'][1] == {'color': '#FF0000'}


@pytest.mark.parametrize('content,path', [
    ({'format': 'post-blocks'}, '/contentInput/document'),
    ({'format': 'post-blocks', 'document': {'format': 'post-blocks', 'version': 1, 'blocks': [
        {'id': '00000000-0000-4000-8000-000000000000', 'type': 'paragraph', 'runs': [{'text': 'x', 'marks': ['bogus']}]}]}},
     '/contentInput/document/blocks/0/runs/0/marks/0'),
    ({'format': 'post-blocks', 'document': {'format': 'post-blocks', 'version': 2, 'blocks': []}}, '/contentInput/document/version'),
    ({'format': 'html'}, '/contentInput/format'),
    # review 7e44e7a blocker: unhashable formats were a 500 (TypeError), not R4's 422.
    ({'format': {}}, '/contentInput/format'),
    ({'format': ['post-blocks']}, '/contentInput/format'),
    ({'format': None}, '/contentInput/format'),
    ('markdown', '/contentInput'),
    ({'format': 'post-block-patch', 'operations': [{'op': 'remove', 'id': uid()}]}, '/contentInput/format'),
])
def test_input_errors_carry_argument_json_pointers(content, path):
    error = failure(lambda: body(content))
    assert error.details['path'] == path and error.status in (413, 422)


# Patch (D2/D4) --------------------------------------------------------------

def test_patch_changes_only_targeted_nodes_and_keeps_other_formatting_and_ids(monkeypatch):
    base = formatted()
    heading, red, plain, listing = base['blocks']
    store, post, writes = harness(monkeypatch, base)
    new = para('Inserted')
    item = listing['items'][0]
    operations = [
        {'op': 'replace', 'id': plain['id'], 'block': {**plain, 'runs': [{'text': 'Plain, revised', 'marks': []}]}},
        {'op': 'insert', 'beforeId': plain['id'], 'block': new},
        {'op': 'insert', 'afterId': item['id'], 'block': {'id': uid(), 'blocks': [para('second item')]}},
        {'op': 'remove', 'id': heading['id']},
        {'op': 'insert', 'afterId': listing['id'], 'block': heading},  # move to the end
    ]
    result = save_post(store, body({'format': 'post-block-patch', 'operations': operations}, post))
    content = writes[0]['content']
    assert [block['id'] for block in content['blocks']] == [red['id'], new['id'], plain['id'], listing['id'], heading['id']]
    assert content['blocks'][0] == red and content['blocks'][4] == heading
    assert content['blocks'][2]['runs'][0]['text'] == 'Plain, revised'
    assert [entry['id'] for entry in content['blocks'][3]['items']][0] == item['id']
    assert len(content['blocks'][3]['items']) == 2 and writes[0]['conversionWarnings'] == []
    assert red['id'] not in {change['baseBlockId'] for change in result['formatChanges'] if change['kind'] != 'moved'}


@pytest.mark.parametrize('operations,path', [
    (None, '/contentInput/operations'),
    ([], '/contentInput/operations'),
    (['remove'], '/contentInput/operations/0'),
    ([{'op': 'move', 'id': uid()}], '/contentInput/operations/0/op'),
    ([{'op': 'remove'}], '/contentInput/operations/0/id'),
    ([{'op': 'remove', 'id': 'NOT-A-UUID'}], '/contentInput/operations/0/id'),
    ([{'op': 'remove', 'id': uid(), 'block': {}}], '/contentInput/operations/0/block'),
    ([{'op': 'insert', 'block': {}}], '/contentInput/operations/0'),
    ([{'op': 'insert', 'afterId': uid(), 'beforeId': uid(), 'block': {}}], '/contentInput/operations/0'),
    ([{'op': 'replace', 'id': uid(), 'block': []}], '/contentInput/operations/0/block'),
])
def test_patch_operation_shapes_fail_with_operation_paths(operations, path):
    error = failure(lambda: apply(formatted(), operations))
    assert error.details['path'] == path and error.code == 'invalid_block_document'


def test_patch_limits():
    many = [{'op': 'remove', 'id': uid()}] * 201
    assert failure(lambda: apply(formatted(), many)).code == 'content_limit_exceeded'
    deep = {'id': uid()}
    for _ in range(45): deep = {'x': deep}
    error = failure(lambda: apply(formatted(), [{'op': 'replace', 'id': uid(), 'block': deep}]))
    assert error.code == 'content_limit_exceeded' and error.details['path'] == '/contentInput/operations'


def test_patch_unknown_and_removed_anchor_ids_point_at_the_operation():
    base = formatted()
    assert failure(lambda: apply(base, [{'op': 'remove', 'id': uid()}])).details['path'] == '/contentInput/operations/0/id'
    target = base['blocks'][2]['id']
    error = failure(lambda: apply(base, [{'op': 'remove', 'id': target}, {'op': 'insert', 'afterId': target, 'block': para('x')}]))
    assert error.details['path'] == '/contentInput/operations/1/afterId'


def test_patch_result_errors_map_to_the_introducing_operation():
    base = formatted()
    plain = base['blocks'][2]['id']
    bad = para('x', ['bogus'])
    error = failure(lambda: apply(base, [{'op': 'replace', 'id': plain, 'block': {**para('fine'), 'id': plain}},
                                         {'op': 'insert', 'afterId': plain, 'block': bad}]))
    assert error.details['path'].startswith('/contentInput/operations/1/block/runs/0/marks')
    # A node nested inside an earlier insertion, replaced later, belongs to the later operation.
    wrapper = {'id': uid(), 'type': 'quote', 'blocks': [para('quoted')]}
    inner = wrapper['blocks'][0]['id']
    error = failure(lambda: apply(base, [{'op': 'insert', 'afterId': plain, 'block': wrapper},
                                         {'op': 'replace', 'id': inner, 'block': {'id': inner, 'type': 'paragraph'}}]))
    assert error.details['path'].startswith('/contentInput/operations/1/block')


def test_patch_duplicate_id_inserted_before_the_original_points_at_the_insert():
    base = formatted()
    original = base['blocks'][2]
    error = failure(lambda: apply(base, [{'op': 'insert', 'beforeId': base['blocks'][0]['id'], 'block': deepcopy(original)}]))
    assert error.details['path'] == '/contentInput/operations/0/block/id'
    # Nested duplicate inside an inserted subtree as well.
    quote = {'id': uid(), 'type': 'quote', 'blocks': [deepcopy(original)]}
    error = failure(lambda: apply(base, [{'op': 'insert', 'beforeId': base['blocks'][0]['id'], 'block': quote}]))
    assert error.details['path'] == '/contentInput/operations/0/block/blocks/0/id'


def test_patch_emptying_a_required_container_points_at_the_removing_operation():
    base = formatted()
    item = base['blocks'][3]['items'][0]['id']
    error = failure(lambda: apply(base, [{'op': 'replace', 'id': base['blocks'][2]['id'], 'block': para('ok')},
                                         {'op': 'remove', 'id': item}]))
    assert error.details['path'] == '/contentInput/operations/1'
    everything = [{'op': 'remove', 'id': block['id']} for block in base['blocks']]
    assert failure(lambda: apply(base, everything)).details['path'] == '/contentInput/operations/3'


def test_patch_base_is_not_mutated_and_result_is_canonical():
    base = formatted(); before = deepcopy(base)
    result = apply(base, [{'op': 'replace', 'id': base['blocks'][2]['id'],
                           'block': {**para('Mixed', [{'color': '#abcdef'}]), 'id': base['blocks'][2]['id']}}])
    assert base == before and result == canonicalize(result)
    assert result['blocks'][2]['runs'][0]['marks'] == [{'color': '#ABCDEF'}]


# Reads (D1) -----------------------------------------------------------------

def test_agent_post_reads_return_the_block_document(monkeypatch):
    from app import posts, post_publications
    from app.research_tools import dispatch
    content = formatted()
    post = SimpleNamespace(id=uuid4(), version=1, archived=False, created_at=None, updated_at=None, content_number=1)
    version = SimpleNamespace(id=uuid4(), post_id=post.id, number=1, created_at=None,
        payload={'title': 'T', 'description': '', 'slug': 's', 'tags': [], 'coverImageId': None, 'documentIds': [], 'content': content})
    draft = SimpleNamespace(id=uuid4(), post_id=post.id, revision=1, base_post_version=1, base_version_id=version.id,
                            state='active', created_at=None, updated_at=None, payload=version.payload)
    monkeypatch.setattr(posts, 'ready', lambda store: None)
    monkeypatch.setattr(posts, 'owned', lambda store, identity, active=False: post)
    monkeypatch.setattr(posts, 'head', lambda store, row: version)
    monkeypatch.setattr(posts, 'current_draft', lambda store, identity: draft)
    monkeypatch.setattr(posts, 'stamp', lambda value: '2026-11-18T00:00:00Z')
    monkeypatch.setattr(post_publications, 'info', lambda db, identity: None)
    store = SimpleNamespace(owner=uuid4(), db=SimpleNamespace(get=lambda *args: SimpleNamespace(login='owner'),
                                                              scalar=lambda query: version))
    got = dispatch(store, 'research_get', {'type': 'post', 'id': str(post.id)})
    assert got['latestVersion']['content'] == content and got['latestVersion']['formatSummary']['lossy'] is True
    snapshot = dispatch(store, 'research_version_get', {'type': 'post', 'id': str(post.id), 'versionId': str(version.id)})
    assert snapshot['content'] == content
    assert dispatch(store, 'research_post_draft_get', {'postId': str(post.id)})['draft']['content'] == content


# Schema (requirement 4) -----------------------------------------------------

def test_save_schema_exposes_all_formats_and_block_definitions():
    from app.research_tools import definitions
    schema = next(tool for tool in definitions() if tool['name'] == 'research_post_save')['inputSchema']
    text = json.dumps(schema)
    for name in ('post-blocks', 'post-block-patch', 'allowFormatLoss', 'Paragraph', 'ListItem', 'Cell', 'Column'):
        assert name in text, name


# Pre-#107 idempotent results (PM/review R1) ---------------------------------

def test_pre107_post_result_still_validates_and_new_results_need_the_actual_summary():
    from config_tests.research_result_fixtures import save_result
    from app.research_results import success
    args = {'title': 'Post', 'description': '', 'tags': [], 'coverImageId': None, 'documentIds': [],
            'contentInput': {'format': 'markdown', 'text': '# Note'}, 'mutationId': uid()}
    current = save_result('post', args)
    assert current['latestVersion']['formatSummary']['lossy'] is False
    assert success('research_post_save', current, args)
    legacy = deepcopy(current); legacy.pop('formatChanges')
    legacy['latestVersion']['formatSummary'] = deepcopy(LEGACY_FORMAT_SUMMARY)
    assert success('research_post_save', legacy, args)
    mixed = deepcopy(legacy); mixed['formatChanges'] = None
    assert not success('research_post_save', mixed, args)
    stale = deepcopy(current); stale.pop('formatChanges')
    assert not success('research_post_save', stale, args)


# CLI argument boundary (D5: 1,000,000 bytes, unchanged) ---------------------

def sized(size):
    head = b'{"type":"document","pad":"'
    data = head + b'x' * (size - len(head) - 2) + b'"}'
    assert len(data) == size
    return data


@pytest.mark.parametrize('size,sent', [(999_999, True), (1_000_000, True), (1_000_001, False)])
def test_runner_cli_argument_boundary(size, sent, monkeypatch, capsys):
    from runner import research_cli as cli
    reached = []
    def grant():
        reached.append(True)
        raise RuntimeError('stop before network')
    monkeypatch.setattr(cli, 'read_grant', grant)
    monkeypatch.setattr(cli.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(sized(size))))
    assert cli.main(['research_list']) == 1
    assert bool(reached) is sent
    assert json.loads(capsys.readouterr().out)['error']['code'] == 'research_api_unavailable'


@pytest.mark.parametrize('size,sent', [(999_999, True), (1_000_000, True), (1_000_001, False)])
def test_pat_client_argument_boundary(size, sent, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location(
        'external_pat_client_107', Path(__file__).parents[1] / 'client/anxious-research-pat/scripts/pat-client.py')
    client = importlib.util.module_from_spec(spec); spec.loader.exec_module(client)
    calls = []
    monkeypatch.setattr(client, 'read_token', lambda filename: 'token')
    monkeypatch.setattr(client, 'exchange', lambda origin, path, **kwargs: calls.append(path) or (200, {'ok': True}))
    monkeypatch.setattr(client.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(sized(size))))
    code = client.main(['--origin', 'https://platform.example', '--credential-file', '/private/research.pat', 'research_list'])
    assert bool(calls) is sent and code == (0 if sent else 1)
    output = json.loads(capsys.readouterr().out)
    assert output == {'ok': True} if sent else output['error']['code'] == 'research_api_unavailable'


def table(widths=None):
    rows = [{'id': uid(), 'header': True, 'cells': [cell('a'), cell('b')]},
            {'id': uid(), 'cells': [cell('c'), cell('d')]}]
    value = {'id': uid(), 'type': 'table', 'rows': rows}
    if widths: value['columnWidths'] = widths
    return value


@pytest.mark.parametrize('widths', [None, [1, 2]])
def test_patch_sibling_dependent_errors_point_at_the_causing_operation(widths):
    # review 7e44e7a major: removing one first-row cell fails on the other row (or columnWidths),
    # a path the operation never touched; it must still name operation 0, not /contentInput/operations.
    base = document(para('before'), table(widths))
    first = base['blocks'][1]['rows'][0]['cells'][0]['id']
    error = failure(lambda: apply(base, [{'op': 'remove', 'id': first}]))
    assert error.details['path'] == '/contentInput/operations/0'
    second = base['blocks'][1]['rows'][1]['cells'][0]['id']
    assert failure(lambda: apply(base, [{'op': 'remove', 'id': second}])).details['path'] == '/contentInput/operations/0'
    # An unrelated earlier operation elsewhere does not take the blame.
    other = base['blocks'][0]['id']
    error = failure(lambda: apply(base, [{'op': 'replace', 'id': other, 'block': {**para('after'), 'id': other}},
                                         {'op': 'remove', 'id': first}]))
    assert error.details['path'] == '/contentInput/operations/1'


def test_table_count_limit_points_at_the_inserting_operation():
    base = document(*[table() for _ in range(20)])
    extra = table()
    error = failure(lambda: apply(base, [{'op': 'insert', 'afterId': base['blocks'][-1]['id'], 'block': extra}]))
    assert error.code == 'content_limit_exceeded' and error.details['path'].startswith('/contentInput/operations/0')


# R4 supplement (PM 2026-11-18): the causing operation is the first one after which the
# document fails validation and never passes again.

@pytest.mark.parametrize('widths', [None, [1, 2]])
def test_review_4a0b5b7_later_valid_edit_does_not_take_the_blame(widths):
    base = document(table(widths))
    rows = base['blocks'][0]['rows']
    paragraph = rows[1]['cells'][0]['blocks'][0]
    operations = [{'op': 'remove', 'id': rows[0]['cells'][0]['id']},
                  {'op': 'replace', 'id': paragraph['id'], 'block': {**paragraph, 'runs': [{'text': 'changed', 'marks': []}]}}]
    assert failure(lambda: apply(base, operations)).details['path'] == '/contentInput/operations/0'
    assert failure(lambda: apply(base, operations[:1])).details['path'] == '/contentInput/operations/0'
    assert apply(base, operations[1:])['blocks'][0]['rows'][1]['cells'][0]['blocks'][0]['runs'][0]['text'] == 'changed'


def test_review_4a0b5b7_duplicate_keeps_its_argument_position_after_a_later_sibling_insert():
    first = para('first')
    quote = {'id': uid(), 'type': 'quote', 'blocks': [para('kept')]}
    base = document(first, quote)
    other = para('other')
    replacement = {'id': quote['id'], 'type': 'quote', 'blocks': [para('one'), deepcopy(first), other]}
    alone = [{'op': 'replace', 'id': quote['id'], 'block': replacement}]
    assert failure(lambda: apply(base, alone)).details['path'] == '/contentInput/operations/0/block/blocks/1/id'
    both = [*alone, {'op': 'insert', 'beforeId': replacement['blocks'][0]['id'], 'block': para('new')}]
    assert failure(lambda: apply(base, both)).details['path'] == '/contentInput/operations/0/block/blocks/1/id'
    assert apply(document(first, quote), [{'op': 'insert', 'beforeId': quote['blocks'][0]['id'], 'block': para('new')}])


def test_transient_failure_repaired_later_is_not_a_cause():
    base = formatted()
    plain = base['blocks'][2]
    # A move written as insert-then-remove: the duplicate id after operation 0 is repaired by operation 1.
    move = [{'op': 'insert', 'beforeId': base['blocks'][0]['id'], 'block': deepcopy(plain)},
            {'op': 'remove', 'id': plain['id']}]
    result = apply(base, move)
    assert [block['id'] for block in result['blocks']].count(plain['id']) == 1
    # A failure after the repair is the cause, not the earlier transient one.
    later = [*move, {'op': 'insert', 'afterId': base['blocks'][1]['id'], 'block': para('x', ['bogus'])}]
    assert failure(lambda: apply(base, later)).details['path'].startswith('/contentInput/operations/2/block/runs/0/marks')
    # Without the repair, operation 0 stays the cause.
    unrepaired = [move[0], later[2]]
    assert failure(lambda: apply(base, unrepaired)).details['path'] == '/contentInput/operations/0/block/id'

def test_document_wide_limit_names_the_operation_that_crossed_it():
    base = document(*[table() for _ in range(20)])
    error = failure(lambda: apply(base, [{'op': 'replace', 'id': base['blocks'][0]['id'], 'block': {**base['blocks'][0]}},
                                         {'op': 'insert', 'beforeId': base['blocks'][0]['id'], 'block': table()}]))
    assert error.code == 'content_limit_exceeded' and error.details['path'].startswith('/contentInput/operations/1')


# R4 supplement 2 (PM 2026-11-18): bounded cause search outside the lock -------

def test_apply_under_the_lock_raises_the_final_error_without_replaying(monkeypatch):
    base = formatted()
    monkeypatch.setattr(patch, 'cause', lambda *args: pytest.fail('no replay under the graph lock'))
    operations = [{'op': 'insert', 'afterId': base['blocks'][0]['id'], 'block': para('x', ['bogus'])}]
    with pytest.raises(PatchFailure) as caught:
        patch.apply(base, operations)
    assert caught.value.details['path'] == '/contentInput/operations'
    assert caught.value.code == 'invalid_block_document' and caught.value.replay == (base, operations)


def test_cause_search_over_the_work_bound_returns_the_final_first_error(monkeypatch):
    base = document(table())
    rows = base['blocks'][0]['rows']
    operations = [{'op': 'remove', 'id': rows[0]['cells'][0]['id']}]
    after = deepcopy(base); after['blocks'][0]['rows'][0]['cells'].pop(0)
    limit = len(patch.encoded(base)) + len(patch.encoded(after))  # bytes of both validated states
    monkeypatch.setattr(patch, 'CAUSE_BUDGET', limit)
    assert failure(lambda: apply(base, operations)).details['path'] == '/contentInput/operations/0'
    monkeypatch.setattr(patch, 'CAUSE_BUDGET', limit - 1)
    error = failure(lambda: apply(base, operations))
    # The final document's first error: the second row no longer matches the first row's width.
    assert error.details == {'path': '/contentInput/operations', 'code': 'invalid_block_document'}


def test_budget_is_16_mib():
    assert patch.CAUSE_BUDGET == 16 * 1024 * 1024


def test_api_refines_only_patch_failures():
    from app.research_api import refined
    plain = ResearchError('validation_error', 'x', 422)
    assert refined(plain, {'kept': True}, 422) == ({'kept': True}, 422)
    assert refined(None, {'ok': True}, 200) == ({'ok': True}, 200)
    base = formatted()
    operations = [{'op': 'insert', 'afterId': base['blocks'][0]['id'], 'block': para('x', ['bogus'])}]
    with pytest.raises(PatchFailure) as caught:
        patch.apply(base, operations)
    result, code = refined(caught.value, None, None)
    assert code == 422 and result['error']['details']['path'].startswith('/contentInput/operations/0/block/runs/0/marks')


def test_pat_path_searches_the_cause_after_the_transaction(monkeypatch):
    from contextlib import contextmanager
    from app import pat_calls, research_api, research_pats
    state = {'open': False, 'refined_open': None}
    @contextmanager
    def begin():
        state['open'] = True
        try: yield db
        finally: state['open'] = False
    @contextmanager
    def nested(): yield
    db = SimpleNamespace(scalar=lambda *args: SimpleNamespace(), execute=lambda *args: None, connection=lambda: None,
                         begin_nested=nested, add=lambda row: None, flush=lambda: None)
    @contextmanager
    def authorization(): yield SimpleNamespace(remaining=lambda: None, end=0)
    principal = SimpleNamespace(owner=uuid4(), credential=uuid4(), name='client')
    monkeypatch.setattr(pat_calls, 'budget', SimpleNamespace(authorization=authorization, checkout=lambda: None))
    monkeypatch.setattr(research_pats, 'observe', lambda sessions, token: principal)
    monkeypatch.setattr(research_pats, 'resolve', lambda *args: principal)
    monkeypatch.setattr(research_pats, 'allowed', lambda *args: None)
    monkeypatch.setattr(research_api, 'require_schema', lambda connection: None)
    base = formatted()
    operations = [{'op': 'insert', 'afterId': base['blocks'][0]['id'], 'block': para('x', ['bogus'])}]
    def dispatch(store, operation, body):
        assert state['open']
        patch.apply(base, operations)
    monkeypatch.setattr(pat_calls, 'dispatch', dispatch)
    real = patch.refine
    def refine_spy(failed):
        state['refined_open'] = state['open']
        return real(failed)
    monkeypatch.setattr(patch, 'refine', refine_spy)
    app = SimpleNamespace(state=SimpleNamespace(research_policy='editors'))
    result, code = pat_calls.call(app, SimpleNamespace(begin=begin), 'research_post_save', {'mutationId': uid()},
                                  'token', lambda value, status=200: (value, status))
    assert state['refined_open'] is False and code == 422
    assert result['error']['details']['path'].startswith('/contentInput/operations/0/block/runs/0/marks')


# R4 supplement 3 (PM 2026-11-18): the first error stays as canonicalize gives it; the
# bound sums the bytes of the states actually validated.

def test_review_64ffb59_shape_error_is_not_masked_by_a_duplicate_id():
    base = formatted()
    quote = {'id': uid(), 'type': 'quote', 'blocks': [deepcopy(base['blocks'][2]), para('x', ['bogus'])]}
    error = failure(lambda: apply(base, [{'op': 'insert', 'beforeId': base['blocks'][0]['id'], 'block': quote}]))
    assert (error.status, error.code) == (422, 'invalid_block_document')
    assert error.details['path'] == '/contentInput/operations/0/block/blocks/1/runs/0/marks/0'


def test_review_64ffb59_payload_too_large_keeps_413_with_a_duplicate_id():
    base = formatted()
    huge = {'id': uid(), 'type': 'paragraph', 'runs': [{'text': 'x' * 20000, 'marks': [] if i % 2 else ['bold']} for i in range(60)]}
    quote = {'id': uid(), 'type': 'quote', 'blocks': [deepcopy(base['blocks'][2]), huge]}
    error = failure(lambda: apply(base, [{'op': 'insert', 'beforeId': base['blocks'][0]['id'], 'block': quote}]))
    assert (error.status, error.code) == (413, 'payload_too_large')
    assert error.details == {'path': '/contentInput/operations/0', 'code': 'payload_too_large'}


def test_review_64ffb59_growing_patch_stops_at_the_byte_bound(monkeypatch):
    # Small base; operation 0 grows the document to ~0.73 MB, operation 1 breaks a table,
    # 198 valid text edits follow. Summed state bytes pass 16 MiB long before step 200.
    base = document(para('p'), table())
    assert len(patch.encoded(base)) < 2000
    marks = ['bold', 'underline', {'color': '#ABCDEF'}, {'fontSize': 14}]
    big = {'id': uid(), 'type': 'paragraph', 'runs': [{'text': 'ab' * 11, 'marks': marks if i % 2 else marks[:2]} for i in range(9000)]}
    target = base['blocks'][0]
    operations = [{'op': 'insert', 'afterId': target['id'], 'block': big},
                  {'op': 'remove', 'id': base['blocks'][1]['rows'][0]['cells'][0]['id']}]
    operations += [{'op': 'replace', 'id': target['id'], 'block': {**target, 'runs': [{'text': 'p' + str(i), 'marks': []}]}}
                   for i in range(198)]
    assert len(patch.encoded(operations[0]['block'])) > 700_000
    calls = []
    real = patch.canonicalize
    monkeypatch.setattr(patch, 'canonicalize', lambda value, root=(): calls.append(1) or real(value, root))
    error = failure(lambda: apply(base, operations))
    assert error.details == {'path': '/contentInput/operations', 'code': 'invalid_block_document'}
    assert len(calls) <= 1 + 1 + 24  # final check + base + at most ~23 states of ~0.73 MB
    # Under the old (operations + 1) x base estimate the replay would have run all 201 states.
    assert 201 * len(patch.encoded(base)) < patch.CAUSE_BUDGET
