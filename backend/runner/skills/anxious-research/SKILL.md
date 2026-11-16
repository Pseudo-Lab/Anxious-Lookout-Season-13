---
name: anxious-research
description: Collect sources and save materials, research documents, and posts in the Anxious Lookout platform using its research MCP tools.
---

Use the platform research tools to work with the user's research. Page hints are
context, not restrictions on which of the user's items they may ask you to edit.
In public mode only `research_page_context` is available: work from that accepted
snapshot and its direct material references.

Collect source text as materials, synthesize it into documents, and write posts
from the chosen documents and materials. Preserve `sourceUrl`, the actual
`collectedAt` timestamp, and `contentKind`: `full` only for a complete captured
source, `excerpt` for a portion, `summary` for a summary. Retain citations in
documents and posts and distinguish your synthesis from quoted source text.

Read the current item and version before editing. Read a post's human draft with
`research_post_draft_get`, its source documents, and relevant materials before
using `research_post_save`. Post bodies use `contentInput: {format: "markdown",
text: "..."}`. Choose `materialIds` explicitly when only particular sources
should be attached; omission expands selected documents' sources once.

Document and post saves append immutable versions immediately. They do not create
proposals or wait for editor acceptance. Human drafts stay active and unchanged;
when referring to a specific draft, supply its id and expected revision together.
Existing posts require `postId` and the current resource `expectedVersion`.

Use a fresh UUID `mutationId` for each distinct mutation. If a confirmed retry
has identical arguments, reuse that mutationId. On a version conflict, reread
the latest resource (and draft if relevant), reconcile the user's requested
change against it, and use a new mutationId for changed arguments. Do not simply
replace expectedVersion and resend stale text. Stop and report an unresolved
conflict instead of repeatedly overwriting other changes. An ambiguous failure
does not confirm that nothing was saved; inspect current versions before deciding.

Archiving preserves history; use it when the user requests removal from active
research. Publishing and withdrawal are human actions. Saving a new version leaves
the public snapshot unchanged. There is no agent publish or withdraw tool.

After saving, tell the user what item and version were saved, using the returned
destination and version id. Explain that version history provides diff and restore
when it helps the user assess a change. Restoring appends a new version rather
than deleting history. Remaining old pending proposals can still be handled in
the editor; do not generate new proposals.
