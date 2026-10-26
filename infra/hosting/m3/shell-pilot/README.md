# Shell-surface A/B pilot (#64, development VM only)

Tasks `fix-failing-test` and `slow-suite` (#58 F tasks 1 and 6) under A (current runner shell surface) and
B (native shell middle option). The 4-turn pilot is done (#64 results). The full run, approved by the user, is
2 tasks × A/B × 15 slots = 60 paid turns, A and B alternating within each task (`harness.ORDER`). Budget cap
for the full run: 8M input and 60k output tokens, 4M / 30k per configuration, in a new ledger (the pilot's
usage is not counted). The harness refuses to start past it and interrupts a turn that would cross it.

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
- `harness.py`: `selfcheck --config A|B` (no model call), `run --task … --config … --slot N`, `budget`, `gate`.
- `campaign.sh`: the full-run driver (gate before each slot, JWT-shape check after it, `STOP` file).
- `pilot.yaml`: namespace `shell-full` (the pilot's `shell-pilot` object stays Terminating on the VM), PVC `pilot-login`, Pods `pilot-login`, `pilot-a` and `pilot-b` (B on SELinux
  `spc_t`: `container_t` denies bubblewrap's devpts mount, measured; production would need a dedicated policy).
- `seccomp-userns.json`: the Docker v27.5.1 default profile plus `clone`, `unshare`, `mount`, `umount2`, `pivot_root` allowed without CAP_SYS_ADMIN (bubblewrap's user, mount and PID namespaces).
- `agent-b.Dockerfile`: the B image.

## Development VM preparation (back)

1. `kernel.yama.ptrace_scope=1` in the guest (`/etc/sysctl.d/90-shell-pilot-yama.conf`, applied with `sysctl --system`). Record it in PARITY as "May differ" (development VM only).
2. Install `seccomp-userns.json` as `/var/lib/kubelet/seccomp/profiles/shell-pilot-userns.json` (root, 0644).
3. Build the B image in the guest Docker, then import it into containerd `k8s.io`. Pin `pilot-b` to the imported digest.
4. `kubectl -n shell-full create configmap pilot-src --from-file=harness.py --from-file=pilot_tasks.py`, then apply `pilot.yaml`.
5. `selfcheck` for A and B. These steps make no model call and use no credentials.

## Operator login (user, one line on the development server)

```sh
ssh -t -i ~/.ssh/anxious-back-devvm -o IdentitiesOnly=yes -p 22107 opc@127.0.0.1 'sudo /usr/local/bin/k3s kubectl -n shell-full exec -it pilot-login -- codex login --device-auth'
```

- It prints a URL and a one-time code. Open the URL on any device, sign in to the operator account, and enter the code.
- No token is printed. `auth.json` stays on the `pilot-login` volume (0600, UID 10001).
- For a ChatGPT workspace account, the workspace admin enables device code login and Codex local use first.
- Then back exports only the access token into the Secret the pilot Pods mount at `/access` (OpenEBS LVM refuses a
  second mount of the login volume, and the refresh token never leaves `pilot-login`). The token passes through
  the pipe only, never a terminal or file:

  ```sh
  set -o pipefail
  kubectl -n shell-full exec pilot-login -- env PYTHONPATH=/app python -B /pilot/src/harness.py export |
    kubectl -n shell-full create secret generic pilot-access --from-file=access.json=/dev/stdin >/dev/null ||
    { kubectl -n shell-full delete secret pilot-access --ignore-not-found; echo 'export failed: stop' >&2; exit 1; }
  kubectl -n shell-full describe secret pilot-access    # byte count only
  ```

  - `pilot-login` has no `PYTHONPATH`. Without `env PYTHONPATH=/app`, the export fails on `import ops`, but
    `create` still makes an empty Secret. A failed pipe deletes it and exits non-zero, and nothing after it runs:
    fix the cause and report before another export.
  - Check the Secret only with `describe`. Never use `-o yaml|json|jsonpath|go-template`: a template error makes
    kubectl print the raw object, token included (this happened in the pilot, see #64).

## Runs (back, review present)

On the development server, with an evidence directory outside the repository:

```sh
infra/hosting/m3/shell-pilot/campaign.sh "$EVIDENCE" [MAX_SLOTS]   # 0 done, 2 stopped, 3 MAX_SLOTS reached
```

- Before each slot, `campaign.sh` reads both Pods' ledgers and pipes them to `harness.py gate`. The gate names
  the next unclaimed slot in `ORDER`, or a stop reason, which stops A and B:
  - an unknown, unsettled or halted run in either Pod;
  - a per-configuration share or the total cap reached;
  - **3 harness errors** across A and B.
- After each slot the driver runs the JWT-shape check (A: results, ws, native-work; B: results, runs) and
  records file names only. Anything other than grep exit 1 stops the run.
- A file `STOP` in the evidence directory stops the run before the next slot. The operator creates it when
  review asks for a stop.
- `run --slot N` claims its slot in the ledger before anything can fail, and a claimed slot never runs again.
  A failure before or outside the turn is an `error` result (`harness_error: <type>`) with no usage. It counts
  towards the 3, and the next slot follows. A failed grade is a result, never a stop reason.
- After a stop, nothing runs until PM sends a new notice. The driver then resumes at the first unclaimed slot,
  within the remaining budget.
- **A workspace:** the model's commands run in the executor as UID 20002, and the files they create (for
  example `__pycache__`, 2755) cannot be removed by the harness (UID 10001). Before each A slot, the harness
  empties `/state/workspace` through the executor itself (`find . -mindepth 1 -delete`), then does its own
  pass. A cleanup failure is an error slot, not a manual step.

- Each run prints one JSON line:
  - grade and tests changed
  - turn status and wall time
  - model requests (`thread/tokenUsage/updated` count)
  - tool calls and failures, timeouts, truncations, patch failures, repeated commands
  - token totals
- The run file in `/pilot/results/` also keeps the commands, truncated to 200 characters.
- Each Pod keeps its own budget file and may start a run only while it is under **half** of the cap (4M input,
  30k output). Usage events arrive after the fact, so this cannot guarantee a turn never overshoots: when a
  turn crosses the remaining share the harness interrupts it, counts its final usage, and halts that Pod.
- Before `turn/start` the run is written as in-flight (fsync, atomic replace). It is settled right after the turn,
  before closing, grading or writing results. Observed usage is always added as a lower bound; without the
  turn's completion event the run is unconfirmed. An unsettled, unconfirmed or halted run blocks every later run.
- **Operator rule:** a stop from the gate, the JWT check or `STOP` is reported to PM before anything else runs.

## Revocation and cleanup (immediately after the last slot or a stop)

```sh
ssh -t -i ~/.ssh/anxious-back-devvm -o IdentitiesOnly=yes -p 22107 opc@127.0.0.1 'sudo /usr/local/bin/k3s kubectl -n shell-full exec pilot-login -- codex logout'
```

1. `codex logout` revokes the refresh token at OpenAI and deletes `auth.json` (measured in #58 C).
2. Then `kubectl -n shell-full delete secret pilot-access` and `kubectl delete namespace shell-full`. The PVC uses the Delete reclaim policy, so its LV is removed; check with `lvs openebs-vg`.
3. If the logout fails, sign that session out in the ChatGPT security settings.
4. The report states both steps and their output, never the token.
