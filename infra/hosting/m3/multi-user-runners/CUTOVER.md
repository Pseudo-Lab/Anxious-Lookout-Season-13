# Issue 16 cutover — PM execution after source and operational review

This is the ordered procedure for the approved source, not evidence of a deployment.
PM builds/imports committed artifacts and executes operations; back does not change
live state. A source `approved:` gate, PR creation, live authorization, operational
acceptance and human merge are distinct decisions. Record failures as failures and
stop at the affected gate. Never reset runtime UID/phase/ledger or recreate retained
storage to make a step pass. Use the linked documents for exact tool inputs:
[provisioning](PROVISIONING.md), [termination/recovery](TERMINATION.md),
[host supply](HOST-SUPPLY.md).

## 1. Freeze scope and preserve evidence

Keep the current singleton account selection and existing version1 runtime env
until migration/import checks succeed. Do not broaden the allowlist or remove the
singleton yet. Inventory API/controller/runner image digests, replica counts, env
selectors, namespace/Node/PVC/PV/Secret UIDs, registry generation/revision/phase/UID,
outbox/admissions/entry receipts, conversations, retained paths and account markers.
Record source and image metadata publicly; credentials, journal, broker, ledger and
full operational inputs stay in a durable root0700 evidence directory on the host,
with files root0600. Do not use a volatile /run directory as the only evidence copy.

**Before any reboot**, export the current-boot k3s journal as JSONL and preserve the
relevant containerd log with metadata/digests. For a reviewed new evidence directory,
use umask077 and noclobber with `journalctl -u k3s -b -o json --no-pager`. Check the
export size against the recovery tool's 16,000,000-byte cap; retain the complete
original privately and produce a reviewed bounded subset if necessary. A large
export is not evidence of absence. Independently record host PID/cgroup namespace
IDs, boot ID, Node identity, and the historical hostname-to-Node mapping. Reboot
invalidates the currently reviewed same-boot legacy recovery path.

Take a verified database backup and retained-storage snapshot/backup through the
existing operational backup procedure before mutation. Verify restoration on a
separate target. Export existing immutable binding plan and resource rows privately;
record their digests and original image pin. Do not expose transport credentials.

## 2. Build, record and import immutable artifacts

PM chooses the **full 40-character reviewed commit SHA** and builds from its git
archive. The documentation commit can be included: use the final source SHA that
review approves, not the historical legacy SHA e5906b0. A clean archive excludes
local edits, .env, private inputs and untracked artifacts. Build all three backend
targets from the same SHA; do not rebuild one role later under the same tag.

```sh
# Set this to the exact reviewed SHA; this example does not select a moving branch.
task_source_sha=<reviewed-40-character-sha>
task_built_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
for task_target in runtime runner provisioner; do
  git archive "$task_source_sha" | docker build -f backend/Dockerfile \
    --target "$task_target" --build-arg APP_GIT_SHA="$task_source_sha" \
    --build-arg APP_BUILT_AT="$task_built_at" \
    -t "anxious-s13-issue16-$task_target:$task_source_sha" -
done
```

`runtime` is the API. `runner` includes the pinned native bundle and the new drain,
idle and pre-stdin UID supply checks. `provisioner` is the trusted worker image and
contains provisioning, observer/idle, host supply, legacy recovery and binding-image
maintenance. It is **not** the API image. Override its ENTRYPOINT to python when
running modules other than ops.runner_provision_worker.

Record build logs and metadata at
`agent-worktree-comm/artifacts/pm/issue16-rollout/<source-sha>/images.json` and link
that sanitized record in issue16. For each target include sourceSHA, builtAt,
architecture, build target, build tag, local Docker image ID, config ID if distinct,
registry repository digest (when published), and imported containerd image identity.
Leave unavailable fields null, never invent digest values. Also record the digest of
the exported OCI/Docker archive and exact import command/output. Tags are labels,
not immutable pins. No source change is needed just to fill this operational record.

For each build inspect `docker image inspect <tag>` and inspect `/app/release.json`
using the **recorded local image ID**:

```sh
docker run --rm --pull=never --network none --read-only --cap-drop ALL \
  --entrypoint cat <local-image-id> /app/release.json
```

Require release.sha to equal task_source_sha. Require Linux/arm64 for the current
node. For the runner, verify the native bundle checksums against
backend/runner/install_codex.py; test the offline protocol suite in Docker. For the
worker, run network-none `python -B -m <module> --help` for runner_provision_worker,
runner_stop_observer, runner_host_supply, runner_legacy_reconcile and
runner_binding_image. Compare module SHA256 values with files from the same archive.
The worker modules are under ops.*. CLI help is a packaging check, not a live proof.

Choose the reviewed registry path or the existing reviewed k3s offline-import
procedure. Capture the registry **repository@sha256:manifest** digest for every
Kubernetes image reference. Do not substitute a Docker config ID as a repository
digest. For offline import, save/load the exact archive into k3s containerd and
verify `k3s ctr -n k8s.io images ls`/CRI inspection resolves the intended repository
name and digest; fail if that exact pin is not available without pulling another
tag. For host Docker commands use the verified local content-addressed ID with
`--pull=never`. The old legacy-only image pin in TERMINATION.md is superseded for
this cutover by this new record; preserve it solely as historical provenance.

## 3. Stop mutators, migrate, then install capabilities

Put Codex entry into the existing maintenance/feature-off boundary, stop the old
API instance(s), preparation/controllers and any external Pod creators. Preserve
the singleton selection in their configuration. Stop timers, including restore,
observer and supply timers; do not merely change an env file under a running process.
This avoids old synchronous stop behavior racing the new schema. Verify no old
process can issue dispatch, prepare, exec or deletion for these owners.

Run these modules from the recorded worker image, with a read-only private admin
DSN mount and ADMIN_DATABASE_URL_FILE. Never put a password/DSN on the command line:

1. `python -B -m app.runner_migrate` (validate/add the runtime schema).
2. `python -B -m ops.runner_bindings_migrate`.
3. `python -B -m ops.runner_termination_migrate`.

Install backend/db/016_runner_controller_grants.sql and
backend/db/016_runner_observer_grants.sql using the reviewed database administrator
channel. These scripts CREATE ROLE once; if roles already exist, compare their
exact grants and membership rather than blindly replaying CREATE ROLE or ignoring
errors. Provision separate login secrets and grant the NOLOGIN capabilities only
to their intended controller/observer logins. API retains no binding mutation,
host storage, source-file or exec capability. Never grant admin to a worker service.
The explicit binding-image maintenance command below uses a short-lived admin DSN
only during frozen maintenance; no new permanent UPDATE grant is introduced.

Verify DB schema/column privilege checks using the new source. Verify private
version2 pool config, CA/TLS and projected token paths and expiry/rotation. Use
separate Kubernetes identities for read-only API, creator/controller, observer
(GET/DELETE) and host supply (GET/exec). Validate named namespace/Node/PV access and
isolated namespace RBAC. API no longer needs DELETE with the new stop schema; remove
legacy DELETE when the old implementation has been stopped. Workload SAs still
have no token/API rights. Do not grant CRI or host auth access to API/runner Pods.

## 4. Import and install stopped service units

Run the immutable operator binding import from PROVISIONING.md with controller
credentials, exact existing v1 runtime/UIDs and uncapped retained paths. Import is
read-only to storage/Kubernetes and inserts only the binding; it cannot release the
legacy FAILED state. Compare imported plan/resources against the private baseline.
Keep the existing operator uncapped exception; new owners use D3 5GiB+1GiB by
default and require allocation+10GiB available. Preserve configured size inventory
for bounded restore/audit; never resize old images by changing environment values.

Install, but do not enable, reviewed external systemd oneshot/timer definitions:
preparation, observer `--idle`, host supply, and bounded-volume restore. Each unit
uses the corresponding recorded image ID and private credential mounts from its
specialized document. Provisioning requires reviewed shared mount propagation and
root filesystem enforcement; observer requires host PID+cgroup/proc+CRI evidence;
host supply requires none of those privileges and must not receive a Docker socket.
Use durable cursor/attempt paths. Supply OnFailure must route sanitized outcomes to
the operator's existing alert channel, with deduplication; no credentials in logs.
The source supplies finite CLIs, not an installed service manager configuration.
Review the generated units, exact mount/network/capability set, kubeconfig plugins,
source UID and read-only source mount **before enabling any timer**. Bounded restore
must precede k3s/preparation after reboot; validate real cap/space and restore in an
isolated test before allowing new production bounded owners. No reboot before
legacy evidence/recovery has completed under its same-boot contract.

## 5. Legacy report, independent review, then apply

Use TERMINATION.md's exact report/apply mount and namespace command, substituting
only the newly verified worker local image ID and the reviewed current private
inputs. Preserve the evidence on durable storage; mount a root-only report directory.
Verify fresh host references and historical Pod attribution. Run **report only**.
A refusal is a blocker, not permission for direct SQL cleanup. Independently review
its owner/state/generation/revision/UID and retained digests/empty audit. Only then,
under the existing operational authorization, run apply with read-only report mount.
Verify idle_stopped, UID null, same state/generation and all eight legacy entry
receipts/history/ledger preserved. Do not start new entry between recovery and the
image transition. If evidence no longer meets the tool's narrow zero-admission
conditions, escalate the concrete mismatch; do not broaden the recovery algorithm.

## 6. Explicit immutable binding image transition

The normal publish/import/worker/API paths remain immutable. Do not patch an old
running Pod to the new image or edit resources to make a spec check pass. A separate
admin-only `ops.runner_binding_image` maintenance operation changes **only**
plan.image after proved termination. All entry/creators/supply/observer remain
stopped. Freshly verify Pod absence and the existing retained resource identities;
archive those read-only checks with the private before-plan/resources export.

Capture the complete old plan JSON from runner_bindings.bindings under the
reviewed admin channel. Compute its canonical hash inside the new worker image,
using `ops.runner_binding_image.plan_digest` (sorted JSON, compact separators),
not a whitespace-sensitive file hash. Supply a root0600 JSON file containing
exactly owner, state, generation, revision, oldPlanSha256 and newImage. The fence
is the **post-recovery** registry fence, not the earlier FAILED revision. newImage
must be the recorded runner repository@sha256 digest, never the worker/API image.

With admin credentials mounted read-only, the worker needs only DB access and
private input/report paths; no CRI/Kube/source/retained-volume mounts:

```sh
# Substitute the recorded worker local image ID and reviewed private paths.
docker run --rm --pull=never --user 0:0 --read-only --cap-drop ALL \
  --security-opt no-new-privileges --network <reviewed-db-network> \
  --mount type=bind,src=<private-input-dir>,dst=/private,readonly \
  --mount type=bind,src=<private-report-dir>,dst=/reports \
  -e ADMIN_DATABASE_URL_FILE=/private/admin-dsn --entrypoint python \
  <worker-local-image-id> -B -m ops.runner_binding_image report \
  --inputs /private/binding-image.json --report /reports/binding-image.json
```

Independently review the report, then use the same invocation with `apply` and a
read-only report mount. It locks membership/owner and binding row, requires the
exact STOPPED fence, no active/ambiguous/pending work, no unsettled outbox or pending
stop/entry intent, and a completed same-generation termination audit. It validates
the full expected plan hash and new strict digest image, then compare-and-swaps
only image. Registry, generation, resource rows and published_at are unchanged.
Existing normal-path DB roles cannot perform this write. Repeating the old command
after success refuses the changed hash: inspect the after-plan instead of retrying.
Archive before/after/report digests in the operational record.

Both images must implement the same retained broker/ledger/native format and
owner/state/reference contract. The new runner must contain drain, monotonic idle,
and `access_supply --expected-pod-uid` support before normal observer/supply is
started. API, worker and runner come from this same reviewed source record. Format
incompatibility blocks cutover; recovery and image migration never rewrite history.
For a reverse image change the same stopped/proved fence, fresh report, old plan
hash and reviewed compatibility are required; never reverse image on a live Pod.

## 7. Canary, expansion and acceptance

Start the new API in pool mode while retaining singleton/explicit canary selection;
remove the mutually exclusive v1 config env only in this reviewed rollout. Start
preparation, observer/idle and supply units with the matching selector/feature env.
Enable one canary owner's authenticated normal entry, verifying same state and next
generation, exactly one creation permit/Pod and unchanged retained history. Verify
native login, real provider response, idle/logout drain, UID-precondition deletion,
host termination proof and session-authorized re-entry. Healthy GET polling must
not create work, reset idle, re-login or consume source permits. Verify operator
alerts with synthetic failure, not by damaging real credentials.

Only after the canary passes, remove the singleton or set the explicit wider
allowlist consistently on API and all workers, preserving feature flag, maxPods
and idleSeconds settings. Restart/reload each process; env edits alone do not apply.
For two approved test owners independently verify distinct state/Pod/PVC/PV/Secret
UIDs, source account sharing without token exposure, history/turn isolation,
cross-owner API denial, Pod-to-Pod and API-server network denies, actual responses,
logout/relogin and idle reclaim, capacity_wait at the configured limit, one slot
release then one waiting-owner admission, revoked session cancellation and no GET
side effects. Capture sanitized results and private evidence locations in issue16.
This gate needs actual cluster/provider tests; Docker source tests do not satisfy it.

## 8. Rollback and stop conditions

On evidence/identity/permission/space/provider failure stop expansion, disable entry
and stop timers/creators. Preserve all unknown permits, receipts and retained data.
Use the reviewed new observer to terminate any newly started runner cleanly where
possible; absent proof keeps its slot reserved. Do not force-delete/reset to unblock
rollback. Preserve FAILED/STOPPING and send the concrete blocker to back/review.

Prefer configuration rollback to the singleton with the new schema-compatible
API/workers. Do not restore the old synchronous-delete API while termination schema
and new workers are active. Stop all processes before any binary rollback, and
verify old/new schema and data compatibility on a restored isolated backup first.
No automatic schema drop/downgrade is supplied. A database backup restore is a
separate operator-reviewed recovery with all writers stopped and data consistency
checks; do not overwrite newer conversation/ledger history blindly. Image reversal
uses step6 only after proven stop and compatible retained formats. Never delete
new owners' retained volumes, resources or evidence as rollback cleanup. Keep the
reviewed new worker artifact available until every outstanding intent is resolved.

## Source review record and remaining execution gates

As of the final source preparation, issue16 has no open reviewer minor backlog
items; earlier front design comments are not new backend minor findings. No minor
sweep commit is needed. All prior feature slices have partial approval; this document
and the explicit binding-image tool are submitted with the final single source SHA
for full-scope **source** review. Image build/import, private reports, generated
units/RBAC/network, host/reboot/provider/two-owner execution and rollback rehearsal
remain PM evidence requirements. Do not describe an unrun gate as passed.
