#!/usr/bin/env bash
# One isolated Docker restore+upgrade. Never connects to live DB/PVC/network.
set -euo pipefail
umask 077
ARCHIVE=${1:?Exact private pre-upgrade archive}
REPORT=${2:?New private report path}
[[ $ARCHIVE == /* && $ARCHIVE != *$'\n'* && $ARCHIVE != *$'\r'* && -f $ARCHIVE && ! -L $ARCHIVE && -f $ARCHIVE.sha256 && ! -L $ARCHIVE.sha256 ]]
[[ $REPORT == /* && ! -e $REPORT && ! -L $REPORT && ! -e $REPORT.stderr ]]
[[ $(wc -l < "$ARCHIVE.sha256") == 1 ]]
read -r M3_EXPECTED M3_CHECKED < "$ARCHIVE.sha256"
[[ $M3_EXPECTED =~ ^[0-9a-f]{64}$ && $M3_CHECKED == "$ARCHIVE" ]]
[[ $(sha256sum "$ARCHIVE" | cut -d ' ' -f 1) == "$M3_EXPECTED" ]]
M3_DIR=$(cd "$(dirname "$0")" && pwd)
M3_PG="issue7-m3-restore-$(cat /proc/sys/kernel/random/uuid)"
M3_IMAGE=sha256:41403b6a5ab16bee5cfb936879761f8a31420085236794de347eb410944defca
M3_OLD=docker.io/library/anxious-hosting-api@sha256:843880452e3b53c62c46fc932356263461b43aeed23b0b3f46896f36b1767e6f
M3_POSTGRES=postgres:18.6-bookworm@sha256:afc7e2d441324c0388fa80c3d24f733b4194a4eb7f47dd8ee2b08eb1a24a647c
M3_TMP="$REPORT.work"
mkdir -m 0700 "$M3_TMP"
cleanup() {
  for file in "$M3_TMP"/*.cid "$M3_TMP/container.id"; do
    [[ -f $file ]] || continue
    id=$(cat "$file")
    if [[ $(timeout 10s docker inspect -f '{{index .Config.Labels "issue7.rehearsal"}}' "$id" 2>/dev/null || true) == "$M3_PG" ]]; then
      timeout 10s docker rm -f "$id" >/dev/null 2>&1 || true
    fi
  done
  # Private partial diagnostics retained on failure, never remove original archive.
}
trap cleanup EXIT
docker image inspect "$M3_IMAGE" "$M3_OLD" "$M3_POSTGRES" >/dev/null
docker run -d --pull=never --name "$M3_PG" --label "issue7.rehearsal=$M3_PG" --network none --read-only --user 999:999 \
  --cap-drop ALL --security-opt no-new-privileges:true \
  --tmpfs /var/lib/postgresql:uid=999,gid=999,size=512m --tmpfs /var/run/postgresql:uid=999,gid=999 --tmpfs /tmp \
  -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=restore_m3 "$M3_POSTGRES" > "$M3_TMP/container.id"
M3_READY=no
for ((i=0;i<40;i++)); do
  if timeout 3s docker exec "$M3_PG" sh -c 'test "$(cat /proc/1/comm)" = postgres && pg_isready -U postgres -d restore_m3' >/dev/null 2>&1; then M3_READY=yes;break;fi
  sleep .5
done
[[ $M3_READY == yes ]]
timeout --signal=TERM --kill-after=2s 120s docker exec -i "$M3_PG" pg_restore -U postgres -d restore_m3 \
  --no-owner --no-privileges --exit-on-error --single-transaction < "$ARCHIVE" > "$M3_TMP/restore.stdout" 2> "$M3_TMP/restore.stderr"
M3_PASSWORD="isolated-$(cat /proc/sys/kernel/random/uuid)"
timeout --signal=TERM --kill-after=2s 150s docker run --cidfile "$M3_TMP/upgrade.cid" --label "issue7.rehearsal=$M3_PG" --rm --pull=never --network "container:$M3_PG" --read-only --tmpfs /tmp \
  --user 10001:10001 --cap-drop ALL --security-opt no-new-privileges:true \
  -e PYTHONPATH=/app -e ADMIN_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1/restore_m3 -e API_DATABASE_PASSWORD="$M3_PASSWORD" \
  --mount "type=bind,src=$M3_DIR,dst=/checks,readonly" --entrypoint python "$M3_IMAGE" /checks/rehearse.py \
  > "$M3_TMP/rehearsal.json" 2> "$M3_TMP/rehearsal.stderr"
M3_STEP=0
for image in "$M3_OLD" "$M3_IMAGE"; do
  M3_STEP=$((M3_STEP+1))
  timeout --signal=TERM --kill-after=2s 30s docker run --cidfile "$M3_TMP/compat-$M3_STEP.cid" --label "issue7.rehearsal=$M3_PG" --rm --pull=never --network "container:$M3_PG" --read-only --tmpfs /tmp \
    --user 10001:10001 --cap-drop ALL --security-opt no-new-privileges:true \
    -e "DATABASE_URL=postgresql+psycopg://anxious_api:$M3_PASSWORD@127.0.0.1/restore_m3" \
    -e APP_ENV=production -e OAUTH_MODE=disabled -e AUTH_ORIGIN=http://127.0.0.1:8080 -e ALLOW_INSECURE_LOOPBACK=true \
    -e CODEX_PERSONAL_ENABLE=false -e CODEX_RUNNERS_FILE= \
    --entrypoint python "$image" -c 'from fastapi.testclient import TestClient; from app.main import create_app; app=create_app(); assert not getattr(app.state,"codex_personal_enabled",False); 
with TestClient(app) as c:
 assert c.get("/healthz").status_code==200 and c.get("/readyz").status_code==200 and c.get("/api/auth/me").status_code==401
print("PASS actual image/auth readiness on upgraded isolated DB; native disabled")' \
    >> "$M3_TMP/compatibility.txt" 2>> "$M3_TMP/compatibility.stderr"
done
docker rm -f "$M3_PG" > "$M3_TMP/cleanup.txt"
docker ps -a --filter "name=$M3_PG" --format '{{.ID}}' > "$M3_TMP/containers-after.txt"
[[ ! -s $M3_TMP/containers-after.txt ]]
# No-overwrite publication; diagnostics stay private and are not dumped to console.
ln -T "$M3_TMP/rehearsal.json" "$REPORT"
printf 'PASS isolated restore/0004 rehearsal and actual M2/M3 API auth readiness; private diagnostics retained.\n'
