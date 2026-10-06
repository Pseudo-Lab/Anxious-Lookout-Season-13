#!/usr/bin/env bash
# Read-only operational collection. Contains no credential/Secret query.
set -euo pipefail
umask 077
target=${1:?Provide new private absolute baseline directory}
[[ "$target" == /* && ! -e "$target" && ! -L "$target" ]] || exit 1
mkdir -m 700 "$target"
kctl() { /usr/local/bin/k3s kubectl --request-timeout=20s "$@"; }
date -u +%Y-%m-%dT%H:%M:%SZ > "$target/time.txt"
kctl get nodes -o wide > "$target/nodes.txt"
kctl get pods,svc,ingress -A -o wide > "$target/workloads.txt"
kctl get statefulsets,pvc -A > "$target/storage.txt"
kctl get pv,storageclass >> "$target/storage.txt"
kctl top nodes > "$target/node-resources.txt"
kctl top pods -A > "$target/pod-resources.txt"
kctl get ccnp anxious-lookout-node-protection anxious-lookout-tenant-boundary -o yaml > "$target/m1-policies.yaml"
docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' > "$target/docker.txt"
docker inspect hermes --format '{{.State.StartedAt}} {{.State.Status}} {{.State.Health.Status}}' > "$target/hermes.txt"
systemctl is-active k3s docker firewalld anxious-k3s-firewall-sync.timer > "$target/systemd.txt"
free -m > "$target/memory.txt"
df -h / > "$target/disk.txt"
ingress_ipv4=$(kctl -n kube-system get svc traefik -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
printf '%s\n' "$ingress_ipv4" > "$target/ingress-address.txt"
if [[ "$ingress_ipv4" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; then
  curl --max-time 10 -sS -D "$target/android.headers" -o "$target/android.body" "http://$ingress_ipv4/android-agent/" || echo 'Existing android probe failed; investigate before apply' > "$target/android.failure"
else
  echo 'No verified IPv4 ingress address; collect an operator-confirmed route baseline' > "$target/android.failure"
fi
echo 'Private read-only baseline collected; collect representative app and packet-policy controls separately before/after apply'
