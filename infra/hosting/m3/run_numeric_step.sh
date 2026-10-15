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
# Admission reserves the operation bound, not merely its starting instant.
# worker also reserves32s for postexec owner/native/result checks (13+13+6).
case "$numeric_mode" in
  collector) numeric_reserve=600 ;;
  worker) numeric_reserve=4500 ;;
  budget|budget-create|budget-read) numeric_reserve=1300 ;;
  budget-prepare|budget-result) numeric_reserve=600 ;;
  *) exit 1 ;;
esac
numeric_admit() {
  numeric_clock
  [ "$numeric_now" -ge "$numeric_start" ] && [ "$((numeric_now+numeric_reserve))" -le "$((numeric_start+7500))" ]
}
numeric_admit || exit 1
case "$numeric_mode" in budget*) exit 0 ;; esac
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
numeric_admit || exit 1
numeric_collector_bound() {
  [ "$numeric_mode" = collector ] || return 0
  if [ ! -e "$numeric_dir/collector.deadlineCs" ] && [ ! -L "$numeric_dir/collector.deadlineCs" ]; then return 0; fi
  local numeric_deadline numeric_remaining
  [ -f "$numeric_dir/collector.deadlineCs" ] && [ ! -L "$numeric_dir/collector.deadlineCs" ] || return 1
  [ "$(stat -c %a "$numeric_dir/collector.deadlineCs")" = 600 ] && [ "$(stat -c %u "$numeric_dir/collector.deadlineCs")" = "$(id -u)" ] || return 1
  read -r numeric_deadline <"$numeric_dir/collector.deadlineCs"
  [[ "$numeric_deadline" =~ ^[0-9]{1,12}$ ]] || return 1
  numeric_clock; numeric_remaining=$((numeric_deadline-numeric_now-100))
  [ "$numeric_remaining" -gt 0 ] || return 1
  [ "$numeric_remaining" -le 500 ] || numeric_remaining=500
  printf -v numeric_seconds '%d.%02ds' "$((numeric_remaining/100))" "$((numeric_remaining%100))"
}
numeric_collector_bound || exit 1
numeric_dispatch_admit() {
  numeric_clock
  [ "$numeric_now" -lt "$numeric_dispatch_deadline" ] && [ "$((numeric_dispatch_deadline-numeric_now))" -le 100 ]
}
if [ "$numeric_mode" = worker ]; then
  # Saved once by the boot/time-namespace validated timing command; 1s is
  # transport admission, not completion of the synchronous remote batch.
  numeric_dispatch_file=$numeric_dir/dispatch.deadlineCs
  [ -f "$numeric_dispatch_file" ] && [ ! -L "$numeric_dispatch_file" ] || exit 1
  [ "$(stat -c %a "$numeric_dispatch_file")" = 600 ] && [ "$(stat -c %u "$numeric_dispatch_file")" = "$(id -u)" ] || exit 1
  read -r numeric_dispatch_deadline <"$numeric_dispatch_file"
  [[ "$numeric_dispatch_deadline" =~ ^[0-9]{1,12}$ ]] || exit 1
  numeric_dispatch_admit || exit 1
fi
: >"$numeric_dir/$numeric_mode.claim"
numeric_exit=0
(
  ulimit -f 128
  numeric_collector_bound || exit 1
  if [ "$numeric_mode" = worker ]; then
    numeric_admit && numeric_dispatch_admit || exit 1
    printf '{"dispatchAdmittedCs":%s,"dispatchDeadlineCs":%s}\n' "$numeric_now" "$numeric_dispatch_deadline" >"$numeric_dir/worker.dispatch.json"
  fi
  exec timeout --signal=TERM --kill-after=1s "$numeric_seconds" "$numeric_kubectl" --request-timeout=8s \
    -n "$numeric_ns" exec "$numeric_pod" -c "$numeric_container" -- python -B -c "$numeric_body"
) >"$numeric_dir/$numeric_mode.stdout" 2>"$numeric_dir/$numeric_mode.stderr" || numeric_exit=$?
printf '%s\n' "$numeric_exit" >"$numeric_dir/$numeric_mode.exit"
printf '{"transportExit":%s,"attemptConsumed":true}\n' "$numeric_exit"
exit "$numeric_exit"
