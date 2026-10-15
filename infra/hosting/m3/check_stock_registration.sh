#!/usr/bin/env bash
# Zero-turn/no-auth registration contract, same sealed image and blank HOME.
set -euo pipefail
REGISTRATION_DIR=$(cd "$(dirname "$0")" && pwd)
docker run --rm --pull=never --network none --read-only --tmpfs /tmp \
  --cap-drop ALL --security-opt no-new-privileges:true --user 10001:10001 \
  --mount "type=bind,src=$REGISTRATION_DIR,dst=/checks,readonly" \
  --entrypoint python sha256:6fc02fa74a1b61b51301d57b3a54a263744e020dc1052d5070ada73bfdc33f87 \
  -B /checks/stock_registration_probe.py
