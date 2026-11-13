# #68 dev-VM stages V2+: evidence helpers (sourced after lib.sh, never run on its own). Development VM only.
# The rules review cleared for V1, plus V2 round 1: the evidence file exists before any change; every line goes to
# stdout and to it; a failed write, result append or evidence hash is never rc 0; once something changed, no
# exit says "nothing changed", and the changes that remain are listed.
#   start_evidence <stage>   create $EVIDENCE/<stage>-<UTC>.txt atomically (rc 3 if impossible)
#   sidecar <name>           create $log.<name> atomically and print its path (for long lists); fails if impossible
#   say <line>               stdout and evidence; a failed evidence write sets logfail
#   changing                 call right before each change: refuses (3) or stops as incomplete (9) on a failed write
#   done_change <text>       record a change that now exists (listed by finish and by every stop after it)
#   finish <rc>              result line and evidence hash; rc 9 if the record is incomplete after a change
#   state <path>             one-line state (type, hash, owner, mode, SELinux label); non-zero if any lookup failed
log=; changed=0; logfail=0; changes=
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
  if [ "$changed" = 1 ]; then say "changes in place:${changes:- none completed}"; fi
  say "result rc=$rc"
  [ "$logfail" = 0 ] || incomplete 'a write to the evidence file failed'
  if [ -n "$log" ]; then
    h=$(sha256sum "$log" 2>/dev/null) && h=${h%% *} && [ ${#h} = 64 ] && printf 'evidence %s %s\n' "$h" "$log" \
      || incomplete 'the evidence hash could not be computed'
  fi
  [ "$changed" = 1 ] && [ "$rc" != 0 ] && printf '%s\n' 'do not run a later stage before this is checked by hand'
  exit "$rc"
}
refuse() {  # before any change only; afterwards a refusal is an incomplete or failed run, never "nothing changed"
  if [ "$changed" = 1 ]; then say "stopped: $1"; finish 9; fi
  say "precheck: $1; nothing changed"; finish 3
}
start_evidence() {
  [ "$(id -u)" = 0 ] || refuse 'not root'
  install -d -o root -g root -m 0700 "$EVIDENCE" || refuse 'evidence directory'
  stamp=$(date -u +%Y%m%dT%H%M%SZ) || refuse 'clock'
  candidate="$EVIDENCE/$1-$stamp.txt"
  ( set -C; : > "$candidate" ) 2>/dev/null || refuse "cannot create $candidate (exists or not writable)"
  log=$candidate
  say "$1: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  [ "$logfail" = 0 ] || refuse 'evidence write'
}
sidecar() { ( set -C; : > "$log.$1" ) 2>/dev/null && printf '%s\n' "$log.$1"; }
changing() {
  [ "$logfail" = 0 ] || refuse 'evidence write failed before the next change'
  changed=1
}
done_change() { changes="$changes [$1]"; say "changed: $1"; }
# state <path>: absent | symlink -> target | <type> <sha256 or -> <owner:group> <mode> <SELinux label>.
state() {
  if [ ! -e "$1" ] && [ ! -L "$1" ]; then printf '%s\n' "absent"; return 0; fi
  if [ -L "$1" ]; then t=$(readlink "$1") || return 1; printf '%s\n' "symlink -> $t"; return 0; fi
  m=$(stat -c '%F|%U:%G|%a|%C' "$1" 2>/dev/null) || return 1
  case "$m" in 'regular file|'*|'regular empty file|'*) h=$(sha256sum "$1" 2>/dev/null) || return 1; h=${h%% *};; *) h=-;; esac
  [ -n "$h" ] || return 1
  printf '%s\n' "$(echo "$m" | cut -d'|' -f1) $h $(echo "$m" | cut -d'|' -f2-4 | tr '|' ' ')"
}
record_state() {  # say the state of $1; non-zero if a lookup failed
  s=$(state "$1") || { say "state $1 LOOKUP FAILED"; return 1; }
  say "state $1 $s"
}
