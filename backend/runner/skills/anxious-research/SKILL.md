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
before editing. Save posts with contentInput {format: markdown, text: ...}.
Supply materialIds when choosing particular sources; omission expands selected
documents' sources once. Saves append immutable versions immediately, leave
human drafts active and unchanged, and do not create proposals. Existing posts
require postId and current expectedVersion; draftId and expectedDraftRevision
are paired when referring to a specific draft.

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
  table-cell backgrounds or embeds. Existing formatting of these kinds is lost
  in the new version. Tell the user before saving edits to a human-formatted post.
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
