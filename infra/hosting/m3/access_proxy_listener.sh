#!/bin/sh
# Fixed infrastructure metadata paths only; stdout never contains rows/ports/FDs.
set -f
unknown() { printf '{"listenerInspection":"unknown"}\n'; exit 1; }
dn10_port=${1-}
case "$dn10_port" in ''|0*|*[!0-9]*) unknown;; esac
[ "$dn10_port" -ge 1 ] && [ "$dn10_port" -le 65535 ] || unknown
dn10_hex=$(printf '%04X' "$dn10_port") || unknown
IFS= read -r dn10_stat < /proc/$$/stat || unknown
dn10_tail=${dn10_stat##*) }
set -- $dn10_tail
[ "$#" -ge 20 ] || unknown
shift
dn10_parent=$1 dn10_group=$2
shift 18
dn10_ticks=$1
[ "$dn10_parent" = "$dn10_group" ] && [ "$dn10_group" -gt 1 ] || unknown
kill -0 "-$dn10_group" 2>/dev/null || unknown
printf '{"listenerChild":{"pid":%s,"parentPid":%s,"group":%s,"startTicks":%s}}\n' \
  "$$" "$dn10_parent" "$dn10_group" "$dn10_ticks" >&2
command -v readlink >/dev/null 2>&1 || unknown
IFS= read -r dn10_comm < /proc/1/comm || unknown
[ "$dn10_comm" = cilium-agent ] || unknown
[ -r /proc/1/fd ] && [ -x /proc/1/fd ] || unknown
dn10_owned=' '
dn10_fd_count=0
# Expand only the selected agent's FD names; do not read any descriptor contents.
set +f
for dn10_fd in /proc/1/fd/[0-9]*; do
  [ -L "$dn10_fd" ] || continue
  dn10_fd_count=$((dn10_fd_count+1))
  [ "$dn10_fd_count" -le 4096 ] || unknown
  dn10_link=$(readlink "$dn10_fd" 2>/dev/null) || {
    [ ! -L "$dn10_fd" ] && continue
    unknown
  }
  case "$dn10_link" in
    'socket:['*']')
      dn10_inode=${dn10_link#'socket:['}
      dn10_inode=${dn10_inode%']'}
      case "$dn10_inode" in ''|*[!0-9]*) unknown;; esac
      dn10_owned="$dn10_owned$dn10_inode "
      ;;
  esac
done
[ "$dn10_fd_count" -gt 0 ] || unknown
set -f
scan_table() {
  dn10_file=$1 dn10_expected_state=$2
  [ -r "$dn10_file" ] || unknown
  dn10_rows=0 dn10_bound=0 dn10_owner=0 dn10_wildcard=0 dn10_header=false
  while read -r dn10_slot dn10_local dn10_remote dn10_state dn10_queue dn10_timer dn10_retry dn10_uid dn10_timeout dn10_inode dn10_extra; do
    if [ "$dn10_slot" = sl ]; then
      [ "$dn10_local" = local_address ] || unknown
      dn10_header=true
      continue
    fi
    [ "$dn10_header" = true ] || unknown
    dn10_rows=$((dn10_rows+1))
    [ "$dn10_rows" -le 4096 ] || unknown
    [ "${dn10_local##*:}" = "$dn10_hex" ] || continue
    [ "$dn10_state" = "$dn10_expected_state" ] || continue
    [ "${dn10_remote##*:}" = 0000 ] || continue
    case "$dn10_inode" in ''|*[!0-9]*|0) unknown;; esac
    dn10_bound=$((dn10_bound+1))
    case "$dn10_owned" in *" $dn10_inode "*) dn10_owner=$((dn10_owner+1));; esac
    dn10_address=${dn10_local%:*}
    case "$dn10_address" in ''|*[!0]*) ;; *) dn10_wildcard=$((dn10_wildcard+1));; esac
  done < "$dn10_file"
  [ "$dn10_header" = true ] || unknown
  printf '{"selectedBoundSockets":%s,"ownedByAgent":%s,"wildcardBinds":%s}' "$dn10_bound" "$dn10_owner" "$dn10_wildcard"
}
# Buffer only bounded result objects; never emit partial data or namespace rows.
dn10_tcp4=$(scan_table /proc/net/tcp 0A) || unknown
dn10_udp4=$(scan_table /proc/net/udp 07) || unknown
dn10_tcp6=$(scan_table /proc/net/tcp6 0A) || unknown
dn10_udp6=$(scan_table /proc/net/udp6 07) || unknown
printf '{"agentProcessMatched":true,"tcp4":%s,"udp4":%s,"tcp6":%s,"udp6":%s,"dnsCause":"unknown"}\n' \
  "$dn10_tcp4" "$dn10_udp4" "$dn10_tcp6" "$dn10_udp6"
