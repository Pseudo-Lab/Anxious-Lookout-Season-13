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

Still to come with the Pod specification commit (#68 order 4): the SELinux policy for `container_t`, the
single resource limit, the `codex-linux-sandbox` link in the image and the read-restricting permission
profile. They will be added here as sections 4 to 7.

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
