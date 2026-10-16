# Issue 13: image-only repair of the existing runner

The earlier recommendation of `anxious-s13-runner-rehearsal@sha256:bbec1f6b5933918d79ffa966fad3c0727071ca77acddb198af937887780a7ad1` was incorrect for real native execution: it has no Codex binary. PM reported supply installed the access ledger but native startup failed. The selected replacement is:

```
docker.io/library/anxious-s13-chatbot-runner-039cc88@sha256:fe3900c07b6f712ebe9e27224298bac7e2f041c9e07f206fa5a09be508f1acd1
```

PM executes after review, preserving the existing owner/state/generation1, PVCs, transport Secret, supply ledger and registry/outbox. This is a one-time operator repair, not automatic lifecycle re-entry. Kubernetes permits updating a Pod's container image ([Pod update rules](https://kubernetes.io/docs/concepts/workloads/pods/#pod-update-and-replacement)). The Pod UID remains; kubelet replaces its container. No runner Pod DELETE, logout, fresh Pod POST, generation increment, receipt reset or DB UPDATE is part of this procedure.

## Why deletion/recreation is not the repair

`Runtime.stop()` commits `runner_failed` and retains pod_uid, reservation and evidence before UID deletion. `ensure()` only acts when pod_uid is NULL and phase is provisioning/capacity_wait. A settled ensure receipt forbids another POST within that generation. Deleting the Pod or clearing only the image cannot grant another create. Do not manually reset those columns/receipts; if the Pod is already gone, terminating or the state is failed, stop and request a separate reconciliation procedure.

`KubePods._pod()` compares the complete normalized spec against the loaded plan, including image. Old plan/new Pod image or new plan/old image refuses dispatch and ordinary stop validation. Moreover it checks spec, not the currently executing imageID: keep API replicas at zero until the new container is verified. Do not log out during this change.

## 1. Quiesce and collect private inputs

The runner uses `restartPolicy: Never`. Kubelet restarts a **currently running**
container when its image changes, overriding that policy for this transition
([Kubernetes v1.36 implementation](https://github.com/kubernetes/kubernetes/blob/v1.36.0/pkg/kubelet/kuberuntime/kuberuntime_manager.go#L1205-L1264)).
An already exited/terminal container does not qualify. The candidate tool checks
Running status/container state; live process evidence and resourceVersion fencing
remain necessary. If startup fails and no running container remains, this procedure
cannot promise an in-place rollback restart: leave API zero and report reconciliation
required, without deleting the Pod or resetting the reservation.

- Verify target node/context, namespace UID, expected Pod UID and generation, native/control PVC+PV references and transport Secret UID from the existing runtime binding. Record old imageID, containerID, restartCount and Pod resourceVersion. Confirm the supplied image's containerd digest alias resolves to the reviewed import, its architecture matches, and `/usr/local/bin/codex --version` succeeds inside the image in network-disabled Docker. No credential mount is needed for this image check.
- Stop user login, prepare, turn, supply and logout attempts. Record API Deployment replicas (expected1), image/template and HPA status. Scale `m2-hosting/deployment/api` to zero and wait until **all app=hosting-api Pods are gone**, including terminating Pods. An external controller must not recreate them during maintenance. This briefly stops the hosting API; leave web/postgres and codex-trial untouched.
- With the API stopped, read the registry/outbox/admissions via the reviewed read-only DB audit path. Require same owner/state/gen1 and persisted UID matching the present nonterminating Pod, phase ready/runner_unreachable, active_requests=0, no incomplete admissions, and the original settled ensure receipt. An ambiguous=true flag from initial Pod observation alone is not native activity evidence: independently verify no native process, no active runner request, and the reported failed pre-execution supply. If any actual execution/activity is uncertain, stop; do not clear ambiguity to proceed.
- Preserve root-private snapshots/backups of the runtime binding, API Deployment, binding Secret `m2-hosting/hosting-user-runner-v1`, runner Pod, registry/outbox/admissions and retained storage. Do not reset the control SQLite ledger or copy credential-bearing data to messages/issues.

Use root shell `umask 077`. Define `source` as the reviewed checkout, `work` as a fresh root-owned0700 directory, `runtime_file` as the existing private prepared runtime.json and `runner_pod` as the verified name. Export only nonsecret path/image variables. Snapshot commands (no secret stdout):

```bash
k() { /usr/local/bin/k3s kubectl "$@"; }
cp "$runtime_file" "$work/runtime.json"
k -n codex-runner get pod "$runner_pod" -o json > "$work/pod.json"
k -n m2-hosting get secret hosting-user-runner-v1 -o json > "$work/binding-secret.json"
chmod 0600 "$work/"*.json
```

Save `inputs.json` privately as provenance; changing it does not regenerate runtime.json. Do not rerun `user_runner_prepare` on this state.

## 2. Generate an offline candidate

Use the installed API image as a dependency environment, mounting this reviewed backend source read-only. No new API image/build is required because this is an offline tool only. Do not mount a Docker socket, live database, kubeconfig or credentials other than the private binding snapshot.

```bash
new_image='docker.io/library/anxious-s13-chatbot-runner-039cc88@sha256:fe3900c07b6f712ebe9e27224298bac7e2f041c9e07f206fa5a09be508f1acd1'
docker run --rm --user 0:0 --network none --read-only \
  -v "$source/backend:/app:ro" -v "$work:/review:rw" -w /app \
  --entrypoint python anxious-s13-chatbot-api-039cc88:build -B -m ops.user_runner_rebind \
  --runtime /review/runtime.json --pod /review/pod.json \
  --binding-secret /review/binding-secret.json --image "$new_image" --output /review/candidate
```

The tool writes new0600 files in a new0700 directory, refuses overwrite, validates digest/identity and Secret binding consistency, and changes only `plan.image`. UID/resourceVersion/full spec-or-data JSON tests fence both patches. Patches contain private Secret data; never print them. The tool is not proof of quiescence, resource provenance, storage safety, executing image or database state. Existing API full `_pod()` checks remain in place.

## 3. Apply while the API is still zero

```bash
k -n m2-hosting patch secret hosting-user-runner-v1 --type=json \
  --patch-file "$work/candidate/binding-secret-patch.json" -o name
k -n codex-runner patch pod "$runner_pod" --type=json \
  --patch-file "$work/candidate/pod-image-patch.json" -o name
```

Do not use `kubectl set image --record` or add Pod annotations; `_pod()` refuses unknown annotations. The JSON patch tests may fail if kubelet status changed the Pod resourceVersion. On failure, keep API zero, read fresh snapshots and investigate the change; never remove tests or blindly replay. If Secret already changed, retain its known candidate and generate a fresh Pod patch against the original runtime with the **original matching Secret snapshot** and fresh verified Pod snapshot into a different output directory; apply only the Pod patch. Do not roll back the Secret silently as part of a retry.

Wait for kubelet to replace the old container. Require the same Pod UID/name/labels/spec except image; changed containerID, terminated old container/process, new Running/Ready container status, matching immutable imageID (resolve the imported manifest/config digest relationship), and successful `codex --version` inside the running runner. `kubectl wait Ready` alone can pass before replacement and is insufficient. Preserve native/control volumes and transport token. Check the new runner reports not_configured before resupply, with no native process/active request. If image pull/startup fails, retain API zero and use rollback.

Confirm Secret `data.runtime.json` matches the candidate and its transport-token bytes/UID are unchanged. Retain the old local file; install the candidate as the operator's active runtime.json with its original UID/GID and0600 mode. Update the private inputs.json image for future provenance without invoking preparation. Preserve the old values for rollback.

Restore API Deployment to its saved replica count (expected1). The newly created API Pod's init container copies the updated Secret to its private emptyDir; the existing init is not rerun by Secret mutation alone. Wait for rollout/ready, verify init completion and privately compare loaded runtime.json to the candidate. Verify the namespace/Pod UID and registry state again. No registry phase, pod_uid or settled receipt is changed by this repair. Image alignment restores `_pod` validation; readiness/health polling does not establish real provider availability.

## 4. Resupply and E2E

The first failed supply already committed the ledger. Keep it. `ServiceAccessAuth.install()` permits the same latest snapshotId with the identical token fingerprint when not revoked/model/policy blocked; do not delete its snapshot/rejected history. The container restart loses only the in-memory access token, so an explicit operator resupply is necessary. Use the existing service supply procedure and current approved credential. Confirm the selected snapshot is unexpired and belongs to the same owner/state/provider account. If the credential changed, use the normal newly selected snapshot path; do not reuse an ID for a different token or bypass a sticky block. No automatic refresh or provider login is added.

Require supply success, real runner availability, one actual Codex response, then logout and UID deletion as the final E2E check. Logout still retains the reservation: this repair does not implement subsequent fresh login recreation. Record outcomes and any phase changes caused by ordinary application operations separately from the rebind.

## Rollback

Keep or scale API back to zero and wait for its Pods to disappear. Stop supply/turn/logout activity. Do not delete the runner or reset DB/ledger. If the new image has executed real work, preserve that history and check quiescence before rollback; an image rollback does not undo provider requests or ledger mutations.

- If only the binding Secret changed, restore only `data.runtime.json` using a JSON patch testing the current Secret UID/resourceVersion/data, replacing with the saved original base64 value. Verify transport token unchanged. Do not use `kubectl replace` of a whole stale Secret snapshot.
- If both changed and the same Pod UID remains, take fresh Pod+Secret snapshots. Run `ops.user_runner_rebind` again using candidate runtime.json as `--runtime`, fresh snapshots, **old image** as `--image`, and a new output path. This generates reverse image/binding patches with fresh version guards. Apply the reverse Pod patch and Secret patch while API is zero; verify the old container/image and Secret alignment before restoring saved API replicas and original local runtime/inputs files.
- If UID/state changed, the Pod vanished, or state became failed/terminating, stop and report reconciliation required. Do not adapt this image-only procedure into a new generation/create permission.

The old image lacks Codex. Rollback restores the previous unavailable-native baseline, not a working E2E service. Preserve all failed/partial evidence and report the blocker. Minor startup-diagnostic improvement remains in the issue backlog and does not block this repair.
