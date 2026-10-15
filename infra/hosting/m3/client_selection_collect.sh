#!/bin/bash
# Internal worker: only run through the reviewed noninteractive POSIX launcher.
set +x +v
set -eu -o pipefail -o noclobber
umask 077
ulimit -c 0
[ "$#" = 6 ] || exit 1
selection_source=$1; selection_docker=$2; selection_dir=$3; selection_sha=$4
selection_start=$5; selection_unsafe=$6
[[ "$selection_start" =~ ^[0-9]{1,12}$ && "$selection_sha" =~ ^[a-f0-9]{40}$ ]] || exit 1
case "$selection_source:$selection_dir:$selection_docker" in /*:/*:/*/docker) ;; *) exit 1 ;; esac
case $selection_unsafe in true|false) ;; *) exit 1 ;; esac
selection_clock() {
  local selection_up selection_fraction
  IFS=' ' read -r selection_up _ </proc/uptime
  selection_fraction=${selection_up#*.}
  [[ "$selection_fraction" =~ ^[0-9]{2}$ ]] || return 1
  selection_now=$((10#${selection_up%.*}*100+10#$selection_fraction+1))
}
selection_end=$((selection_start+300))
selection_clock; [ "$selection_now" -lt "$selection_end" ] || exit 1
[ "$(id -u)" = 0 ] || exit 1
[ -d "$selection_dir" ] && [ ! -L "$selection_dir" ] || exit 1
[ "$(stat -c %a "$selection_dir")" = 700 ] && [ "$(stat -c %u "$selection_dir")" = "$(id -u)" ] || exit 1
[ -z "$(ls -A "$selection_dir")" ] || exit 1
[ -f "$selection_docker" ] && [ -x "$selection_docker" ] || exit 1
[ "$(git -C "$selection_source" rev-parse HEAD)" = "$selection_sha" ] || exit 1
git -C "$selection_source" diff --quiet
git -C "$selection_source" diff --cached --quiet
selection_clock; [ "$selection_now" -lt "$selection_end" ] || exit 1
printf '{"startCs":%s,"deadlineCs":%s}\n' "$selection_start" "$selection_end" >"$selection_dir/budget.json"
selection_status=unknown; selection_reason=unsafe_launch
selection_kind=unknown; selection_transport=unknown
selection_context_nonempty=false; selection_host_nonempty=false
selection_child=''
selection_abort_child() {
  if [ -n "$selection_child" ]; then
    # timeout owns the bounded child group; wait for its fixed kill grace.
    wait "$selection_child" 2>/dev/null || true
    selection_child=''
  fi
}
trap selection_abort_child EXIT
trap 'selection_abort_child; exit 1' TERM INT
selection_read() {
  local selection_cap=$1 selection_left selection_duration selection_read_duration
  local selection_input selection_output selection_pid selection_read_code=0 selection_producer_code=0
  shift
  selection_bytes=''
  selection_clock; selection_left=$((selection_end-selection_now-25))
  [ "$selection_left" -le 75 ] || selection_left=75
  if [ "$selection_left" -le 0 ]; then selection_reason=deadline_exhausted; return 1; fi
  printf -v selection_duration '%d.%02ds' "$((selection_left/100))" "$((selection_left%100))"
  printf -v selection_read_duration '%d.%02d' "$(((selection_left+25)/100))" "$(((selection_left+25)%100))"
  # Gate startup until PID/FDs are captured, avoiding fast-producer coproc races.
  coproc SELECTION_READ {
    IFS= read -r selection_gate || exit 1
    exec timeout --signal=TERM --kill-after=0.25s "$selection_duration" "$@" 2>/dev/null
  }
  selection_pid=$SELECTION_READ_PID; selection_child=$selection_pid
  local selection_original_in=${SELECTION_READ[1]} selection_original_out=${SELECTION_READ[0]}
  exec {selection_input}>&"$selection_original_in" {selection_output}<&"$selection_original_out"
  exec {selection_original_in}>&- {selection_original_out}<&-
  printf 'start\n' >&"$selection_input"
  exec {selection_input}>&-
  # One extra byte detects overflow; NUL terminates early and is refused.
  # EOF returns1 and preserves every LF; no command substitution of raw values.
  LC_ALL=C IFS= read -r -d '' -n "$((selection_cap+1))" -t "$selection_read_duration" \
    -u "$selection_output" selection_bytes || selection_read_code=$?
  exec {selection_output}<&-
  wait "$selection_pid" 2>/dev/null || selection_producer_code=$?
  selection_child=''
  selection_clock
  if [ "$selection_now" -ge "$selection_end" ]; then selection_reason=deadline_exhausted; return 1; fi
  if [ "$selection_producer_code" != 0 ]; then selection_reason=producer_failed_or_timeout; return 1; fi
  if [ "$selection_read_code" != 1 ]; then selection_reason=invalid_or_overflow_frame; return 1; fi
  if [[ "$selection_bytes" != *$'\n' ]]; then selection_reason=missing_terminal_lf; return 1; fi
  selection_bytes=${selection_bytes%$'\n'}
  [[ "$selection_bytes" != *$'\n'* && "$selection_bytes" != *$'\r'* ]] || { selection_reason=multiple_or_invalid_lines; return 1; }
}
selection_classify_host() {
  local selection_endpoint=$1
  selection_transport=unknown
  case $selection_endpoint in
    *$'\n'*|*$'\r'*) return ;;
    unix://?*) selection_transport=unix ;;
    tcp://?*) selection_transport=tcp ;;
    ssh://?*) selection_transport=ssh ;;
    npipe://?*) selection_transport=npipe ;;
  esac
}
selection_valid_name() {
  local LC_ALL=C
  [[ "$1" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$ ]]
}
selection_tls_environment=false
# Default-context initialization can load TLS files; discard presence-check
# values and refuse before any Docker CLI command.
if /usr/bin/printenv DOCKER_TLS >/dev/null 2>&1 || /usr/bin/printenv DOCKER_TLS_VERIFY >/dev/null 2>&1 || /usr/bin/printenv DOCKER_CERT_PATH >/dev/null 2>&1; then
  selection_tls_environment=true
fi
if [ "$selection_unsafe" = false ] && [ "$selection_tls_environment" = false ]; then
  [ -z "${DOCKER_CONTEXT-}" ] || selection_context_nonempty=true
  [ -z "${DOCKER_HOST-}" ] || selection_host_nonempty=true
  if [ "$selection_context_nonempty" = true ]; then selection_kind=environment_context
  elif [ "$selection_host_nonempty" = true ]; then selection_kind=environment_host
  else selection_kind=current_context; fi
  if [ "$selection_kind" = environment_host ]; then
    selection_classify_host "$DOCKER_HOST"
    selection_status=collected; selection_reason=none
  else
    if selection_read 129 "$selection_docker" context show; then
      selection_name=$selection_bytes
      if [ "$selection_context_nonempty" = true ] && [ "$selection_name" != "$DOCKER_CONTEXT" ]; then
        selection_kind=unknown; selection_reason=selected_context_disagreement
      elif [ "$selection_context_nonempty" = true ] && [ "$selection_host_nonempty" = true ] && [ "$selection_name" = default ]; then
        selection_kind=unknown; selection_reason=ambiguous_default_context
      elif selection_valid_name "$selection_name"; then
        selection_template='{{$h := .Endpoints.docker.Host}}{{if and (gt (len $h) 7) (eq (slice $h 0 7) "unix://")}}unix{{else if and (gt (len $h) 6) (eq (slice $h 0 6) "tcp://")}}tcp{{else if and (gt (len $h) 6) (eq (slice $h 0 6) "ssh://")}}ssh{{else if and (gt (len $h) 8) (eq (slice $h 0 8) "npipe://")}}npipe{{else}}unknown{{end}}'
        if selection_read 16 "$selection_docker" context inspect --format "$selection_template" "$selection_name"; then
          case $selection_bytes in unix|tcp|ssh|npipe) selection_transport=$selection_bytes; selection_status=collected; selection_reason=none ;;
            *) selection_reason=unknown_endpoint ;; esac
        fi
      else selection_reason=invalid_context_name; fi
      unset selection_name
    fi
  fi
  if [ "$selection_transport" = unknown ] && [ "$selection_status" = collected ]; then
    selection_status=unknown; selection_reason=unknown_endpoint
  fi
fi
if [ "$selection_unsafe" = false ] && [ "$selection_tls_environment" = true ]; then selection_reason=tls_setup_outside_scope; fi
unset selection_bytes
selection_clock
if [ "$selection_now" -ge "$selection_end" ]; then selection_status=unknown; selection_reason=deadline_exhausted; fi
printf '{"selectionSource":"%s","contextOverrideNonempty":%s,"hostOverrideNonempty":%s,"transport":"%s","status":"%s","toolEnvironmentPlacement":"unproven","daemonNodePlacement":"unproven","correctivePlacementAuthorized":false,"runtimeSelectionAuthorized":false,"controllerCreated":false,"daemonQueries":0,"kubernetesCalls":0,"reason":"%s"}\n' \
  "$selection_kind" "$selection_context_nonempty" "$selection_host_nonempty" "$selection_transport" "$selection_status" "$selection_reason" >"$selection_dir/result.json"
selection_clock
[ "$selection_now" -lt "$selection_end" ] || exit 1
printf '{"completedCs":%s,"deadlineCs":%s}\n' "$selection_now" "$selection_end" >"$selection_dir/complete.json"
selection_clock
if [ "$selection_now" -ge "$selection_end" ]; then rm -f -- "$selection_dir/complete.json"; exit 1; fi
trap - EXIT TERM INT
