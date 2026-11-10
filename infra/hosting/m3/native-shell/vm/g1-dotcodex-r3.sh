#!/bin/sh
# #68 dev-VM C/G1: model-free app-server measurement of an existing runner-owned `.codex` placeholder (design g1 r1
# 0626f27c…, review "code scope accepted"). Derived from v6x-carveout.sh (V6x round 8, 3c9f9648): the same Pod,
# namespace, node pin, prechecks, evidence and cleanup rules; only the Pod name, the harness and its helper, the time
# limits and the record summary differ. Development VM only. Run as root in the guest, once, under a systemd-run
# unit (g1-commands.md), after review cleared this script's SHA-256 and PM gave the go:
#
#   g1-dotcodex.sh <RUN> [reference]   RUN = the UTC stamp the command made (also passed to g1-cleanup.sh in
#                                      ExecStopPost). r3: `reference` (only under the §3R go) also sends
#                                      g1-reference.py (its own pin) as REF_B64; without it the harness never makes the
#                                      §C reference launch and records `not_enabled`.
#
# What it does: one measurement Pod g1-dotcodex in the existing namespace native68-measure (the V3 rendering as for
# V6x: the runner's Pod and container security settings, three emptyDir volumes, `sleep infinity`), plus this run's
# annotation and spec.nodeName pinned to the single local node; the harness g1-harness.py inside it (kubectl exec,
# stdin; the placeholder helper dot_codex.py r2 is prepended as one base64 line, its sha256 checked here and again
# inside); then deletion with the Pod UID as precondition. No model call, no credentials, no owner PV, broker, API,
# database or ledger. Nothing else is created: the namespace, its deny-all NetworkPolicy and the image digest
# reference must already exist as V3 left them, and the namespace must have no Pod, or the run refuses (rc 3).
#
# Evidence: $EVIDENCE/g1-dotcodex-<RUN>.txt (created atomically before anything else), sidecars .harness (the
# harness's JSON lines) and .run (name, run id, then the Pod UID, read by g1-cleanup.sh). Cleanup evidence is
# three separate facts: (i) delete accepted, (ii) Pod absent (GET NotFound), (iii) the kubelet pod directory
# /var/lib/kubelet/pods/<uid> absent; (iii) is never inferred from (ii).
#
# rc: 0 the harness ran every planned case (its records hold the verdicts; read them, not the rc), cleanup (i)(ii)
# confirmed; 3 refused, nothing changed; 5 a change failed (the Pod could not be created or did not become Ready);
# 7 deletion or absence unconfirmed, or a Pod of that name is not this run's (g1-cleanup.sh in ExecStopPost runs
# anyway; do not rerun, the manual template is in g1-commands.md); 9 evidence incomplete after a change;
# 10 the harness stopped before the end (harness rc 2: environment or pin gate, K2, or (r1) a case whose app-server
# did not start or whose gate - policy, S0, C0 - failed, recorded as `gate_stopped` with `at` and `reason`; rc 3: a
# contamination gate; any other: harness failure). A stopped run is still a run: never "nothing ran", not rerun automatically;
# 11 the Pod's identity changed during the harness: the harness records are not valid for this Pod;
# 130 interrupted (INT/TERM: the trap deletes the Pod first). A SIGKILL of this script skips the trap; the unit's
# ExecStopPost (g1-cleanup.sh <RUN> stoppost) is the separate path for that.
set -u
umask 077
here=$(dirname "$0")
. "$here/lib.sh"
. "$here/evidence.sh"

LIB_SHA=4cae0e470ae4f5862c52343570a6e3c94adb70122a58aad6a97f807a6eb82475
EVIDENCE_SHA=b422ac6f1f8bce79529a0d1c573976d62fcdbbb8a2c7755537882d6e3f6ba2e4
DIGEST=sha256:840679946e8071b5495b9609c6e9f33c0708d9196e712e262361e200d4641801
DIGEST_REF=docker.io/library/anxious-dev-runner@$DIGEST
HOST_ID=sha256:ddd7bad4ea352eff5d905be329e7acf32c5ff415b52c8e9ae4a759e6b714f6c0
NS=native68-measure
POD=g1-dotcodex
POD_JSON="$here/g1-pod.json"
POD_JSON_SHA=44d404ca011be4bdb4944a0e9378a1e8053ffc228e9c1a81d5a85b8df78780e2
HARNESS="$here/g1-harness-r3.py"  # r3: installed next to the r2 files under a new name (install never overwrites)
HARNESS_SHA=a05fdd2437821cce249bc521cee58b02204e1725b195b1ce89cd137aeeb22bc0
HELPER="$here/dot_codex.py"
HELPER_SHA=2ca0793e3a4304bbeb7093ac47adb920512619b6a958d73049e051239cf478c6
REF="$here/g1-reference.py"
REF_SHA=644988017b0e26e1d29f3a3c71b865180a4a637b9c4cc5a1d50f7d64d96efeaa
SECCOMP_FILE=$SECCOMP_DIR/anxious-runner-userns-v1.json
SECCOMP_SHA=9a6594419eae4089e737c787597d2ed4950fd7e1031e371f490d5b2c935dff78
PSA_VERSION=v1.36
READY_S=180
HARNESS_S=900
CLEANUP_S=150
KUBELET_PODS=/var/lib/kubelet/pods

RUN=${1:-}
case "$RUN" in [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z) ;;
  *) echo 'usage: g1-dotcodex.sh <RUN UTC stamp YYYYmmddTHHMMSSZ> [reference]'; exit 2;; esac
MODE=${2:-}
case "$MODE" in ''|reference) ;; *) echo 'usage: g1-dotcodex.sh <RUN UTC stamp YYYYmmddTHHMMSSZ> [reference]'; exit 2;; esac

# Evidence named by RUN (not start_evidence's own stamp: g1-cleanup.sh finds it by RUN). Same rules as
# start_evidence: root, created atomically, rc 3 if impossible.
[ "$(id -u)" = 0 ] || { echo 'not root; nothing changed'; exit 3; }
install -d -o root -g root -m 0700 "$EVIDENCE" || { echo 'evidence directory; nothing changed'; exit 3; }
candidate="$EVIDENCE/g1-dotcodex-$RUN.txt"
( set -C; : > "$candidate" ) 2>/dev/null || { echo "cannot create $candidate (exists?); nothing changed"; exit 3; }
log=$candidate
say "g1-dotcodex: $(date -u +%Y-%m-%dT%H:%M:%SZ) run $RUN reference ${MODE:-off}"
harnessfile=$(sidecar harness) || refuse 'sidecar harness'
runfile=$(sidecar run) || refuse 'sidecar run'
printf 'run %s\npod %s/%s\n' "$RUN" "$NS" "$POD" >> "$runfile" || refuse 'run sidecar write'

pod_attempted=0; pod_uid=; incomplete_parts=
note_incomplete() { incomplete_parts="$incomplete_parts [$1]"; say "evidence incomplete: $1"; }
kt() { s=$1; shift; timeout "$s" "$K3S" kubectl "$@"; }
left() { r=$(( deadline - $(date +%s) )); [ "$r" -gt "$1" ] && r=$1; [ "$r" -lt 1 ] && r=1; echo "$r"; }

pod_state() {  # "absent", "ours <uid>", "other <uid>" or "unknown" (annotation and, once known, UID must match)
  out=$(kt "${1:-30}" -n "$NS" get pod "$POD" -o jsonpath='{.metadata.uid} {.metadata.annotations.anxious\.io/run}' </dev/null 2>&1); s=$?
  if [ "$s" != 0 ]; then
    case "$out" in *NotFound*|*'not found'*) echo absent;; *) echo unknown;; esac; return 0
  fi
  uid=${out%% *}; run=${out#* }
  if [ "$run" = "$RUN" ] && { [ -z "$pod_uid" ] || [ "$uid" = "$pod_uid" ]; }; then echo "ours $uid"; else echo "other $uid"; fi
}
local_node_ok() {  # the cluster is exactly the one node $NODE and that node is this host (node machineID = /etc/machine-id)
  names=$(k get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' </dev/null 2>/dev/null) || return 1
  [ "$names" = "$NODE" ] || return 1
  mid=$(k get node "$NODE" -o jsonpath='{.status.nodeInfo.machineID}' </dev/null 2>/dev/null) || return 1
  [ -n "$mid" ] && [ "$mid" = "$(cat /etc/machine-id 2>/dev/null)" ]
}
pod_identity() {  # "uid node restarts containerID phase" of the Pod by name
  k -n "$NS" get pod "$POD" -o jsonpath='{.metadata.uid} {.spec.nodeName} {.status.containerStatuses[0].restartCount} {.status.containerStatuses[0].containerID} {.status.phase}' </dev/null 2>/dev/null
}
identity_ok() {  # this run's UID, the pinned node, restart count 0, a container ID, phase Running
  set -- $1
  [ $# = 5 ] && [ "$1" = "$pod_uid" ] && [ "$2" = "$NODE" ] && [ "$3" = 0 ] && [ -n "$4" ] && [ "$5" = Running ]
}
identity_say() {  # the container ID only as a short digest
  set -- $1
  printf 'uid %s node %s restarts %s phase %s container %s' "${1:-?}" "${2:-?}" "${3:-?}" "${5:-?}" \
    "$(printf '%s' "${4:-}" | sha256sum | cut -c1-12)"
}
cleanup_pod() {  # rc 0: (i)(ii) confirmed or nothing was created; 7 otherwise. Idempotent: an absent Pod is fine.
  [ "$pod_attempted" = 1 ] || return 0
  deadline=$(( $(date +%s) + CLEANUP_S ))
  st=$(pod_state "$(left 30)"); say "cleanup: pod $NS/$POD $st"
  accepted=unknown; absent=unknown; kubelet=unknown
  case "$st" in
    absent) accepted=not-needed; absent=true;;
    other*|unknown) say "cleanup: not deleting a Pod that is not confirmed this run's"
                    say "cleanup evidence: (i) delete accepted $accepted (ii) pod absent $absent (iii) kubelet dir removed $kubelet"
                    return 7;;
    ours*) uid=${st#ours }; [ -n "$pod_uid" ] || pod_uid=$uid
      dout=$(printf '{"kind":"DeleteOptions","apiVersion":"v1","gracePeriodSeconds":10,"preconditions":{"uid":"%s"}}\n' "$uid" \
        | kt "$(left 60)" delete --raw "/api/v1/namespaces/$NS/pods/$POD" -f - 2>&1); s=$?
      case "$s:$dout" in
        0:*) accepted=true; say "cleanup: delete accepted (uid precondition $uid)";;
        *Conflict*) accepted=false; say "cleanup: delete refused, the Pod of that name is not uid $uid; not touched"
                    say "cleanup evidence: (i) delete accepted false (ii) pod absent unknown (iii) kubelet dir removed unknown"
                    return 7;;
        *NotFound*|*'not found'*) accepted=not-needed; say 'cleanup: already gone at delete';;
        *) accepted=false; say "cleanup: delete request rc $s; following the Pod anyway";;
      esac
      while [ "$(date +%s)" -lt "$deadline" ]; do
        st=$(pod_state "$(left 30)")
        case "$st" in absent) absent=true; break;; other*) say "cleanup: a different Pod of that name appeared ($st)"; break;; esac
        sleep "$(left 5)"
      done
      [ "$absent" = true ] || absent=false;;
  esac
  if [ "$absent" = true ] && [ -n "$pod_uid" ]; then
    # (iii) separately: kubelet removes the pod directory (emptyDir data) after the API object is gone. The Pod's
    # node is pinned in the manifest to $NODE, and the precheck proved $NODE is this host (single node, machine-id),
    # so this host's /var/lib/kubelet/pods is the directory of the node the Pod ran on.
    while [ "$(date +%s)" -lt "$deadline" ]; do
      if [ ! -e "$KUBELET_PODS/$pod_uid" ]; then kubelet=true; break; fi
      sleep "$(left 5)"
    done
    [ "$kubelet" = true ] || kubelet=false
  fi
  say "cleanup evidence: (i) delete accepted $accepted (ii) pod absent $absent (iii) kubelet dir removed $kubelet"
  if [ "$absent" = true ]; then
    done_change "pod $NS/$POD (uid ${pod_uid:-?}) deleted, absence confirmed"; pod_attempted=0; return 0
  fi
  return 7
}
end_run() {
  want=$1
  cleanup_pod || want=7
  if [ "$want" = 0 ] && [ -n "$incomplete_parts" ]; then want=9; fi
  finish "$want"
}
# A signal is usually the unit's stop (RuntimeMaxSec or `systemctl stop`): systemd sends SIGKILL TimeoutStopSec=120
# after its SIGTERM, so the trap's cleanup gets 100 s, not CLEANUP_S. If it is cut off, ExecStopPost cleans up.
on_signal() { trap '' INT TERM; say 'interrupted'; CLEANUP_S=100; cleanup_pod || finish 7; finish 130; }
trap on_signal INT TERM

ns_ok() {  # as V3: the measurement namespace labels, exactly one NetworkPolicy deny-all, no Cilium policy
  nsj=$(k get ns "$NS" -o json </dev/null 2>/dev/null) || return 1
  npj=$(k -n "$NS" get networkpolicy -o json </dev/null 2>/dev/null) || return 1
  cnp=$(k -n "$NS" get ciliumnetworkpolicy -o name </dev/null 2>/dev/null) || return 1
  [ -z "$cnp" ] || { say "namespace check: CiliumNetworkPolicy present"; return 1; }
  ccnp=$(k get ciliumclusterwidenetworkpolicy -o name </dev/null 2>/dev/null) || return 1
  [ -z "$ccnp" ] || { say "namespace check: CiliumClusterwideNetworkPolicy present"; return 1; }
  res=$(printf '%s\n%s\n' "$nsj" "$npj" | python3 -I -c '
import json, sys
text = sys.stdin.read()
dec = json.JSONDecoder(); ns, end = dec.raw_decode(text); np, _ = dec.raw_decode(text[end:].lstrip())
labels = ns["metadata"].get("labels", {})
want = {"anxious.io/purpose": "native68-measure"}
for mode in ("enforce", "audit", "warn"):
    want["pod-security.kubernetes.io/" + mode] = "restricted"
    want["pod-security.kubernetes.io/%s-version" % mode] = sys.argv[1]
bad = [k for k, v in want.items() if labels.get(k) != v]
items = np.get("items", [])
if len(items) != 1 or items[0]["metadata"]["name"] != "deny-all":
    bad.append("networkpolicies %s" % [i["metadata"]["name"] for i in items])
else:
    spec = items[0]["spec"]
    if spec.get("podSelector") != {} or sorted(spec.get("policyTypes", [])) != ["Egress", "Ingress"] \
            or spec.get("ingress") or spec.get("egress"):
        bad.append("deny-all spec")
print("ok" if not bad else "FAILED " + "; ".join(bad))
sys.exit(1 if bad else 0)' "$PSA_VERSION" 2>&1); s=$?
  say "namespace check: $res"
  return $s
}

# --- read-only prechecks (any failure: nothing changed) ---
[ "$(sha256sum "$here/lib.sh" 2>/dev/null | cut -d' ' -f1)" = "$LIB_SHA" ] || refuse 'lib.sh digest'
[ "$(sha256sum "$here/evidence.sh" 2>/dev/null | cut -d' ' -f1)" = "$EVIDENCE_SHA" ] || refuse 'evidence.sh digest'
[ "$(sha256sum "$POD_JSON" 2>/dev/null | cut -d' ' -f1)" = "$POD_JSON_SHA" ] || refuse 'g1-pod.json digest'
[ "$(sha256sum "$HARNESS" 2>/dev/null | cut -d' ' -f1)" = "$HARNESS_SHA" ] || refuse 'g1-harness.py digest'
[ "$(sha256sum "$HELPER" 2>/dev/null | cut -d' ' -f1)" = "$HELPER_SHA" ] || refuse 'dot_codex.py digest'
if [ "$MODE" = reference ]; then
  [ "$(sha256sum "$REF" 2>/dev/null | cut -d' ' -f1)" = "$REF_SHA" ] || refuse 'g1-reference.py digest'
fi
grep -Fq "\"image\": \"$DIGEST_REF\"" "$POD_JSON" || refuse 'g1-pod.json does not name the digest reference'
grep -Fq '"annotations"' "$POD_JSON" && refuse 'g1-pod.json already has annotations'
[ "$(sha256sum "$SECCOMP_FILE" 2>/dev/null | cut -d' ' -f1)" = "$SECCOMP_SHA" ] || refuse 'seccomp profile digest'
[ "$(getenforce 2>/dev/null)" = Enforcing ] || refuse 'SELinux is not Enforcing'
node=$(k get node "$NODE" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' </dev/null 2>/dev/null) \
  && [ "$node" = True ] || refuse "node $NODE not Ready"
local_node_ok || refuse "the cluster is not the single node $NODE on this host (machine-id), so its kubelet directory is not checkable here"
grep -Fq '"nodeName"' "$POD_JSON" && refuse 'g1-pod.json already names a node'
d_id=$(image_id "$DIGEST_REF" </dev/null) && [ "$d_id" = "$HOST_ID" ] || refuse "digest reference id ${d_id:-none} != host id (not tagged here)"
k get ns "$NS" -o name </dev/null >/dev/null 2>&1 || refuse "namespace $NS absent (not created here)"
ns_ok || refuse "namespace $NS is not the measurement namespace as required"
pods=$(k -n "$NS" get pods -o name </dev/null 2>/dev/null) || refuse 'pod list'
[ -z "$pods" ] || refuse "namespace $NS has pods: $(echo $pods)"
say "prechecks ok: files, seccomp profile, SELinux Enforcing, node Ready and local (single node, machine-id), digest reference, namespace, no pods"
# This run's annotation and the node pinned in the manifest (spec.nodeName, immutable once set): the Pod can only
# run on $NODE, which local_node_ok proved is this host, so (iii) below checks the kubelet directory where it lives.
podobj=$(sed -e "s|^ \"metadata\": {\$| \"metadata\": {\n  \"annotations\": {\"anxious.io/run\": \"$RUN\"},|" \
             -e "s|^ \"spec\": {\$| \"spec\": {\n  \"nodeName\": \"$NODE\",|" "$POD_JSON") || refuse 'pod object'
[ "$(printf '%s\n' "$podobj" | grep -c '"anxious.io/run"')" = 1 ] || refuse 'run annotation not placed'
[ "$(printf '%s\n' "$podobj" | grep -c "\"nodeName\": \"$NODE\"")" = 1 ] || refuse 'node not pinned'
printf '%s\n' "$podobj" | python3 -I -c 'import json, sys; json.load(sys.stdin)' || refuse 'pod object is not JSON'
dry=$(printf '%s\n' "$podobj" | kt 45 create --dry-run=server -f - -o name 2>&1); s=$?
say "server dry-run rc $s"
[ "$s" = 0 ] || refuse 'admission refused the Pod'

# --- Pod: create (ownership confirmed by GET even if the create answer is lost), Ready, harness, delete ---
changing
pod_attempted=1
printf '%s\n' "$podobj" | kt 45 create -f - >/dev/null 2>&1; s=$?
st=$(pod_state)
case "$st" in
  ours*) pod_uid=${st#ours }; printf 'uid %s\nnode %s\n' "$pod_uid" "$NODE" >> "$runfile" || note_incomplete 'run sidecar uid/node'
         done_change "pod $NS/$POD created (uid $pod_uid, run $RUN, node $NODE pinned; create rc $s)";;
  *) say "pod create rc $s, pod $st"; end_run 5;;
esac
kt $((READY_S + 15)) -n "$NS" wait --for=condition=Ready "pod/$POD" --timeout="${READY_S}s" </dev/null >/dev/null 2>&1; ready=$?
say "pod ready rc $ready"
[ "$ready" = 0 ] || end_run 5
podinfo=$(k -n "$NS" get pod "$POD" -o jsonpath='imageID={.status.containerStatuses[0].imageID} ' </dev/null 2>&1) \
  || { say 'pod status lookup failed'; end_run 5; }
case "$podinfo" in *"imageID=$DIGEST_REF "*|*"imageID=$HOST_ID "*) say 'image chain ok';; *) say 'image chain mismatch'; end_run 5;; esac
# Pod identity before the harness (all required): this UID, the pinned node, phase Running, restart count 0, one
# container ID; compared again after the harness, so a restart or replacement in between voids the records (rc 11).
id_before=$(pod_identity) || { say 'pod identity lookup failed before the harness'; end_run 5; }
identity_ok "$id_before" || { say "pod identity before the harness not as required: $(identity_say "$id_before")"; end_run 5; }
say "pod identity before the harness ok: $(identity_say "$id_before")"
# The helper goes in as one base64 assignment line before the harness (its own namespace inside; sha256 checked
# again there against the harness's HELPER_SHA). Both files were checked against their pins above.
hb64=$(base64 -w0 "$HELPER") && [ -n "$hb64" ] || { say 'helper encoding failed'; end_run 5; }
rline=
if [ "$MODE" = reference ]; then  # r3 §C: only with the `reference` argument (§3R go); checked against its pin above
  rb64=$(base64 -w0 "$REF") && [ -n "$rb64" ] || { say 'reference encoding failed'; end_run 5; }
  rline=$(printf 'REF_B64 = "%s"' "$rb64")
fi
hout=$({ printf 'HELPER_B64 = "%s"\n' "$hb64"; [ -z "$rline" ] || printf '%s\n' "$rline"; cat "$HARNESS"; } | kt "$HARNESS_S" -n "$NS" exec -i "$POD" -- python3 -I - 2>&1); hrc=$?
say "harness rc $hrc"
id_after=$(pod_identity) || id_after=
if [ -n "$id_after" ] && [ "$id_after" = "$id_before" ]; then id_same=true; else id_same=false; fi
say "pod identity after the harness: $( [ -n "$id_after" ] && identity_say "$id_after" || echo 'lookup failed'), same as before $id_same"
printf '%s\n' "$hout" > "$harnessfile" || note_incomplete 'harness sidecar write'
hh=$(sha256sum "$harnessfile" 2>/dev/null) && hh=${hh%% *} && [ ${#hh} = 64 ] || note_incomplete 'harness sidecar hash'
say "harness sidecar $(printf '%s\n' "$hout" | wc -l) lines sha256 ${hh:-?}"
# Summary lines for the log (fixed fields from the harness records; the sidecar has everything).
printf '%s\n' "$hout" | python3 -I -c '
import json, sys
for line in sys.stdin:
    try:
        r = json.loads(line)
    except ValueError:
        print("non-json line"); continue
    k = r.get("k")
    if k in ("env", "pins"):
        bad = sorted(x for x, v in (r.get("checks") or {}).items() if not v)
        print(k + " " + ("checks ok" if r.get("checks") and not bad else "failed: " + " ".join(bad)))
    elif k == "case":
        print("case %s %s%s" % (r.get("id"), r.get("verdict"), (" (" + str(r["reason"]) + ")") if r.get("reason") else ""))
        for x in ((r.get("gate") or {}).get("launches") or []):  # r3: fixed diagnosis fields only
            d = x.get("diag") if isinstance(x, dict) else None
            if isinstance(d, dict):
                print("  gate launch exit %s reach %s signature %s/%s stage %s" % (x.get("exit_code"), d.get("reach_stage"),
                      d.get("signature"), d.get("subclass"), d.get("parse_stage")))
        ref = r.get("reference_launch")
        if isinstance(ref, dict):
            d = ref.get("diag") or {}
            print("  reference rc %s reach %s signature %s/%s effect %s none_left %s" % (ref.get("rc"), d.get("reach_stage"),
                  d.get("signature"), d.get("subclass"), ref.get("effect"), ref.get("none_left")))
        elif ref is not None:
            print("  reference %s" % ref)
    elif k == "pre_reference":
        print("pre_reference recorded for %s" % r.get("id"))
    elif k == "summary":
        print("summary %s" % json.dumps({x: r[x] for x in r if x != "k"}, sort_keys=True))
' 2>&1 | while IFS= read -r l; do say "harness: $l"; done
case "$hrc" in 0) want=0;; *) want=10;; esac
[ "$id_same" = true ] || want=11
end_run "$want"
