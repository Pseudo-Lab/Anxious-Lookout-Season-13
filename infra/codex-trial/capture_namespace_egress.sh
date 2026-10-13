#!/usr/bin/env bash
# Read-only actual k3s baseline; PM executes on the established target.
set -euo pipefail
set +x
umask 077
EGRESS_CAPTURE_DIR=${1:?new private absolute output directory}
[[ "$EGRESS_CAPTURE_DIR" == /* ]] || exit 1
mkdir -m 0700 -- "$EGRESS_CAPTURE_DIR"  # Refuse reuse of captured evidence.
k() { timeout --signal=TERM --kill-after=2s 15s sudo -n /usr/local/bin/k3s kubectl --request-timeout=10s "$@"; }
k -n codex-trial get cnp -o json > "$EGRESS_CAPTURE_DIR/cnp.json"
k get ccnp -o json > "$EGRESS_CAPTURE_DIR/ccnp.json"
k get namespace codex-trial -o json > "$EGRESS_CAPTURE_DIR/namespace.json"
k get networkpolicy -A -o json > "$EGRESS_CAPTURE_DIR/np.json"
k -n codex-trial get deployment access-web-runner-v1 -o json > "$EGRESS_CAPTURE_DIR/runner.json"
k -n codex-trial get pods,services,pvc -o json > "$EGRESS_CAPTURE_DIR/resources.json"
k -n m2-hosting get pods,services,endpointslices -o json > "$EGRESS_CAPTURE_DIR/hosting.json"
k -n m2-hosting get cnp -o json > "$EGRESS_CAPTURE_DIR/hosting-cnp.json"
k -n kube-system get pods -l k8s-app=cilium -o json > "$EGRESS_CAPTURE_DIR/agents.json"
sha256sum "$EGRESS_CAPTURE_DIR/"*.json > "$EGRESS_CAPTURE_DIR/SHA256SUMS"
