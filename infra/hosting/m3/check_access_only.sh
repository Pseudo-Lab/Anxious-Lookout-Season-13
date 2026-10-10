#!/usr/bin/env bash
# Three synthetic exact native cases. No original auth/client/provider/cluster use.
set -euo pipefail
ACCESS_CHECK_DIR=$(cd "$(dirname "$0")" && pwd)
ACCESS_MODELS=${1:?Exact public pinned native models-manager/models.json}
[[ -f $ACCESS_MODELS && ! -L $ACCESS_MODELS ]]
[[ $(sha256sum "$ACCESS_MODELS" | cut -d' ' -f1) == fd219bd9f061278275f528939f82f54d2eb97df4b25c23b022adbe48813d920b ]]
for ACCESS_CASE in default-retries no-retries expired-token; do
  ACCESS_ARGS=()
  if [[ $ACCESS_CASE == default-retries ]]; then ACCESS_ARGS=(--default-retries); fi
  if [[ $ACCESS_CASE == expired-token ]]; then ACCESS_ARGS=(--expired-token); fi
  docker run --rm --pull=never --network none --read-only --tmpfs /tmp \
    --cap-drop ALL --security-opt no-new-privileges:true --user 10001:10001 \
    --mount "type=bind,src=$ACCESS_CHECK_DIR/access_only_probe.py,dst=/checks/probe.py,readonly" \
    --mount "type=bind,src=$ACCESS_MODELS,dst=/checks/models.json,readonly" \
    --entrypoint python sha256:6fc02fa74a1b61b51301d57b3a54a263744e020dc1052d5070ada73bfdc33f87 \
    -B /checks/probe.py "${ACCESS_ARGS[@]}"
done
