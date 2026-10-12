#!/bin/bash
# Fixed reviewed Docker controller; no host language interpreter or eval.
set -eu
set -o noclobber
umask 077
ulimit -c 0
[ "$#" = 5 ] || [ "$#" = 6 ] || exit 1
trial_run_mode=main
if [ "$#" = 6 ]; then [ "$6" = --clock-only ] || exit 1; trial_run_mode=clock; fi
trial_source=$1; trial_kubectl=$2; trial_seed=$3; trial_dir=$4; trial_sha=$5
trial_image=sha256:e1cd49c5e8d36fae0689e727c06459529bbc67e823e08385602ea534c6397450
case "$trial_source:$trial_seed:$trial_dir" in /*:/*:/*) ;; *) exit 1 ;; esac
case "$trial_kubectl" in /*/kubectl) ;; *) exit 1 ;; esac
[[ "$trial_sha" =~ ^[a-f0-9]{40}$ ]] || exit 1
[ "$(git -C "$trial_source" rev-parse HEAD)" = "$trial_sha" ] || exit 1
git -C "$trial_source" diff --quiet
git -C "$trial_source" diff --cached --quiet
[ -f "$trial_seed" ] && [ ! -L "$trial_seed" ] || exit 1
[ -d "$trial_dir" ] && [ ! -L "$trial_dir" ] || exit 1
[ "$(stat -c %a "$trial_dir")" = 700 ] && [ "$(stat -c %u "$trial_dir")" = "$(id -u)" ] || exit 1
[ -z "$(ls -A "$trial_dir")" ] && [ "$(id -u)" = 0 ] || exit 1
trial_helpers=$trial_source/infra/hosting/m3
cp -- "$trial_seed" "$trial_dir/seed.json"
chmod 600 "$trial_dir/seed.json"
trial_clock() {
  local trial_up trial_fraction
  read -r trial_up _ </proc/uptime
  trial_fraction=${trial_up#*.}
  [[ "$trial_fraction" =~ ^[0-9]{2}$ ]] || return 1
  trial_now=$((10#${trial_up%.*}*100+10#$trial_fraction+1))
}
trial_decode() { [ "$1" = - ] || printf '%s' "$1" | base64 -d; }
trial_clock; trial_global_end=$((trial_now+12000))
if [ "$trial_run_mode" = clock ]; then trial_global_end=$((trial_now+1000)); fi
trial_main_end=$trial_global_end; trial_group_end=0; trial_cleaning=false
trial_arm_seen=false; trial_done=false; trial_recovering=false
trial_uid="numeric-controller-$$-$RANDOM"
trial_one() {
  local trial_index=$1 trial_encoded=$2 trial_body=$3 trial_deadline=$4 trial_remaining trial_rc=0 trial_timeout
  local -a trial_argv
  trial_clock; trial_remaining=$((trial_deadline-trial_now))
  [ "$trial_remaining" -gt 100 ] || return 1
  trial_decode "$trial_encoded" >"$trial_dir/rpc-$trial_mode-$trial_seq-$trial_index.argv" || return 1
  mapfile -d '' -t trial_argv <"$trial_dir/rpc-$trial_mode-$trial_seq-$trial_index.argv"
  [ "${#trial_argv[@]}" -gt 0 ] || return 1
  trial_decode "$trial_body" >"$trial_dir/rpc-$trial_mode-$trial_seq-$trial_index.stdin" || return 1
  trial_clock; trial_remaining=$((trial_deadline-trial_now))
  [ "$trial_remaining" -gt 100 ] || return 1
  printf -v trial_timeout '%d.%02ds' "$((trial_remaining/100-1))" "$((trial_remaining%100))"
  (
    ulimit -f 4096
    exec timeout --signal=TERM --kill-after=1s "$trial_timeout" \
      "$trial_kubectl" --request-timeout=8s "${trial_argv[@]}" <"$trial_dir/rpc-$trial_mode-$trial_seq-$trial_index.stdin"
  ) >"$trial_dir/rpc-$trial_mode-$trial_seq-$trial_index.stdout" 2>"$trial_dir/rpc-$trial_mode-$trial_seq-$trial_index.stderr" || trial_rc=$?
  printf '%s\n' "$trial_rc" >"$trial_dir/rpc-$trial_mode-$trial_seq-$trial_index.exit"
}
trial_serve() {
  local trial_mode=$1 trial_seq=0 trial_line trial_deadline trial_count trial_index trial_rc trial_pid trial_op trial_body
  local -a trial_pids trial_fields trial_extra=()
  [ "$trial_mode" != cleanup ] || trial_extra=(--cleanup-only)
  [ "$trial_mode" != clock ] || trial_extra=(--clock-only)
  coproc TRIAL_DOCKER { exec docker run --rm --init -i --pull=never --name "$trial_uid-$trial_mode" --log-driver=none \
    --network none --read-only --tmpfs /tmp:rw,nosuid --cap-drop ALL --security-opt no-new-privileges \
    --user 0:0 --env PYTHONPATH=/checks:/app \
    --mount "type=bind,src=$trial_helpers,dst=/checks,readonly" \
    --mount "type=bind,src=$trial_dir,dst=/evidence,readonly" \
    --entrypoint python "$trial_image" -B /checks/access_numeric_trial.py \
    --seed /evidence/seed.json --evidence /evidence "${trial_extra[@]}" 2>"$trial_dir/controller-$trial_mode.stderr"; }
  local trial_controller_pid=$TRIAL_DOCKER_PID
  local trial_original_in=${TRIAL_DOCKER[1]} trial_original_out=${TRIAL_DOCKER[0]}
  exec {trial_in}>&"$trial_original_in" {trial_out}<&"$trial_original_out"
  exec {trial_original_in}>&- {trial_original_out}<&-
  while true; do
    trial_clock
    local trial_read_end=$trial_main_end trial_wait trial_read_seconds
    [ "$trial_cleaning" = false ] || trial_read_end=$trial_global_end
    [ "$trial_group_end" = 0 ] || [ "$trial_group_end" -ge "$trial_read_end" ] || trial_read_end=$trial_group_end
    trial_wait=$((trial_read_end-trial_now))
    [ "$trial_wait" -gt 0 ] || break
    printf -v trial_read_seconds '%d.%02d' "$((trial_wait/100))" "$((trial_wait%100))"
    IFS= read -r -t "$trial_read_seconds" -u "$trial_out" trial_line || break
    trial_seq=$((trial_seq+1))
    printf '%s\n' "$trial_line" >"$trial_dir/$trial_mode-request-$trial_seq.rpc"
    [ "${#trial_line}" -le 1048576 ] || break
    IFS=$'\t' read -r -a trial_fields <<<"$trial_line"
    [ "${#trial_fields[@]}" -ge 3 ] && [ "${trial_fields[1]}" = "$trial_seq" ] || break
    [[ "${trial_fields[2]}" =~ ^[0-9]{1,12}$ ]] || break
    trial_clock
    [ "$trial_now" -lt "$trial_read_end" ] || [ "${trial_fields[0]}" = cleanup ] || break
    if [ "$trial_mode" = clock ]; then
      case "${trial_fields[0]}" in host|save|done) ;; *) break ;; esac
    fi
    trial_rc=0
    case "${trial_fields[0]}" in
      save)
        [ "${#trial_fields[@]}" = 5 ] || break
        [[ "${trial_fields[3]}" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ ]] || break
        trial_decode "${trial_fields[4]}" >"$trial_dir/${trial_fields[3]}" || break
        ;;
      arm)
        [ "${#trial_fields[@]}" = 3 ] && [ "$trial_mode" = main ] && [ "$trial_arm_seen" = false ] || break
        trial_clock; trial_global_end=$((trial_now+12000)); trial_main_end=$((trial_now+7500)); trial_arm_seen=true
        bash "$trial_helpers/run_numeric_step.sh" arm "$trial_kubectl" - - "$trial_dir" >"$trial_dir/arm.stdout" || break
        ;;
      deadline)
        [ "${#trial_fields[@]}" = 3 ] || break
        trial_deadline=${trial_fields[2]}
        [ "$trial_deadline" -le "$trial_read_end" ] && [ "$trial_deadline" -gt "$trial_now" ] || break
        trial_group_end=$trial_deadline
        ;;
      clear)
        [ "${#trial_fields[@]}" = 3 ] && [ "$trial_group_end" -gt "$trial_now" ] || break
        trial_group_end=0
        ;;
      cleanup)
        [ "${#trial_fields[@]}" = 3 ] && [ "$trial_arm_seen" = true ] || break
        trial_cleaning=true; trial_group_end=0
        ;;
      images)
        [ "${#trial_fields[@]}" = 5 ] || break
        for trial_index in 3 4; do
          [[ "${trial_fields[$trial_index]}" =~ ^docker.io/library/anxious-s13-access-(runner|api)-[a-f0-9]+@sha256:[a-f0-9]{64}$ ]] || break 2
          timeout --kill-after=1s 5s docker image inspect "${trial_fields[$trial_index]}" --format '{{.Id}}' >"$trial_dir/image-$trial_index.txt" || trial_rc=1
          IFS= read -r trial_body <"$trial_dir/image-$trial_index.txt" || trial_rc=1
          [[ "$trial_body" =~ ^sha256:[a-f0-9]{64}$ ]] || trial_rc=1
        done
        ;;
      host)
        [ "${#trial_fields[@]}" = 3 ] || break
        local trial_boot trial_ns
        IFS= read -r trial_boot </proc/sys/kernel/random/boot_id
        trial_ns=$(readlink /proc/self/ns/time)
        [[ "$trial_boot" =~ ^[a-f0-9-]{36}$ ]] && [[ "$trial_ns" =~ ^time:\[[0-9]+\]$ ]] || break
        trial_clock
        printf '{"id":%s,"brokerOk":true,"bootId":"%s","timeNamespace":"%s","nowCs":%s}\n' "$trial_seq" "$trial_boot" "$trial_ns" "$trial_now" >"$trial_dir/$trial_mode-host-response-$trial_seq.json"
        cat "$trial_dir/$trial_mode-host-response-$trial_seq.json" >&"$trial_in"
        continue
        ;;
      batch)
        [ "${#trial_fields[@]}" = 4 ] || break
        trial_deadline=${trial_fields[2]}; trial_count=${trial_fields[3]}
        [[ "$trial_count" =~ ^[0-9]{1,2}$ ]] && [ "$trial_count" -gt 0 ] && [ "$trial_count" -le 64 ] && [ "$trial_deadline" -le "$trial_read_end" ] || break
        trial_pids=()
        for ((trial_index=0;trial_index<trial_count;trial_index++)); do
          IFS=$'\t' read -r -t 1 -u "$trial_out" trial_op trial_body || break 2
          printf '%s\t%s\n' "$trial_op" "$trial_body" >"$trial_dir/$trial_mode-request-$trial_seq-$trial_index.rpc"
          trial_one "$trial_index" "$trial_op" "$trial_body" "$trial_deadline" & trial_pids+=("$!")
        done
        for trial_pid in "${trial_pids[@]}"; do wait "$trial_pid" || trial_rc=1; done
        [ "$trial_rc" = 0 ] || break
        printf '{"id":%s,"brokerOk":true,"results":[' "$trial_seq" >&"$trial_in"
        for ((trial_index=0;trial_index<trial_count;trial_index++)); do
          [ "$trial_index" = 0 ] || printf ',' >&"$trial_in"
          IFS= read -r trial_rc <"$trial_dir/rpc-$trial_mode-$trial_seq-$trial_index.exit"
          [[ "$trial_rc" =~ ^[0-9]{1,3}$ ]] || break 2
          printf '{"exit":%s,"stdout":"rpc-%s-%s-%s.stdout"}' "$trial_rc" "$trial_mode" "$trial_seq" "$trial_index" >&"$trial_in"
        done
        printf ']}\n' >&"$trial_in"
        continue
        ;;
      step)
        [ "${#trial_fields[@]}" = 6 ] || break
        case "${trial_fields[3]}:${trial_fields[5]}" in collector:collector.py|worker:worker.py) ;; *) break ;; esac
        trial_deadline=${trial_fields[2]}; trial_clock
        [ "$trial_deadline" -le "$trial_global_end" ] && [ "$trial_now" -lt "$trial_deadline" ] || break
        if [ "${trial_fields[3]}" = collector ]; then
          printf '%s\n' "$trial_deadline" >"$trial_dir/collector.deadlineCs"
        fi
        bash "$trial_helpers/run_numeric_step.sh" "${trial_fields[3]}" "$trial_kubectl" "${trial_fields[4]}" "$trial_dir/${trial_fields[5]}" "$trial_dir" >"$trial_dir/${trial_fields[3]}.summary" || trial_rc=$?
        printf '{"id":%s,"brokerOk":true,"exit":%s}\n' "$trial_seq" "$trial_rc" >&"$trial_in"
        continue
        ;;
      done)
        [ "${#trial_fields[@]}" = 4 ] || break
        trial_decode "${trial_fields[3]}" >"$trial_dir/broker-$trial_mode-outcome.json" || break
        trial_done=true
        printf '{"id":%s,"brokerOk":true}\n' "$trial_seq" >&"$trial_in"
        break
        ;;
      *) break ;;
    esac
    local trial_ok=false
    [ "$trial_rc" != 0 ] || trial_ok=true
    printf '{"id":%s,"brokerOk":%s}\n' "$trial_seq" "$trial_ok" >&"$trial_in"
  done
  exec {trial_in}>&- {trial_out}<&-
  timeout --kill-after=1s 3s docker stop --time 1 "$trial_uid-$trial_mode" >"$trial_dir/controller-$trial_mode.stop" 2>&1 || true
  kill -KILL "$trial_controller_pid" 2>/dev/null || true
  wait "$trial_controller_pid" 2>/dev/null || true
  [ "$trial_done" = true ]
}
trial_recovery() {
  trap - EXIT TERM INT
  if [ "$trial_arm_seen" = true ] && [ "$trial_done" = false ] && [ "$trial_recovering" = false ]; then
    trial_recovering=true
    trial_cleaning=true; trial_group_end=0
    trial_serve cleanup || printf '{"cleanup":"unknown_handoff","evidenceRetained":true}\n' >"$trial_dir/broker-handoff.json"
  fi
}
trap trial_recovery EXIT
trap 'trial_recovery; exit 1' TERM INT
trial_serve "$trial_run_mode"
trap - EXIT TERM INT
