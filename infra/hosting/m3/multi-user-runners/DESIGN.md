# Issue 16 backend design — decision draft, 2026-10-16

Branch: task/multi-user-runners, base main26197c5. This is design/D1 impact analysis,
not an approved deployment or finalized API. D1 authentication and D2 capacity/idle
values remain user decisions. PM's working assumption is per-user ChatGPT login
as the target, with an allowlist of2–3 owners and individually supplied credentials
for initial live validation. No implementation or live mutation is included here.

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

## D1 impact: managed login is not a UI around the current supply socket

Official OpenAI documentation distinguishes managed ChatGPT login from externally
supplied chatgptAuthTokens. Managed device-code login makes Codex own persistence
and refresh; external-token mode makes the host own token lifecycle. The documented
app-server login type is chatgptDeviceCode, with loginId/verificationUrl/userCode
and asynchronous completion/cancellation. See [App Server authentication](https://learn.chatgpt.com/docs/app-server).

Device login availability depends on account/workspace settings; headless CLI
supports the device-auth flow. See [Authentication](https://learn.chatgpt.com/docs/auth).
The pinned0.160.1 image's `codex login --help` was checked in network-disabled Docker
and includes --device-auth. No actual login, RPC compatibility or account entitlement
was tested. Current web documentation can differ from this pinned binary: verify its
app-server schema and isolated flow before promising exact fields/timing in the API.

- **D1(a), target:** a separate managed-login runner auth adapter is required. Current
  ServiceAccessAuth expects an externally supplied access snapshot and rejects native
  auth.json; do not weaken that adapter or reuse its ledger as a managed credential
  store. Keep managed auth in a dedicated per-owner retained credential location,
  outside browser-accessible data and ordinary tool roots. Browser sees only a
  session-bound device challenge/status; tokens/refresh material stay in runner.
  Login ID is bound to app owner/state/generation/PodUID with CSRF/idempotency and
  expiry/cancel handling. Confirm provider account binding on completion; an app
  session or client-supplied account ID is not proof of provider identity.
- Managed login entails native refresh and credential retention. Issue16 also says
  provider refresh remains in #11 and tokens belong only in runner ledger: these
  statements need D1 clarification because native managed storage is not the current
  access-only ledger. Recommended interpretation if(a) selected: permit Codex's
  built-in refresh and private per-user credential storage; defer custom refresh
  orchestration to#11. This is a proposal, not assumed authorization.
- **D1(c), initial live assumption:** keep external access-only mode and per-owner
  expected provider-account binding; PM supplies each owner's independently selected
  token. No operator token sharing/fallback. Owner's auth-expired/revoked state names
  the actual credential manager. Re-entry loses in-memory access token, so manual
  resupply is expected after new Pod creation. Stored ledger is not a token cache.
  This phase does not satisfy self-service login acceptance if(a) is selected.
- **D1(b):** not assumed; do not silently implement shared operator credentials.

## D2 and rollout gates

Five Pods/thirty minutes are suggestions only. Measure host baseline and2–3 concurrent
native turns before setting requests/limits, max_pods and quota. Also establish a
storage cap/host quota mechanism; report measurements instead of using idle180Mi
as peak memory. Unknown values do not authorize a capacity increase.

Implementation slices after decisions: schema/eligibility + import; crash-safe
provisioner/dynamic plans; termination evidence/re-entry/capacity/idle; selected
credential adapter and front contract; reviewed operational rollout and live matrix.
Each slice pushes to the same task branch and uses one eventual PR.

Docker tests: cross-owner refusal at all API/resource boundaries; concurrent same-owner
preparation and different-owner capacity; create timeout/restart recovery; revoked
membership mid-flight; old UID/generation fences; pending-work stop refusal; verified
termination once-only consumption; idle clock boundaries; existing operator import
and credential history preservation. D1(a) adds owner-bound login/cancel/expiry tests,
no-token-in-HTTP/log/DB checks and wrong-account completion handling. Live: two users'
full response/logout/relogin-response flows, overflow/wakeup, idle recovery and network
verdicts. No full acceptance from mocked tests alone.

Rollback: stop new enrollment/dispatch, retain data, reconcile extra owners' running
Pods before shrinking quota, then restrict eligibility to the original owner and
max1. Never lower quota and assume extra workloads disappeared; never reset registry
history or run destructive down migrations. Keep schema compatibility/import evidence
and old source images. Credential rollback is mode-specific and cannot undo provider
activity or erase changed native auth state.
