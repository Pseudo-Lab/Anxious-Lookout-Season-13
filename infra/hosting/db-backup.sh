#!/usr/bin/env bash
set -euo pipefail
umask 077
mode=${1:?Use docker or k3s}
archive=${2:?Provide a new absolute archive path}
[[ "$archive" == /* && ! -e "$archive" && ! -L "$archive" ]] || { echo 'A new absolute backup path is required' >&2; exit 1; }
db_name=${DB_NAME:-hosting}
[[ "$db_name" =~ ^[a-zA-Z0-9_]+$ ]] || exit 1
set -o noclobber
trap 'rm -f -- "$archive" "$archive.sha256"' ERR
case "$mode" in
  docker)
    : "${COMPOSE_FILE:?Set the explicit project compose file}"
    docker compose -f "$COMPOSE_FILE" exec -T db pg_dump -U postgres -d "$db_name" --no-owner --no-privileges -Fc > "$archive"
    ;;
  k3s)
    /usr/local/bin/k3s kubectl -n m2-hosting exec postgres-0 -c postgres -- pg_dump -U postgres -d "$db_name" --no-owner --no-privileges -Fc > "$archive"
    ;;
  *) echo 'Unknown backup mode' >&2; exit 1 ;;
esac
chmod 600 "$archive"
sha256sum "$archive" > "$archive.sha256"
trap - ERR
echo 'Logical database backup and checksum created; keep private and encrypt before off-host transfer'
