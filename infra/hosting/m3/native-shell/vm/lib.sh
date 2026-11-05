# #68 dev-VM stages: shared constants and read helpers (sourced, never run on its own). Development VM only.
# Nothing here changes state.
NODE=anxious-lookout
K3S=/usr/local/bin/k3s
SECCOMP_DIR=/var/lib/kubelet/seccomp/profiles
CONTROLLER_INPUTS=/etc/anxious-lookout/issue16/controller/inputs.json
RUNNER_SELECTOR=anxious.io/managed=user-runner

k() { timeout 30 "$K3S" kubectl "$@"; }
# psql as the database superuser inside postgres-0; statements on stdin so every result row prints.
sql() { printf '%s\n' "$1" | timeout 60 "$K3S" kubectl -n m2-hosting exec -i postgres-0 -- \
  psql -U postgres -d hosting -Atq -v ON_ERROR_STOP=1 -f - 2>/dev/null; }
