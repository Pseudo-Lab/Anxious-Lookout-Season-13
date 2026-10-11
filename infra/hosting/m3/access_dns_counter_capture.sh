#!/bin/sh
# One exact numeric endpoint GET with own time-namespace/BOOTTIME bounds only.
set -eu
ulimit -c 0
if [ "$#" != 4 ]; then exit 1; fi
counter_mode=$1
counter_id=$2
counter_namespace=$3
counter_cutoff=$4
case "$counter_mode" in before|after) ;; *) exit 1 ;; esac
case "$counter_id" in ''|0*|*[!0-9]*) exit 1 ;; esac
if [ "$counter_id" -gt 65535 ]; then exit 1; fi
case "$counter_cutoff" in ''|*[!0-9]*) exit 1 ;; esac
counter_time_namespace=$(readlink /proc/self/ns/time)
counter_clock() {
  read -r counter_uptime counter_idle </proc/uptime
  printf '%s\n' "$counter_uptime" | awk '
    /^[0-9]+\.[0-9][0-9]$/ {
      split($0,parts,"."); if (parts[1]>9999999999) exit 1;
      printf "%.0f\n",parts[1]*100+parts[2]+1; valid=1
    }
    END {if (!valid) exit 1}'
}
counter_begin=$(counter_clock)
if [ "$counter_mode" = before ]; then
  if [ "$counter_namespace" != - ] || [ "$counter_cutoff" != 0 ]; then exit 1; fi
else
  if [ "$counter_time_namespace" != "$counter_namespace" ] ||
     ! awk -v current="$counter_begin" -v cutoff="$counter_cutoff" 'BEGIN {exit !(current+500<cutoff)}'; then
    printf '{"counterCapture":"unknown","captureAttempted":false}\n' >&2
    exit 1
  fi
fi
counter_exit=0
timeout --signal=TERM --kill-after=1s 4s cilium-dbg endpoint get "$counter_id" -o json || counter_exit=$?
counter_end=$(counter_clock)
counter_complete=true
if [ "$counter_exit" != 0 ]; then counter_complete=false; fi
if [ "$counter_mode" = after ] &&
   ! awk -v current="$counter_end" -v cutoff="$counter_cutoff" 'BEGIN {exit !(current<cutoff)}'; then counter_complete=false; fi
printf '{"mode":"%s","endpointId":%s,"timeNamespace":"%s","beginUpperCs":%s,"endUpperCs":%s,"childExit":%s,"complete":%s}\n' \
  "$counter_mode" "$counter_id" "$counter_time_namespace" "$counter_begin" "$counter_end" "$counter_exit" "$counter_complete" >&2
if [ "$counter_complete" != true ]; then exit 1; fi
