# Shell-surface A/B pilot (#64, development VM only)

Four paid Codex turns approved by the user: tasks `fix-failing-test` and `slow-suite` (#58 F tasks 1
and 6) under A (current runner shell surface) and B (native shell middle option), once each.
Budget cap for the whole pilot: 2.4M input and 100k output tokens; the harness refuses to start
past it and interrupts a turn that would cross it.

| | A (current) | B (middle option) |
| --- | --- | --- |
| Tools | MCP `shell` bridge → ShellBroker → shell-executor sidecar; native shell and unified_exec off | native shell, `apply_patch`, unified_exec |
| Sandbox | `read-only` | `workspace-write` (bubblewrap, seccomp profile `seccomp-userns.json`) |
| Limits | 30 s wall, 64 KiB output per command (executor) | Codex defaults |
| cwd | empty native-work directory; commands run in `/state/workspace` | the workspace |
| Image | `anxious-dev-runner:50ac30b` + `anxious-dev-shell-executor:50ac30b` | `agent-b.Dockerfile` (runner image + bubblewrap) |

The same model, Codex 0.160.1, prompts and fixtures are used in both. Fixtures use stdlib
`unittest` because the executor image has no pytest. The graders restore the original tests
(and add hidden ones for task 1) before running them, so editing tests cannot pass.

## Files

- `pilot_tasks.py`: fixtures, prompts and graders (`test_pilot_tasks.py` checks that the fixtures fail as given, pass with the intended fix, and reject edited tests).
- `harness.py`: `selfcheck --config A|B` (no model call), `run --task … --config …`, `budget`.
- `pilot.yaml`: namespace `shell-pilot`, PVC `pilot-login`, Pods `pilot-login`, `pilot-a` and `pilot-b` (B on SELinux
  `spc_t`: `container_t` denies bubblewrap's devpts mount, measured; production would need a dedicated policy).
- `seccomp-userns.json`: the Docker v27.5.1 default profile plus `clone`, `unshare`, `mount`, `umount2`, `pivot_root` allowed without CAP_SYS_ADMIN (bubblewrap's user, mount and PID namespaces).
- `agent-b.Dockerfile`: the B image.

## Development VM preparation (back)

1. `kernel.yama.ptrace_scope=1` in the guest (`/etc/sysctl.d/90-shell-pilot-yama.conf`, applied with `sysctl --system`). Record it in PARITY as "May differ" (development VM only).
2. Install `seccomp-userns.json` as `/var/lib/kubelet/seccomp/profiles/shell-pilot-userns.json` (root, 0644).
3. Build the B image in the guest Docker, then import it into containerd `k8s.io`. Pin `pilot-b` to the imported digest.
4. `kubectl -n shell-pilot create configmap pilot-src --from-file=harness.py --from-file=pilot_tasks.py`, then apply `pilot.yaml`.
5. `selfcheck` for A and B. These steps make no model call and use no credentials.

## Operator login (user, one line on the development server)

```sh
ssh -t -i ~/.ssh/anxious-back-devvm -o IdentitiesOnly=yes -p 22107 opc@127.0.0.1 'sudo /usr/local/bin/k3s kubectl -n shell-pilot exec -it pilot-login -- codex login --device-auth'
```

- It prints a URL and a one-time code. Open the URL on any device, sign in to the operator account, and enter the code.
- No token is printed. `auth.json` stays on the `pilot-login` volume (0600, UID 10001).
- For a ChatGPT workspace account, the workspace admin enables device code login and Codex local use first.
- Then back exports only the access token into the Secret the pilot Pods mount at `/access` (OpenEBS LVM refuses a
  second mount of the login volume, and the refresh token never leaves `pilot-login`). The token passes through
  the pipe only, never a terminal or file:

  ```sh
  set -o pipefail
  kubectl -n shell-pilot exec pilot-login -- env PYTHONPATH=/app python -B /pilot/src/harness.py export |
    kubectl -n shell-pilot create secret generic pilot-access --from-file=access.json=/dev/stdin >/dev/null ||
    kubectl -n shell-pilot delete secret pilot-access --ignore-not-found
  kubectl -n shell-pilot describe secret pilot-access    # byte count only
  ```

  - `pilot-login` has no `PYTHONPATH`. Without `env PYTHONPATH=/app`, the export fails on `import ops`, but
    `create` still makes an empty Secret, so a failed pipe deletes it.
  - Check the Secret only with `describe`. Never use `-o yaml|json|jsonpath|go-template`: a template error makes
    kubectl print the raw object, token included (this happened in the pilot, see #64).

## Runs (back, review present)

```sh
kubectl -n shell-pilot exec pilot-a -c harness -- python -B /pilot/src/harness.py run --task fix-failing-test --config A
kubectl -n shell-pilot exec pilot-b -c agent   -- python -B /pilot/src/harness.py run --task fix-failing-test --config B
kubectl -n shell-pilot exec pilot-a -c harness -- python -B /pilot/src/harness.py run --task slow-suite --config A
kubectl -n shell-pilot exec pilot-b -c agent   -- python -B /pilot/src/harness.py run --task slow-suite --config B
```

- Each run prints one JSON line:
  - grade and tests changed
  - turn status and wall time
  - model requests (`thread/tokenUsage/updated` count)
  - tool calls and failures, timeouts, truncations, patch failures, repeated commands
  - token totals
- The run file in `/pilot/results/` also keeps the commands, truncated to 200 characters.
- Each Pod keeps its own budget file and may start a run only while it is under **half** of the pilot cap (1.2M
  input, 50k output). Usage events arrive after the fact, so this cannot guarantee a turn never overshoots: when a
  turn crosses the remaining share the harness interrupts it, counts its final usage, and halts that Pod.
- Before `turn/start` the run is written as in-flight (fsync, atomic replace). It is settled right after the turn,
  before closing, grading or writing results. Observed usage is always added as a lower bound; without the
  turn's completion event the run is unconfirmed. An unsettled, unconfirmed or halted run blocks every later run.
- **Operator rule:** any `budget_stop`, unconfirmed or unsettled run stops the whole pilot (both Pods) and is
  reported to PM before anything else runs.

## Revocation and cleanup (immediately after the fourth run)

```sh
ssh -t -i ~/.ssh/anxious-back-devvm -o IdentitiesOnly=yes -p 22107 opc@127.0.0.1 'sudo /usr/local/bin/k3s kubectl -n shell-pilot exec pilot-login -- codex logout'
```

1. `codex logout` revokes the refresh token at OpenAI and deletes `auth.json` (measured in #58 C).
2. Then `kubectl -n shell-pilot delete secret pilot-access` and `kubectl delete namespace shell-pilot`. The PVC uses the Delete reclaim policy, so its LV is removed; check with `lvs openebs-vg`.
3. If the logout fails, sign that session out in the ChatGPT security settings.
4. The report states both steps and their output, never the token.
