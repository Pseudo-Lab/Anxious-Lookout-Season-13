# Issue 16 backend design — decision draft, 2026-10-16

Branch: task/multi-user-runners, base main26197c5. This is design/D1 impact analysis,
not an approved deployment or implemented API. D1 is decided: shared operator access
token, automatically supplied by a host-side component to each owner's isolated
runner. This supersedes the earlier per-user-login/manual-supply assumptions.
D2 capacity/idle values remain pending. No live mutation is included here.

## Current constraints confirmed in source

`user_runners.selected` selects one UUID; Runtime holds one Plan; preparation only
accepts an owner with no existing registry row. Its local mkdir/new PV/PVC/Secret
creation is not an idempotent distributed provisioning workflow. Runtime.stop
retains failed phase, UID and generation's settled ensure receipt. Clearing these
by hand would violate the existing single-create rule. Registry/kernel already
contain stop/stopped/entry transitions, but boolean verification arguments do not
produce actual termination or quiescence evidence. Lifecycle idle period is fixed
at30min and Runtime currently limits max_pods to0/1.

## Common design independent of D1

1. **Eligibility:** one backend predicate for approved editor/admin + feature flag
   + optional allowlist, used by login auto-prepare, codexRunner, status, preflight
   and prepare. Feature disabled/allowlist exclusion stays not_enabled_for_account;
   nonapproval keeps existing auth/access handling. Never fall through to another
   owner's personal runner. Browser supplies no owner UUID for its own actions.
2. **Preparation worker:** API commits an owner-keyed durable preparation intent;
   a separate bounded controller performs provisioning/reconciliation. API does not
   gain host filesystem or PV creation privileges. Allocate state UUID once under
   owner lock; resource names and refs derive from it. Record each intended create
   before I/O. A timeout triggers exact-name GET and spec/UID/owner verification,
   never a second blind create or adoption of a matching-looking foreign object.
   Crash/restart at every step must converge or hold an explicit blocked state.
3. **Permissions:** controller gets only the needed codex-runner PVC/Secret/Pod
   verbs, namespace metadata reads and named prerequisite checks. Local PV creation
   is cluster-scoped and RBAC cannot constrain CREATE by a resourceNames list;
   disclose that trusted-controller privilege, constrain generated paths/specs and
   exclude arbitrary manifest input. Use separate storage helper with only the
   dedicated root mounted, not a host root mount in the API. API's namespace GET
   access validates immutable refs on each owner-scoped dispatch. No runner SA
   token/RBAC. Exact API/controller Role split is an implementation review item.
4. **Durable binding:** add a versioned owner/state resource-binding record (image
   digest, namespace UID, native/control PVC+PV refs and transport Secret UID).
   No provider token in DB. Immutable refs are published atomically only after
   preparation verification. Resolve Plan by authenticated owner+current generation;
   no API restart for enrollment. Transport token comes from the exact UID-bound
   per-owner Secret, not from browser input or a shared global credential.
5. **Storage:** keep native/control separate, UID/GID10001, Retain and same state
   across generations. A requested local PV size is not a filesystem quota.
   Before claiming a per-user cap, PM must establish filesystem/project-quota or
   bounded-volume support and available bytes/inodes. Design a supported enforced
   cap in the storage helper, refusing new allocation when enforcement is absent;
   do not relabel advisory PV capacity as a hard cap. Existing operator storage is
   imported in place and audited before any quota setup, never reinitialized.
6. **Capacity:** use the existing global admission lock and count both active UID
   bindings and uncertain settled creation permits. Quota and controller max agree;
   no new generation while an old execution may exist. D2 numbers remain config
   inputs, not fixed assumptions. Capacity_wait retries via durable controller
   scheduling or explicit idempotent prepare, never GET/status side effects.
7. **Termination/re-entry:** first fence new dispatch durably, then ordinary DELETE
   with old UID precondition. Confirm named Pod absent AND trusted node/container
   termination evidence before consuming a one-time completion for that UID/gen.
   Node unavailable, force-deletion, missing evidence, pending admissions or uncertain
   execution keeps the slot occupied. Record terminal evidence and atomically move
   to idle_stopped/clear current UID; retain old receipt/history. New authorized entry
   increments generation and gets its own ensure receipt, reusing state/storage.
   The #16 request for UID absence is necessary, but absence alone does not satisfy
   the existing kernel invariant. A bounded single-node termination observer is a
   required part of this slice, not the whole #11 drain protocol.
8. **Idle:** update last_activity on trusted admission/completion; UI polling is not
   activity. Stop only when no admitted work, no ambiguous execution and fresh
   quiescence evidence exists. Logout fences immediately; bounded asynchronous stop
   may continue after HTTP logout without retaining browser authorization. Retry of
   runner_failed does not automatically reset state; verified stopped cases re-enter,
   uncertain failures require reconciliation. Busy/uncertain cases never time out
   into a free slot solely because wall-clock time passed.
9. **Network:** retain40-runner-network.json; its namespace selector already covers
   multiple owners. Obtain actual A→B/B→A and API6443/service443 deny evidence when
   two approved users' runners exist. No codex-trial deletion during this slice.

## Existing operator import (no re-preparation)

Read the reviewed active binding, namespace UID, PVC/PV ownership/spec/UIDs,
transport Secret UID, native binding marker/control ledger and current registry.
Import references transactionally into the new binding table with an immutable
migration receipt; rerun must return the same result or reject drift. Do not call
user_runner_prepare, issue a fresh state UUID, replace Secret/token or clear ledger.
Current runner_failed/old pod_uid must pass the same verified termination recovery
as other owners before release/generation2. Preserve conversation owner identity,
state and files. If old evidence is insufficient, explicitly hold the reservation
for PM reconciliation rather than declaring the migration a successful re-entry.

## D1(b-auto): decided shared operator token with host-side injection

[User decision](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/16#issuecomment-6208116845):
all approved editors/admins use separate owner/state/storage/Pod bindings but the
same operator provider account, quota and cost. No device-code/login UI, managed
auth adapter or self-service provider-auth API is included. Retain external
chatgptAuthTokens/ServiceAccessAuth and its replay/revocation protections. No native
or host automatic provider refresh is added; the operator maintains host login.

### Components and sequence

1. The provisioning controller writes the same operator expectedAccountId into
   each new control binding. Keep the existing operator's binding unchanged. It
   emits a nonsecret supply intent containing owner/state/generation/PodUID,
   namespace UID, image/ref identities and an attempt UUID after the bound Pod is
   Running/Ready and its supply socket is available. Kubernetes Ready alone is not
   proof of socket/native/provider readiness. Intents derive from trusted registry
   state, never a caller-supplied Pod name or arbitrary namespace.
2. A separate host-side bounded injector runs as the source file's owner and reads
   exactly `/home/opc/.codex/auth.json` via the existing private-file validation.
   API Pod gets neither this file nor Docker/host filesystem access. A finite worker
   invocation processes a bounded batch; a reviewed host service/timer can schedule
   it. This is application work, not an agent inbox watcher; nothing is installed
   during design. Docker tooling authority and namespace exec authority belong only
   to this trusted host component, with no shell commands taken from intent payloads.
3. Generate owner/state/snapshotId/expectedAccountId supply-inputs automatically in
   a private directory. This JSON contains no access token. Persist the attempt ID
   before export, so an ambiguous delivery reuses the same snapshot identity. A new
   Pod/gen gets a fresh supply intent; do not reuse another owner's metadata.
4. Recheck account approval, current state/gen/UID, namespace and complete expected
   Pod/reference identity immediately before delivery. Serialize supply against
   logout/stop and stale worker completion with the same owner fence. Export in
   network-disabled Docker as the source UID and pipe stdout directly to the
   runner's supply client over verified Kubernetes exec, no TTY or shell interpolation.
   Neither envelope nor raw subprocess output goes to files, logs, queues or DB.
5. The existing manual shell script's GET-UID followed by name-based exec has a
   race. Automation must additionally check expected RUNNER_POD_UID inside the
   runner client **before reading credential stdin**, plus existing owner/state
   envelope checks. A stale target must never be treated as successful. Revalidate
   generation/UID before publishing the receipt; preserve uncertainty rather than
   resetting state after interrupted delivery. This relies on trusted validated
   runner code/spec and restricted creation authority; it is not an exec API UID
   precondition or protection against an administrator replacing trusted code.
6. Only a bounded allowlisted receipt/status is recorded (attempt/owner/gen/UID,
   accepted/reason/timestamp). A lost response is unknown, not success; reconcile
   matching runner health/ledger evidence or safely repeat the same latest snapshot
   under ServiceAccessAuth rules. No destructive ledger reset and no blind automatic
   retry of sticky policy/model/revocation outcomes. Provider authentication and a
   real turn remain separate from acceptance of the supply envelope.

### Expiry, recovery and status

Read actual JWT expiry through the existing exporter; do not assume a ten-day
lifetime. The current exporter rejects expired/under120sec credentials. Host file
renewal is an operator action, not this worker performing OAuth refresh. A changed
operator source may trigger a new bounded supply attempt after account/fence checks;
the same rejected credential is not continuously resent. Initial delivery/re-entry
and changed-source recovery share the same guarded path. A changed provider account
is refused until an explicit binding migration, not silently accepted for all users.

Initial/re-entry supply queued or in flight projects `provisioning` with retry2sec.
Missing/invalid source or exhausted non-auth failure projects persistent
`not_configured`; expiry/revocation keeps `auth_expired`/`auth_revoked`. These failure
statuses include retryAfterSeconds30 for **read-only** UI recovery polling after
operator repair, without hiding the error or repeatedly submitting provider calls.
Terminal policy/model blocks retain their own reasons and require intervention.

Alerts use deduplicated sanitized structured host-service logs: fixed reason,
affected owner/gen/UID/attempt and time, never token/claims/source contents/raw stderr.
This satisfies the requested operator-log notification; no new admin dashboard API
is introduced in this slice. Public status only exposes safe reasons/retry hints.
Credential-manager/auth-mode fields are unnecessary since all users use operator
credentials. In-memory access tokens disappear with the Pod; retained ledgers remain
token-free. Automatic supply restores access on the next generation, subject to a
valid host source. Browser login remains GitHub only.

## D2 and rollout gates

Five Pods/thirty minutes are suggestions only. Measure host baseline and2–3 concurrent
native turns before setting requests/limits, max_pods and quota. Also establish a
storage cap/host quota mechanism; report measurements instead of using idle180Mi
as peak memory. Unknown values do not authorize a capacity increase.

Implementation slices after decisions: schema/eligibility + import; crash-safe
provisioner/dynamic plans; termination evidence/re-entry/capacity/idle; selected
host automatic supply and front contract; reviewed operational rollout and live matrix.
Each slice pushes to the same task branch and uses one eventual PR.

Docker tests: cross-owner refusal at all API/resource boundaries; concurrent same-owner
preparation and different-owner capacity; create timeout/restart recovery; revoked
membership mid-flight; old UID/generation fences; pending-work stop refusal; verified
termination once-only consumption; idle clock boundaries; existing operator import
and credential history preservation. D1(b-auto) adds stale UID before stdin, concurrent
supply/stop, source owner/mode/change/expiry, rejected fingerprint, lost receipt and
no-token-in-HTTP/log/DB tests. Live: two users'
full response/logout/relogin-response flows, overflow/wakeup, idle recovery and network
verdicts. No full acceptance from mocked tests alone.

Rollback: stop new enrollment/dispatch, retain data, reconcile extra owners' running
Pods before shrinking quota, then restrict eligibility to the original owner and
max1. Never lower quota and assume extra workloads disappeared; never reset registry
history or run destructive down migrations. Keep schema compatibility/import evidence
and old source images. Credential rollback is mode-specific and cannot undo provider
activity or erase changed native auth state.

## Status projection checkpoint (2026-10-16)

The first implementation slice exposes `stopping` with a five-second retry hint,
refuses failed-state prepare (including receipt replay) with HTTP 409
`runner_reconciliation_required`, and attaches a 30-second read-only polling hint
to bound user-runner `not_configured`, `auth_expired`, and `auth_revoked` health.
Failure refusal holds the registry owner lock and preserves receipts/generation.
GET can reflect a concurrent reconciliation rather than the preceding POST state.
Legacy personal/trial status responses are unchanged. No discriminator or new
`codexRunner` field shape is introduced. UI idle copy must not hardcode 30 minutes.

This checkpoint does not implement host injection, eligibility expansion, plan
multiplexing, or durable session-bound entry intents during stopping. In particular,
stopping POST is not yet a promise of queued re-entry. Credential polling observes
operator repair; it does not initiate supply. Poll at 30–60 seconds only while the
conversation/tab is visible, without overlapping requests or a fixed count cap.

## Implemented source checkpoints (2026-10-16)

D3, DB observer, legacy recovery and idle source slices have separate partial review
approvals; none is full #16/live approval. PROVISIONING.md, TERMINATION.md and
HOST-SUPPLY.md describe the current source behavior and override earlier draft
mechanics where stated. Host supply uses a finite trusted host process and
stdin-only kubectl transport without a Docker socket. It records a durable exclusive
attempt permit before exec and never automatically resends the same rejected or
uncertain source/Pod combination; operator reconciliation is required. Source-token
replacement or a new Pod/generation yields a new scoped identity. Existing native
health remains authoritative for browser auth failure reasons. Operator alert wiring,
new runtime image binding/cutover, host namespace/mount proof and end-to-end provider
checks remain rollout work; do not treat code tests as evidence of these operations.
