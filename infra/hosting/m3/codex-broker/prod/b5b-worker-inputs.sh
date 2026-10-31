#!/bin/sh
# #74 B5b: move the controller's inputs.json runner and shell-executor image references to the images step A built
# from the #72 merge. First-time bindings are published by the controller worker from this file (production, #72
# window record 2026-10-31 09:29Z); B5 moved the api and the worker images but left these references on the issue36
# runner / issue23 executor, so a new owner would receive a binding that cannot start under the #59 manifest.
# Only the two image strings change; every other key keeps its value, the file keeps its mode and owner.
# Run as root, once, with controller and observer stopped, after review cleared this SHA-256 with the @@ values.
# rc: 0 done and verified; 2 placeholders left; 3 precheck refused, nothing changed; 5 a change failed midway;
#     6 evidence failed, nothing changed; 8 changed but a postcheck failed.
set -u
RUNNER_IMAGE=@@RUNNER_IMAGE_REF@@       # A's runner image, <repo>@sha256:<64 hex>
EXECUTOR_IMAGE=@@EXECUTOR_IMAGE_REF@@   # A's shell-executor image, <repo>@sha256:<64 hex>
OLD_RUNNER_IMAGE=docker.io/library/anxious-s13-issue36-runner@sha256:0efaa9f741e04f1fc9d92544932139d39834de4c483ba1e73d3ec8aed3373f01
OLD_EXECUTOR_IMAGE=docker.io/library/anxious-s13-issue23-shell-executor@sha256:bae153febf222fbe2919cc50afd7c1564730b77bd0a45fbb036f567e5035d958
K='/usr/local/bin/k3s kubectl'
F=/etc/anxious-lookout/issue16/controller/inputs.json
UNITS='issue16-controller.timer issue16-controller.service issue16-observer.timer issue16-observer.service'
EV=/root/issue72/evidence/b5b
case "$RUNNER_IMAGE$EXECUTOR_IMAGE" in *@@*) echo 'placeholders not filled'; exit 2;; esac
refuse() { echo "precheck: $1; nothing changed"; exit 3; }
for i in "$RUNNER_IMAGE" "$EXECUTOR_IMAGE"; do echo "$i" | grep -qxE '[a-z0-9./_-]+@sha256:[0-9a-f]{64}' || refuse 'image is not a digest reference'; done
[ "$RUNNER_IMAGE" != "$OLD_RUNNER_IMAGE" ] && [ "$EXECUTOR_IMAGE" != "$OLD_EXECUTOR_IMAGE" ] || refuse 'new image equals the old one'
[ ! -e "$EV" ] || refuse "$EV already exists"

# 1. Workers stopped: status first, then the named properties.
for u in $UNITS; do
  v=$(timeout 10 systemctl show -p LoadState -p ActiveState "$u" 2>/dev/null) || refuse "systemctl show $u failed"
  v="$(echo "$v" | sed -n 's/^LoadState=//p') $(echo "$v" | sed -n 's/^ActiveState=//p')"
  case "$v" in 'loaded inactive'|'loaded failed') ;; *) refuse "$u is $v (stop the controller and observer first)";; esac
done
# 2. Both new images are in containerd (the binding's Pod pulls with imagePullPolicy Never).
for i in "$RUNNER_IMAGE" "$EXECUTOR_IMAGE"; do
  timeout 30 /usr/local/bin/k3s crictl inspecti "$i" > /dev/null 2>&1 || refuse "image not in containerd: ${i##*/}"
done
# 3. The file: root-owned regular file, exactly the old references, the new ones nowhere, nothing else looked at.
[ -f "$F" ] && [ ! -L "$F" ] || refuse "$F is not a regular file"
st=$(stat -c '%u %g %a' "$F" 2>/dev/null) || refuse "stat $F failed"
case "$st" in '0 0 '*) ;; *) refuse "$F is not root-owned";; esac
mode=${st##* }
python3 -I - "$F" "$OLD_RUNNER_IMAGE" "$OLD_EXECUTOR_IMAGE" "$RUNNER_IMAGE" "$EXECUTOR_IMAGE" <<'PY' 2>/dev/null || refuse 'inputs.json is not the reviewed baseline (image, executor.image)'
import json, sys
path, old_r, old_e, new_r, new_e = sys.argv[1:]
text = open(path).read()
v = json.loads(text)
assert v['image'] == old_r and v['executor']['image'] == old_e
assert text.count(old_r) == 1 and text.count(old_e) == 1 and new_r not in text and new_e not in text
PY
echo "precheck ok: workers stopped, new images in containerd, $F ($mode root) on the old runner and executor images"

# 4. Evidence before any change, as E3/B5: copy, compare with the source, record mode/owner, manifest, verify.
install -d -m 0700 "$EV" || { echo 'evidence directory failed; nothing changed'; exit 6; }
: > "$EV/MODES" && chmod 0600 "$EV/MODES" || { echo 'evidence MODES failed; nothing changed'; exit 6; }
cp -p "$F" "$EV/inputs.json" && cmp -s "$F" "$EV/inputs.json" \
  && [ "$(stat -c '%a %u %g' "$EV/inputs.json")" = "$(stat -c '%a %u %g' "$F")" ] && stat -c '%a %U %G %n' "$F" >> "$EV/MODES" \
  || { echo 'evidence copy failed; nothing changed'; exit 6; }
( cd "$EV" && umask 077 && sha256sum inputs.json MODES > MANIFEST.sha256 ) \
  && [ "$(grep -c . "$EV/MANIFEST.sha256")" = 2 ] && [ "$(grep -c . "$EV/MODES")" = 1 ] \
  && ( cd "$EV" && sha256sum -c --quiet MANIFEST.sha256 ) || { echo 'evidence manifest failed; nothing changed'; exit 6; }
echo 'evidence ok: inputs.json + MODES copied, compared and checksum-verified'

# 5. Change: rewrite the two strings into a sibling temp file with the same mode and owner, verify, then replace.
rc=0
tmp=$F.b5b
{ python3 -I - "$F" "$tmp" "$OLD_RUNNER_IMAGE" "$OLD_EXECUTOR_IMAGE" "$RUNNER_IMAGE" "$EXECUTOR_IMAGE" <<'PY' 2>/dev/null \
    && chmod "$mode" "$tmp" && chown 0:0 "$tmp" && mv -f "$tmp" "$F"; } || { rm -f "$tmp"; echo 'inputs.json update failed'; rc=5; }
import json, os, sys
src, dst, old_r, old_e, new_r, new_e = sys.argv[1:]
v = json.load(open(src))
v['image'] = new_r
v['executor']['image'] = new_e
fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
with os.fdopen(fd, 'w') as f:
    f.write(json.dumps(v, indent=2) + '\n')
    f.flush(); os.fsync(f.fileno())
PY

# 6. Postcheck: the two images are the new ones, everything else equals the evidence copy, mode and owner kept.
if [ $rc = 0 ]; then
  bad=
  python3 -I - "$F" "$EV/inputs.json" "$RUNNER_IMAGE" "$EXECUTOR_IMAGE" <<'PY' 2>/dev/null || bad="$bad content"
import json, sys
now, before, new_r, new_e = sys.argv[1:]
a, b = json.load(open(now)), json.load(open(before))
assert a['image'] == new_r and a['executor']['image'] == new_e
a['image'] = b['image']; a['executor']['image'] = b['executor']['image']
assert a == b
PY
  [ "$(stat -c '%u %g %a' "$F" 2>/dev/null)" = "0 0 $mode" ] || bad="$bad mode"
  ( cd "$EV" && sha256sum -c --quiet MANIFEST.sha256 ) || bad="$bad manifest"
  [ -z "$bad" ] && echo "inputs ok: controller publishes new bindings on ${RUNNER_IMAGE##*/} and ${EXECUTOR_IMAGE##*/}" \
    || { echo "postcheck failed:$bad"; rc=8; }
fi
echo "rollback: install -m $mode -o root -g root $EV/inputs.json $F (workers stay stopped either way)"
echo "result rc=$rc"
exit $rc
