#!/usr/bin/env bash
# Exact pinned gateway and mock ASGI servers inside one --network none namespace.
set -euo pipefail
PROXY_REPO_DIR=$(cd "$(dirname "$0")/../.." && pwd)
PROXY_RENDER_DIR=${1:?Set the synthetic render directory}
PROXY_TEST_IMAGE=${2:?Set the existing synthetic test/inspection image}
PROXY_REVIEW_PROBE=${3:-}  # Optional unchanged reviewer counterexample, never real inputs.
PROXY_RENDER_DIR=$(cd "$PROXY_RENDER_DIR" && pwd)
PROXY_SERVER_ID= PROXY_GATE_ID=
cleanup() {
  if [[ -n "$PROXY_GATE_ID" ]]; then docker rm -f "$PROXY_GATE_ID" >/dev/null 2>&1 || true; fi
  if [[ -n "$PROXY_SERVER_ID" ]]; then docker rm -f "$PROXY_SERVER_ID" >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT
sed -e 's|http://api:8080|http://127.0.0.1:8081|g' \
    -e 's|http://web:8080|http://127.0.0.1:8082|g' \
    "$PROXY_REPO_DIR/backend/config/codex-personal-routes.yml" > "$PROXY_RENDER_DIR/proxy-routes.yml"
# Extract the production-rendered command; change only its test listen port.
docker run --rm --network none --read-only \
  --mount "type=bind,src=$PROXY_RENDER_DIR,dst=/render,readonly" \
  --entrypoint python "$PROXY_TEST_IMAGE" -c 'import json; c=json.load(open("/render/bootstrap.json")); print("\n".join(arg.replace("--entrypoints.web.address=:8080", "--entrypoints.web.address=:8090") for arg in c["services"]["gateway"]["command"]))' \
  > "$PROXY_RENDER_DIR/proxy-command.txt"
mapfile -t PROXY_COMMAND < "$PROXY_RENDER_DIR/proxy-command.txt"
PROXY_EXTRA_MOUNTS=()
if [[ -n "$PROXY_REVIEW_PROBE" ]]; then
  PROXY_EXTRA_MOUNTS=(--mount "type=bind,src=$PROXY_REVIEW_PROBE,dst=/reviewer_probe.py,readonly")
fi
PROXY_SERVER_ID=$(docker run -d --rm --network none --read-only --tmpfs /tmp \
  --mount "type=bind,src=$PROXY_REPO_DIR/backend/config_tests,dst=/probe,readonly" \
  "${PROXY_EXTRA_MOUNTS[@]}" \
  --entrypoint python "$PROXY_TEST_IMAGE" /probe/proxy_mock.py)
PROXY_GATE_ID=$(docker run -d --rm --network "container:$PROXY_SERVER_ID" --read-only --tmpfs /tmp \
  --user 10001:10001 \
  --mount "type=bind,src=$PROXY_RENDER_DIR/proxy-routes.yml,dst=/etc/traefik/routes.yml,readonly" \
  rancher/mirrored-library-traefik:3.7.8@sha256:4299bbed850421258fc5448c2e0e6ad350981d4d335a68de11b92448aedbefe5 \
  "${PROXY_COMMAND[@]}")
docker exec "$PROXY_SERVER_ID" python /probe/proxy_probe.py
if [[ -n "$PROXY_REVIEW_PROBE" ]]; then
  docker exec "$PROXY_SERVER_ID" python /reviewer_probe.py
  printf 'Unchanged reviewer proxy counterexample passed\n'
fi
printf 'Ephemeral proxy/mock containers cleaned by EXIT trap; no host ports/networks created\n'
