#!/bin/sh
# #66: remove owner 3cb453bf's runner state after its termination proof. Account, sessions, conversations
# and posts stay. ops.runner_decommission (DB) from the #66 worker image; Kubernetes objects and host files
# here, all from one reviewed export.
#   decommission.sh report   read-only: fence, the tool's export (identities, counts, row digests; no
#                            secrets), live Kubernetes objects and the host paths that `host` deletes, with sizes
#   decommission.sh apply    the tool deletes the runner rows if a recomputed export is identical; then the
#                            Secret, both PVCs and both PVs, each by DELETE with the export's UID as precondition
#   decommission.sh host     unmount this state's two loop filesystems, delete user-runners/<state> and
#                            provision-output/<owner>; rc 0 only if restore's two inventories match again
#   decommission.sh close    unlinks the admin DSN
# Window: as legacy-stop.sh (units stopped), plus the owner idle_stopped with no Pod. The user creates the
# short-lived admin DSN at $W/private/admin-dsn (directory 0700, file 0600, root), as #72 D.
# Every step is safe to run again after a failure: apply treats an absent row set as already applied and
# a 404 object as already deleted; host deletes only what remains of the expected entries.
# rc: 0 done and verified; 2 placeholders left; 3 precheck or tool refused, nothing changed by this run;
#     5 a change failed or was ambiguous (inspect, then run the same step again); 8 changed but a postcheck failed
set -u
WORKER_IMAGE_ID=@@WORKER_IMAGE_ID@@  # the #66 merge commit's provisioner image, sha256:<64 hex>
OWNER=3cb453bf-d870-40dc-b7e1-3a58aa0cbaf2
OWNER_HEX=3cb453bfd87040dcb7e13a58aa0cbaf2
POD=runner-$OWNER_HEX
K='/usr/local/bin/k3s kubectl'
STORAGE=/var/lib/anxious-lookout/user-runners
OUTPUTS=/var/lib/anxious-lookout/issue16-d4/provision-output
UNITS='issue16-controller.timer issue16-controller.service issue16-observer.timer issue16-observer.service'
W=/root/issue66/decommission
E=$W/report/export.json
action=${1:-}
case "$WORKER_IMAGE_ID" in *@@*) echo 'placeholders not filled'; exit 2;; esac
refuse() { echo "precheck: $1; nothing changed"; echo 'result rc=3'; exit 3; }
fail() { echo "$1"; echo "result rc=$2"; exit "$2"; }
echo "$WORKER_IMAGE_ID" | grep -qxE 'sha256:[0-9a-f]{64}' || refuse 'WORKER_IMAGE_ID is not an image ID'
q() { timeout 30 $K -n m2-hosting exec postgres-0 -- psql -U postgres -d hosting -At -v ON_ERROR_STOP=1 -c "$1" 2>/dev/null; }
field() { python3 -I -c 'import json, sys; v = json.load(open(sys.argv[1]))
for k in sys.argv[2].split("."): v = v[k]
print(v)' "$E" "$1"; }

if [ "$action" = close ]; then
  [ -e "$W/private/admin-dsn" ] || [ -L "$W/private/admin-dsn" ] || { echo 'admin DSN already absent'; echo 'result rc=0'; exit 0; }
  unlink "$W/private/admin-dsn" || fail 'unlink admin DSN failed' 5
  [ ! -e "$W/private/admin-dsn" ] && [ ! -L "$W/private/admin-dsn" ] && { echo 'admin DSN removed'; echo 'result rc=0'; exit 0; }
  fail 'postcheck failed: admin DSN' 8
fi
case "$action" in report|apply|host) ;; *) echo 'usage: decommission.sh report|apply|host|close'; exit 3;; esac

for u in $UNITS; do
  v=$(timeout 10 systemctl show -p LoadState -p ActiveState "$u" 2>/dev/null) || refuse "systemctl show $u failed"
  v="$(echo "$v" | sed -n 's/^LoadState=//p') $(echo "$v" | sed -n 's/^ActiveState=//p')"
  case "$v" in 'loaded inactive'|'loaded failed') ;; *) refuse "$u is $v (stop the controller and observer first)";; esac
done
g=$(timeout 30 $K -n codex-runner get pods -l "anxious.io/owner=$OWNER" -o name 2>/dev/null) || refuse 'runner pod lookup failed'
[ -z "$g" ] || refuse "Pods of $OWNER exist: $(echo $g)"
fence() { q "select state_id||'|'||phase||'|'||generation||'|'||revision||'|'||coalesce(pod_uid::text,'-') from runner_runtime.registry where owner_id='$OWNER'"; }
# One object: kind name -> "<api path> <live uid or empty>".
path() { case "$1" in secret) echo "/api/v1/namespaces/codex-runner/secrets/$2";;
  pvc) echo "/api/v1/namespaces/codex-runner/persistentvolumeclaims/$2";; pv) echo "/api/v1/persistentvolumes/$2";; esac; }
live() { case "$1" in pv) timeout 30 $K get pv "$2" --ignore-not-found -o jsonpath='{.metadata.uid}' 2>/dev/null;;
  *) timeout 30 $K -n codex-runner get "$1" "$2" --ignore-not-found -o jsonpath='{.metadata.uid}' 2>/dev/null;; esac; }
objects() { python3 -I -c 'import json, sys
v = json.load(open(sys.argv[1]))
assert v["namespace"] == "codex-runner" and len(v["resources"]) == 5
for kind in ("secret", "pvc", "pv"):
    for r in v["resources"]:
        if r["kind"] == kind: print(r["kind"], r["name"], r["uid"])' "$E"; }
paths_ok() { S=$(field host.storage) && O=$(field host.output) && st=$(field state) || return 1
  [ "$S" = "$STORAGE/$(echo "$st" | tr -d -)" ] && [ "$O" = "$OUTPUTS/$OWNER_HEX" ] \
    && echo "$S" | grep -qxE "$STORAGE/[0-9a-f]{32}"; }
listing() { # the host paths `host` deletes, with sizes; names only, never file contents
  echo "delete: $S"
  if [ -d "$S" ]; then
    for e in "$S"/* "$S"/.[!.]*; do [ -e "$e" ] || [ -L "$e" ] || continue
      echo "  $(stat -c '%n %F uid=%u mode=%a size=%s' "$e") allocated=$(du -sk "$e" 2>/dev/null | cut -f1)K"; done
    if lt=$(findmnt -rn -o TARGET,SOURCE,FSTYPE); then
      for m in native control; do echo "  mount $S/$m: $(echo "$lt" | awk -v t="$S/$m" '$1 == t {print $2, $3; f = 1} END {if (!f) print "none"}')"; done
    else echo '  mounts: findmnt failed (host refuses until it works)'; fi
  else echo '  absent'; fi
  echo "delete: $O"
  if [ -d "$O" ]; then find "$O" -mindepth 1 -printf '  %p %y uid=%U mode=%m size=%s\n' 2>/dev/null
  else echo '  absent'; fi
  [ -e "$OUTPUTS/.lock-$OWNER_HEX" ] && echo "delete: $OUTPUTS/.lock-$OWNER_HEX ($(stat -c '%F size=%s' "$OUTPUTS/.lock-$OWNER_HEX"))"
  return 0; }
tool() { # action, report mount option
  timeout 120 docker run --rm --pull=never --user 0:0 --read-only --cap-drop ALL --security-opt no-new-privileges \
    --network=host --mount "type=bind,src=$W/private,dst=/private,readonly" \
    --mount "type=bind,src=$W/inputs,dst=/inputs,readonly" --mount "type=bind,src=$W/report,dst=/reports$2" \
    -e ADMIN_DATABASE_URL_FILE=/private/admin-dsn --entrypoint python "$WORKER_IMAGE_ID" \
    -B -m ops.runner_decommission "$1" --inputs /inputs/fence.json --export /reports/export.json 2>&1
}
admin_ready() {
  s=$(stat -c '%u %a %F' "$W/private" 2>/dev/null) || refuse "$W/private missing"
  [ "$s" = '0 700 directory' ] && [ ! -L "$W/private" ] || refuse "$W/private is not a root 0700 directory"
  s=$(stat -c '%u %a %h %F' "$W/private/admin-dsn" 2>/dev/null) || refuse 'admin DSN file missing'
  [ "$s" = '0 600 1 regular file' ] && [ ! -L "$W/private/admin-dsn" ] || refuse 'admin DSN is not a root 0600 regular file'
  i=$(timeout 30 docker image inspect --format '{{.Id}}' "$WORKER_IMAGE_ID" 2>/dev/null) && [ "$i" = "$WORKER_IMAGE_ID" ] \
    || refuse 'worker image not in the Docker store'
}

if [ "$action" = report ]; then
  admin_ready
  [ ! -e "$E" ] || refuse "$E exists (review it, or move $W/report and $W/inputs aside to report again)"
  f=$(fence) || refuse 'registry lookup failed'
  echo "$f" | grep -qxE '[0-9a-f-]{36}\|idle_stopped\|[0-9]+\|[0-9]+\|-' || refuse "registry row is not idle_stopped without a Pod (observed: ${f:-absent})"
  IFS='|' read -r state phase generation revision podu <<EOF
$f
EOF
  { install -d -m 0700 "$W/inputs" "$W/report" && ( umask 077; python3 -I -c 'import json, sys
o, s, g, r = sys.argv[1:]; json.dump({"owner": o, "state": s, "generation": int(g), "revision": int(r)}, sys.stdout)' \
      "$OWNER" "$state" "$generation" "$revision" > "$W/inputs/fence.json" ); } || refuse "writing $W/inputs failed"
  out=$(tool report ''); t=$?
  echo "$out"
  [ $t = 0 ] && [ -f "$E" ] || { echo "the tool refused the report (rc $t; observed values above); nothing changed"; echo 'result rc=3'; exit 3; }
  echo 'export for review:'; cat "$E"; echo
  paths_ok || refuse 'export host paths are not this owner and state'
  bad=
  while read -r kind name uid; do
    l=$(live "$kind" "$name") || refuse "$kind $name lookup failed"
    echo "object $kind $name export=$uid live=${l:-absent}"
    [ -z "$l" ] || [ "$l" = "$uid" ] || bad="$bad $kind/$name"
  done <<EOF
$(objects)
EOF
  [ -z "$bad" ] || refuse "live UID differs from the export:$bad"
  listing
  echo 'next: review the export and the delete list, then decommission.sh apply'
  echo 'result rc=0'; exit 0
fi

[ -f "$E" ] && [ -f "$W/inputs/fence.json" ] || refuse 'no reviewed export (run report first)'
paths_ok || refuse 'export host paths are not this owner and state'

if [ "$action" = apply ]; then
  admin_ready
  f=$(fence) || refuse 'registry lookup failed'
  if [ -n "$f" ]; then
    python3 -I -c 'import json, sys
i = json.load(open(sys.argv[1])); f = sys.argv[2].split("|")
assert [i["state"], "idle_stopped", str(i["generation"]), str(i["revision"]), "-"] == f' \
      "$W/inputs/fence.json" "$f" 2>/dev/null || refuse "registry fence changed since the report (observed: $f)"
  fi
  out=$(tool apply ',readonly'); t=$?
  echo "$out"
  case "$t:$out" in
    *'"result": "deleted"'*|*'"result": "already_applied"'*) [ $t = 0 ] || fail "tool rc $t; inspect, then run apply again" 5 ;;
    3:*) echo 'the tool refused (observed values above); no row deleted'; echo 'result rc=3'; exit 3 ;;
    *) fail "tool rc $t without a result; the rows may or may not be deleted: run apply again (it recomputes)" 5 ;;
  esac
  while read -r kind name uid; do
    l=$(live "$kind" "$name") || fail "$kind $name lookup failed; run apply again" 5
    if [ -z "$l" ]; then echo "object $kind $name already absent"; continue; fi
    [ "$l" = "$uid" ] || fail "object $kind $name live UID $l differs from the export $uid; stop, post to #66" 5
    ( umask 077; printf '{"kind":"DeleteOptions","apiVersion":"v1","preconditions":{"uid":"%s"}}' "$uid" > "$W/delete.json" ) \
      || fail 'writing DeleteOptions failed' 5
    d=$(timeout 30 $K delete --raw "$(path "$kind" "$name")" -f "$W/delete.json" 2>&1 >/dev/null) \
      || fail "DELETE $kind $name failed: $d; run apply again" 5
    if [ "$kind" = pv ]; then w=pv/$name; timeout 90 $K wait --for=delete "$w" --timeout=60s >/dev/null 2>&1
    else w=$kind/$name; timeout 90 $K -n codex-runner wait --for=delete "$w" --timeout=60s >/dev/null 2>&1; fi \
      || fail "$w still present after 60 s (finalizer?): $(live "$kind" "$name"); run apply again" 5
    echo "object $kind $name deleted (uid $uid)"
  done <<EOF
$(objects)
EOF
  bad=
  while read -r kind name uid; do l=$(live "$kind" "$name") || l=lookup-failed; [ -z "$l" ] || bad="$bad $kind/$name=$l"; done <<EOF
$(objects)
EOF
  n=$(q "select (select count(*) from runner_runtime.registry where owner_id='$OWNER')+(select count(*) from runner_bindings.bindings where owner_id='$OWNER')+(select count(*) from runner_termination.stops where owner_id='$OWNER')") || n=lookup-failed
  [ "$n" = 0 ] || bad="$bad db-rows=$n"
  a=$(q "select count(*) from auth.accounts where id='$OWNER'") || a=lookup-failed
  [ "$a" = 1 ] || bad="$bad account=$a"
  [ -z "$bad" ] || fail "postcheck failed:$bad" 8
  ( umask 077; date -u +%Y-%m-%dT%H:%M:%SZ > "$W/applied" ) || echo 'applied marker not written'
  echo "applied: runner rows and 5 objects of $OWNER removed; account kept"
  echo 'next: decommission.sh host (same window; restore needs both host inventories to match)'
  echo 'result rc=0'; exit 0
fi

# host
[ -f "$W/applied" ] || refuse 'apply has not completed (no applied marker)'
bad=
while read -r kind name uid; do l=$(live "$kind" "$name") || l=lookup-failed; [ -z "$l" ] || bad="$bad $kind/$name=$l"; done <<EOF
$(objects)
EOF
[ -z "$bad" ] || refuse "Kubernetes objects still present:$bad"
listing
# Phase 1 checks everything (storage, output, lock) before the first change; a refusal there changes
# nothing. Every lookup is rc-checked; only a successful empty answer means "none". Phase 2 changes; a
# failure there reports exactly what was already changed (rc 5) and is resolved by running host again.
table() { mt=$(findmnt -rn -o TARGET,SOURCE) && [ -n "$mt" ]; }
loops() { devs=$(losetup -n -O NAME -j "$1"); }
mounted() { echo "$mt" | awk -v t="$1" '$1 == t {f = 1} END {exit !f}'; }
changed=
partial() { echo "$1"
  echo "partial: already changed by this run:${changed:- nothing}"
  echo "state: $S $( [ -e "$S" ] && echo present || echo absent ), $O $( [ -e "$O" ] && echo present || echo absent )"
  echo 'restore would refuse until both are gone: fix the cause, then run decommission.sh host again'
  echo 'result rc=5'; exit 5; }
L=$OUTPUTS/.lock-$OWNER_HEX
table || refuse 'findmnt (mount table) failed'
if [ -e "$S" ] || [ -L "$S" ]; then
  u=$(stat -c %u "$S") || refuse "stat $S failed"
  [ -d "$S" ] && [ ! -L "$S" ] && [ "$u" = 0 ] || refuse "$S is not a root directory (observed uid $u)"
  # Only this state's own loop mounts may use its images (no kubelet bind of a PV path remains).
  for m in native control; do
    own=$(echo "$mt" | awk -v t="$S/$m" '$1 == t {print $2}')
    if [ -e "$S/$m.ext4" ]; then
      loops "$S/$m.ext4" || refuse "losetup -j $S/$m.ext4 failed"
    else devs=; fi
    # The mount at $S/$m must come from a loop device of this image; anything else is an inconsistent view.
    if [ -n "$own" ]; then
      echo "$devs" | grep -qxF -- "$own" || refuse "$S/$m is mounted from $own, but losetup lists for $S/$m.ext4: ${devs:-none}"
    fi
    for dev in $devs; do
      o=$(echo "$mt" | awk -v d="$dev" -v t="$S/$m" '($2 == d || index($2, d"[") == 1) && $1 != t')
      [ -z "$o" ] || refuse "$dev ($S/$m.ext4) is also mounted at: $(echo $o)"
    done
  done
  o=$(echo "$mt" | awk -v s="$S/" 'index($1, s) == 1 && $1 != s"native" && $1 != s"control"')
  [ -z "$o" ] || refuse "other mounts under $S: $(echo $o)"
  entries=$(ls -A "$S") || refuse "ls $S failed"
  extra=$(printf '%s\n' "$entries" | awk 'NF && !/^(native|control|native\.ext4|control\.ext4)$/')
  [ -z "$extra" ] || refuse "unexpected entries in $S: $(echo $extra)"
  for m in native control; do
    if [ -d "$S/$m" ] && ! mounted "$S/$m"; then
      c=$(ls -A "$S/$m") || refuse "ls $S/$m failed"
      [ -z "$c" ] || refuse "$S/$m is not mounted but not empty: $(echo "$c" | head -5 | tr '\n' ' ')"
    fi
  done
fi
if [ -e "$O" ] || [ -L "$O" ]; then
  u=$(stat -c %u "$O") || refuse "stat $O failed"
  [ -d "$O" ] && [ ! -L "$O" ] && [ "$u" = 10001 ] || refuse "$O is not the provisioner's output directory (observed uid $u)"
  o=$(echo "$mt" | awk -v s="$O" '$1 == s || index($1, s"/") == 1')
  [ -z "$o" ] || refuse "mounts at or under $O: $(echo $o)"
fi
if [ -e "$L" ] || [ -L "$L" ]; then
  [ -f "$L" ] && [ ! -L "$L" ] || refuse "$L is not a regular file"
fi
# Phase 2: changes.
if [ -e "$S" ]; then
  for m in native control; do
    if mounted "$S/$m"; then
      umount "$S/$m" || partial "umount $S/$m failed (busy?)"
      changed="$changed unmounted:$S/$m"; echo "unmounted $S/$m"
      table || partial "findmnt failed after unmounting $S/$m"
      ! mounted "$S/$m" || partial "$S/$m is still mounted after umount"
    fi
    if [ -e "$S/$m.ext4" ]; then
      loops "$S/$m.ext4" || partial "losetup -j $S/$m.ext4 failed after unmount"
      [ -z "$devs" ] || partial "$S/$m.ext4 is still attached to a loop device: $(echo $devs)"
    fi
    if [ -d "$S/$m" ]; then
      c=$(ls -A "$S/$m") || partial "ls $S/$m failed after unmount"
      [ -z "$c" ] || partial "$S/$m is not empty after unmount: $(echo "$c" | head -5 | tr '\n' ' ')"
    fi
  done
  for m in native control; do
    if [ -e "$S/$m.ext4" ]; then rm -f -- "$S/$m.ext4" || partial "rm $S/$m.ext4 failed"; changed="$changed deleted:$S/$m.ext4"; fi
    if [ -d "$S/$m" ]; then rmdir -- "$S/$m" || partial "rmdir $S/$m failed"; changed="$changed deleted:$S/$m"; fi
  done
  rmdir -- "$S" || partial "rmdir $S failed: $(ls -A "$S" | tr '\n' ' ')"
  changed="$changed deleted:$S"; echo "deleted $S"
fi
if [ -e "$O" ]; then
  rm -rf --one-file-system -- "$O" || partial "rm $O failed"
  changed="$changed deleted:$O"; echo "deleted $O"
fi
if [ -e "$L" ]; then rm -f -- "$L" || partial "rm $L failed"; changed="$changed deleted:$L"; echo "deleted $L"; fi
r=$(python3 -I - "$OUTPUTS" "$STORAGE" <<'PY'
import json, sys
from pathlib import Path
from uuid import UUID
outputs, storage = Path(sys.argv[1]), Path(sys.argv[2])
states, unreadable = set(), []
for o in outputs.iterdir():
    if o.name.startswith('.lock-'):
        continue
    try:
        states.add(UUID(json.load(open(o / 'storage-restore.json'))['state']).hex)
    except Exception:
        unreadable.append(o.name)
dirs = {p.name for p in storage.iterdir()}
print('restore inventories: outputs=%d storage=%d only-storage=%s only-outputs=%s unreadable=%s'
      % (len(states), len(dirs), sorted(dirs - states), sorted(states - dirs), unreadable))
sys.exit(0 if dirs == states and not unreadable else 1)
PY
); t=$?
echo "$r"
[ ! -e "$S" ] && [ ! -e "$O" ] || fail "postcheck failed: $S or $O still present" 8
[ $t = 0 ] || fail 'postcheck failed: restore inventories differ (the next reboot restore would refuse); post to #66' 8
( umask 077; date -u +%Y-%m-%dT%H:%M:%SZ > "$W/host-done" ) || echo 'host-done marker not written'
echo "host: $OWNER's runner storage and provisioner output removed"
echo 'result rc=0'
exit 0
