#!/usr/bin/env bash
# PM-only after exact image/procedure review; never run against arbitrary paths.
set -euo pipefail
[[ $# == 2 && ( $1 == initialize || $1 == verify ) ]] || exit 2
mode=$1
image=$2
[[ $image =~ ^sha256:[a-f0-9]{64}$ || $image =~ ^[a-zA-Z0-9./:_-]+@sha256:[a-f0-9]{64}$ ]] || exit 2
[[ $(hostname -s) == anxious-lookout ]]
[[ $(getenforce) == Enforcing ]]
[[ $(docker image inspect --format '{{.Os}}/{{.Architecture}} {{.Config.User}}' "$image") == 'linux/arm64 10001:10001' ]]
root=/var/lib/anxious-lookout/runner-rehearsal/a9617ace-fa4b-4bbc-b70b-2188f0efe3e6
for leaf in "$root/native" "$root/control"; do
  [[ $(readlink -e "$leaf") == "$leaf" ]]
  [[ $(stat -c '%u:%g:%a' "$leaf") == '10001:10001:700' ]]
  [[ $(stat -c '%C' "$leaf") == *:container_file_t:* ]]
  matchpathcon -V "$leaf"
done
docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
  --user 10001:10001 --tmpfs /tmp:rw,nosuid,nodev \
  --mount "type=bind,src=$root/native,dst=/state" \
  --mount "type=bind,src=$root/control,dst=/control" \
  --entrypoint python "$image" -B -m ops.runner_rehearsal_init "--$mode"
