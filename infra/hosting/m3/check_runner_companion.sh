#!/usr/bin/env bash
# Actual runner source/HTTP path; synthetic private grant/cache, no original mounts.
set -euo pipefail
RUNNER_CHECK_DIR=$(cd "$(dirname "$0")" && pwd)
RUNNER_CHECK_BACKEND=$(cd "$RUNNER_CHECK_DIR/../../../backend" && pwd)
RUNNER_CHECK_IMAGE=${1:?Source-built runner-test immutable image ID}
RUNNER_CHECK_MODELS=${2:?Pinned public model catalog}
[[ $RUNNER_CHECK_IMAGE =~ ^sha256:[a-f0-9]{64}$ ]]
[[ -f $RUNNER_CHECK_MODELS && ! -L $RUNNER_CHECK_MODELS ]]
[[ $(sha256sum "$RUNNER_CHECK_MODELS" | cut -d' ' -f1) == fd219bd9f061278275f528939f82f54d2eb97df4b25c23b022adbe48813d920b ]]
docker run --rm --pull=never --network none --read-only --tmpfs /tmp \
  --cap-drop ALL --security-opt no-new-privileges:true --user 10001:10001 \
  --mount "type=bind,src=$RUNNER_CHECK_DIR,dst=/checks,readonly" \
  --mount "type=bind,src=$RUNNER_CHECK_BACKEND,dst=/source,readonly" \
  --mount "type=bind,src=$RUNNER_CHECK_MODELS,dst=/fixture-models.json,readonly" \
  --env PYTHONPATH=/source --entrypoint python "$RUNNER_CHECK_IMAGE" \
  -B /checks/stock_tools_probe.py --runner-path --legacy-history --legacy-tools \
  --code-mode-wire --enable-code-mode-host --permission-probe
docker run --rm --pull=never --network none --read-only --tmpfs /tmp \
  --cap-drop ALL --security-opt no-new-privileges:true --user 10001:10001 \
  --mount "type=bind,src=$RUNNER_CHECK_BACKEND,dst=/source,readonly" \
  --env PYTHONPATH=/source --env APP_ENV=test --entrypoint pytest "$RUNNER_CHECK_IMAGE" \
  -q -p no:cacheprovider /source/tests/test_runner_native_bundle.py \
  /source/tests/test_runner_protocol.py /source/tests/test_runner_auth.py
