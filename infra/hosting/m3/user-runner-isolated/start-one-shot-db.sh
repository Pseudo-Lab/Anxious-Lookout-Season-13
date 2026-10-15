#!/usr/bin/env bash
# PM-only fresh disposable DB; retain this container/data after the observation.
set -euo pipefail
[[ $# == 3 ]] || exit 2
image=$1 data=$2 password_file=$3
[[ $image =~ ^sha256:[a-f0-9]{64}$ && $(id -u) != 0 ]] || exit 2
[[ $data == /* && $(readlink -e "$data") == "$data" ]]
[[ $(stat -c '%u:%a' "$data") == "$(id -u):700" ]]
contents=$(find "$data" -mindepth 1 -maxdepth 1 -print -quit)
[[ -z $contents ]]
[[ $password_file == /* && $(readlink -e "$password_file") == "$password_file" && -f $password_file ]]
[[ $(stat -c '%u:%a:%h' "$password_file") == "$(id -u):600:1" ]]
[[ $(stat -c '%u:%a' "$(dirname "$password_file")") == "$(id -u):700" ]]
docker run -d --name anxious-s13-pm-issue10-db --restart no \
  --read-only --cap-drop ALL --security-opt no-new-privileges \
  --user "$(id -u):$(id -g)" --publish 127.0.0.1:25410:5432 \
  --tmpfs "/tmp:rw,nosuid,nodev,uid=$(id -u),gid=$(id -g),mode=0700" \
  --tmpfs "/var/run/postgresql:rw,nosuid,nodev,uid=$(id -u),gid=$(id -g),mode=0700" \
  --mount "type=bind,src=$data,dst=/var/lib/postgresql" \
  --mount "type=bind,src=$password_file,dst=/private/postgres-password,readonly" \
  --env POSTGRES_PASSWORD_FILE=/private/postgres-password \
  --env POSTGRES_DB=runner_rehearsal_a9617ace "$image"
