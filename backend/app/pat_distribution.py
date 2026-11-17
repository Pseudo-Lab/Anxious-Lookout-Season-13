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

For post saves use contentInput `{{"format":"markdown","text":"..."}}`;
existing posts also need postId and expectedVersion. Saves append versions and
leave human drafts and public snapshots unchanged. Use the schema for other fields.

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
  table-cell backgrounds or embeds. Existing formatting of these kinds is lost
  in the new version. Tell the user before saving edits to a human-formatted post.
- Research documents use GFM, skip HTML, show images as links and code without
  execution/highlighting; they do not use the post-block converter.

Public reads need no PAT: GET {settings.origin}{settings.base_path}/api/public/documents
or {settings.origin}{settings.base_path}/api/public/posts.
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
