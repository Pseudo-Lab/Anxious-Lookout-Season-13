#!/usr/bin/env bash
# PM-only after review. Raw stdout/stderr use separate FIFOs, never regular files.
set -euo pipefail
set +x
umask 077
ulimit -c 0
: "${DN11_DOCKER:?Absolute trusted Docker binary}"
: "${DN11_KUBECTL:?Absolute trusted kubectl binary}"
: "${DN11_IMAGE:?Reviewed immutable local test/API image ID}"
: "${DN11_SOURCE_DIR:?Reviewed absolute infra/hosting/m3 directory}"
: "${DN11_INPUT_DIR:?Private before.json, anchor.json, classification.json; reads add capability.json}"
: "${DN11_OUTPUT_DIR:?Fresh private empty output directory}"
: "${DN11_AGENT:?Exact selected same-node agent Pod}"
: "${DN11_MODE:?capability, dns-rules or proxy-route}"
[[ $DN11_MODE == capability || $DN11_MODE == dns-rules || $DN11_MODE == proxy-route ]]
[[ $DN11_AGENT =~ ^cilium-[a-z0-9-]+$ && $DN11_IMAGE =~ ^sha256:[a-f0-9]{64}$ ]]
for DN11_BINARY in "$DN11_DOCKER" "$DN11_KUBECTL"; do
  [[ $DN11_BINARY == /* && -f $DN11_BINARY && -x $DN11_BINARY ]]
done
for DN11_PATH in "$DN11_SOURCE_DIR" "$DN11_INPUT_DIR" "$DN11_OUTPUT_DIR"; do
  [[ $DN11_PATH == /* && $DN11_PATH != *,* && $DN11_PATH != *$'\n'* && ! -L $DN11_PATH ]]
  [[ $(realpath -e -- "$DN11_PATH") == "$DN11_PATH" ]]
done
[[ $(stat -c '%u|%a' -- "$DN11_INPUT_DIR") == "$(id -u)|700" ]]
[[ $(stat -c '%u|%a' -- "$DN11_OUTPUT_DIR") == "$(id -u)|700" ]]
shopt -s nullglob dotglob
DN11_INPUTS=("$DN11_INPUT_DIR"/*)
DN11_OUTPUTS=("$DN11_OUTPUT_DIR"/*)
[[ ${#DN11_OUTPUTS[@]} == 0 ]]
DN11_CAPABILITY_ARGS=()
[[ -f $DN11_INPUT_DIR/classification.json && ! -L $DN11_INPUT_DIR/classification.json ]]
if [[ $DN11_MODE != capability ]]; then
  [[ ${#DN11_INPUTS[@]} == 4 && -f $DN11_INPUT_DIR/capability.json && ! -L $DN11_INPUT_DIR/capability.json ]]
  DN11_CAPABILITY_ARGS=(--capability /inputs/capability.json)
else
  [[ ${#DN11_INPUTS[@]} == 3 ]]
fi
[[ -f $DN11_INPUT_DIR/before.json && ! -L $DN11_INPUT_DIR/before.json ]]
[[ -f $DN11_INPUT_DIR/anchor.json && ! -L $DN11_INPUT_DIR/anchor.json ]]
DN11_NONCE=$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')
[[ $DN11_NONCE =~ ^[0-9a-f]{32}$ ]]
printf '%s\n' "$DN11_NONCE" > "$DN11_OUTPUT_DIR/nonce"
mkfifo "$DN11_OUTPUT_DIR/stdout" "$DN11_OUTPUT_DIR/stderr"
# Keep FIFO writers open until kubectl finishes, so reader startup cannot see premature EOF.
exec 3<>"$DN11_OUTPUT_DIR/stdout" 4<>"$DN11_OUTPUT_DIR/stderr"
DN11_READER_PID=
dn11_cleanup() {
  exec 3>&- 4>&-
  if [[ -n $DN11_READER_PID ]]; then wait "$DN11_READER_PID" 2>/dev/null || true; fi
  rm -f -- "$DN11_OUTPUT_DIR/stdout" "$DN11_OUTPUT_DIR/stderr"
}
trap dn11_cleanup EXIT
timeout --signal=TERM --kill-after=2s 35s "$DN11_DOCKER" run --rm --pull=never --log-driver=none --network none --read-only --tmpfs /tmp \
  --cap-drop ALL --security-opt no-new-privileges:true --user "$(id -u):$(id -g)" \
  --mount "type=bind,src=$DN11_SOURCE_DIR,dst=/checks,readonly" \
  --mount "type=bind,src=$DN11_INPUT_DIR,dst=/inputs,readonly" \
  --mount "type=bind,src=$DN11_OUTPUT_DIR,dst=/channels" \
  --workdir /checks --entrypoint python "$DN11_IMAGE" -B /checks/access_tproxy_capture.py \
  --channel-dir /channels --mode "$DN11_MODE" --nonce "$DN11_NONCE" \
  --agent-name "$DN11_AGENT" \
  --before /inputs/before.json --anchor /inputs/anchor.json --receipt /channels/receipt.json \
  --classification /inputs/classification.json \
  "${DN11_CAPABILITY_ARGS[@]}" \
  > "$DN11_OUTPUT_DIR/reader.summary.json" 2>/dev/null 3>&- 4>&- &
DN11_READER_PID=$!
DN11_START=$SECONDS
while [[ ! -f $DN11_OUTPUT_DIR/reader.ready ]]; do
  kill -0 "$DN11_READER_PID" 2>/dev/null || exit 1
  (( SECONDS-DN11_START < 20 )) || exit 1
  sleep 0.05
done
set +e
timeout --signal=TERM --kill-after=2s 10s "$DN11_KUBECTL" -n kube-system exec -i "$DN11_AGENT" -c cilium-agent -- \
  timeout --signal=TERM --kill-after=1s 6s sh -s -- "$DN11_MODE" "$DN11_NONCE" \
  < "$DN11_SOURCE_DIR/access_tproxy_remote.sh" > "$DN11_OUTPUT_DIR/stdout" 2> "$DN11_OUTPUT_DIR/stderr" 3>&- 4>&-
DN11_EXIT=$?
set -e
exec 3>&- 4>&-
printf '%s\n' "$DN11_EXIT" > "$DN11_OUTPUT_DIR/transport.exit.tmp"
mv -- "$DN11_OUTPUT_DIR/transport.exit.tmp" "$DN11_OUTPUT_DIR/transport.exit"
wait "$DN11_READER_PID"
DN11_READER_PID=
[[ $DN11_EXIT == 0 ]]
[[ $(cat "$DN11_OUTPUT_DIR/reader.summary.json") == '{"sanitizedReceiptWritten": true, "captureComplete": true}' ]]
printf '{"sanitizedReceiptWritten":true,"requiresProofAndContext":true}\n'
