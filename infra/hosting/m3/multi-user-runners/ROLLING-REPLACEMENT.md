# Rolling runner replacement and no-freeze releases (#101)

Design and decisions: issue #101, kick-off comment and the 2026-11-25 decisions (D1–D3).
Code: `backend/ops/runner_replacement.py` (one controller pass), `backend/app/runner_image_move.py`
(fingerprints, move preconditions), `backend/db/101_runner_rollout*.sql` (schema, the image-only
write path), `backend/ops/runner_rollout.py` (values → Deployments).

Production steps in this document run only after review approval and with the user's approval at
apply time (PM records it on the issue). Every command marked *read-only* changes nothing.

## 1. What the controller does

The target is the runner-controller's own env: `RUNNER_TARGET_IMAGE` (from values `runnerImage`)
plus the runner flags it renders into new Pods (`RUNNER_STREAM_TEXT`, `STREAM_TRACE`,
`STREAM_RETIRED`, `RUNNER_RESEARCH_API_ENABLE`). Image and flags together are the *fingerprint*.
Each 30 s pass (after the stop steps, before creation):

| Owner | What happens |
|---|---|
| No Pod, binding on another image | The binding moves to the target image at once (`runner_rollout.move_image`: image only, compare-and-swap, only with no Pod and no create permission; a stopped owner also needs the completed stop proof of its generation). Not during the canary, except the canary owner |
| Waiting for its first Pod of a generation (new owner, re-entry) | Moves just before creation, so its Pod starts on the target. During the canary only the canary owner moves; another owner starts on its current binding and is replaced once rolling (a new owner is prepared with `controller-inputs.json` `image`, the target, either way) |
| Pod on another fingerprint | `waiting_turn`: nothing is stopped while a turn runs. Once its runner reports idle for 60 s and the database holds no running conversation, a stop intent is recorded (`draining`) and the existing stop path runs: drain receipt, graceful UID DELETE, 404 proof, release. After release the binding moves and, while the owner still has a live login session, the owner re-enters at once (`restarting` → `verified`); otherwise its next start uses the new image (`moved`) |
| Turn still running after `turnDeadlineSeconds` (default 1800) — also when the runner reports idle but the database still holds a `running` conversation, and when the runner's idle evidence cannot be read (`idle_unknown` until then) | `cutting`: the stop intent and the `cutting` row commit together; the stop fences new turns and the running turn gets the runner's ordinary interrupt (the #90 Stop), resent every pass (also after a controller restart) until the drain receipt or 120 s. The conversation ends `interrupted` (API read first) or `failed`/`codex_runner_lost` (closed at the drain receipt, #114). If no drain receipt follows within 120 s: `blocked:turn_not_settled` (kept while that stop stays open, without a slot; operator R2 if needed) |
| A stop someone else opened (idle stop, logout, operator) | Followed to release as `draining` with reason `external_stop`; it holds no replacement slot |
| A new target (an image change or a rollback) while the previous target still has its own stop, cut or restart open | Nothing is copied to the new target. The old work finishes under the old target first: a cut keeps getting its interrupts within the settle window; after release the owner restarts on its unchanged binding, and that restart (also one the old target had already started) is held as `restarting` under the old target until the owner has its Pod again or stopped; after `2 × canarySeconds` it becomes `blocked:restart_timeout`, which frees the other owners only: that owner's binding stays protected (no move, also not before Pod creation; `rollout_previous` line `protected`) until its restart generation ends (Pod created, or stopped). Only then the old row closes as `failed:superseded`, and an owner whose Pod runs the old image becomes an ordinary replacement of the new target (an unsettled cut becomes `blocked:turn_not_settled` there, operator R2). Until then the new target chooses no canary, records no stop and moves no binding, also not just before a Pod is created (`rollout_previous` log lines; PM 11-25 allowance A). `rollout halt` and the halt watching of target Pods keep working. A rollback may thus start one turn plus one Pod start later |
| Observation of a target Pod | Tied to the observed Pod UID, in the controller's memory: a Pod whose health was never read healthy is never counted as converged, whatever its age; the canary streak counts only consecutive healthy reads of one Pod UID, and any other pass (Pod missing, lookup error, another UID, not healthy) ends it. A controller restart starts these counts again (PM 11-25 allowance B: when unsure, hold; completion may wait, and an account reason such as `auth_revoked` keeps the target `rolling`) |
| `runner_failed` owner, or a stopped owner without the completed stop proof of its generation (legacy) | `blocked:runner_failed` / `blocked:no_stop_proof`, never moved, never the canary |
| Any stop or move | Its transaction locks the target row (as `rollout halt`/`resume` do) and rechecks that the target is current and not halted and, during the canary, that this owner is the canary; otherwise nothing is recorded (`held`) |

At most `maxParallel` (default 1, max 3) of the rollout's own replacements are in
`cutting`/`draining`/`restarting` at once (followed `external_stop`s do not count).
The rollout never deletes a Pod, writes a receipt or releases; #61 R1/R2 and the #114 release path
are unchanged.

### Stages and halts

`canary` → `rolling` → `complete`; `halted`; `superseded` (a newer target replaced it).

- **canary:** one owner (the first of `canaryOwners` that needs work, else the first running owner,
  else the first stopped owner; never a blocked/failed owner, one without its stop proof, or one whose
  binding a superseded target's restart still protects — the next candidate is chosen instead, and a
  canary that becomes protected steps aside as `blocked:awaiting_previous`). A target has at most one
  active canary, enforced by the database (unique index `owners_one_active_canary`); a stepped-aside
  owner becomes the canary again only by being selected, never by a later observation. It
  passes only after `canarySeconds` (default 600) of **consecutive** healthy checks (`/health`
  available, no reason) of the **same** canary Pod (PM condition 1, 11-25 criterion). An account
  reason (`auth_*`, `not_configured`), an unreadable health, a Pod not Running or missing, a lookup
  error or another Pod UID breaks the streak (shown as `restarting:canary_unconfirmed`): neither pass
  nor halt; the next healthy check starts it again. The streak counts only from the pass in which the
  owner is this target's selected canary: healthy reads before that (for example while a previous
  target's work held this one back) never count (PM 11-25). It lives in the controller's memory, so a
  controller restart, or a pass that could not read its target or owners (logged as
  `"owner":null,"result":"rollout","reason":"stuck:..."`), also starts it again. `rollout resume` is the operator's only explicit pass. A
  target fingerprint that once reached `complete` (a rollback) starts in `rolling`.
- **complete** needs every owner settled: a target Pod still starting, with unreadable health, or
  never read healthy by this controller keeps the target `rolling` and watched (a pull failure still
  halts after 120 s).
- **Automatic halt** (reason in `rollout status`): any Pod already on the target fingerprint, in
  `canary` or `rolling`, with
  - `runner_terminated`: the runner container ended or the Pod `Failed`;
  - `image_pull`: `ErrImagePull`/`ImagePullBackOff`/`InvalidImageName` for 120 s;
  - `runner_unavailable`: `/health` unavailable for a reason that is not about the account or the
    provider (`auth_*`, `not_configured`, `not_enabled_for_account`, `budget_exhausted`,
    `provider_rate_limited`, and `unavailable` of a runner that is stopping, are inconclusive, not a halt),
    including a native that never started;
  - `ensure_failed`: three failed creations in a row for an owner bound to the target image.
- **Halted:** no new stop and no new move. Stops already recorded still finish; such owners
  restart on their unchanged binding and are recorded `failed:halted`.

### Operator command (same form as R2 reclaim)

```sh
kubectl -n runner-control exec deploy/runner-controller -- \
  python -B -m ops.runner_controller_service rollout status   # read-only
#   ... rollout halt     canary|rolling -> halted (reason `operator`)
#   ... rollout resume   halted|canary  -> rolling (the operator accepts the canary)
```

rc 0 done, 3 refused (JSON `code`: `no_target`, `state`, `state_changed`), 1 any other failure.
`status` prints the target id, state, reason, reason owner, fingerprint and owner counts by
`phase[:reason]`. Controller log lines: `{"stage":"controller","result":"rollout","reason":<outcome>}`
per owner that is not converged, `"result":"rollout_halted"` with the halt reason, and
`{"owner":null,"result":"rollout","reason":"stuck:..."}` when a pass could not run (it never
blocks stops or creation).

### Research grants during a flag change (decision D2)

A runner Pod without `RUNNER_RESEARCH_API_ENABLE=true` receives turns without their research
grant (the API checks the Pod it dispatches to); the API logs
`research_grant_omitted {"reason": "runner_flag_absent"}` (PM condition 2) and revokes the grant.
A count of those lines over a window is the number of such turns. Because the flag is part of the
fingerprint, such Pods are replaced like an image change.

## 2. One-time installation (administrator, before any controller with `rollout`)

1. *Read-only:* the administrator DSN is a superuser. `ops.runner_rollout_migrate` refuses
   otherwise (the new NOLOGIN writer role must not become a member of a non-superuser creator):

   ```sql
   SELECT current_user, rolsuper FROM pg_roles WHERE rolname = current_user;  -- expect rolsuper = t
   ```
2. `python -B -m ops.runner_rollout_migrate` with the administrator DSN (after the 010/016
   migrations; it validates the runtime, bindings and termination schemas first). Idempotent.
3. Install `backend/db/101_runner_rollout_grants.sql` through the reviewed administrator channel,
   after `016_runner_observer_grants.sql` and `061_runner_controller_grants.sql`. Idempotent `GRANT`s:
   the observer login gets the rollout tables, `EXECUTE` on `move_image` and
   `research.conversations SELECT(id, request_id)`; nobody gets UPDATE on `runner_bindings`.
4. *Read-only:* `ops.runner_rollout_migrate` again prints `PASS rollout schema`.

A runner-controller started with `RUNNER_TARGET_IMAGE` but without this schema refuses to start.

## 3. Values

- `research: {"enable": true|false}` — **required** from #101. Set it to the value production runs
  today (read-only check: the api Deployment's `RESEARCH_AGENT_API_ENABLE` and
  `CODEX_RUNNER_RESEARCH_API_ENABLE`), otherwise the first #101 render changes it.
- `rollout` — optional: `{}` or any of `maxParallel` (1–3), `turnDeadlineSeconds` (60–86400),
  `canarySeconds` (60–86400), `canaryOwners` (≤ 20 UUIDs). Present = the controller's target is
  `runnerImage`. The renderer refuses a render whose controller target differs from the host
  `controller-inputs.json` image new owners are prepared with.
- The render prints `{"research": …, "rollout": {…}}` before anyone applies it.

## 4. Turning rolling replacement on (first deployment)

| Step | Do | Check (read-only) |
|---|---|---|
| A | Deploy the #101 release with `research` set to today's value and **without** `rollout`, by the existing release procedure | api env research keys unchanged from before; controller log has no `rollout` lines; `rollout status` → rc 3 `no_target` (after B) |
| B | Section 2 (migration and grants) | `PASS rollout schema` |
| C | *Read-only:* every binding on one image, and that image equals values `runnerImage`: `SELECT plan->>'image', count(*) FROM runner_bindings.bindings GROUP BY 1;` | one row, equal to `runnerImage` and to `controller-inputs.json` `image` |
| D | Apply values with `rollout: {}` (and `canaryOwners` if wanted), same `runnerImage`. The first target then equals what runs: nothing is replaced, and the target completes, becoming the known-good fingerprint a later rollback returns to without a canary | `rollout status`: `canary`, then `rolling`/`complete` within one canary window; no `rollout_halted` |

If C shows several images, either move them first by the freeze procedure (section 6) or accept
that step D starts replacing every owner not on `runnerImage`.

**Pre-application check (read-only, node-local; run with the user's approval):** every owner's
`control/data/operator-account` equals the account the broker supplies (`BROKER_ACCOUNT_ID`).
Compare by hash; never print the values.

Why: in `access-service` mode the runner refuses a broker envelope whose account is not that file's
value (`ServiceAccessAuth.install`, `runner/service_access.py`). It answers `/health` `policy_refused`
and native does not start. The check is in the runner code itself, not in one image: it is present
in the issue97 runner source and in this release. So it applies to every owner and every image built
from that code.

Observed once on the development VM: five synthetic owners still held a provisioning placeholder in
that file. One of them (…5802) was started on the issue97 runner image after a rollout had moved its
binding and stayed `policy_refused`. The other four were not started. Code says they would refuse
the same way. As a canary such an owner halts the rollout (`runner_unavailable`).

## 5. Changing the runner image (or a runner flag) with rolling replacement on

1. Build and import the runner image; review its digest.
2. Values: new `runnerImage` (or `stream`/`research` flags). Render; check the printed rollout
   target and `controller-inputs.json` `image`.
3. Apply the runner-controller Deployment and install the host inputs by the existing procedure.
   The controller restarts (Recreate, seconds) and records the new target (`canary`).

   **If a `stream` or `research` value changed, apply the api Deployment from the same render too.**
   The renderer writes those flags on the api as well (`stream_env(v, 'api')`, `research_env(v, 'api')`
   in `ops/runner_rollout.py`):
   - the api renders and verifies runner Pods with them;
   - `RESEARCH_AGENT_API_ENABLE` switches the api's own research grants;
   - `RESEARCH_STREAM_ENABLE` switches its `/events`.

   Apply the api first (RollingUpdate), then the controller (section 8's order). *Read-only:* both
   Deployments' rendered keys equal the render (`kubectl get deploy … -o json`: `RESEARCH_*`,
   `CODEX_RUNNER_*`, `STREAM_TRACE`, `RUNNER_TARGET_IMAGE`).

   `stream.enable` alone is api-only: it changes `RESEARCH_STREAM_ENABLE` on the api and nothing in
   the runner fingerprint. So it creates no new target and replaces no Pod. Apply the api only.
4. Watch `rollout status` and the controller log through the canary window. Canary checklist:
   - the canary Pod's image is the new digest and its `/health` is available;
   - API log lines `conversation_refresh_write` (#90 polling fallback) and `research_grant_omitted`
     during the window — a rise means the stream or the grant path regressed: `rollout halt`.
     Compare `conversation_refresh_write` only for a canary turn with `stream.enable` on (with the
     stream off every read is a polling refresh);
   - no `rollout_halted` line;
   - **one real turn** (PM condition 1), sent during the window as the canary owner — preferably the
     test owner named in `canaryOwners`, logged in so its runner restarts at once (a stopped canary
     starts when that owner opens the runner). Check that the turn ends normally; with
     `stream.enable` on, that the chat stream delivers its first event (with it off, that the result
     arrives by polling, as before #90); and, with `research.enable` on, that a research tool call
     works (no `research_grant_omitted` line for it). If any of these fails: `rollout halt`.

   The canary passes on its own when `canarySeconds` ends. Choose a window long enough for the
   turn; if the check cannot finish in time, run `rollout halt` first and `rollout resume` once the
   turn passed. Halt only after the canary Pod already runs on the new image: a halt while the
   canary's own stop is still in flight restarts that owner on its old binding (`failed:halted`).
5. After `rolling`: owners move or are replaced one by one; `complete` when every owner is on the
   target, `blocked` owners listed by reason.
6. **Halt or rollback:** `rollout halt` stops new replacements.

   To roll back, return to the previous **fingerprint**, the runner image together with the rollout
   flags. Put back every value the change touched: `runnerImage`, and the `stream` / `research`
   flags if they changed too. A flag-only change is rolled back by its flags alone. Then apply as in
   step 3:
   - **the api Deployment too** whenever a `stream` or `research` value is put back (api first,
     then the controller);
   - check both Deployments' values against the render.

   For example, a rollback that turns `research` off but applies only the controller leaves the api
   issuing research grants.

   If that fingerprint has a target with `completed_at` in its history, the new target starts at
   `rolling` without a canary (`SqlStore.install`, `ops/runner_replacement.py`). Otherwise it starts
   at `canary`.

   `rollout resume` continues a halted target the operator judged sound.
7. **After `complete` (operator check, PM 11-25):** for a set period, for example the first working
   day, watch the owners that start for the first time on the new target:
   - their runner `/health`: available, with no reason such as `policy_refused`;
   - the controller log.

   If one is unhealthy because of the image or a flag, apply the rollback values (step 6, every
   value the change touched). A previous fingerprint that completed rolls back without a canary.

### Limits of rolling replacement (no automatic cover)

- **A stopped owner that moved is not checked on the new image until its next start.** The canary
  checks the `/health` of one owner's Pod on the target fingerprint. That need not be a new start:
  on the development VM (G3) an already running Pod of the same fingerprint passed it. Stopped owners
  move at once once `rolling` starts, and nothing starts them.
- **No automatic halt after `complete`.** The controller watches target Pods (terminated runner,
  image pull, unavailable `/health`) only while the target is `canary` or `rolling`. An owner that
  starts after `complete` and is unhealthy halts nothing. Step 7 is the manual cover; extending the
  watch is out of #101's scope.

## 6. Changing the runner image with rolling replacement off

Without `rollout` the controller has no target and moves nothing. The reviewed freeze procedure
(#93/#95/#99) stays the only way:

1. Entry off (`CODEX_USER_RUNNERS_ENABLE=false` on the api), runner-controller at 0, host
   `issue16-controller` timer stopped, every runner Pod gone.
2. **Values `runnerImage` = the new image, render, install the host inputs** — this is what keeps
   `controller-inputs.json` `image` (new owners) equal to the moved bindings; skipping it is the
   11-18 mismatch.
3. Move each binding with `ops.runner_binding_image` (report → review → apply), as before.
4. *Read-only:* `SELECT plan->>'image', count(*) FROM runner_bindings.bindings GROUP BY 1;` is one
   row equal to `controller-inputs.json` `image`.
5. Restart controller and timers, entry on.

## 7. Turning rolling replacement off while it runs

Applying values without `rollout` restarts the controller without a target:

- Nothing in the database changes: the current target keeps its state (it does **not** become
  `halted`) and the owner rows stay. `rollout status` still reads them.
- Pods keep running on whatever image they have. No new stop or move is made.
- Stops already recorded still finish through the normal stop path. After release there is no
  binding move and no automatic re-entry; an owner's own queued entry still re-enters, on its
  unchanged binding.
- New owners are prepared with `controller-inputs.json` `image` (= `runnerImage`).
- Differences from `rollout halt`: halt keeps the controller watching and re-entering in-flight
  owners after their release; removing the key does neither. To leave the database saying it was
  stopped on purpose, run `rollout halt` first, then remove the key.
- Turning it back on with the same image and flags continues the same target where it stopped; a
  different fingerprint supersedes it, and its own open stops finish first (section 1).
- From then on, image changes follow section 6 again.

## 8. Replacing api, runner-controller and web without a freeze (request 6)

Running runners keep their image; only new Pods use a new render. Every release must keep:

- **C1 Pod manifest:** api and controller N accept a Pod rendered by N−1 (as #62 legacy callback
  and the #90 flag subsets do in `KubePods._pod`). The N−1 variant may be removed only in a later
  release, and only when all of these hold (read-only): `rollout status` is `complete` with no
  `blocked`/`failed` owner, and no live runner Pod still carries the N−1 render — list every runner
  Pod's image and each rollout flag with its value, and compare them with the target fingerprint (a flag
  counts only with the exact value `true`; `-` means absent):

  ```sh
  kubectl -n codex-runner get pods -l anxious.io/managed=user-runner -o json | python3 -I -c '
  import json, sys
  FLAGS = ("RUNNER_STREAM_TEXT", "STREAM_TRACE", "STREAM_RETIRED", "RUNNER_RESEARCH_API_ENABLE")
  for pod in json.load(sys.stdin)["items"]:
      runner, = [c for c in pod["spec"]["containers"] if c["name"] == "runner"]
      env = {e["name"]: e.get("value") for e in runner.get("env", [])}
      print(pod["metadata"]["name"], runner["image"], " ".join(k + "=" + str(env.get(k, "-")) for k in FLAGS))'
  ```

  A blocked owner's Pod keeps its old render until it is resolved.
- **C2 runner HTTP:** api N talks to runner images N−1 and N; health fields and receipt versions
  are only added; an endpoint the api uses is not removed while an older image still runs.
- **C3 database:** expand-only migrations in a no-freeze release.

Order: expand migration (if any) → api (RollingUpdate) → web → runner-controller (Recreate; while
it restarts only creation, stops and the rollout wait) → runner target (section 5) → `complete`
→ the next release may contract. A release that changes the Pod render applies the controller only
after the api rollout finished, so no old api Pod meets a new Pod.

**Research migration window (PM condition 4).** `/readyz` requires exactly the research alembic
revision the api image expects (`app/main.py` `ready()`). A release with a research migration:
apply the new api (its Pods stay unready) → run the migration → the new Pods become ready while the
old ones turn unready. Between the migration and the first new Pod's readiness the api answers
`503 not_ready` for up to one readiness period plus start-up; runners and entry are unaffected.
Read the api Deployment's `replicas` and `strategy` (read-only) before planning this window:

```sh
kubectl -n m2-hosting get deploy api -o jsonpath='{.spec.replicas} {.spec.strategy}'
```

## 9. Development VM validation (state changes by PM after review approval)

Read-only checks by back. Record for each: target state transitions, owner phases, controller log
lines, Pod UIDs/images, conversation end states.

1. A stopped owner moves at once (rolling); its next start runs the new image.
2. A running owner is replaced between turns; it restarts while logged in; its conversation is
   intact.
3. A turn longer than the deadline (set `turnDeadlineSeconds` low) is interrupted; the conversation
   ends `interrupted` or `failed`/`codex_runner_lost`; the owner restarts on the new image. Also the
   case of a runner that reports idle while the database still holds a `running` conversation: it
   waits until the deadline, then `cutting`, and the drain receipt closes the row.
4. A broken image (unpullable digest or a runner that exits) as canary: `halted` with the reason;
   no other owner moved; rollback by values. Also: a non-canary existing owner that re-enters during
   the canary starts on its old binding; record every owner's binding image before and after the
   halt (no move after it).
5. Flag-only change (`stream.text` or `research.enable`): Pods are replaced without a binding move;
   `research_grant_omitted` lines only before the replacement.
6. A new owner is prepared and starts on the target image.
7. api rolling update with active runners and an open chat stream (SSE). Record the user-visible
   effect (PM condition 3): whether the stream dropped, when it reconnected, whether the page fell
   back to polling (`conversation_refresh_write` count), any error shown, and whether the running
   turn's result arrived.
8. Controller restart in the middle of a replacement: the rollout continues from the database.
   Include a replacement of the controller right after a deadline stop committed: the owner's row
   is already `cutting`, and the new controller resends the interrupt within 120 s.
