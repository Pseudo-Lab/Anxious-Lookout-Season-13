# #68 dev-VM stages V2+: evidence helpers (sourced after lib.sh, never run on its own). Development VM only.
# The same rules review cleared for V1: the evidence file exists before any change, every line goes to stdout and
# to it, and a failed write, result append or evidence hash can never end as rc 0.
#   start_evidence <stage>   create $EVIDENCE/<stage>-<UTC>.txt atomically (rc 3 if impossible)
#   say <line>               stdout and evidence; a failed evidence write sets logfail
#   changing                 call before the first change: from then on evidence failures are rc 9
#   require_evidence         refuse (rc 3) if a write already failed, before the first change
#   finish <rc>              result line, evidence hash; rc 9 if the record is incomplete after a change
log=; changed=0; logfail=0
say() {
  printf '%s\n' "$1"
  if [ -n "$log" ]; then printf '%s\n' "$1" >> "$log" || logfail=1; fi
}
incomplete() {
  printf '%s\n' "evidence incomplete: $1"
  if [ "$changed" = 1 ]; then rc=9; else rc=3; fi
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
refuse() { say "precheck: $1; nothing changed"; finish 3; }
start_evidence() {
  [ "$(id -u)" = 0 ] || refuse 'not root'
  install -d -o root -g root -m 0700 "$EVIDENCE" || refuse 'evidence directory'
  stamp=$(date -u +%Y%m%dT%H%M%SZ) || refuse 'clock'
  candidate="$EVIDENCE/$1-$stamp.txt"
  ( set -C; : > "$candidate" ) 2>/dev/null || refuse "cannot create $candidate (exists or not writable)"
  log=$candidate
  say "$1: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  require_evidence
}
require_evidence() { [ "$logfail" = 0 ] || refuse 'evidence write'; }
changing() { require_evidence; changed=1; }
# File state for before/after comparisons: hash, owner, mode, SELinux label, or "absent".
file_state() {
  if [ -e "$1" ] || [ -L "$1" ]; then
    say "state $1 $(sha256sum "$1" 2>/dev/null | cut -d' ' -f1) $(stat -c '%U:%G %a %F' "$1" 2>/dev/null) $(ls -Z "$1" 2>/dev/null | cut -d' ' -f1)"
  else
    say "state $1 absent"
  fi
}
