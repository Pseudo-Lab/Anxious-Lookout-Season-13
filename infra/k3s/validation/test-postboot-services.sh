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
[[ ${#seen[@]} == 3 ]] || { echo 'FAIL: not all required services checked'; exit 1; }
for inactive in k3s docker firewalld; do
  if services_active; then
    echo "FAIL: accepted inactive $inactive" >&2
    exit 1
  fi
done
echo 'PASS: every required inactive unit prevents postboot startup success'

eval "$(sed -n '/^resource_absent() {/,/^}/p' "$validation_dir/../host/postboot-check.sh")"
policy_output=''
policy_rc=0
function /usr/local/bin/k3s() { printf '%s' "$policy_output"; return "$policy_rc"; }
resource_absent ccnp retired
policy_output='ciliumclusterwidenetworkpolicy.cilium.io/retired'
if resource_absent ccnp retired; then echo 'FAIL: existing policy accepted'; exit 1; fi
policy_output=''; policy_rc=1
if resource_absent ccnp retired; then echo 'FAIL: failed API read accepted as absence'; exit 1; fi

eval "$(sed -n '/^retired_ipsets_empty() {/,/^}/p' "$validation_dir/../host/postboot-check.sh")"
sets_output='unrelated-set'; sets_rc=0; entries_output=""
firewall-cmd() { if [[ "$*" == *--get-entries* ]]; then printf '%s' "$entries_output"; else printf '%s' "$sets_output"; fi; return "$sets_rc"; }
retired_ipsets_empty
entries_output="10.42.0.137"
for sets_output in anxious-k3s-api-clients 'unrelated-set anxious-k3s-metrics-clients'; do
  if retired_ipsets_empty; then echo 'FAIL: retired ipset accepted'; exit 1; fi
done
sets_output=''; sets_rc=1
if retired_ipsets_empty --permanent; then echo 'FAIL: failed firewall read accepted'; exit 1; fi
sets_rc=0; entries_output=""; sets_output="anxious-k3s-api-clients anxious-k3s-metrics-clients"
retired_ipsets_empty
echo 'PASS: policy/ipset presence and read errors fail closed'
