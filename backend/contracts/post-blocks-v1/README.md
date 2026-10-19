# Post blocks v1 — contract revision 1.1

`input.schema.json` is the Pydantic export from `app/post_block_contract.py`.
`golden.json` is the common frontend/backend expectation set. These files are the
adapter's input contract. The API uses post_blocks.canonicalize at all post input
boundaries. See backend/BLOCK_POSTS.md for storage and 0007 migration details.

## Additive revision 1.1 (#36)

The wire envelope stays `version: 1` (strict integer); `1.1` is the contract
revision, not a new document version. No migration or persisted row rewrite is
needed: all prior documents retain exactly the same canonical content and digest.
Unspecified font size inherits the surrounding typography; no mark is inserted.
Unspecified/null table column widths mean equal columns and remain omitted.

- A `{fontSize: pt}` mark accepts only integer points in
  `10,11,12,14,16,18,20,24,28,32,36`, including image/embed captions. It may coexist
  with all prior marks/link (caption links remain forbidden). At most one fontSize
  and one color per run; maximum 7 marks. Sort fontSize after color. Equal adjacent
  runs merge only when all marks/link match. String/float/bool/null sizes fail422.
  Editors and renderers use `pt` or exactly `pt * 4 / 3` CSS pixels, not rounded
  persisted pixels. Validate against the allowlist, then bound display to10..36pt;
  unsupported marks/values must not be silently discarded before saving.
- `image.widthPercent` accepts strict integers10..100 inclusive, default100.
  Render width relative to the containing column/content box, max-width100%,
  height auto, with display clamp10..100. Dragging rounds to an integer and clamps
  before submission; server rejects invalid values instead of clamping stored
  input. Width is a percentage of the column on desktop and its stacked container
  on mobile. Columns already permitted Image in v1's shape; this revision supplies
  explicit shared fixtures. Images still cannot be placed inside quote/list/table
  child slots. Nested columns/tables inside columns remain forbidden.
- `table.columnWidths` is optional/null or a list of1..20 strict integer weights,
  each1..1000. Its length must equal the rectangular table's cell count per row.
  Normalize by the greatest common divisor (`[400,600]`→`[2,3]`); omit any equal
  vector (`[20,20]`→absent). A one-column vector is therefore always omitted.
  Render with fixed table layout and column percentages `100*w/sum(weights)`;
  absent means `100/columnCount`. Wrap cell content/overflow within the available
  width. Drag UI clamps/rounds weights before submit. Do not store measured pixel
  widths or require weights to total100. Merged cells remain forbidden.

Text projection and structural image authority are unchanged. Markdown projection
omits fontSize and columnWidths and lists both in formatSummary.omittedProperties.
Font size appears as changedProperties `runs`/`caption` (format-only); image width
as `widthPercent` (format-only); table weights as `columnWidths` (layout).
Equivalent canonical weights produce no diff. Existing global budgets stay:
2000 IDs, depth8, list depth3, image occurrences100, distinct body+cover images50,
tables20/cells1000 and bytes/text/runs limits. A direct image inside a column has
node depth3; marks and weights add no IDs or node depth but still count as bytes.

### Client capability and rollback

Human post draft create/update, proposal accept and draft commit require HTTP
`X-Post-Contract-Revision: 1.1` before a new write. Missing/other values return409
`post_contract_upgrade_required` with `details.requiredRevision: "1.1"`, before
any post/cache row write. Exact successful idempotent retries still replay under
the original owner/operation/body fingerprint without this header (no mutation).
Authentication/CSRF/owner checks remain. This capability declaration is not an
authorization secret; it fences old cached bundles that cannot preserve new data.
The new client must implement v1.1 before sending it, show upgrade/read-only UI on
that error or unsupported content, and never silently drop fields. Old clients
may still read, but cannot save draft content or commit it. Codex Markdown propose,
research documents/materials and body-free post archive/discard/publication do not
require this header. No CORS configuration change: the application is same-origin.

Backward data compatibility does **not** imply old servers accept newly written
v1.1 marks/widths. Rollback is supported before new-feature writes, or to another
v1.1-capable image; no lossless rollback to #32 after such writes is promised.
PM accepted this forward-only constraint. Deploy matching API/editor/public
renderer together; a cached old client failing the gate must reload.

## Consuming fixtures

- `valid`: input, expected canonical output, exact text projection, sorted structural
  image references (without a cover). Adapter round trips must preserve canonical
  output, including IDs, LF, run merging and cell paragraph alignment omission.
- `invalid`: normative 422 code and request JSON Pointer. `stage=shape` is rejected
  by the exported shapes. `stage=semantic` needs the shared document validator;
  accepting the JSON Schema alone does not make these inputs valid for storage.
  Do not expose raw Pydantic union branch names as API paths. Paths are against a
  request `{content: document}`; root pointer is `""`, escape `~` as `~0`, `/` as `~1`.
- `formatDiff`: expected diff of already canonical documents; equal text must not
  hide formatting changes. `markdown` specifies conversion outcomes, not block IDs
  or complete converter fixtures yet. The converter implements these policies; its dedicated tests additionally cover seed conversion.
- `limits`: agreed budgets. Aggregate limits, UTF-8 byte length and ownership are
  not fully expressible in JSON Schema. Treat all limits conjunctively.

Shape tests verify reproducible export. test_post_blocks executes canonical,
projection, semantic rejection and format expectations; test_post_block_markdown
executes conversion policies. PostgreSQL tests verify the API boundary and atomic
migration. These are isolated checks, not live rollout or browser equivalence.

## Canonical details

Input defaults: paragraph/heading/image/cell align=left, marks/captions=[], image
alt="", widthPercent=100, code language=plain, row header=false. An ordered list's
omitted/null start becomes 1. An unordered list's omitted/null start is absent in
canonical output; a non-null start on an unordered list is 422. Optional link and
cell background may be absent/null (unset); canonical output omits unset values.
Other required fields cannot be null. Background is transparent when unset.

Canonical output materializes the above defaults except cell paragraph align,
which is always omitted; input cell paragraph align may only be absent/left.
Colors become uppercase. CRLF becomes LF before control validation. Mark order:
bold, italic, underline, strike, code, color, fontSize. Duplicate mark kinds are errors;
identical adjacent runs merge after normalization. Code may coexist with color
and link; image/embed captions forbid links. Empty document is one explicitly
identified paragraph with empty runs; no random ID creation during normalization.

Semantic checks enforced by the shared validator or storage boundary: unique IDs across all containers/items/rows/cells;
control characters; safe links (http/https/mailto/#anchor, no credentials/controls);
mark uniqueness; header only on first row; rectangular tables; ordered-list start;
list depth<=3; node depth<=8; all aggregate limits; caption text<=2000; canonical
run<=20000 after merging; image ownership/existence/readiness. Boolean values are
not integers for version/level/width/start. Canonical UUIDs are lowercase with
hyphens. No image authority comes from text/code/link/alt/caption strings.

Depth counts ID-bearing nodes: top-level node=1, then item/column/row/cell and
child block each add one. Envelope, runs, marks and arrays do not add depth.
A nested list's list depth counts only list nodes. Columns cannot contain columns
or tables at any descendant; quote only permits paragraph/list; table only permits
paragraphs, so the restricted shape already prevents indirect forbidden nesting.

Plain projection is exact: join top-level blocks with two LF; paragraph/heading
join run.text without a separator; quote joins children with two LF; list joins
items with one LF and joins each item's paragraph/nested-list with one LF; divider
is empty text; code is unchanged text; table joins cell paragraph projections with
two LF, cells with TAB, rows with LF; columns join their block projections with
two LF and then columns with two LF. Image joins alt and caption with one LF when
both are nonempty (otherwise the nonempty value, or empty); embed uses
`youtube:<resourceId>` followed by one LF and caption only when nonempty. No trim,
Unicode normalization, bullets, ID insertion or escaping is applied to projection.

## Inventory decision and preservation

PM live snapshot: versions=4, publications=4, drafts=1, proposals=0. Maximum
estimated IDs=45, table=5x2, list depth=1, text=863 codepoints/1387 UTF-8 bytes.
Versions and publications each have two language downgrade occurrences; these are
retained snapshots, not four distinct posts. One internal-image candidate occurs
in each nonempty storage category, and still requires structural owner/reference
validation. No other listed conflict type was detected. This does not prove
converter fidelity or capture writes made after that snapshot.

Keep the existing proposed limits (including 2000 total IDs and 1000 cells).
A 1000-cell table may exceed 2000 IDs once paragraphs/rows are counted: smaller
individual bounds never override the total bound. Current data gives no reason
to increase it. Do not truncate; 422 content_limit_exceeded for aggregate overflow,
413 for request/canonical byte overflow. Exact boundaries are inclusive.

Migration preserves source Markdown plus digest privately, row IDs, heads, versions,
slugs, times, references and order. Unknown fence info becomes plain with warning,
without changing code text. Recheck every retained row with the actual converter
before writes. Any newly discovered HTML/MDX/control/multi-paragraph-list/quote-
heading/external-image/unsupported structure or limit overflow aborts the entire
migration pending a separately reviewed preservation rule. No flattening, deletion
or generic raw-HTML fallback. No external-image migration exception is authorized
by this inventory (none observed). A matching backup and writer stop/recheck remain
PM's application prerequisites. Seeds/public views must pass later rendering
comparison; this contract does not certify that check.

## Re-export (inside Docker)

Mount backend read-only at /source, set workdir and PYTHONPATH=/source, and run:
`python -B -m ops.export_post_block_schema`. Capture stdout to input.schema.json.
Use the repository's locked dependencies; no network is needed for export/tests.
