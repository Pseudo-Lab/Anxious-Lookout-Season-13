#!/bin/bash
# PM-only P1 lifecycle. selfcheck is capability-free and never executes BPF.
set -eu
set -o noclobber
umask 077
ulimit -c 0
[ "$#" = 4 ] || exit 1
np_docker=$1
np_source=$2
np_directory=$3
np_mode=$4
np_image=sha256:e1cd49c5e8d36fae0689e727c06459529bbc67e823e08385602ea534c6397450
case "$np_docker" in /*/docker) ;; *) exit 1 ;; esac
case "$np_source" in /*) ;; *) exit 1 ;; esac
case "$np_directory" in /*) ;; *) exit 1 ;; esac
case "$np_source$np_directory" in *,*) exit 1 ;; esac
[ -d "$np_directory" ] && [ ! -L "$np_directory" ] || exit 1
[ "$(stat -c %a "$np_directory")" = 700 ] && [ "$(stat -c %u "$np_directory")" = "$(id -u)" ] || exit 1
case "$np_mode" in
  probe)
    [ -d "$np_source" ] && [ ! -L "$np_source" ] || exit 1
    [ -f "$np_source/access_node_proxy_probe.py" ] && [ ! -L "$np_source/access_node_proxy_probe.py" ] || exit 1
    [ -f "$np_directory/binding.json" ] && [ ! -L "$np_directory/binding.json" ] || exit 1
    [ "$(stat -c %a "$np_directory/binding.json")" = 600 ] && [ "$(stat -c %u "$np_directory/binding.json")" = "$(id -u)" ] || exit 1
    np_seconds=15s
    ;;
  selfcheck) np_seconds=1s ;;
  *) exit 1 ;;
esac
# A claimed directory may never be replayed. The CID file is created by Docker.
[ ! -e "$np_directory/probe.cid" ] && [ ! -L "$np_directory/probe.cid" ] || exit 1
: >"$np_directory/probe.claim"
np_nonce=$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')
[[ "$np_nonce" =~ ^[a-f0-9]{32}$ ]] || exit 1
np_name=access-node-proxy-$np_nonce
printf '%s\n' "$np_name" >"$np_directory/probe.name"
printf '%s\n' "$np_nonce" >"$np_directory/probe.nonce"
np_cid=''
np_attempted=false
np_success=false
np_child_exit=125

np_call() {
  np_tag=$1
  np_duration=$2
  shift 2
  np_code=0
  (
    ulimit -f 128
    exec timeout --signal=TERM --kill-after=1s "$np_duration" "$np_docker" "$@"
  ) >"$np_directory/$np_tag.stdout" 2>"$np_directory/$np_tag.stderr" || np_code=$?
  printf '%s\n' "$np_code" >"$np_directory/$np_tag.exit"
  return "$np_code"
}

np_identity() {
  np_target=$1
  np_tag=$2
  np_call "$np_tag" 5s inspect --format \
    '{{.Id}}|{{.Name}}|{{index .Config.Labels "io.anxious.issue7.node-proxy-owner"}}|{{.Image}}|{{.State.Running}}|{{.State.ExitCode}}' "$np_target" || return 1
  IFS='|' read -r np_id np_actual_name np_owner np_actual_image np_running np_exit <"$np_directory/$np_tag.stdout" || return 1
  [[ "$np_id" =~ ^[a-f0-9]{64}$ ]] || return 1
  [ "$np_actual_name" = "/$np_name" ] && [ "$np_owner" = "$np_nonce" ] && [ "$np_actual_image" = "$np_image" ] || return 1
  [ -z "$np_cid" ] || [ "$np_cid" = "$np_id" ] || return 1
  [[ "$np_running" = true || "$np_running" = false ]] && [[ "$np_exit" =~ ^[0-9]+$ ]] || return 1
  np_cid=$np_id
}

np_absence() {
  if [ -n "$np_cid" ]; then
    np_call absent-id 5s container ls --all --no-trunc --filter "id=$np_cid" --format '{{.ID}}' || return 1
    [ ! -s "$np_directory/absent-id.stdout" ] || return 1
  fi
  np_call absent-name 5s container ls --all --no-trunc --filter "name=^/$np_name$" --format '{{.ID}}' || return 1
  [ ! -s "$np_directory/absent-name.stdout" ] || return 1
  np_call absent-owner 5s container ls --all --no-trunc --filter "label=io.anxious.issue7.node-proxy-owner=$np_nonce" --format '{{.ID}}' || return 1
  [ ! -s "$np_directory/absent-owner.stdout" ]
}

np_cleanup() {
  if ! np_identity "${np_cid:-$np_name}" cleanup-identity; then
    # Inspect errors are not absence. Successful empty daemon list responses
    # are required even when create timed out before a CID acknowledgment.
    np_absence || return 1
    # An unacknowledged create can still be in flight. Empty lists alone do
    # not prove it cannot complete later; it was never started by this wrapper.
    [ -n "$np_cid" ]
    return $?
  fi
  if [ "$np_running" = true ]; then
    # Exact full ID, never an unverified name, label set, or global prune.
    np_call cleanup-kill 5s kill --signal KILL "$np_cid" || :
    np_identity "$np_cid" cleanup-stopped || return 1
    [ "$np_running" = false ] || return 1
  fi
  np_call cleanup-remove 5s rm "$np_cid" || :
  np_absence
}

np_finish() {
  np_original=$?
  trap - EXIT TERM INT HUP
  np_cleanup_known=false
  if [ "$np_attempted" = true ] && np_cleanup; then np_cleanup_known=true; fi
  np_eligible=false
  if [ "$np_mode" = probe ] && [ "$np_success" = true ] && [ "$np_cleanup_known" = true ]; then np_eligible=true; fi
  np_final=$np_original
  if [ "$np_cleanup_known" != true ]; then np_final=125; fi
  printf '{"mode":"%s","containerId":"%s","containerName":"%s","ownerNonce":"%s","imageId":"%s","childExit":%s,"probeCompleted":%s,"containerAbsentVerified":%s,"p2Eligible":%s}\n' \
    "$np_mode" "$np_cid" "$np_name" "$np_nonce" "$np_image" "$np_child_exit" "$np_success" "$np_cleanup_known" "$np_eligible" >"$np_directory/probe.lifecycle.json"
  if [ "$np_cleanup_known" = true ]; then
    printf '{"probeContainerAbsentVerified":true,"p2Eligible":%s}\n' "$np_eligible"
  else
    printf '{"probeContainerTermination":"unknown","p2Eligible":false,"handoffRequired":true}\n'
  fi
  exit "$np_final"
}

# Create may itself lose transport; name+fresh owner label allow reconciliation.
np_call preexisting 5s container ls --all --no-trunc --filter "name=^/$np_name$" --format '{{.ID}}'
[ ! -s "$np_directory/preexisting.stdout" ] || exit 1
trap np_finish EXIT
trap 'exit 143' TERM HUP
trap 'exit 130' INT
np_attempted=true
np_options=(--name "$np_name" --label "io.anxious.issue7.node-proxy-owner=$np_nonce"
            --cidfile "$np_directory/probe.cid" --restart=no --init --pull=never
            --log-driver=none --network none --read-only --cap-drop ALL
            --security-opt no-new-privileges --no-healthcheck)
if [ "$np_mode" = probe ]; then
  np_call create 10s create "${np_options[@]}" --user 0:0 --cap-add BPF --cap-add NET_ADMIN \
    --security-opt seccomp=unconfined --entrypoint python \
    --mount type=bind,src=/etc/machine-id,dst=/target-machine-id,readonly \
    --mount type=bind,src=/proc/sys/kernel/random/boot_id,dst=/target-boot-id,readonly \
    --mount "type=bind,src=$np_source,dst=/checks,readonly" \
    --mount "type=bind,src=$np_directory,dst=/evidence,readonly" "$np_image" \
    -B /checks/access_node_proxy_probe.py --binding /evidence/binding.json --approved-load-only
else
  np_call create 10s create "${np_options[@]}" --user 10001:10001 --entrypoint sh "$np_image" \
    -c 'trap "" TERM; sleep 60 & wait'
fi
np_identity "$np_name" created
[ -f "$np_directory/probe.cid" ] && [ ! -L "$np_directory/probe.cid" ] || exit 1
[ "$(cat "$np_directory/probe.cid")" = "$np_cid" ] || exit 1
np_start_exit=0
np_call probe "$np_seconds" start --attach "$np_cid" || np_start_exit=$?
np_child_exit=$np_start_exit
np_identity "$np_cid" completed || exit 1
if [ "$np_start_exit" = 0 ] && [ "$np_running" = false ] && [ "$np_exit" = 0 ]; then
  np_success=true
  exit 0
fi
if [ "$np_start_exit" = 0 ]; then np_child_exit=$np_exit; exit 1; fi
exit "$np_start_exit"
