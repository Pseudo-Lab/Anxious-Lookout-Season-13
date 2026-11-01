#!/bin/sh
# #61 dev-VM gate W0: read-only baseline, run as root in the guest (sudo -n), after review cleared this SHA-256.
# Changes nothing: kubectl get/exec of read-only commands, psql SELECTs in one READ ONLY transaction,
# systemctl show, sha256sum. The …5804 ledger probe opens the runner's own SQLite ledger with mode=ro in one
# read transaction and prints only: blocked reason, rejected row count, whether the ledger's current snapshot
# fingerprint is rejected (boolean), and the non-secret snapshotId. No token, fingerprint, DSN or payload.
# The same conditions are W2's precheck (lib.sh), so W0 and W2 can never disagree on what "expected" means.
# rc: 0 every check matched; 3 a lookup failed (nothing judged); 4 looked up fine but a condition differs.
set -u
umask 077
here=$(dirname "$0")
. "$here/lib.sh"
trap 'exit 130' INT TERM

echo "w0: $(date -u +%Y-%m-%dT%H:%M:%SZ) read-only baseline"
lookups=0; differs=0
note() { echo "$1"; }
fail_lookup() { echo "LOOKUP FAILED: $1"; lookups=$((lookups+1)); }
differ() { echo "DIFFERS: $1"; differs=$((differs+1)); }

# Node identity (bootID is linked to W6).
n=$(k get node "$NODE" -o jsonpath='{.metadata.uid}|{.status.nodeInfo.bootID}|{.status.conditions[?(@.type=="Ready")].status}') \
  && note "node $NODE uid|bootID|ready=$n" || fail_lookup node

# Host timers and services (PM condition 2): every unit loaded and inactive/failed.
for u in $UNITS; do
  v=$(unit_state "$u") || { fail_lookup "systemctl $u"; continue; }
  note "unit $u $v"
  case "$v" in 'loaded inactive'|'loaded failed') ;; *) differ "unit $u is $v";; esac
done

# runner-control must not exist yet.
r=$(k get namespace runner-control -o name --ignore-not-found) || fail_lookup 'namespace runner-control'
[ -z "${r:-}" ] && note 'namespace runner-control absent' || differ "namespace runner-control present"

# Fixture Pods: full UID, phase, per-container state, image and imageID.
for f in $FIXTURES; do
  set -- $(echo "$f" | tr '|' ' ')  # owner pod-uid expected-case
  owner=$1 uid=$2 want=$3 name=$(pod_name "$1")
  p=$(k -n codex-runner get pod "$name" -o jsonpath='{.metadata.uid}|{.status.phase}|{.metadata.deletionTimestamp}') \
    || { fail_lookup "pod $name"; continue; }
  note "pod $name $p"
  c=$(k -n codex-runner get pod "$name" -o jsonpath='{range .status.containerStatuses[*]}{.name}={.state}|{.image}|{.imageID};{end}') \
    || { fail_lookup "containers $name"; continue; }
  note "containers $name $c"
  case "$want" in
    failed) [ "$p" = "$uid|Failed|" ] || differ "$name expected Failed with uid $uid";;
    runner-dead) case "$p;$c" in "$uid|Running|;"*'runner={"terminated"'*) ;;
                   *) differ "$name expected Running with the runner container terminated";; esac;;
    running) case "$p;$c" in "$uid|Running|;"*'runner={"running"'*) ;;
               *) differ "$name expected Running with the runner container running";; esac;;
  esac
done

# Database, one READ ONLY transaction. Full owner/state/Pod UIDs, fence, open stops, pending work.
rows=$(sql "BEGIN READ ONLY;
SELECT 'registry '||r.owner_id||'|'||r.state_id||'|'||r.phase||'|'||r.generation||'|'||r.revision||'|'||coalesce(r.pod_uid::text,'-')
       ||'|active='||r.active_requests||'|ambiguous='||r.ambiguous FROM runner_runtime.registry r ORDER BY r.owner_id;
SELECT 'stop '||s.owner_id||'|'||s.generation||'|'||s.pod_uid||'|'||s.stop_id||'|receipt='||coalesce(s.receipt->>'version','-')
       ||'|anchored='||(s.node_uid IS NOT NULL) FROM runner_termination.stops s WHERE s.completed_at IS NULL ORDER BY s.owner_id;
SELECT 'running '||a.id||'|'||(SELECT count(*) FROM research.conversations c WHERE c.owner_id=a.id AND c.state='running')
       ||'|admissions='||(SELECT count(*) FROM runner_runtime.admissions m WHERE m.owner_id=a.id AND m.completed_at IS NULL)
       FROM auth.accounts a WHERE a.id IN ($OWNERS_SQL) ORDER BY a.id;
SELECT 'binding '||b.owner_id||'|'||b.state_id||'|image='||(b.plan->>'image')||'|executor='||coalesce(b.plan->'executor'->>'image','-')
       FROM runner_bindings.bindings b WHERE b.owner_id IN ($OWNERS_SQL) ORDER BY b.owner_id;
SELECT 'waiting '||count(*) FROM runner_runtime.registry WHERE phase IN ('provisioning','capacity_wait');
SELECT 'grants061 '||has_column_privilege('anxious_runner_observer','research.conversations','error_code','UPDATE');
COMMIT;") && echo "$rows" || fail_lookup database
w=$(echo "${rows:-}" | sed -n 's/^waiting //p')
[ "${w:-x}" = 0 ] || differ "owners waiting for a Pod: ${w:-unknown}"

# Hosting objects the W2 window touches.
a=$(k -n m2-hosting get deployment api -o jsonpath='{.metadata.uid}|{.spec.template.spec.containers[0].image}') \
  && note "api deployment $a" || fail_lookup 'api deployment'
b=$(k -n codex-broker get pod codex-broker-0 -o jsonpath='{.metadata.uid}|{.status.phase}|{range .status.containerStatuses[*]}{.name}:{.ready};{end}') \
  && note "broker $b" || fail_lookup broker
q=$(k -n codex-runner get resourcequota runner-bootstrap-no-pods -o go-template='{{index .spec.hard "count/pods"}}|{{index .status.used "count/pods"}}') \
  && note "quota pods hard|used=$q" || fail_lookup quota

# W1 inputs: archives on the guest, and whether the images are already imported.
( cd "$ARCHIVES" && sha256sum -c --quiet "$here/archives.sha256" ) >/dev/null 2>&1 \
  && note 'archives sha256 ok' || fail_lookup 'archives sha256'
for ref in $IMAGE_REFS; do
  if ctr_has "$ref"; then note "imported $ref"; else note "not imported $ref"; fi
done

# …5804 ledger probe (read-only, see header). Output: one line, fixed fields.
probe=$(k -n codex-runner exec "$(pod_name "$OWNER_5804")" -c runner -- python -I -c '
import json, os, sqlite3, sys
path = os.path.join(os.environ["CODEX_ACCESS_CONTROL_DIR"], "service-access.sqlite3")
if not os.path.isfile(path) or os.path.islink(path):
    print("ledger missing"); sys.exit(0)
try:
    db = sqlite3.connect("file:" + path + "?mode=ro", uri=True, isolation_level=None, timeout=2)
    db.execute("BEGIN")
    rows = db.execute("SELECT payload FROM metadata").fetchall()
    value = json.loads(rows[0][0]) if len(rows) == 1 else None
    if not isinstance(value, dict) or not isinstance(value.get("snapshotFingerprint"), str):
        print("ledger unreadable: metadata"); sys.exit(0)
    rejected = db.execute("SELECT count(*) FROM rejected").fetchone()[0]
    member = db.execute("SELECT 1 FROM rejected WHERE fingerprint=?", (value["snapshotFingerprint"],)).fetchone() is not None
    db.execute("COMMIT")
    print("ledger blocked=%s rejected=%d current_rejected=%s snapshotId=%s"
          % (value.get("blocked"), rejected, member, value.get("snapshotId")))
except sqlite3.Error as error:
    print("ledger unreadable: " + type(error).__name__)
' 2>/dev/null) && note "5804 $probe" || fail_lookup '5804 ledger probe'

echo "w0 summary lookups_failed=$lookups differs=$differs"
[ "$lookups" = 0 ] || { echo 'result rc=3'; exit 3; }
[ "$differs" = 0 ] || { echo 'result rc=4'; exit 4; }
echo 'result rc=0'
