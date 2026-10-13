#!/usr/bin/env bash
# Only trial-owned temporary objects; UID-preconditioned API deletion.
set -euo pipefail
set +x
EGRESS_DELETE_KIND=${1:?kind}
EGRESS_DELETE_NAME=${2:?name}
EGRESS_DELETE_UID=${3:?UID retained from successful creation}
[[ "$EGRESS_DELETE_UID" =~ ^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$ ]] || exit 1
case "$EGRESS_DELETE_KIND/$EGRESS_DELETE_NAME" in
  pods/trial-egress-source-v1|pods/trial-egress-peer-v1|services/trial-egress-peer-v1|configmaps/trial-egress-probe-v1)
    EGRESS_DELETE_PATH="/api/v1/namespaces/codex-trial/$EGRESS_DELETE_KIND/$EGRESS_DELETE_NAME" ;;
  ciliumnetworkpolicies/trial-namespace-egress-v1)
    EGRESS_DELETE_PATH="/apis/cilium.io/v2/namespaces/codex-trial/$EGRESS_DELETE_KIND/$EGRESS_DELETE_NAME" ;;
  *) exit 1 ;;
esac
printf '{"apiVersion":"v1","kind":"DeleteOptions","preconditions":{"uid":"%s"},"propagationPolicy":"Foreground"}\n' "$EGRESS_DELETE_UID" \
  | timeout --signal=TERM --kill-after=2s 15s sudo -n /usr/local/bin/k3s kubectl --request-timeout=10s \
      delete --raw "$EGRESS_DELETE_PATH" -f -
