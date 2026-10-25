# Issue10 runtime image, initializer and health package

Scope: exact-SHA runtime target, synthetic binding, transport-only HTTP. Provider
credentials, native execution, callback calls and extra Pods are excluded. This
package is for reviewer source/PM execution review; backend does not initialize
host PVs or import into the live containerd store. Keep quota0 and feature off.

## Fixed inputs and storage behavior

`ops.runner_rehearsal_init` accepts only --initialize or --verify, with fixed
container paths /state and /control, owner/state from REHEARSAL-INPUTS.md and
UID/GID10001. Both directories must be private0700, owned by10001, and empty
before initialization. It creates the native account-id/state-binding.json and
control/operator-account (0600). The control string is the explicit placeholder
`synthetic-provider-excluded-rehearsal`, **not a provider identity or credential**.
No access envelope or provider ledger is initialized. The service may create its
own empty control DB/socket on startup, but no supply call is made.

Failure preserves partial files and cannot be retried with --initialize: never
clean, adopt, chown recursively, regenerate the state UUID or delete a live path.
--verify checks existing markers without reinitializing. The trusted PM mount
path/host boundary matters: markers are not proof of which Kubernetes PVC was
mounted. The future wrapper must also compare namespace/PVC/PV/Secret UIDs.

After reviewing the exact image, PM runs on Kubernetes node anxious-lookout
(OS hostname `simple`, local InternalIP `10.0.0.107`):

```sh
bash infra/hosting/m3/user-runner-isolated/initialize-rehearsal.sh initialize "$rehearsal_image_id"
bash infra/hosting/m3/user-runner-isolated/initialize-rehearsal.sh verify "$rehearsal_image_id"
```

An optional third argument selects the image platform: `arm64` (default, production) or `amd64`, used only on the x86_64 development VM (`infra/k3s/DEV-VM.md`). Any other value exits 2 before any host check. On the VM, pass the same `$rehearsal_arch` that was used for the build below:

```sh
bash infra/hosting/m3/user-runner-isolated/initialize-rehearsal.sh initialize "$rehearsal_image_id" amd64
```

Before any Docker operation, the script requires OS hostname `simple`, the local
IPv4 address `10.0.0.107`, and a read-only Node lookup using the explicit local
`/etc/rancher/k3s/k3s.yaml` kubeconfig through `sudo -n /usr/local/bin/k3s`.
Node name and hostname label must be `anxious-lookout`; Node addresses must include
InternalIP `10.0.0.107` and Hostname `simple`. Missing commands, failed lookup,
malformed evidence or mismatches stop execution. This is the PM-observed fixed
deployment mapping, not an assumption that Kubernetes names equal OS hostnames.
The caller needs `ip`, `jq`, and noninteractive sudo for this read-only lookup.

The script also checks fixed resolved host paths, UID/GID/mode, SELinux enforcing,
container_file_t and matchpathcon, and the local linux/arm64 runtime image user.
Run the wrapper as `opc` with noninteractive sudo available for `readlink`, `stat`
and `matchpathcon`: these read-only path checks require root traversal through
the deliberately root-owned 0700 ancestors. Do not relax ancestor permissions.
Docker commands remain in the caller context; the rootful daemon resolves bind
mount sources and the initializer process remains UID/GID10001. A failed sudo
or path check stops before `docker run`.

Host wrapper revisions that only change shell/docs/tests do not require rebuilding
the runtime image. Record wrapper SHA separately from the image source SHA and
retain the reviewed image's digest/release metadata and import/health evidence.
For the traversal fix, PM's image source remains `4b1b72e` and imported Plan.image
candidate remains
`docker.io/library/anxious-s13-runner-rehearsal@sha256:bbec1f6b5933918d79ffa966fad3c0727071ca77acddb198af937887780a7ad1`;
use the existing local image ID from that build for this Docker wrapper. Review
the changed wrapper before execution; this does not assert live initialization.
It mounts only those two dedicated leaves into a network-none, read-only-root,
cap-drop ALL, no-new-privileges container as10001. No initializer Pod is created.
SELinux MCS access is still an actual execution result: if denied, stop and retain
logs; do not add privileged/disable labels/relabel a parent or broaden permissions.
No host command in this document is executed by backend.

## Build and actual-image HTTP check

PM checks out the reviewed exact source SHA. Record it and require no tracked
source modifications; untracked private evidence must not be added to the image.
Use a private evidence directory0700, umask077 and the canonical repository below:

```sh
umask 077
rehearsal_sha=$(git rev-parse HEAD)
git diff --quiet
git diff --cached --quiet
rehearsal_repo=docker.io/library/anxious-s13-runner-rehearsal
rehearsal_tag="$rehearsal_repo:$rehearsal_sha"
rehearsal_arch=arm64  # amd64 only on the x86_64 development VM (infra/k3s/DEV-VM.md)
docker build --platform "linux/$rehearsal_arch" -f backend/Dockerfile --target runtime \
  --build-arg APP_GIT_SHA="$rehearsal_sha" \
  --build-arg APP_BUILT_AT="$(date -u +%FT%TZ)" -t "$rehearsal_tag" .
rehearsal_image_id=$(docker image inspect --format '{{.Id}}' "$rehearsal_tag")
docker image inspect --format '{{.Id}} {{.Os}}/{{.Architecture}} {{.Config.User}}' "$rehearsal_tag"
```

Require linux/$rehearsal_arch and10001:10001. runtime includes runner.app and uvicorn; the
Plan command replaces its default API CMD. No Codex binary is installed, and no
provider/supply/turn path is used. The exact image's code, not a source overlay,
is exercised by this HTTP check. Only the test driver is mounted read-only:

```sh
docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
  --user 10001:10001 --tmpfs /tmp:rw,nosuid,nodev \
  --tmpfs /state:rw,uid=10001,gid=10001,mode=0700 \
  --tmpfs /control:rw,uid=10001,gid=10001,mode=0700 \
  --tmpfs /transport:rw,uid=10001,gid=10001,mode=0700 \
  -v "$PWD/backend/config_tests/runner_rehearsal_http.py:/checks/health.py:ro" \
  -e PYTHONPATH=/app --entrypoint python "$rehearsal_image_id" -B /checks/health.py
```

The check uses fresh tmpfs synthetic state/token, executes the actual Plan uvicorn
command, and verifies HTTP200 with matching owner/state/synthetic podUid,
unverified/available=false/not_configured; wrong bearer401, wrong/missing state503,
no auth.json and clean shutdown. Docker network none leaves only loopback, so
health must work without DNS/callback/provider traffic. This does not establish
Cilium behavior or real Pod UID binding. No host PVC paths are mounted in this test.

## PM-only containerd import and digest provenance

After image checks and source review, PM exports that exact tag and imports into
the target node's **k8s.io** containerd namespace. No registry push is required.
Set rehearsal_evidence to a fresh, reviewed private0700 evidence directory first.
Never overwrite an existing archive or alias; stop on collisions.

```sh
test ! -e "$rehearsal_evidence/runner-image.tar"
docker save --platform "linux/$rehearsal_arch" --output "$rehearsal_evidence/runner-image.tar" "$rehearsal_tag"
sha256sum "$rehearsal_evidence/runner-image.tar"
tar -xOf "$rehearsal_evidence/runner-image.tar" manifest.json > "$rehearsal_evidence/docker-manifest.json"
rehearsal_familiar=${rehearsal_tag#docker.io/library/}
jq -e --arg tag "$rehearsal_tag" --arg familiar "$rehearsal_familiar" \
  'length == 1 and (.[0].RepoTags == [$tag] or .[0].RepoTags == [$familiar])' \
  "$rehearsal_evidence/docker-manifest.json"
rehearsal_config_path=$(jq -er '.[0].Config' "$rehearsal_evidence/docker-manifest.json")
[[ $rehearsal_config_path =~ ^blobs/sha256/[a-f0-9]{64}$ || $rehearsal_config_path =~ ^[a-f0-9]{64}\.json$ ]]
tar -xOf "$rehearsal_evidence/runner-image.tar" "$rehearsal_config_path" > "$rehearsal_evidence/docker-config.json"
rehearsal_config="sha256:$(sha256sum "$rehearsal_evidence/docker-config.json" | cut -d' ' -f1)"
sudo /usr/local/bin/k3s ctr --namespace k8s.io images import --platform "linux/$rehearsal_arch" \
  "$rehearsal_evidence/runner-image.tar"
sudo /usr/local/bin/k3s ctr --namespace k8s.io images ls > "$rehearsal_evidence/images-after.txt"
rehearsal_manifest=$(awk -v ref="$rehearsal_tag" '$1 == ref {print $3}' "$rehearsal_evidence/images-after.txt")
[[ $rehearsal_manifest =~ ^sha256:[a-f0-9]{64}$ ]]
sudo /usr/local/bin/k3s ctr --namespace k8s.io content get "$rehearsal_manifest" > "$rehearsal_evidence/target.json"
```

Docker image Id can be an index digest in the containerd image store, rather than
a config digest. Do not infer its type. Resolve the imported target's `$rehearsal_arch` child
if it is an index, preserving the original target digest for the Plan reference:

```sh
if jq -e '.manifests != null' "$rehearsal_evidence/target.json" >/dev/null; then
  rehearsal_child=$(jq -er --arg arch "$rehearsal_arch" '[.manifests[] | select(.platform.os == "linux" and .platform.architecture == $arch)] | if length == 1 then .[0].digest else error("ambiguous " + $arch + " manifest") end' "$rehearsal_evidence/target.json")
  [[ $rehearsal_child =~ ^sha256:[a-f0-9]{64}$ ]]
  sudo /usr/local/bin/k3s ctr --namespace k8s.io content get "$rehearsal_child" > "$rehearsal_evidence/manifest.json"
else
  cp "$rehearsal_evidence/target.json" "$rehearsal_evidence/manifest.json"
fi
jq -e --arg id "$rehearsal_config" \
  '(.schemaVersion == 2) and (.manifests == null) and (.config.digest == $id)' \
  "$rehearsal_evidence/manifest.json"
sudo /usr/local/bin/k3s ctr --namespace k8s.io content get "$rehearsal_config" > "$rehearsal_evidence/config.json"
cmp "$rehearsal_evidence/docker-config.json" "$rehearsal_evidence/config.json"
jq -e --arg arch "$rehearsal_arch" '.architecture == $arch and .os == "linux" and .config.User == "10001:10001"' \
  "$rehearsal_evidence/config.json"
rehearsal_plan_image="$rehearsal_repo@$rehearsal_manifest"
sudo /usr/local/bin/k3s ctr --namespace k8s.io images tag "$rehearsal_tag" "$rehearsal_plan_image"
sudo /usr/local/bin/k3s ctr --namespace k8s.io images ls > "$rehearsal_evidence/images-digest.txt"
awk -v ref="$rehearsal_plan_image" -v digest="$rehearsal_manifest" \
  '$1 == ref && $3 == digest {ok=1} END {exit !ok}' "$rehearsal_evidence/images-digest.txt"
sudo /usr/local/bin/k3s crictl inspecti "$rehearsal_plan_image" > "$rehearsal_evidence/cri-image.json"
```

Docker save may normalize docker.io/library tags to the familiar short form;
only these two spellings of the same exact source-SHA tag are accepted above.
The final Plan.image input is rehearsal_plan_image, **not Docker image Id**.
The config digest connects the local Docker image to the imported manifest; the
canonical digest alias and CRI inspect prove the node resolves the exact reference.
If an ambiguous/nested index, alias collision, missing CRI record or different config/platform is
observed, stop and review; do not force-tag, pull another image or substitute a tag.
Retain the source SHA/build log/local ID/archive hash/manifest+config/CRI evidence.
Import adds only this image to the runtime store; no Pod, policy or quota is created.

Sources: [K3s image import](https://docs.k3s.io/add-ons/import-images),
[K3s CLI tools](https://docs.k3s.io/cli). The bundled ctr import --platform option
was checked with the pinned k3s v1.36.4-k3s1 container's /bin/ctr; actual host
containerd import and CRI resolution remain PM execution evidence.
