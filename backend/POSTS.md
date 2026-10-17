# Posts implementation checkpoints (#28)

The shared contract is `agent-worktree-comm/api-contracts/issue28-posts.md`.
This branch is under development: partial review checkpoints are not rollout
approval. Do not apply its provisional 0006 migration to a live database before
full-scope review. Disposable test databases must be recreated if the unmerged
0006 schema changes between checkpoints. Existing live 0005 data is preserved.

Stage 1 provides owner posts, immutable versions, server drafts, document option
search and reference validation. There is no direct post creation/update route:
human edits commit drafts. A saved slug is permanent, including after archive.
Document references belong to each immutable version, while draft references
are mutable only with matching revision. Archived references are retained in
history and must be removed before a new commit. No private document body is
copied into a post automatically.

Stage 2 adds research_post_propose and research_post_draft_get, post read tools,
post page hints, and owner proposal discovery/acceptance/discard. Proposal creation
does not change the committed head or human draft. Acceptance verifies the current
head and active draft revision, creates exactly one snapshot, and preserves the
original proposal and draft text. A proposal for a new post has no post until it
is accepted. Slug reservation also happens at acceptance.

D1 also changes research_document_save into a pending proposal for a new document.
Discover it at /api/research/document-proposals and explicitly accept or discard
it there. Existing document proposals keep their nested document routes. New
document proposals have null document/base fields until accepted, then expose
acceptedDocumentId and acceptedVersionId. Old-schema new-document tool calls fail
closed; they never fall back to direct creation. Existing human document APIs and
material collection tools retain their behavior.

Stage 3 adds immutable publication snapshots, preview tokens bound to reference
state, republish/withdraw, anonymous listing/detail/tags and revalidation ETags.
Only the current head is public. References expose titles and public links, never
private document bodies. Archiving a post atomically withdraws its publication.
Public-post conversations use the accepted publication snapshot and have no
owner tools, shell or web-search authority.

The original three MDX files are frozen in db/legacy-posts; 0006 imports them once
with deterministic IDs, original slugs/dates/authors/body and owner=NULL. They are
public-only legacy imports, not implicitly owned by any account. Re-running the
reviewed migration does not duplicate them. Frontend MDX reads/files can be removed
after the seed checkpoint is pushed. Source import never executes MDX.

Image capability remains a later checkpoint. Until implemented, coverImageId and
internal image URLs fail closed with 503. This is not full #28 acceptance.

The migration extends structural/grant verification and the database recovery
signature. Old publication/writing/conversation capabilities recognize 0006.
All verification uses isolated Docker/PostgreSQL. No downgrade or destructive
rollback is supported. No production migration or image application is performed
by backend development.

Image delivery checkpoint must include API PVC creation and Deployment mount in
reviewed ops.runner_rollout templates (or a reviewed standalone manifest), with
PM instructions in this order: create PVC, patch Deployment, verify mount/UID,
then include image PVC alongside DB in backup and restore procedures. Database
backup alone will not be a complete post-image backup.
