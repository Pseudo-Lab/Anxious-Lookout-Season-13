---
name: anxious-research
description: Read and save the user's materials, research documents, and posts through the Anxious Lookout research HTTPS API and CLI; use the accepted snapshot loader for public pages.
---

For owner research, use the platform `anxious-research` CLI and its turn grant
file. If the CLI or grant is unavailable, report that access is unavailable.
Never use internal callbacks or copy provider/broker/runner credentials. Reference
the grant file through the CLI; never print its contents or put a token literal
in a command, prompt, research content or output. Public pages provide only the
non-shell `research_page_context` loader for the admitted snapshot and direct
references; do not use shell or owner operations there.

Discover arguments with `anxious-research schema` or
`anxious-research schema research_material_save`. Call an operation by piping its
JSON arguments to the fixed CLI, for example:

```sh
printf '%s' '{"type":"document","limit":30}' | anxious-research research_list
```

The CLI reads `/state/research-api/turn.json` itself. There is no token argument,
custom endpoint, proxy, redirect, TLS bypass or automatic retry option. It returns
the API JSON and exits nonzero on error. Use the schema for mutation payloads;
do not guess fields or use curl with a token literal.

Collect source text as materials, synthesize documents, then write posts from
chosen documents and materials. Preserve sourceUrl, actual collectedAt and
contentKind: full only for a complete captured source, excerpt for a portion,
summary for synthesis. Preserve citations and distinguish quotations.

Read the current item/version and a post's human draft and source documents
before editing. Save posts with contentInput as in Post blocks; edit existing posts with
post-block-patch.
Supply materialIds when choosing particular sources; omission expands selected
documents' sources once. Saves append immutable versions immediately, leave
human drafts active and unchanged, and do not create proposals. Existing posts
require postId and current expectedVersion; draftId and expectedDraftRevision
are paired when referring to a specific draft.

## Research wiki

Materials are wiki entries, one source each. Relations are reasoned links
between two items. Documents hold comparisons and syntheses across materials.

| What | Where |
|---|---|
| One source's summary, evidence and interpretation | that material's body |
| A link or contrast between two items | a relation (kind + description + evidence) |
| A comparison or synthesis across materials | a document (no type field; its title and summary say what it is) |

Never write a cross-material comparison into one material's body. Revise an
existing document on the same topic as a new version instead of adding another.

Material body template (Markdown headings; the server does not enforce it):
`## 핵심 질문`, `## 방법`, `## 결과`, `## 한계`, `## 원문 근거`, `## 해석`, `## 열린 질문`.
- What the source says is a quote with its location: `> … (p.12)` or `(§3.2)`.
- Your or the user's interpretation goes only under `## 해석`, as does any
  claim without source evidence.

Materials and documents have `summary`: one line, at most 300 characters.
Materials have `bibliography`, a CSL-JSON subset: type, title, author
([{family, given}] or [{literal}]), issued ({"date-parts": [[year, month, day]]},
month/day optional), container-title, publisher, volume, issue, page, number
(arXiv: "arXiv:<id>"), DOI (without https://doi.org/), ISBN, ISSN. Do not send
URL or accessed: sourceUrl and collectedAt are used. Other keys are rejected.
On updates, omitted summary/bibliography keep the latest values; null clears.

Relation kinds (source → target; the source is the item making the statement,
usually the new material). Only same_topic is undirected; omit `directed`.
Document–material relations go from the document with a directed kind.

| key | forward / inverse | polarity | directed |
|---|---|---|---|
| `supports` | 지지함 / 지지받음 | positive | yes |
| `confirms` | 재현·확인함 / 확인됨 | positive | yes |
| `extends` | 확장함 / 확장됨 | positive | yes |
| `disputes` | 이견 있음 / 이견 받음 | negative | yes |
| `refutes` | 반박함 / 반박됨 | negative | yes |
| `qualifies` | 조건을 닮 / 조건이 달림 | neutral | yes |
| `uses_method` | 방법 차용 / 방법 제공 | neutral | yes |
| `uses_data` | 데이터 사용 / 데이터 제공 | neutral | yes |
| `updates` | 갱신·대체함 / 갱신됨 | neutral | yes |
| `same_topic` | 같은 주제 / 같은 주제 | neutral | no |

Every relation needs a non-blank `description` saying why the items are linked.
Add `evidence` (at most 20): {itemId: the source or target, versionId: the
version of that item you read, locator: "p.12"/"§3.2", quote: at most 500
characters}; a locator or a quote is required. Older free-text kinds read as
기타; updating one requires a vocabulary kind with the same direction.

Ingest a new source:
1. Read research_list (the index: summary, relationCount, updatedAt) to find related items.
2. Save the material with the template, summary and bibliography, or update the existing one.
3. Revise related materials and documents as new versions; history keeps the old ones.
4. Relate the new material to existing items; record contradictions as refutes or disputes.
5. Run research_lint and fix what it reports. Semantic contradictions are yours to check.

## Post blocks

Posts are stored as a block document (`post-blocks` v1): `latestVersion.content`
from research_get, `content` from research_version_get, `draft.content` from
research_post_draft_get. Block shapes are the `$defs` in research_post_save's
schema (`anxious-research schema research_post_save`).

- Edit an existing post with contentInput `post-block-patch`: 1–200 operations
  applied in order to the head version; every other block keeps its formatting
  and id. `{"op":"replace","id":ID,"block":{...}}`,
  `{"op":"insert","afterId":ID,"block":{...}}` (or `beforeId`; exactly one),
  `{"op":"remove","id":ID}`. IDs may be any node (block, list item, table
  row/cell, column); move = remove then insert with the same id; replace a
  parent to fill an empty container.
- `post-blocks` sends a whole `document` (`{"format":"post-blocks","version":1,"blocks":[...]}`)
  for a new post, full rewrite or a post built from a human draft. New nodes
  need new lowercase UUID ids, unique in the document.
- `markdown` suits new or plain posts. If the head has formatting Markdown
  cannot keep, the save is refused with 422 `formatting_would_be_lost`
  (`error.details.properties`, `blocks`, `hint`). Use a patch; set
  `allowFormatLoss: true` in contentInput only when the user accepts the loss.
  A post's `formatSummary` shows whether it has such formatting.
- Block errors give one JSON Pointer in `error.details.path`, e.g.
  `/contentInput/document/blocks/3/runs/0` or, for a patch, the operation:
  `/contentInput/operations/2/block/...`. Saves return `formatChanges`.
- Limits: a document is at most 1 MiB compact UTF-8 JSON and 200,000 text
  characters. The CLI sends at most 1,000,000 bytes of arguments (larger input
  is not sent) and reads at most 8,000,000 bytes of response (a larger response
  may follow a completed save: check versions); both print
  `research_api_unavailable`. A patch sends only changed blocks, so its
  arguments are smaller (same limit): prefer it for large posts.
- API responses (and the CLI output) show text that looks like a research token
  as `[REDACTED]`; inputs containing such text are refused. A whole-document save of what you read
  would store the mask instead of the original, so edit existing posts with a
  patch that leaves those blocks alone.

## Post Markdown

- H1–H6 are supported; start body headings at `##` because the post title is H1.
- Use paragraphs, **bold**, *italic*, ~~strike~~, inline code, links, ordered/bullet
  lists (one paragraph per item, up to3 levels), quotes (paragraphs/lists), fenced
  or indented code, tables and horizontal rules.
- Code languages: plain, bash, c, cpp, css, go, html, java, javascript, json, jsx,
  markdown, python, rust, sql, tsx, typescript, xml, yaml. Other fence languages
  (including no language) become plain with `code_language_downgraded`.
- Images must be a standalone paragraph with a root-relative owned upload URL:
  `![alt]({BASE}/api/media/post-images/<UUID>)`; BASE is the configured path or empty.
  Inline/external images are rejected.
- Outside code, HTML, `{...}` expressions and import/export lines are rejected:
  422 `unsupported_content`. Size limits use `payload_too_large` (413) or
  `content_limit_exceeded` (422). Unsupported structures are not flattened.
- Text must contain real newline characters. JSON-encode once: wire `\n`, not `\\n`.
- Markdown saves cannot represent font size/color, underline, alignment, columns,
  table-cell backgrounds or embeds. Edit formatted posts with post-block-patch
  (see Post blocks).
- Research documents use GFM, skip HTML, show images as links and code without
  execution/highlighting; they do not use the post-block converter.

Use a fresh UUID mutationId for each distinct change. An explicitly chosen
identical retry reuses that ID. On CAS conflict, reread and reconcile against
the current resource/draft, then use a new ID for changed arguments. Never just
replace expectedVersion and resend stale text. Stop on unresolved conflict.
Timeout is not proof that nothing was saved; check versions before deciding.
No automatic retry/replay. A research grant expires after at most30 minutes or
at the original login expiry. On research_token_expired, explain that a new turn
is needed; do not renew credentials or resubmit the user's message.

Publishing and withdrawal remain human actions; saved versions do not change
public snapshots. Archive only when requested and preserve history. Report the
actual returned saved item/version/destination. History supports diff and restore;
restore appends a version. Remaining old proposals may still be handled by the
human editor, but do not generate new proposals.
