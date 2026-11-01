#!/bin/sh
# #61 dev-VM gate W1: import the three host-built 5e73bc4 images into k3s containerd, and load the runtime
# image into the guest Docker too (W2 runs the renderer with it, as R4 did). Run once by the user as root in
# the guest after review cleared this SHA-256. No source transfer, no Pod/Deployment/DB change.
# Chain printed for #61: archive SHA-256 (checked against archives.sha256 before any import) → guest import
# reference and digest (ctr) → image ID (crictl inspecti .status.id), which must equal the host image ID.
# Safe to run again: an image already imported with the expected ID is skipped, and so is the docker load when
# guest Docker already holds the runtime image (its Id is the k3s manifest digest or the config ID).
# rc: 0 done and verified; 3 precheck refused, nothing imported; 5 an import failed (earlier ones stay
#     imported, which changes no workload); 8 imported but the chain check failed.
set -u
umask 077
here=$(dirname "$0")
. "$here/lib.sh"
trap 'exit 130' INT TERM
# Host image IDs (docker image inspect on the build host, 2026-11-01T13:38:04Z build of 5e73bc4).
EXPECTED="docker.io/library/anxious-dev-runtime:61-5e73bc4|anxious-dev-runtime-61-5e73bc4.tar|sha256:80af8c59f978f7a7d59d02f71985f3d456ce2e8011ed2d534395297188830771
docker.io/library/anxious-dev-runner:61-5e73bc4|anxious-dev-runner-61-5e73bc4.tar|sha256:4cac37e2173d5aec7b351e05c2977f2e0df76670f3befb91ce54306d2444e8ad
docker.io/library/anxious-dev-shell-executor:61-5e73bc4|anxious-dev-shell-executor-61-5e73bc4.tar|sha256:49b449cb31c5b344f876a9d9aee2d97dd3dcae906c3324bc0eaf00678dd6b883"
refuse() { echo "precheck: $1; nothing imported"; echo 'result rc=3'; exit 3; }
fail() { echo "$1"; echo "result rc=$2"; exit "$2"; }
image_id() { timeout 30 "$K3S" crictl inspecti -o go-template --template '{{.status.id}}' "$1" 2>/dev/null; }

[ "$(id -u)" = 0 ] || refuse 'not root'
[ -d "$ARCHIVES" ] && [ ! -L "$ARCHIVES" ] || refuse "$ARCHIVES missing"
sums=$(cd "$ARCHIVES" && sha256sum -c "$here/archives.sha256" 2>&1) || refuse "archive checksum: $(echo "$sums" | grep -v ': OK$' | head -3)"
echo "$sums"
ctr_has probe-only >/dev/null 2>&1; [ $? -le 1 ] || refuse 'k3s ctr images ls failed'

echo "$EXPECTED" | while IFS='|' read -r ref file id; do
  if ctr_has "$ref" && [ "$(image_id "$ref")" = "$id" ]; then echo "skip $ref (already imported, id ok)"; continue; fi
  timeout 600 "$K3S" ctr -n k8s.io images import "$ARCHIVES/$file" >/dev/null 2>&1 || { echo "import of $file failed"; exit 5; }
  echo "imported $file"
done || fail 'stopped at the failed import' 5
# Guest Docker identity of the runtime image: with the containerd image store (the guest, DEV-VM.md) the Docker
# Id is the manifest digest k3s imported; with the classic store it is the config ID (= host image ID). Either
# names exactly the verified image; nothing else is accepted.
RUNTIME_CONFIG=$(echo "$EXPECTED" | sed -n 1p | cut -d'|' -f3)
runtime_manifest() { timeout 30 "$K3S" ctr -n k8s.io images ls 2>/dev/null \
  | awk '$1 == "docker.io/library/anxious-dev-runtime:61-5e73bc4" {print $3}'; }
docker_ok() {  # $1 = docker Id
  m=$(runtime_manifest)
  [ -n "$1" ] && { [ "$1" = "$RUNTIME_CONFIG" ] || { [ -n "$m" ] && [ "$1" = "$m" ]; }; }
}
r=$(timeout 30 docker image inspect --format '{{.Id}}' anxious-dev-runtime:61-5e73bc4 2>/dev/null)
if ! docker_ok "$r"; then
  timeout 600 docker load -i "$ARCHIVES/anxious-dev-runtime-61-5e73bc4.tar" >/dev/null 2>&1 || fail 'docker load of the runtime image failed' 5
  echo 'docker loaded runtime'
fi

bad=
listing=$(timeout 30 "$K3S" ctr -n k8s.io images ls 2>/dev/null) || fail 'ctr images ls failed after import' 8
echo "$EXPECTED" | { while IFS='|' read -r ref file id; do
  row=$(echo "$listing" | awk -v r="$ref" '$1 == r {print $1" "$3}')
  got=$(image_id "$ref")
  echo "chain $file -> ${row:-missing} -> id ${got:-missing} (host $id)"
  [ -n "$row" ] && [ "$got" = "$id" ] || echo "MISMATCH $ref"
done; } > /run/issue61-w1.chain
cat /run/issue61-w1.chain
grep -q MISMATCH /run/issue61-w1.chain && bad=chain
d=$(timeout 30 docker image inspect --format '{{.Id}}' anxious-dev-runtime:61-5e73bc4 2>/dev/null)
echo "docker runtime id ${d:-missing} (accepted: k3s manifest $(runtime_manifest) or config $RUNTIME_CONFIG)"
docker_ok "$d" || bad="$bad docker"
rm -f /run/issue61-w1.chain
[ -z "$bad" ] || fail "postcheck failed:$bad" 8
echo 'result rc=0'
