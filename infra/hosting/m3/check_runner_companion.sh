#!/usr/bin/env bash
# Actual runner source/HTTP path; synthetic private grant/cache, no original mounts.
set -euo pipefail
RUNNER_CHECK_DIR=$(cd "$(dirname "$0")" && pwd)
RUNNER_CHECK_BACKEND=$(cd "$RUNNER_CHECK_DIR/../../../backend" && pwd)
RUNNER_CHECK_IMAGE=${1:?Source-built runner-test immutable image ID}
RUNNER_CHECK_MODELS=${2:?Pinned public model catalog}
[[ $RUNNER_CHECK_IMAGE =~ ^sha256:[a-f0-9]{64}$ ]]
[[ -f $RUNNER_CHECK_MODELS && ! -L $RUNNER_CHECK_MODELS ]]
[[ $(sha256sum "$RUNNER_CHECK_MODELS" | cut -d' ' -f1) == fd219bd9f061278275f528939f82f54d2eb97df4b25c23b022adbe48813d920b ]]
RUNNER_CHECK_PG="issue7-runner-companion-unit-$(cat /proc/sys/kernel/random/uuid)"
trap 'docker rm -f "$RUNNER_CHECK_PG" >/dev/null 2>&1 || true' EXIT
docker run -d --rm --pull=never --name "$RUNNER_CHECK_PG" --network none --read-only --user 999:999 \
  --cap-drop ALL --security-opt no-new-privileges:true \
  --tmpfs /var/lib/postgresql:uid=999,gid=999,size=256m --tmpfs /var/run/postgresql:uid=999,gid=999 --tmpfs /tmp \
  -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=hosting_test \
  postgres:18.6-bookworm@sha256:afc7e2d441324c0388fa80c3d24f733b4194a4eb7f47dd8ee2b08eb1a24a647c >/dev/null
RUNNER_CHECK_READY=no
for ((i=0;i<40;i++)); do
  if timeout 3s docker exec "$RUNNER_CHECK_PG" pg_isready -U postgres -d hosting_test >/dev/null 2>&1; then RUNNER_CHECK_READY=yes;break;fi
  sleep .5
done
[[ $RUNNER_CHECK_READY == yes ]]
docker run --rm --pull=never --network none --read-only --tmpfs /tmp \
  --cap-drop ALL --security-opt no-new-privileges:true --user 10001:10001 \
  --mount "type=bind,src=$RUNNER_CHECK_DIR,dst=/checks,readonly" \
  --mount "type=bind,src=$RUNNER_CHECK_BACKEND,dst=/source,readonly" \
  --mount "type=bind,src=$RUNNER_CHECK_MODELS,dst=/fixture-models.json,readonly" \
  --env PYTHONPATH=/source --entrypoint python "$RUNNER_CHECK_IMAGE" \
  -B /checks/stock_tools_probe.py --runner-path --legacy-history --legacy-tools \
  --code-mode-wire --enable-code-mode-host --permission-probe
docker run --rm --pull=never --network "container:$RUNNER_CHECK_PG" --read-only --tmpfs /tmp \
  --cap-drop ALL --security-opt no-new-privileges:true --user 10001:10001 \
  --mount "type=bind,src=$RUNNER_CHECK_BACKEND,dst=/source,readonly" \
  --env PYTHONPATH=/source --env APP_ENV=test --env RESEARCH_ACCESS_POLICY=approved \
  --env ADMIN_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1/hosting_test \
  --env API_DATABASE_PASSWORD=synthetic-runner-api-password \
  --env DATABASE_URL=postgresql+psycopg://anxious_api:synthetic-runner-api-password@127.0.0.1/hosting_test \
  --env AUTH_ORIGIN=http://127.0.0.1:28080 --env ALLOW_INSECURE_LOOPBACK=true \
  --env OAUTH_MODE=mock --env GITHUB_CLIENT_ID=synthetic --env GITHUB_CLIENT_SECRET=synthetic \
  --env AUTH_TRANSACTION_KEY=MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA= \
  --entrypoint pytest "$RUNNER_CHECK_IMAGE" \
  -q -p no:cacheprovider /source/tests/test_runner_native_bundle.py \
  /source/tests/test_runner_protocol.py /source/tests/test_runner_auth.py /source/tests/test_runner_binding.py
