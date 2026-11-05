# #68 dev-VM stages: shared constants and read helpers (sourced, never run on its own). Development VM only.
# Nothing here changes state.
NODE=anxious-lookout
K3S=/usr/local/bin/k3s
SECCOMP_DIR=/var/lib/kubelet/seccomp/profiles
CONTROLLER_INPUTS=/etc/anxious-lookout/issue16/controller/inputs.json
RUNNER_SELECTOR=anxious.io/managed=user-runner

k() { timeout 30 "$K3S" kubectl "$@"; }
# psql as the database superuser inside postgres-0; statements on stdin so every result row prints.
# -X: never read a psql start-up file in the container (review minor, V0).
sql() { printf '%s\n' "$1" | timeout 60 "$K3S" kubectl -n m2-hosting exec -i postgres-0 -- \
  psql -X -U postgres -d hosting -Atq -v ON_ERROR_STOP=1 -f - 2>/dev/null; }
EVIDENCE=/root/native68/evidence
IMAGES=/home/opc/native68-images
# Image ID as containerd/CRI sees it (the config digest), for the chain check.
image_id() { timeout 30 "$K3S" crictl inspecti -o go-template --template '{{.status.id}}' "$1" 2>/dev/null; }
