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

The first checkpoint did not enable shell/web or claim #14 operational acceptance.
The web/timing slice below follows it. By user/PM decision on 2026-10-16, Bash,
OS sandbox validation, command concurrency/time/resource controls and private file
isolation are excluded from #14 and tracked in #23, whose start requires a user
decision. Shell remains disabled; BASH-BOUNDARY.md retains the investigation.
The pinned generated schema exposes thread/start developerInstructions, but the
turn/start schema has no direct developerInstructions override. The compact mode
hint therefore remains attached to user input for now; public access is never
controlled by that text. Do not substitute an unverified latest-version API.

New owner image selection uses the #18 values/re-render path. Existing immutable
bindings require #16 `ops.runner_binding_image` report/apply after STOPPED fence and
termination proof; editing values.runnerImage alone cannot rewrite existing bindings.
PM owns image builds/imports, deployment, two-account network checks and canary/timing
measurements. Keep the existing runner network policy unchanged.

## Web and timing slice

Each admitted owner request selects `config.web_search=live`; public and missing
trusted scope select `disabled`. The global default remains disabled. Shell remains
false and sandbox read-only. The access-only provider keeps its endpoint, auth and
zero-retry policy; its native provider display name is `OpenAI` so the pinned native
provider classification permits its web capability. Live provider execution still
requires PM canary evidence, including source URL and material provenance.

Pinned 0.160.1 retains loaded tool registries when thread/resume receives a different
web mode; unsubscribe does not fix this. When a loaded thread changes mode, the
adapter closes and restarts its owned app-server under the existing RPC mutex,
re-authenticates access-only mode, then resumes the same original thread ID with the
new config. No turn is replayed, no thread is replaced, and broker projections remain
signed. A failure stops that request. This adds measurable boundary-switch latency.
`tests/probes/issue14_native_web.py` demonstrates function/undefined/function across
live/disabled/live on the same thread, using the real pinned binary and code-mode
host against a synthetic loopback provider inside Docker `--network none`. It does
not make an actual web search. A simple config-only transition is insufficient.

The `uvicorn.error` INFO event `runner_turn_timing` contains only requestId, state,
promptChars, toolCalls and millisecond durations: queueMs (RPC lock), threadMs
(start/resume including any restart), dispatchMs (turn/start response), nativeMs
(turn/start through completion, including callbacks), toolMs (aggregate callback
handling), historyMs (read/projection), totalMs (worker entry through final save).
Durations overlap: do not sum nativeMs and toolMs. Failed turns omit unfinished
stages and still report totalMs. No prompt, tool arguments/output, token, URL or
provider error is logged. These are server worker timings, not browser/network/end
to end timings. PM should compare repeated equivalent tasks and page modes with
identical model/image, report p50/p95 and distinguish cold resume/mode switches;
this change alone does not establish a before/after speed improvement. A pre-change
baseline requires the same instrumentation on the older prompt or external timing.
