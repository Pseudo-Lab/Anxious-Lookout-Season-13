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
| B3 | `apply-broker.sh` | yes | the runner image reference, storage class and access mode (decision 1); files: `broker_render.py` and `runner-cnp.json` (pinned in the script), `values.json` written on the node |
| C | `check-login.sh` | no (run after the user's `codex login --device-auth`) | none |
| E1 | `retire-e1-disable.sh` | yes, reversible | runner image containerd digest; Pods exempt per decision 3 |
| E2 | `retire-e2-rbac.sh` | yes, reversible | `supply-rbac.json` from this directory (pinned in the script; the harness checks it against `ops.runner_rollout`) |
| E3 | `retire-e3-files.sh` | yes, reversible from evidence | none |
| E4 | `retire-e4-final.sh` | **yes, no return** | stable-period end (decision 2), credential file names (from F), supply database role |

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
  | 6 | evidence failed; nothing removed (E3) |
  | 7 | a removal failed midway (E3) |
  | 8 | changed, but a postcheck failed |

- Every state-changing script prints its rollback line.

**What each script checks:**
- **`kubectl diff` gates:** `kubectl diff` exits 1 on most errors as well as on differences, so each gate needs both the rc and an empty stderr. B3 server-diffs only the objects outside the absent `codex-broker` namespace before the apply; the absent namespace proves the rest are new.
- **B3, the runner CNP:** applied only if the live `runner-callback-draft` spec equals the reviewed file without the one `codex-broker:8080` egress rule. The live copy is kept as `evidence/b3/runner-cnp-before.json` for the rollback.
- **B3, Cilium:** `cilium-config` must be unchanged afterwards. No Cilium upgrade or restart is part of this rollout.

**Mock harness** (Docker only, never on a node):

```sh
docker run --rm --network bridge --user 0 -v "$PWD/infra/hosting/m3:/m3:ro" -v "$PWD/backend:/backend:ro" \
  --entrypoint sh alpine:3.20 /m3/codex-broker/prod/test_steps.sh
```

- `mock/k3s.py` and `mock/systemctl.py` stand in for the cluster and systemd.
- The credential fixtures carry `SECRET` markers. Any marker in any output fails the case.
- **Not covered yet:**
  - B5, the api and provisioner image update with the supply RBAC held back;
  - the D binding moves (`ops.runner_binding_image` report and apply).

  Both depend on the rollout bundle inputs and come next.
