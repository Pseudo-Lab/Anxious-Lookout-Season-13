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
| W2 | `w2-apply.py apply` | user | 061 grants, Namespace, DB Secret, RBAC/pool objects, CNP, api image, controller Deployment |
| W2 rollback | `w2-apply.py rollback <evidence dir>` | user | Restores every object W2 changed from its backup. Deletes only objects W2 created, by UID; the namespace goes last and only if it is empty |
| W3, W3b, W4–W7 | (next drafts) | | R2 for …5804, binding image change, measurements, SA rotation, reboot, postcheck |

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
| 4 | W0 only: a condition differs from the expected state |
| 5 | A change failed. The output lists what this run changed and the rollback command |
| 8 | Changed, but a postcheck failed |

## Mock harness (Docker, Python 3.9 as on the guest)

```sh
docker run --rm -v "$PWD/infra/hosting/m3:/m3:ro" -v "$PWD/backend/db:/db:ro" python:3.9-alpine \
  python3 -I /m3/multi-user-runners/issue61/test_issue61.py
```

`mock/resources.json` is the real renderer output for `rollout/values.example.json`, with dev-like image
references.
