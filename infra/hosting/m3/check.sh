#!/usr/bin/env bash
# Synthetic Docker source dump + actual approved image rehearsal; no live resources.
set -euo pipefail
umask 077
CHECK_DIR=$(cd "$(dirname "$0")" && pwd)
CHECK_OUT=${1:?New /tmp/issue7-m3-check-* private directory}
CHECK_MODE=${2:-all}
[[ $CHECK_MODE == all || $CHECK_MODE == --only-db-guard ]]
[[ $CHECK_OUT =~ ^/tmp/issue7-m3-check-[A-Za-z0-9_-]+$ && ! -e $CHECK_OUT ]]
mkdir -m 0700 "$CHECK_OUT"
CHECK_PG="issue7-m3-source-$(cat /proc/sys/kernel/random/uuid)"
trap 'docker rm -f "$CHECK_PG" >/dev/null 2>&1 || true' EXIT
docker run --rm --network none --read-only --tmpfs /tmp \
  --env PYTHONPATH=/checks:/app --env PYTHONDONTWRITEBYTECODE=1 \
  --mount "type=bind,src=$CHECK_DIR,dst=/checks,readonly" --entrypoint bash anxious-hosting-api-issue7-c4-r4-test \
  -c 'bash -n /checks/rehearse.sh && pytest -q -p no:cacheprovider /checks/test_prepare.py /checks/test_db_guard.py' > "$CHECK_OUT/unit.txt"
docker run -d --pull=never --name "$CHECK_PG" --network none --read-only --user 999:999 \
  --cap-drop ALL --security-opt no-new-privileges:true \
  --tmpfs /var/lib/postgresql:uid=999,gid=999,size=256m --tmpfs /var/run/postgresql:uid=999,gid=999 --tmpfs /tmp \
  -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=hosting \
  postgres:18.6-bookworm@sha256:afc7e2d441324c0388fa80c3d24f733b4194a4eb7f47dd8ee2b08eb1a24a647c > "$CHECK_OUT/source.id"
ready=no
for ((i=0;i<40;i++)); do
  if timeout 3s docker exec "$CHECK_PG" sh -c 'test "$(cat /proc/1/comm)" = postgres && pg_isready -U postgres -d hosting' >/dev/null 2>&1; then ready=yes;break;fi
  sleep .5
done
[[ $ready == yes ]]
docker run -i --rm --pull=never --network "container:$CHECK_PG" --read-only --tmpfs /tmp --user 10001:10001 \
  -e ADMIN_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1/hosting -e API_DATABASE_PASSWORD=isolated-fixture-api-password \
  --entrypoint python sha256:41403b6a5ab16bee5cfb936879761f8a31420085236794de347eb410944defca > "$CHECK_OUT/seed.txt" <<'PY'
from sqlalchemy import create_engine,text
from app.migrate import migrate
migrate()
engine=create_engine('postgresql+psycopg://postgres@127.0.0.1/hosting')
with engine.begin() as c:
    owner=c.execute(text("INSERT INTO auth.accounts(github_id,login,role,is_approved) VALUES('333333','isolated','editor',true) RETURNING id")).scalar_one()
    c.execute(text("INSERT INTO auth.sessions(token_hash,account_id,csrf_token,expires_at) VALUES(:token,:owner,'synthetic-csrf',now()+interval '1 hour')"),{'token':'a'*64,'owner':owner})
    c.execute(text("INSERT INTO auth.oauth_transactions VALUES(:state,:binding,'synthetic-encrypted',now()+interval '1 minute')"),{'state':'b'*64,'binding':'c'*64})
    c.execute(text("INSERT INTO auth.permission_audit(actor,account_id,reason,before,after) VALUES('synthetic',:owner,'preserve','{}','{}')"),{'owner':owner})
engine.dispose()
print('Synthetic populated M2 source ready')
PY
if [[ $CHECK_MODE == all ]]; then
  docker exec "$CHECK_PG" pg_dump -U postgres -d hosting --no-owner --no-privileges -Fc > "$CHECK_OUT/source.dump"
  sha256sum "$CHECK_OUT/source.dump" > "$CHECK_OUT/source.dump.sha256"
  bash "$CHECK_DIR/rehearse.sh" "$CHECK_OUT/source.dump" "$CHECK_OUT/rehearsal.json" > "$CHECK_OUT/rehearsal.txt"
fi
# Verify actual mounted before/after checker on an entirely offline synthetic
# source DB. Bind a nonsecret hosts file instead of accessing cluster DNS.
printf '127.0.0.1 localhost postgres\n' > "$CHECK_OUT/hosts"
chmod 0644 "$CHECK_OUT/hosts"
for stage in before after; do
  if [[ $stage == after ]]; then
    docker run --rm --pull=never --network "container:$CHECK_PG" --read-only --tmpfs /tmp --user 10001:10001 \
      -e ADMIN_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1/hosting -e API_DATABASE_PASSWORD=isolated-fixture-api-password \
      --entrypoint python sha256:41403b6a5ab16bee5cfb936879761f8a31420085236794de347eb410944defca \
      -m app.migrate --revision 0004_publication > "$CHECK_OUT/checker-migration.txt"
  fi
  docker run --rm --pull=never --network "container:$CHECK_PG" --read-only --tmpfs /tmp --user 10001:10001 \
    --mount "type=bind,src=$CHECK_DIR,dst=/checks,readonly" --mount "type=bind,src=$CHECK_OUT/hosts,dst=/etc/hosts,readonly" \
    -e PYTHONPATH=/app -e ADMIN_DATABASE_URL=postgresql+psycopg://postgres:synthetic-admin-password@postgres/hosting \
    -e DATABASE_URL=postgresql+psycopg://anxious_api:isolated-fixture-api-password@postgres/hosting \
    -e API_DATABASE_PASSWORD=isolated-fixture-api-password \
    --entrypoint python sha256:41403b6a5ab16bee5cfb936879761f8a31420085236794de347eb410944defca \
    /checks/verify_database.py "$stage" > "$CHECK_OUT/checker-$stage.json"
done
if [[ $CHECK_MODE == all ]]; then
  printf 'PASS unit boundaries plus actual M2/M3 image isolated dump/0004 compatibility; synthetic data only.\n'
else
  printf 'PASS DB guard unit boundaries and actual before/after readonly identity checks; no restore repeated.\n'
fi
