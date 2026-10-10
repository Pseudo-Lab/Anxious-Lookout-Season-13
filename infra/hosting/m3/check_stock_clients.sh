#!/usr/bin/env bash
# Authless synthetic stock server. Never mounts original HOME/socket/auth.
set -euo pipefail
STOCK_CHECK_DIR=$(cd "$(dirname "$0")" && pwd)
docker run --rm --pull=never --network none --read-only --tmpfs /tmp \
  --cap-drop ALL --security-opt no-new-privileges:true --user 10001:10001 \
  --mount "type=bind,src=$STOCK_CHECK_DIR/stock_client_probe.py,dst=/checks/probe.py,readonly" \
  --entrypoint python sha256:6fc02fa74a1b61b51301d57b3a54a263744e020dc1052d5070ada73bfdc33f87 \
  -B /checks/probe.py
