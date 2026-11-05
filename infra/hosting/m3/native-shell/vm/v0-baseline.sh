#!/bin/sh
# #68 dev-VM V0: read-only baseline, run as root in the guest (sudo -n), after review cleared this SHA-256.
# Development VM only; never on the production node. Changes nothing: kubectl get, psql SELECTs in one READ ONLY
# transaction, getenforce, sysctl -n, systemd-sysctl --cat-config, ausearch, auditctl -s/-l, rpm -q, sha256sum,
# and a JSON key check of the controller input (prints whether `executor` is present and its image reference only).
# Judged: SELinux Enforcing, yama in the LSM list, kernel.yama.ptrace_scope = 1. Everything else is recorded,
# including what the V6-3 exec observer needs (bpftrace, kernel BTF, audit state and lost counter).
# rc: 0 every judged condition holds; 3 a lookup failed; 4 looked up fine but a judged condition differs.
set -u
umask 077
here=$(dirname "$0")
. "$here/lib.sh"
trap 'exit 130' INT TERM

echo "v0: $(date -u +%Y-%m-%dT%H:%M:%SZ) read-only baseline"
lookups=0; differs=0
note() { echo "$1"; }
fail_lookup() { echo "LOOKUP FAILED: $1"; lookups=$((lookups+1)); }
differ() { echo "DIFFERS: $1"; differs=$((differs+1)); }

# Node and kernel.
n=$(k get node "$NODE" -o jsonpath='{.metadata.uid}|{.status.nodeInfo.bootID}|{.status.conditions[?(@.type=="Ready")].status}') \
  && note "node $NODE uid|bootID|ready=$n" || fail_lookup node
note "kernel $(uname -r) $(uname -m)"

# SELinux (V3 measures container_t against this mode).
s=$(getenforce 2>/dev/null) && note "selinux $s" || fail_lookup getenforce
[ "${s:-}" = Enforcing ] || differ "selinux is ${s:-unknown}, expected Enforcing"
p=$(rpm -q selinux-policy container-selinux audit bpftrace 2>/dev/null | tr '\n' ' ')
note "packages $p"  # rpm -q exits non-zero when one package is missing; the line still says which

# LSMs and namespaces.
l=$(cat /sys/kernel/security/lsm 2>/dev/null) && note "lsm $l" || fail_lookup lsm
case ",${l:-}," in *,yama,*) ;; *) differ 'yama is not in the LSM list';; esac
u=$(sysctl -n user.max_user_namespaces 2>/dev/null) && note "user.max_user_namespaces $u" || fail_lookup max_user_namespaces

# ptrace_scope: live value and every file that sets it (V2 renames the guest file; V4-8 measures 0 and 1).
y=$(sysctl -n kernel.yama.ptrace_scope 2>/dev/null) && note "ptrace_scope $y" || fail_lookup ptrace_scope
[ "${y:-}" = 1 ] || differ "ptrace_scope is ${y:-unknown}, expected 1"
c=$(/usr/lib/systemd/systemd-sysctl --cat-config 2>/dev/null) || fail_lookup 'systemd-sysctl --cat-config'
echo "${c:-}" | awk '/^# \//{file=$2} /ptrace_scope/{print "ptrace_scope source " file ": " $0}'

# Seccomp profiles on the node (V2 installs anxious-runner-userns-v1.json; before/after hashes start here).
if [ -d "$SECCOMP_DIR" ]; then
  found=0
  for f in "$SECCOMP_DIR"/*; do
    [ -e "$f" ] || continue
    found=1
    note "seccomp $(sha256sum "$f" | cut -d' ' -f1) $(stat -c '%U:%G %a' "$f") $(basename "$f")"
  done
  [ "$found" = 1 ] || note 'seccomp profiles: none'
else
  note "seccomp profiles: $SECCOMP_DIR absent"
fi

# Exec observer prerequisites (V6-3; validated with known executions in V4 before any model turn).
[ -r /sys/kernel/btf/vmlinux ] && note 'kernel btf present' || note 'kernel btf absent'
if command -v bpftrace >/dev/null 2>&1; then note "bpftrace $(bpftrace --version 2>/dev/null)"; else note 'bpftrace absent'; fi

# Audit (V3 collects AVC denials). Status and rules are read-only queries.
a=$(systemctl is-active auditd 2>/dev/null); note "auditd ${a:-unknown}"
st=$(auditctl -s 2>/dev/null) && note "audit status $(echo "$st" | grep -E '^(enabled|lost|backlog_limit|backlog) ' | tr '\n' ' ')" \
  || fail_lookup 'auditctl -s'
rules=$(auditctl -l 2>/dev/null) && note "audit rules $(echo "$rules" | grep -vc '^No rules')" || fail_lookup 'auditctl -l'
out=$(ausearch -m AVC,USER_AVC -ts boot --format raw 2>/dev/null); r=$?
case "$r" in
  0) note "avc since boot $(echo "$out" | grep -c '^type=AVC')";;
  1) note 'avc since boot 0';;  # ausearch: no matches
  *) fail_lookup "ausearch rc $r";;
esac

# Runner Pods (the manifest gate counts Pending/Running), controller, broker and api images.
pods=$(k -n codex-runner get pods -l "$RUNNER_SELECTOR" -o jsonpath='{range .items[*]}{.metadata.name}|{.metadata.uid}|{.status.phase}|{range .status.containerStatuses[*]}{.name}:{.imageID};{end}{"\n"}{end}') \
  || fail_lookup 'runner pods'
echo "${pods:-}" | sed '/^$/d; s/^/runner pod /'
live=$(echo "${pods:-}" | awk -F'|' '$3=="Pending"||$3=="Running"' | sed '/^$/d' | wc -l)
note "manifest gate: $live live runner pod(s)"
d=$(k -n runner-control get deployment runner-controller -o jsonpath='{.spec.replicas}|{.spec.template.spec.containers[0].image}') \
  && note "runner-controller replicas|image=$d" || fail_lookup runner-controller
b=$(k -n codex-broker get pod codex-broker-0 -o jsonpath='{.metadata.uid}|{.status.phase}|{range .status.containerStatuses[*]}{.name}:{.imageID};{end}') \
  && note "broker $b" || fail_lookup broker
i=$(k -n m2-hosting get deployment api -o jsonpath='{.spec.template.spec.containers[0].image}') \
  && note "api image $i" || fail_lookup 'api deployment'
img=$(timeout 30 "$K3S" ctr -n k8s.io images ls -q 2>/dev/null) || fail_lookup 'containerd images'
echo "${img:-}" | grep 'anxious-dev-' | sed 's/^/containerd /'

# Database, one READ ONLY transaction: registry, bindings (plan version and executor image), open stops.
rows=$(sql "BEGIN READ ONLY;
SELECT 'registry '||r.owner_id||'|'||r.state_id||'|'||r.phase||'|'||r.generation||'|'||r.revision||'|'||coalesce(r.pod_uid::text,'-')
       FROM runner_runtime.registry r ORDER BY r.owner_id;
SELECT 'binding '||b.owner_id||'|'||b.state_id||'|version='||coalesce(b.plan->>'version','1')||'|image='||(b.plan->>'image')
       ||'|executor='||coalesce(b.plan->'executor'->>'image','-') FROM runner_bindings.bindings b ORDER BY b.owner_id;
SELECT 'open stop '||s.owner_id||'|'||s.generation||'|'||s.pod_uid||'|receipt='||coalesce(s.receipt->>'version','-')
       FROM runner_termination.stops s WHERE s.completed_at IS NULL ORDER BY s.owner_id;
COMMIT;") && echo "$rows" || fail_lookup database

# Controller input (V7 removes `executor`): presence and image reference only.
if [ -e "$CONTROLLER_INPUTS" ]; then
  e=$(python3 -I -c '
import json, sys
value = json.load(open(sys.argv[1]))
executor = value.get("executor") if isinstance(value, dict) else None
image = executor.get("image") if isinstance(executor, dict) else "?"
print("executor=absent" if executor is None else "executor=present image=" + str(image))
' "$CONTROLLER_INPUTS" 2>/dev/null) && note "controller inputs $e" || fail_lookup 'controller inputs'
else
  note 'controller inputs: absent'
fi

echo "v0 summary lookups_failed=$lookups differs=$differs"
[ "$lookups" = 0 ] || { echo 'result rc=3'; exit 3; }
[ "$differs" = 0 ] || { echo 'result rc=4'; exit 4; }
echo 'result rc=0'
