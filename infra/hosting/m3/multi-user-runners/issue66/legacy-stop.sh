#!/bin/sh
# #66: stop owner 3cb453bf's gen-9 Pod (rendered before #59) through the observer's own stop path. The
# observer's --legacy-pod-uid accepts this one Pod by identity for anchor/drain only
# (backend/ops/runner_stop_observer.py); entry, creation and idle keep the exact spec check.
#   legacy-stop.sh diagnose   read-only: ops.runner_observer_diagnostics with the same identity check
#   legacy-stop.sh observe    one observer run for this owner. Pod present: anchor -> drain -> DELETE with a UID
#                             precondition. Pod gone: retained receipt -> host proof -> release (idle_stopped)
#   legacy-stop.sh evidence   read-only drain-refusal evidence: Pod state and events, the runner's /health asked
#                             from inside the Pod (the token never leaves it), stop/registry/entry rows
# Window: the observer.sh/controller.sh worker image is WORKER_IMAGE_ID (B5 procedure), controller and
# observer units stopped. Commands are built from the installed observer.sh: the same image, mounts,
# inputs, DB login and host proof, with only the cidfile and the arguments after the image replaced.
# rc: 0 done; 2 placeholders left; 3 precheck refused, nothing ran; 4 the tool ran and refused (its result
#     line says where; the observer deletes nothing before a drain receipt); 5 the tool did not finish
set -u
WORKER_IMAGE_ID=@@WORKER_IMAGE_ID@@  # the #66 merge commit's provisioner image, sha256:<64 hex>
OWNER=3cb453bf-d870-40dc-b7e1-3a58aa0cbaf2
POD_UID=880bf6cd-70ea-4625-9185-c1604c8f8466
POD=runner-3cb453bfd87040dcb7e13a58aa0cbaf2
NS=codex-runner
K='/usr/local/bin/k3s kubectl'
BIN=/usr/local/lib/anxious-lookout/issue16
UNITS='issue16-controller.timer issue16-controller.service issue16-observer.timer issue16-observer.service'
RUN=/run/issue66-legacy
action=${1:-}
case "$WORKER_IMAGE_ID" in *@@*) echo 'placeholders not filled'; exit 2;; esac
refuse() { echo "precheck: $1; nothing ran"; echo 'result rc=3'; exit 3; }
echo "$WORKER_IMAGE_ID" | grep -qxE 'sha256:[0-9a-f]{64}' || refuse 'WORKER_IMAGE_ID is not an image ID'
case "$action" in diagnose|observe|evidence) ;; *) echo 'usage: legacy-stop.sh diagnose|observe|evidence'; exit 3;; esac
q() { timeout 30 $K -n m2-hosting exec postgres-0 -- psql -U postgres -d hosting -At -v ON_ERROR_STOP=1 -c "$1" 2>/dev/null; }

# The Pod: absent, or this UID (never another generation's Pod).
p=$(timeout 30 $K -n $NS get pod "$POD" --ignore-not-found \
  -o jsonpath='{.metadata.uid}|{.metadata.deletionTimestamp}|{.status.phase}|{.spec.nodeName}' 2>/dev/null) \
  || refuse 'runner pod lookup failed'
echo "pod $POD ${p:-absent}"
[ -z "$p" ] || [ "${p%%|*}" = "$POD_UID" ] || refuse "$POD has UID ${p%%|*}, not $POD_UID"

if [ "$action" = evidence ]; then
  rc=0
  if [ -n "$p" ]; then
    timeout 30 $K -n $NS get pod "$POD" -o jsonpath='{range .status.containerStatuses[*]}container {.name} ready={.ready} restarts={.restartCount} state={.state}{"\n"}{end}' 2>/dev/null || rc=1
    timeout 30 $K -n $NS get events --field-selector "involvedObject.uid=$POD_UID" \
      -o custom-columns=LAST:.lastTimestamp,TYPE:.type,REASON:.reason,MESSAGE:.message --no-headers 2>/dev/null | tail -n 20 || rc=1
    if [ -z "$(echo "$p" | cut -d'|' -f2)" ]; then
      # Inside the runner container: the legacy spec mounts the token at /transport/token. Only fixed fields print.
      timeout 30 $K -n $NS exec "$POD" -c runner -- python -I -c '
import json, os, urllib.request
t = open(os.environ.get("RUNNER_TOKEN_FILE", "/transport/token")).read().strip()
r = urllib.request.Request("http://127.0.0.1:8080/health",
    headers={"Authorization": "Bearer " + t, "X-Runner-State-Id": os.environ["RUNNER_STATE_ID"]})
v = json.load(urllib.request.urlopen(r, timeout=5))
print("health " + json.dumps({k: v.get(k) for k in ("ownerId", "stateId", "podUid", "available", "reason", "verification")}))
' 2>/dev/null || { echo 'health: exec or request failed'; rc=1; }
    else
      echo 'health: skipped, the Pod is deleting'
    fi
  fi
  q "select 'registry '||phase||'|'||generation||'|'||revision||'|'||coalesce(pod_uid::text,'-')||'|'||active_requests||'|'||ambiguous from runner_runtime.registry where owner_id='$OWNER'" || { echo 'registry query failed'; rc=1; }
  q "select 'stop '||stop_id||'|'||generation||'|'||pod_uid||'|'||coalesce(boot_id::text,'unanchored')||'|receipt='||(receipt is not null)||'|clean='||coalesce(receipt->>'clean','-')||'|completed='||coalesce(completed_at::text,'-') from runner_termination.stops where owner_id='$OWNER' order by generation desc limit 2" || { echo 'stop query failed'; rc=1; }
  q "select 'pending entries='||(select count(*) from runner_termination.entries where owner_id='$OWNER' and consumed_at is null)||' admissions='||(select count(*) from runner_runtime.admissions where owner_id='$OWNER' and completed_at is null)||' running_conversations='||(select count(*) from research.conversations where owner_id='$OWNER' and state='running')" || { echo 'pending query failed'; rc=1; }
  echo "result rc=$rc"; exit $rc
fi

# Window: units stopped, observer.sh on the reviewed worker image.
for u in $UNITS; do
  v=$(timeout 10 systemctl show -p LoadState -p ActiveState "$u" 2>/dev/null) || refuse "systemctl show $u failed"
  v="$(echo "$v" | sed -n 's/^LoadState=//p') $(echo "$v" | sed -n 's/^ActiveState=//p')"
  case "$v" in 'loaded inactive'|'loaded failed') ;; *) refuse "$u is $v (stop the controller and observer first)";; esac
done
i=$(timeout 30 docker image inspect --format '{{.Id}}' "$WORKER_IMAGE_ID" 2>/dev/null) && [ "$i" = "$WORKER_IMAGE_ID" ] \
  || refuse 'worker image not in the Docker store'
cmd=$(python3 -I - "$BIN/observer.sh" "$action" "$OWNER" "$POD_UID" "$WORKER_IMAGE_ID" "$RUN" <<'PY'
import sys
path, action, owner, pod, image, run = sys.argv[1:]
lines = open(path).read().split('\n')
assert lines[:2] == ['#!/bin/sh', 'set -eu'] and lines[2].startswith('exec ') and lines[3:] == ['']
cmd = lines[2][len('exec '):]
tail = (' /rollout/worker.py observer --inputs /private/inputs.json --limit 10'
        ' --cursor-file /var/lib/anxious-lookout/issue16-d4/observer/cursor.json --idle')
cid = ' --cidfile=/run/issue16-observer/container-id '
assert cmd.endswith(tail) and cmd.count(tail) == 1 and cmd.count(cid) == 1
assert cmd.count(' --entrypoint=python ' + image + ' -B' + tail) == 1
args = ' --inputs /private/inputs.json --owner ' + owner + ' --legacy-pod-uid ' + pod
new = (' -m ops.runner_observer_diagnostics' if action == 'diagnose' else ' /rollout/worker.py observer') + args
print(cmd.replace(cid, ' --cidfile=' + run + '/container-id ')[:-len(tail)] + new)
PY
) || refuse "$BIN/observer.sh is not the rendered observer command on $WORKER_IMAGE_ID"
if [ "$action" = observe ]; then
  n=$(q "select count(*) from runner_termination.entries e join auth.sessions s on s.token_hash=e.session_hash where e.owner_id='$OWNER' and e.consumed_at is null and s.expires_at>clock_timestamp()") \
    || refuse 'entry query failed'
  # release would consume such an entry and start generation 10 instead of idle_stopped.
  [ "$n" = 0 ] || refuse "$n unconsumed entries with a valid session (release would re-enter)"
fi
install -d -m 0700 "$RUN" && rm -f "$RUN/container-id" || refuse "$RUN not usable"
out=$(timeout 300 sh -c "$cmd" 2>&1); t=$?
if [ -f "$RUN/container-id" ]; then
  cid=$(cat "$RUN/container-id"); docker rm -f "$cid" >/dev/null 2>&1; rm -f "$RUN/container-id"
fi
echo "$out"
r=$(echo "$out" | python3 -I -c '
import json, sys
lines = [json.loads(l) for l in sys.stdin.read().splitlines() if l.startswith("{")]
assert len(lines) == 1 and lines[0]["owner"] == sys.argv[1]
print(lines[0]["stage"], lines[0]["result"])
' "$OWNER" 2>/dev/null) || { echo "no single result line for $OWNER (tool rc $t)"; echo 'result rc=5'; exit 5; }
echo "tool rc $t: $r"
case "$action:$r:$t" in
  'diagnose:termination-diagnose ready_for_observation:0'|'diagnose:termination-diagnose retained_receipt_ready:0'|\
  'diagnose:termination-diagnose awaiting_pod_deletion:0'|'diagnose:termination-diagnose no_stop:0') rc=0 ;;
  'observe:termination observed:0') rc=0 ;;
  diagnose:*|observe:*reconciliation_required:*) rc=4 ;;
  *) rc=5 ;;
esac
p=$(timeout 30 $K -n $NS get pod "$POD" --ignore-not-found -o jsonpath='{.metadata.uid}|{.metadata.deletionTimestamp}|{.status.phase}' 2>/dev/null) || p=lookup-failed
echo "after: pod $POD ${p:-absent}"
q "select 'after: registry '||phase||'|'||generation||'|'||coalesce(pod_uid::text,'-')||' stop boot='||coalesce(s.boot_id::text,'unanchored')||' receipt='||(s.receipt is not null)||' completed='||coalesce(s.completed_at::text,'-') from runner_runtime.registry r left join runner_termination.stops s on s.owner_id=r.owner_id and s.generation=r.generation where r.owner_id='$OWNER'" \
  || echo 'after: registry query failed'
[ $rc = 4 ] && echo "refused: run 'legacy-stop.sh evidence', post both outputs to #66; delete nothing by hand"
echo "result rc=$rc"
exit $rc
