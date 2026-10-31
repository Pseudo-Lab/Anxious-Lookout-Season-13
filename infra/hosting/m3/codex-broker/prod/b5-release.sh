#!/bin/sh
# #72 B5: move the api Deployment and the controller/observer worker scripts to the images step A built from
# the #72 merge. Reversible: the api images are patched back and the scripts reinstalled from evidence.
# Narrow on purpose: since the running api (d026b77, node read 3) the release has no migration, no settings
# change and no rollout values change beyond the retired supply keys; the live Deployment also carries the 10-25
# IP/domain change, which a re-render from the 10-21 values would revert. So only image references change.
# Not touched: the supply units and supply.sh (E1-E3 retire them), worker.py (the old wrapper still runs the
# controller/observer modules), restore.sh (no restore code changed since d890447).
# Run as root, once, after C, with controller and observer stopped (the B5+D window), after review cleared this
# SHA-256 with the @@ values.
# rc: 0 done and verified; 2 placeholders left; 3 precheck refused, nothing changed; 5 a change failed midway
#     (stop, report); 6 evidence failed, nothing changed; 8 changed but a postcheck failed.
set -u
RELEASE_SHA=@@RELEASE_SHA@@              # the #72 merge commit, 40 hex; A's APP_GIT_SHA
API_IMAGE=@@API_IMAGE_REF@@              # A's runtime image, <repo>@sha256:<64 hex>
WORKER_IMAGE_ID=@@PROVISIONER_IMAGE_ID@@ # A's provisioner image in the Docker store, sha256:<64 hex>
OLD_API_IMAGE=docker.io/library/anxious-s13-issue36-runtime@sha256:7998b73da0f267e59b91da36a52ba571992365fd0f4056b59ce9640295978e67
OLD_WORKER_ID=sha256:1c3ce2683832a187ef207abe4daa7989589a996cde8214ce374237fa94ea81e6  # node read 2
# #59 gate: a release that changes the runner Pod manifest refuses live Pods of the previous one (KubePods._pod),
# so no runner Pod may be Pending or Running. Decision 3: the #66 owner's issue28 Pod stays; after B5 the api and
# workers refuse it as "requires reconciliation" (fail closed, never deleted) until the #66 runbook.
EXEMPT_PODS='runner-3cb453bfd87040dcb7e13a58aa0cbaf2'
K='/usr/local/bin/k3s kubectl'
BIN=/usr/local/lib/anxious-lookout/issue16
SCRIPTS='controller.sh observer.sh'
UNITS='issue16-controller.timer issue16-controller.service issue16-observer.timer issue16-observer.service'
EV=/root/issue72/evidence/b5
case "$RELEASE_SHA$API_IMAGE$WORKER_IMAGE_ID" in *@@*) echo 'placeholders not filled'; exit 2;; esac
refuse() { echo "precheck: $1; nothing changed"; exit 3; }
echo "$RELEASE_SHA" | grep -qxE '[0-9a-f]{40}' || refuse 'RELEASE_SHA is not a full commit'
echo "$API_IMAGE" | grep -qxE '[a-z0-9./_-]+@sha256:[0-9a-f]{64}' || refuse 'API_IMAGE is not a digest reference'
echo "$WORKER_IMAGE_ID" | grep -qxE 'sha256:[0-9a-f]{64}' || refuse 'WORKER_IMAGE_ID is not an image ID'
[ "$API_IMAGE" != "$OLD_API_IMAGE" ] && [ "$WORKER_IMAGE_ID" != "$OLD_WORKER_ID" ] || refuse 'new image equals the old one'
[ ! -e "$EV" ] || refuse "$EV already exists"

# 1. Workers stopped: status first, then the named properties.
for u in $UNITS; do
  v=$(timeout 10 systemctl show -p LoadState -p ActiveState "$u" 2>/dev/null) || refuse "systemctl show $u failed"
  v="$(echo "$v" | sed -n 's/^LoadState=//p') $(echo "$v" | sed -n 's/^ActiveState=//p')"
  case "$v" in 'loaded inactive'|'loaded failed') ;; *) refuse "$u is $v (stop the controller and observer first)";; esac
done
# 2. The #59 gate: no Pending or Running runner Pod except the exempt one.
pods=$(timeout 30 $K -n codex-runner get pods -l anxious.io/managed=user-runner \
  --field-selector=status.phase!=Failed,status.phase!=Succeeded -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null) \
  || refuse 'runner pod lookup failed'
live=
for name in $pods; do
  case " $EXEMPT_PODS " in *" $name "*) echo "exempt (decision 3): $name stays; the new api/workers refuse it until the #66 runbook";; *) live="$live $name";; esac
done
[ -z "$live" ] || refuse "runner Pods still live (#59 gate):$live"
# 3. The new images are on the node and carry the reviewed release.
i=$(timeout 30 docker image inspect --format '{{.Id}}' "$WORKER_IMAGE_ID" 2>/dev/null) || refuse 'worker image lookup failed'
[ "$i" = "$WORKER_IMAGE_ID" ] || refuse 'worker image not in the Docker store'
r=$(timeout 60 docker run --rm --pull=never --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
  --entrypoint cat "$WORKER_IMAGE_ID" /app/release.json 2>/dev/null) || refuse 'worker image release lookup failed'
printf '%s' "$r" | python3 -I -c 'import json, sys; assert json.load(sys.stdin)["sha"] == sys.argv[1]' "$RELEASE_SHA" 2>/dev/null \
  || refuse 'worker image is not the reviewed release'
timeout 30 /usr/local/bin/k3s crictl inspecti "$API_IMAGE" > /dev/null 2>&1 || refuse 'api image not in containerd (imagePullPolicy Never)'
# 4. The live Deployment: exactly the api container and the runner-pool-input initContainer, both on the old image.
dep=$(timeout 30 $K -n m2-hosting get deployment api -o json 2>/dev/null) || refuse 'api deployment lookup failed'
fence=$(printf '%s' "$dep" | python3 -I -c '
import json, sys
d = json.load(sys.stdin)
s = d["spec"]["template"]["spec"]
assert [c["name"] for c in s["containers"]] == ["api"]
assert [c["name"] for c in s.get("initContainers", [])] == ["runner-pool-input"]
assert s["containers"][0]["image"] == s["initContainers"][0]["image"] == sys.argv[1]
print(d["metadata"]["uid"] + "|" + d["metadata"]["resourceVersion"])
' "$OLD_API_IMAGE" 2>/dev/null) || refuse 'api deployment is not the reviewed baseline (containers or images differ)'
uid=${fence%%|*}; rv=${fence#*|}
# 5. Each worker script names the old image exactly once and the new one nowhere.
for s in $SCRIPTS; do
  f=$BIN/$s
  [ -f "$f" ] && [ ! -L "$f" ] || refuse "$f is not a regular file"
  st=$(stat -c '%u %g %a' "$f" 2>/dev/null) || refuse "stat $f failed"
  case "$st" in '0 0 '*) ;; *) refuse "$f is not root-owned";; esac
  n=$(python3 -I -c 'import sys; t = open(sys.argv[1]).read(); print(t.count(sys.argv[2]), t.count(sys.argv[3]))' "$f" "$OLD_WORKER_ID" "$WORKER_IMAGE_ID" 2>/dev/null) \
    || refuse "read $f failed"
  [ "$n" = '1 0' ] || refuse "$f does not name the old worker image exactly once"
done
echo "precheck ok: workers stopped, no live runner Pod, new images present, api deployment $uid at $rv, worker scripts on the old image"

# 6. Evidence before any change, as E3: copy, compare with the source, record mode/owner, manifest, verify.
install -d -m 0700 "$EV" || { echo 'evidence directory failed; nothing changed'; exit 6; }
( umask 077; printf '%s\n' "$dep" > "$EV/api-deployment-before.json" ) \
  && printf '%s\n' "$dep" | cmp -s - "$EV/api-deployment-before.json" || { echo 'evidence deployment copy failed; nothing changed'; exit 6; }
: > "$EV/MODES" && chmod 0600 "$EV/MODES" || { echo 'evidence MODES failed; nothing changed'; exit 6; }
for s in $SCRIPTS; do
  cp -p "$BIN/$s" "$EV/$s" && cmp -s "$BIN/$s" "$EV/$s" \
    && [ "$(stat -c '%a %u %g' "$EV/$s")" = "$(stat -c '%a %u %g' "$BIN/$s")" ] && stat -c '%a %U %G %n' "$BIN/$s" >> "$EV/MODES" \
    || { echo "evidence copy failed at $s; nothing changed"; exit 6; }
done
( cd "$EV" && umask 077 && sha256sum $SCRIPTS api-deployment-before.json MODES > MANIFEST.sha256 ) \
  && [ "$(grep -c . "$EV/MANIFEST.sha256")" = 4 ] && [ "$(grep -c . "$EV/MODES")" = 2 ] \
  && ( cd "$EV" && sha256sum -c --quiet MANIFEST.sha256 ) || { echo 'evidence manifest failed; nothing changed'; exit 6; }
echo 'evidence ok: 2 scripts + MODES + deployment copied, compared and checksum-verified'

# 7. Change. Stop at the first failure.
rc=0
for s in $SCRIPTS; do
  f=$BIN/$s; tmp=$BIN/.$s.b5
  { sed "s|$OLD_WORKER_ID|$WORKER_IMAGE_ID|" "$f" > "$tmp" && chmod "$(stat -c '%a' "$f")" "$tmp" && chown 0:0 "$tmp" \
    && [ "$(python3 -I -c 'import sys; t = open(sys.argv[1]).read(); print(t.count(sys.argv[2]), t.count(sys.argv[3]))' "$tmp" "$OLD_WORKER_ID" "$WORKER_IMAGE_ID")" = '0 1' ] \
    && mv -f "$tmp" "$f"; } || { rm -f "$tmp"; echo "worker script update failed at $s"; rc=5; break; }
done
# JSON Patch with tests: the Deployment must still be the one read above (uid, resourceVersion, both images).
patch="[{\"op\":\"test\",\"path\":\"/metadata/uid\",\"value\":\"$uid\"},{\"op\":\"test\",\"path\":\"/metadata/resourceVersion\",\"value\":\"$rv\"},\
{\"op\":\"test\",\"path\":\"/spec/template/spec/containers/0/image\",\"value\":\"$OLD_API_IMAGE\"},\
{\"op\":\"test\",\"path\":\"/spec/template/spec/initContainers/0/image\",\"value\":\"$OLD_API_IMAGE\"},\
{\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/image\",\"value\":\"$API_IMAGE\"},\
{\"op\":\"replace\",\"path\":\"/spec/template/spec/initContainers/0/image\",\"value\":\"$API_IMAGE\"}]"
[ $rc = 0 ] && { timeout 30 $K -n m2-hosting patch deployment api --type=json -p "$patch" > /dev/null 2>&1 || { echo 'api deployment patch failed'; rc=5; }; }
[ $rc = 0 ] && { timeout 320 $K -n m2-hosting rollout status deployment/api --timeout=300s > /dev/null 2>&1 || { echo 'api rollout did not complete'; rc=5; }; }

# 8. Postcheck: lookups status-checked; a failed lookup is a failed check.
if [ $rc = 0 ]; then
  bad=
  img=$(timeout 30 $K -n m2-hosting get deployment api \
    -o jsonpath='{.spec.template.spec.containers[0].image}|{.spec.template.spec.initContainers[0].image}' 2>/dev/null) || img=lookup-failed
  [ "$img" = "$API_IMAGE|$API_IMAGE" ] || bad="$bad deployment-images"
  ap=$(timeout 30 $K -n m2-hosting get pods -l app=hosting-api \
    -o jsonpath='{range .items[*]}{.metadata.name}|{.status.phase}|{.status.containerStatuses[?(@.name=="api")].ready}|{.status.containerStatuses[?(@.name=="api")].imageID}{"\n"}{end}' 2>/dev/null) \
    || ap=lookup-failed
  echo "api pods: $(echo "$ap" | cut -d'|' -f1-3 | tr '\n' ' ')"
  [ -n "$ap" ] || bad="$bad api-pods"
  for l in $ap; do
    case "$l" in *"|Running|true|"*"@${API_IMAGE#*@}") ;; *) bad="$bad api-pods"; break;; esac
  done
  r=$(timeout 30 $K -n m2-hosting exec deploy/api -c api -- cat /app/release.json 2>/dev/null) || r=
  printf '%s' "$r" | python3 -I -c 'import json, sys; assert json.load(sys.stdin)["sha"] == sys.argv[1]' "$RELEASE_SHA" 2>/dev/null \
    || bad="$bad api-release"
  for s in $SCRIPTS; do
    n=$(python3 -I -c 'import sys; t = open(sys.argv[1]).read(); print(t.count(sys.argv[2]), t.count(sys.argv[3]))' "$BIN/$s" "$OLD_WORKER_ID" "$WORKER_IMAGE_ID" 2>/dev/null) || n=
    [ "$n" = '0 1' ] || bad="$bad $s"
  done
  [ -z "$bad" ] && echo "release ok: api and workers on $RELEASE_SHA" || { echo "postcheck failed:$bad"; rc=8; }
fi
# D requires this record (and rechecks the live state): written only when every step and the postcheck passed.
if [ $rc = 0 ]; then
  ( umask 077; date -u +%Y-%m-%dT%H:%M:%SZ > "$EV/done" ) || { echo 'completion record failed'; rc=8; }
fi
echo "rollback: $K -n m2-hosting patch deployment api --type=json -p '[{\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/image\",\"value\":\"$OLD_API_IMAGE\"},{\"op\":\"replace\",\"path\":\"/spec/template/spec/initContainers/0/image\",\"value\":\"$OLD_API_IMAGE\"}]'; for each script in $EV: install -m <its mode> -o root -g root $EV/<script> $BIN/<script>. Workers stay stopped either way."
echo "result rc=$rc"
exit $rc
