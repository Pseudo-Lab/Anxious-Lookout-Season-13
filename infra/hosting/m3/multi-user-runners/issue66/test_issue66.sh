#!/bin/sh
# Mock harness for the #66 node scripts. Docker only, never on a real node:
#   docker run --rm -v "$PWD/infra/hosting/m3:/m3:ro" -v "$PWD/backend:/backend:ro" alpine:3.20 \
#     sh /m3/multi-user-runners/issue66/test_issue66.sh
# Stubs replace kubectl (k3s), docker, systemctl, findmnt, losetup and umount. observer.sh is rendered by the
# real backend/ops/runner_rollout.py. Credential fixtures hold SECRET markers that must never reach output.
set -u
set -a  # prefix assignments before t must reach the script under test
apk add -q python3 coreutils findutils grep sed >/dev/null 2>&1 || { echo 'apk failed'; exit 1; }
P=/m3/multi-user-runners/issue66
mkdir -p /usr/local/sbin
for s in k3s:/usr/local/bin/k3s systemctl:/usr/bin/systemctl docker:/usr/bin/docker; do
  printf '#!/bin/sh\nexec python3 %s/mock/%s.py "$@"\n' $P "${s%%:*}" > "${s#*:}"; chmod 0755 "${s#*:}"
done
for s in findmnt losetup umount; do  # ahead of busybox in PATH
  printf '#!/bin/sh\nexec python3 %s/mock/host.py %s "$@"\n' $P $s > /usr/local/sbin/$s; chmod 0755 /usr/local/sbin/$s
done
# rm passes through, except for the path in MOCK_RM_FAIL (a deletion failing after earlier changes).
printf '#!/bin/sh\nfor a in "$@"; do [ -n "${MOCK_RM_FAIL:-}" ] && [ "$a" = "$MOCK_RM_FAIL" ] && exit 1; done\nexec /bin/rm "$@"\n' \
  > /usr/local/sbin/rm; chmod 0755 /usr/local/sbin/rm
WID=sha256:$(printf '9%.0s' $(seq 64))
OLDWID=sha256:1c3ce2683832a187ef207abe4daa7989589a996cde8214ce374237fa94ea81e6
B5ID=sha256:$(printf '7%.0s' $(seq 64))
OWNER=3cb453bf-d870-40dc-b7e1-3a58aa0cbaf2
OH=3cb453bfd87040dcb7e13a58aa0cbaf2
POD_UID=880bf6cd-70ea-4625-9185-c1604c8f8466
STATE=5e3a0c1d-2b4f-4a6e-8c9d-0123456789ab
SH=5e3a0c1d2b4f4a6e8c9d0123456789ab
OTHER=6f4b1d2e3c5a4b7f9dae123456789abc
ST=/var/lib/anxious-lookout/user-runners
OUT=/var/lib/anxious-lookout/issue16-d4/provision-output
S=$ST/$SH
O=$OUT/$OH
B=/root/bin
W=/root/issue66/decommission
pass=0; failed=0

render() { # <role>.sh exactly as runner_rollout.render writes it, on image $1 (role $2, default observer)
  python3 -I - "$1" "${2:-observer}" <<'PY'
import sys
sys.path.insert(0, '/backend')
from ops.runner_rollout import docker, shell_quote
v = {'criSocket': '/run/k3s/containerd/containerd.sock', 'crictl': '/usr/local/bin/crictl', 'provisionerImageId': sys.argv[1],
     'maxPods': 3, 'idleSeconds': 1800, 'nativeSizeGiB': 5, 'controlSizeGiB': 1,
     'allowlist': ['3cb453bf-d870-40dc-b7e1-3a58aa0cbaf2']}
sys.stdout.write('#!/bin/sh\nset -eu\nexec ' + ' '.join(shell_quote(arg) for arg in docker(v, sys.argv[2])) + '\n')
PY
}

build() { # $1: legacy (owner stopping, Pod Running) or stopped (idle_stopped, Pod gone)
  rm -rf /mock /root/issue66 /var/lib/anxious-lookout /usr/local/lib/anxious-lookout /run/issue66-legacy $B
  mkdir -p /mock $B /usr/local/lib/anxious-lookout/issue16 $ST $OUT $W/private
  for u in issue16-controller.timer issue16-controller.service issue16-observer.timer issue16-observer.service; do
    echo 'loaded inactive' > /mock/unit.$u; done
  echo 'loaded failed' > /mock/unit.issue16-observer.service
  echo "$WID" > /mock/docker-images
  render "$WID" > /usr/local/lib/anxious-lookout/issue16/observer.sh
  for f in legacy-stop.sh decommission.sh worker-swap.sh; do
    sed -e "s/@@WORKER_IMAGE_ID@@/$WID/" -e "s/@@B5_WORKER_IMAGE_ID@@/$B5ID/" $P/$f > $B/$f; done
  cat > /mock/objects.json <<EOF
{"secret/transport-$SH": "11111111-0000-4000-8000-000000000001", "pvc/native-$SH": "11111111-0000-4000-8000-000000000002",
 "pvc/control-$SH": "11111111-0000-4000-8000-000000000003", "pv/native-$SH": "11111111-0000-4000-8000-000000000004",
 "pv/control-$SH": "11111111-0000-4000-8000-000000000005"}
EOF
  python3 -I -c 'import json; o = json.load(open("/mock/objects.json"))
json.dump([{"kind": k.split("/")[0], "name": k.split("/")[1], "uid": u} for k, u in o.items()], open("/mock/export-resources.json", "w"))'
  if [ "$1" = legacy ]; then
    echo "{\"uid\": \"$POD_UID\"}" > /mock/pod.json
    echo "{\"registry\": \"$STATE|runner_unreachable|9|38|$POD_UID\", \"stop\": \"stop s|9|$POD_UID|unanchored|receipt=false|clean=-|completed=-\", \"rows\": 9}" > /mock/db.json
  else
    echo "{\"registry\": \"$STATE|idle_stopped|9|40|-\", \"rows\": 9}" > /mock/db.json
  fi
  # Host: this owner's state and output, and one other owner that must stay.
  for h in $SH $OTHER; do
    mkdir -p $ST/$h/native $ST/$h/control; head -c 4096 /dev/zero > $ST/$h/native.ext4; head -c 2048 /dev/zero > $ST/$h/control.ext4
    chmod 0600 $ST/$h/*.ext4; chmod 0700 $ST/$h
  done
  for pair in "$OH:$STATE" "aaaaaaaabbbb4ccc8dddeeeeeeeeeeee:6f4b1d2e-3c5a-4b7f-9dae-123456789abc"; do
    d=$OUT/${pair%%:*}; mkdir -p $d
    printf '{"version":1,"state":"%s"}' "${pair#*:}" > $d/storage-restore.json
    echo '{"plan":{}}' > $d/runtime.json; echo 'SECRET-TRANSPORT-TOKEN' > $d/transport-token
    chown -R 10001:10001 $d; chmod 0700 $d; chmod 0600 $d/*
    : > $OUT/.lock-${pair%%:*}
  done
  printf '%s\n' "$S/native /dev/loop1 ext4" "$S/control /dev/loop2 ext4" "$ST/$OTHER/native /dev/loop3 ext4" > /mock/mounts
  printf '%s\n' "$S/native.ext4 /dev/loop1" "$S/control.ext4 /dev/loop2" "$ST/$OTHER/native.ext4 /dev/loop3" > /mock/loops
  echo 'postgresql://admin:SECRET-DSN@db/hosting' > $W/private/admin-dsn; chmod 0700 $W/private; chmod 0600 $W/private/admin-dsn
}

t() { # name, expected rc, script args...
  name=$1 want=$2; shift 2
  out=$(sh "$@" 2>&1); got=$?
  if [ "$got" = "$want" ] && ! echo "$out" | grep -q SECRET; then pass=$((pass+1)); echo "ok   $name"
  else failed=$((failed+1)); echo "FAIL $name (rc $got, want $want)"; echo "$out" | sed 's/^/     /' | tail -n 25; fi
}
has() { # name, text: the last output contains text
  if echo "$out" | grep -qF -- "$2"; then pass=$((pass+1)); echo "ok   $1"
  else failed=$((failed+1)); echo "FAIL $1 (missing: $2)"; fi
}
check() { # name, shell condition
  if sh -c "$2"; then pass=$((pass+1)); echo "ok   $1"; else failed=$((failed+1)); echo "FAIL $1 ($2)"; fi
}

echo '## legacy-stop.sh'
build legacy
t 'placeholders refuse' 2 $P/legacy-stop.sh diagnose
t 'diagnose: ready' 0 $B/legacy-stop.sh diagnose
has 'diagnose: result line' 'termination-diagnose ready_for_observation'
check 'diagnose: built from observer.sh with the legacy option only' \
  "grep -q -- '-B -m ops.runner_observer_diagnostics --inputs /private/inputs.json --owner $OWNER --legacy-pod-uid $POD_UID' /mock/docker.log"
check 'diagnose: same mounts and DB login as observer.sh' \
  "grep -q 'OBSERVER_DATABASE_URL_FILE=/private/database-url' /mock/docker.log && grep -q 'dst=/host/proc' /mock/docker.log"
MOCK_DIAG=pod_invalid t 'diagnose: pod_invalid is rc 4' 4 $B/legacy-stop.sh diagnose
echo 'loaded active' > /mock/unit.issue16-observer.timer
t 'observe: running timer refuses' 3 $B/legacy-stop.sh observe
echo 'loaded inactive' > /mock/unit.issue16-observer.timer
render "$OLDWID" > /usr/local/lib/anxious-lookout/issue16/observer.sh
t 'observe: observer.sh on another image refuses' 3 $B/legacy-stop.sh observe
render "$WID" | sed 's/ --idle$//' > /usr/local/lib/anxious-lookout/issue16/observer.sh
t 'observe: unexpected observer.sh tail refuses' 3 $B/legacy-stop.sh observe
render "$WID" > /usr/local/lib/anxious-lookout/issue16/observer.sh
echo '{"uid": "99999999-0000-4000-8000-000000000000"}' > /mock/pod.json
t 'observe: another Pod UID refuses' 3 $B/legacy-stop.sh observe
echo "{\"uid\": \"$POD_UID\"}" > /mock/pod.json
python3 -I -c 'import json; d = json.load(open("/mock/db.json")); d["entries_valid"] = 1; json.dump(d, open("/mock/db.json", "w"))'
t 'observe: a valid unconsumed entry refuses' 3 $B/legacy-stop.sh observe
python3 -I -c 'import json; d = json.load(open("/mock/db.json")); d["entries_valid"] = 0; json.dump(d, open("/mock/db.json", "w"))'
rm -f /mock/docker.log
MOCK_OBSERVE=refused t 'observe: reconciliation_required is rc 4' 4 $B/legacy-stop.sh observe
has 'observe: refusal points to evidence' "legacy-stop.sh evidence"
MOCK_OBSERVE_HANG=1 t 'observe: no result line is rc 5' 5 $B/legacy-stop.sh observe
check 'observe: cidfile cleaned' '[ ! -e /run/issue66-legacy/container-id ]'
t 'evidence: read-only with /health from inside the Pod' 0 $B/legacy-stop.sh evidence
has 'evidence: health fields' '"reason": "auth_revoked"'
has 'evidence: stop row' 'stop s|9|'
MOCK_HEALTH_FAIL=1 t 'evidence: exec failure is rc 1' 1 $B/legacy-stop.sh evidence
check 'evidence/diagnose: never ran the observer' "! grep -q 'worker.py observer' /mock/docker.log || grep -c 'worker.py observer' /mock/docker.log | grep -qx 2"
t 'observe: anchor, drain, DELETE' 0 $B/legacy-stop.sh observe
has 'observe: Pod deleting after' "after: pod runner-$OH $POD_UID|2026-11-01T00:00:00Z"
t 'evidence: skips /health while deleting' 0 $B/legacy-stop.sh evidence
has 'evidence: deleting noted' 'health: skipped'
rm /mock/pod.json
t 'diagnose: Pod gone, receipt ready' 0 $B/legacy-stop.sh diagnose
t 'observe: proof and release' 0 $B/legacy-stop.sh observe
has 'observe: idle_stopped after' "after: registry $STATE|idle_stopped|9|39|-"

echo '## decommission.sh'
build stopped
t 'placeholders refuse' 2 $P/decommission.sh report
t 'host before report refuses' 3 $B/decommission.sh host
echo "{\"uid\": \"$POD_UID\"}" > /mock/pod.json
t 'report: a Pod of the owner refuses' 3 $B/decommission.sh report
rm /mock/pod.json
echo "{\"registry\": \"$STATE|runner_unreachable|9|38|$POD_UID\"}" > /mock/db.json
t 'report: not idle_stopped refuses with observed row' 3 $B/decommission.sh report
has 'report: observed fence printed' "observed: $STATE|runner_unreachable|9|38|$POD_UID"
echo "{\"registry\": \"$STATE|idle_stopped|9|40|-\", \"rows\": 9}" > /mock/db.json
mv $W/private/admin-dsn /tmp/dsn
t 'report: missing admin DSN refuses' 3 $B/decommission.sh report
mv /tmp/dsn $W/private/admin-dsn
MOCK_TOOL=refuse t 'report: tool refusal is rc 3 with observed values' 3 $B/decommission.sh report
has 'report: tool observed values' '"unconsumedEntries": 1'
rm -rf $W/report $W/inputs
python3 -I -c 'import json; o = json.load(open("/mock/objects.json")); o["pv/native-'$SH'"] = "99999999-0000-4000-8000-000000000000"; json.dump(o, open("/mock/objects.json", "w"))'
t 'report: live UID differing from the export refuses' 3 $B/decommission.sh report
rm -rf $W/report $W/inputs
build stopped
t 'report: ok' 0 $B/decommission.sh report
has 'report: export printed' '"phase": "idle_stopped"'
has 'report: delete list has the storage images with sizes' "$S/native.ext4 regular file uid=0 mode=600 size=4096"
has 'report: delete list has the loop mounts' "mount $S/native: /dev/loop1 ext4"
has 'report: delete list names the output files' "$O/transport-token f uid=10001 mode=600"
has 'report: delete list has the lock file' "delete: $OUT/.lock-$OH"
check 'report: nothing deleted' "[ -d $S ] && [ -d $O ] && [ ! -e /mock/deletes.log ] && grep -q idle_stopped /mock/db.json"
t 'report: a second report refuses' 3 $B/decommission.sh report
t 'host: before apply refuses' 3 $B/decommission.sh host
python3 -I -c 'import json; d = json.load(open("/mock/db.json")); d["registry"] = d["registry"].replace("|40|", "|41|"); json.dump(d, open("/mock/db.json", "w"))'
t 'apply: fence changed refuses' 3 $B/decommission.sh apply
python3 -I -c 'import json; d = json.load(open("/mock/db.json")); d["registry"] = d["registry"].replace("|41|", "|40|"); json.dump(d, open("/mock/db.json", "w"))'
MOCK_TOOL=refuse t 'apply: tool refusal deletes nothing' 3 $B/decommission.sh apply
check 'apply: refusal left objects and rows' "[ ! -e /mock/deletes.log ] && grep -q idle_stopped /mock/db.json"
MOCK_DELETE_FAIL=pvc/native-$SH t 'apply: a failed DELETE is rc 5' 5 $B/decommission.sh apply
check 'apply: secret deleted with its UID precondition' "grep -qx 'secret/transport-$SH 11111111-0000-4000-8000-000000000001' /mock/deletes.log"
t 'apply: rerun after the failure completes' 0 $B/decommission.sh apply
has 'apply: rerun sees the rows already gone' '"result": "already_applied"'
has 'apply: rerun skips the deleted secret' "object secret transport-$SH already absent"
check 'apply: secret deleted exactly once' "[ \$(grep -c '^secret/' /mock/deletes.log) = 1 ]"
check 'apply: all five objects gone, account kept' "grep -qx '{}' /mock/objects.json"
t 'apply: a third run is a no-op' 0 $B/decommission.sh apply
build stopped
sh $B/decommission.sh report >/dev/null 2>&1
MOCK_TOOL=ambiguous t 'apply: tool without a result line is rc 5' 5 $B/decommission.sh apply
t 'apply: rerun after the ambiguous tool completes' 0 $B/decommission.sh apply
build stopped
sh $B/decommission.sh report >/dev/null 2>&1
python3 -I -c 'import json; o = json.load(open("/mock/objects.json")); o["pv/control-'$SH'"] = "99999999-0000-4000-8000-000000000000"; json.dump(o, open("/mock/objects.json", "w"))'
t 'apply: a recreated object (other UID) is rc 5, never deleted' 5 $B/decommission.sh apply
check 'apply: the foreign PV was not deleted' "! grep -q '^pv/control-' /mock/deletes.log"
build stopped
sh $B/decommission.sh report >/dev/null 2>&1
MOCK_STUCK=pvc/control-$SH t 'apply: an object that stays is rc 5' 5 $B/decommission.sh apply
build stopped
sh $B/decommission.sh report >/dev/null 2>&1
sh $B/decommission.sh apply >/dev/null 2>&1
echo "/var/lib/kubelet/pods/x/volumes/native /dev/loop1[/data] ext4" >> /mock/mounts
t 'host: a second mount of the loop refuses' 3 $B/decommission.sh host
has 'host: delete list printed first' "delete: $S"
check 'host: refusal deleted nothing' "[ -f $S/native.ext4 ] && [ -d $O ] && grep -q '^$S/native ' /mock/mounts"
# Review round 1: a failed lookup must never read as "none".
rm -f /mock/losetup.calls
MOCK_LOSETUP_FAIL=1 t 'host: losetup failing with a foreign bind refuses' 3 $B/decommission.sh host
has 'host: losetup failure named' "losetup -j $S/native.ext4 failed"
check 'host: losetup failure kept files and mounts' \
  "[ -f $S/native.ext4 ] && [ -f $S/control.ext4 ] && [ -d $O ] && grep -q kubelet /mock/mounts && grep -q '^$S/native ' /mock/mounts"
MOCK_LOSETUP_EMPTY=1 t 'host: losetup empty while mounted from a loop refuses' 3 $B/decommission.sh host
has 'host: inconsistent view named' "$S/native is mounted from /dev/loop1, but losetup lists"
sed -i '/kubelet/d' /mock/mounts
MOCK_FINDMNT_FAIL=1 t 'host: findmnt failing refuses' 3 $B/decommission.sh host
has 'host: listing marks the failed mount lookup' 'mounts: findmnt failed'
check 'host: findmnt failure kept files and mounts' "[ -f $S/native.ext4 ] && [ -d $O ] && grep -q '^$S/native ' /mock/mounts"
rm -f /mock/losetup.calls
MOCK_LOSETUP_FAIL_FROM=3 t 'host: losetup failing after the unmount is rc 5' 5 $B/decommission.sh host
check 'host: post-unmount failure deleted no file' "[ -f $S/native.ext4 ] && [ -f $S/control.ext4 ] && [ -d $O ]"
rm -f /mock/losetup.calls
printf '%s\n' "$S/native /dev/loop1 ext4" "$S/control /dev/loop2 ext4" >> /mock/mounts
printf '%s\n' "$S/native.ext4 /dev/loop1" "$S/control.ext4 /dev/loop2" >> /mock/loops
touch $S/unexpected
t 'host: an unexpected entry refuses' 3 $B/decommission.sh host
rm $S/unexpected
# Review round 2: every precheck (output and lock too) runs before the first change.
echo "$O/submount /dev/loop9 ext4" >> /mock/mounts
t 'host: a mount under the output refuses before any change' 3 $B/decommission.sh host
check 'host: output refusal kept storage, mounts and output' \
  "[ -f $S/native.ext4 ] && [ -d $S ] && [ -d $O ] && grep -q '^$S/native ' /mock/mounts && grep -q '^$S/control ' /mock/mounts"
sed -i '/submount/d' /mock/mounts
chown 0:0 $O
t 'host: an output not owned by the provisioner refuses before any change' 3 $B/decommission.sh host
check 'host: owner refusal kept storage' "[ -f $S/native.ext4 ] && grep -q '^$S/native ' /mock/mounts"
chown 10001:10001 $O
mv $OUT/.lock-$OH /tmp/lock; mkdir $OUT/.lock-$OH
t 'host: a lock path that is not a file refuses before any change' 3 $B/decommission.sh host
check 'host: lock refusal kept storage' "[ -f $S/native.ext4 ] && grep -q '^$S/native ' /mock/mounts"
rmdir $OUT/.lock-$OH; mv /tmp/lock $OUT/.lock-$OH
rm -f /mock/losetup.calls
MOCK_LOSETUP_FAIL_FROM=3 t 'host: a failure after the first unmount reports partial state' 5 $B/decommission.sh host
has 'host: partial names the unmount' "partial: already changed by this run: unmounted:$S/native"
check 'host: partial run never says nothing changed' "! echo \"\$out\" | grep -q 'nothing changed'"
rm -f /mock/losetup.calls
printf '%s\n' "$S/native /dev/loop1 ext4" >> /mock/mounts
printf '%s\n' "$S/native.ext4 /dev/loop1" >> /mock/loops
MOCK_UMOUNT_BUSY=$S/control t 'host: a busy mount is rc 5' 5 $B/decommission.sh host
check 'host: busy mount deleted no file' "[ -f $S/native.ext4 ] && [ -f $S/control.ext4 ] && [ -d $O ]"
MOCK_LOOP_STAYS=1 t 'host: a loop still attached is rc 5' 5 $B/decommission.sh host
sed -i '/loop2$/d' /mock/loops
MOCK_RM_FAIL=$O t 'host: output deletion failing after the storage is gone is rc 5' 5 $B/decommission.sh host
has 'host: partial lists the deleted storage' "deleted:$S"
has 'host: partial shows the state' "state: $S absent, $O present"
t 'host: rerun completes' 0 $B/decommission.sh host
has 'host: restore inventories match' 'restore inventories: outputs=1 storage=1 only-storage=[] only-outputs=[]'
check 'host: this owner gone, the other owner kept' \
  "[ ! -e $S ] && [ ! -e $O ] && [ ! -e $OUT/.lock-$OH ] && [ -f $ST/$OTHER/native.ext4 ] && [ -d $OUT/aaaaaaaabbbb4ccc8dddeeeeeeeeeeee ] && grep -q '^$ST/$OTHER/native ' /mock/mounts"
t 'host: a second run is a no-op' 0 $B/decommission.sh host
mkdir $ST/77777777777747778777777777777777
t 'host: inventories differing is rc 8' 8 $B/decommission.sh host
has 'host: the orphan named' "only-storage=['77777777777747778777777777777777']"
t 'close: removes the admin DSN' 0 $B/decommission.sh close
check 'close: DSN gone' "[ ! -e $W/private/admin-dsn ]"
t 'close: again is a no-op' 0 $B/decommission.sh close

echo '## worker-swap.sh'
build legacy
for r in controller observer; do render "$B5ID" $r > /usr/local/lib/anxious-lookout/issue16/$r.sh; done
chmod 0700 /usr/local/lib/anxious-lookout/issue16/*.sh
cp -p /usr/local/lib/anxious-lookout/issue16/observer.sh /tmp/observer.b5
t 'placeholders refuse' 2 $P/worker-swap.sh apply
echo 'loaded active' > /mock/unit.issue16-controller.timer
t 'apply: running timer refuses' 3 $B/worker-swap.sh apply
echo 'loaded inactive' > /mock/unit.issue16-controller.timer
echo "$OLDWID" > /mock/docker-images
t 'apply: new image missing refuses' 3 $B/worker-swap.sh apply
echo "$WID" > /mock/docker-images
t 'apply: ok' 0 $B/worker-swap.sh apply
check 'apply: both scripts on the new image, mode kept' \
  "grep -q $WID /usr/local/lib/anxious-lookout/issue16/controller.sh && ! grep -q $B5ID /usr/local/lib/anxious-lookout/issue16/observer.sh && [ \$(stat -c %a /usr/local/lib/anxious-lookout/issue16/observer.sh) = 700 ]"
t 'apply: again skips both' 0 $B/worker-swap.sh apply
has 'apply: again says so' 'observer.sh already on the new image'
t 'legacy-stop: diagnose works on the swapped observer.sh' 0 $B/legacy-stop.sh diagnose
t 'rollback: ok' 0 $B/worker-swap.sh rollback
check 'rollback: byte-identical to before' "cmp -s /tmp/observer.b5 /usr/local/lib/anxious-lookout/issue16/observer.sh"
t 'rollback: again is a no-op' 0 $B/worker-swap.sh rollback
render "$OLDWID" observer > /usr/local/lib/anxious-lookout/issue16/observer.sh; chmod 0700 /usr/local/lib/anxious-lookout/issue16/observer.sh
t 'apply: a script on neither image refuses' 3 $B/worker-swap.sh apply

echo "passed $pass, failed $failed"
[ "$failed" = 0 ]
