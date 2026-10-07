#!/usr/bin/env bash
# Emit only: strategic merge modifies a single named container image field.
set -euo pipefail
workload=${1:?Use api or web}
[[ "$workload" == api || "$workload" == web ]] || exit 1
: "${IMAGE:?Set exact verified imported immutable image reference}"
[[ "$IMAGE" =~ ^[a-zA-Z0-9._/:-]+@sha256:[0-9a-f]{64}$ ]] || exit 1
printf '{"spec":{"template":{"spec":{"containers":[{"name":"%s","image":"%s"}]}}}}\n' "$workload" "$IMAGE"
