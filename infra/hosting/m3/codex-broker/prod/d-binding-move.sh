#!/bin/sh
# #72 D: move one owner's binding to A's runner and shell-executor images in one CAS (decision 6), with
# ops.runner_binding_image from A's provisioner image (CUTOVER.md section 6, rollout README "Existing binding images").
#   d-binding-move.sh report <owner-uuid>   read-only: fence + plan, inputs, the tool's report; review the report
#   d-binding-move.sh apply <owner-uuid>    the reviewed report's CAS; the tool changes only the two image digests
#   d-binding-move.sh close                 ends the window: unlinks the admin DSN file
# Window: after B5, controller and observer stopped, entry off. The user creates the short-lived admin DSN on
# the node at $W/private/admin-dsn (directory 0700, file 0600, root; the database address the workers use today,
# --network=host as their units). It is never read or printed here.
# rc: 0 done and verified; 2 placeholders left; 3 precheck or tool refused, nothing changed;
#     5 apply failed or was ambiguous (inspect the after-plan, never repeat); 8 applied but a postcheck failed.
set -u
WORKER_IMAGE_ID=@@PROVISIONER_IMAGE_ID@@  # A's provisioner image in the Docker store, sha256:<64 hex>
RUNNER_IMAGE=@@RUNNER_IMAGE_REF@@         # A's runner image, <repo>@sha256:<64 hex>
EXECUTOR_IMAGE=@@EXECUTOR_IMAGE_REF@@     # A's shell-executor image, <repo>@sha256:<64 hex>
RELEASE_SHA=@@RELEASE_SHA@@               # the #72 merge commit (as B5)
API_IMAGE=@@API_IMAGE_REF@@               # A's runtime image (as B5)
OLD_WORKER_ID=sha256:1c3ce2683832a187ef207abe4daa7989589a996cde8214ce374237fa94ea81e6  # node read 2 (as B5)
EXEMPT_OWNERS='3cb453bf-d870-40dc-b7e1-3a58aa0cbaf2'  # decision 3: moved in the #66 runbook
K='/usr/local/bin/k3s kubectl'
BIN=/usr/local/lib/anxious-lookout/issue16
UNITS='issue16-controller.timer issue16-controller.service issue16-observer.timer issue16-observer.service'
W=/root/issue72/d
action=${1:-}; owner=${2:-}
case "$WORKER_IMAGE_ID$RUNNER_IMAGE$EXECUTOR_IMAGE$RELEASE_SHA$API_IMAGE" in *@@*) echo 'placeholders not filled'; exit 2;; esac
refuse() { echo "precheck: $1; nothing changed"; exit 3; }
echo "$WORKER_IMAGE_ID" | grep -qxE 'sha256:[0-9a-f]{64}' || refuse 'WORKER_IMAGE_ID is not an image ID'
for i in "$RUNNER_IMAGE" "$EXECUTOR_IMAGE" "$API_IMAGE"; do echo "$i" | grep -qxE '[a-z0-9./_-]+@sha256:[0-9a-f]{64}' || refuse 'image is not a digest reference'; done
echo "$RELEASE_SHA" | grep -qxE '[0-9a-f]{40}' || refuse 'RELEASE_SHA is not a full commit'

if [ "$action" = close ]; then
  [ -z "$owner" ] || refuse 'close takes no owner'
  [ -e "$W/private/admin-dsn" ] || [ -L "$W/private/admin-dsn" ] || { echo 'admin DSN already absent'; echo 'result rc=0'; exit 0; }
  unlink "$W/private/admin-dsn" || { echo 'unlink admin DSN failed'; echo 'result rc=5'; exit 5; }
  [ ! -e "$W/private/admin-dsn" ] && [ ! -L "$W/private/admin-dsn" ] && { echo 'admin DSN removed'; echo 'result rc=0'; exit 0; }
  echo 'postcheck failed: admin DSN'; echo 'result rc=8'; exit 8
fi
case "$action" in report|apply) ;; *) echo 'usage: d-binding-move.sh report|apply <owner-uuid> | close'; exit 3;; esac
echo "$owner" | grep -qxE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' || refuse 'owner is not a canonical UUID'
case " $EXEMPT_OWNERS " in *" $owner "*) refuse "$owner is exempt (decision 3; the #66 runbook moves it)";; esac
O=$W/$owner
q() { timeout 30 $K -n m2-hosting exec postgres-0 -- psql -U postgres -d hosting -At -v ON_ERROR_STOP=1 -c "$1" 2>/dev/null; }

# 1. Window: B5 completed and still in place, workers stopped, this owner's Pod absent, the admin DSN in place
#    (mode/owner only). B5 writes evidence/b5/done only after its postcheck passed; a B5 that stopped midway
#    (scripts changed, api not) has no record, and the live state is rechecked anyway.
[ -f /root/issue72/evidence/b5/done ] && [ ! -L /root/issue72/evidence/b5/done ] || refuse 'B5 not completed (no evidence/b5/done)'
for s in controller.sh observer.sh; do
  n=$(python3 -I -c 'import sys; t = open(sys.argv[1]).read(); print(t.count(sys.argv[2]), t.count(sys.argv[3]))' "$BIN/$s" "$OLD_WORKER_ID" "$WORKER_IMAGE_ID" 2>/dev/null) \
    || refuse "read $BIN/$s failed"
  [ "$n" = '0 1' ] || refuse "B5 state: $s is not on the new worker image"
done
img=$(timeout 30 $K -n m2-hosting get deployment api \
  -o jsonpath='{.spec.template.spec.containers[0].image}|{.spec.template.spec.initContainers[0].image}' 2>/dev/null) \
  || refuse 'api deployment lookup failed'
[ "$img" = "$API_IMAGE|$API_IMAGE" ] || refuse 'B5 state: the api deployment is not on the new image'
r=$(timeout 30 $K -n m2-hosting exec deploy/api -c api -- cat /app/release.json 2>/dev/null) || refuse 'api release lookup failed'
printf '%s' "$r" | python3 -I -c 'import json, sys; assert json.load(sys.stdin)["sha"] == sys.argv[1]' "$RELEASE_SHA" 2>/dev/null \
  || refuse 'B5 state: the running api is not the reviewed release'
for u in $UNITS; do
  v=$(timeout 10 systemctl show -p LoadState -p ActiveState "$u" 2>/dev/null) || refuse "systemctl show $u failed"
  v="$(echo "$v" | sed -n 's/^LoadState=//p') $(echo "$v" | sed -n 's/^ActiveState=//p')"
  case "$v" in 'loaded inactive'|'loaded failed') ;; *) refuse "$u is $v (stop the controller and observer first)";; esac
done
pod=runner-$(echo "$owner" | tr -d -)
g=$(timeout 30 $K -n codex-runner get pod "$pod" --ignore-not-found -o name 2>/dev/null) || refuse 'runner pod lookup failed'
[ -z "$g" ] || refuse "$pod exists"
s=$(stat -c '%u %a %F' "$W/private" 2>/dev/null) || refuse "$W/private missing"
[ "$s" = '0 700 directory' ] && [ ! -L "$W/private" ] || refuse "$W/private is not a root 0700 directory"
s=$(stat -c '%u %a %h %F' "$W/private/admin-dsn" 2>/dev/null) || refuse 'admin DSN file missing'
[ "$s" = '0 600 1 regular file' ] && [ ! -L "$W/private/admin-dsn" ] || refuse 'admin DSN is not a root 0600 regular file'
i=$(timeout 30 docker image inspect --format '{{.Id}}' "$WORKER_IMAGE_ID" 2>/dev/null) && [ "$i" = "$WORKER_IMAGE_ID" ] \
  || refuse 'worker image not in the Docker store'
# 2. The registry fence: stopped, no Pod.
f=$(q "select state_id||'|'||phase||'|'||generation||'|'||revision||'|'||coalesce(pod_uid::text,'-') from runner_runtime.registry where owner_id='$owner'") \
  || refuse 'registry lookup failed'
echo "$f" | grep -qxE '[0-9a-f-]{36}\|idle_stopped\|[0-9]+\|[0-9]+\|-' || refuse "registry row is not idle_stopped without a Pod (${f:-absent})"
IFS='|' read -r state phase generation revision podu <<EOF
$f
EOF
tool() { # action, report mount option
  timeout 120 docker run --rm --pull=never --user 0:0 --read-only --cap-drop ALL --security-opt no-new-privileges \
    --network=host --mount "type=bind,src=$W/private,dst=/private,readonly" \
    --mount "type=bind,src=$O/inputs,dst=/inputs,readonly" --mount "type=bind,src=$O/report,dst=/reports$2" \
    -e ADMIN_DATABASE_URL_FILE=/private/admin-dsn --entrypoint python "$WORKER_IMAGE_ID" \
    -B -m ops.runner_binding_image "$1" --inputs /inputs/binding-image.json --report /reports/binding-image.json 2>&1
}

if [ "$action" = report ]; then
  [ ! -e "$O" ] || refuse "$O already exists"
  plan=$(q "select plan::text from runner_bindings.bindings where owner_id='$owner'") && [ -n "$plan" ] || refuse 'binding lookup failed'
  # v2 plans only (production runner Pods carry the shell executor); not already on the new images.
  printf '%s' "$plan" | python3 -I -c '
import json, sys
p = json.load(sys.stdin)
assert p.get("version") == 2 and type(p.get("executor")) is dict
assert (p["image"], p["executor"]["image"]) != (sys.argv[1], sys.argv[2])
' "$RUNNER_IMAGE" "$EXECUTOR_IMAGE" 2>/dev/null || refuse 'binding plan is not v2, or already on the new images'
  # The plan digest is computed inside the new worker image (CUTOVER section 6), never from a file hash.
  digest=$(printf '%s' "$plan" | timeout 60 docker run --rm -i --pull=never --network none --read-only --cap-drop ALL \
    --security-opt no-new-privileges --entrypoint python "$WORKER_IMAGE_ID" -I -c \
    'import json, sys; sys.path.insert(0, "/app"); from ops.runner_binding_image import plan_digest; print(plan_digest(json.load(sys.stdin)))' 2>/dev/null) \
    && echo "$digest" | grep -qxE '[0-9a-f]{64}' || refuse 'plan digest failed'
  { install -d -m 0700 "$O" "$O/inputs" "$O/report" && ( umask 077; printf '%s\n' "$plan" > "$O/before-plan.json" \
      && python3 -I -c '
import json, sys
o, s, g, r, d, n, e = sys.argv[1:]
json.dump({"owner": o, "state": s, "generation": int(g), "revision": int(r), "oldPlanSha256": d,
           "newImage": n, "executorImage": e}, sys.stdout)
' "$owner" "$state" "$generation" "$revision" "$digest" "$RUNNER_IMAGE" "$EXECUTOR_IMAGE" > "$O/inputs/binding-image.json" ); } \
    || { echo "writing $O failed; nothing changed in the database"; echo 'result rc=3'; exit 3; }
  out=$(tool report ''); t=$?
  echo "$out"
  [ $t = 0 ] && [ -f "$O/report/binding-image.json" ] || { echo 'tool refused the report; nothing changed'; echo 'result rc=3'; exit 3; }
  echo 'report for review (identities and image digests only):'
  cat "$O/report/binding-image.json"; echo
  echo "next: review it, then d-binding-move.sh apply $owner"
  echo 'result rc=0'; exit 0
fi

# apply
[ -f "$O/inputs/binding-image.json" ] && [ -f "$O/report/binding-image.json" ] || refuse 'no reviewed report for this owner'
[ ! -e "$O/applied" ] || refuse 'already applied (inspect the after-plan; never repeat)'
python3 -I -c '
import json, sys
i = json.load(open(sys.argv[1]))
assert [i["state"], str(i["generation"]), str(i["revision"])] == sys.argv[2:5]
assert (i["newImage"], i["executorImage"]) == (sys.argv[5], sys.argv[6])
' "$O/inputs/binding-image.json" "$state" "$generation" "$revision" "$RUNNER_IMAGE" "$EXECUTOR_IMAGE" 2>/dev/null \
  || refuse 'registry fence or images changed since the report'
rc=0
out=$(tool apply ',readonly') || rc=5
echo "$out"
[ $rc = 0 ] || { echo 'apply failed or ambiguous; inspect the after-plan, never repeat'; echo "result rc=$rc"; exit $rc; }
( umask 077; date -u +%Y-%m-%dT%H:%M:%SZ > "$O/applied" ) || echo 'applied marker not written'
bad=
a=$(q "select (plan::jsonb->>'image')||'|'||(plan::jsonb->'executor'->>'image') from runner_bindings.bindings where owner_id='$owner'") || a=lookup-failed
[ "$a" = "$RUNNER_IMAGE|$EXECUTOR_IMAGE" ] || bad="$bad plan-images"
f2=$(q "select state_id||'|'||phase||'|'||generation||'|'||revision||'|'||coalesce(pod_uid::text,'-') from runner_runtime.registry where owner_id='$owner'") || f2=lookup-failed
[ "$f2" = "$f" ] || bad="$bad registry"
[ -z "$bad" ] && echo "moved: $owner on the new runner and executor images" || { echo "postcheck failed:$bad"; rc=8; }
echo "rollback: a reverse move is a new report/apply with the images in $O/before-plan.json as newImage/executorImage"
echo "result rc=$rc"
exit $rc
