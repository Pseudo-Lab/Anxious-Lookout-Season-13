#!/bin/sh
# #66: move the controller/observer worker scripts from the #72 worker image (B5) to the #66 merge's worker
# image. Only the image ID in controller.sh and observer.sh changes (the B5 mechanism without the api, which
# #66 does not change: the legacy identity check is used by the observer only).
#   worker-swap.sh apply      backs both scripts up into $EV (no clobber), then replaces the image ID
#   worker-swap.sh rollback   reinstalls both scripts from $EV
# Run as root with controller and observer stopped. Safe to run again: apply skips a script already on the
# new image (and refuses one on neither), rollback skips a script already restored.
# rc: 0 done and verified; 2 placeholders left; 3 precheck refused, nothing changed; 5 a change failed
#     midway (run rollback); 8 changed but a postcheck failed
set -u
OLD_WORKER_ID=@@B5_WORKER_IMAGE_ID@@  # the #72 worker image B5 installed (evidence/b5), sha256:<64 hex>
WORKER_IMAGE_ID=@@WORKER_IMAGE_ID@@   # the #66 merge commit's provisioner image, sha256:<64 hex>
BIN=/usr/local/lib/anxious-lookout/issue16
SCRIPTS='controller.sh observer.sh'
UNITS='issue16-controller.timer issue16-controller.service issue16-observer.timer issue16-observer.service'
EV=/root/issue66/evidence/worker
action=${1:-}
case "$OLD_WORKER_ID$WORKER_IMAGE_ID" in *@@*) echo 'placeholders not filled'; exit 2;; esac
refuse() { echo "precheck: $1; nothing changed"; echo 'result rc=3'; exit 3; }
fail() { echo "$1"; echo "result rc=$2"; exit "$2"; }
for i in "$OLD_WORKER_ID" "$WORKER_IMAGE_ID"; do echo "$i" | grep -qxE 'sha256:[0-9a-f]{64}' || refuse "$i is not an image ID"; done
[ "$OLD_WORKER_ID" != "$WORKER_IMAGE_ID" ] || refuse 'old and new image are the same'
case "$action" in apply|rollback) ;; *) echo 'usage: worker-swap.sh apply|rollback'; exit 3;; esac
for u in $UNITS; do
  v=$(timeout 10 systemctl show -p LoadState -p ActiveState "$u" 2>/dev/null) || refuse "systemctl show $u failed"
  v="$(echo "$v" | sed -n 's/^LoadState=//p') $(echo "$v" | sed -n 's/^ActiveState=//p')"
  case "$v" in 'loaded inactive'|'loaded failed') ;; *) refuse "$u is $v (stop the controller and observer first)";; esac
done
counts() { python3 -I -c 'import sys; t = open(sys.argv[1]).read(); print(t.count(sys.argv[2]), t.count(sys.argv[3]))' \
  "$1" "$OLD_WORKER_ID" "$WORKER_IMAGE_ID" 2>/dev/null; }
for s in $SCRIPTS; do
  [ -f "$BIN/$s" ] && [ ! -L "$BIN/$s" ] && [ "$(stat -c '%u %a %h' "$BIN/$s")" = '0 700 1' ] \
    || refuse "$BIN/$s is not a root 0700 regular file (observed: $(stat -c '%F uid=%u mode=%a links=%h' "$BIN/$s" 2>&1))"
  n=$(counts "$BIN/$s") || refuse "read $BIN/$s failed"
  case "$action:$n" in apply:'1 0'|apply:'0 1'|rollback:*) ;; *) refuse "$s has old/new image counts $n";; esac
done

if [ "$action" = apply ]; then
  i=$(timeout 30 docker image inspect --format '{{.Id}}' "$WORKER_IMAGE_ID" 2>/dev/null) && [ "$i" = "$WORKER_IMAGE_ID" ] \
    || refuse 'new worker image not in the Docker store'
  install -d -m 0700 "$EV" || refuse "$EV not usable"
  for s in $SCRIPTS; do
    [ "$(counts "$BIN/$s")" = '0 1' ] && { echo "$s already on the new image"; continue; }
    if [ -e "$EV/$s.pre66" ]; then cmp -s "$BIN/$s" "$EV/$s.pre66" || fail "$EV/$s.pre66 exists and differs from $BIN/$s" 5
    else cp -p "$BIN/$s" "$EV/$s.pre66" || fail "backup of $s failed" 5; fi
    python3 -I - "$BIN/$s" "$OLD_WORKER_ID" "$WORKER_IMAGE_ID" <<'PY' || fail "rewrite of $s failed; run rollback" 5
import os, sys
path, old, new = sys.argv[1:]
text = open(path).read()
assert text.count(old) == 1 and text.count(new) == 0
tmp = path + '.issue66'
with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700), 'w') as out:
    out.write(text.replace(old, new))
os.replace(tmp, path)
PY
    echo "$s: $OLD_WORKER_ID -> $WORKER_IMAGE_ID"
  done
  bad=
  for s in $SCRIPTS; do
    [ "$(counts "$BIN/$s")" = '0 1' ] && [ "$(stat -c '%u %a %h' "$BIN/$s")" = '0 700 1' ] || bad="$bad $s"
    python3 -I - "$BIN/$s" "$EV/$s.pre66" "$OLD_WORKER_ID" "$WORKER_IMAGE_ID" <<'PY' || bad="$bad $s-other-bytes"
import sys
now, before, old, new = sys.argv[1:]
assert open(now).read() == open(before).read().replace(old, new)
PY
  done
  [ -z "$bad" ] || fail "postcheck failed:$bad" 8
  echo "rollback: worker-swap.sh rollback (reinstalls $EV/*.pre66)"
  echo 'result rc=0'; exit 0
fi

for s in $SCRIPTS; do
  [ -f "$EV/$s.pre66" ] || fail "no backup $EV/$s.pre66" 5
  cmp -s "$EV/$s.pre66" "$BIN/$s" && { echo "$s already restored"; continue; }
  install -o root -g root -m 0700 "$EV/$s.pre66" "$BIN/$s" || fail "reinstall of $s failed" 5
  echo "$s restored"
done
bad=
for s in $SCRIPTS; do [ "$(counts "$BIN/$s")" = '1 0' ] || bad="$bad $s"; done
[ -z "$bad" ] || fail "postcheck failed:$bad" 8
echo 'result rc=0'
