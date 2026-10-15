#!/bin/sh
# PM-only: stdin source, exact read argv, no remote files or configuration writes.
set -f
ulimit -c 0 || exit 2
[ "$#" -eq 2 ] || exit 2
dn11_mode=$1 dn11_nonce=$2
case "$dn11_mode" in help|read) ;; *) exit 2;; esac
[ "${#dn11_nonce}" -eq 32 ] || exit 2
case "$dn11_nonce" in *[!0-9a-f]*) exit 2;; esac
IFS= read -r dn11_comm < /proc/1/comm || exit 2
[ "$dn11_comm" = cilium-agent ] || exit 2
IFS= read -r dn11_stat < /proc/$$/stat || exit 2
dn11_tail=${dn11_stat##*) }
set -- $dn11_tail
[ "$#" -ge 20 ] || exit 2
shift
dn11_parent=$1 dn11_group=$2
shift 18
dn11_ticks=$1
[ "$dn11_parent" = "$dn11_group" ] && [ "$dn11_group" -gt 1 ] && [ "$$" -gt 1 ] || exit 2
kill -0 "-$dn11_group" 2>/dev/null || exit 2
printf 'DN11:%s:{"pid":%s,"parentPid":%s,"group":%s,"startTicks":%s}\n' \
  "$dn11_nonce" "$$" "$dn11_parent" "$dn11_group" "$dn11_ticks" >&2
case "$dn11_mode" in
  help) exec cilium-dbg config --help;;
  read) exec cilium-dbg config --read-only -o json;;
esac
