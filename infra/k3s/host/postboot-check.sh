#!/usr/bin/env bash
# One-time boot evidence collector. No policy changes, restarts or remediation.
set -uo pipefail
[[ $EUID == 0 ]] || exit 1
umask 077
log=/var/log/anxious-k3s-postboot.log
[[ ! -L "$log" ]] || exit 1
exec >> "$log" 2>&1
chmod 0600 "$log"
echo "BEGIN postboot UTC=$(date -u +%FT%TZ)"
: "${NODE_PRIVATE_IPV4:?Set in the root-owned service environment file}"
: "${NODE_PUBLIC_IPV4:?Set in the root-owned service environment file}"
: "${DOCKER_CONTAINER:?Set the existing application container name}"
: "${DOCKER_NETWORK_NAME:?Set the existing application Docker network name}"
validation_dir=${VALIDATION_DIR:-/usr/local/lib/anxious-k3s-validation}
before_file=/var/lib/anxious-lookout/reboot-check/before-boot-id
before=$(cat "$before_file") || exit 1
after=$(cat /proc/sys/kernel/random/boot_id) || exit 1
printf 'boot-before=%s boot-after=%s\n' "$before" "$after"
[[ "$before" != "$after" ]] || { echo 'FAIL: boot ID unchanged'; exit 1; }
fixtures_ready() {
  local ns
  for ns in tenant-validation-a tenant-validation-b platform-system validation-control validation-private; do
    /usr/local/bin/k3s kubectl -n "$ns" wait --for=condition=Ready pod \
      -l anxious-lookout.io/validation=issue-1 --timeout=5s >/dev/null 2>&1 || return 1
  done
}
services_active() {
  local unit
  for unit in k3s docker firewalld anxious-k3s-firewall-sync.timer; do
    systemctl is-active --quiet "$unit" || return 1
  done
}
ready=0
deadline=$((SECONDS + 300))
while (( SECONDS < deadline )); do
  if services_active \
    && /usr/local/bin/k3s kubectl wait --for=condition=Ready node/anxious-lookout --timeout=5s >/dev/null 2>&1 \
    && /usr/local/bin/k3s kubectl -n kube-system rollout status ds/cilium --timeout=5s >/dev/null 2>&1 \
    && /usr/local/bin/k3s kubectl -n kube-system wait --for=condition=Available \
      deployment/coredns deployment/metrics-server deployment/traefik deployment/local-path-provisioner --timeout=5s >/dev/null 2>&1 \
    && fixtures_ready; then
    ready=1; break
  fi
  sleep 5
done
(( ready )) || { echo 'FAIL: cluster did not recover within startup wait'; exit 1; }
failed=0
check() { if "$@"; then return 0; else echo "FAIL command: $*"; failed=1; fi; }
check env KUBECONFIG=/etc/rancher/k3s/k3s.yaml /usr/local/bin/cilium status --wait=false
check iptables -S CILIUM_FORWARD
check iptables -t nat -S CILIUM_POST_nat
check /usr/local/bin/k3s kubectl get ccnp anxious-lookout-node-protection anxious-lookout-tenant-boundary
check firewall-cmd --ipset=anxious-k3s-api-clients --get-entries
check firewall-cmd --ipset=anxious-k3s-metrics-clients --get-entries
health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$DOCKER_CONTAINER")
echo "existing-Docker-state=$health"
[[ "$health" == healthy || "$health" == running ]] || failed=1
check docker exec "$DOCKER_CONTAINER" python3 -c 'import socket, urllib.request; socket.getaddrinfo("example.com",443); response=urllib.request.urlopen("https://example.com",timeout=10); assert response.status==200; print("Existing Docker DNS OK; HTTPS 200")'
DOCKER_SERVICE_IPV4=$(docker inspect -f "{{with index .NetworkSettings.Networks \"$DOCKER_NETWORK_NAME\"}}{{.IPAddress}}{{end}}" "$DOCKER_CONTAINER")
[[ -n "$DOCKER_SERVICE_IPV4" ]] || { echo 'FAIL: Docker network address missing'; exit 1; }
export NODE_PRIVATE_IPV4 NODE_PUBLIC_IPV4 DOCKER_SERVICE_IPV4
# Give Cilium a chance to emit evidence while the probes are running. These
# logs contain addresses/policy events only, not packets or application payloads.
timeout 180 /usr/local/bin/k3s kubectl -n kube-system exec ds/cilium -- \
  cilium-dbg monitor --type drop &
monitor_pid=$!
trap 'kill "$monitor_pid" 2>/dev/null || true' EXIT INT TERM
bash "$validation_dir/run.sh"
matrix_rc=$?
kill "$monitor_pid" 2>/dev/null || true
wait "$monitor_pid" 2>/dev/null || true
trap - EXIT INT TERM
if (( matrix_rc == 1 )); then failed=1; fi
if (( matrix_rc > 2 )); then failed=1; fi
if (( failed )); then
  echo 'FAIL: postboot infrastructure/network checks need intervention'
  exit 1
fi
if (( matrix_rc == 2 )); then
  echo 'PENDING REVIEW: baseline checks passed; correlate recorded drop events with inconclusive guard probes'
  exit 2
fi
echo "PASS postboot UTC=$(date -u +%FT%TZ)"
