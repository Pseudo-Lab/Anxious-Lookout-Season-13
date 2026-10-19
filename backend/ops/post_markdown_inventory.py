"""Read-only Markdown shape inventory. Output contains counts, never source text/IDs.

This is a planning probe, not conversion validation or migration approval.
"""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import re
import sys

from markdown_it import MarkdownIt

LANGUAGES = set('plain bash c cpp css go html java javascript json jsx markdown python rust sql tsx typescript xml yaml'.split())


def inspect_markdown(source):
    counts = Counter(documents=1, utf8_bytes=len(source.encode()), codepoints=len(source))
    if len(source.encode()) > 1048576: counts['over_byte_limit'] += 1
    if len(source) > 200000: counts['over_text_limit'] += 1
    if re.search(r'[\x00-\x09\x0b\x0c\x0e-\x1f\x7f-\x9f]|\r(?!\n)', source):
        counts['control_character_documents'] += 1
    # HTML enabled only to detect it; tokens are never rendered or executed.
    tokens = MarkdownIt('commonmark', {'html': True}).enable(['table', 'strikethrough']).parse(source)
    stack = []
    items = []
    list_depth = 0
    row_cells = 0
    table_rows = 0
    for token in tokens:
        kind = token.type
        if kind.endswith('_open'):
            counts[kind.removesuffix('_open')] += 1
            if kind in ('bullet_list_open', 'ordered_list_open'):
                list_depth += 1
                counts['max_list_depth'] = max(counts['max_list_depth'], list_depth)
                if list_depth > 3: counts['list_depth_over_three'] += 1
            if kind == 'list_item_open': items.append(0)
            if kind == 'paragraph_open' and items and stack and stack[-1] == 'list_item_open':
                items[-1] += 1
                if items[-1] == 2: counts['multi_paragraph_list_item'] += 1
            if kind == 'heading_open' and 'blockquote_open' in stack: counts['quote_heading'] += 1
            stack.append(kind)
            counts['max_token_depth'] = max(counts['max_token_depth'], len(stack))
            if kind == 'table_open': table_rows = 0
            if kind == 'tr_open':
                row_cells = 0
                table_rows += 1
            if kind in ('td_open', 'th_open'): row_cells += 1
        elif kind.endswith('_close'):
            if kind == 'tr_close': counts['max_table_columns'] = max(counts['max_table_columns'], row_cells)
            if kind == 'table_close': counts['max_table_rows'] = max(counts['max_table_rows'], table_rows)
            if kind in ('bullet_list_close', 'ordered_list_close'): list_depth -= 1
            if kind == 'list_item_close': items.pop()
            if stack: stack.pop()
        else:
            counts[kind] += 1
        if kind == 'fence' and token.info.strip() not in LANGUAGES:
            counts['code_language_downgrade'] += 1
        if kind in ('html_block', 'html_inline'): counts['raw_html'] += 1
        for child in token.children or []:
            counts['inline_' + child.type] += 1
            if child.type == 'html_inline': counts['raw_html'] += 1
            if child.type == 'image':
                src = child.attrGet('src') or ''
                prefix = re.escape(os.environ.get('APP_BASE_PATH', '').rstrip('/'))
                if re.fullmatch(prefix + r'/api/media/post-images/[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', src):
                    counts['internal_image_candidate'] += 1
                else: counts['noncanonical_image'] += 1
    # MDX is not part of commonmark; report conservatively for manual policy review.
    if re.search(r'(?m)^\s*(?:import|export)\s|\{[^\n}]*\}', source): counts['possible_mdx_documents'] += 1
    counts['estimated_ids'] = sum(counts[key] for key in (
        'paragraph', 'heading', 'blockquote', 'bullet_list', 'ordered_list', 'list_item',
        'hr', 'fence', 'code_block', 'inline_image', 'table', 'tr', 'td', 'th')) + counts['td'] + counts['th']
    return counts


def summarize(contents):
    total = Counter()
    maxima = Counter()
    for source in contents:
        if not isinstance(source, str):
            total['non_string_content_rows'] += 1
            continue
        counts = inspect_markdown(source)
        total.update({key: value for key, value in counts.items() if not key.startswith('max_')})
        for key, value in counts.items():
            maxima[key] = max(maxima[key], value)
    return {'counts': dict(sorted(total.items())), 'maxima': dict(sorted(maxima.items()))}


def database_inventory(engine):
    from sqlalchemy import text
    result = {}
    # One snapshot across all retained states; no schema/DDL/writer operations.
    with engine.connect() as connection:
        with connection.begin():
            connection.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
            connection.execute(text("SET LOCAL statement_timeout = '60s'"))
            connection.execute(text("SET LOCAL lock_timeout = '5s'"))
            for table, column in (('post_versions', 'payload'), ('post_drafts', 'payload'),
                                  ('post_proposals', 'payload'), ('post_publications', 'snapshot')):
                # Identifiers are fixed constants; no operator-supplied SQL identifiers.
                rows = connection.execution_options(stream_results=True).execute(
                    text(f"SELECT {column}->'content' FROM research.{table}"))
                result[table] = summarize(row[0] for row in rows)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--legacy', action='store_true', help='Repository seeds only; not live inventory')
    mode.add_argument('--database', action='store_true', help='All retained live rows; PM executes reviewed procedure')
    args = parser.parse_args()
    try:
        if args.legacy:
            paths = sorted((Path(__file__).resolve().parents[1] / 'db/legacy-posts').glob('*.mdx'))
            if len(paths) != 3: raise ValueError('Unexpected seed count')
            result = {'legacy_seeds_only': summarize(path.read_text().split('---', 2)[2].lstrip('\n') for path in paths)}
        else:
            from sqlalchemy import create_engine
            from app.settings import secret
            engine = create_engine(secret('DATABASE_URL'), hide_parameters=True)
            try: result = database_inventory(engine)
            finally: engine.dispose()
        print(json.dumps(result, sort_keys=True, indent=2))
    except Exception:
        # Driver/parser exception strings can contain connection or source details.
        print('Inventory failed; no source or connection details emitted.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__': sys.exit(main())
