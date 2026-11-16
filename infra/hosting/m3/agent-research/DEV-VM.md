# #97 C1/C2 minimum candidate (review-owned execution)

This is a development VM procedure, not a production rollout. Back prepares the
code/images and runs pure Docker unit tests; review/PM execute the actual VM path.
Do not change #62 CNP/VAP, shell/network/filesystem grants, Pod security/command,
provider/model pin, or the broker's authentication policy to make this pass.

## Candidate artifacts

Use one exact full source SHA on `task/agent-research-api-97` (provided in the push
message), the same build timestamp, and linux/amd64. Build with Docker:

```sh
docker build -f backend/Dockerfile --target runtime \
  --build-arg APP_GIT_SHA="$candidate_sha" --build-arg APP_BUILT_AT="$built_at" \
  -t anxious-dev-runtime:issue97-candidate .
docker build -f backend/Dockerfile --target runner-research-gate \
  --build-arg APP_GIT_SHA="$candidate_sha" --build-arg APP_BUILT_AT="$built_at" \
  --build-arg RESEARCH_MCP_GATE_MODE=retain -t anxious-dev-runner:issue97-retain .
docker build -f backend/Dockerfile --target runner-research-gate \
  --build-arg APP_GIT_SHA="$candidate_sha" --build-arg APP_BUILT_AT="$built_at" \
  --build-arg RESEARCH_MCP_GATE_MODE=restart -t anxious-dev-runner:issue97-restart .
```

Also build normal `--target runner` from that SHA for C2. Record immutable IDs and
digests; use the existing import/STOPPED binding-image report/apply path to install
them. The benchmark variants share the identical runner base. They differ only in
the root-owned `/etc/codex/research-gate-mode` value. A benchmark-only final import
selects `GateNative`; the actual Pod command stays `runner.app:create_app`. Neither
the normal runner nor runtime stage contains that import/marker. Do not use a
benchmark image as the compatible recovery image or production runner.

## VM and authentication precheck

Use only the development guest `anxious-dev` (`ssh -p 22107 opc@127.0.0.1`); verify
the VM identity and `infra/k3s/PARITY.md` before writes. Do not use production
kubeconfig, credentials or backups. Check reviewer/PM VM occupancy before changes.
Back has not verified VM availability or current credentials.

Use the existing synthetic development owner
`00000000-0000-4000-8000-000000005805` if its reviewed binding/auth preparation is
still valid; verify rather than assuming its old stateId/account grants survived.
Use the reviewed broker access supply, a fresh approved synthetic browser session
and scope `{page:{kind:"general"}}`. Keep session/CSRF/provider inputs in private
files, not argv, shared artifacts or logs. The measurement factory refuses other
scope kinds. Apply 0008 in the explicit reviewed migration job after the normal
VM backup/writer-drain checks. Require research health and API image metadata to
match the candidate. Record `RUNNER_STREAM_TEXT=true` and the selected #99 default
holdback/trace settings; keep both modes identical. Preserve #95 retired-secret
flags at their current values. No authentication/budget/security bypass is part
of this procedure.

## C1: first text latency, 12 reserved real turns

Before the first message POST, review must have an explicit approved turn budget
and a durable, shared reservation ledger. Reserve before sending; failures and
unknown dispatches count. No automatic retry or paid replay. Suggested C1 cap is
12 (one cold + five warm per mode). Additional/failed samples need a new decision
if the remaining authorized budget cannot provide five valid warm samples.

Use identical conversation history lengths, prompt, scope, model, images/base,
flags and reader route. Use one original native thread per mode; each warm sample
must resume that mode's original thread, never start a new one. Prefer matched
fresh conversations restored from the same fixture state. Prompt every turn:

> Write about 500 words in plain prose on how ocean tides work. Do not use tools.

For each mode, record a cold turn separately, then five completed warm turns. Use
the existing authenticated `/sessions/{id}/messages` + `/events` reader and normal
polling after completion; refresh expectedVersion per completed turn and use new
request/mutation IDs. Reserve the turn before the POST. Record samples in order,
not just successful minima. Confirm the runner's request/thread/turn correlation.

Primary metric is **`runner_stream_summary.firstEventMs`** for that request. In the
current #99 code it measures from `Hub.begin` (inside `Native.run_turn`, before
`prepare_research` and all process reset/initialize/thread-resume work) until the
first emitted **`item.delta` text**. It excludes time before runner worker entry;
it is not the earlier SSE heartbeat, turn.started, user-message/snapshot event or
client connection time. Also report `firstDeltaMs`, `runner_turn_timing.threadMs`,
`dispatchMs`, `totalMs`, and the reader's send-to-first-text elapsed time separately.
Capture only allowlisted summary/timing records, not raw provider/stdout/answers.

The `retain` benchmark initializes the same real MCP child/catalogue once and
retains app-server/MCP for warm turns. **Every MCP call is refused** in that mode,
so its retained capability can never invoke the research API or borrow a later
turn. It is solely a text-latency control. Any attempted tool call invalidates
that text sample and is recorded without retry. The `restart` image uses the
candidate's normal per-request grant/restart path. No fake model/transport layer
provides C1 evidence. Unit tests use a fake Native + mock HTTP and are labeled so.

Save the per-request raw **numeric** samples plus image/source/flag metadata,
completion state and process-generation observation in
`agent-worktree-comm/artifacts/review/issue97/<sha>/c1/` (or a private VM evidence
directory relayed there without credentials). Publish both cold samples, all ten
warm samples, each median, and `median(restart)-median(retain)` in #97.
**Above +1000 ms: stop this lifecycle implementation and report evidence plus
alternatives to PM.** Five valid warm samples per mode and +1000 ms or below only
clear C1; they do not approve the whole change. If measurement cannot run, report
that limitation rather than a simulated pass.

## C2: actual pinned MCP, original resume, approval and streaming

After C1 passes, use the normal runner image and original native threads. Inspect
the actual MCP startup/catalogue with pinned native 0.160.1. In a real model turn,
ask Codex to use the installed `anxious-research` skill to save a cited material,
then a document, then a post. Require the internal API to append new versions,
leave publications and active human drafts unchanged, record Codex authorship,
and refuse owner writes from public mode and foreign IDs. Read the document/post
history and verify diff/restore through the ordinary owner APIs/UI.

Record native MCP item shapes during the turn and after ordinary thread/read,
platform GET and a later process restart. Confirm actual `mcpToolCall` names,
arguments/result/status and same original thread id on resume. Include an existing
pre-transition thread when one is available: any retained dynamic call must be
refused; it must not create a proposal or bypass the MCP turn boundary. Approval
behavior must not require widening the native shell profile or granting unknown
elicitation/server requests. This candidate still needs C2 evidence before B5's
persisted envelope/summary projection and front F2 contract can be finalized.

In a separate reserved turn, interrupt after incremental text starts and verify
normal interrupted completion, later resume, stream cleanup and no native replay.
Check #90 still has no stream-path database writes, delta merging remains active,
and #95's deferred switches remain deferred. The candidate's ordinary callbacks
and existing polling may write as before; do not count those as stream-path writes.

Suggested total plan: C1 12 turns + C2 workflow/resume/interrupt 3 = **15**; any
extra negative-mode actual model turns need a recorded extra reservation/approval.
Authentication/API-only negative tests need no model turn. This suggestion is
not a claim that a 15-turn budget is already approved. C2/B5/skill behavior may
fail and must stay unverified until actual evidence exists.

## Cleanup and recovery-image gate

PM decision (2026-11-16, #97): **keep 0008 and the candidate**, including when C1
exceeds +1000 ms. Before applying 0008, import both exact-SHA storage fallback
images below and record their containerd manifest digests. An archive on the
host alone does not satisfy that prerequisite. DB downgrade and restoring the
pre-migration database backup are not cleanup steps.

Normal cleanup target:

- API runtime and ordinary runner from source
  `8804a971aa908586b451baac3341029bbc003805`, or a later review-passed candidate
  explicitly recorded by PM. Keep the schema at 0008.
- Stop the synthetic runner through normal lifecycle APIs, prove Pod/drain release,
  and select the **ordinary runner** image through the reviewed STOPPED
  binding-image flow. `retain` and `restart` benchmark images are never cleanup
  targets. Do not restore the pre-0008 API image or its old runner image binding.
- Restore only temporary measurement flags that were changed, retaining the
  intended #90/#95 baseline. Revoke the temporary browser session and record
  exact API/runner source and manifest digests plus the final stopped state.
- If C1 fails, stop further model turns and lifecycle implementation, publish
  the evidence, and await PM's next design decision. Keeping the candidate on
  this isolated VM is not C1 acceptance, production approval, or permission to
  run additional turns.

Emergency fallback (only if the candidate API fails startup or health): runtime
**and ordinary runner** from storage-only source
`2e4da866bb12a1ad2bf2669917cff3e52899a185`. Use the separately supplied images and
immutable metadata/archive, not a benchmark image. Keep 0008 and all research and
native records. Change the API and the stopped runner binding through the reviewed
image flow; verify research health, existing owner reads and a new human save.
C1 threshold failure alone does not select this fallback. If this pair also fails
health, keep the synthetic runner stopped and report the failure to PM; do not
try an older API or downgrade/reset the database.

For B3, certify the fallback's actual built/imported digests and the above
0008-compatible read/save behavior on the development VM. Preserve signatures,
version provenance, failed/ambiguous turn reservations and native evidence. The
fallback source has passed storage probes but its VM recovery behavior is not
certified until review records the actual result. This decision applies to the
development VM; production recovery and rollout still need separate approval.
