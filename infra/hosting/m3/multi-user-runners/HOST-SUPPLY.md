# Host access-token supply — source review, no live enablement

`ops.runner_host_supply` is a finite host worker for D1. It reads only the fixed
`/home/opc/.codex/auth.json` source, with a trusted configured owner UID and private
regular-file/canonical-path checks. It validates a ChatGPT access JWT, its account
claim and remaining lifetime, then sends a service envelope for the current exact
owner/state/Pod. It never exports refresh/id tokens, refreshes credentials, runs
browser login, or persists access-token plaintext in files/DB/logs. The existing
source is not modified. JWT structural checks do not prove provider entitlement;
runner-native login remains the provider check.

A root-only private JSON input contains exactly:

- `pool`: the version2 pool API/namespace/UID/CA/token configuration.
- `account`: the independently selected provider account ID expected by each
  prepared control binding. This is not an app owner ID.
- `sourceUid`: numeric owner UID of the fixed host source, established by operator
  inventory; do not chown the source to make a check pass.
- `kubeconfig`: private verified-TLS kubeconfig path for the same cluster/namespace.
- `kubectl`: canonical root-owned executable path, normally `/usr/local/bin/kubectl`.
- `attemptRoot`: durable root-owned mode0700 metadata directory, writable only by
  the host worker. Preserve it across restarts and include it in operational backup.

Use the dedicated observer DB login through `OBSERVER_DATABASE_URL_FILE`, never
API credentials or a database superuser. Set `CODEX_USER_RUNNERS_ENABLE=true` and
match the API singleton/allowlist settings. The finite CLI accepts `--owner <UUID>`
or `--cursor-file /private/supply-cursor.json --limit 10`. A persisted owner cursor
advances over refused/excluded owners and wraps, preventing a blocked prefix from
starving later users. An external systemd oneshot/timer may invoke it only after
its image, credentials, scope and mount plan have been reviewed. It starts no
internal watcher. Initially run one explicitly selected synthetic/test owner.

The worker holds membership and owner locks across the operation, rechecks current
binding/generation/Pod UID, refuses pending stop/active requests/running conversations,
and validates namespace and live Pod before source access. A healthy available
runner returns without reading the source or logging in again. Only not-configured,
expired or revoked auth states permit supply; model/policy/unknown errors refuse.
The envelope snapshot ID is stable for the same access token and owner/state/Pod/
generation. Before exec, an exclusive fsynced file records only these identities
and unknown status. An accepted receipt updates that metadata. A repeated timer
cannot send the same source to that Pod again, including after an uncertain or
rejected attempt. Only a changed source or new Pod has a new permit. A transient
delivery failure therefore requires operator reconciliation if the source remains
unchanged; never delete attempt records automatically or reset the service ledger.
Healthy polling returns before source/permit access. No token plaintext is included
in attempt records, cursor, DB or stdout.

Credential bytes are passed as subprocess stdin to verified-TLS `kubectl exec -i`
(no TTY, no token argument/env). The receiver checks `--expected-pod-uid` against
its downward-API UID **before reading stdin**, then sends the envelope to the
existing private `/control/access.sock`. This requires the updated runner image;
old image compatibility must be reviewed before enabling supply. The native socket
serializes with drain/turn admission and maintains existing service-ledger rules.
Only the exact safe accepted receipt is recognized; stdout is never relayed and
exec stderr is discarded. CLI output is fixed owner/stage/result metadata only.
Timeout or unrecognized receipt is an uncertain result, not permission to dispatch
or replace the Pod. No user turn or provider paid request is created by this worker.

The host worker needs a read-only mount of the selected host source directory at
`/home/opc/.codex`, private config/DB/kube credentials and CA, a read-only root-owned
kubectl binary, network reachability to DB/API, and a writable private cursor path.
It does **not** need host PID/cgroup namespaces, a CRI/Docker socket, storage writes
or mount privileges. Do not reuse the privileged legacy recovery invocation.
Drop all capabilities except the minimum read permission needed for the explicitly
owned source; prefer `DAC_READ_SEARCH` rather than broad privilege. Use a read-only
root filesystem, no-new-privileges, and no shell tracing or environment/argv logs.
Kubernetes credentials need bound resource reads and pods/exec in the isolated
namespace, not Pod creation/deletion. Kubeconfig must not contain unreviewed exec
plugins. API credentials never receive pods/exec or the host auth mount.

Build a new provisioner target from the reviewed source archive and record its
immutable image ID before PM rollout; the legacy image pinned in TERMINATION.md
predates this module. Then invoke its Python entrypoint with
`-B -m ops.runner_host_supply --inputs /private/supply.json --owner <reviewed-owner>`.
Actual live source reading/supply has not been executed. Source tests use synthetic
access tokens, real disposable PostgreSQL and a fake Kubernetes exec transport.
Real provider login, two-owner separation, restart/resupply and network policy
acceptance remain PM operational gates after source/image review.

A batch with any refused owner exits nonzero after processing its bounded list.
Expired/under-120s source credentials emit the fixed `auth_expired` outcome; other
refusals emit `supply_refused`, without exception or subprocess contents. Wire the
reviewed oneshot's OnFailure to the operator's existing alert channel and deduplicate
by owner/stage/reason at that channel; no new external messaging integration is
installed by this source slice. Excluded/disabled owners emit `excluded` and do not
read source or cause a failure alert. Browser health retains not_configured or its
native auth_expired/auth_revoked reason until actual repair; UI polling cannot
trigger source reads or supply. A source repair is an operator action, not OAuth
refresh by this worker.

Implementation choice: this trusted host process reads the selected source and
passes in-memory bytes to kubectl directly. It does not spawn nested Docker or
mount the Docker socket. Unlike the original draft's separate network-disabled
exporter, the finite process has network access for DB/API and is therefore inside
the host credential trust boundary. Its restricted network policy, image and source
mount require explicit deployment review. The API and runner still cannot read the
host source. This tradeoff avoids granting the automation Docker-daemon control.
