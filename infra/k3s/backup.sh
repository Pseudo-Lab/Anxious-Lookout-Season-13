#!/usr/bin/env bash
# Online CONTROL-PLANE backup via SQLite's transactional backup API.
# Root-operated. Does not snapshot running application data or restart services.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Run as root' >&2; exit 1; }
backup_dir=${1:-/var/backups/anxious-lookout/k3s}
[[ "$backup_dir" == /* && "$backup_dir" != / ]] || { echo 'Use an absolute non-root backup directory' >&2; exit 1; }
[[ "$(realpath -m -- "$backup_dir")" == "${backup_dir%/}" ]] || { echo 'Use a canonical path without symlinks or traversal' >&2; exit 1; }
backup_dir=${backup_dir%/}
if [[ -e "$backup_dir" ]]; then
  [[ -d "$backup_dir" && ! -L "$backup_dir" && $(stat -c %u "$backup_dir") == 0 ]] || {
    echo 'Existing backup directory must be a root-owned real directory' >&2; exit 1;
  }
fi
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
umask 077
install -d -m 0700 "$backup_dir"
staging=$(mktemp -d /var/tmp/anxious-k3s-backup.XXXXXX)
partial=''
cleanup() {
  local exit_status=$?
  trap - EXIT
  [[ -z "$partial" ]] || rm -f -- "$partial"
  rm -rf -- "$staging"
  exit "$exit_status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
install -d -m 0700 "$staging/recovery" "$staging/var/lib/rancher/k3s/server/db"
cp -a "$script_dir" "$staging/recovery/infra-k3s"
/usr/local/bin/k3s --version > "$staging/recovery/k3s-version.txt"
/usr/local/bin/k3s kubectl --request-timeout=20s get ccnp -o yaml > "$staging/recovery/network-policies.yaml"
/usr/local/bin/k3s kubectl --request-timeout=20s get namespaces -o yaml > "$staging/recovery/namespaces.yaml"
paths=(var/lib/rancher/k3s/server/token var/lib/rancher/k3s/server/tls
       var/lib/rancher/k3s/server/cred var/lib/rancher/k3s/server/manifests etc/rancher/k3s)
for optional in etc/firewalld etc/NetworkManager/conf.d etc/systemd/system/k3s.service \
                etc/systemd/system/k3s.service.env etc/systemd/system/anxious-k3s-firewall-sync.service \
                etc/systemd/system/anxious-k3s-firewall-sync.timer usr/local/sbin/anxious-k3s-firewall-sync.py \
                etc/systemd/system/anxious-k3s-postboot-check.service etc/anxious-lookout/postboot.env \
                usr/local/sbin/anxious-k3s-postboot-check.sh usr/local/lib/anxious-k3s-validation \
                var/lib/anxious-lookout/reboot-check; do
  [[ ! -e "/$optional" ]] || paths+=("$optional")
done
for required in "${paths[@]}"; do
  [[ -e "/$required" ]] || { echo "Required backup input absent: /$required" >&2; exit 1; }
  (cd / && cp -a --parents -- "$required" "$staging")
done
# This is an operational backup, not a host test invocation. SQLite reads the
# live DB+WAL transactionally; never raw-copy a live state.db alone.
python3 - "$staging/var/lib/rancher/k3s/server/db/state.db" <<'PY'
import os, sqlite3, sys
source = sqlite3.connect('file:/var/lib/rancher/k3s/server/db/state.db?mode=ro', uri=True, timeout=30)
target = sqlite3.connect(sys.argv[1])
try:
    source.backup(target, pages=256, sleep=0.1)
    target.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    target.execute('PRAGMA journal_mode=DELETE')
finally:
    target.close()
    source.close()
os.chmod(sys.argv[1], 0o600)
PY
partial=$(mktemp "$backup_dir/.partial.XXXXXX")
tar --acls --xattrs --selinux --numeric-owner -C "$staging" -czf "$partial" .
archive="$backup_dir/control-plane-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
[[ ! -e "$archive" ]] || { echo 'Backup filename collision' >&2; exit 1; }
mv -- "$partial" "$archive"
partial=''
chmod 0600 "$archive"
(cd -- "$backup_dir" && sha256sum -- "$(basename -- "$archive")" > "$(basename -- "$archive").sha256")
printf 'Created control-plane archive: %s\n' "$archive"
echo 'Contains credentials. Keep root-only; encrypt before off-host transfer. Application volumes are excluded.'
echo 'No archives were deleted. Retain the last seven verified snapshots and an off-host copy before pruning manually.'
