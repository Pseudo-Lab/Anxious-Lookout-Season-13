#!/usr/bin/env bash
# Docker only, synthetic fixtures only. Never applies cluster resources.
set -euo pipefail
TRIAL_REPO_DIR=$(cd "$(dirname "$0")/../.." && pwd)
TRIAL_OUTPUT_DIR=${1:?Set a disposable /tmp/issue7-trial-* fixture directory}
TRIAL_TEST_IMAGE=${2:?Set the existing Docker synthetic test image}
[[ "$TRIAL_OUTPUT_DIR" =~ ^/tmp/issue7-trial-[A-Za-z0-9_-]+$ ]] || exit 1
mkdir "$TRIAL_OUTPUT_DIR"  # Refuse existing directories; never chmod actual inputs.
TRIAL_OUTPUT_DIR=$(cd "$TRIAL_OUTPUT_DIR" && pwd)
chmod 0777 "$TRIAL_OUTPUT_DIR"  # Wholly synthetic ephemeral cert; not an actual-input directory.
TRIAL_SERVER_ID= TRIAL_GATE_ID=
cleanup() {
  local result=$?
  if [[ "$result" != 0 ]]; then
    if [[ -n "$TRIAL_GATE_ID" ]]; then docker logs "$TRIAL_GATE_ID" >&2 || true; fi
    if [[ -n "$TRIAL_SERVER_ID" ]]; then docker logs "$TRIAL_SERVER_ID" >&2 || true; fi
  fi
  if [[ -n "$TRIAL_GATE_ID" ]]; then docker rm -f "$TRIAL_GATE_ID" >/dev/null 2>&1 || true; fi
  if [[ -n "$TRIAL_SERVER_ID" ]]; then docker rm -f "$TRIAL_SERVER_ID" >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT
docker run --rm --network none --read-only --tmpfs /tmp \
  --mount "type=bind,src=$TRIAL_REPO_DIR/infra/codex-trial,dst=/checks,readonly" \
  --entrypoint pytest "$TRIAL_TEST_IMAGE" -q -p no:cacheprovider /checks/test_render.py /checks/test_inputs.py
docker run --rm --network none --read-only --tmpfs /tmp \
  --mount "type=bind,src=$TRIAL_REPO_DIR/infra/codex-trial,dst=/checks,readonly" \
  --mount "type=bind,src=$TRIAL_OUTPUT_DIR,dst=/fixture" \
  --entrypoint python "$TRIAL_TEST_IMAGE" /checks/prepare_proxy.py
TRIAL_SERVER_ID=$(docker run -d --rm --network none --read-only --tmpfs /tmp \
  -e PYTHONPATH=/app \
  --mount "type=bind,src=$TRIAL_REPO_DIR/infra/codex-trial,dst=/checks,readonly" \
  --mount "type=bind,src=$TRIAL_REPO_DIR/backend/app,dst=/app/app,readonly" \
  --mount "type=bind,src=$TRIAL_OUTPUT_DIR,dst=/fixture,readonly" \
  --entrypoint python "$TRIAL_TEST_IMAGE" /checks/proxy_mock.py)
launch_proxy() {
  docker run -d --rm --network "container:$TRIAL_SERVER_ID" --read-only --tmpfs /tmp --user 10001:10001 \
  --mount "type=bind,src=$TRIAL_OUTPUT_DIR,dst=/routing,readonly" \
  rancher/mirrored-library-traefik:3.7.8@sha256:4299bbed850421258fc5448c2e0e6ad350981d4d335a68de11b92448aedbefe5 \
  --entrypoints.web.address=:8000 --entrypoints.websecure.address=:8443 --entrypoints.websecure.http.tls=true \
  --providers.file.filename="/routing/$1" --api.dashboard=false --log.level=ERROR
}
TRIAL_GATE_ID=$(launch_proxy routes.yml)
docker exec "$TRIAL_SERVER_ID" python /checks/proxy_probe.py
docker rm -f "$TRIAL_GATE_ID" >/dev/null
TRIAL_GATE_ID=$(launch_proxy probe-routes.yml)
docker exec "$TRIAL_SERVER_ID" python /checks/proxy_probe.py --access-only
printf 'Synthetic containers removed by EXIT trap; no host ports/new networks/cluster writes/provider calls\n'
