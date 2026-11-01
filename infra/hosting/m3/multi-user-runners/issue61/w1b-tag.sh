#!/bin/sh
# #61 dev-VM gate W1b: give the three imported 61-5e73bc4 images their repository digest references in k3s
# containerd, exactly as infra/hosting/README.md does after an import (never `images import --digests`):
#   ctr -n k8s.io images tag docker.io/library/NAME:61-5e73bc4 docker.io/library/NAME@sha256:<manifest>
# then `crictl inspecti NAME@sha256:<manifest>` must resolve to the host image ID. W1 imported the images but
# skipped this tag, so a Pod pinned to NAME@sha256:… could not start (ErrImageNeverPull, W2 rc 5).
# Run once by the user as root in the guest after review cleared this SHA-256. Adds references only: no
# import, no Pod/Deployment/DB change. Safe to run again: a reference that already resolves to the expected ID
# is skipped.
# rc: 0 every reference resolves to its host ID; 3 precheck refused (tag missing or its manifest differs),
#     nothing tagged; 5 a tag failed; 8 tagged but a reference does not resolve to the expected ID.
set -u
umask 077
here=$(dirname "$0")
. "$here/lib.sh"
trap 'exit 130' INT TERM
# name|manifest digest (W1 chain, #61 comment 6559508981)|host image ID
WANT="anxious-dev-runtime|sha256:eb3a18acd1b2856f6a95c7686c0c0ee4e5194b6237266b3443664f37feb3377c|sha256:80af8c59f978f7a7d59d02f71985f3d456ce2e8011ed2d534395297188830771
anxious-dev-runner|sha256:067c77de5d6b3673ece9b243f7753a9184a007cd44ab0ea1c93dc3044458a5e4|sha256:4cac37e2173d5aec7b351e05c2977f2e0df76670f3befb91ce54306d2444e8ad
anxious-dev-shell-executor|sha256:9c87d626cdf6798db5bc5a562cc68af1b13a36c8f90d3738e19c6553e7cf4d12|sha256:49b449cb31c5b344f876a9d9aee2d97dd3dcae906c3324bc0eaf00678dd6b883"
refuse() { echo "precheck: $1; nothing tagged"; echo 'result rc=3'; exit 3; }
fail() { echo "$1"; echo "result rc=$2"; exit "$2"; }
resolve() { timeout 30 "$K3S" crictl inspecti -o go-template --template '{{.status.id}}' "$1" 2>/dev/null; }

[ "$(id -u)" = 0 ] || refuse 'not root'
# Order: only after the W2 rollback restored the api. A Deployment still pointing at a 61-5e73bc4 digest would
# start by itself as soon as its reference resolves, outside any reviewed step.
api=$(k -n m2-hosting get deployment api -o jsonpath='{.spec.template.spec.containers[?(@.name=="api")].image}') \
  || refuse 'api Deployment lookup failed'
case "$api" in *anxious-dev-*@sha256:eb3a18ac*|*anxious-dev-*@sha256:067c77de*|'') refuse "api Deployment image is ${api:-unknown}; roll W2 back first";; esac
echo "api Deployment image $api"
listing=$(timeout 30 "$K3S" ctr -n k8s.io images ls 2>/dev/null) || refuse 'ctr images ls failed'
# Every source tag must exist with exactly the W1 manifest before anything is tagged.
echo "$WANT" | while IFS='|' read -r name digest id; do
  got=$(echo "$listing" | awk -v r="docker.io/library/$name:61-5e73bc4" '$1 == r {print $3}')
  [ "$got" = "$digest" ] || { echo "precheck: $name:61-5e73bc4 has manifest ${got:-none}, want $digest; nothing tagged"; exit 3; }
done || { echo 'result rc=3'; exit 3; }

echo "$WANT" | while IFS='|' read -r name digest id; do
  ref="docker.io/library/$name@$digest"
  if [ "$(resolve "$ref")" = "$id" ]; then echo "skip $ref (resolves to $id)"; continue; fi
  timeout 30 "$K3S" ctr -n k8s.io images tag "docker.io/library/$name:61-5e73bc4" "$ref" >/dev/null 2>&1 \
    || { echo "tag of $ref failed"; exit 5; }
  echo "tagged $ref"
done || fail 'stopped at the failed tag' 5

bad=
for line in $WANT; do
  name=$(echo "$line" | cut -d'|' -f1) digest=$(echo "$line" | cut -d'|' -f2) id=$(echo "$line" | cut -d'|' -f3)
  got=$(resolve "docker.io/library/$name@$digest")
  echo "resolve docker.io/library/$name@$digest -> ${got:-none} (host $id)"
  [ "$got" = "$id" ] || bad="$bad $name"
done
[ -z "$bad" ] || fail "postcheck failed:$bad" 8
echo 'result rc=0'
