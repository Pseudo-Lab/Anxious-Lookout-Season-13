#!/bin/bash
# PM-only, one operation directory and one collector/worker claim each.
set -eu
set -o noclobber
umask 077
ulimit -c 0
[ "$#" = 5 ] || exit 1
numeric_mode=$1
numeric_kubectl=$2
numeric_pod=$3
numeric_program=$4
numeric_dir=$5
case "$numeric_kubectl" in /*/kubectl) ;; *) exit 1 ;; esac
case "$numeric_dir" in /*) ;; *) exit 1 ;; esac
[ -d "$numeric_dir" ] && [ ! -L "$numeric_dir" ] || exit 1
[ "$(stat -c %a "$numeric_dir")" = 700 ] && [ "$(stat -c %u "$numeric_dir")" = "$(id -u)" ] || exit 1
numeric_clock() {
  read -r numeric_uptime _ </proc/uptime
  numeric_whole=${numeric_uptime%.*}; numeric_fraction=${numeric_uptime#*.}
  [[ "$numeric_whole" =~ ^[0-9]+$ && "$numeric_fraction" =~ ^[0-9]{2}$ ]] || return 1
  numeric_now=$((10#$numeric_whole*100+10#$numeric_fraction+1))
}
numeric_clock
if [ "$numeric_mode" = arm ]; then
  printf '%s\n' "$numeric_now" >"$numeric_dir/operation.startCs"
  exit 0
fi
[ -f "$numeric_dir/operation.startCs" ] && [ ! -L "$numeric_dir/operation.startCs" ] || exit 1
read -r numeric_start <"$numeric_dir/operation.startCs"
[[ "$numeric_start" =~ ^[0-9]+$ ]] || exit 1
# At least45s of the120s operation is reserved for cleanup. This budget gate
# must also be called before each create through mode budget.
[ "$numeric_now" -ge "$numeric_start" ] && [ "$numeric_now" -lt "$((numeric_start+7500))" ] || exit 1
[ "$numeric_mode" != budget ] || exit 0
case "$numeric_mode" in
  collector)
    [[ "$numeric_pod" =~ ^api-[a-z0-9-]+$ ]] || exit 1
    numeric_ns=m2-hosting; numeric_container=api; numeric_seconds=5s
    ;;
  worker)
    [[ "$numeric_pod" =~ ^access-runner-numeric-tls-v1-[a-z0-9-]+$ ]] || exit 1
    numeric_ns=codex-trial; numeric_container=diagnose; numeric_seconds=12s
    ;;
  *) exit 1 ;;
esac
[ -f "$numeric_program" ] && [ ! -L "$numeric_program" ] || exit 1
[ "$(stat -c %a "$numeric_program")" = 600 ] && [ "$(stat -c %u "$numeric_program")" = "$(id -u)" ] || exit 1
[ "$(stat -c %s "$numeric_program")" -le 65536 ] || exit 1
numeric_body=$(cat "$numeric_program")
[ -n "$numeric_body" ] || exit 1
: >"$numeric_dir/$numeric_mode.claim"
numeric_exit=0
(
  ulimit -f 128
  exec timeout --signal=TERM --kill-after=1s "$numeric_seconds" "$numeric_kubectl" --request-timeout=8s \
    -n "$numeric_ns" exec "$numeric_pod" -c "$numeric_container" -- python -B -c "$numeric_body"
) >"$numeric_dir/$numeric_mode.stdout" 2>"$numeric_dir/$numeric_mode.stderr" || numeric_exit=$?
printf '%s\n' "$numeric_exit" >"$numeric_dir/$numeric_mode.exit"
printf '{"transportExit":%s,"attemptConsumed":true}\n' "$numeric_exit"
exit "$numeric_exit"
