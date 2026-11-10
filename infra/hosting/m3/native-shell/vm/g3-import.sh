# #68 G3-1b (design g3 r2 §2; guest, as root, sent as the argument of `sh -c`; never installed): import the G3-0
# runner archive (code 0117d137) into k3s containerd and add its digest reference, with the evidence chain
# source SHA -> host image ID -> archive SHA-256 -> import reference / digest -> CRI image ID (must equal the host ID)
# -> digest reference -> CRI image ID. v1-import.sh's logic (archive check, import, chain check, an existing
# reference with another ID refuses) for the runner archive only, plus v3-selinux.sh's tagging (L242-246).
# Development VM only. No Pod, Deployment, binding, DB or node file change; nothing is loaded into guest Docker;
# the image is selected by nothing until G3-2.
#
# Evidence: /root/native68/evidence/g3-import-<UTC>.txt is created (never overwritten) before anything is imported;
# every line goes to it and to stdout. A failed write before the import refuses (rc 3); after it, rc 9.
# rc: 0 imported (or already present with the host ID), digest reference present and verified; 3 refused, nothing
#     imported or tagged; 5 the import or the tag command failed (an import stays; it changes no workload);
#     8 imported but a chain or digest-reference check failed; 9 evidence incomplete; 130 interrupted.
# Never rerun on a nonzero rc. The printed `g2 digest reference` line is the value G3-2 pins (from this output).
set -u
umask 077
cd /root/native68 || exit 3
LIB=4cae0e470ae4f5862c52343570a6e3c94adb70122a58aad6a97f807a6eb82475
printf '%s  %s\n' "$LIB" lib.sh | sha256sum -c - >/dev/null 2>&1 || { echo 'lib.sh is not at its pin; nothing imported'; echo 'result rc=3'; exit 3; }
. ./lib.sh
SOURCE=0117d137d0f1b17b9d2421751d3b246b85de7b24
TAG_REF=docker.io/library/anxious-dev-runner:68-0117d13
FILE=anxious-dev-runner-68-0117d13.tar
ARCHIVE_SHA=b20085405c3f5690a1ef54c7337f4859ea5494bd4b6367589193d44bddd52afe
HOST_ID=sha256:b6f52716ec20803570b45797a5c47787fd69c26a40ae64bb11580d47a24cec32
REPO=docker.io/library/anxious-dev-runner

log=; imported=0; logfail=0
say() {  # stdout and the evidence file; a failed evidence write is remembered, never ignored
  printf '%s\n' "$1"
  if [ -n "$log" ]; then printf '%s\n' "$1" >> "$log" || logfail=1; fi
}
incomplete() {
  printf '%s\n' "evidence incomplete: $1"
  if [ "$imported" = 1 ]; then rc=9; else rc=3; fi
}
finish() {
  rc=$1
  say "result rc=$rc"
  [ "$logfail" = 0 ] || incomplete 'a write to the evidence file failed'
  if [ -n "$log" ]; then
    h=$(sha256sum "$log" 2>/dev/null) && h=${h%% *} && [ ${#h} = 64 ] && printf 'evidence %s %s\n' "$h" "$log" \
      || incomplete 'the evidence hash could not be computed'
  fi
  exit "$rc"
}
refuse() { say "precheck: $1; nothing imported"; finish 3; }
trap 'say "interrupted: an import or tag may be partial; check ctr images ls and crictl inspecti for the two references and report"; exit 130' INT TERM

[ "$(id -u)" = 0 ] || refuse 'not root'
[ -d "$EVIDENCE" ] && [ ! -L "$EVIDENCE" ] || refuse 'evidence directory missing'
stamp=$(date -u +%Y%m%dT%H%M%SZ) || refuse 'clock'
candidate="$EVIDENCE/g3-import-$stamp.txt"
( set -C; : > "$candidate" ) 2>/dev/null || refuse "cannot create $candidate (exists or not writable)"
log=$candidate
say "g3-import: $(date -u +%Y-%m-%dT%H:%M:%SZ), source $SOURCE, $TAG_REF, host id $HOST_ID"
[ "$logfail" = 0 ] || refuse 'evidence write'

[ -d "$IMAGES" ] && [ ! -L "$IMAGES" ] || refuse "$IMAGES missing"
[ -f "$IMAGES/$FILE" ] && [ ! -L "$IMAGES/$FILE" ] || refuse "$FILE is not a regular file"
printf '%s  %s\n' "$ARCHIVE_SHA" "$IMAGES/$FILE" | sha256sum -c - >/dev/null 2>&1 || refuse "$FILE is not $ARCHIVE_SHA"
say "archive sha256 $ARCHIVE_SHA $FILE: OK"
present=$(timeout 30 "$K3S" ctr -n k8s.io images ls -q 2>/dev/null) || refuse 'k3s ctr images ls failed'

# A reference that exists must already carry the host ID (never retagged). Presence comes from the listing, so a
# failed ID lookup is never read as "absent".
if printf '%s\n' "$present" | grep -qxF "$TAG_REF"; then
  got=$(image_id "$TAG_REF")
  [ "$got" = "$HOST_ID" ] || refuse "$TAG_REF exists with ID ${got:-unreadable}, expected $HOST_ID; never retagged"
  say "skip import of $TAG_REF (already imported with the host ID)"
else
  [ "$logfail" = 0 ] || refuse 'evidence write'
  imported=1
  timeout 600 "$K3S" ctr -n k8s.io images import "$IMAGES/$FILE" </dev/null >/dev/null 2>&1 || { say "import of $FILE failed"; finish 5; }
  say "imported $FILE"
fi

listing=$(timeout 30 "$K3S" ctr -n k8s.io images ls 2>/dev/null) || { say 'ctr images ls failed after the import'; finish 8; }
digest=$(printf '%s\n' "$listing" | awk -v r="$TAG_REF" '$1 == r {print $3}')
got=$(image_id "$TAG_REF")
say "chain source $SOURCE -> host id $HOST_ID -> archive $FILE $ARCHIVE_SHA -> import $TAG_REF ${digest:-missing} -> cri id ${got:-missing}"
case "$digest" in
  sha256:*) d=${digest#sha256:} ;;
  *) d= ;;
esac
if [ ${#d} != 64 ] || ! printf '%s' "$d" | grep -qx '[0-9a-f]*' || [ "$got" != "$HOST_ID" ]; then
  say 'MISMATCH: the import digest is not one sha256 or the CRI ID is not the host ID'; finish 8
fi
DIGEST_REF=$REPO@$digest
if printf '%s\n' "$listing" | awk 'NR > 1 {print $1}' | grep -qxF "$DIGEST_REF"; then
  say "digest reference $DIGEST_REF already present (not tagged by this run)"
else
  timeout 60 "$K3S" ctr -n k8s.io images tag "$TAG_REF" "$DIGEST_REF" </dev/null >/dev/null 2>&1; s=$?
  [ "$s" = 0 ] || { say "tag rc $s"; finish 5; }
  say "tagged $DIGEST_REF"
fi
d_id=$(image_id "$DIGEST_REF")
say "digest reference $DIGEST_REF -> cri id ${d_id:-missing}"
[ "$d_id" = "$HOST_ID" ] || { say 'MISMATCH: the digest reference does not resolve to the host ID'; finish 8; }
say "g2 digest reference: $DIGEST_REF"
finish 0
