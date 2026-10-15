#!/usr/bin/env bash
# Entirely synthetic, no host ports or cluster writes; Docker language tools only.
set -euo pipefail
ROOT_CHECK_DIR=$(cd "$(dirname "$0")" && pwd)
ROOT_FIXTURE=${1:?New /tmp/issue7-root-https-check-* directory}
[[ $ROOT_FIXTURE =~ ^/tmp/issue7-root-https-check-[A-Za-z0-9_-]+$ && ! -e $ROOT_FIXTURE ]]
mkdir -m 0777 "$ROOT_FIXTURE" # Synthetic disposable TLS only, never actual input.
chmod 0777 "$ROOT_FIXTURE"
ROOT_MOCK_ID= ROOT_PROXY_ID=
cleanup() {
  local result=$?
  if [[ $result != 0 && -n $ROOT_PROXY_ID ]]; then docker logs "$ROOT_PROXY_ID" >&2 || true; fi
  if [[ $result != 0 && -n $ROOT_MOCK_ID ]]; then docker logs "$ROOT_MOCK_ID" >&2 || true; fi
  if [[ -n $ROOT_PROXY_ID ]]; then docker rm -f "$ROOT_PROXY_ID" >/dev/null 2>&1 || true; fi
  if [[ -n $ROOT_MOCK_ID ]]; then docker rm -f "$ROOT_MOCK_ID" >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT
docker run --rm --pull=never --network none --read-only --tmpfs /tmp --user 10001:10001 \
  -e PYTHONPATH=/checks:/app \
  --mount "type=bind,src=$ROOT_CHECK_DIR,dst=/checks,readonly" \
  --entrypoint pytest anxious-hosting-api-issue7-c4-r4-test \
  -q -p no:cacheprovider /checks/test_https_prepare.py /checks/test_prepare.py
docker run --rm --pull=never --network none --read-only --tmpfs /tmp --user 10001:10001 \
  -e PYTHONPATH=/checks:/app \
  --mount "type=bind,src=$ROOT_CHECK_DIR,dst=/checks,readonly" \
  --mount "type=bind,src=$ROOT_FIXTURE,dst=/fixture" \
  --entrypoint python anxious-hosting-api-issue7-c4-r4-test /checks/https_fixture.py
ROOT_MOCK_ID=$(docker run -d --rm --pull=never --network none --read-only --tmpfs /tmp --user 10001:10001 \
  --cap-drop ALL --security-opt no-new-privileges:true -e PYTHONPATH=/app \
  --mount "type=bind,src=$ROOT_CHECK_DIR,dst=/checks,readonly" \
  --mount "type=bind,src=$ROOT_FIXTURE,dst=/fixture,readonly" \
  --entrypoint python sha256:ddd324c1078ffee5c3deef2f3fc21543f94edb25bb9ac9828c68121266fdfde3 /checks/https_mock.py)
ROOT_PROXY_ID=$(docker run -d --rm --pull=never --network "container:$ROOT_MOCK_ID" --read-only --tmpfs /tmp \
  --user 10001:10001 --cap-drop ALL --security-opt no-new-privileges:true \
  --mount "type=bind,src=$ROOT_FIXTURE,dst=/routing,readonly" \
  rancher/mirrored-library-traefik:3.7.8@sha256:4299bbed850421258fc5448c2e0e6ad350981d4d335a68de11b92448aedbefe5 \
  --entrypoints.web.address=:8000 --entrypoints.websecure.address=:8443 --entrypoints.websecure.http.tls=true \
  --providers.file.filename=/routing/routes.yml --api.dashboard=false --log.level=ERROR)
docker exec "$ROOT_MOCK_ID" python /checks/https_probe.py
docker rm -f "$ROOT_PROXY_ID" >/dev/null
ROOT_PROXY_ID=$(docker run -d --rm --pull=never --network "container:$ROOT_MOCK_ID" --read-only --tmpfs /tmp \
  --user 10001:10001 --cap-drop ALL --security-opt no-new-privileges:true \
  --mount "type=bind,src=$ROOT_FIXTURE,dst=/routing,readonly" \
  rancher/mirrored-library-traefik:3.7.8@sha256:4299bbed850421258fc5448c2e0e6ad350981d4d335a68de11b92448aedbefe5 \
  --entrypoints.web.address=:8000 --entrypoints.websecure.address=:8443 --entrypoints.websecure.http.tls=true \
  --providers.file.filename=/routing/probe-routes.yml --api.dashboard=false --log.level=ERROR)
docker exec "$ROOT_MOCK_ID" python /checks/https_probe.py --tls-only
printf 'Owned synthetic containers removed by EXIT trap; no actual API/native/provider/cluster changes\n'
