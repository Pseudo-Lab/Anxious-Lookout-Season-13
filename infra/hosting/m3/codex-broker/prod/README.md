# Production step scripts (#72)

These are the per-step scripts for the codex-broker production rollout and the host supply retirement.
The plan, acceptance criteria and user decisions are in [#72](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/72).

**Nothing here runs by itself.** Each state-changing script goes through these stages:
1. review clears the script's SHA-256, with its `@@…@@` values filled in;
2. it is installed root-only on the node;
3. the user runs it **once**, through a command that checks the hash and keeps the original rc.

A non-zero or unclear rc is reported and never rerun, and partial evidence is kept. Agents only read production.

| Step | Script | Changes state | Inputs (`@@…@@` and files) |
|---|---|---|---|
| F | `inventory.sh` | no | none |
| B3 | `apply-broker.sh` | yes | the runner image reference (after A); filled: `local-path` + `ReadWriteOnce` (decision 1); files: `broker_render.py` and `runner-cnp.json` (pinned in the script), `values.json` written on the node |
| B5 | `b5-release.sh` | yes, reversible | the #72 merge SHA, A's runtime image reference and A's provisioner image ID (after A); filled: the live baselines from node reads 2 and 3 (api `issue36-runtime@sha256:7998b73d…`, worker `sha256:1c3ce268…`) and the #66 Pod exemption (decision 3). Run in the B5+D window, after C |
| D | `d-binding-move.sh report\|apply <owner>`, then `close` | yes (one CAS per owner; a reverse move is a new report/apply) | A's provisioner image ID, runner and shell-executor references, plus B5's `RELEASE_SHA` and `API_IMAGE_REF` (same values, for D's B5-state recheck) (after A); filled: the #66 owner exempt; the admin DSN file the user creates on the node |
| C | `check-login.sh` | no (run after the user's `codex login --device-auth`) | none |
| E1 | `retire-e1-disable.sh` | yes, reversible | runner image containerd digest (after A); filled: the #66 owner `runner-3cb453bfd87040dcb7e13a58aa0cbaf2` exempt (decision 3, option c). Requires a broker login, then writes `evidence/e1/stopped-at` and `evidence/e1/session` (`auth.json` file identity) |
| E2 | `retire-e2-rbac.sh` | yes, reversible | `supply-rbac.json` from this directory (pinned in the script; the harness checks it against `ops.runner_rollout`) |
| E3 | `retire-e3-files.sh` | yes, reversible from evidence | none |
| E4 | `retire-e4-final.sh` | **yes, no return** | none; filled: 24 h after E1's `stopped-at` (decision 2), plus a broker-recorded `scheduled` `ok` refresh **in the same login session** (an unbroken `before`→`after` chain from E1's `session` identity to the current `auth.json`); the five credential file names from F. Deletes the supply ServiceAccount and the credential directory only; **no database step** (below) |

**A and D (decision 6, user, 2026-10-31).** The full plan is in #72; this records what the scripts here depend on.
- **A builds four images** from the #72 merge commit on the OCI node (`linux/arm64`, `APP_GIT_SHA` = the full merge SHA):

  | Image | `backend/Dockerfile` target | Store |
  |---|---|---|
  | runner (and the broker) | `runner` | containerd (`k3s ctr -n k8s.io images import`) |
  | api | `runtime` | containerd |
  | provisioner | `provisioner` | containerd, **and the Docker store**: the worker units and the D wrappers run it as a local image ID with `--pull=never` |
  | shell-executor | `shell-executor` | containerd |

  Each archive's `sha256sum` and each containerd digest go on #72. B3's `RUNNER_IMAGE_REF` and E1's `RUNNER_IMAGE_ID` come from the runner row.
- **D moves the runner and the executor image in one CAS** per binding: `ops.runner_binding_image` with `newImage` (A's runner) and `executorImage` (A's shell-executor) for v2 plans.
  - It runs from **A's provisioner image**, after B5. That image carries the `executorImage` swap (#59, `bec1764`) and the `plan_digest` of the schema B5 leaves.
  - Since #23 (`dab0c7d`), the executor changed only by #59's SIGTERM handler (`0643d9f`). The interface with the runner is unchanged; moving the executor makes Pod stops end at once instead of at the grace limit.
  - Each D window uses a fresh short-lived admin DSN file (`root:root 0600`). It points at the database address the workers use today; the 10-25 change left no after record, so nothing older is a source for it. The file is unlinked after `apply`. Nothing from earlier windows is reused, and the before-plan is captured fresh.

**Node-local delivery.** The user runs every command in their own session on the OCI node; nobody here has production ssh. Scripts and inputs reach the node the same way:
1. `git -C <node checkout> fetch`;
2. `git show <sha>:<path>` into `/root/issue72/` (scripts) or `/root/issue72/render/` (inputs), with no clobber;
3. `sha256sum -c`;
4. run, keeping the original rc.

**The B3 render happens on the node.** The script renders the broker objects with the pinned `broker_render.py`, from a `values.json` the user writes there.
- The ChatGPT workspace id (`accountId`) never leaves the node.
- Every other values field must equal the script's reviewed constants.
- The rendered copy is kept as `evidence/b3/broker.json` for the postcheck and the rollback.

**What every script shares:**
- Each script prints selected fields only. It never reads a Secret object, a credential file's content, `auth.json` or `codex login status` text.
- **rc legend:**

  | rc | Meaning |
  |---|---|
  | 0 | done and verified |
  | 2 | placeholders left |
  | 3 | precheck refused; nothing changed |
  | 5 | a change failed midway; stop |
  | 6 | evidence failed; nothing removed or changed (E3, B5) |
  | 7 | a removal failed midway (E3) |
  | 8 | changed, but a postcheck failed |

- Every state-changing script prints its rollback line.

**What each script checks:**
- **`kubectl diff` gates:** `kubectl diff` exits 1 on most errors as well as on differences, so each gate needs both the rc and an empty stderr. B3 server-diffs only the objects outside the absent `codex-broker` namespace before the apply; the absent namespace proves the rest are new.
- **B3, the runner CNP:** applied only if the live `runner-callback-draft` spec equals the reviewed file without the one `codex-broker:8080` egress rule. The live copy is kept as `evidence/b3/runner-cnp-before.json` for the rollback.
- **E1, runner images:** Running user-runner Pods are matched on the `runner` container's status by name. Production Pods also carry the shell executor. Every `runner_bindings` `plan.image` must be the reviewed digest too; decision-3 names are exempt.
- **`systemctl` and `stat` lookups:** a lookup's own exit status is checked before any property in its output is used. Correct-looking stdout followed by a failure status never passes.
- **E3, other consumers:** before removing anything, E3 reads every file reachable with `find -L` for references to the supply scripts. It covers:
  - the unit search roots (fragments, drop-ins, wants links, vendor units);
  - `/usr/local/lib/anxious-lookout` and `/usr/local/sbin`;
  - cron.
- **E4, assumptions:** the file identities show filesystem continuity; they are not a transactional session lock. E4's user command therefore states two operating assumptions:
  - no device login or logout between E1 and E4;
  - E4 runs in the coordinated window, not concurrently with a login.

  A login concurrent with an in-flight refresh, a file replacement between E4's reads and its first change, and the app-server's own refresh were not exercised (review, `bacf694`).
- **E4, no database step:** production has no supply database role. The supply `database-url` is a copy of the observer login (`OBSERVER_DATABASE_URL`; rollout README before #60, `HOST-SUPPLY.md`), and the observer worker keeps that login until #61. E4 removes the copy with the credential directory and never connects to the database; the harness fails any E4 case that reaches `psql`. Rotating the observer login's password, so the removed copy is no longer valid anywhere, is a separate change with the observer's own `database-url`.
- **E1, a running batch:** production's timer starts a supply batch about every 15 s, and each batch currently ends `failed` after a few seconds (node read, 2026-10-31). E1 accepts a `failed` service (the F baseline) and waits out an `active`/`activating` one, read-only, for up to 30 s before it refuses. A batch the timer starts between that check and `disable --now` is stopped by it. Supply records each attempt as `unknown` before it delivers and never redelivers one, so a stopped batch is not retried.
- **B5, image references only:**
  - Since the running api (`d026b77`, node read 3) the release has no migration, no settings change, and no rollout-values change except the retired supply keys. The api change is the runner Pod manifest in `runner_kube.py`.
  - The live Deployment also carries the 10-25 IP/domain change, which a re-render from the 10-21 values would revert. So B5 patches only the `api` container and `runner-pool-input` initContainer images. It uses a JSON Patch whose `test` ops pin the uid, the resourceVersion and both old images as read in the precheck.
  - It replaces the one image ID in `controller.sh` and `observer.sh`, keeping each file's mode.
  - It leaves alone:
    - the supply units and `supply.sh` (E1–E3 retire them);
    - `worker.py` (the installed wrapper still runs the controller and observer modules);
    - `restore.sh` (no restore code changed since `d890447`).
- **B5, the #59 gate:** neither production release contains #59, so B5 changes the runner Pod manifest. The new api and workers refuse a live Pod rendered by the old code (`KubePods._pod`). B5 therefore refuses while any runner Pod is Pending or Running, except the #66 owner's issue28 Pod (decision 3). After B5, lookups of that Pod refuse as "requires reconciliation": fail closed, never deleted, until the #66 runbook.
- **B5 and D, one window:** the new manifest has no `RUNNER_TOKEN_FILE`, so an old runner image cannot start under it. Every existing binding moves in the same window, before the next entry.
  - Window order: the user stops entry and the controller and observer timers and services → B5 → `d-binding-move.sh report` per owner → review → `apply` per owner → `close` → canary.
  - B5 and D both refuse while a controller or observer unit is active.
  - B5 writes `evidence/b5/done` only after its postcheck passed. D requires that record and rechecks the live state: both worker scripts on the new ID and none on the old one, both api Deployment images, and the running api's `/app/release.json`. A B5 that stopped midway (rc 5) or failed its postcheck (rc 8) therefore blocks D.
  - B5's evidence follows E3: each copy is compared with its source (content, mode, owner) and recorded in `MODES`, and the manifest is verified before any change; otherwise rc 6 with nothing changed.
- **D, per owner:**
  - `report` reads the registry fence (`idle_stopped`, no Pod) and the plan, which must be v2 and not already on the new images. It computes `plan_digest` inside A's provisioner image (`--network none`), writes the root 0600 inputs, then runs the tool's `report`.
  - `apply` rechecks the fence and images against the inputs, then runs the tool with the report mounted read-only.
  - The postcheck reads both plan images and an unchanged registry row.
  - The tool runs as CUTOVER §6 shows (`--network=host` as the workers' units, the admin DSN mounted read-only). The DSN file's mode and owner are checked; its content is never read.
- **E1, execution conditions:** these are a point-in-time SELECT and Pod list, not a fence. E1's user command must therefore run only with entry, controller and observer quiesced (as for the D binding moves), and right after a fresh `inventory.sh` read of the binding and registry lines.
- **B3, Cilium:** `cilium-config` must be unchanged afterwards. No Cilium upgrade or restart is part of this rollout.

**Mock harness** (Docker only, never on a node):

```sh
docker run --rm --network bridge --user 0 -v "$PWD/infra/hosting/m3:/m3:ro" -v "$PWD/backend:/backend:ro" \
  --entrypoint sh alpine:3.20 /m3/codex-broker/prod/test_steps.sh
```

- The production node runs Python 3.9 (F inventory, 2026-10-31). Run the harness on `alpine:3.15` (Python 3.9) as well as `alpine:3.20`: every `python3 -I` snippet and the node-side B3 render run under it.
- `mock/k3s.py` and `mock/systemctl.py` stand in for the cluster and systemd.
- The credential fixtures carry `SECRET` markers. Any marker in any output fails the case.
- `mock/docker.py` stands in for A's provisioner image: its release record, `plan_digest`, and `ops.runner_binding_image` report and apply.
- **What the mocks cannot show:** the real image's import paths and the tool's database checks (`config_tests/test_runner_binding_image.py` covers the tool on disposable PostgreSQL; review's tier). It also cannot show the real Deployment rollout, or whether `k3s crictl inspecti` resolves A's reference.
