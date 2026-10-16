#!/usr/bin/env bash
# One-time read-only evidence collector for issue-13 decision C.
set -uo pipefail
[[ $EUID == 0 ]] || exit 1
umask 077
log=/var/log/anxious-k3s-postboot.log
[[ ! -L "$log" ]] || exit 1
exec >> "$log" 2>&1
chmod 0600 "$log"
echo "BEGIN postboot UTC=$(date -u +%FT%TZ)"
: "${DOCKER_CONTAINER:?Set the existing application container name}"
: "${DOCKER_NETWORK_NAME:?Set the existing application Docker network name}"
before=$(cat /var/lib/anxious-lookout/reboot-check/before-boot-id) || exit 1
after=$(cat /proc/sys/kernel/random/boot_id) || exit 1
printf 'boot-before=%s boot-after=%s\n' "$before" "$after"
[[ "$before" != "$after" ]] || { echo 'FAIL: boot ID unchanged'; exit 1; }
services_active() {
  local unit
  for unit in k3s docker firewalld; do
    systemctl is-active --quiet "$unit" || return 1
  done
}
# A failed API read must never be mistaken for an absent policy.
resource_absent() {
  local result
  result=$(/usr/local/bin/k3s kubectl get "$@" --ignore-not-found -o name) || return 1
  [[ -z "$result" ]]
}
retired_ipsets_empty() {
  local sets name entries
  sets=$(firewall-cmd "$@" --get-ipsets) || return 1
  for name in $sets; do
    case "$name" in anxious-k3s-api-clients|anxious-k3s-metrics-clients)
      entries=$(firewall-cmd "$@" --ipset="$name" --get-entries) || return 1
      [[ -z "$entries" ]] || return 1;;
    esac
  done
}
ready=0
deadline=$((SECONDS + 300))
while (( SECONDS < deadline )); do
  if services_active \
    && /usr/local/bin/k3s kubectl wait --for=condition=Ready node/anxious-lookout --timeout=5s >/dev/null 2>&1 \
    && /usr/local/bin/k3s kubectl -n kube-system rollout status ds/cilium --timeout=5s >/dev/null 2>&1 \
    && /usr/local/bin/k3s kubectl -n kube-system wait --for=condition=Available \
      deployment/coredns deployment/traefik deployment/local-path-provisioner --timeout=5s >/dev/null 2>&1; then
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
check resource_absent ccnp anxious-lookout-node-protection anxious-lookout-tenant-boundary
check resource_absent cnp -n m2-hosting hosting-boundary hosting-access-web-link-v1
# Presence only. Compare retained specs with the private pre-transition snapshot manually.
check /usr/local/bin/k3s kubectl -n codex-runner get cnp runner-callback-draft -o name
check /usr/local/bin/k3s kubectl -n codex-runner get networkpolicy runner-default-deny -o name
rule='rule family="ipv4" source address="10.42.0.0/16" port port="6443" protocol="tcp" accept'
check firewall-cmd --zone=public --query-rich-rule="$rule"
check firewall-cmd --permanent --zone=public --query-rich-rule="$rule"
check retired_ipsets_empty
check retired_ipsets_empty --permanent
for retired_rule in \
  'rule family="ipv4" source ipset="anxious-k3s-api-clients" port port="6443" protocol="tcp" accept' \
  'rule family="ipv4" source ipset="anxious-k3s-metrics-clients" port port="10250" protocol="tcp" accept'; do
  for view in runtime permanent; do
    opts=(); [[ "$view" == runtime ]] || opts+=(--permanent)
    rules=$(firewall-cmd "${opts[@]}" --zone=public --list-rich-rules) || { failed=1; continue; }
    [[ "$rules" != *"$retired_rule"* ]] || failed=1
  done
done
health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$DOCKER_CONTAINER")
echo "existing-Docker-state=$health"
[[ "$health" == healthy || "$health" == running ]] || failed=1
check docker exec "$DOCKER_CONTAINER" python3 -c 'import socket, urllib.request; socket.getaddrinfo("example.com",443); response=urllib.request.urlopen("https://example.com",timeout=10); assert response.status==200; print("Existing Docker DNS OK; HTTPS 200")'
address=$(docker inspect -f "{{with index .NetworkSettings.Networks \"$DOCKER_NETWORK_NAME\"}}{{.IPAddress}}{{end}}" "$DOCKER_CONTAINER")
[[ -n "$address" ]] || failed=1
if (( failed )); then
  echo 'FAIL: postboot infrastructure/network checks need intervention'
  exit 1
fi
echo 'PENDING: baseline passed; compare retained policy specs and verify API/runner flow and external Docker response (DEFAULT-ALLOW.md). Metrics may be degraded.'
exit 2
