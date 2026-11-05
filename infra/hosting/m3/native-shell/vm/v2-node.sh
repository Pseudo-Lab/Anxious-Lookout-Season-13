#!/bin/sh
# #68 dev-VM V2: node preparation, as root in the guest after review cleared this SHA-256. Development VM only;
# the production procedures are infra/hosting/m3/native-shell/README.md sections 1 and 2 (user / PM).
#   A. Install the seccomp profile seccomp-runner-userns-v1.json as
#      /var/lib/kubelet/seccomp/profiles/anxious-runner-userns-v1.json (root 0644), never overwriting.
#   B. Rename the guest's ptrace_scope file /etc/sysctl.d/90-shell-pilot-yama.conf to the production name
#      90-anxious-yama.conf. The value stays 1 throughout; the old file is kept in $EVIDENCE, not deleted.
# No Pod, Deployment, binding, DB, SELinux or package change. A is inert until a Pod names the profile.
# Each step is skipped when it is already in its target state, and refused when the state is anything else.
# Failure in B restores the old file and ptrace_scope=1, then checks both; INT/TERM does the same.
# rc: 0 done and verified; 3 refused, nothing changed; 5 a step failed and was restored (A's new file removed,
#     B's old file and value back); 6 a step failed and the restore did not verify (stop, report, check by hand);
#     8 changed but the postcheck failed; 9 evidence incomplete after a change; 130 interrupted (restored as for 5).
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
phase=none

live() { sysctl -n kernel.yama.ptrace_scope 2>/dev/null; }
settings() { grep -Ev '^[[:space:]]*(#|;|$)' "$1" 2>/dev/null; }  # the file's non-comment lines
last_source() { /usr/lib/systemd/systemd-sysctl --cat-config 2>/dev/null \
  | awk '/^# \//{file=$2} /^[[:space:]]*kernel\.yama\.ptrace_scope/{last=file": "$0} END{print last}'; }

restore_b() {  # back to the old file and value 1; prints the verdict
  [ -e "$KEPT" ] && [ ! -e "$OLD_SYSCTL" ] && mv -n "$KEPT" "$OLD_SYSCTL"
  rm -f "$NEW_SYSCTL"
  sysctl -w kernel.yama.ptrace_scope=1 >/dev/null 2>&1
  if [ -f "$OLD_SYSCTL" ] && [ ! -e "$NEW_SYSCTL" ] && [ "$(live)" = 1 ] && [ "$(settings "$OLD_SYSCTL")" = "$SETTING" ]; then
    say 'restore B verified: old file back, ptrace_scope 1'; return 0
  fi
  say "RESTORE B NOT VERIFIED: old=$( [ -f "$OLD_SYSCTL" ] && echo present || echo absent) new=$( [ -e "$NEW_SYSCTL" ] && echo present || echo absent) live=$(live)"
  return 1
}
fail_a() { say "A failed: $1"; rm -f "$PROFILE_DST"; [ ! -e "$PROFILE_DST" ] && { say 'restore A verified: new profile removed'; finish 5; }; say 'RESTORE A NOT VERIFIED'; finish 6; }
fail_b() { say "B failed: $1"; restore_b && finish 5; finish 6; }
on_signal() {
  say "interrupted in phase $phase"
  case "$phase" in
    a) rm -f "$PROFILE_DST";;
    b) restore_b || say 'check by hand before any later stage';;
  esac
  [ "$(live)" = 1 ] || sysctl -w kernel.yama.ptrace_scope=1 >/dev/null 2>&1
  say "ptrace_scope $(live) after interrupt"
  exit 130
}
trap on_signal INT TERM

start_evidence v2

# Precheck: nothing changes here.
say "selinux $(getenforce 2>/dev/null)"
[ "$(live)" = 1 ] || refuse "ptrace_scope is $(live), expected 1"
say "ptrace_scope 1 (source $(last_source))"
[ "$(sha256sum "$PROFILE_SRC" 2>/dev/null | cut -d' ' -f1)" = "$PROFILE_SHA" ] || refuse 'profile source digest'
for f in "$PROFILE_DST" "$SECCOMP_DIR/shell-pilot-userns.json" "$OLD_SYSCTL" "$NEW_SYSCTL" "$KEPT"; do file_state "$f"; done
require_evidence

if [ -e "$PROFILE_DST" ] || [ -L "$PROFILE_DST" ]; then
  [ -f "$PROFILE_DST" ] && [ ! -L "$PROFILE_DST" ] && [ "$(sha256sum "$PROFILE_DST" | cut -d' ' -f1)" = "$PROFILE_SHA" ] \
    || refuse "$PROFILE_DST exists with other content; never overwritten"
  do_a=0
else
  [ -d "$SECCOMP_DIR" ] && [ ! -L "$SECCOMP_DIR" ] || refuse "$SECCOMP_DIR missing"
  do_a=1
fi
if [ -e "$NEW_SYSCTL" ]; then
  [ ! -e "$OLD_SYSCTL" ] && [ "$(settings "$NEW_SYSCTL")" = "$SETTING" ] || refuse "$NEW_SYSCTL exists with another state"
  do_b=0
else
  [ -f "$OLD_SYSCTL" ] && [ ! -L "$OLD_SYSCTL" ] && [ "$(settings "$OLD_SYSCTL")" = "$SETTING" ] \
    || refuse "$OLD_SYSCTL is not the expected single setting"
  [ ! -e "$KEPT" ] || refuse "$KEPT exists"
  do_b=1
fi
say "plan A=$( [ "$do_a" = 1 ] && echo install || echo skip) B=$( [ "$do_b" = 1 ] && echo rename || echo skip)"
require_evidence

# A: seccomp profile.
if [ "$do_a" = 1 ]; then
  changing; phase=a
  install -o root -g root -m 0644 "$PROFILE_SRC" "$PROFILE_DST" || fail_a 'install'
  restorecon "$PROFILE_DST" 2>/dev/null || true  # default label for the kubelet directory
  [ "$(sha256sum "$PROFILE_DST" | cut -d' ' -f1)" = "$PROFILE_SHA" ] && [ "$(stat -c '%U:%G %a' "$PROFILE_DST")" = 'root:root 644' ] \
    || fail_a 'postcheck of the installed profile'
  say 'A installed'
  phase=none
fi

# B: rename the sysctl file; the value never leaves 1.
if [ "$do_b" = 1 ]; then
  changing; phase=b
  printf '%s\n' "$SETTING" | install -o root -g root -m 0644 /dev/stdin "$NEW_SYSCTL" || fail_b 'write the new file'
  restorecon "$NEW_SYSCTL" 2>/dev/null || true
  mv -n "$OLD_SYSCTL" "$KEPT" && [ ! -e "$OLD_SYSCTL" ] || fail_b 'keep the old file in evidence'
  sysctl -p "$NEW_SYSCTL" >/dev/null 2>&1 || fail_b 'sysctl -p of the new file'
  [ "$(live)" = 1 ] || fail_b "ptrace_scope is $(live) after the change"
  case "$(last_source)" in "$NEW_SYSCTL: kernel.yama.ptrace_scope = 1") ;; *) fail_b "boot order: last source is $(last_source)";; esac
  say 'B renamed'
  phase=none
fi

# Postcheck.
bad=
[ "$(live)" = 1 ] || bad="$bad ptrace_scope"
[ "$(getenforce 2>/dev/null)" = Enforcing ] || bad="$bad selinux"
[ "$(sha256sum "$PROFILE_DST" 2>/dev/null | cut -d' ' -f1)" = "$PROFILE_SHA" ] || bad="$bad profile"
[ "$(settings "$NEW_SYSCTL")" = "$SETTING" ] && [ ! -e "$OLD_SYSCTL" ] || bad="$bad sysctl-file"
say "after: ptrace_scope $(live), last source $(last_source), selinux $(getenforce 2>/dev/null)"
for f in "$PROFILE_DST" "$SECCOMP_DIR/shell-pilot-userns.json" "$OLD_SYSCTL" "$NEW_SYSCTL" "$KEPT"; do file_state "$f"; done
[ -z "$bad" ] || { say "postcheck failed:$bad"; finish 8; }
finish 0
