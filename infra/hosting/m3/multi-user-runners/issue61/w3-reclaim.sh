#!/bin/sh
# #61 dev-VM gate W3: R2 operator reclaim of the blocked …5804 runner, run once by the user as root in the guest
# after review cleared this SHA-256 and the user gave the go with W0's count of …5804 running conversations.
#   1. precheck (read-only): runner-controller is the Deployment W2 created and Ready; …0048/…5802 already
#      released by the dead-runner path; …5804 Pod b9bbc857 Running with the runner container running; registry
#      on that Pod; no receipt yet. Prints …5804's running conversations NOW (R2 fails them: codex_runner_lost).
#   2. reclaim, exactly the reviewed CLI inside the controller Pod:
#        python -B -m ops.runner_controller_service reclaim --owner <…5804> --pod-uid <b9bbc857…>
#      It observes /health, the UIDs and a drain asked now, and refuses (rc 3, code + observed) unless the
#      runner is blocked and the drain is refused 409/503. Its JSON line is printed unchanged.
#   3. read-only wait (bounded, 600 s) for the controller loop: Pod b9bbc857 gone (404), stop completed with
#      a v4 receipt and proof podUid b9bbc857, registry idle_stopped with no Pod. Then the evidence lines.
# No Pod, object or row is changed by this script itself except through the reclaim command.
# rc: 0 reclaimed and released; 3 precheck refused, nothing ran; 4 reclaim refused (its code is printed,
#     nothing reclaimed); 5 reclaim failed (fixed text) or its outcome is unclear; 6 accepted but not released
#     within 600 s (read-only status printed; do not rerun, back checks).
set -u
umask 077
here=$(dirname "$0")
. "$here/lib.sh"
trap 'exit 130' INT TERM
POD_5804=b9bbc857-cd36-4a7b-89b5-1ca7c7d3b61b
NAME=$(pod_name "$OWNER_5804")
refuse() { echo "precheck: $1; nothing ran"; echo 'result rc=3'; exit 3; }
q() { sql "$1"; }

[ "$(id -u)" = 0 ] || refuse 'not root'
CHANGES=$(ls -d /root/issue61/evidence/w2-*/changes.json 2>/dev/null)
[ "$(echo "$CHANGES" | grep -c .)" = 1 ] || refuse 'expected exactly one W2 evidence directory'
want=$(python3 -I -c 'import json,sys; c=json.load(open(sys.argv[1])); print(c["created"].get("Deployment runner-control/runner-controller","") if not c.get("pending") and not c["uncertain"] else "")' "$CHANGES") \
  || refuse 'W2 changes.json unreadable'
[ -n "$want" ] || refuse 'W2 did not finish cleanly (pending/uncertain or no controller UID recorded)'
d=$(k -n runner-control get deployment runner-controller -o jsonpath='{.metadata.uid}|{.status.readyReplicas}') \
  || refuse 'controller lookup failed'
[ "$d" = "$want|1" ] || refuse "runner-controller is $d, want $want|1"
rows=$(q "BEGIN READ ONLY;
SELECT 'registry '||owner_id||'|'||phase||'|'||generation||'|'||revision||'|'||coalesce(pod_uid::text,'-') FROM runner_runtime.registry
 WHERE owner_id IN ($OWNERS_SQL) ORDER BY owner_id;
SELECT 'stop '||owner_id||'|'||generation||'|'||pod_uid||'|receipt='||coalesce(receipt->>'version','-') FROM runner_termination.stops
 WHERE completed_at IS NULL ORDER BY owner_id;
SELECT 'running '||count(*) FROM research.conversations WHERE owner_id='$OWNER_5804' AND state='running';
COMMIT;") || refuse 'database lookup failed'
echo "$rows"
echo "$rows" | grep -qx "registry $OWNER_0048|idle_stopped|.*|-" || refuse '…0048 is not released yet'
echo "$rows" | grep -qx "registry $OWNER_5802|idle_stopped|.*|-" || refuse '…5802 is not released yet'
echo "$rows" | grep -q "^registry $OWNER_5804|runner_unreachable|[0-9]*|[0-9]*|$POD_5804\$\|^registry $OWNER_5804|stopping|[0-9]*|[0-9]*|$POD_5804\$" \
  || refuse '…5804 registry is not on Pod b9bbc857 in runner_unreachable/stopping'
echo "$rows" | grep "^stop $OWNER_5804|" | grep -qv 'receipt=-$' && refuse '…5804 stop already has a receipt'
p=$(k -n codex-runner get pod "$NAME" -o jsonpath='{.metadata.uid}|{.status.phase}|{.metadata.deletionTimestamp}|{.status.containerStatuses[?(@.name=="runner")].state}') \
  || refuse 'pod lookup failed'
case "$p" in "$POD_5804|Running||"*'"running"'*) echo "pod $NAME $p";; *) refuse "…5804 Pod is $p";; esac
echo "running conversations of …5804 now: $(echo "$rows" | sed -n 's/^running //p') (R2 fails them with codex_runner_lost)"

echo "reclaim: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
out=$(timeout 120 "$K3S" kubectl -n runner-control exec deploy/runner-controller -- \
  python -B -m ops.runner_controller_service reclaim --owner "$OWNER_5804" --pod-uid "$POD_5804" 2>/dev/null); r=$?
line=$(echo "$out" | grep '^{"' | tail -1)
echo "reclaim output: ${line:-none} (rc $r)"
case "$r:$line" in
  0:*'"result": "accepted"'*) ;;
  3:*'"result": "refused"'*) echo 'reclaim refused: nothing reclaimed'; echo 'result rc=4'; exit 4;;
  *) echo 'reclaim outcome unclear: do not rerun; back checks read-only'; echo 'result rc=5'; exit 5;;
esac

i=0
while [ $i -lt 60 ]; do
  gone=$(k -n codex-runner get pod "$NAME" --ignore-not-found -o jsonpath='{.metadata.uid}') || gone=lookup-failed
  reg=$(q "SELECT phase||'|'||coalesce(pod_uid::text,'-') FROM runner_runtime.registry WHERE owner_id='$OWNER_5804';") || reg=lookup-failed
  if [ -z "$gone" ] && [ "$reg" = 'idle_stopped|-' ]; then break; fi
  i=$((i+1)); sleep 10
done
echo "released check: $(date -u +%Y-%m-%dT%H:%M:%SZ) pod=${gone:-absent} registry=$reg"
ev=$(q "SELECT 'stop '||generation||'|'||pod_uid||'|v'||(receipt->>'version')||'|drain='||(receipt->'operator'->>'drainStatus')
  ||'|health='||(receipt->'operator'->'health'->>'reason')||'|proof='||(proof->>'podUid')||'|'||(proof->>'evidence')||'|boot='||(proof->>'bootId')
  ||'|completed='||coalesce(completed_at::text,'-') FROM runner_termination.stops WHERE owner_id='$OWNER_5804' AND pod_uid='$POD_5804';
SELECT 'running '||count(*)||' failed_lost '||(SELECT count(*) FROM research.conversations WHERE owner_id='$OWNER_5804' AND error_code='codex_runner_lost')
  FROM research.conversations WHERE owner_id='$OWNER_5804' AND state='running';
SELECT 'quota used '||count(*) FROM runner_runtime.registry WHERE pod_uid IS NOT NULL;") || ev='evidence lookup failed'
echo "$ev"
if [ -z "$gone" ] && [ "$reg" = 'idle_stopped|-' ] && echo "$ev" | grep -q "^stop .*|$POD_5804|v4|.*|proof=$POD_5804|pod-deleted|"; then
  echo 'result rc=0'; exit 0
fi
echo 'accepted but not released as expected within 600 s: do not rerun; back checks read-only'
echo 'result rc=6'; exit 6
