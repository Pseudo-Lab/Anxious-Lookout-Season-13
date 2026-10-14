# User runner controller: durable creation boundary

This source connects the PostgreSQL registry to a trusted Pod adapter port and
adds the REST adapter described below. It is not a deployed controller and does not enable
HTTP dispatch. Default HTTP feature remains off. There are no credentials,
provider calls, cluster operations, admission/completion evidence or stop actions
in the original creation coordinator. Further integration must supply deployed
namespace/RBAC/network policy, initialized private owner/state volumes and transport binding,
and actual native admission/completion/quiescence/termination evidence.

## One irreversible create permission

`Controller.ensure(Fence)` takes a durable registry fence, never user-supplied Pod
parameters. It checks current approved editor/admin membership, serializes the
owner, and requires a preparing/waiting unbound generation. It reserves capacity
only after validating the reviewed runner schema and grants, and repeats that
validation before external I/O. Drift refuses without automatically repairing DDL.
It serializes capacity decisions
under a shared PostgreSQL advisory lock (44011). Each implementation using this
DB must use this same protocol and operator configuration; this is not a cluster
quota against unrelated or manually created Pods.

Before any external I/O, it commits the generation's ensure outbox rows as
`settled=true`. Here settlement means **the one create permission has been issued**,
not successful creation or readiness. These immutable dispatch receipts must not
be reset, deleted or replayed. An uncertain outcome is tracked by the still-unbound
registry row. Pending outbox scans alone are therefore insufficient: a future
worker must also reconcile unbound reserved generations. No schema/grant migration
is introduced. The existing API role can execute this metadata protocol; actual
controller database credentials and RBAC remain a separate deployment concern.

After reservation it reacquires membership and owner locks and verifies the same
owner/state/generation. It looks up the deterministic `runner-<owner hex>` name.
Only the worker that obtained the first permission may send a single create if
absent. Every later worker is lookup-only. All results must match the complete
identity and a UUID Pod UID. A foreign/old-generation object is refused without
deleting or replacing it. The adapter must validate API metadata and resource
bindings; the dataclass alone is not independent proof of those facts.

The transaction spans the bounded adapter call and binding. If creation succeeds
but the response is lost, or binding rolls back, the committed permission still
prevents another create. A later authoritative lookup may bind the same object.
A crash before sending the first request is intentionally indistinguishable from
uncertain delivery: absence, age or a timeout never permits a second create. It
holds the slot until separately reviewed reconciliation. No automatic reset or
operator override tool is provided here. This sacrifices automatic recovery to
avoid allowing delayed or uncertain operations to produce duplicate execution.

The Pod adapter must not retry POST internally, use generated names, delete a
conflicting object, select by partial identity, silently follow a foreign endpoint,
or start a replacement. Only this protocol may create these managed Pods. A future
Kubernetes implementation must enforce those assumptions within its namespace and
must set bounded I/O timeouts; otherwise this source does not establish its claimed
single-create boundary. External calls hold DB connections/locks: timeout and pool
budgets require load measurement before deployment. No new connection pool tuning
is included.

## Capacity and readiness

`max_pods` is an explicit nonnegative integer; default **0** permits no new creation.
This default represents an unselected operational limit, not a proposed production
capacity. Count bound Pods and committed unresolved creation permissions, including
stopping and uncertain generations. Release a slot only after the registry reaches
`idle_stopped`, whose existing contract requires actual previous termination
verification. Waiting is durable and does not extend activity; an explicitly
configured larger limit can admit a waiter without another browser request.
All controller instances must share the same reviewed limit. Global concurrent
model execution remains a separate configuration/measurement decision.

Binding a Pod sets `runner_unreachable`, `ambiguous=true`, and preserves activity.
Pod existence does not establish provider readiness, native quiescence, completion,
or dispatch permission. An absent already-bound Pod is never recreated by this
unit. New generation creation is reachable only through the existing explicit
stop/termination registry barrier and subsequent entry. The stable owner/state
identity, receipts and all unrelated user data remain intact. This unit has no
Pod or volume deletion operation.

## Verification and limits

`backend/config_tests/test_runner_controller.py` uses the disposable Docker
PostgreSQL fixture and a controlled in-memory Pod adapter. It exercises concurrent
owners and duplicate workers, durable capacity reservation, response loss before
and after create, controller restart, DB rollback after external success, foreign
identity rejection, revocation, missing bound Pod, and retained history across a
synthetically verified stop/re-entry. The API-role test uses real restricted DB
grants. Actual Kubernetes semantics, node partition/process termination, RBAC,
network isolation, native evidence and provider authentication are not proven by
these tests and must be reviewed in subsequent units before enablement.

## Lookup reconciliation and REST adapter (next source unit)

`Controller.pending(after=UUID, limit<=100)` scans unbound reserved generations
with keyset pagination, including settled permissions that a pending-outbox-only
worker would miss. `Controller.reconcile(Fence)` validates schema/current fence
and performs **lookup only**, even after the account loses eligibility. This
tracks an already-created Pod without authorizing new creation or model dispatch.
Absence returns no binding and preserves the entire reservation. A matching Pod
is bound as unreachable/ambiguous; activity is unchanged. Schema drift, foreign
identity, stale fences and I/O errors refuse. No loop is started by importing the
module, and neither GET status nor browser POST executes reconciliation.

`KubePods` implements the Pod port over the Kubernetes core/v1 REST API with an
explicit HTTPS origin, namespace, CA trust and private bearer input. It uses no
ambient proxy, redirect following or HTTP transport retry. Each create call sends
at most one POST; even 409/timeout/invalid response is not automatically retried.
Only a committed controller permission may call create_once. It must never be
called directly by a user retry, generic job retry mechanism or operations script.

A private reviewed `Plan` is required for each exact owner/state/generation. Its
image must be digest-pinned. Native/control PVC names, UIDs and PV names, immutable
transport Secret name/UID, callback HTTPS URL and service account are explicit.
There is no dynamic provisioning or automatic adoption of existing user volumes.
Plans for a user's generations must retain the same owner/state/storage/transport
binding; cross-owner PVC/PV/token-reference reuse is refused. Existing state
markers and `/control/operator-account` must be initialized/verified by a future
reviewed storage procedure before this adapter can be enabled. The adapter does
not supply that metadata, credentials or provider authentication.

Before creating or binding, the adapter checks PVC owner/state labels, exact UID,
Bound status and PV name, plus transport Secret owner/state, UID, immutable=true,
Opaque type and exactly one syntactically valid token entry. Reading this Secret
requires secret-get permission and puts the transport token in process memory;
no value is emitted in logs/errors or embedded into the Pod manifest. This is not
provider credential access. UID/reference checks do not inspect the PVC's files,
prove filesystem lease ownership or prove that two different PV names cannot
alias the same external storage; provisioning and storage isolation remain gates.

The generated Pod has one pinned runner container, no init/sidecar/ephemeral
containers, no host namespaces/hostPath, a non-root UID/GID, read-only root,
capabilities dropped, default seccomp and no service account token automount.
Native/control mounts are separate and retained; only the Pod consumes them.
The transport Secret is mounted read-only at mode0444 so the non-root process can
read Kubernetes' root-owned projected file without changing existing volume group
ownership. It is accessible inside this single isolated container, not publicly
served. Runner authentication still validates the existing private state marker.

Pod responses must match exact owner/state/generation/name/namespace/UUID and
expected command/env/image/volumes/mounts/security. Only named harmless API defaults
and scheduler-generated nodeName are normalized. Extra labels, annotations or
finalizers are refused, including labels that could select another workload's
network allow policy. A cluster's required CNI annotations must be separately
reviewed before allowing them; this source does not silently accept them.
Unknown admission mutation is
refused; the adapter never patches the Pod to make it match. Native lifecycle and
provider readiness are never inferred from Pod phase/readiness/status.

Each HTTP request has an elapsed budget (default10s). Connect, TLS, each socket
read and each partial write receive the smaller of the remaining budget and the
phase timeout (default2s). Incoming body chunks are checked without aggregation;
the 2MiB uncompressed body cap and identity encoding restriction still apply.
Real endpoints must use a literal API IP covered by the trusted certificate's IP
SAN, avoiding unbounded hostname resolution. TLS verification is never disabled.
Connections are not reused and there is no shared connection-pool capacity queue.
The custom transport replaces HTTPX's private pool using pinned HTTPX0.28.1 and
httpcore1.0.9; dependency upgrades must rerun socket and real loopback TLS tests.
CPU parsing and OS scheduling are not preempted: this is not a hard real-time
guarantee. Several GETs plus a POST occur while the owner transaction is held;
the deadline applies per HTTP request, not to the whole controller transaction.
Actual cluster TLS, admission defaults, load and total DB pool/lock budgets require
isolated cluster/load verification before activation; that plan belongs to unit3.
No production resource sizing or Pod resource quota is chosen by this source.

### Required deployment permissions and isolation (not installed)

Use a dedicated namespace and dedicated controller identity. The minimum verbs
for this adapter are namespaced pods create, pods get for the configured deterministic
names, and get for the selected PVC and transport Secret names. It needs no list,
watch, delete, patch, update, exec/attach, logs, service proxy, PVC creation or
cluster-admin privilege. Resource names can narrow get grants. Kubernetes RBAC
cannot restrict top-level create by resourceNames; therefore RBAC alone cannot
enforce this adapter's Pod template. Namespace admission policy must reject foreign
owner/state/storage/transport, token automount, privilege changes, unknown images
and extra containers/volumes before any enablement. The runner service account
needs no Kubernetes grants.

Install and verify default-deny ingress/egress for managed runner Pods, then review
only the exact controller/API transport, callback, DNS and approved provider routes
needed by later execution integration. No broad runner-to-runner or unrestricted
namespace egress exception is acceptable. This unit does not install policy,
provide an admission-policy manifest, grant privileges or prove actual network
isolation. Those deployment artifacts and their integration review remain required.

Primary references for the API/permission boundary:
- [Kubernetes API concepts](https://kubernetes.io/docs/reference/using-api/api-concepts/)
- [Kubernetes RBAC resource-name limitations](https://kubernetes.io/docs/reference/access-authn-authz/rbac/)

### Read-only user status projection

For an unbound preparing generation with a settled ensure permission, both status GET and
preparation POST return the existing runner_unreachable reason, available=false,
unverified and retryAfterSeconds=5. The receipt indicates uncertainty from the
moment permission is issued; it may briefly appear while a normal create is in
flight, without alleging permanent failure. Polling cannot touch activity, create
Pods, release capacity or enqueue model work. Same-key POST is unchanged; new-key
POST coalesces in the same generation. Message preflight uses the same reason and
refuses before dispatch. A retained settled receipt from an idle_stopped generation
does not mask idle_stopped as a connection problem. No owner/state/Pod or receipt
metadata reaches the browser.

Front accepted the existing state flow and clarified its wording in bbeaca7 after
its current review-fix work. No new field or reason is introduced. That state has
no retry button or automatic POST; reload uses the single normal entry POST.
User-facing copy must not promise that retry recreates a Pod
or frees the reservation. Explicit termination/slot release and the operator
procedure for unresolved reservations remain **unit2, incomplete**. The module's
pending scan is a diagnostic primitive, not a reviewed release tool. If termination
or prevention of late creation cannot be established, release stays unavailable
and activation remains blocked; user records must never be deleted as a workaround.
