# #90 chat stream — development VM `anxious-dev` procedure

Issue: https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/90.

- **Source**: `IMAGE_SHA`, the reviewed code commit given in the review/PM message. It is not `27973da`: the API evidence-log fix and P5-0 (default off) come after it. Design r3, contract v1.1.
- **Roles**: back writes this file; review checks it and its hash; PM runs the commands in its session.
- **On error**: a nonzero rc is reported, never re-run.
- **Turn budget**: phases P0–P4 use **no Codex turn**. P5 needs the user's turn budget first and is not run before that.

All commands run inside the guest unless a step says otherwise. Chain time-coupled commands in one ssh command (relay gaps).

## P0 — read-only inventory (before anything changes)

Record, and keep each as a rollback copy:
- **Deployment specs**: `kubectl -n m2-hosting get deploy api -o json`, the runner-controller Deployment, and the ConfigMaps the api Deployment reads. Export these privately; do not publish values.
- **Image references**: `sudo /usr/local/bin/k3s ctr -n k8s.io images ls` (rows for the current API/runtime and runner images, column DIGEST).
- **Runner state**: the runner registry rows (owner, phase, generation, revision, pod UID) and each published binding's `plan.image`. Read them the same read-only way the #61/#68 steps did (`ops.runner_binding_image report` needs `oldPlanSha256` from this).
- **No turn running**: no conversation in `running`. The same read-only check as the earlier runner stages. If one is running, stop here.
- **Flags**: the api Deployment env has no `RESEARCH_STREAM_ENABLE`, and the runner Pod env has no `RUNNER_STREAM_TEXT` (`runner_kube.py` sets neither). Both default to `false`.

## P1 — build the images in the guest (from the approved SHA)

Use the guest's Docker (containerd image store, see `infra/k3s/DEV-VM.md`), with the production build arguments. `FULL_SHA` is the approved SHA above. `BUILT_AT` is the UTC build time, recorded.

Two SHAs, never one checkout (review 70e92e7):
- **Image source**: `IMAGE_SHA` (the reviewed code). `test "${#IMAGE_SHA}" = 40` before use.
- **Tools and this file**: the reviewed procedure commit `TOOL_SHA`, given in the review/PM message (it adds `infra/hosting/m3/chat-stream/`, which older code commits do not have).

Each is extracted with `git archive` into its own directory. Nothing is checked out, so neither can disappear mid-run.

```sh
# Separate, fresh directories (refuse if they already exist).
mkdir /PRIVATE/issue90 /PRIVATE/issue90/build /PRIVATE/issue90/tools
git -C "$SRC" fetch
git -C "$SRC" archive "$IMAGE_SHA" | tar -x -C /PRIVATE/issue90/build
git -C "$SRC" show "$TOOL_SHA:infra/hosting/m3/chat-stream/journal_lengths.py" > /PRIVATE/issue90/tools/journal_lengths.py
git -C "$SRC" show "$TOOL_SHA:infra/hosting/m3/chat-stream/DEV-VM.md" > /PRIVATE/issue90/tools/DEV-VM.md
sha256sum /PRIVATE/issue90/tools/journal_lengths.py /PRIVATE/issue90/tools/DEV-VM.md   # compare with the reviewed hashes
docker build -f /PRIVATE/issue90/build/backend/Dockerfile --target runtime --build-arg APP_GIT_SHA="$IMAGE_SHA" \
  --build-arg APP_BUILT_AT="$BUILT_AT" -t "anxious-s13-issue90-runtime:$IMAGE_SHA" /PRIVATE/issue90/build
docker build -f /PRIVATE/issue90/build/backend/Dockerfile --target runner --build-arg APP_GIT_SHA="$IMAGE_SHA" \
  --build-arg APP_BUILT_AT="$BUILT_AT" -t "anxious-s13-issue90-runner:$IMAGE_SHA" /PRIVATE/issue90/build
docker save "anxious-s13-issue90-runtime:$IMAGE_SHA" "anxious-s13-issue90-runner:$IMAGE_SHA" -o /PRIVATE/issue90/images.tar
sha256sum /PRIVATE/issue90/images.tar
sudo /usr/local/bin/k3s ctr -n k8s.io images import /PRIVATE/issue90/images.tar
sudo /usr/local/bin/k3s ctr -n k8s.io images ls | grep issue90
```

- Then tag the repo@sha256 references from the DIGEST column, as in `infra/hosting/README.md`. Do not use `import --digests`.
- **The digests are produced here, on the guest.** back does not quote them in advance. Record runtime and runner `repo@sha256` from the `images ls` output (pins copied from output, never typed).

## P2 — deploy with both flags off

**API**: the #62 step A pattern (`infra/hosting/m3/user-runner-e2e/RUNNER-NETWORK.md`).
- Render `infra/hosting/render.sh` with `API_IMAGE` = the new runtime `repo@sha256` and every other input as recorded in P0.
- Server dry-run and diff, then apply **only Deployment `api`**. Do not set `RESEARCH_STREAM_ENABLE`.
- `kubectl -n m2-hosting rollout status deploy/api`.
- The runner-controller image stays as it is for P0–P4. With both stream flags unset, `IMAGE_SHA` renders a runner Pod exactly as the current controller does (P5-0 adds keys only for `true`; `runner_controller.py` is unchanged since c50c41c), and the new API accepts that Pod. The controller moves to the same runtime image only at P5, with the flags (P5-0 below).

**Runner**: the per-binding image swap (`infra/hosting/m3/multi-user-runners/CUTOVER.md` section 6, `rollout/README.md` "Existing binding images").
- For each owner in `idle_stopped` with a completed termination proof, with entry stopped and runner-controller scaled to 0: `ops.runner_binding_image report` → review → `apply`.
  - Inputs: `newImage` = the new runner `repo@sha256`, plus the P0 fence (`owner`, `state`, `generation`, `revision`, `oldPlanSha256`).
- Scale runner-controller back. The next entry renders the Pod from the new plan.

**Regression checks (flags off, no turn)**:
- `/api/health` and `/api/version` show the approved SHA.
- `/api/research/codex/status` keeps its earlier shape. Prepare one runner entry (the existing prepare path) and check it reaches `available: true` (an explicit signal: `/codex/status` reads `available:true`, not an empty log).
- `GET /api/research/sessions` and `GET /api/research/sessions/{id}` give the same items as before for an existing conversation.
- `GET …/events` answers 404 `stream_disabled`, and `POST …/interrupt` answers 404.
- Use the browser session from `infra/k3s/DEV-VM.md` (token file in the guest, `session_digest` computed in the API image).
- **Sending a message is a Codex turn**: it is in P5 (S0), not here.

**Rollback**: apply the saved api Deployment, and run `ops.runner_binding_image` back to each owner's P0 `plan.image` (the same report/apply, idle_stopped). Nothing in the DB schema changes in #90, so no data step.

## P3 — L measurement (read only, no turn)

L is the longest recorded secret literal of a runner's redaction journal. Streamed text waits L−1 chars (design r3 B1).

1. For an owner whose runner is in `idle_stopped` (no Pod), find the control PVC's PV path:
   - `kubectl -n codex-runner get pvc <control volume of the binding plan> -o jsonpath='{.spec.volumeName}'`
   - then `kubectl get pv <name> -o jsonpath='{.spec.local.path}{.spec.hostPath.path}'`
2. Run the reviewed script on that directory, read only, from the new runtime image:

```sh
docker run --rm --pull=never --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
  --user 0:0 -v "$PV_PATH:/control:ro" -v /PRIVATE/issue90/tools:/m:ro \
  --entrypoint python "anxious-s13-issue90-runtime:$IMAGE_SHA" -I -B /m/journal_lengths.py /control
echo "rc=$?"
```

- **Output**: one JSON line `{"ok", "literals", "L", "holdbackChars", "lengths": {length: count}}`. No literal, digest, filter or MAC.
- **Exit codes**: 2 = refused input (missing, symlink, non-regular file, hot `-journal`/`-wal`), 3 = database error. Report the rc, do not retry.
- **Read only**: lstat checks before any open, and `mode=ro&immutable=1`. Tests: `config_tests/test_journal_lengths.py` (unchanged files, refusals).
- If L is large (hundreds of chars or more), the prefix optimization is decided as a separate design inside #90 (PM decision 2026-11-14).

## P4 — runner and API shutdown with a real runner (no turn)

These need **`RESEARCH_STREAM_ENABLE=true` on the api Deployment** (a state change, PM decision). `RUNNER_STREAM_TEXT` stays off.
- An idle stream carries `runner`/`idle` frames and heartbeats only, so no Codex turn is needed.
- review writes the measuring client: it holds an `/events` stream open and records when each frame arrives and when the stream ends.

Client: review's `artifacts/review/issue90/p4_client.py` (sha256 `e185f7f9d4fd396eaa17ebad41649a776221903be632ef1a0bdf6b4d01c1c877`, usage in `P4-CLIENT.md`).

**Evidence, kept apart** (review 70e92e7):
- **Runner side**: the drain receipt, Pod deletion time, runner log `Application shutdown complete`.
- **Browser stream**: the frames the client saw, and when it ended.
- **API slot**: the `api_stream_summary` line the API writes when a stream's slot is released (one line per closed connection, `reason` included).

After the runner stops, the API relay is **expected to stay open**. It sends `runner{available:false}`, then waits in its not-ready loop (up to `NOT_READY_LIMIT_SECONDS`, 600 s) for a runner to come back. The browser stream ending is therefore **not** a pass condition. The client closes it explicitly once it has seen `available:false`.

| # | Action | Pass |
|---|---|---|
| P4-0 | Slot baseline: the same user opens 3 streams (the per-user cap); a 4th answers 429 `stream_limit`. Close one from the client, wait for its `api_stream_summary` line, then open again | 4th refused while 3 are open; one `api_stream_summary` per close; a new stream accepted after the line |
| P4-1 | Reading stream open; stop the runner with the existing path (`DELETE /api/research/codex/runner`; runner-controller drains) | Runner side: drain receipt `clean: true`, Pod gone within its 20 s grace from the stop. Browser: `runner{available:false}` arrives (the stream itself may stay open). Then the client closes; API: one `api_stream_summary` for it |
| P4-2 | As P4-1, by idle stop (`CODEX_USER_RUNNER_IDLE_SECONDS`, 1800 s unless PM lowers it for the test) | As P4-1 |
| P4-3 | Stream open with a reader that stopped reading (`SO_RCVBUF` small, no reads); `kubectl -n m2-hosting rollout restart deploy/api` | Old API Pod terminated well inside its grace (pod timestamps); API log has no lasting `Waiting for connections to close`; the new Pod serves `/api/health`. The client sees a local deadline or reset, not a clean SSE end; that is expected |
| P4-4 | P4-1 with a reader that stopped reading | Runner side only (the client cannot see frames): drain receipt `clean: true`, Pod gone within 20 s, runner log `Application shutdown complete`. After the client closes: one `api_stream_summary` |

Not covered here, because it needs event volume and therefore a turn: slow reader → relay 10 s stall → runner connection closed inside the runner's 20 s grace. That is S6 in P5.

## P5 — measurements with real Codex turns (only after the user's turn budget)

**Prerequisite P5-0 (code, separate review)**: the runner Pod env must be able to carry `RUNNER_STREAM_TEXT=true` and `STREAM_TRACE=true`.
- **What checks the Pod env**: `runner_kube.py` renders a fixed env, and its Pod verification (`KubePods._pod`) requires the live Pod spec to equal the render exactly. The runner admission draft (`user-runner-policy-draft`) is gated and not applied. An earlier version of this file said "the runner policy checks it"; the check is the code.
- **The change** (its own commit):
  - `CODEX_RUNNER_STREAM_TEXT` / `CODEX_RUNNER_STREAM_TRACE` (`true`/`false`, default `false`; anything else refuses) in the process env of runner-controller and API add exactly those keys with `"true"`.
  - By default the render is byte-identical to before.
  - `_pod` also accepts a Pod rendered under the other flag state (only the exact `"true"` keys may differ), so a flag change never strands a running Pod. Every other env difference is still refused.
- **Turning text on (P5, after review)**: move the runner-controller Deployment to the `IMAGE_SHA` runtime image (it renders the Pods), then set `CODEX_RUNNER_STREAM_TEXT=true` (and `CODEX_RUNNER_STREAM_TRACE=true` for S4) on **both** the runner-controller Deployment and the api Deployment, and wait for both rollouts.
  - A runner Pod created after that has the keys; one created before keeps working without them until its next entry.
  - Off again: set them back to `false` (or remove them) the same way. `RESEARCH_STREAM_ENABLE=false` on the API alone already closes every browser stream.

| # | Purpose | Turns | Notes |
|---|---|---|---|
| S0 | Regression: a normal send with both flags off (record, tool callback, refresh) | 1 | Can run right after P2 |
| S1 | V1 live item ids vs `thread/read` ids; first text time; `runner_stream_summary` incl. `legacyL` (= L from the live journal) | 1 | text on |
| S2 | V3 delta methods and the tool items code-mode actually emits (a turn that runs one shell command with output) | 1 | text on |
| S3 | V2 Stop mid-answer: partial answer in `thread/read` or display-only; `turnStatus: interrupted`; GET = resume record | 1 | text on |
| S4 | D7 per-hop delay (① API Pod→runner, ② API Service, ③ Traefik `web`, ④ host forward) on one long answer; `STREAM_TRACE` | 2 | text on; repeat once for spread |
| S5 | §8 large command output (output caps, event count/bytes) and reload mid-turn (snapshot → continue) | 1 | text on |
| S6 | Slow reader with a real runner: browser stops reading mid-answer → relay stall 10 s → runner connection closed → runner SIGTERM/drain completes inside 20 s grace | 1 | text on |
| | **Total** | **8** | PM's suggested cap was 10 |

- DB writes (S1, S4, S5): `pg_stat_force_next_flush()` before and after, `research.conversations` `n_tup_upd`/`n_tup_ins`, with no other activity on the VM.
- Refresh UPDATEs are counted from the API log `conversation_refresh_write`.
- Production TLS/h2 is not covered by any of this.
