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
| W5–W7 | (next drafts) | | SA rotation, reboot, postcheck |

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

`sudo -n python3 -I /root/issue61/w5-rotation.py`

1. **Precheck:**
   - no earlier `w5-*` evidence;
   - host `issue16-*` units inactive;
   - the host 24 h kube-token files under `/etc/anxious-lookout/issue16/` are stat-ed only, and must be older than 24 h or absent;
   - the `runner-controller` Deployment and Pod are at the recorded UIDs, 1/1, with only the default `kube-api-access` token (3607 s), mounted read-only, and no hostPath.
2. **Claims:** a fixed reader in the controller container decodes only the JWT payload. It prints the claims `iat, nbf, exp, warnafter, jti, sub, aud, namespace, pod, serviceaccount, node` and the projected volume's `..data` link. It reads every 60 s until the `jti` changes, for at most 75 min.
3. **Proof:** a fresh process runs the controller's code: `build()`, `observer_pool.bound(…5804)` (PVC GETs), `runtime.namespace()`, `service.pod()`, `NodeEvidence.identity()`. Each request re-reads the token file. The `jti` is read before and after, and both must equal the rotated `jti`.
4. **Resident controller:** same container, no restart, and no `reconciliation_required` line since the first read.

**Limits:**
- k3s extends projected tokens, so the old token stays valid until the Pod goes. A success shows that the rotated token works, not that the old one is refused.
- With no runner Pod, the resident loop makes only its watch call. The proof process covers the rest; the resident process uses the same per-request read (`runner_controller_service.py:408-411`, `runner_kube.py:208-214`).

| rc | meaning |
| --- | --- |
| 0 | rotated and proved |
| 3 | precheck refused |
| 4 | a proof call failed, or the resident controller restarted or logged `reconciliation_required` |
| 5 | not rotated within 75 min |
| 6 | rotated again during the proof (no retry) |
| 7 | claims unreadable or malformed |
| 8 | controller Pod changed during the window |
| 70 | unexpected error |
| 130 | interrupted (SIGINT, SIGTERM or SIGHUP; `run.json` is still written) |

**Evidence:** `/root/issue61/evidence/w5-<stamp>/run.json`, claims and outcomes only.

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
```

`test_w3b.py` uses `mock/fake_w3b.py`, which emulates the binding tool's guards, report and compare-and-swap,
and `mock/w3b-plans.json`, the two live plans from the 2026-11-03T14:53:48Z read-only snapshot.

`mock/resources.json` is the real renderer output for `rollout/values.example.json`, with dev-like image
references.
