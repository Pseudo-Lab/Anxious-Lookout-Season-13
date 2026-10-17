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

Image capability, post publication, MDX seed and Codex post proposals are later
checkpoints. Until implemented, coverImageId and internal image URLs fail closed
with 503, and Post.publication is null. The legacy document_save tool still has
its 0005 behavior at stage 1; D1 enforcement is the next checkpoint, not a claim
that this partial branch already meets #28 acceptance criteria.

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
