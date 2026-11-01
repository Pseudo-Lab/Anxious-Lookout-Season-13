# #66: terminate the legacy runner Pod and decommission owner `3cb453bf…`

Production runbook. PM runs it as root on the OCI node in one window, with the user's go. Scope (user
decision, 2026-10-31): remove this owner's **runner state only**. Kept: `auth.accounts`, sessions,
conversations and posts. Removed:

- the runner rows in `runner_runtime`, `runner_bindings` and `runner_termination`
- the transport Secret, both PVCs and both PVs (Retain)
- the two loop filesystems under `user-runners/<state>`
- `provision-output/<owner>`

| Script | What it does |
| --- | --- |
| `worker-swap.sh apply\|rollback` | Puts `controller.sh`/`observer.sh` on the #66 worker image (the B5 mechanism, worker only). |
| `legacy-stop.sh diagnose\|observe\|evidence` | Runs the observer's own stop path with `--legacy-pod-uid`. |
| `decommission.sh report\|apply\|host\|close` | Runs `ops.runner_decommission` (DB), then deletes the Kubernetes objects (UID preconditions) and the host files. |

All three scripts use the same rc legend:

- 0 done and verified
- 2 placeholders left
- 3 precheck or tool refused; nothing changed by this run
- 4 (`legacy-stop.sh` only) the tool ran and refused
- 5 a change failed or was ambiguous
- 8 changed, but a postcheck failed

A refusal names the observed value. Post every output to #66. Never delete a Pod, an object or a file by hand,
and never edit the DB by hand.

Mock harness (Docker only; Python 3.9 as on the node, and 3.12):

```sh
docker run --rm -v "$PWD/infra/hosting/m3:/m3:ro" -v "$PWD/backend:/backend:ro" alpine:3.15 \
  sh /m3/multi-user-runners/issue66/test_issue66.sh
```

## Inputs and install

Fill these placeholders after the merge:

- `@@WORKER_IMAGE_ID@@` (all three scripts): the provisioner image built from the #66 merge commit and loaded
  into the node's Docker store (`sha256:<64 hex>`).
- `@@B5_WORKER_IMAGE_ID@@` (`worker-swap.sh`): the worker image that #72 B5 installed. Take it from
  `/root/issue72/evidence/b5`, or from the image ID in the current `controller.sh`.

Install into `/root/issue66/bin/` (root 0700) with `git show <merge sha>:<path>`, then fill the placeholders.
Record the SHA-256 of each filled file and check it with `sha256sum -c` before every run, as for #72.

The fixed identities in the scripts:

| | Value |
| --- | --- |
| owner | `3cb453bf-d870-40dc-b7e1-3a58aa0cbaf2` |
| Pod | `runner-3cb453bfd87040dcb7e13a58aa0cbaf2`, UID `880bf6cd-70ea-4625-9185-c1604c8f8466` |

Both come from the #72 F inventory and the 10-31 runbook record.

## Steps

### 1. Open the window

```sh
systemctl stop issue16-controller.timer issue16-observer.timer
systemctl stop issue16-controller.service issue16-observer.service
systemctl is-active issue16-controller.service issue16-observer.service   # both inactive (or failed)
T=$(date -u '+%Y-%m-%d %H:%M:%S UTC'); echo "$T"
```

- **Changes:** pauses Pod creation and stop handling for all users. Running runners stay up.
- **Undo:** step 9.
- **Run again:** safe.

### 2. Worker image

```sh
sh /root/issue66/bin/worker-swap.sh apply
```

- **Changes:** only the image ID in `controller.sh` and `observer.sh`. The originals are kept in
  `/root/issue66/evidence/worker/*.pre66`.
- **Undo:** `worker-swap.sh rollback`, which restores both files byte for byte.
- **Run again:** safe. A script that is already on the new image is skipped. If a backup exists and differs, the
  script refuses (rc 5).

### 3. Precondition: no re-entry pending

`legacy-stop.sh observe` checks this itself and refuses with rc 3. Read it first:

```sh
sh /root/issue66/bin/legacy-stop.sh evidence
```

- **Pass:** `pending entries=0`, and the registry shows `runner_unreachable|9|…|880bf6cd…`. A stop row for gen 9
  shows `unanchored`, `receipt=false`, `completed=-`.
- **If an unconsumed entry exists:** release would start generation 10 instead of `idle_stopped`. Stop here and
  post to #66.

### 4. Diagnose (read-only)

```sh
sh /root/issue66/bin/legacy-stop.sh diagnose
```

- **Expected:** `termination-diagnose ready_for_observation`, rc 0. Without the option this owner reports
  `pod_invalid`; with it the identity check passes.
- **rc 4 with another code** (for example `node_mismatch` or `pod_address_invalid`): stop, post the output, then
  go to step 9.
- **Run again:** safe (read-only).

### 5. Observe once: anchor → drain → UID-conditioned DELETE

```sh
sh /root/issue66/bin/legacy-stop.sh observe
```

**Expected:**

- `termination observed`, rc 0
- `after: pod … 880bf6cd…|<deletionTimestamp>|…`
- `after: registry …` with a boot ID and `receipt=true`

**Changes (the observer's normal path):**

- records the node attribution in the stop row
- the runner writes its drain receipt
- a graceful DELETE for that Pod UID only

**Run again:** safe. The anchor is not rewritten. A repeated drain returns the stored receipt. A Pod that is
already deleting is left to kubelet.

#### Stop point: drain refused (rc 4, `reconciliation_required`)

The legacy runner may refuse its drain:

- **409 `conflict`:** an active native session, a held turn lock, or a non-idle session mapping.
- **503:** native was never constructed and the auth failure is not `not_configured`, or the runner is already
  stopping.

The observer then deletes nothing. Do this, in order, and change nothing:

```sh
sh /root/issue66/bin/legacy-stop.sh evidence                              # Pod state/events, runner /health, rows
sh /root/issue66/bin/legacy-stop.sh diagnose                              # where the read-only path stops now
journalctl -u issue16-observer.service --since "$T" -o cat | tail -n 40   # earlier timer runs, if any
```

`evidence` asks the runner's `/health` from inside the runner container:

- the token stays in the Pod
- only `ownerId`, `stateId`, `podUid`, `available`, `reason` and `verification` print

How to read `/health`:

- **`available=false`, `reason` set (for example `auth_revoked` or `not_configured`):** native is absent or
  blocked.
- **`reason=unavailable`:** the runner is already stopping.
- **`available=true`:** native exists, so a 409 means a session or lock is still held.

**Post all three outputs to #66 and stop.** Do not delete the Pod or edit the DB. Go to step 9 (timers back). The
stop stays open, and the observer retries it every cycle with the exact check, which refuses this Pod, so nothing
destructive repeats. back decides the next step from the evidence.

### 6. Wait for the Pod to go, then observe once more: proof → release

```sh
if /usr/local/bin/k3s kubectl -n codex-runner wait --for=delete pod/runner-3cb453bfd87040dcb7e13a58aa0cbaf2 --timeout=180s; then
  sh /root/issue66/bin/legacy-stop.sh observe
else
  echo 'WAIT TIMEOUT: do not run the observer; run legacy-stop.sh evidence, post it, go to step 9'
fi
```

- **Expected:** `observed`, rc 0, and `after: registry <state>|idle_stopped|9|-`. The proof podUid in the stop row
  is `880bf6cd…`.
- **Run again:** safe. Once released, `release` returns the consumed state and never releases a later UID.
- **rc 4:** run `evidence` and post it. Usual causes:
  - the host proof (CRI or proc still shows the UID)
  - the retained receipt (`termination-<uid>.json` on the native volume)

### 7. Decommission report (read-only), then review

Create the short-lived admin DSN as for #72 D:

- `/root/issue66/decommission/private/admin-dsn`
- directory root 0700, file root 0600
- the database address that the workers use

Then:

```sh
sh /root/issue66/bin/decommission.sh report
```

The output shows:

- **The export:** owner, state, gen/rev, plan digest, images, the five objects with UIDs, the last stop and its
  proof, and the row count and digest of each of the 8 tables. It contains no secrets and no session hashes.
- **Each object's live UID**, which must equal the export's.
- **The delete list:** `user-runners/<state>` entries with size and allocation, the two loop mounts,
  `provision-output/<owner>` file names and sizes (contents are never read), and the lock file.

**Refusals** print the observed values:

- `not_idle_stopped` shows the phase and row
- `pending_work` shows the counts
- `termination_unproven`, `resources_mismatch` and `export_changed` show the differing fields

**Run again:** move `/root/issue66/decommission/{report,inputs}` aside first. It refuses while an export exists.

### 8. Apply, then host, in the same window

```sh
sh /root/issue66/bin/decommission.sh apply
sh /root/issue66/bin/decommission.sh host
sh /root/issue66/bin/decommission.sh close     # unlinks the admin DSN
```

**`apply`**

1. The tool recomputes the export under the owner lock and deletes the runner rows only if the export is
   identical (`deleted`).
2. It then deletes the Secret, both PVCs and both PVs, each by `DELETE` with the export's UID as precondition,
   and waits until each is gone.

The postcheck requires:

- no runner rows
- `auth.accounts` still 1
- all five objects absent

**Run again after any rc 5:** safe.

- The tool reports `already_applied` when no row remains.
- An object that returns 404 is skipped.
- A live object with a different UID is never deleted (rc 5: stop and post).

**`host`** is required, not optional.

- Why: `worker.py restore_inventory` requires the `user-runners/` and `provision-output/` sets to match. If only
  one side were removed, the next boot's restore would refuse for every owner.
- It prints the delete list first.
- It refuses (rc 3, nothing deleted) if either of these is true:
  - this state's loop device is mounted anywhere else (a kubelet bind)
  - `user-runners/<state>` has an unexpected entry
- Then it:
  1. unmounts `native` and `control`
  2. checks that the loop is detached and the mount points are empty
  3. deletes the two `.ext4` images, the directories, `provision-output/<owner>` and its lock file
- rc 0 only if both are absent **and** restore's two inventories match again (`only-storage=[]
  only-outputs=[]`).
- **Run again:** safe. It deletes only what remains of the expected entries.
- **Irreversible:** this owner's runner-side Codex thread files are deleted. Conversations in the DB stay.

### 9. Close the window

```sh
systemctl start issue16-controller.timer issue16-observer.timer
systemctl list-timers 'issue16-*' --no-pager
```

Read-only inventory afterwards (fields only):

```sql
SELECT owner_id, phase, generation, pod_uid FROM runner_runtime.registry ORDER BY owner_id;   -- 4 owners, no 3cb453bf
SELECT count(*) FROM runner_termination.stops WHERE completed_at IS NULL;                     -- 0
SELECT count(*) FROM auth.accounts WHERE id='3cb453bf-d870-40dc-b7e1-3a58aa0cbaf2';            -- 1
```

```sh
# Pod names carry the owner hex; PVC, PV and Secret names carry the state hex.
S=$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["state"].replace("-",""))' /root/issue66/decommission/report/export.json)
/usr/local/bin/k3s kubectl -n codex-runner get pods -o name | grep -c 3cb453bfd87040dcb7e13a58aa0cbaf2   # 0
/usr/local/bin/k3s kubectl -n codex-runner get pvc,secrets -o name | grep -c "$S"                       # 0
/usr/local/bin/k3s kubectl get pv -o name | grep -c "$S"                                                 # 0
```

If the user comes back later, the API registers a fresh runner. The controller then prepares new storage and a new
binding, without the old state.
