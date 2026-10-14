#!/usr/bin/env bash
# Verify a full logical dump in a disposable isolated clone, never live recovery.
set -euo pipefail
set +x
umask 077
CHATBOT_RESTORE_ARCHIVE=${1:?Private absolute DB dump}
CHATBOT_RESTORE_IMAGE=${2:?Verified immutable production API Docker ID}
[[ $CHATBOT_RESTORE_ARCHIVE == /* && -f $CHATBOT_RESTORE_ARCHIVE && ! -L $CHATBOT_RESTORE_ARCHIVE ]]
[[ $CHATBOT_RESTORE_IMAGE =~ ^sha256:[a-f0-9]{64}$ ]]
CHATBOT_RESTORE_NAME="issue9-chatbot-restore-$(cat /proc/sys/kernel/random/uuid)"
CHATBOT_RESTORE_PG='postgres:18.6-bookworm@sha256:afc7e2d441324c0388fa80c3d24f733b4194a4eb7f47dd8ee2b08eb1a24a647c'
trap 'docker rm -f "$CHATBOT_RESTORE_NAME" >/dev/null 2>&1 || true' EXIT
docker run -d --rm --pull=never --name "$CHATBOT_RESTORE_NAME" --network none --read-only --user 999:999 \
  --cap-drop ALL --security-opt no-new-privileges:true \
  --tmpfs /var/lib/postgresql:uid=999,gid=999,size=1g --tmpfs /var/run/postgresql:uid=999,gid=999 --tmpfs /tmp \
  -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=restore_chatbot "$CHATBOT_RESTORE_PG" >/dev/null
CHATBOT_RESTORE_READY=no
for ((i=0;i<40;i++)); do
  if timeout 3s docker exec "$CHATBOT_RESTORE_NAME" pg_isready -U postgres -d restore_chatbot >/dev/null 2>&1; then CHATBOT_RESTORE_READY=yes; break; fi
  sleep .5
done
[[ $CHATBOT_RESTORE_READY == yes ]]
timeout 120s docker exec -i "$CHATBOT_RESTORE_NAME" pg_restore -U postgres -d restore_chatbot \
  --no-owner --no-privileges --exit-on-error --single-transaction < "$CHATBOT_RESTORE_ARCHIVE"
CHATBOT_RESTORE_SQL="SELECT json_build_object('items',(SELECT count(*) FROM research.items),'versions',(SELECT count(*) FROM research.versions),'relations',(SELECT count(*) FROM research.relations),'conversations',(SELECT count(*) FROM research.conversations),'publications',(SELECT count(*) FROM research.publications),'heads',(SELECT count(*) FROM research.publication_heads),'nativeMetadata',(SELECT md5(coalesce(string_agg(native_record::text,'|' ORDER BY id),'')) FROM research.conversations));"
CHATBOT_RESTORE_BEFORE=$(docker exec "$CHATBOT_RESTORE_NAME" psql -X -qAt -v ON_ERROR_STOP=1 -U postgres -d restore_chatbot -c "$CHATBOT_RESTORE_SQL")
docker run --rm --pull=never --network "container:$CHATBOT_RESTORE_NAME" --read-only --tmpfs /tmp \
  --cap-drop ALL --security-opt no-new-privileges:true --user 10001:10001 \
  -e RESTORE_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1/restore_chatbot \
  -e API_DATABASE_PASSWORD=disposable-chatbot-restore-password --entrypoint python "$CHATBOT_RESTORE_IMAGE" \
  -B -m app.restore_research --database restore_chatbot --confirm-isolated-candidate >/dev/null
CHATBOT_RESTORE_AFTER=$(docker exec "$CHATBOT_RESTORE_NAME" psql -X -qAt -v ON_ERROR_STOP=1 -U postgres -d restore_chatbot -c "$CHATBOT_RESTORE_SQL")
[[ $CHATBOT_RESTORE_BEFORE == "$CHATBOT_RESTORE_AFTER" ]]
printf '{"dbRestoreVerified":true,"researchAndNativeMetadataPreserved":true,"liveRestored":false}\n'
