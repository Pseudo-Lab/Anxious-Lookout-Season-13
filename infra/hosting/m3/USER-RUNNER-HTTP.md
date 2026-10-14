# Issue10 HTTP preparation (intermediate integration)

This connects reviewed lifecycle registry persistence to the frozen frontend HTTP
interface. It is an intermediate source/integration chunk. It does not start a
controller/supplier, create Pods/PVCs, access source credentials, or authorize live
feature-on/model dispatch. Registry validation is PostgreSQL18.6 scoped.

`CODEX_USER_RUNNERS_ENABLE` defaults to `false` and accepts only `false`/`true`.
The API reads it at construction; no user-facing flag/config endpoint exists.
Operators must not enable this intermediate source as a completed feature.

- `GET /api/auth/me` includes `user.codexRunner`. It reflects approved editor/admin
  entry permission and current research policy/feature flag, not provider readiness.
  false does not revoke research/history/menu/document permissions.
- `POST /api/research/codex/runner` accepts only `{}` and UUID Idempotency-Key.
  A live origin-bound session, current approval/role/policy and Origin+CSRF precede
  receipt replay. UUID representations normalize to the same DB receipt. The current
  transaction atomically records entry/state/outbox; multiple tabs coalesce. Default
  off rejects preparation with403 while legacy status/history remain intact.
- Response200 means an existing Pod-bound lifecycle intent is reused;202 means a
  durable preparation/wait intent. Both use the unwrapped GET status parser. They
  reserve no model turn. Same-key/coalesced preparation never extends activity.
  HTTP supplies a clock sampled after the per-owner database lock. Request arrival
  order may differ from lock order without producing a stale activity timestamp.
  Actual sampled clock reversal is still refused; timestamps are not clamped and
  replay/coalesced preparation still leaves last_activity unchanged.
- Enabled `GET /api/research/codex/status` validates the research/runner schema and
  reads only the caller's registry phase. An authenticated ineligible caller gets
  200/not_enabled_for_account; unauthenticated gets401. GET never creates/wakes/
  renews/updates activity. It exposes no owner/state/Pod/source/private fields.
- Provisioning/wait/unreachable hints are2/15/5seconds; service bodies omit trial
  expiry/count fields. With flag off, the original legacy authorization/mapping and
  trial fields are retained. Auth/research permissions remain server-enforced.
- Enabled /readyz requires the reviewed research and runner structure/grants;
  missing/drifted state refuses with503, with no automatic DDL repair. Off continues
  the existing readiness path, without adopting/repairing any runner schema.

## Unconnected execution boundary

A DB READY row establishes neither a reachable service transport nor authentication/
fixed-model entitlement. In this source chunk READY and STOPPING report
runner_unreachable, available:false; STOPPING is not reported as idle_stopped before
termination evidence. The message endpoint returns the appropriate six fixed
preflight503 codes before message/idempotency-success/tool-token/dispatch admission.
It never uses the old static trial mapping to bypass this gate.

Existing accepted running conversations remain owned/visible with their original
pending input and native record. While enabled, they are not reconciled through
legacy static mappings until the service evidence adapter is connected. Off restores
legacy evidence lookup, but a failed lookup/tool-token expiry is never evidence of
turn termination. No elapsed-time inference, cancellation or automatic paid resend
is introduced. Identical accepted message keys still return their original202.

New six preflight codes are separate from the Native/provider error map: a local
failure before submit may be definitive, but an unknown/network/503 after an attempted
submit remains running/unresolved. Merely receiving one of the new six codes after
submit does not prove pre-intake refusal. Token expiry still denies expired tool
callbacks; it does not prove a model turn ended.

## Remaining acceptance gates

Frontend can now exercise preparation/status/permission/preflight behavior against
an actual FastAPI/API-role/disposable PostgreSQL fixture. Controller phase changes in
those fixtures are controlled data/evidence inputs, not actual Pod/state/auth supply
proof. Normal successful model sending is not integrated or claimed here.

Next integration must bind trusted server-derived service destinations/transport
secrets to current owner/state/generation/Pod UID, fence stale responses, and atomically
connect platform model admission to the registry before dispatch. Native evidence
must provide original request/thread/turn-bound durable terminal timestamps and
uncertainty reconciliation; neither polling time nor a verified flag substitutes.
Controller delivery/claim/capacity/Pod lease/actual previous child termination and
selected same-source expiry/resupply remain to implement and review. Real availability
and verification=real require actual correlated non-fixture completed turns.

Actual conversion additionally requires current live trial user's native/control/
workspace/documents/conversations preservation, new-user provisioning, and registry-
inclusive backups as separate reviewed scopes. Service-v2 JSON conversion is not the
live-trial migration tool. No old backup overwrite, stale JSON rollback, actual supply,
DB migration, deployment or enable is authorized by this source chunk.
