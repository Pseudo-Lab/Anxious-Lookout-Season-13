# User runner controller: durable creation boundary

This source unit connects the PostgreSQL registry to a trusted Pod adapter port.
It is not a deployed controller or a Kubernetes adapter, and it does not enable
HTTP dispatch. Default HTTP feature remains off. There are no credentials,
provider calls, cluster operations, admission/completion evidence or stop actions
in this unit. The next integration units must supply the concrete Pod client,
namespace/RBAC/network policy, private owner/state volumes and transport binding,
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
