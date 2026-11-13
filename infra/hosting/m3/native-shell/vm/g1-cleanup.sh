#!/bin/sh
# #68 dev-VM C/G1: the separate cleanup path for the measurement Pod of one g1-dotcodex.sh run (derived from
# v6x-cleanup.sh, V6x round 8 927f71c8: only the Pod and evidence names differ). Development VM only.
# Run as root in the guest:
#
#   g1-cleanup.sh <RUN> stoppost    the unit's ExecStopPost (runs after the main script ended, also after SIGKILL or
#                                   a RuntimeMaxSec stop, when the main script's trap could not run)
#   g1-cleanup.sh <RUN> manual      the manual recovery template (PM runs it, only after PM/review asked for it)
#
# Idempotent and safe to run after the main script's own cleanup: it only reads, unless a Pod named g1-dotcodex in
# native68-measure carries this RUN's annotation (and, when the run sidecar recorded one, this UID); then it sends
# one delete with the UID as precondition. It never deletes a Pod it cannot confirm as this run's, never retries a
# refused delete, and changes nothing else. Evidence: $EVIDENCE/g1-dotcodex-<RUN>.cleanup-<mode>-<UTC>.txt (created
# atomically) with three separate facts: (i) delete accepted, (ii) Pod absent (GET NotFound), (iii) kubelet pod
# directory absent (only checked when the UID is known, (ii) is true, and the Pod's node is proven to be this host:
# recorded node = the pinned single node whose machineID is this host's /etc/machine-id; never inferred from (ii)).
# rc: 0 (ii) true; 7 not confirmed or not this run's Pod; 3 bad arguments or no evidence file to write.
set -u
umask 077
here=$(dirname "$0")
. "$here/lib.sh"
NS=native68-measure
POD=g1-dotcodex
KUBELET_PODS=/var/lib/kubelet/pods
CLEANUP_S=100   # under the unit's TimeoutStopSec=120, which also bounds ExecStopPost; every wait uses left()

RUN=${1:-}; MODE=${2:-}
case "$RUN" in [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z) ;; *) echo 'usage: g1-cleanup.sh <RUN> stoppost|manual'; exit 3;; esac
case "$MODE" in stoppost|manual) ;; *) echo 'usage: g1-cleanup.sh <RUN> stoppost|manual'; exit 3;; esac
[ "$(id -u)" = 0 ] || { echo 'not root'; exit 3; }
stamp=$(date -u +%Y%m%dT%H%M%SZ) || exit 3
log="$EVIDENCE/g1-dotcodex-$RUN.cleanup-$MODE-$stamp.txt"
( set -C; : > "$log" ) 2>/dev/null || { echo "cannot create $log"; exit 3; }
say() { printf '%s\n' "$1"; printf '%s\n' "$1" >> "$log"; }
done_rc() {
  say "cleanup evidence: (i) delete accepted $accepted (ii) pod absent $absent (iii) kubelet dir removed $kubelet"
  say "result rc=$1"
  h=$(sha256sum "$log" 2>/dev/null) && printf 'evidence %s\n' "$h"
  exit "$1"
}
say "g1-cleanup $MODE: $(date -u +%Y-%m-%dT%H:%M:%SZ) run $RUN"
runfile="$EVIDENCE/g1-dotcodex-$RUN.txt.run"
uid_known=; node_known=
if [ -f "$runfile" ]; then
  uid_known=$(sed -n 's/^uid \([0-9a-f-]*\)$/\1/p' "$runfile" | head -1)
  node_known=$(sed -n 's/^node \([a-z0-9.-]*\)$/\1/p' "$runfile" | head -1)
fi
say "run sidecar: $( [ -f "$runfile" ] && echo present || echo absent ), uid ${uid_known:-unknown}, node ${node_known:-unknown}"
accepted=unknown; absent=unknown; kubelet=unknown
deadline=$(( $(date +%s) + CLEANUP_S ))
left() { r=$(( deadline - $(date +%s) )); [ "$r" -gt "$1" ] && r=$1; [ "$r" -lt 1 ] && r=1; echo "$r"; }
kc() { s=$1; shift; timeout "$(left "$s")" "$K3S" kubectl "$@"; }
lookup() {  # absent | ours <uid> <node> | other <uid> | unknown
  out=$(kc 30 -n "$NS" get pod "$POD" -o jsonpath='{.metadata.uid} {.metadata.annotations.anxious\.io/run} {.spec.nodeName}' </dev/null 2>&1); s=$?
  if [ "$s" != 0 ]; then case "$out" in *NotFound*|*'not found'*) echo absent;; *) echo unknown;; esac; return 0; fi
  set -- $out
  if [ "${2:-}" = "$RUN" ] && { [ -z "$uid_known" ] || [ "$1" = "$uid_known" ]; }; then echo "ours $1 ${3:-}"; else echo "other ${1:-}"; fi
}
# (iii) is checked here only for the node the Pod ran on: the recorded (or the Pod's own) node must be $NODE and
# $NODE must be this host (the cluster is that single node, its machineID = /etc/machine-id). Otherwise unknown.
local_node_ok() {
  names=$(kc 20 get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' </dev/null 2>/dev/null) || return 1
  [ "$names" = "$NODE" ] || return 1
  mid=$(kc 20 get node "$NODE" -o jsonpath='{.status.nodeInfo.machineID}' </dev/null 2>/dev/null) || return 1
  [ -n "$mid" ] && [ "$mid" = "$(cat /etc/machine-id 2>/dev/null)" ]
}
st=$(lookup); say "pod $NS/$POD: $st"
case "$st" in
  absent) accepted=not-needed; absent=true;;
  other*|unknown) say 'not this run'"'"'s Pod or lookup failed: not touched'; done_rc 7;;
  ours*) set -- $st; uid=$2; [ -n "$uid_known" ] || uid_known=$uid; [ -n "$node_known" ] || node_known=${3:-}
    dout=$(printf '{"kind":"DeleteOptions","apiVersion":"v1","gracePeriodSeconds":10,"preconditions":{"uid":"%s"}}\n' "$uid" \
      | kc 60 delete --raw "/api/v1/namespaces/$NS/pods/$POD" -f - 2>&1); s=$?
    case "$s:$dout" in
      0:*) accepted=true;;
      *Conflict*) accepted=false; say 'delete refused (UID precondition): not touched'; done_rc 7;;
      *NotFound*|*'not found'*) accepted=not-needed;;
      *) accepted=false; say "delete request rc $s";;
    esac
    while [ "$(date +%s)" -lt "$deadline" ]; do
      st=$(lookup)
      case "$st" in absent) absent=true; break;; other*) say "a different Pod of that name appeared"; break;; esac
      sleep "$(left 5)"
    done
    [ "$absent" = true ] || absent=false;;
esac
if [ "$absent" = true ] && [ -n "$uid_known" ]; then
  if [ "$node_known" = "$NODE" ] && local_node_ok; then
    while [ "$(date +%s)" -lt "$deadline" ]; do
      if [ ! -e "$KUBELET_PODS/$uid_known" ]; then kubelet=true; break; fi
      sleep "$(left 5)"
    done
    [ "$kubelet" = true ] || kubelet=false
  else
    say "kubelet directory not checked: the Pod's node is not proven to be this host (node ${node_known:-unknown})"
  fi
fi
[ "$absent" = true ] && done_rc 0
done_rc 7
