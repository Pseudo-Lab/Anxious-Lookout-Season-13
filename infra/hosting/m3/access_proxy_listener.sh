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
decimal() { case "$1" in ''|*[!0-9]*) return 1;; esac; [ "${#1}" -le 20 ]; }
hexword() { [ "${#1}" -eq "$2" ] || return 1; case "$1" in *[!0-9A-Fa-f]*) return 1;; esac; }
hexpair() {
  hexword "${1%:*}" "$2" && hexword "${1#*:}" "$3"
}
timerword() {
  hexword "${1%:*}" 2 || return 1
  dn10_timer_tail=${1#*:}
  [ "${#dn10_timer_tail}" -ge 8 ] && [ "${#dn10_timer_tail}" -le 16 ] || return 1
  case "$dn10_timer_tail" in *[!0-9A-Fa-f]*) return 1;; esac
}
pointerword() { hexword "$1" 8 || hexword "$1" 16; }
scan_table() {
  dn10_file=$1 dn10_expected_state=$2
  case "$dn10_file" in *6) dn10_width=32 dn10_remote_column=remote_address;; *) dn10_width=8 dn10_remote_column=rem_address;; esac
  dn10_columns="sl local_address $dn10_remote_column st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode"
  [ "$dn10_expected_state" = 07 ] && dn10_columns="$dn10_columns ref pointer drops"
  [ -r "$dn10_file" ] || unknown
  dn10_rows=0 dn10_bound=0 dn10_owner=0 dn10_wildcard=0 dn10_header=false
  while :; do
    dn10_line=
    if ! IFS= read -r dn10_line; then
      # A partial final line is not a complete socket table.
      [ -z "$dn10_line" ] || unknown
      break
    fi
    [ "${#dn10_line}" -le 4096 ] || unknown
    set -- $dn10_line
    [ "$#" -gt 0 ] || unknown
    if [ "$dn10_header" = false ]; then
      [ "$*" = "$dn10_columns" ] || unknown
      dn10_header=true
      continue
    fi
    dn10_rows=$((dn10_rows+1))
    [ "$dn10_rows" -le 4096 ] || unknown
    # Validate every row before filtering by port, including unrelated sockets.
    [ "$#" -ge 12 ] || unknown
    dn10_slot=$1 dn10_local=$2 dn10_remote=$3 dn10_state=$4 dn10_inode=${10}
    case "$dn10_slot" in *:) decimal "${dn10_slot%:}" || unknown;; *) unknown;; esac
    hexpair "$dn10_local" "$dn10_width" 4 && hexpair "$dn10_remote" "$dn10_width" 4 || unknown
    case "$dn10_state" in 01|02|03|04|05|06|07|08|09|0A|0B|0C) ;; *) unknown;; esac
    hexpair "$5" 8 8 && timerword "$6" && hexword "$7" 8 || unknown
    decimal "$8" && decimal "$9" && decimal "${10}" && decimal "${11}" && pointerword "${12}" || unknown
    if [ "$dn10_expected_state" = 07 ]; then
      [ "$#" -eq 13 ] || unknown
      case "$dn10_state" in 01|07) ;; *) unknown;; esac
      decimal "${13}" || unknown
    elif [ "$#" -eq 12 ]; then
      # Kernel TIME_WAIT/open-request rows omit the five TCP tail fields.
      case "$dn10_state:$dn10_inode" in 06:0|03:0) ;; *) unknown;; esac
    else
      [ "$#" -eq 17 ] || unknown
      decimal "${13}" && decimal "${14}" && decimal "${15}" && decimal "${16}" || unknown
      [ "${17}" = -1 ] || decimal "${17}" || unknown
    fi
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
