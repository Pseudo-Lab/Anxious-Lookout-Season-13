# Native shell runner: host and rollout procedures (#68)

Issue [#68](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/68). Since #68 the runner Pod has
one container. Codex 0.160.1 runs native shell commands, `apply_patch` and unified_exec in a bubblewrap
(`workspace-write`) sandbox there; the shell-executor sidecar and the MCP `shell` bridge are gone.

This file holds the procedures that touch a node or the controller inputs. Each one says who runs it.
Nothing here runs from a render, a test or an agent session on production.

| Section | What | Production executor | When |
| --- | --- | --- | --- |
| 1 | `kernel.yama.ptrace_scope=1` | user (read-only node for agents) | before the #68 window: a section 3 precondition |
| 2 | Seccomp profile `seccomp-runner-userns-v1.json` | PM after review | before the #68 window |
| 3 | Controller inputs without `executor`, binding runner images | PM after review | the #68 window |
| 4 | Runner approval path and startup self-check (reference) | nobody: runner code | every app-server launch and turn |
| 5 | Codex version: what to recheck | back, review | before any Codex pin change |
| 6 | A `.codex` in the workspace blocks the runner: availability and recovery | operator, with the owner | when it happens |
| 7 | Runner Pod and image (#68 order 4) | reference | the #68 window |
| 8 | SELinux module `anxious_runner_bwrap` for `container_t` | user (production node) | before the #68 window: a section 3 precondition |

## 1. `kernel.yama.ptrace_scope=1` (user, production node)

**Why.** The sandboxed shell cannot see or read app-server because bubblewrap puts it in its own PID and
user namespace (#68 requirement 2). That holds with `ptrace_scope` 0 or 1, and the development VM
measurement checks it at both. Even so, **1 on the production node is a prerequisite of the #68 production
apply** (section 3, preconditions; user decision 2026-11-05): while app-server and the sandboxed shell share
one container, 1 is what actually narrows memory access between processes on the node. If the runner is
later split into separate containers, this prerequisite is reviewed again. With 1, a process can trace only its own descendants
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

The #68 isolation does not depend on this value, so a rollback does not force a runner rollback. It does
remove a #68 production prerequisite: report it to PM, who decides whether #68 stays applied.

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

**Structure seen on the development VM (2026-11-05, read-only).** Two paths, with different jobs:
- Runner Pods are rendered and reconciled by the in-cluster `runner-control/runner-controller` (runtime image,
  `ops.runner_controller_service`). It reads no `inputs.json`: it renders each Pod from the binding's stored plan.
  Swapping the runtime image therefore changes the Pods of **every** owner at their next start. That is why the
  window keeps entry frozen until every binding is swapped.
- `inputs.json` is read only when a **new** owner is prepared, by the host `issue16-controller` service (the
  provisioner image's `worker.py controller`). On the development VM that unit and its timer, like the other
  `issue16-*` units, are installed but **disabled**.
- Two allowlists, both named `CODEX_USER_RUNNER_ALLOWLIST`: the API's gates new runner requests
  (`app/user_runners.py`); the controller's own gates Pod creation for requests already waiting in
  `runner_runtime.registry` (`ops/runner_controller_service.py`: `reconcile` skips an owner that is not
  `allowed`, `ensure` requires it). Narrowing only the API's leaves already-waiting requests to the controller.
- Each binding has its own runner and executor images (two different pairs on the development VM, neither equal
  to `inputs.json`). The rollback record keeps them per binding.

Before the production window, the user checks the same points read-only, because "every mutator stopped" names
different things if production differs: whether the host `issue16-*` units and timers are enabled; the
`runner-controller` replicas and image; the API and controller allowlists; pending runner requests in the
database; and the runner and executor image of every binding.

### Order

Run it after #61 and #63, or inside their window. Either way it is the same window procedure: one freeze,
one runner-Pod drain and one rollback point.

1. **Preconditions** (all hold before the first mutation):
   - The Runner Pod manifest gate: no Pending or Running runner Pod
     (`multi-user-runners/rollout/README.md`). Stop runners through `DELETE /api/research/codex/runner`, never
     by deleting the Pod.
   - Entry frozen. Every mutator stopped: the host `issue16-controller` timer and service,
     runner-controller scaled to 0 (before #61: the host observer), any external creator.
   - No waiting or running runner request of any owner. With real users this is a user step before the
     window: announce the window, wait for running turns to finish, then stop runners through
     `DELETE /api/research/codex/runner`. Check read-only that `runner_runtime.registry` has no row in
     `provisioning`, `capacity_wait`, `ready`, `runner_unreachable` or `stopping` and
     `runner_termination.stops` none open. If any remains, the window does not start.
   - Both allowlists recorded (API and runner-controller `CODEX_USER_RUNNER_ALLOWLIST`): the step 5
     narrowing and the final widening restore them.
   - `kernel.yama.ptrace_scope` is 1 on the node (section 1, applied by the user): `sysctl -n
     kernel.yama.ptrace_scope` prints 1 and `systemd-sysctl --cat-config` ends with the
     `90-anxious-yama.conf` line. Record both outputs. If it is 0, stop: the window does not start.
   - The section 2 profile installed, with its digest recorded.
   - The section 8 SELinux module installed (the version the development VM ended with).
   - `thread-check.sh` (section 7, "What catches what") run on the reviewed #68 runner digest prints
     `verdict pass`. Record the output and its SHA-256. Any other verdict: the window does not start.
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
4. **Existing binding runner images: the canary owner only.** Swap the canary owner's binding first;
   every other binding waits for step 6. For each swap, use `ops.runner_binding_image`
   (`multi-user-runners/CUTOVER.md` section 6):
   - inputs: the registry fence, `oldPlanSha256` (computed in the new worker image), `newImage` = the
     reviewed #68 runner digest;
   - **no `executorImage` and no `executor`.** A v2 plan keeps its `executor` unchanged. The new release
     ignores it, and the rollback needs it to render the sidecar again;
   - `report`, review it, `apply` with the same inputs and the report;
   - record each new plan digest: the rollback swap needs it.
5. **Canary**, with runner-controller started again and entry open for the canary owner only. Before the
   controller starts: set **both** allowlists (API and runner-controller) to the canary owner alone, and
   check again that no other owner has a waiting request. While the canary runs, check after every step that
   `codex-runner` holds no runner Pod but the canary's. Another owner's Pod would be an old binding rendered by
   the new release: stop and report, do not delete it (runners stop only through the API).
   The canary passes only if all of these hold, in this order:
   - the Pod has exactly one container and names the section 2 profile;
   - the runner log has no `runner_policy_check` line with `"result": "refused"` (the startup self-check,
     section 4, passed);
   - the first turn creates its thread (`thread/start` succeeded: the turn reaches the model and
     completes). A configuration that loads but cannot create a thread fails here for every owner;
   - in that turn one shell command asks for approval before it runs, and its output comes back from
     the sandbox;
   - stop: drain → DELETE → 404 → release.

   If any check fails: stop, swap the canary binding back (rollback step 1, for that binding only), and
   report. No other binding is swapped.
6. **Remaining binding runner images**, with runner-controller stopped again: the step 4 swap for every
   other binding from step 1. Then widen entry as in the rollout procedure: restore both allowlists to the
   values recorded in step 1, then start runner-controller. The development VM rehearsal (V7a) does steps
   1–5 the same way and keeps entry narrowed until V7b.

Swapping the images is what moves owners onto the native shell. The order of steps 2 and 3 only decides
which owners could get a binding while one half is in place. With every mutator stopped from step 1 to
step 4 and entry open to the canary owner only in step 5, no other binding is created in between. If one is anyway, for example when the input was changed early,
on the old release:

- a binding published from the new input on the **old** release is v1. That owner has no shell until the
  new release, and then the native shell. It needs only the step 4 image swap.
- a binding requested with the old input on the **new** release is refused, and published after step 2.

### Rollback

Same precondition: no Pending/Running runner Pod, entry frozen, every mutator stopped.

1. **Binding runner images back.**
   - For each binding swapped in steps 4 and 6, run `ops.runner_binding_image` with the **new** release's
     worker image and the **old** runner digest from step 1. Its `oldPlanSha256` is the digest recorded
     at its swap.
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
| `/etc/codex/config.toml` (system layer) | `backend/runner/codex-etc/config.toml` | permission profile `native`: `:minimal` read, workspace write, deny `/control`, `/run/codex-broker`, `/state/home` (not `/state/codex`: see "CODEX_HOME" below); `approvals_reviewer = "user"`, `default_permissions = "native"`; `[agents] enabled = false`; `[shell_environment_policy.set] HOME = "/usr/local/share/anxious-shell-home"`. No `approval_policy`: 0.160.1 refuses `untrusted` in any config file and the app-server does not start |
| `/etc/codex/requirements.toml` (managed) | `backend/runner/codex-etc/requirements.toml` | `allowed_approval_policies = ["untrusted"]`, `allowed_approvals_reviewers = ["user"]`, `[features] unified_exec = false`, `multi_agent_v2 = false`, `deny_read` (second layer only) |

**Why these settings are load-bearing** (Codex 0.160.1 source, `rust-v0.160.1` `d27764b`):

- **Deny entries.** Under `untrusted`, a sandbox-denied command is retried without the sandbox, and
  without a second approval because the first one already passed (`core/src/tools/orchestrator.rs`,
  `should_bypass_approval`). Codex skips that retry only when the filesystem policy has a deny entry
  (`unsandboxed_execution_allowed`). The same check turns a model's `require_escalated` into an ordinary
  sandboxed run. The runner never sees the retry. Only the profile's deny entries stop it. The three
  entries `/control`, `/run/codex-broker` and `/state/home` keep that condition true.
- **CODEX_HOME (`/state/codex`) is not denied, and cannot be.** Codex always adds `CODEX_HOME/tmp/arg0`
  (its execve wrapper and `codex-linux-sandbox` links, created at app-server start) as a readable root
  (`core/src/config/permissions.rs`, `get_readable_roots_required_for_codex_runtime`). A managed
  `deny_read` on it or an ancestor fails the configuration load at `thread/start` ("not in the allowed set
  all managed deny_read restrictions"). A profile deny on an ancestor is masked by bubblewrap after every
  read bind (`linux-sandbox/src/bwrap.rs`), so the helper cannot be executed and `thread/start` fails
  (`execvp … codex-linux-sandbox: Permission denied`). Both were found on `68-755f65c`; that image is not
  used. CODEX_HOME's protection is that the profile never reads it: the only read roots are `:minimal`
  and the workspace (compared exactly by self-check 5), the only workspace root is the workspace and no
  extra writable root exists (self-check 6). A sandboxed command sees nothing of CODEX_HOME but
  `tmp/arg0`. This rests on CODEX_HOME holding no credential: the broker token lives under
  `/run/codex-broker` (denied) and the V6 measurement checks that no token file stays in CODEX_HOME.
  `config_tests/test_native_policy.py::test_no_deny_entry_covers_the_codex_runtime_root` refuses a deny
  entry on `tmp/arg0` or an ancestor of it (`/`, `/state`, `/state/codex`, `/state/codex/tmp`).
- **`unified_exec` pinned off in requirements.** 0.160.1 turns unified exec back on unless managed
  requirements pin it (`core/src/config/managed_features.rs`). The runner's former
  `-c features.unified_exec=false` had no effect; it never mattered before #68 because the shell tool was
  off. With it pinned off, `exec_command` is one-shot and `write_stdin` is not registered.
- **`allowed_approvals_reviewers = ["user"]`.** Otherwise an `auto_review` subagent could approve in the
  runner's place.
- **`proposedExecpolicyAmendment`** comes with almost every approval request: Codex derives it from the
  command. It is applied only through a rule-adding decision, and the runner has none.
- **No sub-agents: `[agents] enabled = false` and `multi_agent_v2` pinned off.** With the runner's
  `features.multi_agent=false` and `features.multi_agent_v2=false` alone, 0.160.1 still offered the
  `collaboration` tools (`spawn_agent`, `send_message`, `followup_task`, `wait_agent`, `list_agents`,
  `interrupt_agent`): when no config override applies, the version comes from the model's metadata
  (`Config::multi_agent_version_for_model`, `core/src/config/mod.rs:1606`). The override is
  `multi_agent_v2` on → v2, else `agents.enabled = false` → disabled (`:1586`), and it wins on every path:
  new threads (`thread_manager.rs:1754`), each turn and resumed threads (`session/mod.rs:4249`). Disabled
  means the handlers are never registered (`tools/spec_plan.rs:1298`, `collab_tools_enabled`), and the
  registry answers a call to an unregistered name with an "unsupported call" error
  (`tools/registry.rs:554`). Hiding a tool is not enough: the registry dispatches by name, not by
  exposure, which is why a direct call to the nested-only `exec_command` was executed (local diagnostic,
  2026-11-05). `multi_agent_v2` is pinned in requirements because it would take precedence over
  `agents.enabled`.
- **HOME for sandboxed commands.** app-server keeps `HOME=/state/home`, which is denied in the sandbox.
  bash then printed `/state/home/.bashrc: Permission denied` (or `.bash_profile` for `bash -l`) before
  every command, into the owner's conversation and the model input. `shell_environment_policy.set.HOME`
  gives commands an empty, root-owned, 0555 directory in the image (build gate: owner, mode, empty, not a
  link). Not the workspace: a start-up file the model wrote there would run, unseen and unapproved, before
  every later command. Owner-visible effect: `~` is not writable inside commands (`pip install --user`,
  `git config --global` and similar fail); before this change it was not even readable.
  `thread-check.sh` checks the final HOME of `sh` and of `bash -l` and that no start-up file can be created.

**Tools the model is offered** (0.160.1, runner configuration; read from the first model request's
`additional_tools` input item, local diagnostic 2026-11-05). This list is part of the #68 boundary.

| Namespace | Tool | Reaches |
| --- | --- | --- |
| `functions` | `exec` (custom, code mode: JavaScript) | nested tools below, through the same tool registry and handlers as a direct call |
| `functions` | `wait` | a running `exec` cell |
| `functions` | `request_user_input`, `request_user_input_async` | a client request the runner always refuses (table below) |
| `clock` | `sleep` | time only |
| nested in `exec` | `exec_command` | the shell: approval request, then bubblewrap (`native` profile) |
| nested in `exec` | `apply_patch` | file changes: approval request, then the sandboxed filesystem |
| nested in `exec` | `clock__curr_time` | time only |

Not offered and not registered: the `collaboration` namespace (above); `write_stdin` (unified exec
pinned off); web search (`web_search = "disabled"`); `view_image` and `create_goal` / `get_goal` /
`update_goal` (requirements pin `view_image = false`, `goals = false`: the handler and the goals
extension exist only when the feature is on, `core/src/tools/spec_plan.rs` ViewImageHandler,
`app-server/src/extensions.rs:85`). On `68-8548f13` both were still offered (nested in `exec`).
`view_image` read through the sandboxed filesystem with the turn profile (`view_image.rs:159–177`); goals
can start automatic continuation turns.

**Owner-visible effects of this tool set** (also for the PR description):
- **From #68 the model cannot look at images in the workspace** (`view_image` off). Before #68 it most likely
  could: `main` runs the same 0.160.1 without disabling it, and the feature defaults to on. That is from source
  and arguments; the pre-#68 tool list was not measured. The user decides whether to keep it off
  (to turn it back on: the requirements line, `native_policy.requirements`, the tests and this table).
- No sub-agents and no goals (automatic continuation turns).
- `~` is not writable inside commands (HOME above).

**Code mode (`exec`)**, 0.160.1 source:
- The JavaScript runs in a separate `codex-code-mode-host` process (pinned binary). app-server starts it
  with no arguments (stdio protocol), its own cwd and environment minus a few token variables
  (`code-mode/src/remote_session/connection.rs:157–199`). The process runs as UID 10001 like app-server.
  It is **not** in bubblewrap.
- Inside it, the script is a bare V8 isolate. `console`, `Atomics`, `SharedArrayBuffer` and
  `WebAssembly` are deleted. Only `tools`, `ALL_TOOLS`, `setTimeout`/`clearTimeout`, `text`/`image`/`audio`/
  `generatedImage`, `store`/`load` (memory), `notify`, `yield_control` and `exit` are added
  (`code-mode-runtime/src/runtime/globals.rs:14–48`). Every static or dynamic import throws
  (`runtime/module_loader.rs:225`). There is no file, network, process or environment API.
- Every `tools.<name>(...)` call goes back to core as a nested call with its own
  `call_id = exec-<uuid>` (`core/src/tools/code_mode/delegate.rs:323`). From there it takes the same
  dispatch as a direct call (`tools/parallel.rs` → `router` → `registry`), so a nested `exec_command`
  gets its own approval request and the bubblewrap sandbox. `Promise.all` runs nested calls in
  parallel; each one still asks separately.
- Limits: no V8 heap or CPU limit in this source. A runaway script is bounded by the Pod's limits and
  ends with the turn. The isolation then rests on V8's own boundary. Breaking out of the isolate would
  give the host's privileges (UID 10001, no sandbox), the same as app-server.
- Since `1949f45` (#7, 2026-10-10) the runner has started app-server with `features.code_mode_host=true`.

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
   `approvalsReviewer: user`, `activePermissionProfile.id: native`, the workspace as `cwd`, the workspace
   as the only `runtimeWorkspaceRoots` entry, and `sandbox` of type `workspaceWrite` with no extra
   `writableRoots` and `networkAccess: false`.

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
- which CODEX_HOME paths Codex makes readable (0.160.1: only `tmp/arg0`), whether a deny on an ancestor
  of them still fails `thread/start`, and what `tmp/arg0` holds (0.160.1: `.lock` and four links to
  `/usr/local/bin/codex`; `thread-check.sh` fails on anything else). CODEX_HOME stays outside every read
  root either way;
- `require_escalated` is still lowered to a sandboxed run when deny entries exist;
- unified exec can still be pinned off only by managed requirements, and `write_stdin` is absent then;
- `allowed_approvals_reviewers` still exists and `user` still means the client;
- the approval request fields and decision names (the runner's allow lists in `native_policy.py`);
- the `configRequirements/read`, `config/read` and thread response fields the self-check reads;
- `thread/start.permissions` and `activePermissionProfile` (experimental in 0.160.1);
- project layers are still disabled for an untrusted cwd;
- the workspace-root `.codex` is still protected while missing, and `.git` once present;
- what Codex runs outside the sandbox in the cwd (git metadata), if git is in the image;
- **the tools the model is offered** (section 4 table): read them from a model request's `additional_tools`
  input item, nested tools from the `exec` description. Every new tool needs its access path stated
  (sandboxed or not, approval or not) before the pin moves;
- the multi-agent version order (`multi_agent_v2` → `agents.enabled` → model metadata) and that disabled
  still means "handlers not registered";
- that `shell_environment_policy.set` still reaches commands and that bash's start-up files still come from
  `HOME`;
- **`untrusted` is still supported.** 0.160.1 refuses `approval_policy = "untrusted"` in a config file ("retired",
  `UnsupportedUntrustedApprovalPolicyError`); the runner reaches it only through the thread/start and thread/resume
  override, the untrusted project and the managed `allowed_approval_policies`. Before moving the pin, check first
  that the override still exists and still yields `untrusted` in the thread response.

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

**What the operator sees.** The runner logs one line per refusal with a fixed check name, never file
contents, tokens, paths or user data:

```sh
kubectl -n codex-runner logs <runner-pod> -c runner | grep runner_policy_check
# runner_policy_check {"result": "refused", "check": "local.workspace_dot_codex"}
```

| Check name | Means | Who fixes it |
| --- | --- | --- |
| `local.workspace_dot_codex`, `local.parent_dot_codex` | a `.codex` in or above the workspace | operator with the owner (below) |
| `local.codex_home_config`, `local.codex_home_rules` | a user config or rules file in CODEX_HOME | report to back first |
| `binary.*`, `file.*`, `requirements.*`, `config.*`, `rpc.*`, `thread.*` | the image, its `/etc/codex` files or what Codex reports applied | deployment: back and review, never the owner |

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
  bubblewrap 0.8.0 has no `--argv0`. Inside app-server, Codex runs its sandbox helper from
  `CODEX_HOME/tmp/arg0`. Outside it (`codex sandbox`, used by `thread-check.sh` and the V3 probe) Codex finds
  the helper by this name on PATH. Without the link that path fails with
  `bwrap: execvp codex-linux-sandbox: No such file or directory` (measured 2026-11-05 on `68-8548f13`
  with the link removed; `thread/start` still worked). The link is part of the image evidence chain.
- `/usr/local/share/anxious-shell-home`: empty, root, 0555; HOME for sandboxed commands (section 4).
- `/etc/codex/config.toml` and `requirements.toml` (section 4).
- **Build gate.** The `runner` stage ends with `RUN --network=none python -m runner.image_check` as UID 10001, so every
  build, production included, fails unless the image as built passes these checks:
  - pinned Codex binaries;
  - bubblewrap and the sandbox link;
  - `/etc/codex` root 0755 and both policy files (`native_policy.check_files`);
  - no git;
  - `/app` readable;
  - the pinned Codex accepts the image's config: an offline `codex app-server` (no login) started in the
    runner's own layout (`/state/home`, `/state/codex`, `/state/workspace`, removed again; the image ships
    `/state` empty) answers configRequirements/read and config/read, and both pass the runner's own checks.

  Unit tests cannot see file modes in the built image. The #68 V1 check found `/etc/codex` at 644 this way.
  Until `68-755f65c` the gate used a throwaway home under `/tmp`, so a path rule that only bites under
  `/state` could not show there.
- **What catches what.** A configuration can load and still fail to create a thread (`68-755f65c`:
  CODEX_HOME denied, section 4). Four checks, each with its limit:

  | Check | Runs | Catches | Cannot catch |
  | --- | --- | --- | --- |
  | Unit tests (`config_tests/test_native_policy.py`) | every push, back and review | policy files equal to the checked values; no deny entry on `CODEX_HOME/tmp/arg0` or an ancestor (`test_no_deny_entry_covers_the_codex_runtime_root`); read and write roots exactly `:minimal` and the workspace (`test_codex_home_is_outside_every_read_and_write_root`, the `check_config` refusals); `check_thread` refusals for another policy, profile, cwd, workspace root, writable root or network | anything about the built image or the real Codex |
  | Build gate (`runner.image_check`) | every image build | binaries, modes, packages; the real Codex loading `/etc/codex` in the `/state` layout | thread creation: a build `RUN` has no user namespace, so bubblewrap and `thread/start` always fail there, and the build is not given more privilege |
  | Pre-release check (`thread-check.sh <image>`) | review, on every runner image before V1 import and before the section 3 window | in one local container with the runner's UID, `/state`, `/control` and `/tmp` mounts, read-only root, no capabilities, no-new-privileges, seccomp profile and no network: bubblewrap, a workspace write, fake CODEX_HOME state, `/state/home` and `/control` unreadable (also through workspace symlinks), the config checks, `thread/start` with `check_thread`, the `tmp/arg0` contents. `verdict pass` or `verdict fail <check>` | SELinux `container_t` (V3, on the VM); what a command sees during a real turn (V4); the local AppArmor profile is the host's userns profile, not the node's |
  | Runner start (section 4 self-check) and the section 3 canary | every app-server start and turn; once per window | everything above on the real node | nothing earlier: when it fires, the image is already deployed |
- Pod manifest change: the Runner Pod manifest gate applies (section 3).
- **Difference from the issue design.** The #68 issue body sketches control UID 10001 and agent UID
  10002 in separate places. This branch keeps one `runner` container with UID 10001, and the sandbox
  (bubblewrap user, mount and PID namespaces, restricted read profile) is the boundary. Whether to split
  the containers is decided after the VM evidence (PM agreement 2026-11-05; the exposure comparison
  is on the issue).
- **No git in the image** (the former executor image had none either, and the #64 pilot tasks did not
  use git). If git is added later, review first:
  - a workspace-root `.git` is read-only in the sandbox once it exists, so commits to a root
    repository fail (subdirectory repositories are unaffected);
  - Codex also runs git in the cwd **outside the sandbox** for turn metadata. It disables
    `core.fsmonitor` and hooks there, but other repository-configured programs need review.


## 8. SELinux module `anxious_runner_bwrap` (user, production node)

**Why.** Under SELinux `container_t` the sandbox does not start: bubblewrap's `--dev /dev` mounts a new devpts
instance, and the node policy denies `container_t` `devpts_t:filesystem mount` (V3, 2026-11-05: 10 AVCs of that
one kind, `thread/start` failed with `Can't mount devpts on /newroot/dev/pts`). Codex 0.160.1 always passes
`--dev /dev`. The distribution policy grants exactly this rule to `container_userns_t`, a type Pod Security
restricted does not allow. User decision 2026-11-05 (#68): a module for `container_t` with at most two rules;
no `container_engine_t`, `spc_t` or `container_userns_t`; no proc mount; no `dontaudit`.

**The module** (`selinux/`, no macros, so the compiled module adds exactly these allows):

| Version | Rules | When |
| --- | --- | --- |
| 1 | `allow container_t devpts_t:filesystem mount;` | observed (V3) |
| 2 | version 1 and `allow container_t tmpfs_t:filesystem remount;` | only if V3b shows the mask tmpfs `--remount-ro` denied (predicted: the tmpfs superblock is `tmpfs_t`, which `container_t` may mount and unmount but not remount) |

`selinux/build.sh <out>` builds both with checkpolicy/libsepol 3.6 (Oracle Linux 9, as the nodes). The build is
reproducible: version 1 `68270c77…f6e6`, version 2 `3f4fc25c…beea`. `vm/selinux-module.sh` pins these.

**Not allowed: a fresh `/proc` in the sandbox.** When Codex's preflight cannot mount a fresh procfs (`Can't mount
proc … Operation not permitted` or `Permission denied`), it reruns the sandbox without `/proc`
(`linux_run_main.rs`). Why it cannot, kept apart:
- **Measured (local Docker, AppArmor, 2026-11-05):** bubblewrap replaying the preflight gets `Can't mount proc on
  /newroot/proc: Operation not permitted` (EPERM) and succeeds without `--proc`. The container's `/proc` has
  paths mounted over it (`/proc/kcore`, `/proc/keys`, `/proc/sys`, … — the runtime's masked paths), and the
  kernel refuses a new procfs in a user namespace while that is so. No SELinux there: the kernel refuses.
- **Recorded in V3b (development VM, `container_t`):** the same replay's error text and whether a proc mount AVC
  appears in the same time window (EACCES with an AVC would mean SELinux refused first).
- **Prediction, not measured:** that allowing `proc_t:filesystem mount` in SELinux would still leave the sandbox
  without `/proc` on the node, because the same kernel check applies to the Pod's masked `/proc`. Not tested:
  opening that rule is outside the user's decision. The
mask, read and write limits are the same in that run (measured locally and in V3b/V3c). Each command's preflight
leaves one proc mount denial in the audit log. Owner-visible effect of a sandbox without `/proc` (local
measurement 2026-11-05, the same fallback): `/dev/stderr`, `/dev/stdin`, `/dev/fd/*` and bash process
substitution fail, and `/proc/self`, `/proc/cpuinfo` and `/proc/meminfo` are absent; `nproc`, Python's
`subprocess(close_fds=True)`, `multiprocessing` and `os.cpu_count()` work; `ps` and `free` are not in the image.

**Residual risk accepted by the user (2026-11-05), measured facts:**
- The rules apply to every `container_t` process on the node, not only the runner. On the development VM,
  `container_t` Pods without a seccomp filter (`Seccomp: 0`) run today: coredns, metrics-server,
  local-path-provisioner and the openebs/topolvm controllers. `user.max_user_namespaces` is 29738 and k3s sets
  no seccomp default.

**Residual risk, documentation only (not measured; V3x was not run):**
- That such a process could only mount devpts or remount tmpfs inside its own user and mount namespace (a new
  pty instance; only tmpfs it mounted itself) and not change other namespaces or existing mounts rests on kernel
  rules, not on measurement. It is an unverified claim.
- A node-wide seccomp default is a follow-up issue, not a #68 precondition; its effect has to be checked on its
  own (runtime profile, explicit Pod settings, running containers).

**Production procedure** (user; read-only first):
1. Before: on the node, list the `container_t` Pods and their seccomp state, `user.max_user_namespaces`, the
   kubelet seccomp default, and `semodule -lfull` (no module named `anxious_runner_bwrap`). Record them.
2. Install the version the development VM ended with: check the `.pp` SHA-256, then
   `semodule -X 400 -i anxious_runner_bwrap-<v>.pp`. Check `semodule -lfull` shows it at priority 400 and record
   `sha256sum /sys/fs/selinux/policy`.
3. Check: `thread-check.sh` is local; on the node, the section 3 canary (`thread/start` and a sandboxed command)
   is the check.
4. Remove (rollback): `semodule -X 400 -r anxious_runner_bwrap`, then `semodule -lfull` no longer lists it. The
   runner cannot start a sandbox without the module; roll the runner back first (section 3 rollback).
5. PARITY records the module, its version and SHA-256 on both sides.

On the development VM the same steps are `vm/selinux-module.sh install 1 | upgrade 2 | remove`, with the
before/after module list, stored module and loaded policy recorded; V9 removes it.
