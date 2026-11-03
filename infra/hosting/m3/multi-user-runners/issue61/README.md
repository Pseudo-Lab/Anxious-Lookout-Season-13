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
| W4–W7 | (next drafts) | | Measurements, SA rotation, reboot, postcheck |

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
| 80 | A tool container was not proved stopped; Deployments left at 0 |
| 130 | Interrupted before any apply; restored |

## Mock harness (Docker, Python 3.9 as on the guest)

```sh
docker run --rm -v "$PWD/infra/hosting/m3:/m3:ro" -v "$PWD/backend/db:/db:ro" python:3.9-alpine \
  python3 -I /m3/multi-user-runners/issue61/test_issue61.py
docker run --rm -v "$PWD/infra/hosting/m3:/m3:ro" python:3.9-alpine \
  python3 -I /m3/multi-user-runners/issue61/test_w3b.py
```

`test_w3b.py` uses `mock/fake_w3b.py`, which emulates the binding tool's guards, report and compare-and-swap,
and `mock/w3b-plans.json`, the two live plans from the 2026-11-03T14:53:48Z read-only snapshot.

`mock/resources.json` is the real renderer output for `rollout/values.example.json`, with dev-like image
references.
