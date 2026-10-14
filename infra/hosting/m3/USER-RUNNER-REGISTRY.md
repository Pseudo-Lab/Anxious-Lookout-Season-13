# Issue10 PostgreSQL lifecycle registry (integration foundation)

This adds transactional persistence to the isolated lifecycle kernel. It is not
an enabled API, running controller, Pod/volume lease, auth supplier, or live user
conversion. No Kube client, source cache, provider call, model settings, URL,
transport token, or user-selectable global limit is part of this adapter.

## Persistent boundary

A separate `runner_runtime` schema stores one registry row per existing approved
editor/admin account, immutable owner/state identity, generation/revision/current
Pod UID, activity/busy/ambiguity, durable entry receipts, admitted request IDs and
actual completion timestamps, and a fenced lifecycle outbox. Pod UID uniqueness
prevents double assignment in this DB; it does not prove there is only one actual
Pod/process per owner. New users get a new stable state UUID. Re-entry after idle
uses the same state UUID and increments generation without deleting any history.

Every mutation requires an existing outer transaction and uses a savepoint so a
caller catching a refusal cannot commit half a state/receipt/outbox operation.
Membership is checked/locked before entry replay/admission/capacity. Owner advisory
locks serialize first creation and per-owner changes; generation/revision compare
and swap guards state updates. Independent owners have separate locks and receipt
keys. API session/CSRF/Origin and research policy checks must still precede this
adapter: passing an owner UUID is not proof of browser authorization.

Entry replay and coalesced pending preparation do not extend activity. New admitted
requests and independently verified original completion timestamps do. Completion
replay looks up only the target admission row; no lifetime completion tuple is
loaded/copied. Changed completion timestamps, unadmitted events and wrong fences
are refused. Completed history remains indexed and retained. Even a claimed idle
observation cannot clear a durable uncompleted admission: its busy/ambiguity is
preserved, and idle stop refuses while any admission is uncompleted. `verified`
flags are preconditions for future evidence adapters, not native termination or
completion proof. Actual durable Native completion delivery remains to integrate.

Outbox insertion is atomic with lifecycle state. Delivery is at least once; an
actual controller must re-check current owner/state/generation/revision/Pod fence,
eligibility and operator capacity decision before external actions, then reconcile
uncertain creation or stop results. Stale outbox rows never authorize a second Pod.
No worker dispatch/claim, actual create/delete/lease/termination barrier or periodic
loop is started in this source chunk. Stop work means Pod only, with no PVC, native,
control, admission, conversation, research or original account deletion.

## Explicit additive migration and grants

Run `python -B -m app.runner_migrate` **inside the reviewed Docker image** with
reviewed admin credentials, only after the existing auth migration/backup gate.
This never runs during API startup. It takes an advisory migration lock, validates
the existing auth contract, creates only a new schema, and verifies a checked-in
frozen structural contract and exact API grants. An existing partial/unknown/
drifted schema is refused without repair, stamping or downgrade. Re-run of the
known schema preserves records. Existing auth/research revisions/permissions are
not widened. Reflection defaults for existing research remain unchanged.

The API role has SELECT/INSERT on lifecycle data and UPDATE only on mutable state,
completion timestamps and outbox settlement. It cannot delete/truncate history,
update owner/state IDs/admission identities, create runtime objects, or update auth
approval/role. Dedicated controller credentials/RBAC and their exact grants are
not created here; backend/frontend transport integration is still pending.

Before any live migration, expand the reviewed DB backup/restore tooling to include
this schema and test current-user preservation separately from new-user creation
and service-v2 JSON conversion. An old dump excluding this registry is insufficient;
an older registry restore can lose receipts and permit duplicate operations.
No automatic old-backup restore/downgrade, stale JSON rollback, or existing live
trial adoption is authorized. Current live user's native/control/history migration
requires exact owner/state/volume identities, quiescence and actual previous child
termination with a reviewed target image; this module does not supply that evidence.

## Docker verification

`docker compose -p anxious-s13-back-issue10-registry -f backend/compose.runner-runtime-test.yml run --rm test`
uses a disposable PostgreSQL18.6 fixture, internal network, no published ports,
synthetic passwords, tmpfs DB, immutable existing Python test image, read-only source
and container filesystem, and no provider/source mount. Dispose only this test
project after checks. Database transaction/concurrency/grant tests are actual local
PostgreSQL checks, not live migration, persistent-volume crash or provider proofs.
