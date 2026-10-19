# Block post storage (0007)

Post content now uses the v1 `post-blocks` object described in
`contracts/post-blocks-v1/`. Research material/document Markdown is unchanged.
The new runtime requires `0007_post_blocks`; it fails readiness for post operations
on the old marker. The old runtime cannot serve the new object storage format.
Deploy the matching frontend, API and runner tool definitions together.

## API and Codex boundary

Existing post-draft create/update and proposal-accept endpoints take canonical
block `content` (raw Markdown is rejected). The shared validator normalizes it,
reports source-free JSON Pointer errors in `error.details.path/code`, and validates
all structural and aggregate rules. Persisted versions/drafts/proposals/publication
snapshots retain block content and server `textProjection`. Owner serialization
also returns a readable `markdownProjection` and explicit lossy `formatSummary`.
Source selectors, image-owner checks, CAS, draft revision checks, preview tokens,
publication immutability, acceptance-only version creation and idempotency remain.
Image references come from `type=image.imageId` plus cover; their union is at most
50. Text/alt/caption/code/link image-looking strings grant no access.

`research_post_propose` takes the existing metadata/base/mutation fields and
`contentInput: {format: "markdown", text: "..."}`. `content` is not accepted by
this tool. Conversion uses owner + mutationId as a stable seed, never evaluates
HTML/MDX or fetches URLs, and rejects unsupported structures. Unknown code fence
language alone becomes `plain` with `conversionWarnings`. Proposal creation
stores no post version and leaves human drafts untouched. It returns candidateHash,
baseContentHash, baseTextProjection, textProjection and formatChanges.

Owner-only `POST /api/research/post-proposals/{id}/comparison` accepts exactly
`{content: blockDocument}` and recomputes against the current post head (or the
proposal's new-post draft). It returns baseVersionId/baseDraftRevision, both
content hashes, both projections and formatChanges. This is a read operation, no
idempotency key required; it neither saves nor accepts. Frontend must recompute on
candidate/base changes and keep the existing accept CAS checks. A comparison is
not an authorization to publish. API paths respect the configured base path.

Pre-0007 cached post-route mutation responses are adapted on read by deterministic
conversion; original fingerprints and cached rows are not rewritten, and no
mutation is replayed. Ordinary research document responses are not adapted.
For post HTTP writes and `research_post_propose`, the owner-scoped original
fingerprint is checked before the new input model. Only exact successful cache
hits bypass that model; changed bodies with the same key return 409, while
uncached legacy Markdown writes return 422. HTTP authentication/CSRF/readiness
and tool callback authorization/readiness still run before cache access.

## Migration and preservation

The operator runs the approved image's `python -m app.migrate --revision
0007_post_blocks` after quiescing writers and taking a matching DB/image backup.
Restore a production backup into an isolated candidate first and test this exact
release with the correct APP_BASE_PATH. No live apply is authorized by this file.

Alembic uses one transaction. The data migration locks posts/images/retained
stores/heads, reads all versions/drafts/proposals/publications regardless of
state, and aborts the entire transaction on unsupported syntax, limits, unexpected
content shape or changed image-reference/owner/deleted-image relationships.
Unsupported input is not flattened or stripped. Code language fallback preserves
code text. UUIDv5 node IDs derive from retained row ID and structural path;
publications use their version_id so matching version/publication bodies share IDs.
Existing row IDs, timestamps, slug, heads, metadata and image_ids are unchanged.

`research.post_markdown_backups` preserves each original Markdown, its SHA-256 and
the converted canonical-content SHA-256. It has no public/API-role privileges;
no serialization/API returns these originals. A repeated helper invocation checks
existing backup/content digests rather than converting twice; mismatches fail.
Alembic revision replay skips the completed migration, including later native
block writes. There is no downgrade path. Post-migration drafts may legitimately
change; preservation digests describe their original converted content, not their
latest mutable content.

Restore candidate validation restores the reviewed no-access backup-table grants
and verifies original Markdown/conversion digest pairs. Recovery signatures include
this private table, and full pg_dump backs it up with all research rows. Keep the
same application base path when verifying original image URLs. A failed data
conversion rolls back 0007; reverting an applied release requires the reviewed
matching DB/image backup in an isolated restore candidate, not schema downgrade.
Never delete/recreate the existing production image PVC.

## Validation boundaries

Unit golden tests exercise canonicalization, semantic/shape errors, projection,
format diff and conversion. PostgreSQL tests exercise owner/CAS/D1/history/image
retention and atomic migration/replay. The restore probe compares full database
row signatures plus independently restored image bytes. These do not establish
live provider operation or browser rendering equivalence. Frontend integration,
legacy/public browser comparison and actual operator rollout remain explicit gates.
