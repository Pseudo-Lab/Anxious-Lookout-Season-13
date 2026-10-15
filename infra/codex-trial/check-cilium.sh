#!/usr/bin/env bash
# Upstream semantic validation only, no live agent/Kubernetes operations.
set -euo pipefail
CILIUM_CHECK_REPO=$(cd "$(dirname "$0")/../.." && pwd)
CILIUM_CHECK_OUTPUT=${1:?Set a new /tmp/issue7-cilium-* synthetic directory}
CILIUM_CHECK_PYTHON_IMAGE=${2:?Set the existing synthetic Python inspection image}
CILIUM_CHECK_VALIDATOR_IMAGE=${3:-anxious-s13-back-cilium-validator:1.20.2}
[[ "$CILIUM_CHECK_OUTPUT" =~ ^/tmp/issue7-cilium-[A-Za-z0-9_-]+$ ]] || exit 1
mkdir -m 0755 "$CILIUM_CHECK_OUTPUT"  # Refuse any existing/actual-input directory.
docker run --rm --network none --read-only --tmpfs /tmp \
  --mount "type=bind,src=$CILIUM_CHECK_REPO/infra/codex-trial,dst=/checks,readonly" \
  --entrypoint python "$CILIUM_CHECK_PYTHON_IMAGE" /checks/cilium-validator/cases.py \
  > "$CILIUM_CHECK_OUTPUT/cilium-cases.json"
chmod 0644 "$CILIUM_CHECK_OUTPUT/cilium-cases.json"  # Synthetic only; scratch UID10001 reads.
docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true --cpus 1 --memory 256m \
  --mount "type=bind,src=$CILIUM_CHECK_OUTPUT,dst=/cases,readonly" \
  "$CILIUM_CHECK_VALIDATOR_IMAGE" /cases/cilium-cases.json \
  | tee "$CILIUM_CHECK_OUTPUT/semantic.log"
