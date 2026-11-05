#!/bin/sh
# #68 dev-VM: the runner's SELinux module `anxious_runner_bwrap` (native-shell/selinux/, README section 8).
# Development VM only. Run as root in the guest after review cleared this script's SHA-256.
#
#   selinux-module.sh install 1     version 1 (devpts mount), only when no module of that name is installed
#   selinux-module.sh upgrade 2     version 2 (adds tmpfs remount), only over this script's version 1
#   selinux-module.sh remove        remove the module (rollback), only when it is one of this script's versions
#
# The module files are anxious_runner_bwrap-<v>.pp next to this script, built by native-shell/selinux/build.sh
# with the guest's checkpolicy/libsepol 3.6 and pinned below. Recorded before and after every change: the
# module list with priorities (`semodule -lfull`), the stored module of that name (its decompressed `hll`
# SHA-256, compared with the pinned file), and the loaded policy (`/sys/fs/selinux/policy` SHA-256, a copy kept
# as a sidecar for offline `sesearch`). `semodule` applies a transaction: it either installs or leaves the
# store as it was; the after-state is looked up in both cases.
# rc: 0 done and verified; 2 usage; 3 refused, nothing changed; 5 semodule failed (state recorded); 8 the
# after-state does not match; 9 evidence incomplete after a change; 130 interrupted.
set -u
umask 077
here=$(dirname "$0")
. "$here/lib.sh"
. "$here/evidence.sh"

NAME=anxious_runner_bwrap
PP1_SHA=68270c7727d8fab64bf990d0d6769e412db205a0f386e6f4af6bb2d80175f6e6
PP2_SHA=3f4fc25cae5bfdceb86e1026f3ffbc72f1194c1a8e7cb75178e968dd159dbeea
STORE=/var/lib/selinux/targeted/active/modules/400/$NAME

action=${1:-}; version=${2:-}
case "$action:$version" in
  install:1|upgrade:2|remove:) ;;
  *) echo 'usage: selinux-module.sh install 1 | upgrade 2 | remove'; exit 2;;
esac
attempted=0
on_signal() {
  trap '' INT TERM
  say "interrupted (semodule attempted: $attempted)"
  [ "$attempted" = 1 ] && { record after_interrupt || true; finish 9; }
  finish 130
}
trap on_signal INT TERM

stored_sha() {  # SHA-256 of the stored module: libsemanage keeps the .pp bzip2-compressed as `hll`
  # (bzip-blocksize=5 in semanage.conf; checked on the guest: decompressed it starts with the .pp magic).
  # No bzip2 binary on the guest, so Python's bz2. "absent" when no module of that name is stored.
  [ -e "$STORE" ] || { echo absent; return 0; }
  [ -f "$STORE/hll" ] || return 1
  h=$(python3 -I -c 'import bz2, hashlib, sys; print(hashlib.sha256(bz2.decompress(open(sys.argv[1], "rb").read())).hexdigest())' "$STORE/hll" 2>/dev/null) || return 1
  [ ${#h} = 64 ] || return 1; echo "$h"
}
record() {  # $1 label: module list, stored module, loaded policy (hash + sidecar copy)
  l=$(semodule -lfull 2>/dev/null) || { note "$1: semodule -lfull failed"; return 1; }
  say "$1 modules with $NAME: $(printf '%s\n' "$l" | grep -E "(^| )$NAME( |\$)" || echo none)"
  say "$1 module count $(printf '%s\n' "$l" | grep -c .)"
  st=$(stored_sha) || { note "$1: stored module lookup failed"; return 1; }
  say "$1 stored $NAME $st"
  lp=$(sidecar "policy-$1") || { note "$1: sidecar"; return 1; }
  cat /sys/fs/selinux/policy > "$lp" 2>/dev/null || { note "$1: loaded policy copy"; return 1; }
  h=$(sha256sum "$lp" 2>/dev/null) && h=${h%% *} && [ ${#h} = 64 ] || { note "$1: loaded policy hash"; return 1; }
  say "$1 loaded policy sha256 $h (copy $lp)"
  eval "${1}_stored=\$st"
}
note() { say "evidence incomplete: $1"; logfail=1; }

start_evidence "selinux-$action"
[ "$(getenforce 2>/dev/null)" = Enforcing ] || refuse 'SELinux is not Enforcing'
say "policy $(rpm -q selinux-policy-targeted container-selinux libsepol policycoreutils 2>/dev/null | tr '\n' ' ')"
case "$action" in
  install) pp="$here/$NAME-1.pp"; want=$PP1_SHA;;
  upgrade) pp="$here/$NAME-2.pp"; want=$PP2_SHA;;
  remove) pp=; want=;;
esac
if [ -n "$pp" ]; then
  [ "$(sha256sum "$pp" 2>/dev/null | cut -d' ' -f1)" = "$want" ] || refuse "$pp digest"
fi
record before || refuse 'before-state lookup'
case "$action:$before_stored" in
  install:absent) ;;
  install:*) refuse "a module named $NAME is already installed (stored $before_stored); not replacing it";;
  upgrade:"$PP1_SHA") ;;
  upgrade:*) refuse "the installed $NAME is not this script's version 1 (stored $before_stored)";;
  remove:"$PP1_SHA"|remove:"$PP2_SHA") ;;
  remove:*) refuse "the installed $NAME is not one of this script's versions (stored $before_stored)";;
esac
semodule -lfull 2>/dev/null | grep -E "(^| )$NAME( |\$)" | grep -qv '^400 ' && refuse "$NAME exists at a priority other than 400"

changing
attempted=1
case "$action" in
  install|upgrade) out=$(semodule -X 400 -i "$pp" 2>&1); s=$?;;
  remove) out=$(semodule -X 400 -r "$NAME" 2>&1); s=$?;;
esac
say "semodule rc $s $(printf '%s' "$out" | head -3 | tr '\n' ' ')"
record after || { say 'after-state lookup incomplete'; finish 9; }
case "$action" in
  install) [ "$after_stored" = "$PP1_SHA" ] && done_change "module $NAME version 1 installed (priority 400)";;
  upgrade) [ "$after_stored" = "$PP2_SHA" ] && done_change "module $NAME upgraded to version 2 (priority 400)";;
  remove) [ "$after_stored" = absent ] && done_change "module $NAME removed";;
esac
[ "$s" = 0 ] || finish 5
case "$action:$after_stored" in
  install:"$PP1_SHA"|upgrade:"$PP2_SHA"|remove:absent) finish 0;;
  *) say "after-state does not match: stored $after_stored"; finish 8;;
esac
