#!/bin/sh
# #68 dev-VM V2: node preparation, as root in the guest after review cleared this SHA-256. Development VM only;
# the production procedures are infra/hosting/m3/native-shell/README.md sections 1 and 2 (user / PM).
#   A. Install the seccomp profile seccomp-runner-userns-v1.json as
#      /var/lib/kubelet/seccomp/profiles/anxious-runner-userns-v1.json (root 0644, default label), never overwriting.
#   B. Rename the guest's ptrace_scope file /etc/sysctl.d/90-shell-pilot-yama.conf to the production name
#      90-anxious-yama.conf. The value stays 1 throughout; the old file is kept in $EVIDENCE, not deleted.
# No Pod, Deployment, binding, DB, SELinux policy or package change. A is inert until a Pod names the profile.
#
# Target states (the same check after an install and for a skip):
#   A: regular file, not a symlink, the pinned digest, root:root 644, label = matchpathcon's default.
#   B: new file regular, not a symlink, exactly "kernel.yama.ptrace_scope = 1\n", root:root 644, default label;
#      old file absent (no dangling link either); live value 1; the last ptrace_scope source systemd-sysctl
#      reads at boot is the new file with 1.
# A step already in its target state is skipped; any other existing state refuses. Every lookup is checked: a
# failed lookup refuses before a change and fails the run after one.
# B failure restores the old file and value 1 and compares the restored file with the original's hash, owner,
# mode and label. A, if it was done, stays and is reported as staying. INT/TERM restores the step in progress
# (further signals are ignored during that) and checks the result.
# rc: 0 done and verified; 3 refused, nothing changed; 5 the failed step was restored and verified (earlier steps
#     stay, listed); 6 a restore did not verify (check by hand); 8 changed but the postcheck failed; 9 evidence
#     incomplete after a change; 130 interrupted, the step in progress restored and verified.
set -u
umask 077
here=$(dirname "$0")
. "$here/lib.sh"
. "$here/evidence.sh"

PROFILE_SRC="$here/seccomp-runner-userns-v1.json"
PROFILE_SHA=9a6594419eae4089e737c787597d2ed4950fd7e1031e371f490d5b2c935dff78
PROFILE_DST="$SECCOMP_DIR/anxious-runner-userns-v1.json"
OLD_SYSCTL=/etc/sysctl.d/90-shell-pilot-yama.conf
NEW_SYSCTL=/etc/sysctl.d/90-anxious-yama.conf
KEPT="$EVIDENCE/90-shell-pilot-yama.conf.v0"
SETTING='kernel.yama.ptrace_scope = 1'
SETTING_SHA=$(printf '%s\n' "$SETTING" | sha256sum | cut -d' ' -f1)
WATCHED="$PROFILE_DST $SECCOMP_DIR/shell-pilot-userns.json $OLD_SYSCTL $NEW_SYSCTL $KEPT"
phase=none; old_state=

present() { [ -e "$1" ] || [ -L "$1" ]; }
live() { sysctl -n kernel.yama.ptrace_scope 2>/dev/null; }
default_label() { matchpathcon -n "$1" 2>/dev/null; }
settings() { grep -Ev '^[[:space:]]*(#|;|$)' "$1" 2>/dev/null; }
last_source() {  # prints "<file>: <line>"; non-zero if systemd-sysctl fails or names no source
  cfg=$(/usr/lib/systemd/systemd-sysctl --cat-config 2>/dev/null) || return 1
  out=$(printf '%s\n' "$cfg" | awk '/^# \//{file=$2} /^[[:space:]]*kernel\.yama\.ptrace_scope/{last=file": "$0} END{print last}')
  [ -n "$out" ] || return 1
  printf '%s\n' "$out"
}
is_file_as() {  # $1 path, $2 sha256, $3 owner:group mode: regular, not a symlink, digest, owner/mode, default label
  [ -f "$1" ] && [ ! -L "$1" ] || return 1
  s=$(state "$1") || return 1
  d=$(default_label "$1") || return 1
  [ "$s" = "regular file $2 $3 $d" ]
}
a_done() { is_file_as "$PROFILE_DST" "$PROFILE_SHA" 'root:root 644'; }
b_done() {
  is_file_as "$NEW_SYSCTL" "$SETTING_SHA" 'root:root 644' && ! present "$OLD_SYSCTL" && [ "$(live)" = 1 ] || return 1
  src=$(last_source) && [ "$src" = "$NEW_SYSCTL: $SETTING" ]
}
record_all() {  # every watched path; non-zero if any lookup failed
  ok=0
  for f in $WATCHED; do record_state "$f" || ok=1; done
  return $ok
}

restore_a() {
  rm -f "$PROFILE_DST"
  if ! present "$PROFILE_DST"; then say 'restore A verified: the new profile is gone'; return 0; fi
  say 'RESTORE A NOT VERIFIED: the new profile is still present'; return 1
}
restore_b() {  # old file back as it was, new file gone, value 1
  if present "$KEPT" && ! present "$OLD_SYSCTL"; then mv -n "$KEPT" "$OLD_SYSCTL"; fi
  rm -f "$NEW_SYSCTL"
  [ "$(live)" = 1 ] || sysctl -w kernel.yama.ptrace_scope=1 >/dev/null 2>&1
  now=$(state "$OLD_SYSCTL") || now='LOOKUP FAILED'
  if [ "$now" = "$old_state" ] && ! present "$NEW_SYSCTL" && [ "$(live)" = 1 ]; then
    say "restore B verified: old file back ($now), new file gone, ptrace_scope 1"; return 0
  fi
  say "RESTORE B NOT VERIFIED: old=$now (expected $old_state) new=$(state "$NEW_SYSCTL" 2>/dev/null || echo '?') live=$(live)"
  return 1
}
fail_step() {  # $1 a|b, $2 reason
  say "$(echo "$1" | tr ab AB) failed: $2"
  trap '' INT TERM
  if restore_"$1"; then phase=none; finish 5; fi
  finish 6
}
on_signal() {
  trap '' INT TERM  # a second signal must not interrupt the restore
  say "interrupted in phase $phase"
  ok=0
  case "$phase" in
    a) restore_a || ok=1;;
    b) restore_b || ok=1;;
  esac
  [ "$(live)" = 1 ] || sysctl -w kernel.yama.ptrace_scope=1 >/dev/null 2>&1
  say "ptrace_scope $(live) after interrupt"
  [ "$(live)" = 1 ] || ok=1
  record_all || ok=1
  [ "$ok" = 0 ] && finish 130
  say 'interrupt restore NOT VERIFIED'
  finish 6
}
trap on_signal INT TERM

start_evidence v2

# Precheck: nothing changes here; every lookup must succeed.
[ "$(getenforce 2>/dev/null)" = Enforcing ] || refuse "SELinux is $(getenforce 2>/dev/null || echo unknown), expected Enforcing"
say 'selinux Enforcing'
command -v matchpathcon >/dev/null 2>&1 || refuse 'matchpathcon missing (needed to check labels)'
[ "$(live)" = 1 ] || refuse "ptrace_scope is $(live), expected 1"
src=$(last_source) || refuse 'systemd-sysctl --cat-config failed'
say "ptrace_scope 1, last source $src"
[ "$(sha256sum "$PROFILE_SRC" 2>/dev/null | cut -d' ' -f1)" = "$PROFILE_SHA" ] || refuse 'profile source digest'
record_all || refuse 'a state lookup failed'

if a_done; then do_a=0
elif present "$PROFILE_DST"; then refuse "$PROFILE_DST exists in another state; never overwritten"
else [ -d "$SECCOMP_DIR" ] && [ ! -L "$SECCOMP_DIR" ] || refuse "$SECCOMP_DIR missing"; do_a=1
fi
if b_done; then do_b=0
elif present "$NEW_SYSCTL"; then refuse "$NEW_SYSCTL exists but is not in the target state"
else
  [ -f "$OLD_SYSCTL" ] && [ ! -L "$OLD_SYSCTL" ] && [ "$(settings "$OLD_SYSCTL")" = "$SETTING" ] \
    || refuse "$OLD_SYSCTL is not the expected single setting"
  present "$KEPT" && refuse "$KEPT exists"
  old_state=$(state "$OLD_SYSCTL") || refuse "state of $OLD_SYSCTL"
  do_b=1
fi
say "plan A=$( [ "$do_a" = 1 ] && echo install || echo skip) B=$( [ "$do_b" = 1 ] && echo rename || echo skip)"

# A: seccomp profile.
if [ "$do_a" = 1 ]; then
  changing; phase=a
  install -o root -g root -m 0644 "$PROFILE_SRC" "$PROFILE_DST" || fail_step a 'install'
  restorecon "$PROFILE_DST" || fail_step a 'restorecon'
  a_done || fail_step a 'the installed profile is not in the target state'
  phase=none; done_change "A: $PROFILE_DST installed"
fi

# B: rename the sysctl file; the value never leaves 1.
if [ "$do_b" = 1 ]; then
  changing; phase=b
  printf '%s\n' "$SETTING" | install -o root -g root -m 0644 /dev/stdin "$NEW_SYSCTL" || fail_step b 'write the new file'
  restorecon "$NEW_SYSCTL" || fail_step b 'restorecon'
  mv -n "$OLD_SYSCTL" "$KEPT" && ! present "$OLD_SYSCTL" || fail_step b 'keep the old file in evidence'
  kept=$(state "$KEPT") && [ "$kept" = "$old_state" ] || fail_step b 'the kept file differs from the original'
  sysctl -p "$NEW_SYSCTL" >/dev/null 2>&1 || fail_step b 'sysctl -p of the new file'
  b_done || fail_step b "not in the target state (live $(live), last source $(last_source || echo 'lookup failed'))"
  phase=none; done_change "B: $OLD_SYSCTL -> $NEW_SYSCTL (old kept as $KEPT)"
fi

# Postcheck: both targets, Enforcing, and the after states.
bad=
a_done || bad="$bad A"
b_done || bad="$bad B"
[ "$(getenforce 2>/dev/null)" = Enforcing ] || bad="$bad selinux"
say "after: ptrace_scope $(live), last source $(last_source || echo 'lookup failed'), selinux $(getenforce 2>/dev/null)"
record_all || bad="$bad state-lookup"
[ -z "$bad" ] || { say "postcheck failed:$bad"; finish 8; }
finish 0
