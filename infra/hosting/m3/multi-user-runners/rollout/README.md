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
Observer cleanup/idle scanning still covers existing registry records; its allowlist
gates new entry after release, not cleanup. Stop its timer AND service to suspend
observation; disabling the API or narrowing the allowlist does not stop cleanup.

The example includes PM's 2026-10-16 imported image references. PM independently
verified Docker's containerd image store uses OCI **index digests** as local IDs,
and registered repo@index references in k3s/CRI. These are not config digests.
Worker uses the recorded provisioner local ID with `--pull=never`. API uses
`imagePullPolicy: Never`; runner retains its reviewed `IfNotPresent` behavior and
exact digest. Verify the imported references resolve on the one target node.
No new application image is needed: the reviewed `worker.py` wrapper is mounted
read-only into the original provisioner. Record BOTH image provenance and this
bundle's git SHA/hash in the operation record.

PM must replace providerAccountId and verify sourceUid, node/boot/namespace IDs,
CIDR, API TLS names and canonical root-owned executable paths in the example.
The provider account is an identifier, never an access/refresh token. Inventory
PID/cgroup references independently on the host; do not discover them from the
observer container. After reboot, refresh the observer boot/namespace input before
resuming its timer; stale proof deliberately refuses. The example is not live input.

Export Deployment and its envFrom ConfigMaps privately (no Secret export). Inputs
can contain existing literal environment values, so do not publish the snapshots.
`configmaps.json` accepts a single ConfigMap or Kubernetes List. Export the selected
object with `kubectl get configmap NAME -n m2-hosting -o json`. Limit the snapshot to reviewed
non-secret ConfigMaps. The renderer never prints their contents.

```sh
# Run from the backend worktree. /private-rollout is a root-only staging directory
# holding values.json, api-deployment.json and configmaps.json. No credentials here.
# Worker image is the PM-provided local ID; this run has no network/host privileges.
docker run --rm --pull=never --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --user 0:0 \
  -v "$PWD/backend:/source:ro" \
  -v "$PWD/infra/hosting/m3/multi-user-runners/rollout:/templates:ro" \
  -v /private-rollout:/inputs:ro -e PYTHONPATH=/source \
  --entrypoint python \
  sha256:65113675ba9e1d067a2213127157bcabece62e47254c8d9987abcff4c088e108 \
  -B -m ops.runner_rollout --values /inputs/values.json \
  --deployment /inputs/api-deployment.json --configmaps /inputs/configmaps.json \
  --templates /templates --check
```

To render, repeat with a writable root-only staging parent mounted at `/output`,
and `--output /output/candidate` (must not exist). `--check` alone writes nothing;
`--check --output ...` both validates and renders. Files are 0600, scripts 0700.
Review `resources.json`, `api-candidate.json`, `api-patch.json`, inputs, scripts and
units. The renderer never runs kubectl, Docker, systemctl, migrations or deletion.

`api-patch.json` is **JSON Patch**, with exact Deployment UID/resourceVersion tests.
It replaces only the Pod spec. Replicas, selectors, rollout strategy and unrelated
app/auth config remain as captured. Re-export after scaling to zero/mutator stop;
do not remove a stale test to force application. `api-candidate.json` is for diff,
not apply: applying that snapshot could restore stale replicas/metadata.

```sh
kubectl diff -f /private-rollout/candidate/resources.json
kubectl diff -f /private-rollout/candidate/api-candidate.json
# Diff exit1 means differences; exit>1 is an error. Review before continuing.
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
| runner-pool-controller | Pod/PVC/Secret GET/CREATE; named runner-workload SA and runner-default-deny KNP GET | PV GET/CREATE; named Namespace GET |
| runner-pool-observer | Pod/PVC/Secret GET; Pod DELETE | PV GET; named Node and Namespace GET |
| runner-pool-supply | Pod/PVC/Secret GET; pods/exec CREATE | named Namespace GET |

API never receives create/delete/exec/PV privileges. New worker SAs have no
automount. Existing runner-web / runner-preparer Roles and bindings, and their
runner-web-namespace / codex-runner-preparer cluster counterparts, are replaced
with empty rules/subjects. Do this only with old mutators stopped. Existing old
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

Install generated controller/observer/supply input JSON as respectively
`/etc/anxious-lookout/issue16/{controller,observer,supply}/inputs.json`.
Each directory is root0700 and files root0600, with no symlink/hardlink, as required
by the existing `read_private` implementation. Supply these additional files
privately, never through values, command-line credential arguments or Git:

| Directory | Additional files |
| --- | --- |
| controller | database-url (controller DB login), kube-token (runner-pool-controller SA), ca.crt |
| observer | database-url (observer DB login), kube-token (runner-pool-observer SA), ca.crt |
| supply | database-url (observer DB login), kube-token (runner-pool-supply SA), ca.crt, kubeconfig |

The controller uses the same file for DATABASE_URL_FILE and
CONTROLLER_DATABASE_URL_FILE. Apply the existing #16 SQL grants before starting.
Never inject API/admin credentials into these files. Host tokens must be issued
for their own SA, have reviewed expiration, and be replaced atomically before
expiry; these host processes do not have kubelet projection or self-mint rights.
PM's existing credential procedure owns renewal. An expired token causes refusal.
Do not enable unattended timers without arranging renewal and alerting.

Supply kubeconfig must address the same hostApi/CA/SA as supply pool config, have
exactly one reviewed context, verified TLS, `certificate-authority: /private/ca.crt`,
and `tokenFile: /private/kube-token`; no exec/auth-provider plugins, impersonation,
insecure-skip-tls-verify or embedded broad admin credentials. PM validates it.
Only supply receives a read-only `/home/opc/.codex` bind. No role gets Docker socket.
Use canonical standalone root-owned kubectl/crictl binaries compatible with the
image; do not bind a symlink to k3s without its resolved target/runtime dependencies.

## Installation and shared mount (PM after review)

Use a new empty output root; **never mount the legacy provision output**. Preserve
partial output and source credentials. The following creates only the named roots;
PM checks expected ownership and any existing contents before installation:

```sh
install -d -o root -g root -m 0700 /usr/local/lib/anxious-lookout/issue16
install -d -o root -g root -m 0700 /etc/anxious-lookout/issue16/controller \
  /etc/anxious-lookout/issue16/observer /etc/anxious-lookout/issue16/supply
install -d -o root -g root -m 0700 /var/lib/anxious-lookout/user-runners \
  /var/lib/anxious-lookout/issue16-d4 \
  /var/lib/anxious-lookout/issue16-d4/provision-output \
  /var/lib/anxious-lookout/issue16-d4/observer \
  /var/lib/anxious-lookout/issue16-d4/supply \
  /var/lib/anxious-lookout/issue16-d4/supply/attempts
out=/private-rollout/candidate
lib=/usr/local/lib/anxious-lookout/issue16
for role in controller observer supply restore; do
  install -o root -g root -m 0700 "$out/$role.sh" "$out/stop-$role.sh" "$lib/"
  install -o root -g root -m 0644 "$out/issue16-$role.service" /etc/systemd/system/
done
for role in controller observer supply; do
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
Observer has host PID/cgroup namespaces, read-only host proc/CRI socket/crictl and
rslave storage, DAC_READ_SEARCH/SYS_PTRACE; socket read-only does not limit CRI RPCs.
Supply has only source/config/binary reads and its own writable cursor/attempt dir,
DAC_READ_SEARCH, host network, no host PID/cgroup/CRI/dev/storage mounts.

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
services for the canary. Controller, observer(--idle), supply timers are separate
oneshot batches every15seconds after the previous batch finishes, not inbox watchers.
`worker.py` converts per-owner reconciliation_required into exit1 so systemd can
alert even though the original CLI catches per-owner errors. Each unit has a
commented OnFailure hook location; PM supplies the existing alert unit. Timeout/stop
cleans up only the container ID recorded by that invocation, preserving host files.
No timers are started by installation/render. Enable timers only after the reviewed
canary checks; no restore validation/rollback rehearsal or two-owner full E2E is
added as a PR gate. A real mount/host/native/provider check remains an execution
result to report, not implied by the offline tests.

Use `systemctl stop issue16-controller.timer issue16-observer.timer issue16-supply.timer`
and stop the corresponding services before changing inputs/allowlist or D4 actions.
Changing API env alone does not stop workers. On expansion, re-render API and all
worker scripts from one values file and review/diff again; retain source/size settings
for existing storage. systemd service env does not carry credential bytes.
