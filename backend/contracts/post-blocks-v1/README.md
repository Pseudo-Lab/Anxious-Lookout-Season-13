# Post blocks v1 — contract 0.3

`input.schema.json` is the Pydantic export from `app/post_block_contract.py`.
`golden.json` is the common frontend/backend expectation set. These files are the
adapter's input contract, not evidence that the application's validator, converter,
projections, or migration are implemented. No API routes use these models yet.

## Consuming fixtures

- `valid`: input, expected canonical output, exact text projection, sorted structural
  image references (without a cover). Adapter round trips must preserve canonical
  output, including IDs, LF, run merging and cell paragraph alignment omission.
- `invalid`: normative 422 code and request JSON Pointer. `stage=shape` is rejected
  by the exported shapes. `stage=semantic` needs the subsequent document validator;
  accepting the JSON Schema alone does not make these inputs valid for storage.
  Do not expose raw Pydantic union branch names as API paths. Paths are against a
  request `{content: document}`; root pointer is `""`, escape `~` as `~0`, `/` as `~1`.
- `formatDiff`: expected diff of already canonical documents; equal text must not
  hide formatting changes. `markdown` specifies conversion outcomes, not block IDs
  or complete converter fixtures yet. No converter is part of this slice.
- `limits`: agreed budgets. Aggregate limits, UTF-8 byte length and ownership are
  not fully expressible in JSON Schema. Treat all limits conjunctively.

Tests in this slice verify shape acceptance/rejection and reproducible export.
They do NOT claim to execute canonicalization, projection, semantic rejection,
format comparison, Markdown conversion or API error mapping. Those expected
outputs intentionally precede implementation and become its regression oracle.

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
bold, italic, underline, strike, code, color. Duplicate marks/color are errors;
identical adjacent runs merge after normalization. Code may coexist with color
and link; image/embed captions forbid links. Empty document is one explicitly
identified paragraph with empty runs; no random ID creation during normalization.

Semantic checks still required: unique IDs across all containers/items/rows/cells;
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
