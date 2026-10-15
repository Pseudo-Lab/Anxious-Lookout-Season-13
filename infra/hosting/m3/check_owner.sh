#!/usr/bin/env bash
# Synthetic Docker unit/semantic checks and baked binding, never vendor startup.
set -euo pipefail
OWNER_CHECK_DIR=$(cd "$(dirname "$0")" && pwd)
OWNER_CHECK_OUTPUT=${1:?New /tmp/issue7-owner-check-* directory}
[[ $OWNER_CHECK_OUTPUT =~ ^/tmp/issue7-owner-check-[A-Za-z0-9_-]+$ && ! -e $OWNER_CHECK_OUTPUT ]]
mkdir -m 0755 "$OWNER_CHECK_OUTPUT"
docker run --rm --pull=never --network none --read-only --tmpfs /tmp --user 10001:10001 \
  --cap-drop ALL --security-opt no-new-privileges:true \
  --mount "type=bind,src=$OWNER_CHECK_DIR,dst=/checks,readonly" -e PYTHONPATH=/checks:/app \
  --entrypoint pytest anxious-hosting-api-issue7-c4-r4-test -q -p no:cacheprovider \
  /checks/test_owner_prepare.py /checks/test_owner_lookup.py /checks/test_prepare.py /checks/test_https_prepare.py
docker run --rm --pull=never --network none --read-only --tmpfs /tmp --user 10001:10001 \
  --mount "type=bind,src=$OWNER_CHECK_DIR,dst=/checks,readonly" -e PYTHONPATH=/checks:/app \
  --entrypoint python anxious-hosting-api-issue7-c4-r4-test /checks/owner_cases.py > "$OWNER_CHECK_OUTPUT/cases.json"
chmod 0644 "$OWNER_CHECK_OUTPUT/cases.json"
docker run --rm --pull=never --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true --cpus 1 --memory 256m \
  --mount "type=bind,src=$OWNER_CHECK_OUTPUT,dst=/cases,readonly" \
  anxious-s13-back-cilium-validator:1.20.2 /cases/cases.json
for OWNER_IMAGE in \
  sha256:5a2a95d93810bcfc035bb883d1096376d9f2cf6f48fc99f88f1f8c308c308566 \
  sha256:6fc02fa74a1b61b51301d57b3a54a263744e020dc1052d5070ada73bfdc33f87; do
  docker run --rm --pull=never --network none --read-only --tmpfs /tmp --user 10001:10001 \
    --cap-drop ALL --security-opt no-new-privileges:true --entrypoint python "$OWNER_IMAGE" -c \
    'from pathlib import Path; import json,uuid; from runner.binding import initialize_binding,require_binding; from app.codex_policy import CodexFailure; owner,state=str(uuid.uuid4()),str(uuid.uuid4()); root=Path("/tmp/bound"); initialize_binding(root,owner,state); require_binding(root,owner,state)
try: require_binding(root,owner,str(uuid.uuid4()))
except CodexFailure: pass
else: raise AssertionError("wrong state accepted")
try: initialize_binding(root,owner,state)
except CodexFailure: pass
else: raise AssertionError("bound root reinitialized")
assert not (root/"codex").exists() and not (root/"broker.json").exists(); assert json.loads(Path("/app/release.json").read_text())["sha"]=="70fbad43084d1b7dd435c83d9ad3d6ffc2106e63"; print("PASS baked binding valid/wrong-state/reinitialize; no vendor/auth")'
done
