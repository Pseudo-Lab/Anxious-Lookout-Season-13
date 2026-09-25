#!/usr/bin/env bash
# Run only after root applies fixtures and waits for Cilium policy realization.
# All probes execute in Kubernetes containers, never the host language runtime.
set -uo pipefail
: "${NODE_PRIVATE_IPV4:?Set node private IPv4}"
: "${NODE_PUBLIC_IPV4:?Set node public IPv4}"
: "${DOCKER_SERVICE_IPV4:?Set existing Docker service container IPv4}"
validation_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
kctl() { /usr/local/bin/k3s kubectl --request-timeout=20s "$@"; }
failed=0
inconclusive=0
passed=0
probe() {
  local ns=$1 pod=$2 kind=$3 host=$4 port=$5
  PROBE_OUTPUT=$(kctl exec -i -n "$ns" "$pod" -- python - "$kind" "$host" "$port" < "$validation_dir/probe.py" 2>&1)
  PROBE_RC=$?
}
allow() {
  local label=$1; shift
  probe "$@"
  if [[ $PROBE_RC == 0 && "$PROBE_OUTPUT" == CONNECTED ]]; then
    echo "PASS allowed: $label"; ((passed+=1))
  else
    echo "FAIL allowed: $label (rc=$PROBE_RC, $PROBE_OUTPUT)"; ((failed+=1))
  fi
}
deny() {
  local label=$1 source_ns=$2 source_pod=$3 host=$4 port=$5 control_ns=$6 control_pod=$7
  # A working positive control is necessary to call a timeout a verified block.
  probe "$control_ns" "$control_pod" tcp "$host" "$port"
  local control_rc=$PROBE_RC
  probe "$source_ns" "$source_pod" tcp "$host" "$port"
  if [[ $PROBE_RC == 0 ]]; then
    echo "FAIL denied: $label unexpectedly connected"; ((failed+=1))
  elif [[ $PROBE_RC == 2 && $control_rc == 0 ]]; then
    echo "PASS denied: $label (target reachable from authorized control)"; ((passed+=1))
  else
    echo "INCONCLUSIVE denied: $label (source_rc=$PROBE_RC control_rc=$control_rc); correlate a Cilium policy-denied event"; ((inconclusive+=1))
  fi
}
get_ip() { kctl get "$1" "$2" -n "$3" -o "jsonpath={$4}"; }
a_ip=$(get_ip pod agent tenant-validation-a .status.podIP) || exit 1
b_ip=$(get_ip pod agent tenant-validation-b .status.podIP) || exit 1
b_svc=$(get_ip svc agent tenant-validation-b .spec.clusterIP) || exit 1
a_svc=$(get_ip svc agent tenant-validation-a .spec.clusterIP) || exit 1
api_ip=$(get_ip pod validation-materials-api platform-system .status.podIP) || exit 1
api_svc=$(get_ip svc validation-materials-api platform-system .spec.clusterIP) || exit 1
private_ip=$(get_ip pod private-api validation-private .status.podIP) || exit 1
dns_ip=$(get_ip svc kube-dns kube-system .spec.clusterIP) || exit 1
allow 'tenant DNS UDP' tenant-validation-a agent dns-udp "$dns_ip" 53
allow 'tenant DNS TCP' tenant-validation-a agent dns-tcp "$dns_ip" 53
allow 'tenant internet HTTPS with certificate verification' tenant-validation-a agent https example.com 443
allow 'tenant internet TCP 80' tenant-validation-a agent tcp example.com 80
allow 'materials API direct' tenant-validation-a agent tcp "$api_ip" 8080
allow 'materials API Service' tenant-validation-a agent tcp "$api_svc" 8080
allow 'manager to tenant A' platform-system validation-agent-manager tcp "$a_ip" 8080
allow 'manager to tenant B' platform-system validation-agent-manager tcp "$b_ip" 8080
deny 'tenant A to B direct' tenant-validation-a agent "$b_ip" 8080 platform-system validation-agent-manager
deny 'tenant A to B Service' tenant-validation-a agent "$b_svc" 8080 platform-system validation-agent-manager
deny 'tenant B to A direct' tenant-validation-b agent "$a_ip" 8080 platform-system validation-agent-manager
deny 'tenant B to A Service' tenant-validation-b agent "$a_svc" 8080 platform-system validation-agent-manager
deny 'materials API forbidden port' tenant-validation-a agent "$api_ip" 9090 validation-control probe
deny 'unrelated private Pod' tenant-validation-a agent "$private_ip" 8080 validation-control probe
deny 'unapproved ingress to tenant' validation-control probe "$a_ip" 8080 platform-system validation-agent-manager
deny 'Docker container direct' tenant-validation-a agent "$DOCKER_SERVICE_IPV4" 8642 validation-control probe
# Node-protection also denies these destinations to the unlabelled control.
# Timeout alone is intentionally INCONCLUSIVE: root must capture policy-drop
# evidence, not disable the identity boundary for a positive-control test.
for destination in "$NODE_PRIVATE_IPV4:22" "$NODE_PRIVATE_IPV4:6443" "$NODE_PRIVATE_IPV4:10250" "$NODE_PRIVATE_IPV4:8642" "$NODE_PUBLIC_IPV4:8642" '169.254.169.254:80' '10.43.0.1:443'; do
  deny "node boundary $destination" tenant-validation-a agent "${destination%:*}" "${destination##*:}" validation-control probe
done
printf 'RESULT pass=%s fail=%s requires-policy-drop-evidence=%s\n' "$passed" "$failed" "$inconclusive"
((failed == 0)) || exit 1
((inconclusive == 0)) || exit 2
