#!/usr/bin/env bash
# Explicit operator-selected access snapshot only; no refresh, secret logging or TTY.
set -euo pipefail
set +x
: "${RUNNER_SOURCE_DIR:?Private source directory owned by the invoking uid}"
: "${RUNNER_SOURCE_FILE:?Basename of the explicitly selected ChatGPT auth file}"
: "${RUNNER_SUPPLY_INPUT_DIR:?Private input directory owned by the invoking uid}"
: "${RUNNER_SUPPLY_INPUT_FILE:?Basename of service-envelope metadata JSON}"
: "${RUNNER_TOOL_IMAGE:?Reviewed API image containing ops.access_snapshot}"
: "${RUNNER_KUBECONFIG:?Operator kubeconfig path}"
: "${RUNNER_NAMESPACE:?Reviewed isolated namespace}"
: "${RUNNER_POD:?Exact prepared Pod name}"
: "${RUNNER_POD_UID:?Observed UID of the bound Pod}"
[[ "$RUNNER_SOURCE_FILE" != */* && "$RUNNER_SUPPLY_INPUT_FILE" != */* ]]
[[ "$RUNNER_TOOL_IMAGE" == *@sha256:* ]]
actual_uid=$(kubectl --kubeconfig "$RUNNER_KUBECONFIG" -n "$RUNNER_NAMESPACE" get pod "$RUNNER_POD" -o 'jsonpath={.metadata.uid}')
[[ "$actual_uid" == "$RUNNER_POD_UID" ]]
# Export stdout is piped directly to exec stdin; only the supply receipt is stdout.
# Envelope owner/state and provider-account binding are checked inside the Pod.
docker run --rm -i --user "$(id -u):$(id -g)" --network none --read-only \
  --cap-drop ALL --security-opt no-new-privileges \
  --mount "type=bind,src=$RUNNER_SOURCE_DIR,dst=/source,readonly" \
  --mount "type=bind,src=$RUNNER_SUPPLY_INPUT_DIR,dst=/inputs,readonly" \
  --entrypoint python "$RUNNER_TOOL_IMAGE" -B -m ops.access_snapshot \
  --source "/source/$RUNNER_SOURCE_FILE" --inputs "/inputs/$RUNNER_SUPPLY_INPUT_FILE" \
  --supply-mode service --export |
  kubectl --kubeconfig "$RUNNER_KUBECONFIG" -n "$RUNNER_NAMESPACE" exec -i "$RUNNER_POD" \
    -- python -B -m runner.access_supply /control/access.sock --expected-pod-uid "$RUNNER_POD_UID"
