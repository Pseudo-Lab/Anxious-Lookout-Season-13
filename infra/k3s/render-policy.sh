#!/usr/bin/env bash
# Render only; caller inspects and applies. No cluster or host mutations.
set -euo pipefail
: "${NODE_PUBLIC_IPV4:?Set NODE_PUBLIC_IPV4 to this server's current public IPv4 address}"
if [[ ! "$NODE_PUBLIC_IPV4" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
  echo 'NODE_PUBLIC_IPV4 must be a single IPv4 address' >&2
  exit 1
fi
IFS=. read -r -a octets <<< "$NODE_PUBLIC_IPV4"
for octet in "${octets[@]}"; do
  if (( 10#$octet > 255 )); then
    echo 'NODE_PUBLIC_IPV4 contains an invalid octet' >&2
    exit 1
  fi
done
infra_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
sed "s/__NODE_PUBLIC_IPV4__/${NODE_PUBLIC_IPV4}/g" "$infra_dir/policies/tenant-boundary.yaml.tpl"
