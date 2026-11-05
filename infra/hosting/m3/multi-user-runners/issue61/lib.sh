# #61 dev-VM gate: shared constants and read helpers for w0/w1/w2 (sourced, never run on its own).
# Fixed identities come from the 2026-11-01 read-only VM checks (#61 comments). Nothing here changes state.
NODE=anxious-lookout
K3S=/usr/local/bin/k3s
UNITS='issue16-controller.timer issue16-controller.service issue16-observer.timer issue16-observer.service'
OWNER_0048=00000000-0000-4000-8000-000000000048
OWNER_5802=00000000-0000-4000-8000-000000005802
OWNER_5804=00000000-0000-4000-8000-000000005804
# owner|Pod UID|expected state (failed: Pod Failed; runner-dead: Running, runner container terminated; running)
FIXTURES="$OWNER_0048|3bb0fe46-af68-4d3a-9c67-35bce5f44cf7|failed
$OWNER_5802|4107e603-0c33-4fb3-9fc3-911212a716fb|runner-dead
$OWNER_5804|b9bbc857-cd36-4a7b-89b5-1ca7c7d3b61b|running"
OWNERS_SQL="'$OWNER_0048','$OWNER_5802','$OWNER_5804'"
ARCHIVES=/home/opc/issue61-images
IMAGE_REFS='docker.io/library/anxious-dev-runtime:61-5e73bc4
docker.io/library/anxious-dev-runner:61-5e73bc4
docker.io/library/anxious-dev-shell-executor:61-5e73bc4'

k() { timeout 30 "$K3S" kubectl "$@"; }
pod_name() { echo "runner-$(echo "$1" | tr -d -)"; }
unit_state() {  # "LoadState ActiveState"; fails if systemctl fails
  v=$(timeout 10 systemctl show -p LoadState -p ActiveState "$1" 2>/dev/null) || return 1
  echo "$(echo "$v" | sed -n 's/^LoadState=//p') $(echo "$v" | sed -n 's/^ActiveState=//p')"
}
# psql as the database superuser inside postgres-0; statements on stdin so every result row prints.
sql() { printf '%s\n' "$1" | timeout 60 "$K3S" kubectl -n m2-hosting exec -i postgres-0 -- \
  psql -U postgres -d hosting -Atq -v ON_ERROR_STOP=1 -f - 2>/dev/null; }
ctr_has() {
  list=$(timeout 30 "$K3S" ctr -n k8s.io images ls -q 2>/dev/null) || return 2
  echo "$list" | grep -qxF "$1"
}
