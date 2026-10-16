# Workspace shell executor (#23, first implementation slice)

This process must be PID 1, UID 20002, in a dedicated container PID namespace.
It accepts newline-delimited JSON only through `/run/shell-executor/command.sock`;
Linux SO_PEERCRED must report runner UID 10001. There is no TCP listener, credential,
provider client, or auth/control mount. Socket group is 10001; directory and socket
permissions must allow only the two service identities. Workspace is mounted at
`/state/workspace`. Root filesystem is read-only and capabilities are dropped.
The Pod must not enable shared process namespaces. The executor image target is
`shell-executor`; PM owns production build/import/application.

Requests contain exactly version=1, canonical UUID requestId, command and relative
cwd. A connection carries one request and must remain open until its response.
The server uses a nonblocking global command lease (busy calls are rejected),
a fixed deadline selected from deployment environment, and a combined output cap:

| Environment | Default | Allowed range |
| --- | --- | --- |
| SHELL_WALL_SECONDS | 30 seconds | 1–300 |
| SHELL_OUTPUT_BYTES | 65536 captured bytes | 1024–1048576 |

No model/request argument can increase these limits. Deployment changes require a
reviewed plan transition; do not edit an existing Pod. Output is decoded as UTF-8
with replacement; its JSON wire representation can exceed the captured byte cap.
The runner must sanitize returned content before persisting or projecting it.

When the command ends, times out or loses its client connection, PID 1 sends
SIGKILL to every other process in its own PID namespace and reaps adopted children,
including setsid/double-fork descendants. The lease is not released until `/proc`
contains only PID 1. Failure to prove cleanup in five seconds exits the executor
instead of accepting another command. Container termination kills its PID namespace.
Commands have no inherited service variables/fds or stdin; Bash starts without
profile/rc files. Core dumps are disabled and child RLIMIT_NOFILE=128,
RLIMIT_NPROC=64. RLIMIT_NPROC counts a real UID's processes and is not a per-Pod
cgroup PID limit; on shared hosts this can also reduce availability of other
containers using the same UID. CPU/memory enforcement belongs to container limits.
An OOM/kill is not labelled resource-limit without corresponding evidence.

Same-Pod egress is intentionally permitted by the issue's user decision. Mount
isolation, a distinct UID, separate process namespace and the existing Pod network
policies are the boundary. A command can damage its own workspace or disrupt its
executor; loss of the executor must fail the turn without fallback to local shell.
No private runner files or socket credentials may be copied into the workspace.

## Offline test evidence

`tests/probes/issue23_executor_client.py` runs as UID 10001 in a separate client
container with only the IPC directory mounted. The server runs as UID 20002/GID
10001, PID 1, network none, read-only root, drop ALL/no-new-privileges, CPU 0.5,
memory 128 MiB, pids-limit 64. For quick timeout/output probes set 1 second/1024
bytes. Only temporary workspace and IPC volumes plus read-only server source are
mounted; no production image build is needed. Test checks private path absence,
workspace writes, cwd escape refusal, output truncation, busy rejection, deadline,
escaped descendant cleanup, disconnect cancellation and command-UID peer refusal.

The opt-in v2 runner integration uses a fixed stdio MCP bridge, private per-turn
authorization broker and stopped binding CAS transition; see
[`runner/SHELL-EXECUTOR.md`](../runner/SHELL-EXECUTOR.md). Native shell remains
false/read-only. No operational success is claimed by these offline tests.
