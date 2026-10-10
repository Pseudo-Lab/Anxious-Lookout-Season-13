#!/usr/bin/env bash
# Candidate-only checks; no original HOME, auth, socket, cluster or listener port.
set -euo pipefail
CANDIDATE_DIR=$(cd "$(dirname "$0")" && pwd)
CANDIDATE_BACKEND=$(cd "$CANDIDATE_DIR/../../../backend" && pwd)
CANDIDATE_ASSETS=${1:?Directory with verified candidate-image-id}
CANDIDATE_MODELS=${2:?Pinned public models-manager/models.json}
CANDIDATE_IMAGE=$(cat "$CANDIDATE_ASSETS/candidate-image-id")
[[ $CANDIDATE_IMAGE =~ ^sha256:[a-f0-9]{64}$ ]]
[[ $CANDIDATE_IMAGE != sha256:6fc02fa74a1b61b51301d57b3a54a263744e020dc1052d5070ada73bfdc33f87 ]]
[[ $(docker image inspect "$CANDIDATE_IMAGE" --format '{{index .Config.Labels "issue7.fixture-only"}}') == true ]]
[[ $(docker image inspect "$CANDIDATE_IMAGE" --format '{{index .Config.Labels "issue7.base-image"}}') == sha256:6fc02fa74a1b61b51301d57b3a54a263744e020dc1052d5070ada73bfdc33f87 ]]
[[ -f $CANDIDATE_MODELS && ! -L $CANDIDATE_MODELS ]]
[[ $(sha256sum "$CANDIDATE_MODELS" | cut -d' ' -f1) == fd219bd9f061278275f528939f82f54d2eb97df4b25c23b022adbe48813d920b ]]
for CANDIDATE_TRANSPORT in stdio unix; do
  CANDIDATE_ARGS=()
  if [[ $CANDIDATE_TRANSPORT == stdio ]]; then CANDIDATE_ARGS=(--stdio); fi
  docker run --rm --pull=never --network none --read-only --tmpfs /tmp \
    --cap-drop ALL --security-opt no-new-privileges:true --user 10001:10001 \
    --mount "type=bind,src=$CANDIDATE_DIR,dst=/checks,readonly" \
    --mount "type=bind,src=$CANDIDATE_BACKEND,dst=/source,readonly" \
    --mount "type=bind,src=$CANDIDATE_MODELS,dst=/fixture-models.json,readonly" \
    --env PYTHONPATH=/source --entrypoint python "$CANDIDATE_IMAGE" \
    -B /checks/stock_tools_probe.py --legacy-history --legacy-tools --code-mode-wire --enable-code-mode-host --permission-probe "${CANDIDATE_ARGS[@]}"
done
