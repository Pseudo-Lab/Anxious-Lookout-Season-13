#!/usr/bin/env bash
# Isolated Docker PostgreSQL + adapter fixtures, never actual auth/services/PVCs.
set -euo pipefail
UID_REPO=$(cd "$(dirname "$0")/../.." && pwd)
UID_PG="issue7-uid-fixture-$(cat /proc/sys/kernel/random/uuid)"
trap 'docker rm -f "$UID_PG" >/dev/null 2>&1 || true' EXIT
docker run -d --rm --pull=never --name "$UID_PG" --network none --read-only --user 999:999 \
  --cap-drop ALL --security-opt no-new-privileges:true \
  --tmpfs /var/lib/postgresql:uid=999,gid=999,size=256m --tmpfs /var/run/postgresql:uid=999,gid=999 --tmpfs /tmp \
  -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=hosting_test \
  postgres:18.6-bookworm@sha256:afc7e2d441324c0388fa80c3d24f733b4194a4eb7f47dd8ee2b08eb1a24a647c >/dev/null
UID_READY=no
for ((i=0;i<40;i++)); do
  if timeout 3s docker exec "$UID_PG" sh -c 'test "$(cat /proc/1/comm)" = postgres && pg_isready -U postgres -d hosting_test' >/dev/null 2>&1; then UID_READY=yes;break;fi
  sleep .5
done
[[ $UID_READY == yes ]]
docker run --rm --pull=never --network "container:$UID_PG" --read-only --tmpfs /tmp --user 10001:10001 \
  --cap-drop ALL --security-opt no-new-privileges:true \
  --mount "type=bind,src=$UID_REPO/backend/app,dst=/app/app,readonly" \
  --mount "type=bind,src=$UID_REPO/backend/runner,dst=/app/runner,readonly" \
  --mount "type=bind,src=$UID_REPO/backend/ops,dst=/app/ops,readonly" \
  --mount "type=bind,src=$UID_REPO/backend/tests,dst=/app/tests,readonly" \
  -e PYTHONPATH=/app -e APP_ENV=test -e RESEARCH_ACCESS_POLICY=approved \
  -e ADMIN_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1/hosting_test \
  -e API_DATABASE_PASSWORD=synthetic-uid-fixture-api-password \
  -e DATABASE_URL=postgresql+psycopg://anxious_api:synthetic-uid-fixture-api-password@127.0.0.1/hosting_test \
  -e AUTH_ORIGIN=http://127.0.0.1:28080 -e ALLOW_INSECURE_LOOPBACK=true \
  -e OAUTH_MODE=mock -e GITHUB_CLIENT_ID=synthetic -e GITHUB_CLIENT_SECRET=synthetic \
  -e AUTH_TRANSACTION_KEY=MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA= \
  --entrypoint pytest anxious-hosting-api-issue7-c4-r4-test -q -p no:cacheprovider \
  /app/tests/test_runner_binding.py /app/tests/test_source_metadata.py \
  /app/tests/test_runner_protocol.py /app/tests/test_runner_auth.py \
  /app/tests/test_conversations.py /app/tests/test_codex_status.py
printf 'PASS isolated user-UID/state/session fixtures; no actual Pods/provider/host auth; owned PG removed by EXIT\n'
