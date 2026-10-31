#!/bin/sh
# #72 F: read-only before/after inventory of the production node. Run as root, once per checkpoint.
# Prints selected fields only: no Secret object, credential file content, token or DSN is read.
# Every lookup's own exit status is checked before its output is used; a failed lookup prints the
# fixed line "<name> lookup_failed" (never its diagnostics) and makes the final rc 3.
# rc 0 = every lookup answered; rc 3 = at least one lookup_failed line.
set -u
K='/usr/local/bin/k3s kubectl'
SUPPLY_LIB=/usr/local/lib/anxious-lookout/issue16
rc=0
say() { printf '%s %s\n' "$1" "$2"; }
fail() { say "$1" lookup_failed; rc=3; }
q() { timeout 30 $K -n m2-hosting exec postgres-0 -- psql -U postgres -d hosting -At -v ON_ERROR_STOP=1 -c "$1" 2>/dev/null; }
py() { code=$1; shift; python3 -I -c "$code" "$@" 2>/dev/null; }
lines() { # name, command...: one line per result line, or lookup_failed when the command fails.
  name=$1; shift
  out=$("$@" 2>/dev/null) || { fail "$name"; return; }
  [ -n "$out" ] || { say "$name" none; return; }
  printf '%s\n' "$out" | while IFS= read -r l; do say "$name" "$l"; done
}
# A named object that may legitimately be absent: --ignore-not-found turns absence into rc 0 and
# empty output, so any non-zero rc is a real failure (auth, transport, timeout).
maybe() { # name, kubectl args...
  name=$1; shift
  out=$(timeout 30 $K "$@" --ignore-not-found 2>/dev/null) || { fail "$name"; return; }
  say "$name" "${out:-absent}"
}

say at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
lines node timeout 30 $K get nodes -o jsonpath='{range .items[*]}{.metadata.name}|{.status.conditions[?(@.type=="Ready")].status}|cpu={.status.allocatable.cpu}|memory={.status.allocatable.memory}|pods={.status.allocatable.pods}{"\n"}{end}'
for ns in codex-runner codex-broker; do
  lines "pod.$ns" timeout 30 $K -n $ns get pods -o jsonpath='{range .items[*]}{.metadata.name}|{.metadata.uid}|{.status.phase}|{.status.containerStatuses[*].restartCount}|{.status.containerStatuses[*].imageID}{"\n"}{end}'
done
lines pod.api timeout 30 $K -n m2-hosting get pods -l app=hosting-api -o jsonpath='{range .items[*]}{.metadata.name}|{.status.phase}|{.status.containerStatuses[*].imageID}{"\n"}{end}'
# Runner capacity: the Pod quota the rollout renders (hard and used Pod counts only).
j=$(timeout 30 $K -n codex-runner get resourcequota runner-bootstrap-no-pods -o json 2>/dev/null) \
  && c=$(printf '%s' "$j" | py 'import json,sys; s=json.load(sys.stdin)["status"]; print("hard=%s|used=%s" % (s["hard"]["count/pods"], s["used"]["count/pods"]))') \
  && say capacity.pods "$c" || fail capacity.pods
lines registry q "select owner_id||'|'||phase||'|'||generation||'|'||revision||'|'||coalesce(pod_uid::text,'-') from runner_runtime.registry order by 1"
lines binding q "select owner_id||'|'||coalesce(plan::jsonb->>'image','-')||'|'||coalesce(plan::jsonb->'executor'->>'image','-') from runner_bindings.bindings order by 1"
# #66: the stuck owner must be unchanged before and after every step.
lines owner66 q "select owner_id||'|'||phase||'|'||generation||'|'||revision||'|'||coalesce(pod_uid::text,'-') from runner_runtime.registry where owner_id::text like '3cb453bf%'"

for u in issue16-supply.timer issue16-supply.service; do
  v=$(timeout 10 systemctl show -p LoadState -p ActiveState -p UnitFileState --value "$u" 2>/dev/null) || { fail "unit.$u"; continue; }
  say "unit.$u" "$(printf '%s' "$v" | tr '\n' '|')"
done
# pgrep: 0 = matches, 1 = none, >=2 = error.
n=$(pgrep -fc "$SUPPLY_LIB/supply.sh" 2>/dev/null); s=$?
case $s in 0|1) say supply.processes "${n:-0}";; *) fail supply.processes;; esac
for f in /etc/systemd/system/issue16-supply.timer /etc/systemd/system/issue16-supply.service $SUPPLY_LIB/supply.sh $SUPPLY_LIB/stop-supply.sh; do
  if [ -L "$f" ]; then say file "symlink $f"
  elif [ -e "$f" ]; then m=$(stat -c '%F|%a|%U|%s' "$f" 2>/dev/null) && say file "$m $f" || fail "file $f"
  else say file "absent $f"; fi
done
# Names, types and modes only; never contents.
d=/etc/anxious-lookout/issue16/supply
if [ -e "$d" ]; then lines supply.credentials find "$d" -mindepth 1 -printf '%y %m %u %f\n'; else say supply.credentials absent; fi
# Attempt records hold owner/state/generation/podUid/snapshotId/result metadata only. Selected:
# total, count per result, and the five newest as mtime|owner|result.
a=/var/lib/anxious-lookout/issue16-d4/supply/attempts
if [ -d "$a" ]; then
  lines supply.attempts py '
import collections, json, os, sys, time
entries = [e for e in os.scandir(sys.argv[1]) if e.is_file(follow_symlinks=False)]
rows = []
for e in entries:
    with open(e.path) as f:
        r = json.load(f)
    rows.append((e.stat(follow_symlinks=False).st_mtime, str(r.get("owner", "-")), str(r.get("result", "-"))))
print("total=%d" % len(rows))
for result, n in sorted(collections.Counter(r[2] for r in rows).items()):
    print("result.%s=%d" % (result, n))
for m, owner, result in sorted(rows)[-5:]:
    print("newest %s|%s|%s" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(m)), owner, result))
' "$a"
else say supply.attempts absent; fi
# The name comes first so an existing object with empty rules/subjects is not mistaken for absence.
maybe rbac.supply.role -n codex-runner get role runner-pool-supply -o jsonpath='{.metadata.name}|{.rules}'
maybe rbac.supply.rolebinding -n codex-runner get rolebinding runner-pool-supply -o jsonpath='{.metadata.name}|{.subjects}'
maybe rbac.supply.clusterrole get clusterrole runner-pool-supply -o jsonpath='{.metadata.name}|{.rules}'
maybe rbac.supply.clusterrolebinding get clusterrolebinding runner-pool-supply -o jsonpath='{.metadata.name}|{.subjects}'
maybe sa.supply -n codex-runner get serviceaccount runner-pool-supply -o jsonpath='{.metadata.uid}'
lines cilium timeout 30 $K -n kube-system get configmap cilium-config -o jsonpath='enable-l7-proxy={.data.enable-l7-proxy}|kube-proxy-replacement={.data.kube-proxy-replacement}'
j=$(timeout 30 $K -n codex-runner get cnp runner-callback-draft -o json 2>/dev/null) \
  && h=$(printf '%s' "$j" | py 'import hashlib,json,sys; print(hashlib.sha256(json.dumps(json.load(sys.stdin)["spec"],sort_keys=True).encode()).hexdigest())') \
  && say cnp.runner "$h" || fail cnp.runner
lines storageclass timeout 30 $K get storageclass -o jsonpath='{range .items[*]}{.metadata.name}|{.provisioner}|{.reclaimPolicy}|{.volumeBindingMode}{"\n"}{end}'
lines python py 'import sys; print("%d.%d" % sys.version_info[:2])'
say "inventory rc" "$rc"
exit $rc
