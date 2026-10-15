#!/usr/bin/env bash
# PM only: reviewed diagnostic image, private mounts, no cluster policy mutation.
set -euo pipefail
[[ $# == 5 ]] || exit 2
action=$1 image=$2 credentials=$3 permit=$4 evidence=$5
[[ $action == bootstrap-db || $action == run || $action == host-probe ]] || exit 2
[[ $image =~ ^sha256:[a-f0-9]{64}$ ]] || exit 2
for directory in "$credentials" "$permit" "$evidence"; do
  [[ $directory == /* && $(readlink -e "$directory") == "$directory" ]]
  [[ $(stat -c '%u:%a' "$directory") == "$(id -u):700" ]]
done
[[ $credentials != "$permit" && $credentials != "$evidence" && $permit != "$evidence" ]]
module=ops.runner_one_shot
args=("$action")
if [[ $action == host-probe ]]; then module=ops.runner_host_probe; args=(); fi
docker run --rm --network host --read-only --cap-drop ALL --security-opt no-new-privileges \
  --user "$(id -u):$(id -g)" --tmpfs /tmp:rw,nosuid,nodev \
  --mount "type=bind,src=$credentials,dst=/private,readonly" \
  --mount "type=bind,src=$permit,dst=/permit" \
  --mount "type=bind,src=$evidence,dst=/evidence" \
  --entrypoint python "$image" -B -m "$module" "${args[@]}"
