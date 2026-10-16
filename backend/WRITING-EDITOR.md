# #22 writing editor backend

Server drafts are mutable private working copies. Only explicit draft commit or
proposal acceptance creates a content version. Codex `research_document_update`
now creates a pending proposal and returns its status/base identifiers instead of
updating the document head. Tool input remains unchanged. The user accepts a
reviewed title/content through the editor HTTP API; Codex has no accept tool.
New-document `research_document_save` and material tools retain their behavior.

All writes reuse the authenticated owner, CSRF checks, owner-scoped idempotency
keys and graph transaction lock. Draft revisions and document versions are
independent. Commit/accept checks all expected values before creating one snapshot
and closing the draft/proposal atomically. Replays return the original response;
new writes against terminal states fail. Original committed/discarded drafts and
accepted/discarded proposals are retained, and existing publication snapshots do
not change. A late autosave cannot revive a closed draft. Drafts of archived
items remain readable/discardable, but cannot be saved or committed.

The API contract is `agent-worktree-comm/api-contracts/issue22-writing-editor.md`.
Diff uses existing immutable version GETs on the client; there is no diff endpoint.
Proposal `origin=codex` describes its authenticated creation path. Existing
versions are not retrospectively labeled as model-generated.

## Reviewed migration / deployment handoff

New revision: `0005_writing`, additive from `0004_publication`. It adds only
`research.document_drafts` and `research.document_proposals` plus indexes/grants.
Existing items, versions, publication records, conversations and native state are
not rewritten. Auth migration marker remains unchanged. The schema contract is
frozen in `db/005_writing_contract.json` and effective privileges are checked.

PM should use the reviewed #22 runtime/provisioner image, take a database backup,
quiesce writers, and run the existing maintenance container workflow with:

```
python -m app.migrate --revision 0005_writing
```

Run this inside the authorized migration container with its existing private
ADMIN_DATABASE_URL/API_DATABASE_PASSWORD supply; never add secrets to argv or
logs. This document does not perform or claim a live migration. Check the research
health route and a disposable owner draft autosave/commit/proposal acceptance
before enabling the editor. Native runner code needs the updated turn prompt;
the API callback always enforces proposal semantics even if an old thread cached
the old dynamic-tool description. Do not use old callback code during rollout.

Before migration, draft/proposal routes and document_update fail closed instead
of falling back to direct latest-version writes. The general research API can
still serve supported older revisions. On rollback preserve the DB and writing
history; older application builds may refuse the new marker, so roll back only
to a build that explicitly understands 0005 or restore a separately verified
candidate. No automatic downgrade/deletion is provided. `app.restore_research`
recognizes 0005, reapplies the new grants and revokes old login/tool grants while
retaining drafts/proposals with existing private history.

Legacy M3 0004-specific rehearsal/initial-deploy scripts retain their historical
revision gates; they are not the #22 migration command. Use the explicit revision
above for #22. Tests use a disposable Docker/PostgreSQL project, never live state.
