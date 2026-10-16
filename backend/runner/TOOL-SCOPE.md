# Issue 14 implementation checkpoints

The first slice replaces page-specific owner tool restrictions with `owner_research`
for every non-public page. Public document requests retain snapshot-only
`research_page_context`; they cannot invoke any owner DB tool. Missing trusted
scope still refuses tools. Existing token/login/approval/ownership/version/idempotency
checks remain. Page hints are resolved at admission but do not restrict later targets.
Archiving the viewed item does not block work on other owned data.

The prompt includes only request mode, a small untrusted page hint and the user text;
it no longer repeats allowedTools or inserts the full material/document body. Model
instructions never grant authority: both runner and API callback enforce mode, then
the Store enforces ownership. The existing per-request native catalog is unchanged.
Tool descriptions explain web material provenance and versioned, citation-preserving
writing. No new publish or destructive deletion tool is added.

This checkpoint does not enable shell/web or claim #14 operational acceptance.
Subsequent slices must validate the pinned Codex 0.160.1 binary and OS sandbox,
command concurrency/time/resource controls, private file isolation and turn timing.
The pinned generated schema exposes thread/start developerInstructions, but the
turn/start schema has no direct developerInstructions override. The compact mode
hint therefore remains attached to user input for now; public access is never
controlled by that text. Do not substitute an unverified latest-version API.

New owner image selection uses the #18 values/re-render path. Existing immutable
bindings require #16 `ops.runner_binding_image` report/apply after STOPPED fence and
termination proof; editing values.runnerImage alone cannot rewrite existing bindings.
PM owns image builds/imports, deployment, two-account network checks and canary/timing
measurements. Keep the existing runner network policy unchanged.
