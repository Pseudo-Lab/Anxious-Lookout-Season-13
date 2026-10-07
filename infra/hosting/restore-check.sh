#!/usr/bin/env bash
set -euo pipefail
archive=${1:?Provide an owned authentication backup archive}
[[ "$archive" == /* && -f "$archive" && ! -L "$archive" ]] || exit 1
image='postgres:18.6-bookworm@sha256:afc7e2d441324c0388fa80c3d24f733b4194a4eb7f47dd8ee2b08eb1a24a647c'
restore_name="anxious-auth-restore-$(date -u +%Y%m%d%H%M%S)-$$"
cleanup() { docker rm -f "$restore_name" >/dev/null 2>&1 || true; }
trap cleanup EXIT
# Fresh container-local tmpfs only; no live DB, PVC, host network, ports or credentials.
docker run -d --name "$restore_name" --network none --read-only --user 999:999 \
  --cap-drop ALL --security-opt no-new-privileges \
  --tmpfs /var/lib/postgresql:uid=999,gid=999,size=256m \
  --tmpfs /var/run/postgresql:uid=999,gid=999 --tmpfs /tmp \
  -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=restore_only "$image" >/dev/null
ready=0
for _ in {1..60}; do
  if docker exec "$restore_name" pg_isready -U postgres -d restore_only >/dev/null 2>&1; then ready=1; break; fi
  sleep 1
done
[[ $ready == 1 ]] || { echo 'Isolated restore server did not become ready' >&2; exit 1; }
docker exec -i "$restore_name" pg_restore -U postgres -d restore_only --no-owner --no-privileges --exit-on-error --single-transaction < "$archive"
result=$(docker exec -i "$restore_name" psql -X -qAt -v ON_ERROR_STOP=1 -U postgres -d restore_only <<'SQL'
SELECT CASE WHEN (SELECT version FROM auth.schema_version WHERE singleton)=1
  AND NOT EXISTS(SELECT 1 FROM auth.accounts WHERE role NOT IN ('commenter','editor','admin'))
  AND NOT EXISTS(SELECT 1 FROM auth.sessions s LEFT JOIN auth.accounts a ON a.id=s.account_id WHERE a.id IS NULL)
  AND NOT EXISTS(SELECT 1 FROM auth.permission_audit p LEFT JOIN auth.accounts a ON a.id=p.account_id WHERE a.id IS NULL)
THEN 'VALID' ELSE 'INVALID' END;
DO $$
BEGIN
  IF to_regclass('auth.alembic_version') IS NOT NULL THEN
    IF (SELECT version_num FROM auth.alembic_version) IS DISTINCT FROM '0001_auth' THEN
      RAISE EXCEPTION 'Unsupported authentication Alembic revision';
    END IF;
  ELSE
    RAISE NOTICE 'Legacy v1 backup: reviewed Alembic upgrade required before app readiness';
  END IF;
END $$;
SELECT 'RESTORE pre-invalidation sessions=' || count(*) FROM auth.sessions;
SELECT 'RESTORE pre-invalidation transactions=' || count(*) FROM auth.oauth_transactions;
BEGIN;
DELETE FROM auth.sessions;
DELETE FROM auth.oauth_transactions;
COMMIT;
SELECT 'RESTORE accounts=' || count(*) FROM auth.accounts;
SELECT 'RESTORE approval-audits=' || count(*) FROM auth.permission_audit;
SELECT 'RESTORE live-sessions=' || count(*) FROM auth.sessions;
SQL
)
[[ "$result" == VALID* ]] || { echo 'Restored authentication invariants failed' >&2; exit 1; }
printf '%s\n' "$result"
echo 'PASS isolated logical restore; sessions/transactions invalidated. Reconcile approval changes before real recovery.'
