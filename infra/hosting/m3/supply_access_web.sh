#!/usr/bin/env bash
# PM-only after review. Exact one-file read -> nonlogged Docker stdout -> TLS exec stdin.
set -euo pipefail
set +x
umask 077
declare -F ACCESS_SUPPLY_DOCKER >/dev/null
declare -F ACCESS_SUPPLY_KUBECTL >/dev/null
: "${ACCESS_SUPPLY_IMAGE:?Reviewed immutable local production API Docker ID}"
: "${ACCESS_SUPPLY_SOURCE:?Explicitly selected canonical file}"
: "${ACCESS_SUPPLY_INPUT_DIR:?Private directory containing only inputs.json}"
: "${ACCESS_SUPPLY_POD:?Exact recorded runner Pod name}"
: "${ACCESS_SUPPLY_POD_UID:?Expected Pod UID from reviewed apply}"
: "${ACCESS_SUPPLY_RUNNER_REF:?Reviewed immutable production runner reference}"
[[ $ACCESS_SUPPLY_IMAGE =~ ^sha256:[a-f0-9]{64}$ ]]
[[ $ACCESS_SUPPLY_POD =~ ^access-web-runner-v1-[a-z0-9-]+$ && $ACCESS_SUPPLY_POD_UID =~ ^[a-f0-9-]{36}$ ]]
# Keep legacy reviewed refs; add only #9's approved/imported exact runner.
[[ $ACCESS_SUPPLY_RUNNER_REF =~ ^docker.io/library/anxious-s13-access-runner-[a-f0-9]+@sha256:[a-f0-9]{64}$ ||
   $ACCESS_SUPPLY_RUNNER_REF == 'docker.io/library/anxious-s13-chatbot-runner-e9f5943@sha256:0158f92ebeeedcf2c237a7ca5847c46ba0c4d4335913af848456071e099bd76c' ]]
for ACCESS_SUPPLY_PATH in "$ACCESS_SUPPLY_SOURCE" "$ACCESS_SUPPLY_INPUT_DIR"; do
  [[ $ACCESS_SUPPLY_PATH == /* && $ACCESS_SUPPLY_PATH != *,* && $ACCESS_SUPPLY_PATH != *$'\n'* && ! -L $ACCESS_SUPPLY_PATH ]]
  [[ $(realpath -e -- "$ACCESS_SUPPLY_PATH") == "$ACCESS_SUPPLY_PATH" ]]
done
[[ -f $ACCESS_SUPPLY_SOURCE && -d $ACCESS_SUPPLY_INPUT_DIR ]]
[[ $(stat -c '%u' -- "$ACCESS_SUPPLY_SOURCE") == "$(id -u)" ]]
[[ $(stat -c '%u|%a' -- "$ACCESS_SUPPLY_INPUT_DIR") == "$(id -u)|700" ]]
shopt -s nullglob dotglob
ACCESS_SUPPLY_ENTRIES=("$ACCESS_SUPPLY_INPUT_DIR"/*)
[[ ${#ACCESS_SUPPLY_ENTRIES[@]} == 1 && ${ACCESS_SUPPLY_ENTRIES[0]} == "$ACCESS_SUPPLY_INPUT_DIR/inputs.json" ]]
ACCESS_SUPPLY_OWNER=$(jq -r .ownerId "$ACCESS_SUPPLY_INPUT_DIR/inputs.json")
ACCESS_SUPPLY_STATE=$(jq -r .stateId "$ACCESS_SUPPLY_INPUT_DIR/inputs.json")
ACCESS_SUPPLY_BEFORE=$(stat -c '%d|%i|%u|%g|%f|%h|%s|%y|%z' -- "$ACCESS_SUPPLY_SOURCE")
ACCESS_SUPPLY_SELECTED=$(ACCESS_SUPPLY_KUBECTL -n codex-trial get pod "$ACCESS_SUPPLY_POD" -o json)
[[ $(printf '%s' "$ACCESS_SUPPLY_SELECTED" | jq -r .metadata.uid) == "$ACCESS_SUPPLY_POD_UID" ]]
printf '%s' "$ACCESS_SUPPLY_SELECTED" | jq -e --arg owner "$ACCESS_SUPPLY_OWNER" --arg state "$ACCESS_SUPPLY_STATE" --arg image "$ACCESS_SUPPLY_RUNNER_REF" '
  .metadata.labels.app == "access-web-runner" and
  (.spec.containers | length) == 1 and .spec.containers[0].name == "runner" and
  .spec.containers[0].image == $image and
  ([.spec.containers[0].env[] | select(.name == "RUNNER_ACCOUNT_ID")][0].value == $owner) and
  ([.spec.containers[0].env[] | select(.name == "RUNNER_STATE_ID")][0].value == $state) and
  .status.phase == "Running"' >/dev/null

# A pipe alone does NOT suppress Docker stdout logging.
ACCESS_SUPPLY_DOCKER run --rm --pull=never --log-driver=none --network none --read-only --tmpfs /tmp \
  --cap-drop ALL --security-opt no-new-privileges:true --user "$(id -u):$(id -g)" \
  --mount "type=bind,src=$ACCESS_SUPPLY_SOURCE,dst=/selected/source.json,readonly" \
  --mount "type=bind,src=$ACCESS_SUPPLY_INPUT_DIR,dst=/operator,readonly" \
  --entrypoint python "$ACCESS_SUPPLY_IMAGE" -B -m ops.access_snapshot \
  --source /selected/source.json --inputs /operator/inputs.json --export | \
ACCESS_SUPPLY_KUBECTL -n codex-trial exec -i "$ACCESS_SUPPLY_POD" -- \
  python -B -m runner.access_supply /control/access-web/access.sock
[[ $(stat -c '%d|%i|%u|%g|%f|%h|%s|%y|%z' -- "$ACCESS_SUPPLY_SOURCE") == "$ACCESS_SUPPLY_BEFORE" ]]
ACCESS_SUPPLY_AFTER=$(ACCESS_SUPPLY_KUBECTL -n codex-trial get pod "$ACCESS_SUPPLY_POD" -o json)
[[ $(printf '%s' "$ACCESS_SUPPLY_AFTER" | jq -r .metadata.uid) == "$ACCESS_SUPPLY_POD_UID" ]]
[[ $(printf '%s' "$ACCESS_SUPPLY_AFTER" | jq -r '.spec.containers[0].image') == "$ACCESS_SUPPLY_RUNNER_REF" ]]
