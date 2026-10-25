# Isolated shell executor (#23)

Opt-in v2 bindings add the `shell-executor` image target to the runner Pod.
Native Codex shell remains disabled and its sandbox remains read-only. The fixed
stdio MCP `shell` tool calls a runner-private broker, which checks the active
turn lease, owner scope, current binding/auth, and the API callback's current
login/approval/request token before forwarding to the executor Unix socket.
Neither that API token nor provider/control/transport state enters the executor.

The runner UID is 10001; the executor UID is 20002 with primary group 10001.
Both Unix peers check SO_PEERCRED. The broker is in the private state root; only
workspace and shell-ipc PVC subPaths plus a dedicated tmpfs enter the executor.
Codex uses private `native-work` as cwd so executor-written `.codex` configuration
cannot become runner-side MCP/process configuration. Existing native originals
remain in the same CODEX_HOME and thread IDs are preserved.

Every v2 turn resets the native process before resume/start, changes the private
lease, and explicitly enables MCP only for trusted owner scopes. Public/missing
scope disables the server; old bridge processes cannot borrow a new lease.
The only preapproved MCP tool is this fixed `shell`; native shell and other tools
are not enabled by that policy. Revocation closes active executor connections;
there is no replay, local-shell fallback, or guessed OOM result. The executor
cleans descendants before releasing a command lease. See `executor/README.md`.

Same-Pod network egress is allowed by the #23 decision. Existing runner-to-runner
and API-server network restrictions remain unchanged. Do not mount an API token,
provider auth, skill documents, or DB credentials into the executor in #23.
Those belong to the separately scoped #25 design.

## Configuration and transition

Legacy plans omit `version`/`executor` and render the original one-container Pod.
An explicit v2 plan adds exactly:

```json
{"version":2,"executor":{"image":"registry.invalid/executor@sha256:<64 lowercase hex>","wallSeconds":30,"outputBytes":65536}}
```

Limits: wallSeconds integer 1–300; outputBytes integer 1024–1048576. These become
SHELL_WALL_SECONDS/SHELL_OUTPUT_BYTES in the reviewed Pod. CPU/memory limits are
1/512Mi for executor (requests 100m/128Mi) and 500m/768Mi for runner (requests
100m/192Mi); the executor `/tmp` is a disk-backed 256Mi emptyDir. Values and their
measurements are in #58 E. Separate PID namespaces are required.
Do not patch a live Pod or alter existing binding JSON directly.

For new owners, optional `executor` in rollout values is validated and copied to
controller inputs; preparation publishes v2 only when supplied. Existing published
bindings remain unchanged when controller inputs change.

The concrete rollout input template is
[`values.example.json`](../../infra/hosting/m3/multi-user-runners/rollout/values.example.json).
Its `executor` block demonstrates the 30-second/65536-byte opt-in. The
`registry.invalid` executor image and all-zero digest are placeholders, not a
built or approved image. Before rendering, PM replaces this with the reviewed
`shell-executor` image digest and replaces runtimeImage, runnerImage and
provisionerImageId with builds containing #23 (the template's older image IDs do
not include the bridge/init code). PM also refreshes the existing node, namespace,
provider-account and allowlist values from reviewed operational evidence.
Remove the entire optional `executor` block for legacy v1 new-owner preparation.
Use the same reviewed executor object in the existing-owner maintenance inputs;
changing rollout values alone never upgrades an existing binding.

For an existing v1 owner, PM stops controllers/API/creator workers, obtains an
accepted termination proof, and uses `python -m ops.runner_binding_image report`
then `apply` in the reviewed maintenance container. Private inputs retain the
existing owner/state/generation/revision/oldPlanSha256/newImage fields and add the
`executor` object above. Report records full old/new plan hashes and executor
values; apply compares the report again. Both operations require exact STOPPED
fence, no Pod UID, no ambiguous/active/pending work, settled creator receipts,
completed termination proof, and old-plan CAS. Only image/version/executor change;
resource identities, native originals, auth and history remain intact. A v2 plan
cannot be silently downgraded/reconfigured by this opt-in operation. Production
image build/import/live application belong to PM after review.

## Display and validation

API projection keeps `type: tool_call`, `name: shell`, original arguments, and
executor output/status (`completed`, `failed`, `timed_out`, `rejected`). Output
contains stdout/stderr, exitCode, durationMs, truncated, wallSeconds when executed.
An authorization rejection can contain only status/error. The current polling
path exposes the running turn; individual shell results become available with
native history refresh after the turn. Do not promise per-command streaming.

Docker-only tests include manifest/CAS/database authorization/protocol regression.
`tests/probes/issue23_native_mcp.py` with `issue23_mcp_stub.py` checks actual pinned
registry removal/restoration. `issue23_native_shell.py` substitutes the fixed
bridge at /stub.py and mounts /source plus isolated executor IPC: actual Codex
calls execute, same-thread public transitions remove the tool, and original MCP
items project correctly. These use a loopback synthetic provider with network
none and no paid requests. `issue23_bridge_executor.py` covers timeout and stale/
public leases against the real UID20002 executor configured for a 1-second limit.
Live k3s operational acceptance remains a PM/reviewer step, not claimed here.
