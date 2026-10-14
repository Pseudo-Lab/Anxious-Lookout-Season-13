# Issue #9 operator package — proposed, no live execution

The package emits private JSON; it cannot call kubectl, import an image, supply
auth, reserve/replay a turn or restore live data. PM executes only reviewed
artifacts within existing user authorization. Final application source approval,
package/source review, immutable image review, operational execution and final PR
approval are distinct gates. Do not build a release from an earlier Front candidate
while source revision is pending.

## Recorded baseline and permitted diff

Read-only preparation captured the existing Deployments and immutable configs on
2026-10-14. `common_chatbot_prepare.py` seals their full defaulted specs, names,
namespaces, UIDs and config digest. It also checks the previously reviewed Retain
PVC/PV UIDs/claimRefs/Bound state/node through `owner_prepare.storage`.

| Role | Existing Deployment | Existing image |
|---|---|---|
| API | m2-hosting/api | access-api-9157719 @ 597a505f… |
| Web | m2-hosting/web | personal-web f5 @ a82a2ac0… |
| Runner | codex-trial/access-web-runner-v1 | access-runner-9157719 @ 2948da59… |

Only these changes are emitted: API `CODEX_PERSONAL_ENABLE=true→false→true`, each
role's immutable image, runner replicas `1→0→1`. All other defaulted spec fields,
owner/state, config/Secret references, volumes, probes, strategy, model, network,
OAuth, root origin/base path, dispatch windows and existing data remain exact.
Unknown drift is refused, including during recovery. A fresh resourceVersion/full
spec test is included in every JSON Patch. Admission hold is temporary deployment
maintenance; it does not revoke the existing user's authorization or reset auth.

The backup reader is one temporary same-node Pod with two existing PVCs mounted
read-only, no fsGroup/chown, credentials/vendor, SA token or Service. Its four env
fields identify only the existing owner/state and native/control paths. A separate
explicit deny-all CNP selects **only** this new backup-reader label. It does not
modify existing network policies. Use create, not apply/adoption of an old reader.

## Final artifact preparation (back)

After reviewer approval of the application source, local builds can proceed while
the package source is reviewed. The source/tool/artifact combination requires
independent approval before PM imports or applies anything:

```sh
bash infra/hosting/m3/build_common_chatbot.sh FINAL_APPROVED_APP_SHA /tmp/issue9-chatbot-build-UNIQUE [CLEAN_APP_SOURCE_WORKTREE]
```

The wrapper requires exact clean HEAD, builds production API/runner/web targets
for linux/arm64 with root web base path, source labels and release metadata, and
records build logs, metadata, inspect, tar and local audit for each role. It uses
`access_image_audit.py` to hash every archived layer and bind index→arm64 manifest→
config→source. No native process/auth is started. No registry push/containerd
import occurs. `images.json` has `{sourceSha,api,runner,web}`.

When tooling commits follow the approved app SHA, back creates a dedicated clean
detached `/tmp/issue9-chatbot-source-SUFFIX` worktree at that app SHA. The wrapper
reads application inputs there, validates no application-input diff against the
tooling tree, and records both applicationSourceSha and toolsSourceSha in build.json.
Added operator files are not falsely called approved application changes.

PM's reviewed existing import procedure imports only those three archives after
independent source/archive/hash review. Capture imported target.json,
manifest.json, config.json and cri.json separately for every role. Then run in
an offline Docker Python container with read-only `/checks`, `/build`, `/imported`:

```text
python -B /checks/common_chatbot_image_audit.py ROLE --source FINAL_APPROVED_SHA
  --build /build --imported /imported/ROLE --reference EXACT_IMPORTED_REF [--pod /inputs/ROLE-pod-image.json]
```

The imported index may differ; its arm64 manifest/config must match the measured
archive and source label. Preserve local `images.json`; if necessary write a
separate reviewed imported-images.json with actual exact references. Never edit
the local audit, trust a tag or use existing sealed #7 pins for new images. After
rollout, minimal Pod association input is `{specImage,criId,podImageId}` from the
actual selected Pod/CRI. Require all three audits, exact final SHA and source
`/api/version`, `/version.json` checks before considering the rollout matched.

## PM input and command conventions

Use existing reviewed kubectl/Docker wrappers and trust context. Full input,
rendered specs, logs and backups stay in a fresh private directory (0700, files
0600), with `set +x; umask 077`, no TTY or session payload recording. Placeholder
paths below are inside the Docker mount or PM's private operator directory;
they are data inputs, not an instruction for PM to write new code.

1. Collect fresh read-only `hosting-deployments.json` (api+web List), runner.json,
   configmaps.json (hosting-root-https-v1+hosting-access-web-v1), pvcs.json and
   pvs.json. Do not read Secret data or original Codex auth. Seal with:

   ```text
   python -B /checks/common_chatbot_prepare.py seal --baseline /inputs/snapshot-dir
   ```

   Redirect to private baseline.json. Spec/config/UID drift from the recorded
   baseline stops preparation; report to back, never silently re-seal it. The
   earlier preparation capture is not operational freshness or quiescence proof.
   Record fresh Secret UID/resourceVersion metadata (hosting-api,
   hosting-access-web-input-v1 and access-web-transport-v1), config/PVC/PV identity,
   existing Service selectors and network policy metadata privately. Do not export
   Secret data. Compare them before/after each stage: image patches cannot modify
   those resources, but external drift/rotation must stop this operation too.
2. At every stage collect fresh current.json as `{api:<Deployment>,web:<Deployment>,
   runner:<Deployment>}`. Rendering command, in Docker (offline/read-only):

   ```text
   python -B /checks/common_chatbot_prepare.py ACTION --baseline /inputs/baseline.json
     --release /inputs/imported-images.json --approved-source-sha FINAL_APPROVED_SHA
     --current /inputs/current.json [--evidence /inputs/evidence.json]
   ```

   Output is `{target:{namespace,name,uid},patch:[...]}`. Back's fixed recipe
   `jq '.patch' packet.json > patch.json` extracts the patch. PM checks the exact
   target/diff, server dry-runs `kubectl -n TARGET_NS patch deployment TARGET_NAME
   --type=json --patch-file patch.json --dry-run=server -o json`, then applies
   **that** patch after review. A stale RV or unexpected diff requires a fresh
   current capture and re-render; never force it or replace a test.

## Forward order and evidence

1. Render/apply `hold-api`. Wait for updated/observed/ready generation1 and exactly
   one current API Pod with hold=false effective env, no old enabled API Pods.
   Here hold=false refers to `CODEX_PERSONAL_ENABLE=false`. Capture exact API Pod
   UID/image/owner ReplicaSet→Deployment association before/after stdin exec.
2. On that held API Pod, one bounded reviewed read-only stdin exec:

   ```text
   timeout 25s kubectl -n m2-hosting exec -i EXACT_API_POD -c api -- python -B - api < common_chatbot_probe.py
   ```

   It reads only the already mounted API DB credential, uses a READ ONLY DB
   transaction (connect3s/statement3s/lock1s/process20s), validates auth/research
   schema/grants and returns safe counts. On the exact current runner Pod:

   ```text
   timeout 25s kubectl -n codex-trial exec -i EXACT_RUNNER_POD -c runner -- python -B - runner < common_chatbot_probe.py
   ```

   It reads only existing native binding/broker and control ledger, returning
   owner/state match, running/ambiguous counts, ledger hash/lifetime count. It
   never prints text, UUIDs, provider identity, MAC key or credentials. Require
   both running counts0 and ambiguous counts0. Unknown/failed-unavailable work
   requires reconciliation with safe records; do not replay, reset or archive
   it merely to pass this gate. A failed/timeout check is unconfirmed.
   This package supports only the **existing used access-only owner**, not a new
   unused installation. The five protected files are native account-id,
   state-binding.json and broker.json, plus control access-ledger.json and
   access-projection.json. All must exist as owned regular files. The probe checks
   ledger owner/state/trial/window and unique reservations, exact ledger↔broker
   dedup coverage (no orphaned or multiply assigned reservations), journal binding
   and integrity MAC, and saved history projection
   MACs without loading auth or starting native. A missing/empty broker or ledger,
   missing journal, or inconsistency stops the operation without initialization.
   Save the first successful held/quiescent runner check unchanged. Extract its
   safe comparison anchor with the fixed command
   `jq '{protectedState,lifetimeReservations}' runner-check.json > preservation.json`.
   Keep this original anchor throughout the operation; do not regenerate it to
   bless a changed file. Compare it against every stopped-reader and post-start
   probe. It contains only five SHA256 digests and the lifetime count, not contents.
3. Save observations.json as **verified data** from fresh Deployment/Pod/health/
   public-read evidence: `checkedAt`, `apiHoldObserved`, `runnerPodCount`,
   `backupPodCount`, `referenceMetadataVerified` (the unchanged external references
   and metadata above), and later `apiReady`, `webReady`, `runnerReady`,
   `podImagesVerified`, `publicReadVerified`, `authAvailable`, `authReason`.
   Do not mark a check true because a command was planned. Create evidence with:

   ```text
   python -B /checks/common_chatbot_prepare.py receipt --baseline /inputs/baseline.json
     --current /inputs/current.json --api-probe /inputs/api-check.json
     --runner-probe /inputs/runner-check.json --observations /inputs/observations.json
     --preservation /inputs/preservation.json
     [--backups /inputs/backups.json]
   ```

   It combines zero-count checks, preserves the oldest check time and binds the
   result to current UID/full-spec digest. Checks expire after120s; refresh them
   before each mutation. This is an operator receipt, not cryptographic proof of
   review or of its capture origin. Review the underlying logs/Pod associations.
   All three timestamps are checked individually, including future clocks.
   Receipt requires exact protected-file digests/count against the unchanged
   preservation anchor and records them with protectedStateVerified=true.
4. Render/apply `stop-runner`; normal graceful termination, no force deletion.
   Require Deployment0 and selected runner Pod count0. No source auth is supplied.
   Render `backup-reader --baseline ...` without any release input. Verify the
   new Pod/CNP names do not exist; dry-run/create the deny CNP **before** the Pod.
   Capture created UIDs/specs. Never use the app=access-web-runner label. No RWO
   reader may coexist with a native writer. The reader sleeps and starts no app.
5. On the exact reader Pod, the same runner probe reads stopped state using its
   four preserved env fields. Stream a full native/control tar via stdin exec:

   ```text
   timeout 120s kubectl -n codex-trial exec -i EXACT_READER_POD -c reader -- python -B - stream < common_chatbot_archive.py > FRESH_PRIVATE_NATIVE_CONTROL_TAR
   ```

   Use shell noclobber for the fresh tar. Never tee/print it or use Pod logs for
   transfer. The source is bounded, owned, no symlinks, with only the exact stale
   supply socket skipped, and detects any inventory/file change. Partial output
   stays private and is not a valid backup. Verify locally in an offline read-only
   Docker with `common_chatbot_archive.py verify --archive /backup/state.tar
   --expected-state /inputs/preservation.json`, with /checks on PYTHONPATH.
   It only inspects; it never extracts or restores a live ledger. Record SHA256.
   Verification requires all five files in the tar, repeats binding/reservation/
   journal/history integrity checks from their archived bytes, and compares their
   exact digests/count to the original anchor. Structurally valid but changed or
   incomplete archives are rejected. Save the safe archive-check.json result.
6. Create a fresh full logical DB dump with the existing reviewed
   `infra/hosting/db-backup.sh k3s FRESH_PRIVATE_DB_DUMP` and record its checksum.
   Verify in an isolated Docker clone with:

   ```sh
   bash infra/hosting/m3/common_chatbot_restore.sh PRIVATE_DB_DUMP VERIFIED_API_DOCKER_ID
   ```

   The clone uses no live network/PVC/credentials. Existing restore_candidate
   validates schemas/grants, revokes cloned browser/tool logins and preserves
   research row counts plus native_record checksum. It deletes only its own
   temporary clone. Nothing is copied back into production. backups.json contains
   `dbBackupSha256,nativeControlBackupSha256,dbRestoreVerified,
   nativeControlArchiveVerified`, derived from successful actual checks.
   Also copy `protectedState,lifetimeReservations` from archive-check.json into
   backups.json. Receipt requires these equal the original preservation anchor.
7. Remove **only** the exact newly created reader Pod/CNP with UID-preconditioned
   DeleteOptions through the reviewed raw delete path. Preserve all original
   resources. Wait for reader Pod count0 and release of its RWO mounts. Do not
   start a runner merely because the reader process timed out.
   For each resource, render `cleanup --baseline /inputs/baseline.json --created
   /inputs/created-resource.json --current /inputs/fresh-resource.json`. It binds
   the actual created UID and current RV, refuses replacement/spec drift, and
   permits only the scheduler's same-node nodeName default. Extract `.body` as
   private delete-options.json, then `kubectl delete --raw EXACT_PACKET_PATH
   -f delete-options.json`. Delete the Pod and confirm absent before its deny CNP.
8. With API held, fresh probes/evidence/backups and runner0/Pod0/backup Pod0:
   `upgrade-runner`, `upgrade-api`, `upgrade-web` in that order. Every action
   changes only its approved role image (API remains held; runner remains0).
   Recollect current and regenerate evidence per stage, using the stopped-state
   probe through the reader **before its removal** and independent continued
   Pod0/no-writer observation afterward. If that stopped check expires, create a
   new isolated reader only after confirming absent names, then check/remove it
   again. Do not fabricate fresh probe timestamps.
9. Render/apply `start-runner`, require reader0/old runner Pod0. Wait for current
   runner Ready1/owner-state binding and API/web Ready1. Verify exact imported
   Pod image chains, schema/readiness, root HTTPS origin/base path, auth routes,
   public page/versions and recorded conversations without submitting a question.
   Capture the actual runner health through its already reviewed authenticated
   local transport path; do not export that transport secret. Record the safe
   auth availability/reason separately from Pod readiness.
   Run the same read-only runner probe on the exact new Pod before any auth supply
   or question and compare it to preservation.json in receipt. A complete but
   changed ledger/broker/journal is not accepted as preserved. Keep pre/reader/
   archive/post receipts privately for review. Confirm existing conversations and
   their native session/request associations against the DB snapshot using the
   reviewed read-only records; these file checks do not substitute for DB/native
   request-by-request reconciliation. Any mismatch stops admission restoration.
10. Only if all three new specs match and read-only checks pass, `resume-api`
    restores the original enable=true env. It does not install an access token.
    In the existing owner's authenticated browser, read `/api/research/codex/status`
    and panel state without Send. When auth is unavailable, require clear matching
    not_configured/expired/revoked/budget/model/unavailable guidance and disabled
    send. Read-only success plus enable=true is **not** model/auth success.

## Auth recovery and actual acceptance

Runner restart loses its in-memory access snapshot, so unavailable/not_configured
is expected until explicit recovery. Existing user reconnection authorization
and the reviewed ACCESS-WEB-TEST.md/supply_access_web.sh procedure remain valid
for the same approved account, source and environment; this package does not
require repeating human permission for that already authorized scope. PM checks
that authority and executes the reviewed step when appropriate. Back performs
no supply or automatic rearm during preparation. New account, destructive changes
or ambiguous turn replay are separate scope.

Preserve owner/state/provider binding, old windows, lifetime reservations, request
dedup and projection journal. Use existing same-window vs new-window rules; never
restore a prior ledger or auto-create a window to replenish budget. Verify source
eligibility without exposing tokens, actual supply receipt and subsequent owner
status/expiry/admissions before any real turn. Source/fixture/read-only checks do
not prove provider/model entitlement. Real general/page/tool acceptance is a
separate PM phase within existing authorization and finite window budget, with
safe failure evidence and no automatic resubmit if dispatch is unknown.

## Recovery order

On failure keep admission held (or render `hold-api` on the new enabled version),
capture safe evidence, resolve running/unknown work, and `stop-runner`. From fresh
exact current metadata with backup and quiescence evidence, roll back only roles
that were upgraded: `rollback-web`, `rollback-api`, `rollback-runner`. Mixed known
stages are supported; unrelated drift fails closed. Native/control and DB remain
the latest retained data. Start the original runner only after reader/runner Pod0,
verify all three original specs/readiness/image/binding, then `resume-old-api`.
Never DB-downgrade, overwrite current history, extract an old control backup or
replay a #9 request with old code. Never reset the preservation anchor to make a mismatch pass. After authorized supply/
real turns, protected files legitimately change; an earlier maintenance anchor
cannot authorize a later rollback. Stop and obtain independently reviewed
reconciliation of the latest DB/native/control records and a new private anchor
with its lineage before rendering that later operation. Preserve the old anchor.
The old UI may display new native preambles and
lacks #9 scope badges; rollback restores the prior application behavior. Catastrophic
storage recovery is outside these image patches and needs a separately reviewed
data reconciliation procedure; the backup is recovery evidence, not an automatic
reset. Auth recovery follows the same existing authorized procedure after rollback.
