# #90 stage B import (chat-stream/DEV-VM.md P1; guest, as root, sent as the argument of `sh -c`; never installed): import
# the runner and the runtime archive built by i90-host-build.sh (stage A) into k3s containerd and add their digest
# references, each with the evidence chain source SHA -> host image ID -> archive SHA-256 -> import reference / digest
# -> CRI image ID (must equal the host ID) -> digest reference -> CRI image ID. native-shell/vm/pa-import.sh's logic
# (archive check, import, chain check, an existing reference with another ID refuses, digest-reference tagging) for two
# archives, with the constants below instead of lib.sh. Development VM only. No Pod, Deployment, binding, DB or node
# file change; nothing is loaded into guest Docker; the images are selected by nothing until i90-switch (stage C).
#
# PINS: back fills SOURCE / *_ARCHIVE_SHA / *_HOST_ID / the tags from the stage A build output only (copied from that
# output, never typed); review clears this file by hash. While any pin is the placeholder PIN the script refuses.
#
# Evidence: /root/issue90/evidence/i90-import-<UTC>.txt is created (never overwritten) before anything is imported;
# every line goes to it and to stdout. A failed write before an import refuses (rc 3); after one, rc 9.
# rc: 0 both imported (or already present with the host ID), both digest references present and verified; 3 refused,
#     nothing imported or tagged; 5 an import or tag command failed (an import stays; it changes no workload);
#     8 imported but a chain or digest-reference check failed; 9 evidence incomplete; 130 interrupted.
# Never rerun on a nonzero rc. The printed `i90 runner digest reference` / `i90 runtime digest reference` lines are
# the values stage C pins (from this output).
set -u
umask 077
K3S=/usr/local/bin/k3s
EVIDENCE=/root/issue90/evidence
IMAGES=/home/opc/issue90-images
SOURCE=PIN
RUNNER_TAG_REF=PIN
RUNNER_FILE=PIN
RUNNER_ARCHIVE_SHA=PIN
RUNNER_HOST_ID=PIN
RUNTIME_TAG_REF=PIN
RUNTIME_FILE=PIN
RUNTIME_ARCHIVE_SHA=PIN
RUNTIME_HOST_ID=PIN

image_id() { timeout 30 "$K3S" crictl inspecti -o go-template --template '{{.status.id}}' "$1" 2>/dev/null; }

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
trap 'say "interrupted: an import or tag may be partial; check ctr images ls and crictl inspecti for the references and report"; exit 130' INT TERM

for pin in "$SOURCE" "$RUNNER_TAG_REF" "$RUNNER_FILE" "$RUNNER_ARCHIVE_SHA" "$RUNNER_HOST_ID" \
           "$RUNTIME_TAG_REF" "$RUNTIME_FILE" "$RUNTIME_ARCHIVE_SHA" "$RUNTIME_HOST_ID"; do
  [ "$pin" != PIN ] || { printf '%s\n' 'precheck: a pin is still the placeholder; nothing imported'; printf '%s\n' 'result rc=3'; exit 3; }
done
[ "$(id -u)" = 0 ] || refuse 'not root'
[ -d "$EVIDENCE" ] && [ ! -L "$EVIDENCE" ] || refuse 'evidence directory missing'
stamp=$(date -u +%Y%m%dT%H%M%SZ) || refuse 'clock'
candidate="$EVIDENCE/i90-import-$stamp.txt"
( set -C; : > "$candidate" ) 2>/dev/null || refuse "cannot create $candidate (exists or not writable)"
log=$candidate
say "i90-import: $(date -u +%Y-%m-%dT%H:%M:%SZ), source $SOURCE, $RUNNER_TAG_REF $RUNNER_HOST_ID, $RUNTIME_TAG_REF $RUNTIME_HOST_ID"
[ "$logfail" = 0 ] || refuse 'evidence write'

[ -d "$IMAGES" ] && [ ! -L "$IMAGES" ] || refuse "$IMAGES missing"
for pair in "$RUNNER_FILE $RUNNER_ARCHIVE_SHA" "$RUNTIME_FILE $RUNTIME_ARCHIVE_SHA"; do
  file=${pair%% *}; want=${pair#* }
  [ -f "$IMAGES/$file" ] && [ ! -L "$IMAGES/$file" ] || refuse "$file is not a regular file"
  printf '%s  %s\n' "$want" "$IMAGES/$file" | sha256sum -c - >/dev/null 2>&1 || refuse "$file is not $want"
  say "archive sha256 $want $file: OK"
done
present=$(timeout 30 "$K3S" ctr -n k8s.io images ls -q 2>/dev/null) || refuse 'k3s ctr images ls failed'

# Both references are checked before either import: one that exists must already carry its host ID (never retagged).
for pair in "$RUNNER_TAG_REF $RUNNER_HOST_ID" "$RUNTIME_TAG_REF $RUNTIME_HOST_ID"; do
  ref=${pair%% *}; want=${pair#* }
  if printf '%s\n' "$present" | grep -qxF "$ref"; then
    got=$(image_id "$ref")
    [ "$got" = "$want" ] || refuse "$ref exists with ID ${got:-unreadable}, expected $want; never retagged"
  fi
done

import_one() {  # $1 tag reference, $2 archive file
  if printf '%s\n' "$present" | grep -qxF "$1"; then
    say "skip import of $1 (already imported with the host ID)"
    return 0
  fi
  [ "$logfail" = 0 ] || { say 'evidence write failed before an import'; finish 3; }
  imported=1
  timeout 600 "$K3S" ctr -n k8s.io images import "$IMAGES/$2" </dev/null >/dev/null 2>&1 || { say "import of $2 failed"; finish 5; }
  say "imported $2"
}

verify_one() {  # $1 name, $2 tag reference, $3 archive file, $4 archive sha, $5 host id
  listing=$(timeout 30 "$K3S" ctr -n k8s.io images ls 2>/dev/null) || { say 'ctr images ls failed after the import'; finish 8; }
  digest=$(printf '%s\n' "$listing" | awk -v r="$2" '$1 == r {print $3}')
  got=$(image_id "$2")
  say "$1 chain source $SOURCE -> host id $5 -> archive $3 $4 -> import $2 ${digest:-missing} -> cri id ${got:-missing}"
  case "$digest" in
    sha256:*) d=${digest#sha256:} ;;
    *) d= ;;
  esac
  if [ ${#d} != 64 ] || ! printf '%s' "$d" | grep -qx '[0-9a-f]*' || [ "$got" != "$5" ]; then
    say "MISMATCH ($1): the import digest is not one sha256 or the CRI ID is not the host ID"; finish 8
  fi
  repo=${2%:*}
  digest_ref=$repo@$digest
  if printf '%s\n' "$listing" | awk 'NR > 1 {print $1}' | grep -qxF "$digest_ref"; then
    say "digest reference $digest_ref already present (not tagged by this run)"
  else
    timeout 60 "$K3S" ctr -n k8s.io images tag "$2" "$digest_ref" </dev/null >/dev/null 2>&1; s=$?
    [ "$s" = 0 ] || { say "tag rc $s ($1)"; finish 5; }
    say "tagged $digest_ref"
  fi
  d_id=$(image_id "$digest_ref")
  say "digest reference $digest_ref -> cri id ${d_id:-missing}"
  [ "$d_id" = "$5" ] || { say "MISMATCH ($1): the digest reference does not resolve to the host ID"; finish 8; }
  say "i90 $1 digest reference: $digest_ref"
}

import_one "$RUNNER_TAG_REF" "$RUNNER_FILE"
import_one "$RUNTIME_TAG_REF" "$RUNTIME_FILE"
verify_one runner "$RUNNER_TAG_REF" "$RUNNER_FILE" "$RUNNER_ARCHIVE_SHA" "$RUNNER_HOST_ID"
verify_one runtime "$RUNTIME_TAG_REF" "$RUNTIME_FILE" "$RUNTIME_ARCHIVE_SHA" "$RUNTIME_HOST_ID"
finish 0
