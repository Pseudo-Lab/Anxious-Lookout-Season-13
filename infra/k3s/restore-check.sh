#!/usr/bin/env bash
# Root/operator invocation only. Restores a COPY, never mounts live k3s data.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Run as root' >&2; exit 1; }
archive=${1:?Usage: restore-check.sh /absolute/root-owned/control-plane-archive.tar.gz}
[[ "$archive" == /* && -f "$archive" && ! -L "$archive" && $(stat -c %u "$archive") == 0 ]] || {
  echo 'Use an absolute root-owned regular archive, not a symlink' >&2; exit 1;
}
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$script_dir/versions.env"
restore_image="rancher/k3s:${K3S_VERSION/+/-}"
docker image inspect "$restore_image" >/dev/null
umask 077
staging=$(mktemp -d /var/tmp/anxious-k3s-restore.XXXXXX)
container="anxious-k3s-restore-$$"
cleanup() {
  local exit_status=$?
  trap - EXIT
  if docker container inspect "$container" >/dev/null 2>&1; then
    docker rm -f -v "$container" >/dev/null || exit_status=1
  fi
  rm -rf -- "$staging"
  exit "$exit_status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
# Reject traversal in generated archive paths before extracting an explicit subset.
while IFS= read -r member; do
  [[ "$member" != /* && "/$member/" != */../* ]] || { echo 'Unsafe archive path' >&2; exit 1; }
done < <(tar -tzf "$archive")
tar -xzf "$archive" -C "$staging" \
  ./var/lib/rancher/k3s/server/db ./var/lib/rancher/k3s/server/token \
  ./var/lib/rancher/k3s/server/tls ./var/lib/rancher/k3s/server/cred
live_hash=$(/usr/local/bin/k3s kubectl --request-timeout=20s -n kube-system \
  get secret k3s-serving -o 'jsonpath={.data}' | sha256sum | cut -d ' ' -f 1)
# No privileged flag, host network, host PID, published ports or live volume.
# :Z relabels only the new root-owned restore copy for enforcing SELinux.
docker run -d --name "$container" --hostname anxious-lookout --network none \
  --memory 2g --cpus 1 \
  -v "$staging/var/lib/rancher/k3s:/var/lib/rancher/k3s:Z" \
  "$restore_image" server --disable-agent --disable-network-policy \
  --flannel-backend=none --node-name=anxious-lookout \
  --bind-address=127.0.0.1 --advertise-address=127.0.0.1 --node-ip=127.0.0.1 \
  --cluster-cidr=10.42.0.0/16 --service-cidr=10.43.0.0/16 \
  --secrets-encryption --token-file=/var/lib/rancher/k3s/server/token >/dev/null
ready=0
for ((attempt=0; attempt<60; attempt++)); do
  if docker exec "$container" kubectl --request-timeout=3s get --raw /readyz > "$staging/readyz" 2>/dev/null && [[ $(cat "$staging/readyz") == ok ]]; then
    ready=1; break
  fi
  running=$(docker inspect -f '{{.State.Running}}' "$container")
  [[ "$running" == true ]] || break
  sleep 2
done
if (( ! ready )); then
  # Container logs can contain credential-bearing startup messages; save only
  # root-only and never print them to GitHub. The EXIT trap removes the copy.
  echo 'Isolated restored API did not become ready; inspect private logs before repeating' >&2
  docker logs "$container" > /var/log/anxious-k3s-restore-failure.log 2>&1 || true
  chmod 0600 /var/log/anxious-k3s-restore-failure.log
  exit 1
fi
docker exec "$container" kubectl --request-timeout=10s get ccnp \
  anxious-lookout-tenant-boundary anxious-lookout-node-protection -o name
docker exec "$container" kubectl --request-timeout=10s get namespaces \
  kube-system platform-system tenant-validation-a tenant-validation-b -o name
restored_hash=$(docker exec "$container" kubectl --request-timeout=10s -n kube-system \
  get secret k3s-serving -o 'jsonpath={.data}' | sha256sum | cut -d ' ' -f 1)
[[ "$live_hash" == "$restored_hash" ]] || {
  echo 'Secret data hash mismatch; investigate certificate rotation or restore mismatch' >&2; exit 1;
}
echo 'PASS: isolated restored API ready, policies/namespaces present, encrypted Secret data hash matches live data'
echo 'No application PVC or replacement-host network recovery is claimed by this check.'
