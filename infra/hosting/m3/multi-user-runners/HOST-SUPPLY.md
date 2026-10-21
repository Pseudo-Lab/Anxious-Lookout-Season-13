# Host access-token supply — source review, no live enablement

Use [CUTOVER.md](CUTOVER.md) for build/pin records, stopped binding-image transition,
service installation order, expansion gates and rollback. No live build is implied.

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
exec stderr is discarded. Before recording accepted or returning supplied, the
worker rechecks the locked registry fence and pending stop, then freshly verifies
namespace identity and the same running, non-terminating Pod UID, complete spec
and PVC/Secret references. A missing/replaced/changed target or lookup failure
keeps the permit unknown and never triggers redelivery. This is a current check,
not a guarantee that Kubernetes will preserve the Pod after the check.
CLI output is fixed owner/stage/result metadata, with a fixed `reason` on batch failures.
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

## Single-owner diagnosis (#46)

PM builds/pins the reviewed provisioner image and updates `rollout/worker.py`
with it. The new failure JSON adds a fixed `reason` field; the previous wrapper
rejects that field. Preserve the old image/wrapper together for rollback. No
runner binding image change is needed to run diagnosis. Do not run these modules
with the host Python installation.

Inside the reviewed supply container, with the same private inputs, DB login,
allowlist, source mount, CA and network as the service, run:

```sh
python -B -m ops.runner_host_supply --inputs /private/supply.json \
  --owner <owner-uuid> --diagnose
```

Use read-only mounts for inputs, source and attemptRoot. Do not provide a cursor.
The command reads the selected credential in memory only when earlier checks pass;
it does not exec, refresh, supply, write a permit, update DB or call drain/idle.
All DB transactions, including pool resolution, are read-only with 10s statement
and 3s lock timeouts. It emits only canonical owner, fixed stage and fixed result
codes. No exception text, credential, account identifier or JWT claims are emitted.
Diagnostic exit 0 means no tested blocker, **not** successful supply or termination.
Checks are point-in-time and may race real workers; only a subsequent normal worker
can establish its own locked preconditions and receipt.

For stop diagnosis use the same command with
`--observer-inputs /private/observer.json`. Run this combined invocation in the
reviewed **observer** container mount/namespace plan, additionally mounting the
supply inputs, kubectl and selected source read-only. Observer config construction
requires its existing host PID/cgroup view, proc, CRI socket and crictl inventory;
diagnosis does not invoke CRI, process death proof, drain, idle or DELETE. It reads
Node/Pod and, when the Pod is absent, retained receipt evidence. Output has two
lines with stages `host-supply-diagnose` and `termination-diagnose`. Do not pass
this diagnostic command through the timer wrapper, whose schema is for batches.

| Fixed result(s) | Owner action | PM/operator action |
| --- | --- | --- |
| `excluded`, `owner_ineligible` | Obtain normal account approval if needed | Check intended enable/allowlist and membership; never bypass eligibility |
| `running_conversation` | Open the existing conversation to refresh its native result | Preserve request/thread and inspect retained evidence; do not submit another turn or mark DB terminal manually |
| `active_requests`, `pending_admissions` | Wait for current work | Reconcile existing request using its evidence; do not zero counters |
| `stop_pending`, `state_ineligible` | Wait for termination | Use stop diagnosis below before attempting further supply |
| `source_expired`, `source_unreadable`, `source_account_mismatch` | If the selected source is yours, perform the existing trusted host login procedure | Verify fixed path, UID, permissions, selected account and expiry; Kubernetes SA renewal does not renew this source. Never copy tokens into messages or change ownership to bypass checks |
| `attempt_unknown`, `attempt_accepted`, `attempt_mismatch`, `attempt_unreadable` | None | Preserve permit and service ledger; inspect receipt/health against the same Pod. An accepted old permit with unavailable health is not permission to replay; never delete permits to retry |
| `already_available` | None | No source repair needed |
| `ready_for_supply` | None | Normal supply may be run within approved operational scope; this code has not tested receiver/provider login |
| `input_invalid`, `db_validate_failed`, `registry_missing`, `binding_unavailable`, `binding_mismatch` | None | Check private config, observer login/grants/schema and binding identity; repair through reviewed tooling, never handwritten DB mutations |
| `namespace_unavailable`, `pod_unavailable`, `pod_uid_mismatch`, `pod_invalid`, `pod_not_running`, `pod_address_invalid`, `pod_terminating` | None | Compare namespace/Pod UID, spec/references, network and API credentials with recorded inventory; preserve any replacement or uncertain target |
| `health_unavailable`, `health_identity_mismatch`, `health_refused` | None | Check runner reachability, transport identity and current safe health reason; do not infer provider token expiry from these codes |
| `delivery_uncertain`, `diagnostic_failed` | None | Current preflight cannot identify the original delivery failure; preserve evidence and stop automatic repair attempts |
| `no_stop` | Normal logout can request stop if intended | No current stop intent; diagnosis does not test native idle eligibility or create an intent |
| `stale_stop`, `stop_identity_mismatch` | None | Keep state; escalate for an evidence-specific tool correction, no DB edits |
| `node_unavailable`, `node_mismatch` | None | Check Node UID/boot/heartbeat and scheduled Node against reviewed host inventory; do not overwrite anchored evidence |
| `receipt_unavailable`, `receipt_mismatch` | None | Preserve retained storage and stop record; reconcile receipt identity, never fabricate a clean drain |
| `ready_for_observation`, `retained_receipt_ready`, `awaiting_pod_deletion` | None | Run the existing observer as described below; native drain and host death proof are still untested |

### Safe reconciliation sequence for the affected owner

1. PM records image/config provenance and runs both diagnoses for the explicit
   owner. Compare with current registry generation, stop and Pod UID through the
   approved read-only inventory. The first failing precondition is reported; fix
   that cause and diagnose again. These codes cannot prove an earlier failure's
   cause. In particular, repeated `supply_refused` plus `reconciliation_required`
   does not establish token expiry or state corruption.
2. For `running_conversation`, the owner opens the existing conversation detail.
   The existing authenticated GET reconciles a matching native request result;
   it does not send a turn. If native is still running/unreachable, retain the
   state and report the blocker. No forced cancellation, synthetic completion,
   archive or manual DB edit is authorized. Logging in again can queue a deferred
   entry; inspect this before assuming the final phase will remain STOPPED.
3. With current stop identity and pending work clear, invoke the reviewed observer
   entrypoint in its **existing host isolation/mount plan**:
   `python -B -m ops.runner_stop_observer --inputs /private/observer.json --owner <owner-uuid>`.
   Omit `--idle`: this reconciles an existing stop, not a new idle decision.
   The worker anchors Node identity, obtains clean drain evidence, commits it,
   and uses an ordinary UID-preconditioned Pod DELETE. A later invocation consumes
   retained receipt plus CRI/proc proof to release capacity. Never use forced Pod
   deletion or call `release` directly. The wrapper's `observer` role can execute
   this same finite command and reports a failure exit for reconciliation_required.
4. If drain or host proof fails, preserve the slot/Pod/evidence and escalate with
   fixed codes; `ready_for_observation` is not proof those active steps will pass.
   There is deliberately no universal force-reconcile command. Once the worker
   actually records STOPPED and the exact Pod is gone, PM follows CUTOVER's stopped
   binding-image procedure. A queued valid entry may instead advance to PREPARING;
   that is not permission to change a live binding.

### Repeated failure JSON policy

Normal batch supply now adds `reason` from a fresh read-only preflight after a
failure. This describes a current blocker, not a traceback or proof of the original
failure. If preflight now passes, `delivery_uncertain` is used. With a cursor, the
same owner/outcome/reason is emitted once and at most every 900 seconds thereafter.
Changed reasons emit immediately; success clears suppression so a recurrence emits
immediately. Metadata `supply-log-<owner>.json` lives beside the private cursor and
contains only outcome/reason/time. It is separate from supply permits. Corruption
or inability to write log metadata fails open for logging. Clock rollback emits
again. Concurrent invocations may duplicate a line; they never skip actual checks.

This suppresses repeated **owner JSON**, not systemd's own failed-unit messages.
Every bounded batch still attempts each selected owner, advances the cursor and
exits nonzero when any owner failed, even if every failure JSON was suppressed.
Manual `--owner` without a cursor always emits. Timer cadence and OnFailure behavior
are unchanged. Routing/deduplicating OnFailure notifications needs the operator's
chosen alert channel; this change installs no external notification destination.
