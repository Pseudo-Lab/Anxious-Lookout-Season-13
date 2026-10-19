import json
from ops.post_markdown_inventory import inspect_markdown, summarize, database_inventory


def test_conflicting_shapes_and_no_source_echo():
    source = '> # PRIVATE_TITLE\n\n- first\n\n  second\n\n```unknown\nSECRET\tCODE\n```\n\n<script>SECRET</script>\n\n![SECRET](https://private.invalid/secret)'
    counts = inspect_markdown(source)
    assert counts['quote_heading'] == 1
    assert counts['multi_paragraph_list_item'] == 1
    assert counts['code_language_downgrade'] == 1
    assert counts['control_character_documents'] == 1
    assert counts['raw_html'] == 1
    assert counts['noncanonical_image'] == 1
    output = json.dumps(summarize([source]))
    assert 'SECRET' not in output and 'private.invalid' not in output and 'PRIVATE_TITLE' not in output


def test_counts_not_unique_documents_and_invalid_content():
    result = summarize(['hello', 'hello', None])
    assert result['counts']['documents'] == 2
    assert result['counts']['non_string_content_rows'] == 1
    assert result['counts']['utf8_bytes'] == 10
    assert result['maxima']['utf8_bytes'] == 5


def test_database_read_only_snapshot_and_all_retained_states():
    calls = []
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def begin(self): return self
        def execution_options(self, **kwargs): return self
        def execute(self, query):
            calls.append(str(query))
            return [('secret',)] if str(query).startswith('SELECT') else []
    class Engine:
        def connect(self): return Connection()
    result = database_inventory(Engine())
    assert calls[0] == 'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'
    assert set(result) == {'post_versions', 'post_drafts', 'post_proposals', 'post_publications'}
    assert len([call for call in calls if call.startswith('SELECT')]) == 4
    assert all('WHERE' not in call for call in calls)
    assert 'secret' not in json.dumps(result)


def test_table_bounds_and_canonical_image_candidate(monkeypatch):
    monkeypatch.setenv('APP_BASE_PATH', '/view')
    counts = inspect_markdown('| a | b |\n|---|---|\n| c | d |\n\n![x](/view/api/media/post-images/00000000-0000-0000-0000-000000000001)')
    assert counts['max_table_columns'] == 2
    assert counts['max_table_rows'] == 2
    assert counts['internal_image_candidate'] == 1
    assert counts['estimated_ids'] >= 11
