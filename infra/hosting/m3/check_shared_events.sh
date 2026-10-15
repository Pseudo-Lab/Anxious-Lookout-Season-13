#!/usr/bin/env bash
# Fixture-only. No original mounts, real credentials, provider or cluster access.
set -euo pipefail
EVENT_CHECK_DIR=$(cd "$(dirname "$0")" && pwd)
EVENT_BACKEND_DIR=$(cd "$EVENT_CHECK_DIR/../../../backend" && pwd)
EVENT_MODELS=${1:?Exact pinned public models-manager/models.json}
shift
[[ -f $EVENT_MODELS && ! -L $EVENT_MODELS ]]
[[ $(sha256sum "$EVENT_MODELS" | cut -d' ' -f1) == fd219bd9f061278275f528939f82f54d2eb97df4b25c23b022adbe48813d920b ]]
for EVENT_PROBE in check_owned_events.py stock_tools_probe.py; do
  EVENT_ARGS=()
  if [[ $EVENT_PROBE == stock_tools_probe.py ]]; then EVENT_ARGS=("$@"); fi
  docker run --rm --pull=never --network none --read-only --tmpfs /tmp \
    --cap-drop ALL --security-opt no-new-privileges:true --user 10001:10001 \
    --mount "type=bind,src=$EVENT_CHECK_DIR,dst=/checks,readonly" \
    --mount "type=bind,src=$EVENT_BACKEND_DIR,dst=/source,readonly" \
    --mount "type=bind,src=$EVENT_MODELS,dst=/fixture-models.json,readonly" \
    --env PYTHONPATH=/source --entrypoint python \
    sha256:6fc02fa74a1b61b51301d57b3a54a263744e020dc1052d5070ada73bfdc33f87 \
    -B "/checks/$EVENT_PROBE" "${EVENT_ARGS[@]}"
done
