"""Fixed public distribution paths; no arbitrary filesystem download."""
from pathlib import Path
from fastapi.responses import FileResponse, Response
from .research_tools import TOOLS

ROOT = Path(__file__).resolve().parent.parent / 'client' / 'anxious-research-pat'


def register(app, settings, response):
    prefix = settings.base_path + '/api/research/client'

    @app.get(settings.base_path + '/api/research/llm.md')
    def llm():
        from typing import get_args
        from .post_block_contract import Language
        from .wiki_rules import rules
        api = settings.origin + settings.base_path + '/api/research'
        operations = ', '.join(name for name in TOOLS if name != 'research_page_context')
        languages = ', '.join(get_args(Language))
        content = f'''# Anxious Lookout research API

API: {api}/agent/v1
Schema (GET, no authentication): {api}/client/operations.json

POST the operation's JSON arguments to `<API>/<operation>` with:
`Authorization: Bearer <PAT>` and `Content-Type: application/json`.

Operations: {operations}.

Read documents: POST `{api}/agent/v1/research_list`
```json
{{"type":"document","limit":30}}
```
Save a document: POST `{api}/agent/v1/research_document_save`
```json
{{"mutationId":"<new UUID>","title":"Title","content":"Markdown with source links"}}
```

All PATs can read and save their owner's data. Approved editor/admin is required;
commenters can only read public content. Publishing and withdrawal are human actions.

Use a new mutationId for each change. An explicitly chosen identical retry uses
the same ID and arguments. Read before updating and send the current expectedVersion.
On conflict, reread and reconcile before a new change. On timeout, check history;
do not assume the save failed or retry automatically. Stop on401/403/429.

Post saves take `contentInput` in one of three formats; existing posts also need
postId and expectedVersion. Saves append versions and leave human drafts and
public snapshots unchanged. Use the schema for other fields.

## Post blocks

Posts are stored as a block document (`post-blocks` v1). Read it at
`latestVersion.content` (research_get), `content` (research_version_get) or
`draft.content` (research_post_draft_get). The block shapes are the `$defs` in
research_post_save's argumentsSchema in the schema above.

- Edit an existing post with `post-block-patch`. Only the operations' blocks
  change; every other block keeps its formatting and id.
  `{{"format":"post-block-patch","operations":[{{"op":"replace","id":"<node id>","block":{{...}}}},
  {{"op":"insert","afterId":"<sibling id>","block":{{...}}}},{{"op":"remove","id":"<node id>"}}]}}`
  1–200 operations apply in order to the head version. Ids may be any node
  (block, list item, table row/cell, column); insert takes exactly one of
  afterId/beforeId and goes into the sibling's container. Move = remove, then
  insert with the same id. Replace a parent to fill an empty container.
- `post-blocks` sends a whole document
  `{{"format":"post-blocks","document":{{"format":"post-blocks","version":1,"blocks":[...]}}}}`
  for a new post, a full rewrite, or a post built from a human draft. New nodes
  need new lowercase UUID ids, unique in the document.
- `markdown` suits new or plain posts. If the existing post has formatting
  Markdown cannot keep (color, fontSize, underline, align, widthPercent,
  caption, background, columnWidths, columns, embed, multi-paragraph table
  cells, headerless tables), the save is refused with 422
  `formatting_would_be_lost`; `error.details` lists `properties`, `blocks`
  and a `hint`. Use post-block-patch, or set `"allowFormatLoss":true` in
  contentInput only when the user accepts the loss. A post's `formatSummary`
  shows whether it has such formatting.
- Block errors return `error.details.path`, one JSON Pointer into your
  arguments, e.g. `/contentInput/document/blocks/3/runs/0/marks/1` or, for a
  patch, the operation that caused it: `/contentInput/operations/2/block/...`.
- Saves return `formatChanges` (base head → new version; null for a new post).
- Size: a document is at most 1 MiB of compact UTF-8 JSON and 200,000 text
  characters. pat-client.py sends at most 1,000,000 bytes of arguments (larger
  input is not sent) and reads at most 8,000,000 bytes of response (a larger
  response may follow a completed save: check versions); both print
  `research_api_unavailable` and exit nonzero. A patch
  sends only the changed blocks, so its arguments are smaller (same limit):
  prefer it for large posts.
- API responses (and pat-client.py output) show text that looks like a research
  token as `[REDACTED]`; inputs containing such text are refused. Saving a
  masked document back whole would replace the original text with the mask, so
  edit existing posts with a patch that leaves those blocks untouched.

## Post Markdown

- H1–H6 are supported; start body headings at `##` because the post title is H1.
- Use paragraphs, **bold**, *italic*, ~~strike~~, inline code, links, ordered/bullet
  lists (one paragraph per item, up to3 levels), quotes (paragraphs/lists), fenced
  or indented code, tables and horizontal rules.
- Code languages: {languages}. Other fence languages (including no language)
  become plain with `code_language_downgraded`.
- Images must be a standalone paragraph using a root-relative owned upload URL:
  `![alt]({settings.base_path}/api/media/post-images/<UUID>)`. Inline/external images are rejected.
- Outside code, HTML, `{{...}}` expressions and import/export lines are rejected:
  422 `unsupported_content`. Size limits use `payload_too_large` (413) or
  `content_limit_exceeded` (422). Unsupported structures are not flattened.
- Text must contain real newline characters. JSON-encode once: wire `\\n`, not `\\\\n`.
- Markdown saves cannot represent font size/color, underline, alignment, columns,
  table-cell backgrounds or embeds. Edit formatted posts with post-block-patch;
  see Post blocks.
- Research documents use GFM, skip HTML, show images as links and code without
  execution/highlighting; they do not use the post-block converter.

Public reads need no PAT: GET {settings.origin}{settings.base_path}/api/public/documents
or {settings.origin}{settings.base_path}/api/public/posts.

{rules()}

The relation vocabulary is also served as JSON (GET, no authentication):
{api}/relation-kinds
'''
        return Response(content, media_type='text/markdown',
                        headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    @app.get(prefix + '/pat-client.py')
    def client():
        return FileResponse(ROOT / 'scripts/pat-client.py', media_type='text/x-python',
            filename='pat-client.py', headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    @app.get(prefix + '/SKILL.md')
    def skill():
        return FileResponse(ROOT / 'SKILL.md', media_type='text/markdown',
            filename='SKILL.md', headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    @app.get(prefix + '/operations.json')
    def schema():
        return response({name: {'description': description, 'argumentsSchema': model.model_json_schema()}
                         for name, (model, description) in TOOLS.items() if name != 'research_page_context'})
