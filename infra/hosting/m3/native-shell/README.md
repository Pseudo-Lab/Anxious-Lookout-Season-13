# Native shell runner: host and rollout procedures (#68)

Issue [#68](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/68). Since #68 the runner Pod has
one container. Codex 0.160.1 runs native shell commands, `apply_patch` and unified_exec in a bubblewrap
(`workspace-write`) sandbox there; the shell-executor sidecar and the MCP `shell` bridge are gone.

This file holds the procedures that touch a node or the controller inputs. Each one says who runs it.
Nothing here runs from a render, a test or an agent session on production.

| Section | What | Production executor | When |
| --- | --- | --- | --- |
| 1 | `kernel.yama.ptrace_scope=1` | user (read-only node for agents) | any time, independent of the #68 window |
| 2 | Seccomp profile `seccomp-runner-userns-v1.json` | PM after review | before the #68 window |
| 3 | Controller inputs without `executor`, binding runner images | PM after review | the #68 window |
| 4 | Runner approval path and startup self-check (reference) | nobody: runner code | every app-server launch and turn |
| 5 | Codex version: what to recheck | back, review | before any Codex pin change |
| 6 | A `.codex` in the workspace blocks the runner: availability and recovery | operator, with the owner | when it happens |
| 7 | Runner Pod and image (#68 order 4) | reference | the #68 window |

Still to come: the SELinux policy for `container_t` (#68 requirement 3). It needs the VM's AVC
denials under `container_t` first and will be section 8.

## 1. `kernel.yama.ptrace_scope=1` (user, production node)

**Why.** Defence in depth only. The sandboxed shell cannot see or read app-server because bubblewrap puts it
in its own PID and user namespace (#68 requirement 2). That holds with `ptrace_scope` 0 or 1, and the
development VM measurement checks it at both. With 1, a process can trace only its own descendants
unless it has `CAP_SYS_PTRACE`, so a non-root process on the node can no longer attach to a sibling
(#58 F). The development VM has had 1 since 2026-10-25 (`infra/k3s/PARITY.md`).

**Effect.** It applies at once to new attach attempts. No process restarts and no reboot are needed.
Yama checks attach-mode access only (`PTRACE_ATTACH`/`PTRACE_SEIZE`, `process_vm_readv`/`writev`,
`/proc/<pid>/mem`). Reading `/proc/<pid>/status`, `ns/` or `cmdline` and `nsenter` keep their usual checks.

- Root and k3s/containerd hold `CAP_SYS_PTRACE` and are unaffected.
- A non-root `gdb -p`, `strace -p` or `py-spy dump` against a process that is not its child fails with
  `EPERM`.
- No hosting component (API, web, PostgreSQL, gateway, Cilium, broker, runners) is known to attach to a
  non-child without `CAP_SYS_PTRACE`. If one fails after the change, roll back and report.

### Read-only precheck

```sh
cat /sys/kernel/security/lsm                    # must contain yama
sysctl -n kernel.yama.ptrace_scope              # 0 (2026-10-26 read-only check)
/usr/lib/systemd/systemd-sysctl --cat-config | grep -n 'ptrace_scope'
test ! -e /etc/sysctl.d/90-anxious-yama.conf && echo absent
```

- On Oracle Linux 9 the expected source is `/usr/lib/sysctl.d/10-default-yama-scope.conf`
  (`kernel.yama.ptrace_scope = 0`).
- **Stop and report** if `yama` is missing, the current value is not 0 or 1, the file already exists,
  or another file sets `ptrace_scope` and sorts after `90-anxious-yama.conf` (for example
  `99-sysctl.conf` → `/etc/sysctl.conf`). The later file would put the old value back at the next boot.

### Apply

```sh
printf 'kernel.yama.ptrace_scope = 1\n' |
  install -o root -g root -m 0644 /dev/stdin /etc/sysctl.d/90-anxious-yama.conf
sysctl -p /etc/sysctl.d/90-anxious-yama.conf    # loads this file only, not the whole sysctl.d set
sysctl -n kernel.yama.ptrace_scope              # 1
/usr/lib/systemd/systemd-sysctl --cat-config | grep -n 'ptrace_scope'   # the 90-anxious line is the last
```

Use `sysctl -p <file>`, not `sysctl --system`: the latter re-applies every file, and would also reset any
live value that differs from its file. Record the UTC time and the outputs of both checks. After the next
planned reboot, check `sysctl -n kernel.yama.ptrace_scope` again (1). Do not reboot for this change.

**Never set 2 or 3.** 3 cannot be lowered without a reboot, and neither is needed.

### Rollback

```sh
sysctl -w kernel.yama.ptrace_scope=0
rm -- /etc/sysctl.d/90-anxious-yama.conf
sysctl -n kernel.yama.ptrace_scope              # 0
```

The #68 isolation does not depend on this value, so a rollback does not require a runner rollback.

### After it is applied

- PARITY: the `kernel.yama.ptrace_scope` row becomes **Must match** (1 on both sides).
- The development VM renames its file `/etc/sysctl.d/90-shell-pilot-yama.conf` to the production name,
  with the same value, in the #68 VM stage.

## 2. Seccomp profile (PM, production node)

**What.** `seccomp-runner-userns-v1.json` in this directory is the #64 pilot profile
(`shell-pilot/seccomp-userns.json`, moved here unchanged, SHA-256
`9a6594419eae4089e737c787597d2ed4950fd7e1031e371f490d5b2c935dff78`):

- the Docker v27.5.1 default profile;
- plus `clone`, `unshare`, `mount`, `umount2` and `pivot_root` allowed without `CAP_SYS_ADMIN`. This lets
  bubblewrap create its user, mount and PID namespaces;
- `clone3` still returns `ENOSYS` without `CAP_SYS_ADMIN`, so callers fall back to `clone`;
- `setns`, the new mount API (`fsopen`, `move_mount`, …), `bpf` and `perf_event_open` still require
  `CAP_SYS_ADMIN`. The runner container drops all capabilities, so they stay denied.

A `mount` outside a new user namespace still fails, because the container has no `CAP_SYS_ADMIN` in the
node's user namespace. The profile only stops seccomp from refusing the namespace setup itself.

**Where.** The runner Pod asks for
`seccompProfile: {type: Localhost, localhostProfile: profiles/anxious-runner-userns-v1.json}`. kubelet
(k3s, root dir `/var/lib/kubelet`) resolves that to
`/var/lib/kubelet/seccomp/profiles/anxious-runner-userns-v1.json`.

**Versioned name.** kubelet reads the file when it creates a container, and the Pod manifest gate cannot
see its contents. So the file is never changed in place. A changed profile gets a new name (`-v2`) and a
reviewed manifest change that names it, rolled out under the Runner Pod manifest gate
(`multi-user-runners/rollout/README.md`).

### Install (before the #68 window)

Copy the reviewed file from the approved checkout to the node, then as root:

```sh
src=/private-rollout/native-shell/seccomp-runner-userns-v1.json
dst=/var/lib/kubelet/seccomp/profiles/anxious-runner-userns-v1.json
echo '9a6594419eae4089e737c787597d2ed4950fd7e1031e371f490d5b2c935dff78  '"$src" | sha256sum -c -
test ! -e "$dst" || { echo 'already present: compare, do not overwrite' >&2; exit 1; }
install -d -o root -g root -m 0755 /var/lib/kubelet/seccomp /var/lib/kubelet/seccomp/profiles
install -o root -g root -m 0644 "$src" "$dst"
sha256sum "$dst"; stat -c '%U:%G %a' "$dst"      # the same digest; root:root 644
```

If `$dst` already exists with the same digest, it is installed: record that and continue. A different
digest stops the procedure.

The file has no effect until a Pod names it. If it is missing, kubelet refuses to create the runner
container (`CreateContainerError`), so the runner never starts without it.

### Rollback and removal

Keep the file through a #68 rollback: no other Pod uses it, and a later roll-forward needs it. Remove it only
after no runner Pod in any phase names it:

```sh
kubectl -n codex-runner get pods -l anxious.io/managed=user-runner -o json |
  jq -r '.items[] | select(.spec.securityContext.seccompProfile.localhostProfile? == "profiles/anxious-runner-userns-v1.json"
                            or any(.spec.containers[]; .securityContext.seccompProfile.localhostProfile? == "profiles/anxious-runner-userns-v1.json"))
         | .metadata.name'                      # must print nothing
rm -- /var/lib/kubelet/seccomp/profiles/anxious-runner-userns-v1.json
```

### Development VM

The pilot installed the same bytes as `profiles/shell-pilot-userns.json`. The #68 VM stage installs
`anxious-runner-userns-v1.json` by the procedure above. The pilot file is removed once no Pod uses it
(the pilot Pods are gone), and PARITY records both steps.

## 3. Controller inputs and existing bindings (PM, the #68 window)

### What the code does since #68

| Input | Old release (before #68) | New release (#68) |
| --- | --- | --- |
| Rollout values with `executor` | rendered into the controller `inputs.json` | `runner_rollout` refuses to render |
| Controller `inputs.json` with `executor` | new owners get v2 plans (sidecar) | `user_runner_prepare` refuses before any mutation |
| Controller `inputs.json` without `executor` | new owners get v1 plans: **no shell at all** | new owners get v1 plans: native shell |
| Stored v2 plan (`executor` set) | runner + shell-executor sidecar | `executor` read and ignored: runner only |
| Stored v1 plan | runner, no shell | runner, native shell |

A refused preparation creates no output directory and changes no registry row. The owner stays waiting
and the next controller run tries again, so a mismatched input blocks new owners but leaves no partial
state to repair.

The Pod image comes from `plan.image` in each binding, not from the release. An existing binding keeps
its old runner image until it is swapped. The old runner image has no bubblewrap and no native shell
setup, so under the new manifest it would start with no shell. **Every existing binding is swapped
in the window.**

Until #63 the controller input is `/etc/anxious-lookout/issue16/controller/inputs.json`, used by the host
`issue16-controller`. If #63 replaces that path first, the same rule applies to its replacement: it
carries no `executor`.

### Order

Run it after #61 and #63, or inside their window. Either way it is the same window procedure: one freeze,
one runner-Pod drain and one rollback point.

1. **Preconditions** (all hold before the first mutation):
   - The Runner Pod manifest gate: no Pending or Running runner Pod
     (`multi-user-runners/rollout/README.md`). Stop runners through `DELETE /api/research/codex/runner`, never
     by deleting the Pod.
   - Entry frozen. Every mutator stopped: the host `issue16-controller` timer and service,
     runner-controller scaled to 0 (before #61: the host observer), any external creator.
   - The section 2 profile installed, with its digest recorded.
   - The section 4 SELinux module installed (#68 order 4).
   - A DB backup. The rollback digests recorded: API/runtime/worker image, runner image, and the executor
     image named by each v2 plan.
   - A private root0600 copy of the current controller `inputs.json` in the operation directory
     (rollback input).
   - The binding list, recorded in the operation directory:
     ```sql
     SELECT owner_id, plan->>'image' AS image, plan->'version' AS version,
            plan->'executor'->>'image' AS executor_image
       FROM runner_bindings.bindings ORDER BY owner_id;
     ```
2. **Controller input.**
   - Remove `executor` from the private `values.json` and render with the new release's renderer. That
     renderer refuses a value set that still has the key.
   - Diff the new `controller-inputs.json` against the installed one. The only difference must be the
     removed `executor`.
   - Install it the same way as before (`multi-user-runners/rollout/README.md` "Installation"):
     root0600, no symlink, written to a temporary file in the same directory, then `mv -T`.
3. **Release images.** Deploy the new API/runtime/worker images by the existing rollout procedure. They
   render every Pod as one `runner` container, from a v1 or v2 plan alike.
4. **Existing binding runner images.** For each binding from step 1, use `ops.runner_binding_image`
   (`multi-user-runners/CUTOVER.md` section 6):
   - inputs: the registry fence, `oldPlanSha256` (computed in the new worker image), `newImage` = the
     reviewed #68 runner digest;
   - **no `executorImage` and no `executor`.** A v2 plan keeps its `executor` unchanged. The new release
     ignores it, and the rollback needs it to render the sidecar again;
   - `report`, review it, `apply` with the same inputs and the report;
   - record each new plan digest: the rollback swap needs it.
5. **Canary**, with runner-controller started again and entry open for the allowlisted canary owner only:
   - the Pod has exactly one container and names the section 2 profile;
   - a turn that runs a shell command;
   - stop: drain → DELETE → 404 → release.

   Then widen entry as in the rollout procedure.

Swapping the images is what moves owners onto the native shell. The order of steps 2 and 3 only decides
which owners could get a binding while one half is in place. With every mutator stopped from step 1 to
step 5, no binding is created in between. If one is anyway, for example when the input was changed early,
on the old release:

- a binding published from the new input on the **old** release is v1. That owner has no shell until the
  new release, and then the native shell. It needs only the step 4 image swap.
- a binding requested with the old input on the **new** release is refused, and published after step 2.

### Rollback

Same precondition: no Pending/Running runner Pod, entry frozen, every mutator stopped.

1. **Binding runner images back.**
   - For each binding swapped in step 4, run `ops.runner_binding_image` with the **new** release's
     worker image and the **old** runner digest from step 1. Its `oldPlanSha256` is the digest recorded
     in step 4.
   - A v2 plan still holds its `executor`, so the old release renders the sidecar again.
2. **Bindings published during the new release** are v1 (no `executor`). On the old release they have
   no shell. Either:
   - leave them without a shell, and record them; or
   - after step 4 below, opt them into v2 with the **old** release's `ops.runner_binding_image`
     (`executor` input). That is the #59 opt-in, which the new release refuses.

   Which one is a user decision at the time of the rollback.
3. **Controller input.** Install the step 1 copy of `inputs.json` (with `executor`) the same way:
   temporary file, `mv -T`, root0600.
4. **Release images.** Re-deploy the previous API/runtime/worker images.
5. Leave the section 2 profile and section 1 sysctl in place. Neither depends on the release.
6. Resume the mutators and lift the freeze on the old release.

If #68 shares a window with #61/#63, roll back in reverse order: #68 first, then #63 and #61 by their
own procedures.

## 4. Approval path and startup self-check (runner, reference)

The policy lives in the image, the expected values in runner code
(`backend/runner/native_policy.py`). Nothing is read from the workspace or a user setting.

| File in the image | Repository | Holds |
| --- | --- | --- |
| `/etc/codex/config.toml` (system layer) | `backend/runner/codex-etc/config.toml` | permission profile `native`: `:minimal` read, workspace write, deny `/control`, `/run/codex-broker`, `/state/codex`, `/state/home`; `approval_policy = "untrusted"`, `approvals_reviewer = "user"`, `default_permissions = "native"` |
| `/etc/codex/requirements.toml` (managed) | `backend/runner/codex-etc/requirements.toml` | `allowed_approval_policies = ["untrusted"]`, `allowed_approvals_reviewers = ["user"]`, `[features] unified_exec = false`, `deny_read` (second layer only) |

**Why these settings are load-bearing** (Codex 0.160.1 source, `rust-v0.160.1` `d27764b`):

- **Deny entries.** Under `untrusted`, a sandbox-denied command is retried without the sandbox, and
  without a second approval because the first one already passed (`core/src/tools/orchestrator.rs`,
  `should_bypass_approval`). Codex skips that retry only when the filesystem policy has a deny entry
  (`unsandboxed_execution_allowed`). The same check turns a model's `require_escalated` into an ordinary
  sandboxed run. The runner never sees the retry. Only the profile's deny entries stop it.
- **`unified_exec` pinned off in requirements.** 0.160.1 turns unified exec back on unless managed
  requirements pin it (`core/src/config/managed_features.rs`). The runner's former
  `-c features.unified_exec=false` had no effect; it never mattered before #68 because the shell tool was
  off. With it pinned off, `exec_command` is one-shot and `write_stdin` is not registered.
- **`allowed_approvals_reviewers = ["user"]`.** Otherwise an `auto_review` subagent could approve in the
  runner's place.
- **`proposedExecpolicyAmendment`** comes with almost every approval request: Codex derives it from the
  command. It is applied only through a rule-adding decision, and the runner has none.

**Startup self-check.** On every app-server launch (first start, tool reset, restart) and again
before every turn, the runner refuses (`policy_refused`, nothing runs) unless all of these hold:

1. The Codex and code-mode host binaries match the pinned SHA-256 (`install_codex.PINS`). This is
   what ties the checked inputs to the 0.160.1 behaviour above.
2. Both `/etc/codex` files are regular, root-owned, not writable by the runner, and equal to
   `native_policy.system_config('/state')` / `requirements('/state')`.
3. `CODEX_HOME/config.toml`, `CODEX_HOME/rules` and any `.codex` from the workspace up do not exist.
4. `configRequirements/read` reports the requirements applied (policies, reviewer, `unified_exec`).
5. `config/read` (cwd = workspace, with layers) reports:
   - the effective profile and policy keys exactly as in the system layer;
   - exactly one enabled system layer, `/etc/codex/config.toml`;
   - no other enabled layer setting a policy key (`permissions`, `default_permissions`,
     `approval_policy`, `approvals_reviewer`, `sandbox_mode`, `sandbox_workspace_write`);
   - every project layer disabled.
6. Every `thread/start` and `thread/resume` response reports `approvalPolicy: untrusted`,
   `approvalsReviewer: user`, `activePermissionProfile.id: native` and the workspace as `cwd`.

Not readable through the API: the compiled filesystem policy and the requirements `deny_read`. The
VM stage judges those by behaviour: the sandbox boundary of the first and every later execution, and
start refusal for damaged or missing configuration.

**Approval answers.** Only `accept` or `decline`, built from a two-value enum. No runner source names a
session-wide, rule-adding or network-amending decision; a unit test enforces this.

| Request | Declined without asking the API | Otherwise |
| --- | --- | --- |
| `item/commandExecution/requestApproval` | any unknown field; `kind` other than `command` (`writeStdin` included); `approvalId`, `additionalPermissions`, `networkApprovalContext` or `proposedNetworkPolicyAmendments` present; cwd outside the workspace; not the pending thread/turn; an item already decided; a finished turn | `runner_shell_authorize` with the request's command and cwd, before execution |
| `item/fileChange/requestApproval` | any unknown field; `grantRoot` present; not the pending thread/turn; an item already decided; a finished turn | `runner_shell_authorize`, before the patch is applied |
| any other server request (`item/permissions/requestApproval`, user input, elicitation) | always: JSON-RPC error, no grant | - |

Requests that arrive before the `turn/start` response wait (at most 32) and are decided once the turn
id is known. A request outside a turn is declined.

**Re-verification triggers.** The VM evidence holds for one combination: runner image digest
(Codex binaries and both `/etc/codex` files included), seccomp profile, SELinux module, bubblewrap
package and Pod manifest. A change to any of them is re-verified on the VM before it reaches production.

## 5. Codex version: what to recheck

Moving the Codex pin (`backend/runner/install_codex.py`) needs all of these rechecked against the
new source, then on the VM:

- `unsandboxed_execution_allowed` still depends on a deny entry, and the orchestrator still skips the
  unsandboxed retry under that condition;
- `require_escalated` is still lowered to a sandboxed run when deny entries exist;
- unified exec can still be pinned off only by managed requirements, and `write_stdin` is absent then;
- `allowed_approvals_reviewers` still exists and `user` still means the client;
- the approval request fields and decision names (the runner's allow lists in `native_policy.py`);
- the `configRequirements/read`, `config/read` and thread response fields the self-check reads;
- `thread/start.permissions` and `activePermissionProfile` (experimental in 0.160.1);
- project layers are still disabled for an untrusted cwd.

## 6. A `.codex` in the workspace blocks the runner

**Availability constraint (accepted, user decision pending).** If `/state/workspace/.codex`, a `.codex`
above it, `CODEX_HOME/config.toml` or `CODEX_HOME/rules` exists, the runner refuses to start Codex and
refuses every turn (`policy_refused`, section 4 check 3). It does not matter how the file got there.
The owner then cannot chat or run commands until an operator resolves it. This is deliberate
fail-closed behaviour: the approval policy is never relaxed to get past it, and the runner never
removes or renames the file itself. Auto-removal is refused because the file could be recreated
between the check and the run, an already loaded config could survive, and the owner's data would be lost.

**What usually prevents it (Codex 0.160.1 source, VM evidence still required).** For a writable root
that is also the cwd, Codex adds read-only carve-outs for `.git`, `.agents`, `.codex` and `.aws`, and
protects `.codex` even while it is missing (`protect_missing_dot_codex`, `protocol/src/permissions.rs`).
The cwd is the workspace, so a sandboxed command or `apply_patch` should not be able to create or
write `/state/workspace/.codex`. The VM stage checks that the protection holds for:

- a `.codex` that already exists (made before the run);
- renaming a directory into `.codex`;
- symlinks and path aliases (`.codex -> elsewhere`, `sub/../.codex`);
- `git clone <url> .` and archive extraction into the workspace root;
- a file change (`apply_patch`) that creates `.codex/config.toml`.

Each must fail inside the sandbox and must not be retried outside it (section 4). The cost of this
protection: work that needs a `.codex` or `.git` at the workspace root fails. A repository with
a `.codex` cloned into the workspace root fails; cloning it into a subdirectory works.
`/state/workspace/<repo>/.codex` is not a policy source: Codex reads `.codex` from the cwd upwards
only, and the runner checks the same places.

**What the owner sees.** The generic refusal: "정책에 따라 Codex 사용이 거부되었습니다." (status banner) and
"정책에 따라 이 요청이 거부되었습니다." (turn). Neither the owner nor the operator can tell a workspace
`.codex` from a broken deployment configuration from this text, and the runner logs no cause. A
cause code without file contents (for example `workspace_policy_file`) would need an API contract and
front change, so it waits for that decision.

**Recovery (operator, current method, not yet rehearsed on the VM).** Never relax the approval policy
or the configuration for it.

1. Ask the owner whether the `.codex` is theirs and whether to keep it. Do not open its files without
   that consent.
2. The blocked runner Pod stays up until runner-controller stops it as idle (`idleSeconds`). While it
   exists, a cluster admin renames the directory in place, so nothing is deleted:
   ```sh
   pod=$(kubectl -n codex-runner get pods -l anxious.io/owner=<owner-uuid> -o name)
   kubectl -n codex-runner exec "$pod" -c runner -- \
     sh -c 'test -e /state/workspace/.codex && mv -T /state/workspace/.codex /state/workspace/codex-dir-kept-$(date -u +%Y%m%dT%H%M%SZ)'
   ```
   Use the same `mv` for `CODEX_HOME/config.toml` or `rules` (`/state/codex/...`) only after
   confirming what wrote them; those are not owner files, so report them to back first.
3. Let the runner stop and release normally. Never delete the Pod by hand. The next entry starts a
   new Pod, and the startup check runs again from the beginning.
4. If the Pod is already gone, the next entry recreates it and it blocks again; repeat step 2 on that Pod.

## 7. Runner Pod and image (#68 order 4)

- One container, `runner`, for the runner, app-server and sandboxed commands. No init container and no
  shell-executor sidecar; a stored v2 plan's `executor` is ignored.
- Pod `seccompProfile`: `Localhost`, `profiles/anxious-runner-userns-v1.json` (section 2).
- Container: UID/GID 10001, `allowPrivilegeEscalation: false`, read-only root filesystem, all
  capabilities dropped (unchanged). bubblewrap runs in a user namespace without any capability.
- Resources (provisional, `runner_kube.RESOURCES`): requests 200m CPU / 320Mi, limits 1500m / 1280Mi,
  the #59 runner (500m / 768Mi) plus executor (1 / 512Mi) limits. The VM stage measures the
  native shell under load and sets the final value; PARITY records the difference from #59.
- Image: Debian bookworm `bubblewrap` (0.8.0) and `/usr/local/bin/codex-linux-sandbox -> codex`.
  bubblewrap 0.8.0 has no `--argv0`, so Codex reaches its Linux sandbox helper through this name.
  The link is part of the image evidence chain.
- `/etc/codex/config.toml` and `requirements.toml` (section 4).
- Pod manifest change: the Runner Pod manifest gate applies (section 3).

