# #90 chat stream — development VM `anxious-dev` procedure

Issue: https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/90.

- **Source**: the approved SHA `27973daa1fd200f4d22c97a7307e837e75b4d310` (review approved, design r3, contract v1.1).
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

```sh
git -C "$SRC" fetch && git -C "$SRC" checkout --detach 27973daa1fd200f4d22c97a7307e837e75b4d310
test "$(git -C "$SRC" rev-parse HEAD)" = 27973daa1fd200f4d22c97a7307e837e75b4d310
docker build -f "$SRC/backend/Dockerfile" --target runtime --build-arg APP_GIT_SHA=27973daa1fd200f4d22c97a7307e837e75b4d310 \
  --build-arg APP_BUILT_AT="$BUILT_AT" -t anxious-s13-issue90-runtime:27973da "$SRC"
docker build -f "$SRC/backend/Dockerfile" --target runner --build-arg APP_GIT_SHA=27973daa1fd200f4d22c97a7307e837e75b4d310 \
  --build-arg APP_BUILT_AT="$BUILT_AT" -t anxious-s13-issue90-runner:27973da "$SRC"
docker save anxious-s13-issue90-runtime:27973da anxious-s13-issue90-runner:27973da -o /PRIVATE/issue90/images.tar
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
- The runner-controller image stays as it is: #90 does not change the controller (`runner_kube.py`, `runner_controller.py` unchanged since c50c41c).

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
  --user 0:0 -v "$PV_PATH:/control:ro" -v "$SRC/infra/hosting/m3/chat-stream:/m:ro" \
  --entrypoint python anxious-s13-issue90-runtime:27973da -I -B /m/journal_lengths.py /control
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

| # | Action | Pass |
|---|---|---|
| P4-1 | Stream open; stop the runner with the existing path (`DELETE /api/research/codex/runner`; runner-controller drains) | Drain receipt `clean: true`. Stream gets `runner{available:false}` and ends. API slot released (a new `/events` from the same user is not 429). Runner Pod gone before its 20 s grace runs out |
| P4-2 | Stream open; idle stop (`CODEX_USER_RUNNER_IDLE_SECONDS`, 1800 s unless PM lowers it for the test) | Same as P4-1 |
| P4-3 | Stream open with a reader that stopped reading (`SO_RCVBUF` small, no reads); `kubectl -n m2-hosting rollout restart deploy/api` | Old API Pod terminates without waiting for the reader (well inside the Pod grace). `Waiting for connections to close` does not hold. The new Pod serves |
| P4-4 | P4-1 again while the reader has stopped reading | As P4-1. The runner's own drain does not wait for the API connection |

Not covered here, because it needs event volume and therefore a turn: slow reader → relay 10 s stall → runner connection closed inside the runner's 20 s grace. That is S6 in P5.

## P5 — measurements with real Codex turns (only after the user's turn budget)

**Prerequisite P5-0 (code + policy, separate review)**: the runner Pod env must be able to carry `RUNNER_STREAM_TEXT=true` and `STREAM_TRACE=true`. `runner_kube.py` builds a fixed env, and the runner policy checks it. Until that change is reviewed and applied, text events cannot be turned on in the cluster. back will prepare it as its own commit when PM schedules P5.

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
