# Static synthetic node-local PV preparation

User authorized this work type in Issue10. Exact inputs/render/procedure review
still precede PM execution. No commands in this document have been executed by
backend. Namespace is codex-runner-rehearsal (PM confirmed NotFound on2026-10-15).
Node hostname label must still be verified as anxious-lookout before applying.

05-static-volumes.json.in defines two **static local** PVs backed by dedicated
node directories, not requests to the rancher local-path provisioner. This uses
node-local paths with Retain and explicit node affinity; it neither changes a
StorageClass nor adopts dynamically provisioned codex-trial storage. No hostPath
Pod volume is introduced. Static local PV class is explicitly empty, avoiding the
default Delete StorageClass and WFFC. The corresponding claim render must use
RUNNER_STATIC_STORAGE_CLASS="", RUNNER_APPROVED_ACCESS_MODE=ReadWriteOnce,
matching native/control sizes, PV names and RUNNER_TEST_NAMESPACE=codex-runner-rehearsal.

PV names must be distinct, new cluster-wide names. Synthetic owner/state must be
fresh canonical UUIDs. Both state and owner must agree across PV/PVC/Secret/Plan.
Sizes are still explicit rendered inputs, not an inferred node capacity or a hard
filesystem quota. Local PV declared capacity does not impose per-directory disk
limits. ReadWriteOnce is not a process fence, and Retain is not backup/encryption.

## Host preparation, PM only after review

Run the reviewed commands on node anxious-lookout with the approved administrative
identity, not inside a normal runner container. First inspect node identity,
filesystem/mount/free-space and every existing ancestor using read-only host tools.
Refuse symlinks in `/var`, `/var/lib`, `/var/lib/anxious-lookout`, the rehearsal base
or any state path. Check ancestors are controlled by root and not group/other
writable. Do not change permissions of existing application directories to make
this check pass. Stop if the base overlaps existing runner/provider/user data.

The concrete procedure must preserve these steps; replace STATE_UUID only with
the approved canonical UUID, not arbitrary user text:

```sh
# Run only after the ancestor/node checks above. Private evidence, no secret data.
RUNNER_STATE_UUID='STATE_UUID'
RUNNER_BASE='/var/lib/anxious-lookout/runner-rehearsal'
# PM verifies the UUID and that the state directory does not exist before mutation.
# If the two base directories are absent, create only those missing directories
# with root ownership and mode0700 using the reviewed host preparation plan.
# Do not recursively chown/chmod an existing path.
mkdir -- "$RUNNER_BASE/$RUNNER_STATE_UUID"
chmod 0700 -- "$RUNNER_BASE/$RUNNER_STATE_UUID"
mkdir -- "$RUNNER_BASE/$RUNNER_STATE_UUID/native" "$RUNNER_BASE/$RUNNER_STATE_UUID/control"
chown 10001:10001 -- "$RUNNER_BASE/$RUNNER_STATE_UUID/native" "$RUNNER_BASE/$RUNNER_STATE_UUID/control"
chmod 0700 -- "$RUNNER_BASE/$RUNNER_STATE_UUID/native" "$RUNNER_BASE/$RUNNER_STATE_UUID/control"
stat -c '%n %u:%g %a %F' -- "$RUNNER_BASE/$RUNNER_STATE_UUID" "$RUNNER_BASE/$RUNNER_STATE_UUID/native" "$RUNNER_BASE/$RUNNER_STATE_UUID/control"
```

Commands are a reviewed sequence, not an unattended script: stop at the first
failure. Use restrictive umask077 before mkdir. The state directory remains root
owned; only the two mounted leaves become10001:10001. No `mkdir -p` for the state
path, no recursive ownership change, no rm/reuse on collision. Run preparation
serially with no untrusted writer of the parent. A partial result is preserved
for investigation rather than retried against unknown contents. Record stat and
filesystem evidence; never initialize with copied real credentials or state.

## PV/PVC sequence and acceptance

1. Apply reviewed foundation and confirm namespace UID, quota0, default deny and
   no workload grants. Confirm neither PV name nor the two claim names exists.
2. Complete the host preparation above. Ensure empty native/control leaves; no
   runner or initialization Pod is needed for static binding.
3. Create the two individually reviewed PV objects (cluster-scoped). Inspect their
   assigned UIDs, local paths, node affinity, empty class and Retain. claimRef
   reserves each exact namespace/name before PVC creation; it has no invented UID.
4. Create the two reviewed static PVCs. Verify Bound, volumeName and mutual binding:
   each PV claimRef.uid must equal the actual PVC UID, and namespace/name match.
   Read and record both PV UIDs as well. Pending or mismatched identity means stop;
   never clear claimRef/UID, delete/recreate or switch classes to force binding.
5. PM injects the synthetic immutable transport Secret through the separate private
   procedure. Record metadata only. These empty volumes are not initialized native
   or control state: initialization belongs to the one-shot test-path review.

Sample read-only checks from the authorized diagnostic container:

```sh
kubectl --context APPROVED_CONTEXT get node anxious-lookout -o json
kubectl --context APPROVED_CONTEXT get pv APPROVED_NATIVE_PV APPROVED_CONTROL_PV -o json
kubectl --context APPROVED_CONTEXT -n codex-runner-rehearsal get pvc native-synthetic control-synthetic -o json
```

Keep output private (paths/identities). Do not dump Secret resources. No cleanup,
namespace deletion, PV release, reclaim or automatic retry is supplied here.
Retain and bound identities must survive an uncertain test. No live codex-trial
resources or host paths are changed. This does not implement Issue11 termination
or slot release.
