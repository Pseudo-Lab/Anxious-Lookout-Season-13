# D4 pool rollout bundle (#18)

Backend authors this bundle; review approves its SHA; PM installs and applies it.
This is the missing concrete installation layer for [CUTOVER.md](../CUTOVER.md),
not permission to run D4 deletion or enable a service. Keep D4 backup, stopped
mutators, migration/grants and canary order. No old operator import is required.

## Inputs and rendering (offline, Docker only)

`values.schema.json` describes the values file; `ops.runner_rollout --check` also
checks canonical UUIDs, IPv4 CIDR, HTTPS endpoints with explicit port, image pins,
paths without shell syntax/traversal, selection and positive size limits. Unknown
keys, duplicates, empty allowlists and plaintext credential fields are refused.
`allowlist: ["<owner UUID>"]` selects canary owners; **null** explicitly expands to
all approved editors/admins. API and all workers receive the same selection.
Defaults in `values.example.json`: cap3, idle1800, native5GiB/control1GiB.
runner-controller (#61) stop/idle handling still covers existing registry records; the
allowlist gates new entry after release, not cleanup. Scale its Deployment to 0 to
suspend it; disabling the API or narrowing the allowlist does not stop cleanup.

The example includes PM's 2026-10-16 imported image references. PM independently
verified Docker's containerd image store uses OCI **index digests** as local IDs,
and registered repo@index references in k3s/CRI. These are not config digests.
Worker uses the recorded provisioner local ID with `--pull=never`. API uses
`imagePullPolicy: Never`; runner retains its reviewed `IfNotPresent` behavior and
exact digest. Verify the imported references resolve on the one target node.
For worker execution, the reviewed `worker.py` wrapper is mounted
read-only into the original provisioner. Record BOTH image provenance and this
bundle's git SHA/hash in the operation record.

Rendering is separate: use the **approved source SHA's runtime image digest**
(or a provisioner built from that same SHA). Never use an old provisioner merely
because current backend code is bound at `/source`: Python can still import
`/app/ops/runner_rollout.py`. Set RENDERER_IMAGE to the reviewed repo@sha256 digest
from release evidence, not a mutable tag or the historical example worker ID.
Record approved SHA, image digest, templates SHA and module hash together.
`postImagesStorageClass` is required; this cluster selects `m2-postgres-retain`.

PM must replace providerAccountId and verify node/boot/namespace IDs,
CIDR, API TLS names and canonical root-owned executable paths in the example.
The provider account is an identifier, never an access/refresh token.
Both API endpoints must use **IP literals**: `app.runner_kube.KubePods` rejects
DNS hostnames in real execution, including `localhost` and `kubernetes.default.svc`.
Use `api=https://10.43.0.1:443` for the in-cluster API and
`hostApi=https://127.0.0.1:6443` for host workers.
PM corrected its earlier truncated certificate report: the serving certificate has
IP SANs `127.0.0.1`, `10.43.0.1` and `10.0.0.107`. The
[PM canary report](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/16#issuecomment-6214030575)
records successful Pod creation and supply with these endpoint values, with user
response verification still pending at that checkpoint. Do not disable TLS verification.
The current offline renderer checks HTTPS syntax but does not enforce the runtime
IP-literal requirement; `--check` success alone does not establish endpoint usability.
runner-controller reads the named Node's UID and boot ID from the API at every proof
(#61), so a reboot needs no input refresh; host PID/cgroup/boot inputs are gone. The
example is not live input.

Export Deployment and its envFrom ConfigMaps privately (no Secret export). Inputs
can contain existing literal environment values, so do not publish the snapshots.
`configmaps.json` accepts a single ConfigMap or Kubernetes List. Export the selected
object with `kubectl get configmap NAME -n m2-hosting -o json`. Limit the snapshot to reviewed
non-secret ConfigMaps. The renderer never prints their contents.

```sh
# Run from the backend worktree. /private-rollout is a root-only staging directory
# holding values.json, api-deployment.json and configmaps.json. No credentials here.
# Set APPROVED_SHA and RENDERER_IMAGE from reviewed release evidence first.
# Hash the approved Git object, not an uncommitted working copy.
test -n "$APPROVED_SHA" && test -n "$RENDERER_IMAGE"
git show "$APPROVED_SHA:backend/ops/runner_rollout.py" > /private-rollout/approved-renderer.py
MODULE_SHA256=$(sha256sum /private-rollout/approved-renderer.py | cut -d ' ' -f 1)
# Templates must also come from that approved checkout. No source overlay.
docker run --rm --pull=never --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --user 0:0 \
  -v "$PWD/infra/hosting/m3/multi-user-runners/rollout:/templates:ro" \
  -v /private-rollout:/inputs:ro \
  --entrypoint python \
  "$RENDERER_IMAGE" \
  -B -m ops.runner_rollout --values /inputs/values.json \
  --deployment /inputs/api-deployment.json --configmaps /inputs/configmaps.json \
  --templates /templates --expected-module-sha256 "$MODULE_SHA256" --check
```

To render, repeat with a writable root-only staging parent mounted at `/output`,
and `--output /output/candidate` (must not exist). `--check` alone writes nothing;
`--check --output ...` both validates and renders. Files are 0600, scripts 0700.
Review `resources.json`, `api-candidate.json`, `api-patch.json`, inputs, scripts and
units. The renderer never runs kubectl, Docker, systemctl, migrations or deletion.
The module path/hash are printed before rendering and saved in
`renderer-provenance.json`. A hash mismatch fails before output creation; old
renderers reject the mandatory flag. Do not remove the flag to make them run.
Post-render preflight checks the PVC file, explicit class, claim, writable mount,
POST_IMAGE_ROOT and patch/candidate agreement, including files read back from disk.
Any nonzero renderer exit blocks apply; discard partial output and rerender into
a fresh directory. Keep the successful output private and unchanged until apply.

### Existing image PVC gate (PM, before any apply)

Use `kubectl get pvc hosting-post-images -n m2-hosting -o json` to distinguish
NotFound from authorization/connection errors (errors stop the procedure).
If absent, inspect the selected StorageClass and require reclaimPolicy=Retain,
approved provisioner/node placement/capacity, then `kubectl create -f
post-images-pvc.json`. Do not use apply here: a concurrent creation must fail.
WaitForFirstConsumer may stay Pending until the API schedules.

If present, require metadata.uid=`2eefbd17-f87d-4880-90a7-a6918f759536`
for the existing live installation, status.phase=Bound and a nonempty
spec.volumeName. Fetch that PV and require its claimRef UID/name/namespace match
this PVC and spec.persistentVolumeReclaimPolicy=Retain. **Skip PVC apply**.
This PVC still has immutable class `local-path`; PM patched its existing PV to
Retain on 2026-10-17. Never delete/recreate it or change its class to match the
new template. A different UID requires investigation and a reviewed baseline.
If reclaim is Delete, stop before deployment apply; PM must verify the exact PV
and, within authorized operations, patch its reclaim policy to Retain and reread
it. Do not change the cluster default StorageClass. Record PVC/PV UID and policy
evidence; Retain alone does not establish backup or recovery.

local-path is node-local and a 10Gi request is not a disk quota. Record actual
disk usage and matched DB/image backup recovery evidence. Keep the observed
0:10001 mode2777 parent away from untrusted other-UID writers and protect its
10001:10001 mode2700 private data child; do not loosen permissions for migration.

`api-patch.json` is **JSON Patch**, with exact Deployment UID/resourceVersion tests.
It replaces only the Pod spec. Replicas, selectors, rollout strategy and unrelated
app/auth config remain as captured. Re-export after scaling to zero/mutator stop;
do not remove a stale test to force application. `api-candidate.json` is for diff,
not apply: applying that snapshot could restore stale replicas/metadata.

**Runner Pod manifest gate.** API and workers accept a runner Pod only if it equals
the manifest their own code renders (`KubePods._pod`). A release that changes that
manifest (#59: resources, executor `/tmp`, transport token as an env reference)
refuses live Pods created by the previous release, so roll it out only while
`codex-runner` has no Pending or Running runner Pod:

```sh
kubectl -n codex-runner get pods -l anxious.io/managed=user-runner \
  --field-selector=status.phase!=Failed,status.phase!=Succeeded -o name   # must print nothing
```

If a runner is running, stop it through `DELETE /api/research/codex/runner` and let
runner-controller (before #61, the observer) finish first. Do not delete the Pod by hand.
A Failed or Succeeded runner Pod (a node reboot or a container exit, #58 A) does not
block a rollout. Since #61 runner-controller records it as a terminal receipt, fails its
running conversation with `codex_runner_lost`, deletes it and releases the owner.

**Existing binding images (#59).** A published binding pins `plan.image` and, for v2,
`plan.executor.image` in `runner_bindings.bindings.plan`. Rollout values change only new
owners' bindings, and `Bindings.publish` refuses a different plan for an existing owner.
The #59 manifest no longer provides `RUNNER_TOKEN_FILE`, so a runner image older than
#59 cannot start under it: move every existing binding to the reviewed images in the
same window, after the API and worker images run the new code and before the next
entry. Use `ops.runner_binding_image` (CUTOVER.md section 6) per owner in `idle_stopped`
with a completed termination proof, with entry stopped and runner-controller scaled to 0
(before #61: the host controller and observer stopped):

- inputs: `owner`, `state`, `generation`, `revision` (the current registry fence),
  `oldPlanSha256` (`plan_digest` of the current plan, computed in the new worker image),
  `newImage` (runner) and, for a v2 plan, `executorImage`. One report/apply changes both
  digests; the executor's `wallSeconds`/`outputBytes` stay. A v1 plan takes `newImage`
  alone, or the explicit `executor` opt-in instead.
- `report` first, review it, then `apply` with the same inputs and the report mounted
  read-only. Registry, generation, resources, receipts and outbox rows are unchanged; the
  next entry renders the Pod from the updated plan.
- The retained `provision-output/<owner>/runtime.json` is not read after publication
  (restore uses only its owner and state), so it stays as the original preparation record.

```sh
kubectl diff -f /private-rollout/candidate/resources.json
kubectl diff -f /private-rollout/candidate/api-candidate.json
# Diff exit1 means differences; exit>1 is an error. Review before continuing.
# Complete the existing image PVC gate above before these mutations.
kubectl apply -f /private-rollout/candidate/resources.json
kubectl -n m2-hosting patch deployment api --type=json \
  --patch-file /private-rollout/candidate/api-patch.json
```

These are PM commands after review and D4 migration/grants, not executed by render.

## API configuration and authorization

The API keeps service account `hosting`. All CODEX_PERSONAL_* keys,
CODEX_RUNNERS_FILE, singleton CONFIG_FILE/ACCOUNT_ID and the owner-input mount are
removed. envFrom ConfigMaps become explicit configMapKeyRefs for retained keys;
explicit env wins over inherited keys. This prevents the ConfigMap restoring the
removed singleton restriction. Existing auth/database Secret references remain.
Unknown secretRef envFrom, sidecars or unexpected baseline identities refuse.

Pool v2 config is a Kubernetes Secret `hosting-runner-pool-v2` with only seven
noncredential fields. A UID10001 init container copies the projected config into
a UID10001-owned mode0700 directory and mode0600 regular file in private memory
emptyDir. This satisfies `read_private`; direct Secret symlinks do not. The API's
separate `runner-kube` volume projects a rotating 3600-second SA token and CA.
No token value is rendered. Automatic general SA mounting remains disabled.

| Identity | Namespace rights | Cluster rights |
| --- | --- | --- |
| m2-hosting/hosting | Pod/PVC/Secret GET | named codex-runner Namespace GET |
| runner-control/runner-controller (#61, in-cluster) | Pod GET/LIST/WATCH/CREATE/DELETE; PVC/Secret GET | named codex-runner Namespace and named Node GET |
| runner-pool-controller (host storage preparation until #63) | Pod GET; PVC/Secret GET/CREATE; named runner-workload SA and runner-default-deny KNP GET | PV GET/CREATE; named Namespace GET |
| runner-pool-observer | retired (#61): rendered empty | retired |
| runner-pool-supply | retired (#60): Role, ClusterRole and bindings rendered empty | retired |

API never receives create/delete/exec/PV privileges. New worker SAs have no
automount. Existing runner-web / runner-preparer Roles and bindings, and their
runner-web-namespace / codex-runner-preparer cluster counterparts, are replaced
with empty rules/subjects. So are runner-pool-supply's (host supply retired by
codex-broker, #60); delete its ServiceAccount after the retirement steps below. Do this only with old mutators stopped. Existing old
SAs and Secrets are retained as evidence, with no grant from those bindings.
RBAC is additive: PM must audit any OTHER binding to these SAs, and check effective
`kubectl auth can-i --as=system:serviceaccount:...` rights before enabling services.
The existing namespace ResourceQuota `runner-bootstrap-no-pods` is set to cap3.

Existing network policy is preserved. PM's live CNP snapshot has endpointSelector
`{}` in codex-runner, host/API ingress8080, namespace-based cross-runner/trial deny,
and API-server/6443 egress deny; it does not depend on the old owner/state label.
Its egress `toEntities: all` is the existing #13 repair, not introduced here.
Confirm API-to-runner and host-to-Pod-IP routes, callback, DB and TLS API reachability
with current Cilium/host rules. This bundle does not establish those live results.

## Private host inputs

Install the generated controller input JSON as
`/etc/anxious-lookout/issue16/controller/inputs.json` (host storage preparation and
binding publication only since #61; the observer input is no longer rendered).
Each directory is root0700 and files root0600, with no symlink/hardlink, as required
by the existing `read_private` implementation. Supply these additional files
privately, never through values, command-line credential arguments or Git:

| Directory | Additional files |
| --- | --- |
| controller | database-url (controller DB login), kube-token (runner-pool-controller SA), ca.crt |

The in-cluster runner-controller needs no host file: its token is projected by kubelet
and its two DSNs come from the Secret `runner-controller-db` (section below).

The controller uses the same file for DATABASE_URL_FILE and
CONTROLLER_DATABASE_URL_FILE. Apply the existing #16 SQL grants before starting.
Never inject API/admin credentials into these files. Host tokens must be issued
for their own SA, have reviewed expiration, and be replaced atomically before
expiry; these host processes do not have kubelet projection or self-mint rights.
PM's existing credential procedure owns renewal. An expired token causes refusal.
Do not enable unattended timers without arranging renewal and alerting.

For this test rollout, PM requests **24h** tokens with its existing authorized
operator identity, not with a worker SA. Renew at the earlier of **12h** or half
the actual granted lifetime, and before any test run extending past expiry. This
is an operational recommendation, not an automated renewer installed by this
bundle. The installed kubectl's `create token --help` confirms the server may grant
a shorter or longer lifetime; record the returned `status.expirationTimestamp`,
not an assumed now+24h. Default audience is the Kubernetes API server; no extra
audience or broad long-lived SA Secret is needed.

Example root-shell issuance/renewal, with worker timers/services stopped between
batches and the reviewed operator kubeconfig/context selected. New temporary
files stay inside each private root0700 role directory. Token bytes never go to
terminal, argv, environment, Git or the operation report:

```sh
set -eu
umask 077
for role in controller; do
  dir=/etc/anxious-lookout/issue16/$role
  request=$(mktemp "$dir/.token-request.XXXXXX")
  candidate=$(mktemp "$dir/.kube-token.XXXXXX")
  kubectl -n codex-runner create token "runner-pool-$role" --duration=24h -o json > "$request"
  jq -er '.status.token | select(type == "string" and length > 0)' "$request" > "$candidate"
  jq -er '.status.expirationTimestamp' "$request" # only expiry is displayed
  chown root:root "$candidate"
  chmod 0600 "$candidate"
  mv -T "$candidate" "$dir/kube-token"
  rm -- "$request"
done
```

On failure stop here; protect/remove only these newly created temporary files
after inspection, never print their contents. Existing token remains intact until
atomic replacement. Each new finite worker invocation reads the new token.
Resume timers only with valid tokens and monitoring.

No role gets Docker socket or `/home/opc/.codex` (operator access comes from codex-broker, #60).
Since #61 no role needs CRI, crictl, host proc or host PID/cgroup namespaces: the
observer and its pinned crictl binding are retired (git history before #61 has the
previous crictl provenance notes).

## Installation and shared mount (PM after review)

Use a new empty output root; **never mount the legacy provision output**. Preserve
partial output and source credentials. The following creates only the named roots;
PM checks expected ownership and any existing contents before installation:

```sh
install -d -o root -g root -m 0700 /usr/local/lib/anxious-lookout/issue16
install -d -o root -g root -m 0700 /etc/anxious-lookout/issue16/controller
install -d -o root -g root -m 0700 /var/lib/anxious-lookout/user-runners \
  /var/lib/anxious-lookout/issue16-d4 \
  /var/lib/anxious-lookout/issue16-d4/provision-output
out=/private-rollout/candidate
lib=/usr/local/lib/anxious-lookout/issue16
for role in controller restore; do
  install -o root -g root -m 0700 "$out/$role.sh" "$out/stop-$role.sh" "$lib/"
  install -o root -g root -m 0644 "$out/issue16-$role.service" /etc/systemd/system/
done
for role in controller; do
  install -o root -g root -m 0644 "$out/issue16-$role.timer" /etc/systemd/system/
  install -o root -g root -m 0600 "$out/$role-inputs.json" \
    "/etc/anxious-lookout/issue16/$role/inputs.json"
done
install -o root -g root -m 0600 "$out/worker.py" "$lib/worker.py"
install -o root -g root -m 0700 "$out/storage-root.sh" "$lib/storage-root.sh"
install -o root -g root -m 0644 "$out/issue16-storage-root.service" /etc/systemd/system/
install -d -o root -g root -m 0755 /etc/systemd/system/k3s.service.d
install -o root -g root -m 0644 "$out/k3s-issue16.conf" /etc/systemd/system/k3s.service.d/issue16.conf
# Supply private credentials by the existing secret procedure, as described above.
systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/issue16-*.service /etc/systemd/system/issue16-*.timer
```

The dedicated storage-root service self-binds the exact canonical storage root
and marks it rshared; it never unmounts or cleans up. It runs after local filesystems
and before Docker/k3s at boot. Existing service startup does not restart Docker.
Check effective propagation before starting controller:

```sh
systemctl start issue16-storage-root.service
findmnt --mountpoint /var/lib/anxious-lookout/user-runners -o TARGET,SOURCE,PROPAGATION
# Read-only probe of Docker mount namespace, no allocation:
docker run --rm --pull=never --network none --read-only --cap-drop ALL \
  --mount type=bind,src=/var/lib/anxious-lookout/user-runners,dst=/var/lib/anxious-lookout/user-runners,bind-propagation=rshared \
  --entrypoint findmnt \
  sha256:65113675ba9e1d067a2213127157bcabece62e47254c8d9987abcff4c088e108 \
  --mountpoint /var/lib/anxious-lookout/user-runners -o TARGET,PROPAGATION
```

Controller/restore are trusted root mount helpers: SYS_ADMIN, CHOWN, DAC_OVERRIDE,
FOWNER, MKNOD; loop-control device and block-major7 access; host /dev bind; rshared
storage bind. They require a rootful Docker daemon and reviewed loop availability,
free space, seccomp/AppArmor and host propagation. AppArmor confinement is disabled
for these two helpers only. No host source/DB/network is mounted into restore.

Restore runs after local-fs/Docker and **before k3s**; the k3s drop-in Requires/After
makes restore failure prevent k3s startup. Do not add After=k3s/network-online to
restore (boot cycle). It reads durable `storage-restore.json` + completed runtime
from each new owner's provision output, written before binding/Pod creation, and
requires exact agreement with state directories in the storage root. Partial,
orphan, foreign, symlink or missing inventories block startup; no auto cleanup.
Existing ext4 images are mounted and verified, never formatted. D4 initially has
an empty inventory, which succeeds. A partial controller run needs operator
reconciliation before reboot; do not bypass failed restore to make k3s start.

Start restore once before any new owner can enter, then manually run finite worker
services for the canary. The controller timer runs a separate oneshot batch
(storage preparation and binding publication only) every15seconds after the previous
batch finishes; runner-controller creates the Pod on the NOTIFY that publication sends.
`worker.py` converts per-owner reconciliation_required into exit1 so systemd can
alert even though the original CLI catches per-owner errors. Each unit has a
commented OnFailure hook location; PM supplies the existing alert unit. Timeout/stop
cleans up only the container ID recorded by that invocation, preserving host files.
No timers are started by installation/render. Enable timers only after the reviewed
canary checks; no restore validation/rollback rehearsal or two-owner full E2E is
added as a PR gate. A real mount/host/native/provider check remains an execution
result to report, not implied by the offline tests.

Use `systemctl stop issue16-controller.timer`, stop the service and scale
`runner-control/runner-controller` to 0 before changing inputs/allowlist or D4 actions.
Changing API env alone does not stop workers. On expansion, re-render API and all
worker scripts from one values file and review/diff again; retain source/size settings
for existing storage. systemd service env does not carry credential bytes.

## In-cluster runner-controller (#61)

`runner-control/runner-controller` is one resident Deployment (replicas 1, Recreate).
It replaces the host observer and the host worker's Pod creation:

- **Creation:** owners with a published binding and no Pod go through the existing
  `Controller.ensure`, so capacity, single CREATE and lost-response adoption are unchanged.
- **Stop:** stop intent → drain (v1 receipt) → graceful DELETE with a UID precondition →
  GET 404 → Node `anxious-lookout` Ready with a heartbeat ≤ 600 s, its UID and bootID read
  from the API → `release` (proof `evidence: pod-deleted`). A 404 means kubelet finished
  killing the containers and tearing down the volumes, so no host CRI/proc inspection is needed.
  A Pod already gone before any receipt is recorded as a `podAbsent` terminal receipt and
  proved the same way. This relies on runner-controller being the only rendered identity with
  Pod DELETE and never force-deleting: never force-delete a runner Pod by hand.
- **Dead runner** (Pod phase Failed, or the `runner` container terminated): no drain.
  Running conversations become `failed`/`codex_runner_lost` (the existing frontend text),
  open admissions are completed, a v2 `terminal` receipt is recorded, then DELETE → 404 → release.
- **Idle:** the same native idle evidence as before, then an ordinary stop intent.
- **Wake-ups:** Postgres `NOTIFY runner_runtime` (sent by API code on entry/stop and by the
  host worker on publication), the Pod watch (errors back off 1 s → 60 s) and a 30 s rescan.
- **Logs:** one JSON line per owner per pass: `{"owner", "stage": "controller", "result"}`.

New owners' storage is still prepared by the host `issue16-controller` oneshot until #63.
It no longer creates Pods: it publishes the binding and sends NOTIFY.

Rendered objects (`resources.json`):
- Namespace `runner-control` (PSA restricted).
- SA `runner-controller`, Role (codex-runner) and ClusterRole (named Namespace and Node) with bindings.
- The Deployment: runtime image, `imagePullPolicy: Never`, UID/GID 10001, `fsGroup` 10001
  (only a Secret and a memory emptyDir are mounted), read-only root filesystem.
- `runner-pool-observer` Role/ClusterRole and bindings rendered empty.

The host controller role loses all Pod verbs except GET.

### Production window: #61 and #63 together

Apply #61 and #63 in **one maintenance window**. #61 alone works for existing bindings, but
#63 removes the host preparation that #61 still uses for new owners. #63 also moves existing
bindings to CSI storage. One window means one freeze, one runner-Pod drain and one rollback point.

Preconditions (all must hold before the first mutation):

1. No **Pending or Running runner Pod**:
   ```sh
   kubectl -n codex-runner get pods -l anxious.io/managed=user-runner \
     --field-selector=status.phase!=Failed,status.phase!=Succeeded -o name   # must print nothing
   ```
   Stop live runners through `DELETE /api/research/codex/runner` and let the **old** observer
   finish before stopping it. Never delete a runner Pod by hand. Failed/Succeeded Pods may
   remain: runner-controller handles them after the switch (dead-runner path above).
2. Entry frozen (API maintenance/feature off) and the old mutators stopped:
   - `issue16-controller.timer`/`.service`
   - `issue16-observer.timer`/`.service`
   - any external creator
3. No open stop for a Pod that still exists: every `runner_termination.stops` row with
   `completed_at IS NULL` must belong to a Pod that is already gone or Failed.
4. A DB backup and the rollback image digests recorded with the operation.
5. No owner in `runner_failed` (`SELECT owner_id FROM runner_runtime.registry WHERE
   phase='runner_failed'` returns nothing). runner-controller never selects this phase (see
   PROVISIONING.md "Existing operator import"). Such an owner needs a separately reviewed and
   approved recovery before the window; never reset it by SQL.
6. No owner waiting for a Pod (`phase IN ('provisioning','capacity_wait')` returns nothing),
   or runner-controller would create its Pod as soon as it starts in step 5.

Order:

1. **Grants:**
   - Install `backend/db/061_runner_controller_grants.sql` through the reviewed administrator
     channel (after `016_runner_observer_grants.sql`).
   - It adds only `anxious_runner_observer` column UPDATEs (conversation state/error,
     admission `completed_at`); the API role gains nothing.
   - The script is idempotent `GRANT` statements; re-running it is harmless.
2. **DB Secret.** Write two DSN files in a fresh root0700 directory, each mode 0600:
   - `controller-url`: the existing controller login.
   - `observer-url`: the existing observer login.

   Address the database as `postgres.m2-hosting.svc.cluster.local:5432` (cluster DNS works
   in-cluster; host files use the Pod IP only because the host cannot resolve it).
   Create the Namespace, then the Secret from files. Never put DSNs on argv or in Git:
   ```sh
   jq '.items[] | select(.kind == "Namespace" and .metadata.name == "runner-control")' \
     /private-rollout/candidate/resources.json | kubectl apply -f -
   kubectl -n runner-control create secret generic runner-controller-db \
     --from-file=controller-url=/root/runner-db/controller-url \
     --from-file=observer-url=/root/runner-db/observer-url
   ```
3. **Runtime image.** The Deployment uses the API runtime image with `Never`. Verify that
   exact digest resolves in k3s containerd on the node (`k3s ctr -n k8s.io images ls`).
4. **Runner network policy:**
   - Apply the reviewed `user-runner-e2e/40-runner-network.json` (`runner-callback-draft`).
   - Runner 8080 ingress now comes from `runner-control`/`app=runner-controller` instead of
     `host`; no runner Pod probes use the host path.
   - Run `kubectl diff` first: the only intended change is that ingress rule (plus the #60
     broker egress if #60 is not yet applied).
5. **Apply `resources.json`** (Deployment, RBAC, emptied observer grants), then:
   ```sh
   kubectl -n runner-control rollout status deployment/runner-controller --timeout=120s
   kubectl -n runner-control logs deployment/runner-controller --tail=20   # JSON lines only, no DSN
   kubectl auth can-i create pods -n codex-runner --as=system:serviceaccount:runner-control:runner-controller  # yes
   kubectl auth can-i create pods -n codex-runner --as=system:serviceaccount:codex-runner:runner-pool-controller  # no
   ```
6. **Let runner-controller finish the dead runners, then stop it.**
   - Wait until no open stop and no runner Pod remain:
     ```sh
     kubectl -n codex-runner get pods -l anxious.io/managed=user-runner -o name   # nothing, Failed included
     # and: SELECT count(*) FROM runner_termination.stops WHERE completed_at IS NULL;  -- 0
     ```
     A Failed Pod that does not clear shows `reconciliation_required` in the controller log;
     investigate it, never delete it by hand.
   - Stop the controller and wait for its Pod to be gone. The API entry freeze does not stop
     the controller's own work:
     ```sh
     kubectl -n runner-control scale deployment/runner-controller --replicas=0
     kubectl -n runner-control wait --for=delete pod -l app=runner-controller --timeout=120s
     ```
   - Recheck preconditions 1, 5 and 6. From here until step 8 every mutator is stopped: the
     host controller and observer timers, runner-controller at 0, entry frozen.
   - Disable the host observer for good, but keep it restorable until the window closes:
     `systemctl disable --now issue16-observer.timer issue16-observer.service`. Keep its units,
     scripts, `/etc/anxious-lookout/issue16/observer` (token, DSN, inputs), the cursor directory
     and the `runner-pool-observer` SA. They are removed in step 9.
7. **#63 steps in the same window** (storage; documented with #63), with all mutators stopped:
   - CSI StorageClass.
   - Existing binding transition: per owner, in `idle_stopped` with a completed termination
     proof, one **compare-and-swap** of `runner_bindings.bindings.plan`. Inputs are the
     registry fence (owner/state/generation/revision) plus the current `plan_digest`. Run
     `report` first, then `apply` with the identical report.
   - It works like `ops.runner_binding_image` (CUTOVER.md section 6). A mismatched fence or
     digest refuses, and the registry, generation, receipts and outbox stay unchanged.
   - #61 itself does not change the runner Pod manifest (`KubePods._pod`), so a #61-only
     deployment leaves every binding as it is.
8. **Canary:**
   - Start the controller again:
     `kubectl -n runner-control scale deployment/runner-controller --replicas=1`, then wait for
     `rollout status`.
   - Unfreeze entry for the allowlisted canary owner.
   - Check it in this order:
     - cold start: NOTIFY → Pod
     - turn
     - stop: drain → DELETE → 404 → release
     - re-entry
   - Check the stop's `proof` (`evidence: pod-deleted` with the current Node UID/bootID) in
     `runner_termination.stops`. Then widen the allowlist through re-render as before.
9. **Close the window** (only after the canary passed): remove the host observer units and
   `observer.sh`/`stop-observer.sh`, then `/etc/anxious-lookout/issue16/observer`. That
   directory holds the observer kube token and DSN. Delete the `runner-pool-observer` SA,
   which invalidates its tokens. Keep `/var/lib/anxious-lookout/issue16-d4/observer` (cursor)
   as evidence. After this step a rollback must first re-create the observer identity (below).

### Rollback

- **Stop the new path first:**
  - Scale `runner-control/runner-controller` to 0 and wait for its Pod to be gone.
  - Keep entry frozen.
  - Require no Pending/Running runner Pod.
- **Receipts decide what the old release can finish.** It reads only open stops
  (`completed_at IS NULL`) and accepts only v1 drain receipts:
  - Completed stops are history, whatever their receipt version, and are never read again.
  - An open stop with a v2 `terminal` receipt would stay STOPPING under the old release, as
    such an owner did before #61. Nothing is corrupted.
  - Before rolling back, finish those owners with runner-controller. Otherwise record them as
    left for the next roll-forward. The check:
    ```sql
    SELECT owner_id FROM runner_termination.stops
     WHERE completed_at IS NULL AND receipt->>'version' = '2';   -- finish or record these
    ```
- **Restore the old objects:**
  - Re-apply the previous bundle's `resources.json` (it renders the `runner-pool-observer` SA,
    Role and ClusterRole with grants again).
  - Re-apply the previous `40-runner-network.json` (host ingress).
  - The 061 grants may stay: they are column UPDATEs used only by the observer login.
- **Observer identity:**
  - Before step 9, the retained units, inputs and token still work. Check that the token has
    not expired.
  - After step 9, or if the token expired, the SA is new. Issue a new token with the procedure
    in "Private host inputs" (`for role in observer`; the SA name is `runner-pool-observer`).
    Replace `/etc/anxious-lookout/issue16/observer/kube-token` atomically as root0600 and
    restore the observer DSN file and inputs from the operation's private backup.
  - Check `kubectl auth can-i delete pods -n codex-runner
    --as=system:serviceaccount:codex-runner:runner-pool-observer` returns yes.
  - Reinstall the previous observer units and scripts from the previous bundle. The retained
    DSN and inputs alone do not make a deleted SA's token valid again.
- **Resume:** start the old controller and observer timers, then lift the freeze on the old
  release.
- **#63 has its own rollback for the storage part.** If either half fails inside the window,
  roll back both and lift the freeze on the old release.
- Rollback needs the same precondition: no Pending/Running runner Pod.

## Host supply retirement (#60)

Runners now pull operator access from `codex-broker` (`infra/hosting/m3/codex-broker/README.md`).
The rendered bundle has no supply unit, input or RBAC grant. On a node installed before
#60, after the broker is logged in and a canary runner reports `available`:

```sh
systemctl disable --now issue16-supply.timer issue16-supply.service
rm -f /etc/systemd/system/issue16-supply.timer /etc/systemd/system/issue16-supply.service \
  /usr/local/lib/anxious-lookout/issue16/supply.sh /usr/local/lib/anxious-lookout/issue16/stop-supply.sh
systemctl daemon-reload
kubectl apply -f /private-rollout/candidate/resources.json      # empties runner-pool-supply Role/ClusterRole
kubectl -n codex-runner delete serviceaccount runner-pool-supply
```

- Keep `/var/lib/anxious-lookout/issue16-d4/supply/attempts` as evidence of past deliveries.
  Remove `/etc/anxious-lookout/issue16/supply` (database URL, kube token, kubeconfig) once
  the canary succeeded; revoke that database login with the other #16 grants.
- The host's `/home/opc/.codex/auth.json` is no longer read. Logging it out (`codex logout`
  as `opc`) revokes that host session; whether to keep it for the operator's own use is the
  operator's choice. It must never be copied into the broker volume (single writer).
