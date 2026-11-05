#!/bin/sh
# #68 dev-VM V1 (guest part): import the three host-built images into k3s containerd and record the evidence chain
# source SHA -> host image ID -> archive SHA-256 -> import reference/digest -> CRI image ID (must equal the host ID).
# The Pod imageID link is recorded when the first #68 Pod starts (V3/V4). Run as root in the guest after review
# cleared this SHA-256. Development VM only. No Pod, Deployment, binding, DB or node file change; nothing is
# loaded into guest Docker.
#
# Evidence: $EVIDENCE/v1-<UTC>.txt is created (never overwritten) before anything is imported, and every line is
# written to it and to stdout. A failed write before the first import refuses (rc 3). A failed write after it is
# rc 9: images may be imported but the evidence is incomplete, so nothing may build on this run.
# A reference already present with the expected ID is skipped and recorded as "skip"; with another ID, the run
# refuses before any import (rc 3), never retags.
# Interrupted (INT/TERM): rc 130 and a note. An import may have been partial: check `k3s ctr -n k8s.io images ls`
# and `crictl inspecti` for the three references and report before any later stage. Nothing is removed automatically.
# rc: 0 imported and verified; 3 refused, nothing imported; 5 an import failed (earlier imports stay, which
#     changes no workload); 8 imported but the chain check failed; 9 evidence incomplete; 130 interrupted.
set -u
umask 077
here=$(dirname "$0")
. "$here/lib.sh"
SOURCE=755f65cff7184800ae7de5c30d309bf1fa3f8b0a
# reference|archive|host image ID (docker image inspect on the build host; v1-host-build.sh images.txt).
EXPECTED='docker.io/library/anxious-dev-runtime:68-755f65c|anxious-dev-runtime-68-755f65c.tar|sha256:f109458721e92ee6ffe98ec59f304a1d1b44bcfe59af35201c8d984155c466ea
docker.io/library/anxious-dev-runner:68-755f65c|anxious-dev-runner-68-755f65c.tar|sha256:50de1819a6c4dd68113dbe42e91595e5bcc7f658dc69d92ba34a6018f2071e1e
docker.io/library/anxious-dev-provisioner:68-755f65c|anxious-dev-provisioner-68-755f65c.tar|sha256:a46d31493a2ab59fddbaae2a84f69247868a38b9cbb57da644644fa31109408c'
MANIFEST="$here/v1-archives.sha256"

log=; imported=0; logfail=0
say() {  # stdout and the evidence file; a failed evidence write is remembered, never ignored
  printf '%s\n' "$1"
  if [ -n "$log" ]; then printf '%s\n' "$1" >> "$log" || logfail=1; fi
}
incomplete() {  # evidence failed: rc 9 once anything was imported, otherwise rc 3 (nothing imported)
  printf '%s\n' "evidence incomplete: $1"
  if [ "$imported" = 1 ]; then rc=9; else rc=3; fi
}
finish() {  # $1 rc: append the result, then print the evidence hash; a failure of either is never rc 0
  rc=$1
  say "result rc=$rc"
  [ "$logfail" = 0 ] || incomplete 'a write to the evidence file failed; images may be imported'
  if [ -n "$log" ]; then
    h=$(sha256sum "$log" 2>/dev/null) && h=${h%% *} && [ ${#h} = 64 ] && printf 'evidence %s %s\n' "$h" "$log" \
      || incomplete 'the evidence hash could not be computed'
  fi
  exit "$rc"
}
refuse() { say "precheck: $1; nothing imported"; finish 3; }
trap 'say "interrupted: an import may be partial; check ctr images ls and crictl inspecti for the three references and report before any later stage"; exit 130' INT TERM

[ "$(id -u)" = 0 ] || refuse 'not root'
install -d -o root -g root -m 0700 "$EVIDENCE" || refuse 'evidence directory'
stamp=$(date -u +%Y%m%dT%H%M%SZ) || refuse 'clock'
candidate="$EVIDENCE/v1-$stamp.txt"
( set -C; : > "$candidate" ) 2>/dev/null || refuse "cannot create $candidate (exists or not writable)"
log=$candidate
say "v1: $(date -u +%Y-%m-%dT%H:%M:%SZ) import, source $SOURCE"
[ "$logfail" = 0 ] || refuse 'evidence write'

[ -d "$IMAGES" ] && [ ! -L "$IMAGES" ] || refuse "$IMAGES missing"
sums=$(cd "$IMAGES" && sha256sum -c "$MANIFEST" 2>&1) || refuse "archive checksum: $(echo "$sums" | grep -v ': OK$' | head -3)"
say "$sums"
while read -r line; do say "archive sha256 $line"; done < "$MANIFEST"
present=$(timeout 30 "$K3S" ctr -n k8s.io images ls -q 2>/dev/null) || refuse 'k3s ctr images ls failed'

# Tag conflicts before any import: a reference that exists must already carry the expected ID. Presence comes
# from the containerd listing, so a failed ID lookup can never be read as "absent".
plan=
for line in $EXPECTED; do
  ref=${line%%|*}; id=${line##*|}
  if ! printf '%s\n' "$present" | grep -qxF "$ref"; then plan="$plan import:$line"; continue; fi
  got=$(image_id "$ref")
  [ "$got" = "$id" ] || refuse "$ref exists with ID ${got:-unreadable}, expected $id; never retagged"
  plan="$plan skip:$line"
done
[ "$logfail" = 0 ] || refuse 'evidence write'

for step in $plan; do
  action=${step%%:*}; line=${step#*:}
  ref=${line%%|*}; rest=${line#*|}; file=${rest%%|*}
  if [ "$action" = skip ]; then say "skip $ref (already imported with the expected ID)"; continue; fi
  [ "$logfail" = 0 ] || { say 'evidence write failed before an import'; [ "$imported" = 1 ] && finish 9; finish 3; }
  imported=1
  timeout 600 "$K3S" ctr -n k8s.io images import "$IMAGES/$file" >/dev/null 2>&1 || { say "import of $file failed"; finish 5; }
  say "imported $file"
done

listing=$(timeout 30 "$K3S" ctr -n k8s.io images ls 2>/dev/null) || { say 'ctr images ls failed after import'; finish 8; }
bad=0
for line in $EXPECTED; do
  ref=${line%%|*}; id=${line##*|}; rest=${line#*|}; file=${rest%%|*}
  row=$(echo "$listing" | awk -v r="$ref" '$1 == r {print $3}')
  got=$(image_id "$ref")
  archive=$(awk -v f="$file" '$2 == f {print $1}' "$MANIFEST")
  say "chain source $SOURCE -> host id $id -> archive $file $archive -> import $ref ${row:-missing} -> cri id ${got:-missing}"
  [ -n "$row" ] && [ "$got" = "$id" ] && [ -n "$archive" ] || { say "MISMATCH $ref"; bad=1; }
done
[ "$bad" = 0 ] || { say 'postcheck failed: chain'; finish 8; }
finish 0
