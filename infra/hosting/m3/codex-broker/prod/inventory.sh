#!/bin/sh
# #72 F: read-only before/after inventory of the production node. Run as root, once per checkpoint.
# Prints selected fields only: no Secret object, credential file content, token or DSN is read.
# rc 0 = every lookup answered; rc 3 = at least one lookup failed (its line says lookup_failed).
set -u
K='/usr/local/bin/k3s kubectl'
SUPPLY_LIB=/usr/local/lib/anxious-lookout/issue16
rc=0
say() { printf '%s %s\n' "$1" "$2"; }
fail() { say "$1" lookup_failed; rc=3; }
q() { timeout 30 $K -n m2-hosting exec postgres-0 -- psql -U postgres -d hosting -At -v ON_ERROR_STOP=1 -c "$1" 2>/dev/null; }
lines() { # name, command...: one output line per result line, or lookup_failed.
  name=$1; shift
  out=$("$@") || { fail "$name"; return; }
  [ -n "$out" ] || { say "$name" none; return; }
  printf '%s\n' "$out" | while IFS= read -r l; do say "$name" "$l"; done
}

say at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
lines node timeout 30 $K get nodes -o jsonpath='{range .items[*]}{.metadata.name}|{.status.conditions[?(@.type=="Ready")].status}{"\n"}{end}'
for ns in codex-runner codex-broker; do
  lines "pod.$ns" timeout 30 $K -n $ns get pods -o jsonpath='{range .items[*]}{.metadata.name}|{.metadata.uid}|{.status.phase}|{.status.containerStatuses[*].restartCount}|{.status.containerStatuses[*].imageID}{"\n"}{end}'
done
lines pod.api timeout 30 $K -n m2-hosting get pods -l app=hosting-api -o jsonpath='{range .items[*]}{.metadata.name}|{.status.phase}|{.status.containerStatuses[*].imageID}{"\n"}{end}'
lines registry q "select owner_id||'|'||phase||'|'||generation||'|'||revision||'|'||coalesce(pod_uid::text,'-') from runner_runtime.registry order by 1"
lines binding q "select owner_id||'|'||coalesce(plan::jsonb->>'image','-')||'|'||coalesce(plan::jsonb->'executor'->>'image','-') from runner_bindings.bindings order by 1"
# #66: the stuck owner must be unchanged before and after every step.
lines owner66 q "select owner_id||'|'||phase||'|'||generation||'|'||revision||'|'||coalesce(pod_uid::text,'-') from runner_runtime.registry where owner_id::text like '3cb453bf%'"
for u in issue16-supply.timer issue16-supply.service; do
  v=$(timeout 10 systemctl show -p LoadState -p ActiveState -p UnitFileState --value "$u" 2>/dev/null) || { fail "unit.$u"; continue; }
  say "unit.$u" "$(printf '%s' "$v" | tr '\n' '|')"
done
n=$(pgrep -fc "$SUPPLY_LIB/supply.sh"); [ "$n" ] || n=0; say supply.processes "$n"
for f in /etc/systemd/system/issue16-supply.timer /etc/systemd/system/issue16-supply.service $SUPPLY_LIB/supply.sh $SUPPLY_LIB/stop-supply.sh; do
  if [ -L "$f" ]; then say file "symlink $f"; elif [ -e "$f" ]; then say file "$(stat -c '%F %a %U %s' "$f") $f"; else say file "absent $f"; fi
done
# Names, types and modes only; never contents.
d=/etc/anxious-lookout/issue16/supply
if [ -e "$d" ]; then lines supply.credentials find "$d" -mindepth 1 -printf '%y %m %u %f\n'; else say supply.credentials absent; fi
a=/var/lib/anxious-lookout/issue16-d4/supply/attempts
if [ -d "$a" ]; then lines supply.attempts sh -c "find '$a' -type f | wc -l"; else say supply.attempts absent; fi
lines rbac.supply sh -c "for o in 'role -n codex-runner' clusterrole; do $K get \$o runner-pool-supply -o jsonpath='{.kind}|{.rules}{\"\n\"}' 2>&1 | sed 's/.*NotFound.*/absent/'; done"
lines sa.supply sh -c "$K -n codex-runner get serviceaccount runner-pool-supply -o jsonpath='{.metadata.uid}' 2>&1 | sed 's/.*NotFound.*/absent/'"
lines cilium timeout 30 $K -n kube-system get configmap cilium-config -o jsonpath='enable-l7-proxy={.data.enable-l7-proxy}|kube-proxy-replacement={.data.kube-proxy-replacement}'
lines cnp.runner sh -c "$K -n codex-runner get cnp runner-callback-draft -o json | python3 -I -c 'import hashlib,json,sys; print(hashlib.sha256(json.dumps(json.load(sys.stdin)[\"spec\"],sort_keys=True).encode()).hexdigest())'"
lines storageclass timeout 30 $K get storageclass -o jsonpath='{range .items[*]}{.metadata.name}|{.provisioner}|{.reclaimPolicy}|{.volumeBindingMode}{"\n"}{end}'
say "inventory rc" "$rc"
exit $rc
