# #61 dev-VM gate scripts (development VM only)

These scripts run the #61 in-cluster runner-controller gate on the development VM (`infra/k3s/DEV-VM.md`). They
follow the #60 window pattern:

1. back drafts a stage script and this mock harness.
2. review mocks the failure paths and clears the SHA-256 of each installed file.
3. back installs the files under `/root/issue61/`: root 0700, no clobber.
4. PM gives the user a one-line command, and the user runs it once.
5. back runs a read-only postcheck, and review confirms it independently.

Never run these scripts on the production node.

The images are built on the host from `5e73bc4` (`--platform linux/amd64`) and moved as archives to
`~opc/issue61-images/`. The guest import digest is the reference. No source is transferred.

| Stage | File | Who | Changes |
| --- | --- | --- | --- |
| W0 | `w0-baseline.sh` | back, after clearance | Nothing. Read-only baseline, including the …5804 ledger probe (`mode=ro`, one read transaction, fixed fields only) |
| W1 | `w1-import.sh` | user | Imports the three archives into k3s containerd and loads the runtime image into guest Docker for the renderer. Prints the image chain |
| W1c | `w1c-finalize-ns.py` | user, after the W2 rollback | Finalizes only the empty, Terminating `runner-control` with the development UID `54a486c3-…` (UID and resourceVersion precondition, full content enumeration, absence postcheck). Never for another environment |
| W1b | `w1b-tag.sh` | user, after a W2 rollback | Tags the three `NAME@sha256:DIGEST` references (infra/hosting/README.md) and checks each resolves to its host ID; refuses while the api points at the new digest |
| W2 | `w2-apply.py apply` | user | 061 grants, Namespace, DB Secret, RBAC/pool objects, CNP, api image, controller Deployment |
| W2 rollback | `w2-apply.py rollback <evidence dir>` | user | Restores every object W2 changed from its backup. Deletes only objects W2 created, by UID; the namespace goes last and only if it is empty |
| W2 verify | `w2-verify.py <evidence dir>` | back, after clearance | Nothing. Read-only certification of a finished W2 apply: clean evidence, the eight created UIDs live, backup UIDs, api and controller rolled out, the can-i set, controller outcome lines |
| W3 | `w3-reclaim.py --expected-running N` | user | R2 reclaim of …5804. Refuses unless N equals the running count now; bounded read-only release check |
| W3b | `w3b-binding.py` (+ `w3b-expected.json`) | user | Moves the bindings of …0048 and …5804 to the #61 runner/executor digests with `ops.runner_binding_image`, inside a proved write window (see below) |
| W4 entry | `w4-enter.py` | user | One 1-hour session for …5804, one runner POST, the controller-created Pod proved on the #61 images; session revoked |
| W4 observe | `w4-observe.py <evidence dir> [--wait]` | back | Nothing. Read-only R1 verdict until the deadline (Pod start + 60 min) |
| W5 | `w5-rotation.py [--preflight]` | user | Nothing (read-only): controller SA token rotation |
| W6 | `w6-enter.py`, `w6-reboot.py`, `w6-observe.py` | user (4 commands; steps 1 and 2 relayed together) | Two runner entries (gen 4→5, 5→6) and one guest reboot; see the W6 section |
| W7 | (next draft) | back | Nothing: final read-only postcheck |

## Files installed next to the scripts

- `lib.sh`: shared constants and read helpers (part of the W1 clearance; unchanged since).
- `w0-lib.sh`: W0-only helpers (`check_imports`), kept apart from `lib.sh` so W1's cleared set stays the same.
- `archives.sha256`: the archive manifest.
- `40-runner-network.json`, from `infra/hosting/m3/user-runner-e2e/`.
- `061_runner_controller_grants.sql`, from `backend/db/`.
- `worker.py` and `storage-root.sh`, from `rollout/`. They are renderer templates only and are never installed as workers.

All of these are copied from the same commit with `git show <sha>:<path>`.

`w2-apply.py` has three placeholders, `@@RUNTIME_REF@@`, `@@RUNNER_REF@@` and `@@EXECUTOR_REF@@`. Fill them with
`docker.io/library/anxious-dev-<name>@sha256:<digest>` from W1's chain output. Review clears the filled file.

## rc legend

| rc | Meaning |
| --- | --- |
| 0 | Done and verified |
| 2 | Placeholders left |
| 3 | Precheck, render or backup refused; nothing changed |
| 4 | W0 and `w2-verify.py` only: a condition differs from the expected state (nothing changed) |
| 5 | A change failed. The output lists what this run changed and the rollback command |
| 8 | Changed, but a postcheck failed |

## W2 run `w2-20261102T151325Z`: rc 8 was a false alarm in the check

The installed `w2-apply.py` for this run (SHA-256 `f0038fa7…`) ended with
`can-i controller execs pods: yes (want no)` and rc 8. That postcheck asked `kubectl auth can-i create pods/exec`.
kubectl reads `TYPE/NAME`, so this asked "may it create the Pod named exec?". The controller may create Pods, so
the answer was yes. Asked as a subresource (`--subresource=exec`, and also attach, portforward and
ephemeralcontainers), every answer is no, and no binding grants exec. Everything else W2 applied matched, and its
`changes.json` is clean.

- The fixed postcheck is in the branch's `w2-apply.py` (`CHECKS`, asked with `--subresource`).
- The mock now models the `TYPE/NAME` meaning, so the old form fails the harness.
- That run is certified by `w2-verify.py` on its evidence directory, not by its own rc. Read its rc 8 together
  with the verifier's output.
- The installed `f0038fa7…` stays as that run's script. Its apply path is unchanged, and so is its rollback,
  which any rollback of this run uses. Only its postcheck question was wrong.

## W3b: binding image move

`w3b-expected.json` is the review-approved offline report (SHA-256 `cc1049a7…`), byte for byte: per owner the tool
inputs and the exact report `ops.runner_binding_image report` must print. The driver refuses any other file.

- **Tool and image.** The tool runs in guest Docker from the W1 runtime image, by its verified Id (the same check as
  W2's renderer). Before the window, a network-none container checks the two module hashes
  (`ops/runner_binding_image.py`, `app/runner_bindings.py`, identical at `5e73bc4` and HEAD). The target digest
  references must resolve in k3s to the W1 image IDs.
- **Write window, proved.** runner-controller and then api are scaled to 0 by UID-preconditioned patch, and
  their Pods must be gone. Then `pg_stat_activity` must show no client session on database `hosting` except the
  check's own. Host units and guest containers stay stopped, no runner Pod exists, and every fence and plan
  digest is unchanged. Before each apply the session check runs again.
- **Admin DSN.** It comes from Secret `m2-hosting/hosting-ops` (`database-url`), with the host replaced by the
  postgres-0 Pod IP, as in #60. It is written `O_EXCL` root 0600 in the run's private directory (0700) only after
  the window is proved. It is never printed and is removed on every exit path.
- **Reports, then applies.** The tool's `report` runs for both owners before any write, and each report must
  equal the approved one. Then `apply` runs for …0048 and …5804 in separate tool transactions. The second owner
  runs only if the first is confirmed by the tool and its plan digest is the approved new one. A partial result
  is reported as such (rc 7) and never retried.
- **Restore.** It runs once a Deployment was touched, and only after every tool container is proved gone:
  replicas back by UID, rollout, readiness with the same UID, `/api/health`, admin DSN absent. If a container
  cannot be proved stopped, the Deployments stay at 0 (rc 80).

| rc | W3b meaning |
| --- | --- |
| 0 | Both bindings moved and confirmed, fences unchanged, restored |
| 3 | Precheck refused; nothing changed |
| 4 | Window not proved or admin DSN not written; no tool ran; restored |
| 5 | A guest report was refused or differs from the approved one; no apply; restored |
| 6 | Apply phase began, no binding changed; restored |
| 7 | Partial: …0048 moved, …5804 still old; restored |
| 8 | A plan digest is neither old nor approved new (or unreadable), or a fence changed |
| 9 | Both moved, but an apply was not confirmed by the tool |
| 70 | rc 0 otherwise, but a restore item failed |
| 80 | A tool container was not proved stopped. Deployments left at 0 if they were scaled; the output says "untouched" when the precheck module-hash container is the stuck one |
| 130 | Interrupted before any apply; restored |

## W4: R1 on …5804 under the new image

`w4-enter.py` runs once. It refuses unless …5804 is exactly at the W3b fence, no runner Pod and no open stop exist, nothing is pending or waiting, no live session of …5804 exists, and the API origin is `http://192.88.99.1` with no base path.

- **Session:** it creates one `auth.sessions` row for …5804 (1 hour) through psql. The session token and CSRF token live only in the process's memory: no file, no argument, no output.
- **POST:** it makes one `POST /api/research/codex/runner` through the gateway and never repeats it. Right after the POST it revokes that exact row.
- **Pod:** it waits at most 300 s for the controller-created Pod. Then it proves the instance and images:
  - spec images and status `imageID` equal the approved manifest references;
  - each `containerID` resolves through CRI to this Pod (container labels and sandbox). On this runtime `crictl inspect -o json <id>` gives `status.imageRef` = the manifest reference, and `crictl ps -o json --id <id>` lists exactly that container with `imageRef` = the config ID in the same sandbox. crictl options must come before the ID;
  - the POST carries `Origin: http://192.88.99.1`, which the route requires before the CSRF check;
  - `inspecti(imageID)` gives the same config ID;
  - the init container exited 0;
  - `automountServiceAccountToken` is false, and the only token is the `codex-broker`-audience projected token at `runner:/run/codex-broker`.
- **Context:** it then records an early `/health` of this exact runner, read inside the runner-controller Pod with the controller's own client and printed sanitized, plus the unchanged W0 ledger probe.

`w4-observe.py` is read-only. Entry evidence certifies only with entry result 0 (or 70), `proof.ok`, a 200/202 POST with its Idempotency-Key, and a deadline equal to Pod start + 60 min. Otherwise the observation is diagnostic only (rc 6). It declares R1 done only on all of the following:
- a completed generation-4 stop of this owner, state and Pod, completed at or before the deadline;
- a version 3 receipt whose owner, state, generation, Pod and stopId are that stop's, with `unavailable.reason == "auth_revoked"`;
- a `pod-deleted` proof for this Pod, verified at or before the deadline;
- no open stop, and the registry idle_stopped gen 4 without a Pod;
- no Pod by name at all (a replacement is named as such);
- unchanged conversations.

A late completion is printed and kept as evidence, but it is not R1 done.

Any other receipt is "R1 not done". No stop by the deadline is "R1 not done at the deadline", and nothing is extended, retried or reclaimed.

| rc | `w4-enter.py` | `w4-observe.py` |
| --- | --- | --- |
| 0 | Entered, Pod proved, session revoked (`r1_state` true or unobserved) | R1 done |
| 3 | Precheck refused; nothing changed | Evidence or a lookup unreadable |
| 4 | Session not created; no POST | R1 not done (another receipt or release) |
| 5 | POST answered an error; session revoked | Pending, or not done at the deadline |
| 6 | POST outcome unknown; never repeated | |
| 7 | Pod not proved Running within 300 s; no retry or reclaim | |
| 8 | Instance, image or token proof failed; Pod left as is | |
| 9 | Early health is not the R1 state; runner left running | |
| 6 (observe) | | Entry evidence does not certify: diagnostic only |
| 70 | rc 0 otherwise, but the session revoke was not proved | |
| 130 | Interrupted before the POST | |

## W4 follow-up: `w4-broker-events.py` (read-only, optional diagnosis)

**Context.** The W4 run `w4-20261104T014222Z` ended with entry rc 9 and observer rc 6. After the early health read, the runner reached `failure=auth_revoked`. The idle stop at 02:12:32Z then:
1. wrote a v3 receipt with reason `auth_revoked`;
2. deleted the Pod by UID;
3. released it.

The cause of `auth_revoked` is not established. There are four candidates:
- ledger rejection;
- `login_access`;
- `account/read`;
- the startup 401 refresh.

This one-shot narrows those candidates. It does not decide them.

**What it reads.** Run `sudo -n python3 -I /root/issue61/w4-broker-events.py`. It reads exactly `/data/broker-events.jsonl` in container `broker` of `codex-broker/codex-broker-0`, and only if the Pod has the fixed UID `1a92b7f7…`.
- The read is one `kubectl exec … python -I -c <fixed reader>`.
- The reader opens that path with `O_NOFOLLOW` and caps the read at 4 MiB.
- The Pod and container identity is checked before and after the read.

**What it outputs.**
- The raw bytes stay in the process on the guest.
- Printed entries must have exactly the keys `at, event, kind, ok, before, after`, with valid types and values. `before` and `after` must each be `<inode>:<mtime>` or `-`.
- Rejected lines are counted by class and never echoed.
- The output gives:
  - the sha256 of the raw file;
  - the entries from 01:12:22Z to 02:42:22Z (at most 100 lines);
  - the last entry before that window;
  - the sha256 of the printed lines;
  - the interpretation limits.
- Nothing is written, and no broker endpoint is called.

| rc | meaning |
| --- | --- |
| 0 | printed |
| 3 | broker Pod identity or state not as fixed (no exec) |
| 4 | file absent, a symlink, not a regular file, or too large |
| 5 | kubectl failed or timed out, or the Pod or container changed during the read |
| 130 | interrupted |

**Harness.** `test_w4b.py`, with `mock/fake_w4b.py`. The fake's `exec` really runs the fixed reader against `/data`.

### Note for the prod rehearsal: the W4 early health read should be a bounded wait

W4 read health once, at T+9 s. That single sample raced the first broker supply. A later entry should instead poll `/health` every 5 s, for at most 300 s from the moment the Pod is proved, until health leaves `not_configured`:
- `r1_state` is true only when `failure` is exactly the stimulus failure;
- any other concrete failure → rc 9, with that failure named;
- still `not_configured` after 300 s → rc 10 ("never supplied within 300 s; runner left running");
- in every case: no retry, no reclaim.

The stimulus must be stated, and proved available, before the POST. On the dev VM it could not be proved read-only, because that needs the served token's fingerprint. #61 W4 was closed by an explicit user exception, so no re-entry script exists.

## W5: controller SA token rotation (read-only)

- `sudo -n python3 -I /root/issue61/w5-rotation.py`: the window, at most 75 min.
- `sudo -n python3 -I /root/issue61/w5-rotation.py --preflight`: one validated claims read, bound by the Pod identity before and after. It writes nothing.

1. **Precheck:**
   - No earlier `w5-*` evidence exists.
   - The host `issue16-*` units are inactive.
   - Two known host token paths under `/etc/anxious-lookout/issue16/` are checked with `lstat` only:
     - each must be absent, or a **regular file** with mtime older than 24 h;
     - a symlink or any other file type is refused.

     These tokens were issued with `--duration=24h` (`rollout/README.md`), but mtime is not the JWT's own expiry.
   - The `runner-controller` Deployment and Pod are at the recorded UIDs and 1/1. The Pod has only the default `kube-api-access` token (3607 s), mounted read-only, and no hostPath.
2. **Claims:**
   - A fixed reader in the controller container decodes only the JWT payload. The guest validates every value before printing or recording it: exact key set, int timestamps, UUID `jti`, the controller's `sub`/namespace/SA/Pod UID, `aud`/node shapes and the `..data` pattern.
   - Unexpected keys are counted, never named.
   - The reader runs every 60 s until the `jti` changes.
   - The window is 75 min on a monotonic clock, and call timeouts are bounded by the time left. A rotation first seen after the window is not accepted, and no proof starts.
3. **Proof** (after-window grace: it may finish after the window, but only starts for a rotation seen inside it):
   - A fresh process runs the controller's code: `build()`, `observer_pool.bound(…5804)` (PVC GETs), `runtime.namespace()`, `service.pod()`, `NodeEvidence.identity()`.
   - Each request re-reads the token file. The `jti` is read before and after, and both must equal the rotated `jti`.
   - The output is checked against a strict success or failure schema before anything of it is shown:
     - success requires namespace ok, a Pod UID or null, and a node name;
     - failure requires a known step and an exception class name.
4. **Resident identity:** same container ID and restart count, and no `reconciliation_required` since the first read.

**What success means:** the controller's code succeeds with the rotated `jti`, and the resident process stayed the same.

**What it does not show:**
- The resident watch re-establishing after the rotation: `Wake.watch` swallows errors and logs nothing. This is an explicit unproved limit, also written to `run.json`.
- Refusal of the old token: k3s extends projected tokens, so the old one stays valid until the Pod goes.
- Absence of host tokens beyond the two known paths.

| rc | meaning |
| --- | --- |
| 0 | rotated and proved |
| 3 | precheck refused |
| 4 | a proof call failed or its output was not as expected, or the resident controller logged `reconciliation_required` |
| 5 | not rotated within the window, or first seen after it |
| 6 | rotated again around the proof (no retry) |
| 7 | claims unreadable or malformed |
| 8 | controller Pod or container changed |
| 9 | everything passed, but `run.json` could not be written (stdout is then the only record) |
| 70 | unexpected error |
| 130 | interrupted (SIGINT, SIGTERM or SIGHUP; `run.json` is still written) |

**Evidence:** `/root/issue61/evidence/w5-<stamp>/run.json`, with validated claims and outcomes only.

## W6: guest reboot with a runner running, recovery with no manual input, re-entry

**Gate (all of G1–G4, tied together by IDs).**

| | what must be shown | tied by |
|---|---|---|
| G1 before the reboot | …5804 runner gen 5 on state `5ff583e9…` proved Running on the approved images (identity, image, CRI, token mount, the recorded PVC/PV pair, host loop mounts). Registry `ready` or `runner_unreachable` (ambiguous only with the latter), 0 active. No gen-5 stop, no open stop anywhere, no entry/admission/outbox, 0 running conversations. No login/reclaim process. Restore preconditions hold. The #64 paid window is closed: `shell-full`/`shell-pilot` may still exist as empty namespace objects and must hold no Pod | Pod UID, container IDs, Node UID, boot ID **B0**, controller identity |
| G2 the reboot | Same Node UID; **B1 ≠ B0** on both `/proc` and the Node API; the `issue16-restore` PASS line in **this** boot's journal (`_BOOT_ID` = B1); restore and storage-root ran this boot with Result=success; Node Ready; every state's loop mount linked to its backing inode | B0 → B1 |
| G3 recovery, no manual input | One new gen-5 stop requested after the reboot intent; **version 2 terminal receipt** equal to the stop row (owner, state, gen 5, Pod UID, stopId); completed `pod-deleted` proof on the recorded Node and **B1**; registry `idle_stopped` gen 5, no Pod; no open stop; conversations unchanged; workloads back | gen-5 Pod UID → stop → receipt → proof(B1) |
| G4 re-entry | One POST gives gen 6 on the same state, proved like G1, with the same PVC/PV UIDs, paths and capacities and the post-reboot loop mounts linked to the same backing files. Health polled 5 s × 300 s with the exact owner/state/Pod on every read | gen 5 → 6, record hashes |

**Not shown by W6:** resident watch reconnection after a token rotation (the post-reboot controller container is new);
provider supply success (`auth_revoked` is the environment's expected failure, cause still a blind spot); production
restore, backup or reboot; host reboot. "No manual input" is stated within the observed scope only: the v2 receipt,
the controller's outcome lines, the host units inactive/failed, and this boot's sudo/sshd journal. Absence from those
logs is not proof of absence; there is no API audit log, so a DELETE by another privileged actor cannot be fully
excluded.

**Operating boundary (PM, recorded on #61).** The no-login / no-paid-run / no-reclaim rule rests on window ownership:
the #64 paid window is closed and recovered, the dev VM is held by the #61 window, and only the user runs state
changes, on PM relay. The scans in `w6-reboot.py` check that the snapshot matches that commitment; they are not the
guarantee. CUTOVER.md is the production procedure: the dev VM has no backup procedure and W6 adds none. The reboot
exercises the issue16 bounded restore (`issue16-restore.service` → `worker.py restore` from the pinned provisioner
image, tag `50ac30b` by naming convention), which passed at the 2026-10-26 boot with the same inventory. The legacy
same-boot host recovery is not in use on the dev VM. W6 adopts the CUTOVER "before any reboot" evidence rule as its
evidence standard only.

**Steps.** Each one is a separate user command relayed by PM. On any nonzero rc: no rerun, back checks read-only,
PM decides.

| step | script | user go | what changes |
|---|---|---|---|
| 1 | `w6-enter.py` (enter1) | relayed together with step 2 (user decision: the KVM guest may be handled freely) | one 1-hour session row (revoked), one runner POST: gen 4 → 5 |
| 2 | `w6-reboot.py` | run only after step 1 returned rc 0, before its printed bound | evidence files, then one `systemctl reboot` |
| 3 | `w6-observe.py` | after ssh answers again | nothing (read-only, at most 30 min) |
| 4 | `w6-enter.py` (enter2) | step 4 go | one session row (revoked), one runner POST: gen 5 → 6 |
| W7 | (read-only, separate draft) | back | nothing |

**Risk text for step 2 (PM relays this verbatim with steps 1 and 2):**

> Step 2 reboots the dev VM guest once. If the issue16 restore fails at boot, k3s does not start: there is no bypass,
> and recovery is an operator reconciliation outside W6. If ssh does not come back, recovery is host-side, by the
> user. The script refuses (no reboot) if its checks or evidence saving fail. `reboot requested` and an ssh
> `remote rc=255` right after it mean the request was made, not that the reboot succeeded; step 3 proves the new
> boot.

**Timing.** Step 2 must **start within 20 minutes of step 1's POST**: the bound is min(POST, Pod start) + 1200 s, and
step 1 prints it as `NEXT … must START before <time>`. After that the script refuses, because the idle drain begins at
1800 s. Step 1 takes about 2–6 min, including up to 5 min of health polling. In step 2, the k3s journal export (about
240 MB at the last read) takes up to a few minutes. The reboot takes about 3 min. Step 3 waits at most 30 min. Step 4
takes about 2–6 min.

**rc.**

| script | rc |
|---|---|
| `w6-enter.py` | 0 entered, health usable or `auth_revoked` · 3 precheck refused · 4 session not created · 5 POST error · 6 POST outcome unknown · 7 Pod not proved within 300 s · 8 instance/image/volume/identity proof failed · 9 another concrete failure · 10 still `not_configured` after 300 s · 11 health not observed inside the window (probe failures, or a read that returned after it) · 12 conversation snapshot changed during the entry · 70 session revoke not proved · 72 proved, but the final record not written · 130 interrupted before the POST |
| `w6-reboot.py` | 0 reboot requested · 3 precheck refused · 4 evidence not saved, **no reboot** · 5 final look changed, **no reboot** · 7 `systemctl reboot` failed (not repeated) · 8 reboot requested, but the call result or the final record not written (not repeated) · 130 interrupted before the final look, **no reboot** |
| `w6-observe.py` | 0 G2+G3 proved · 3 precheck refused (nothing written; also when the boot is still B0) · 5 not reached in 30 min · 6 definitive mismatch (v1/v3/v4 or malformed receipt, another Pod UID, stop before the intent, proof on B0 or another Node, more than one new stop, replacement Pod, registry past gen 5) · 9 proved but evidence not saved · 130 interrupted |

**One step at a time.** Every W6 script takes the root-only `/root/issue61/w6.lock` (flock, non-blocking) before it
reads the evidence directory, and holds it to its final record. A second W6 process gets rc 3 before any session,
POST, evidence, intent or reboot call. The evidence directory, created under the lock, is the step's permit and is
never reused, whatever the outcome.

**Round 2 semantics (review round 1).**
- **Conversations.** enter fails with rc 12 if the conversation snapshot after the entry differs from the one
  before it. The reboot refuses unless the current snapshot equals both enter1 snapshots, before and after.
  enter2 refuses unless it equals the snapshot the chain recorded.
- **Final look.** The reboot's final look, after the evidence export, repeats the login/reclaim process scan and
  the Pod/namespace overview (#64 paid namespaces, unknown namespaces, other runner Pods).
- **Pod lookups in observe are three-way:** ok, explicit absence, or failure. G3 needs a successful single GET
  that says absent AND a successful list with no Pod at all.
- **Terminal and time checks in observe.**
  - The v2 `terminal` must be content that `terminal_evidence()` can produce.
  - Times must chain: requested ≥ intent; verifiedAt ≥ requested; completed ≥ verifiedAt; none in the future.
- **Deadlines.**
  - In the Pod wait, the health poll and the observe window, call timeouts and sleeps are bounded by the
    monotonic time left.
  - A result that returns after the window is not accepted: Pod → rc 7; health → rc 10/11; observe → rc 5.
  - Saving evidence after a pass is grace, outside the window.

**Evidence chain.** `/root/issue61/evidence/w6-{enter1,reboot,observe,enter2}-<stamp>/` (root 0700, files 0600, each
fsynced). Each directory is created before that step's first change, so an attempt is never reused. `w6-observe`
records the sha256 of the enter1 and reboot `run.json` files. enter2 re-checks those hashes, the current boot (= B1),
the gen-5 stop in the database (v2, terminal, proof B1), and the exact gen-5 fence that observe recorded. The reboot
directory also holds the full k3s journal (`k3s-journal.jsonl`; above the 16,000,000-byte tool cap it is recorded as
over cap, never as empty), a `containerd.log` copy with its source metadata, and the namespace, hostname, B0 and Node
identities. The observe directory holds the raw sudo/sshd journals and the controller log. Only counts, times and
digests of those are printed.

**W7 gate (stated before W6 runs).** W7 is read-only. Its deadline is the gen-6 Pod start + 60 min. It accepts exactly
one of:
- a **v1** idle receipt: a valid clean drain, if the runner was supplied and became available; or
- a **v3** receipt whose `unavailable.reason` equals the runner's own `health.failure` (expected: `auth_revoked`).

Either way it needs the gen-6 Pod UID's `pod-deleted` proof, the release (`idle_stopped` gen 6, no Pod), and unchanged
conversations. If neither has happened by the deadline: a read-only report, and PM decides. Never a DELETE or reclaim
as a substitute. This does not change the W4 R1 verdict. If enter2 returns rc 10 (still `not_configured`), the runner
is left running and PM decides.

### W6 attempt 1 outcome (2026-11-04): stopped at step 2, no reboot

- **Step 1** (`w6-enter.py`, enter1) returned rc 0 at 09:54Z.
  - Gen 5, Pod `c00de0a9…`, health `auth_revoked`.
  - Bound: step 2 had to start before 10:14:11Z.
- **Gen 5 was idle-drained before step 2 ran.**
  - `lastActivity` was 09:54:11Z, so the idle drain was due at 10:24:11Z.
  - Stop `3b15f417…` was requested at 10:24:36Z.
  - Its receipt is **v3**, reason `auth_revoked`, `clean=false`.
  - The `pod-deleted` proof is on B0. The stop completed at 10:24:37Z.
- **Step 2** (`w6-reboot.py`) started at 10:33:11Z, which was 19 minutes past the bound.
  - It refused with rc 3: too late, the registry was `idle_stopped`, a gen-5 stop existed, and the Pod was gone.
  - It wrote no evidence and no intent, and requested no reboot.
- **Read-only diagnosis at 10:34Z.**
  - Registry: `idle_stopped`, gen 5, revision 20, no Pod, 0 active, not ambiguous.
  - …5804 has 5 stops in total. Open stops, entries, admissions, outbox and sessions are all 0.
  - Conversations: n=0, digest `-`.
  - The boot is still B0. `w6.lock` is present, and no process holds it.
- **The W6 reboot gate was not run.** A natural drain on B0 is not the v2 terminal recovery that W6 needs.
- **Why it failed:** the delay between relaying step 1 and step 2.
- **The approved attempt-1 scripts cannot resume.** `w6-enter.py` refuses a second enter1, its FENCE1 is pinned to
  gen 4 rev 16, and its bound has passed.
- **Attempt-1 files are kept as they ran.** The scripts below stay unchanged; their hashes are the round-3 approval.
  The record `w6-enter1-20261104T095411Z` and `w6.lock` are never written by attempt 2.

### W6 attempt 2: same gate, new baseline, one command for steps 1 and 2

The scripts are `w6a2-enter.py`, `w6a2-reboot.py` and `w6a2-observe.py`. They are attempt-1's scripts with these
changes only; every safeguard from c192b52 stays.

- **Baseline (pinned, refused rather than adapted).** enter1 needs the 10:34Z diagnosis exactly:
  - fence: `idle_stopped`, gen 5, **rev 20**, no Pod, 0 active, not ambiguous;
  - the gen-5 stop is exactly stop `3b15f417…` for Pod `c00de0a9…`: completed, receipt v3 `auth_revoked` `clean=false`, not terminal;
  - its `pod-deleted` proof and stop row are on B0 and the recorded Node;
  - …5804 has 5 stops in total.
- **Chain.**
  - enter1: gen 5 → **6**.
  - reboot: under gen 6. The registry still has 5 stops and no gen-6 stop.
  - observe: the gen-6 stop is v2 terminal with proof on B1, and is the only new stop (6 in total). The registry is `idle_stopped` gen 6.
  - enter2: gen 6 → **7**. It re-checks the gen-6 stop and the 6-stop count.
  - Every stop query names its generation. The harness fake asserts that each query names the expected generation.
- **Attempt separation.**
  - Records go in `w6a2-{enter1,reboot,observe,enter2}-<stamp>`, and every record carries `"attempt": "a2"`.
  - The lock is `/root/issue61/w6a2.lock`.
  - Each attempt-2 step also needs attempt 1's `w6-*` records to be exactly `w6-enter1-20261104T095411Z`. Anything missing or extra gives rc 3.
  - enter1 also checks that record's content: result 0, gen 5, Pod `c00de0a9…`.
  - An attempt-1 script run by mistake refuses (rc 3) before any session, POST or reboot. It writes no record and does not consume attempt 2's permit (tested).
- **Unchanged:** the 1200 s bound, health classes, three-way Pod lookups, deadlines, conversation checks, the evidence capture, exactly one `systemctl reboot`, no restore bypass, and the rc tables above (with gens 6/7 in place of 5/6).

**Steps (attempt 2).**

| step | command | what changes |
|---|---|---|
| 1+2 | **one chained command** (below) | one session row (revoked), one runner POST (gen 5 → 6); then, only if enter returned 0: evidence files and one `systemctl reboot` |
| 3 | `w6a2-observe.py`, separate command after ssh answers again | nothing (read-only, at most 30 min) |
| 4 | `w6a2-enter.py` (enter2), separate command | one session row (revoked), one runner POST: gen 6 → 7 |
| W7 | read-only, separate draft | nothing |

**Steps 1+2, exact command** (PM relays it verbatim; review clears this text together with the hashes):

<!-- w6a2-chain -->
```
! ssh -i ~/.ssh/anxious-back-devvm -o IdentitiesOnly=yes -p 22107 opc@127.0.0.1 'printf "%s  %s\n%s  %s\n" 1ab4baf7a48794577fd853aff68f135f1a55423368918d61a32a5076a183ba3a w6a2-enter.py 4c0e570dd71d6baab3cb12b2e566894cf046b0da1ac64c148c38c4d6a07fc096 w6a2-reboot.py | sudo -n sh -c "cd /root/issue61 && sha256sum -c -"; rc=$?; [ "$rc" -eq 0 ] || echo "hash check failed rc=$rc"; [ "$rc" -eq 0 ] && { sudo -n python3 -I /root/issue61/w6a2-enter.py; rc=$?; echo "enter rc=$rc"; [ "$rc" -eq 0 ] && { sudo -n python3 -I /root/issue61/w6a2-reboot.py; rc=$?; echo "reboot rc=$rc"; }; }; echo "remote rc=$rc"; exit "$rc"'
```

- **Hashes first.** Both script hashes are checked before anything runs. If either fails, nothing runs, and the
  `sha256sum` rc is the remote rc.
- **Enter.** `w6a2-enter.py` runs only after both hashes pass. Its rc is printed as `enter rc=N`.
- **Reboot only after enter rc 0.** Any other enter rc, including 70, 72, 6 or 130 after an interruption, ends the
  command with that rc as `remote rc`. Nothing is retried.
- **Reboot.** `w6a2-reboot.py` runs only after enter rc 0. Its rc is printed as `reboot rc=N` and is the remote rc.
  - Its own precheck and its 1200 s bound still decide; it does not rely on the chain.
  - When the reboot is requested, the ssh session usually drops before `reboot rc=0` is printed. Then `ssh` exits
    255. That is the expected end, and it is not evidence either way. Step 3 decides.
- **Exit code.** The command always ends with `remote rc=<rc>; exit <rc>`. No trailing `echo` can turn a failure
  into rc 0.

**W7 (attempt 2).** W7 is the same gate as above, applied to **gen 7**: v1 clean drain, or v3 with `unavailable.reason` equal to the gen-7 runner's `health.failure`. It needs the gen-7 `pod-deleted` proof and `idle_stopped` gen 7. The deadline is the gen-7 Pod start + 60 min.

## Mock harness (Docker, Python 3.9 as on the guest)

```sh
docker run --rm -v "$PWD/infra/hosting/m3:/m3:ro" -v "$PWD/backend/db:/db:ro" python:3.9-alpine \
  python3 -I /m3/multi-user-runners/issue61/test_issue61.py
docker run --rm -v "$PWD/infra/hosting/m3:/m3:ro" python:3.9-alpine \
  python3 -I /m3/multi-user-runners/issue61/test_w3b.py
docker run --rm -v "$PWD/infra/hosting/m3:/m3:ro" python:3.9-alpine \
  python3 -I /m3/multi-user-runners/issue61/test_w4.py
docker run --rm -v "$PWD/infra/hosting/m3:/m3:ro" python:3.9-alpine \
  python3 -I /m3/multi-user-runners/issue61/test_w4b.py
docker run --rm -v "$PWD/infra/hosting/m3:/m3:ro" python:3.9-alpine \
  python3 -I /m3/multi-user-runners/issue61/test_w5.py
docker run --rm --network none --hostname simple -v "$PWD/infra/hosting/m3:/m3:ro" \
  -v "$PWD/backend/ops:/ops:ro" python:3.9-alpine python3 -I /m3/multi-user-runners/issue61/test_w6.py
docker run --rm --network none --hostname simple -v "$PWD/infra/hosting/m3:/m3:ro" \
  -v "$PWD/backend/ops:/ops:ro" python:3.9-alpine python3 -I /m3/multi-user-runners/issue61/test_w6a2.py
```

`test_w6a2.py` uses `mock/fake_w6a2.py`. It installs attempt 1's approved scripts beside attempt 2's, as on the
guest. It also creates attempt 1's record, its `w6.lock`, and the gen-5 stop as diagnosed. It runs the README's
chained command through `sh` with a `sudo -n` stub; the hashes are swapped for those of the mock-pointed copies.

`test_w3b.py` uses `mock/fake_w3b.py`, which emulates the binding tool's guards, report and compare-and-swap,
and `mock/w3b-plans.json`, the two live plans from the 2026-11-03T14:53:48Z read-only snapshot.

`mock/resources.json` is the real renderer output for `rollout/values.example.json`, with dev-like image
references.
