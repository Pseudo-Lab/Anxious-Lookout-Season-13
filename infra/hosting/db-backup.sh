#!/usr/bin/env bash
set -euo pipefail
umask 077
mode=${1:?Use docker or k3s}
archive=${2:?Provide a new absolute archive path}
[[ "$archive" == /* && "$archive" != *$'\n'* && "$archive" != *$'\r'* && "$archive" != *\\* ]] || { echo 'A new absolute backup path is required' >&2; exit 1; }
checksum="$archive.sha256"
lock_dir="$archive.lock"
collision() { [[ -e "$archive" || -L "$archive" || -e "$checksum" || -L "$checksum" ]]; }
if collision; then echo 'Backup destination or checksum already exists; nothing changed' >&2; exit 1; fi
db_name=${DB_NAME:-hosting}
[[ "$db_name" =~ ^[a-zA-Z0-9_]+$ ]] || exit 1
case "$mode" in
  docker)
    : "${COMPOSE_FILE:?Set the explicit project compose file}"
    : "${BACKUP_PROJECT_NAME:?Set the explicit existing project name}"
    [[ "$BACKUP_PROJECT_NAME" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || exit 1 ;;
  k3s) ;;
  *) echo 'Unknown backup mode' >&2; exit 1 ;;
esac
# Atomic per-destination lock. Never remove a lock that this run did not create.
mkdir -m 700 -- "$lock_dir" || { echo 'Backup destination is locked; nothing changed' >&2; exit 1; }
temporary=""
cleanup() {
  if [[ -n "$temporary" ]]; then
    rm -f -- "$temporary/dump" "$temporary/checksum"
    rmdir -- "$temporary" 2>/dev/null || true
  fi
  rmdir -- "$lock_dir" 2>/dev/null || true
}
trap cleanup EXIT
if collision; then echo 'Backup collision after locking; nothing changed' >&2; exit 1; fi
temporary=$(mktemp -d -- "$(dirname -- "$archive")/.auth-backup-XXXXXX")
case "$mode" in
  docker)
    docker compose -p "$BACKUP_PROJECT_NAME" -f "$COMPOSE_FILE" exec -T db pg_dump -U postgres -d "$db_name" --no-owner --no-privileges -Fc > "$temporary/dump"
    ;;
  k3s)
    /usr/local/bin/k3s kubectl -n m2-hosting exec postgres-0 -c postgres -- pg_dump -U postgres -d "$db_name" --no-owner --no-privileges -Fc > "$temporary/dump"
    ;;
  *) echo 'Unknown backup mode' >&2; exit 1 ;;
esac
backup_hash=$(sha256sum -- "$temporary/dump" | cut -d ' ' -f 1)
printf '%s  %s\n' "$backup_hash" "$archive" > "$temporary/checksum"
chmod 600 "$temporary/dump" "$temporary/checksum"
if collision; then echo 'Backup collision before publication; existing files preserved' >&2; exit 1; fi
# Hard-link publication never overwrites a destination, even if an external
# writer ignores our lock. Cleanup touches temporary files only. If publication
# is interrupted between links, preserve any public artifact for operator review.
ln -T -- "$temporary/dump" "$archive"
ln -T -- "$temporary/checksum" "$checksum"
echo 'Logical database backup and checksum created; keep private and encrypt before off-host transfer'
