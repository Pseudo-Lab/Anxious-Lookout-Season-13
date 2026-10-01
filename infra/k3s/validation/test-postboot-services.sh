#!/usr/bin/env bash
# Run in a PM-managed Bash container. Tests the actual collector helper only.
set -euo pipefail
validation_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
eval "$(sed -n '/^services_active() {/,/^}/p' "$validation_dir/../host/postboot-check.sh")"
seen=()
inactive=''
systemctl() {
  [[ $# == 3 && $1 == is-active && $2 == --quiet ]] || return 64
  seen+=("$3")
  [[ "$3" != "$inactive" ]]
}
services_active
[[ ${#seen[@]} == 4 ]] || { echo 'FAIL: not all required services checked'; exit 1; }
for inactive in k3s docker firewalld anxious-k3s-firewall-sync.timer; do
  if services_active; then
    echo "FAIL: accepted inactive $inactive" >&2
    exit 1
  fi
done
echo 'PASS: every required inactive unit prevents postboot startup success'
