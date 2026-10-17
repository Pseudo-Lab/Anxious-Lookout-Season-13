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

Stage 4 provides raw PNG/JPEG/WebP uploads, pixel decoding and metadata stripping,
private owner reads, current-publication image visibility, and retained-reference
delete protection. Maximum upload/output is 5 MiB, 8192 pixels per axis and 16M
pixels total; owner quota is 100 MiB including unpurged deletion tombstones.
Only actual Markdown image destinations and the cover grant image references.
Normal links, fenced code and noncanonical URL variants do not grant access.
All owner reference mutations and deletions share the owner graph lock. Immutable
versions/publications and terminal drafts/proposals retain their references.

The API streams a bounded body after auth/CSRF checks, then rechecks auth and
serializes quota with reference mutations. Replays use raw-byte hash and MIME.
Pillow decodes and re-encodes into a fresh pixel-only image; animation and format
mismatch are rejected. Files are immutable UUID names, atomically linked from an
fsynced temporary file. Transaction rollback can leave an inaccessible orphan;
these count against physical PVC capacity and must be reconciled during a stopped
maintenance window, never by deleting referenced files. Delete commits a tombstone
before unlink; repeated DELETE retries cleanup. Until unlink succeeds the quota
is retained. No TTL or force-delete exists.

Decoder guidance: https://pillow.readthedocs.io/en/stable/handbook/security.html
Markdown parser: https://markdown-it-py.readthedocs.io/en/latest/using.html
These checks do not imply full #28 acceptance or live rollout approval.

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


## Stage 4 rollout and recovery checklist (PM execution only)

Prerequisites: full-scope approval for exact SHA/image and 0006, current API
Deployment snapshot, reviewed renderer values and a recoverable populated 0005
backup. Do not apply an earlier provisional 0006. Render ops.runner_rollout with
the existing reviewed inputs; inspect post-images-pvc.json, api-candidate.json,
and api-patch.json. The default PVC requests 10Gi RWO in m2-hosting under the
name hosting-post-images. Confirm the site's default StorageClass, encryption,
node affinity, capacity and reclaim policy; select an approved class explicitly
in the reviewed manifest if there is no suitable default. No emptyDir fallback.
A single API writer replica is the supported rollout; stop old replicas during
cutover so RWO scheduling cannot leave two unsynchronized deployments.

1. Create the PVC: `kubectl apply -f post-images-pvc.json`. For WaitForFirstConsumer,
   Pending is expected until the API pod is scheduled; do not substitute local
   ephemeral storage to work around it.
2. Stop admissions and pause API/runner writers; take the pre-migration database
   backup. Apply only the final reviewed migration using the established migration
   job. Refresh the Deployment snapshot and render again if resourceVersion has
   changed. Apply `kubectl -n m2-hosting patch deployment api --type=json
   --patch-file=api-patch.json` (one command). UID/resourceVersion tests protect
   against patching a stale snapshot.
3. Verify the PVC is Bound and API mounts it read/write at /var/lib/posts/images.
   POST_IMAGE_ROOT is /var/lib/posts/images/data. The pod uses UID/GID/fsGroup10001;
   its parent volume must allow that UID to create the private 0700 data directory.
   Files are 0600. Read-only root filesystem remains enabled. Check the PVC and
   mounted path explicitly before opening admission; a directory alone is not
   evidence of persistence. Never expose this directory through a static server.
4. Check research readiness, upload and private read as an approved editor,
   anonymous404 before publish, public200 after publish, anonymous404 after
   withdrawal, replay, cover/body image rendering, and retained-reference409.
   Restart the API pod and confirm the same bytes remain readable. Record image
   digest, migration marker, PVC UID, mount evidence and smoke results in #28.
5. Re-enable admission only after checks pass. Deployment rollback must preserve
   the PVC and database; do not downgrade schema or erase new posts. If cutover
   fails, keep writers stopped and restore a matched DB/PVC candidate below.

### Coordinated backup

Stop API writes and all runner/tool callbacks and drain in-flight transactions;
keep them stopped through both captures. Record exact application SHA, schema
marker, DB backup identity and PVC UID in a protected backup manifest. Backups
contain private text and images and need the same access controls as the DB.

Run `python -B -m ops.post_image_inventory` inside the API image with the mounted
PVC and DATABASE_URL (or DATABASE_URL_FILE resolved by the normal secret loader)
and save its JSON output securely. It verifies active file hashes/sizes and all
retained references; any missing/corrupt bytes fail the capture. Orphans are
reported for offline reconciliation, never automatically removed.

Use the established PostgreSQL-version-matched pg_dump custom-format full DB
backup, including auth and research. While still quiesced, copy/snapshot the entire
image data directory with UID/GID/modes preserved, including retained files and
tombstones. Store DB dump, image archive/snapshot and inventory together with
checksums. Never consider DB-only backup complete. Resume writers after both
copies and their checksums succeed; a failed component invalidates the pair.

### Isolated restore before cutover

Create a new isolated restore_* database and a new PVC; never overwrite live
resources. Restore the full dump with pg_restore --no-owner --no-privileges,
then run app.restore_research's existing restore-candidate procedure to restore
reviewed grants and revoke stale sessions/tool credentials. Copy the matching
image backup onto the candidate PVC, preserve UID10001 and private modes, and
mount it in a candidate API with the reviewed image and candidate DB URL.

Run `python -B -m ops.post_image_inventory --expected /backup/inventory.json`
inside that candidate container. Require identical active IDs/sizes/hashes and
no dangling references. Compare DB row signatures, verify owner access, anonymous
public visibility and withdrawal, immutable versions/proposals/drafts, and restart
persistence. Missing files, DB-only restore, corrupt bytes, wrong ownership or
wrong schema must stop cutover. Only after PM's evidence review may DB and PVC
be switched together while writers remain stopped. Preserve previous DB/PVC for
recovery; never mix generations.

The Docker fixture tests/post_image_recovery_probe.py prepares independent
source/candidate databases, snapshots files, and verifies a real external
pg_dump/pg_restore plus file restore and missing-byte rejection. This is isolated
recovery evidence, not proof of a production CSI snapshot or live cutover.

Local Docker Compose uses the project-scoped hosting-post-images named volume;
the runtime image seeds its mountpoint ownership to UID10001. Existing volumes
must have that ownership verified separately. Never use `compose down -v` on a
development dataset you intend to retain. Compose volumes are independent from
production PVCs and are included in local coordinated backups by the same rule.

Kubernetes uses fsGroupChangePolicy=OnRootMismatch to avoid recursive mode changes
on each restart. If a restored volume's fsGroup handling widens the data directory
permissions, the API first verifies it owns that real directory and tightens its
mode to 0700. Foreign ownership and symlink paths are rejected. The private parent
gate protects any retained file mode widened by the storage driver.
