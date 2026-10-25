#!/usr/bin/env bash
# PM-only after exact image/procedure review; never run against arbitrary paths.
set -euo pipefail
[[ ( $# == 2 || $# == 3 ) && ( $1 == initialize || $1 == verify ) ]] || exit 2
mode=$1
image=$2
# arm64 is production; amd64 is only the x86_64 development VM (infra/k3s/DEV-VM.md).
architecture=${3-arm64}  # an explicit empty value refuses below
[[ $architecture == arm64 || $architecture == amd64 ]] || exit 2
[[ $image =~ ^sha256:[a-f0-9]{64}$ || $image =~ ^[a-zA-Z0-9./:_-]+@sha256:[a-f0-9]{64}$ ]] || exit 2
# OS hostname differs from the configured Kubernetes node name. Bind the local
# host's address to the observed Node through the local k3s kubeconfig; never
# inherit a caller's KUBECONFIG/context. All checks precede any Docker operation.
[[ $(hostname -s) == simple ]]
ip -json address show | jq -se 'length == 1 and (.[0] | any(.[].addr_info[]; .family == "inet" and .local == "10.0.0.107"))' >/dev/null
sudo -n /usr/local/bin/k3s kubectl --kubeconfig /etc/rancher/k3s/k3s.yaml \
  --request-timeout=10s get node anxious-lookout -o json | jq -se 'length == 1 and (.[0] |
  .kind == "Node" and .metadata.name == "anxious-lookout" and
  .metadata.labels["kubernetes.io/hostname"] == "anxious-lookout" and
  any(.status.addresses[]; .type == "InternalIP" and .address == "10.0.0.107") and
  any(.status.addresses[]; .type == "Hostname" and .address == "simple"))' >/dev/null
[[ $(getenforce) == Enforcing ]]
[[ $(docker image inspect --format '{{.Os}}/{{.Architecture}} {{.Config.User}}' "$image") == "linux/$architecture 10001:10001" ]]
root=/var/lib/anxious-lookout/runner-rehearsal/a9617ace-fa4b-4bbc-b70b-2188f0efe3e6
for leaf in "$root/native" "$root/control"; do
  # The root-owned 0700 ancestors deliberately prohibit opc traversal. Elevate
  # only these read-only checks; Docker's daemon performs the bind mounts.
  resolved=$(sudo -n readlink -e "$leaf")
  [[ $resolved == "$leaf" ]]
  ownership=$(sudo -n stat -c '%u:%g:%a' "$leaf")
  [[ $ownership == '10001:10001:700' ]]
  context=$(sudo -n stat -c '%C' "$leaf")
  [[ $context == *:container_file_t:* ]]
  sudo -n matchpathcon -V "$leaf"
done
docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
  --user 10001:10001 --tmpfs /tmp:rw,nosuid,nodev \
  --mount "type=bind,src=$root/native,dst=/state" \
  --mount "type=bind,src=$root/control,dst=/control" \
  --entrypoint python "$image" -B -m ops.runner_rehearsal_init "--$mode"
