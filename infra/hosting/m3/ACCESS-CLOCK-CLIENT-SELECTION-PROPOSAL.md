# Issue #7: bounded Docker client-selection scope — preparation only

No executable collector or new execution is approved here. PM's existing
record establishes the command chain, not the tool environment or daemon
placement. The consumed runtime capture's corrected interpretation and
parser closure atbb3f303 do not authorize another observation. Keep the
direct-node placement candidate conditional and all original guards intact.
The bounded source is now prepared separately in ACCESS-CLIENT-SELECTION.md;
that concrete revision requires its own execution review. An isolated actual
CLI both-variable counterexample is handled by refusing disagreement before
inspect; it does not override the selected context or assert physical placement.

## Purpose and limits

The smallest useful next read scope is the invoking Docker client's current
connection selection, classified without saving its value. It can distinguish
a Unix/TCP/SSH/npipe selection and which selection mechanism supplied it.
It cannot prove where the daemon runs: a Unix socket can be mediated, and a
TCP/SSH transport alone does not identify its physical Node. It also cannot
prove that the operator is inside/outside an outer execution environment.
Those two placement questions must remain explicitly unproven.

This proposal does not create another controller or repeat the clock capture.
It makes no daemon/API connection and reads no daemon metadata, other
containers/PIDs, namespace markers, cgroups, mount/config contents or process
environment dump. It changes no context, environment, profile or namespace.

## Exact read targets and output allowlist

Future host-side Bash collector would run in the same trusted root operator
launch environment as the recorded invocation, with a reviewed absolute
Docker CLI and a fresh empty caller-owned0700 evidence directory. No language
interpreter runs on the host. It must disable xtrace before any selection
value is read; an inherited traced/debug-hook environment refuses collection.

Read only nonempty selection status of DOCKER_CONTEXT and DOCKER_HOST. If
DOCKER_CONTEXT is nonempty, resolve that selected context through the CLI.
Otherwise, if DOCKER_HOST is nonempty, classify its scheme directly in Bash
without printing/persisting the value. Otherwise resolve the CLI's current
context. Do not clear/override either variable to make a query pass.

Docker documents DOCKER_CONTEXT overriding DOCKER_HOST/current context in
the [Docker CLI environment reference](https://docs.docker.com/reference/cli/docker/#environment-variables).
Context inspection is of the [selected client context](https://docs.docker.com/engine/manage-resources/contexts/),
not an independently authenticated statement about daemon placement.

The only CLI reads permitted for context-selected branches are:

1. docker context show: hold the selected name only in bounded Bash memory;
   never save it. Require exactly one name,1..128 ASCII bytes matching
   [a-zA-Z0-9][a-zA-Z0-9_.-]*. Unsupported names remain unknown, with no fallback.
2. docker context inspect --format ENUM_TEMPLATE SELECTED_NAME: one validated
   quoted name only. Directly emit the transport enum using this template;
   never emit/store full context JSON or the endpoint itself:

```gotemplate
{{$h := .Endpoints.docker.Host}}{{if and (gt (len $h) 7) (eq (slice $h 0 7) "unix://")}}unix{{else if and (gt (len $h) 6) (eq (slice $h 0 6) "tcp://")}}tcp{{else if and (gt (len $h) 6) (eq (slice $h 0 6) "ssh://")}}ssh{{else if and (gt (len $h) 8) (eq (slice $h 0 8) "npipe://")}}npipe{{else}}unknown{{end}}
```

No context enumeration, second context, default fallback, docker info/inspect
of containers, socket connection, config-file parsing, certificate read,
daemon status or query of old controller IDs is included. CLI stderr is
discarded; stdout name stays ephemeral. Reject newline/multiple-name output,
truncation, timeout or CLI error before any follow-up context inspection.

Persist only these fixed fields in a private JSON result:

| Field | Allowed values |
| --- | --- |
| selectionSource | environment_context / environment_host / current_context / unknown |
| contextOverrideNonempty, hostOverrideNonempty | boolean |
| transport | unix / tcp / ssh / npipe / unknown |
| status | collected / unknown |
| toolEnvironmentPlacement, daemonNodePlacement | unproven (always) |
| correctivePlacementAuthorized, runtimeSelectionAuthorized | false (always) |
| controllerCreated, daemonQueries, kubernetesCalls | false,0,0 |
| reason | fixed error label, no arbitrary command output |

Do not include context names, endpoint strings/hosts/ports/socket paths,
Docker configuration directory, TLS/certificate/proxy values, credentials,
user environment, hostname, container/process identifiers or raw stdout.
Classify the nonempty DOCKER_HOST scheme with the same enum rules; an empty
suffix or unsupported scheme must yield unknown rather than guessed locality.
The template rejects an empty suffix; classification alone is not endpoint
validity/authentication verification.

## Bounds, ownership and required counterexamples

One invocation, total3s BOOTTIME startup/reads/parse/evidence budget. Each CLI
read gets at most0.75s plus0.25s kill, tightened to remaining time. Limit
ephemeral name output to129 bytes to detect truncation, enum output to16 bytes,
and result to4096 bytes. Never write raw intermediates. Write a completion
receipt only if parsing and result writes finish inside the original deadline;
without it the entire result is unknown. No resource is created, so there is
no controller stop/delete or actor cleanup. Do not extend the deadline or retry.

The scope binds reads to the current invocation's client/environment and its
single selected context. It authorizes no unrelated context/container lookup.
Offline Docker tests must verify precedence when both variables are set,
empty variables, selected/default context, every recognized scheme, missing
endpoint/suffix, unknown/malformed/multiple/oversized context output, CLI
failure/timeouts and an exhausted deadline. Test that secrets/endpoints never
appear in files/stdout/stderr, no enumeration/fallback/daemon call occurs,
and every outcome retains both placement fields as unproven and both action
authorization fields as false. Local fake context/template tests must establish
that CLI context reads do not contact a daemon. These are required tests,
not tests already executed for this documentation-only proposal.

## Disposition after evidence

If this scope is accepted, prepare the bounded executable collector, exact
source/argv and those tests for independent execution review before PM runs
it once. No standalone command in this document is an execution instruction.
Do not add this branch to the controller's host/save/done RPC whitelist.

Any transport result still requires authoritative runtime/operator launch
provenance before a direct-node placement candidate can be justified. If that
provenance is unavailable, stop/re-scope instead of cycling through another
clock capture. An actual placement candidate requires separate security/
isolation/source/argv review and unchanged actor-local clock guards; none is
selected here. No numeric replay/arm, production mutation, runtime install/
restart, clock bridge, API-enable/auth/state/PVC-content/Native/turn or final
PR/publication is included. Existing authorization covers preparation without
a renewed routine user permission request.
