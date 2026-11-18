---
name: anxious-research-pat
description: Read public Anxious Lookout content and read or save the PAT owner's materials, research documents, and posts from an external agent using the public HTTPS research API.
---

Use this external client when the user provides their platform HTTPS origin and
private PAT-file location. For platform-internal Codex, use its installed
`anxious-research` turn-grant skill instead. Never borrow internal grants, runner,
broker or provider credentials. This skill does not authorize publishing or
withdrawal, token issuance/revocation, or model/provider execution.

Download `scripts/pat-client.py` from the same platform origin's
`/api/research/client/pat-client.py`. It requires Python3 on POSIX with platform
OpenSSL CA paths. The user stores the token alone in their own0600 regular file
inside their own0700 directory; no symlink/hardlink. Keep the actual value out of
argv, environment, prompts, output and research content. The client accepts its
file path only. Obtain a new token through the account UI if access expired or
was revoked; do not issue or silently replace one for the user.

Set the origin to the site's public HTTPS origin, including an IP literal when
that is the site's origin, and add `--base-path` only if the site actually uses
one. Never use a Kubernetes Service or internal callback URL. No proxy, TLS
bypass, redirect following or automatic retry is supported.

Discover fields from the server schema rather than guessing:

```sh
python3 -I scripts/pat-client.py --origin https://platform.example schema research_material_save
printf '%s' '{"type":"document","limit":30}' | python3 -I scripts/pat-client.py --origin https://platform.example --credential-file /private/research.pat research_list
python3 -I scripts/pat-client.py --origin https://platform.example public --path /api/public/documents
```

Public reads need no token. An approved commenter PAT still permits only public
content. All PATs permit private reads and saves for a currently approved
editor/admin; a token does not grant an account role.
Read the current resource/history, source documents/materials and human post
draft before edits. Collect materials with sourceUrl, actual collectedAt and
contentKind(full/excerpt/summary); preserve citations and distinguish synthesis.
Save posts with contentInput {format: markdown, text: ...}. Existing resources
need their current expectedVersion; draftId/expectedDraftRevision are paired.
Saves append immutable versions immediately and retain active human drafts and
public snapshots. Archive only when requested, with history preserved.

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

Use a fresh UUID mutationId per distinct change. Only an explicitly chosen
identical retry reuses it. On CAS conflict reread/reconcile and use a fresh ID for
changed arguments; do not merely change expectedVersion on stale text. On timeout
or lost response, check versions before deciding whether to repeat. On429 or
permission failure stop and explain; never automatically retry. Report the actual
returned resource/version/destination. A save is not publication.
