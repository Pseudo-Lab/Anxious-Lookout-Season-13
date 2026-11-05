#!/bin/sh
# #68 dev-VM V1 (guest part): import the three host-built images into k3s containerd and print the evidence chain
# source SHA -> host image ID -> archive SHA-256 -> import reference/digest -> CRI image ID (must equal the host ID).
# The Pod imageID link is recorded when the first #68 Pod starts (V3/V4). Run as root in the guest after review
# cleared this SHA-256. Development VM only. No Pod, Deployment, binding, DB or node file change; nothing is
# loaded into guest Docker. Output also goes to $EVIDENCE/v1-<UTC>.txt (root 0600, never overwritten).
# Safe to run again: an image already imported with the expected ID is skipped.
# rc: 0 imported and verified; 3 precheck refused, nothing imported; 5 an import failed (earlier imports stay,
#     which changes no workload); 8 imported but the chain check failed.
set -u
umask 077
here=$(dirname "$0")
. "$here/lib.sh"
trap 'exit 130' INT TERM
SOURCE=0a7a6150527e68d8a9826b9ee8304c438c12e7da
# reference|archive|host image ID (docker image inspect on the build host; v1-host-build.sh images.txt).
EXPECTED='docker.io/library/anxious-dev-runtime:68-0a7a615|anxious-dev-runtime-68-0a7a615.tar|sha256:7cadf4cf5fb1cbf861d6ce1c6531085d5ed4dfd1e859d60bffda95cc27d2a527
docker.io/library/anxious-dev-runner:68-0a7a615|anxious-dev-runner-68-0a7a615.tar|sha256:966d3846910362fbe574a7a1e531d90ed6cb33f5803b429d185fd89b80315292
docker.io/library/anxious-dev-provisioner:68-0a7a615|anxious-dev-provisioner-68-0a7a615.tar|sha256:565a299342a2550a53df4aedf291b2a1ae3f90cbb9330a2c7f36c4360d4674c3'

main() {
  echo "v1: $(date -u +%Y-%m-%dT%H:%M:%SZ) import, source $SOURCE"
  [ -d "$IMAGES" ] && [ ! -L "$IMAGES" ] || { echo "precheck: $IMAGES missing; nothing imported"; return 3; }
  sums=$(cd "$IMAGES" && sha256sum -c "$here/v1-archives.sha256" 2>&1) \
    || { echo "precheck: archive checksum: $(echo "$sums" | grep -v ': OK$' | head -3); nothing imported"; return 3; }
  echo "$sums"
  sed 's/^/archive sha256 /' "$here/v1-archives.sha256"
  timeout 30 "$K3S" ctr -n k8s.io images ls -q >/dev/null 2>&1 || { echo 'precheck: k3s ctr images ls failed; nothing imported'; return 3; }

  echo "$EXPECTED" | while IFS='|' read -r ref file id; do
    if [ "$(image_id "$ref")" = "$id" ]; then echo "skip $ref (already imported, id ok)"; continue; fi
    timeout 600 "$K3S" ctr -n k8s.io images import "$IMAGES/$file" >/dev/null 2>&1 || { echo "import of $file failed"; exit 5; }
    echo "imported $file"
  done || { echo 'stopped at the failed import'; return 5; }

  listing=$(timeout 30 "$K3S" ctr -n k8s.io images ls 2>/dev/null) || { echo 'ctr images ls failed after import'; return 8; }
  bad=0
  for line in $EXPECTED; do
    ref=$(echo "$line" | cut -d'|' -f1); file=$(echo "$line" | cut -d'|' -f2); id=$(echo "$line" | cut -d'|' -f3)
    row=$(echo "$listing" | awk -v r="$ref" '$1 == r {print $3}')
    got=$(image_id "$ref")
    echo "chain source $SOURCE -> host id $id -> archive $file $(grep " $file\$" "$here/v1-archives.sha256" | cut -d' ' -f1) -> import $ref ${row:-missing} -> cri id ${got:-missing}"
    [ -n "$row" ] && [ "$got" = "$id" ] || { echo "MISMATCH $ref"; bad=1; }
  done
  [ "$bad" = 0 ] || { echo 'postcheck failed: chain'; return 8; }
  return 0
}

[ "$(id -u)" = 0 ] || { echo 'precheck: not root; nothing imported'; echo 'result rc=3'; exit 3; }
install -d -o root -g root -m 0700 "$EVIDENCE" || { echo 'precheck: evidence directory; nothing imported'; echo 'result rc=3'; exit 3; }
log="$EVIDENCE/v1-$(date -u +%Y%m%dT%H%M%SZ).txt"
[ ! -e "$log" ] || { echo "precheck: $log exists"; echo 'result rc=3'; exit 3; }
rcfile=$(mktemp)
{ main; echo $? > "$rcfile"; } 2>&1 | tee "$log"
rc=$(cat "$rcfile"); rm -f "$rcfile"
echo "result rc=$rc" | tee -a "$log"
echo "evidence $(sha256sum "$log" | cut -d' ' -f1) $log"
exit "$rc"
