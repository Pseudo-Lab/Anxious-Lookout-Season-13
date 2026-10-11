#!/bin/sh
# PM streams this reviewed script to the selected agent; no remote files/listeners.
set -f
ulimit -c 0
dn6_mode=${1-}
case "$dn6_mode" in
  probe|observe) ;;
  check)
    shift
    [ "$#" -eq 4 ] || exit 2
    for dn6_number in "$@"; do
      case "$dn6_number" in ''|*[!0-9]*|0) exit 2;; esac
    done
    dn6_pid=$1 dn6_parent=$2 dn6_group=$3 dn6_ticks=$4
    [ "$dn6_pid" -gt 1 ] && [ "$dn6_parent" -gt 1 ] && [ "$dn6_group" -gt 1 ] || exit 2
    [ "$dn6_parent" = "$dn6_group" ] || exit 2
    dn6_child_gone=true
    if [ -r "/proc/$dn6_pid/stat" ]; then
      IFS= read -r dn6_stat < "/proc/$dn6_pid/stat" || exit 2
      dn6_tail=${dn6_stat##*) }
      set -- $dn6_tail
      [ "$#" -ge 20 ] || exit 2
      shift 19
      [ "$1" != "$dn6_ticks" ] || dn6_child_gone=false
    fi
    dn6_group_gone=true
    if kill -0 "-$dn6_group" 2>/dev/null; then dn6_group_gone=false; fi
    printf '{"pid":%s,"parentPid":%s,"group":%s,"startTicks":%s,"childGone":%s,"groupGone":%s}\n' \
      "$dn6_pid" "$dn6_parent" "$dn6_group" "$dn6_ticks" "$dn6_child_gone" "$dn6_group_gone"
    [ "$dn6_child_gone" = true ] && [ "$dn6_group_gone" = true ]
    exit $?
    ;;
  *) exit 2;;
esac
dn6_id=${2-0}
case "$dn6_id" in ''|*[!0-9]*) exit 2;; esac
if [ "$dn6_mode" = observe ]; then
  [ "$dn6_id" -ge 1 ] && [ "$dn6_id" -le 65535 ] || exit 2
else
  [ "$dn6_id" = 0 ] || exit 2
fi
dn6_duration=6s
[ "$dn6_mode" != probe ] || dn6_duration=1s
dn6_child='
set -f
IFS= read -r dn6_stat < /proc/$$/stat || exit 2
dn6_tail=${dn6_stat##*) }
set -- "$1" "$2" $dn6_tail
dn6_mode=$1 dn6_id=$2
shift 3
dn6_parent=$1 dn6_group=$2
shift 18
dn6_ticks=$1
[ "$dn6_parent" = "$dn6_group" ] || exit 2
[ "$$" -gt 1 ] && [ "$dn6_group" -gt 1 ] || exit 2
kill -0 "-$dn6_group" 2>/dev/null || exit 2
printf "{\"dn6Control\":\"start\",\"mode\":\"%s\",\"pid\":%s,\"parentPid\":%s,\"group\":%s,\"startTicks\":%s}\n" "$dn6_mode" "$$" "$dn6_parent" "$dn6_group" "$dn6_ticks"
if [ "$dn6_mode" = probe ]; then
  trap '\''kill -TERM "$dn6_probe_child" 2>/dev/null; wait "$dn6_probe_child" 2>/dev/null; exit 0'\'' INT
  sleep 30 &
  dn6_probe_child=$!
  wait
else
  exec cilium-dbg monitor --type drop --related-to "$dn6_id" --json --numeric
fi
'
# No --foreground: the timeout must own a separate group, tested by probe first.
timeout --signal=INT --kill-after=1s "$dn6_duration" sh -c "$dn6_child" sh "$dn6_mode" "$dn6_id"
dn6_exit=$?
printf '{"dn6Control":"end","mode":"%s","timeoutExit":%s}\n' "$dn6_mode" "$dn6_exit"
[ "$dn6_exit" -eq 124 ] || [ "$dn6_exit" -eq 137 ]
