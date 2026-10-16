# Provisioning and dynamic plans — source checkpoint, no live approval

The API records registry/outbox intents. A separate root host preparation worker
(`ops.runner_provision_worker`, finite `--limit 10` batch) creates retained resources,
publishes immutable bindings, then lets the existing Controller issue one Pod create
permit per generation. The worker never reads provider credentials. API credentials
have SELECT only on the new binding schema. API dispatch resolves a fresh owner/state
plan and exact UID-bound transport Secret per operation, without restarting the API.

Run the explicit `ops.runner_bindings_migrate` with administrator DB credentials
before configuring the pool. It adds a separate schema and verifies its exact
contract; API startup never performs DDL. The separate
`backend/db/016_runner_controller_grants.sql` creates a NOLOGIN capability role;
a deployment-specific controller login receives that role and its own secret.
Do not grant it to anxious_api. The controller needs SELECT of approved membership,
registry lifecycle/outbox rights and binding INSERT, not auth/research writes.

`CODEX_USER_RUNNER_POOL_CONFIG_FILE` is a private version-2 JSON file containing
exactly version, api, namespace, namespaceUid, podCIDR, caFile and kubeTokenFile.
Its Kubernetes token is a projected service-account token. It is mutually exclusive
with CODEX_USER_RUNNER_CONFIG_FILE. Runtime plans come from the DB; provider tokens
and host source paths are absent. The API Kubernetes Role for this mode needs
namespace-scoped GET of Pods/PVCs/Secrets and DELETE of Pods, plus named namespace
metadata GET. No create/exec/PV/root permission belongs in that API role. Controller
needs namespace PVC/Secret/Pod create/get, named prerequisite SA/KNP reads and
cluster PV create/get plus named namespace metadata GET. PV CREATE is inherently
cluster-scoped trusted privilege; exact path/spec generation and review remain gates.
No workload service-account token or new runner-to-runner/API-server allow rule.

## Existing operator import

Keep the current singleton env/config active until the reviewed cutover. Run
`ops.runner_binding_import --inputs <private-input>` using controller credentials.
Input keys: runtime (the existing version-1 runtime JSON with the actual private
transportTokenFile path), kubeTokenFile, caFile, storageRoot and expectedAccountId.
The import reads namespace UID/PSA, PVC and Secret UIDs, PV labels/claim UIDs/Retain
and local paths, native account/state markers, operator-account and transport token.
Only an immutable DB binding is inserted. Repetition accepts the same binding;
drift or cross-owner shared refs fails. It does not prepare storage, reset failed
state/receipt/ledger, recover a missing Pod or establish termination evidence.

The existing operator path is **uncapped** on root XFS (`noquota`). Import preserves
that exception. Before claiming all-owner hard caps, explicitly choose either
(a) root prjquota activation with approved reboot, or (b) verified stop then copy
into bounded storage, integrity verification and a separately reviewed binding
migration. Neither is performed by import; immutable bindings cannot be silently
rewritten by the API or this worker. PM must present this exception at deployment
approval. New bounded layout is state/native/data and state/control/data; legacy
import uses state/native and state/control.

### Legacy failed operator reconciliation — execution gate

The live operator registry is reported as `runner_failed` after #13. Its old
runner has no new termination receipt. Binding import above does not make it
eligible for entry. `Registry.enter` rejects this phase, and `Registry.stopped`
only accepts `stopping`; neither is a recovery command for this legacy case.
At source checkpoint 089df16 there is **no executable legacy recovery tool**.
Do not substitute a direct SQL phase/UID reset, call an adapter with a fabricated
`verified=True`, replay the old one-shot create permit, or manufacture a receipt.
The observer slice must supply a tested recovery tool and exact pinned-image
invocation here before PM can execute this transition. This section specifies
its required evidence and preservation boundary, not approval to change live state.

PM's read-only inventory for that operational review must contain:

- Registry owner ID, state ID, generation, revision, phase, Pod UID, activity,
  active/ambiguous fields; related admissions, entry receipts and outbox records.
- Bound namespace name/UID, Pod name and current GET result, including UID,
  nodeName, deletion timestamp and container IDs if present. Retain the previous
  UID-bearing Pod/create evidence if the API object is absent.
- Node name/UID, current Ready condition and heartbeat, node boot ID, and the
  relation between that node and the old Pod. A replacement node with the same
  name is not sufficient evidence.
- Actual CRI Unix socket endpoint, runtime/version, container and sandbox
  inventory scoped to the old Pod UID, and the host proc mount path. The proc
  view must cover the host PID namespace and readable process cgroups; record
  its boot ID and collection times. Do not expose a runtime socket to the API.
- Canonical retained native/control paths and layout (legacy uncapped or bounded),
  PV/PVC/Secret names and UIDs, state/account markers, broker mapping, and existing
  permit/operation evidence locations. Record metadata and integrity evidence
  privately; never copy credential values or conversation text into messages.

The reviewed recovery tool must first produce a read-only report bound to this
exact owner/state/generation/revision/Pod UID and evidence timestamps. An operator
must reconcile unfinished admissions and uncertain conversation/native work from
their original evidence; a dead process does not prove a request completed.
Missing receipts cannot be replaced by ledger interpretation: `nativeAttached`
records attachment history, not process liveness. Preserve that ledger unchanged.

Before a recovery commit, fence dispatch and all creators for this owner, verify
the exact Pod is absent, and independently establish termination through the
trusted node CRI and host process view. Pod 404, an empty container query alone,
or a stale snapshot is insufficient. Recheck membership and the full registry
fence under the owner lock, and refuse changed identity, pending/uncertain work,
incomplete process visibility, node/boot drift, or mismatched retained bindings.
Evidence collection failures stop recovery; they never count as absence.

The intended recovery result is a separately audited, transactional release into
`idle_stopped`, clearing only the confirmed old Pod association and preserving
owner/state identity and generation. The next authorized entry allocates the next
generation via the normal path. Recovery must retain old entry/create receipts,
admissions/completions, native conversation history, service ledger, transport
bindings and storage; it must not delete/reformat data or re-run preparation.
Any legacy inconsistency that cannot meet these conditions remains `runner_failed`
for a separately reviewed resolution. The final tool, transition tests, safe
repeat behavior and invocation are outstanding observer-slice release gates.

## New storage and host service

Build the separate Docker `provisioner` target. The helper runs as root, with only
the dedicated storage/output/input roots exposed and reviewed controller credentials.
It requires loop-device access and SYS_ADMIN for mounts in a host-visible shared
mount subtree; these are trusted host privileges, never API privileges. Before
running it, verify the dedicated storage root is a shared bind mount and Docker's
mount is rshared. Allocation refuses a missing shared mount. Do not run this image
as the API image. Exact device/propagation/container invocation must be reviewed
on the host before enabling any scheduled worker.

Use a root systemd oneshot wrapper for one finite batch, with a timer such as
OnUnitInactiveSec=15s (not a permanent in-process watcher). Wrapper invokes the
pinned provisioner image with `--inputs`, `--outputs`, `--limit`; mount secret files
read-only, set DATABASE_URL_FILE and CONTROLLER_DATABASE_URL_FILE to the same
controller secret. Inputs reuse user_runner_prepare's trusted infrastructure fields,
without owner: owner derives from the authenticated registry intent. No shell text
or paths come from the browser. Output root is root-owned mode0700; individual
finished binding output directories are UID10001/mode0700.

Set `CODEX_USER_RUNNERS_ENABLE=true` explicitly in the worker environment, with
the same `CODEX_USER_RUNNER_ACCOUNT_ID` or `CODEX_USER_RUNNER_ALLOWLIST` selection
as the API. Unset/false disables pending selection and refuses direct processing,
including already published plans; invalid flag values fail closed. Selection is
applied before the batch LIMIT so excluded owners cannot starve allowed owners.
Changing an API process environment does not change a running worker environment:
disable/stop the worker service as part of a reviewed operational flag-off change.
This finite worker does not provide a separate lookup-only reconciliation mode.

For each new owner the helper allocates native 5GiB and control 1GiB files by default
with posix_fallocate, formats
fresh ext4 filesystems once, mounts nodev/nosuid, then initializes empty data roots
UID/GID10001. Existing/partial output is never overwritten or formatted again.
Set `CODEX_RUNNER_NATIVE_SIZE_GIB` and `CODEX_RUNNER_CONTROL_SIZE_GIB` to positive
whole GiB integers (defaults 5 and 1). Empty, fractional and invalid values refuse
before allocation. PV capacity and PVC requests use these same configured sizes.
Every new allocation requires the sum of both sizes plus 10GiB remaining free disk
headroom (16GiB available at the defaults). Pod count
(default3, configurable) does not cap retained owners' disk usage: stopped owners
still occupy 6GiB at the defaults. Capacity/disk refusal needs operator visibility
and no deletion. The measured 79GB free after Docker cleanup on 2026-10-16 is
planning evidence, not a promise for later allocations; each allocation checks free
space again. Existing uncapped operator import behavior remains unchanged.

Keep the same size environment in preparation, binding audit, observer and restore
processes for each bounded owner. Record the settings with its private restore
inventory. Changing settings does not resize existing images: mismatched images
fail verification/restore. Mixed-size inventories require invoking these tools with
each owner's recorded settings; do not globally change settings and assume old
images will be resized or adopted.

## Reboot and failure recovery gates

Before enabling the timer, install a reviewed root oneshot restore service ordered
After=local-fs.target and Before=k3s.service and the provision timer. It must recreate
the dedicated shared bind mount, then invoke `ops.runner_storage --restore
/var/lib/anxious-lookout/user-runners/<state-hex>` for each published **bounded**
owner. Derive this list from reviewed published bindings/manifests, not arbitrary
user input. Existing uncapped operator storage is skipped. Restore mounts existing
images only, requires empty underlying mountpoints, checks size/ownership and
filesystem bounds, and never runs mkfs or cleanup. Persist the restore inventory
before allowing the first Pod for a newly provisioned owner. Until this orchestration
and restart probe are reviewed/installed, do not enable production auto-preparation.

After restore, audit owner/state markers and Pod/PVC/PV references before dispatch.
A missing image, failed mount, partial preparation or ambiguous Kubernetes create
must retain data and halt that owner's preparation for reconciliation. Controller
settled create receipts prevent duplicate Pods. No force deletion or automatic
resource adoption is introduced. Private partial output is evidence, not garbage.

Source tests use disposable PostgreSQL and fake Kubernetes/storage adapters.
They do not establish real loop-mount propagation, reboot recovery, native startup,
provider access, two-owner network denies or end-to-end responses. Those are later
reviewed PM operational checks, after remaining #16 lifecycle/supply slices.
