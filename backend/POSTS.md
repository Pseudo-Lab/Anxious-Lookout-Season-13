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
backup. Do not apply an earlier provisional 0006. Run the renderer from the
approved SHA's runtime digest (or same-SHA provisioner), using the mandatory
module hash preflight in [the rollout runbook](../infra/hosting/m3/multi-user-runners/rollout/README.md).
A `/source` bind does not prove which module Python imports. Record actual module
path/hash from stdout and renderer-provenance.json. Inspect post-images-pvc.json,
api-candidate.json and api-patch.json; missing PVC, wrong claim/root, read-only
mount or mismatched patch fails preflight. Values must explicitly provide
postImagesStorageClass (`m2-postgres-retain` on this cluster); there is no default.
The PVC requests 10Gi RWO in m2-hosting under hosting-post-images.
Confirm encryption, node affinity, capacity and Retain policy. No emptyDir fallback.
A single API writer replica is the supported rollout; stop old replicas during
cutover so RWO scheduling cannot leave two unsynchronized deployments.

1. Follow the rollout runbook's existing-PVC gate before applying anything.
   If absent, verify the selected StorageClass uses Retain, then create using
   `kubectl create -f post-images-pvc.json` (an existence race must fail).
   If present, assert expected UID, Bound and bound PV Retain, then **skip PVC apply**.
   The existing live PVC UID is `2eefbd17-f87d-4880-90a7-a6918f759536`, class
   local-path; PM patched its PV to Retain on 2026-10-17. Do not delete/recreate
   it or change its immutable storageClassName to match the new template.
   For WaitForFirstConsumer,
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
   Files are 0600. Read-only root filesystem remains enabled.
   Keep the local-path parent (observed 0:10001 mode2777) exclusive to trusted
   writers; never share it with another untrusted UID writer. Protect the private
   data child (observed 10001:10001 mode2700). A setgid bit does not grant access.
   local-path is node-local, not HA or node-loss recovery; a 10Gi request is not
   a physical quota. Record actual disk usage and matched DB/image backup and
   recovery evidence before claiming operational durability.
   Check the PVC and mounted path explicitly before opening admission; a directory alone is not
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

Build the API, runner and frontend artifacts from the final approved SHA before
patching a Deployment to use them. Record their immutable image digests and
confirm availability on the selected node (the renderer uses imagePullPolicy
Never). A development test image with an earlier release stamp and a /source bind
is test evidence only, not a release artifact. Keep admissions stopped across
the migration and image switch; do not reopen until both the final 0006 schema
and the matching API/frontend images pass the checks above. The initial 0005
backup predates image metadata, so post_image_inventory applies only after 0006;
record an empty/pre-cutover image PVC separately for that initial backup.

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

### Offline frontend integration fixture

The integration Compose API now mounts project-scoped `post-images` at
`/var/lib/posts/images`; the offline overlay preserves this mount alongside its
read-only fixture credentials. Rebuild the test image from this branch and set
M3_FIXTURE_IMAGE to that image. Its Dockerfile seeds UID10001 mount ownership.
Use a fresh disposable database migrated explicitly to 0006_posts, as in the
frontend live-offline script; older provisional schemas/images are incompatible.

In the offline native fixture only, a user message containing
`/fixture/post-propose` reads the current post with research_get on a post page,
or the latest draft revision with research_post_draft_get on a post_workspace
page with draftId, then calls research_post_propose with a complete candidate.
Without either hint it proposes a new post. Source metadata is retained and
"Complete fixture post proposal" is appended to title/body. Read failure stops
without a proposal. This creates a pending proposal only; acceptance remains the
human HTTP action. The production native binary has no such fixture command.

## #31 writing input update (W1/W3)

The superseding contract is `agent-worktree-comm/api-contracts/issue31-write-ux.md`.
New drafts/proposals do not reserve a slug: supplied values are ignored. The first
human commit/accept allocates `post-` plus UUIDv4 hex under the global slug lock,
retrying collisions. Existing/imported addresses remain permanent. Existing-post
writes may omit slug, while an explicit different nonempty slug is rejected.

PostText now accepts materialIds. Omission expands the chosen documents' active
direct material relations once; an explicit array (including empty) is the final
selection. The resolve endpoint lets the editor merge sources when a document is
added; both automatic and direct sources can be removed. Stored materialSources
records document origins for display. References are validated under the owner
graph lock, bounded to50, and frozen with each version/proposal/draft. Relation
changes never recompute a saved selection. Publication previews bind selected
material versions/archive state; public snapshots include only id/title/sourceUrl,
never material bodies, notes or relation descriptions.

This update extends existing JSONB payloads, not database structure. No0007
migration, rewrite of retained records, or change to imported addresses is needed.
Old payloads/snapshots are read with empty material fields. Continue the existing
0006 readiness and coordinated backup procedure when deploying the reviewed
images. Old immutable public snapshots remain unchanged in storage. W4 is outside
this backend change until a separate decision is delivered.
