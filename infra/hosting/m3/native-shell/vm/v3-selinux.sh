#!/bin/sh
# #68 dev-VM V3 reruns: the runner image under the node's SELinux policy (container_t) and the module
# anxious_runner_bwrap (selinux-module.sh), in a Pod with the runner's own security settings (v3-pod.json, rendered
# by `python -m ops.native_measure`). Development VM only. Run as root in the guest after review cleared this
# script's SHA-256.
#
#   v3-selinux.sh v3b   after `selinux-module.sh install 1` (devpts mount)
#   v3-selinux.sh v3c   after `selinux-module.sh upgrade 2` (adds tmpfs remount), only if V3b showed the need
#
# Beyond V3: the probe's second sandbox run (/proc present or not, writes into the masked paths, function survey)
# with step timestamps for the AVC frequency, and a host observation of one sandboxed `sleep 20` (nonce handshake,
# container cgroup, bwrap ancestor): its user/pid/mnt/net namespaces against the container's (expected to differ)
# and its SELinux label (recorded, not judged).
#
# Measures, never secrets: the Pod imageID (last link of the V1 chain), the measurement volumes' owner and mode,
# the container's SELinux label and capabilities from the host, the in-Pod probe (v3-probe.py: bubblewrap through
# `codex sandbox`, fake-marker reads, the runner's app-server config checks and one offline thread/start,
# CODEX_HOME/tmp/arg0), and every AVC/USER_AVC from the window start. No model call, no network from the Pod
# (deny-all NetworkPolicy, checked), no credentials.
#
# Differences from the runner Pod (measurement only): /state and /control are emptyDir volumes, not PVCs. Kubernetes
# creates an emptyDir directory root-owned with mode 0777; the runner's PVCs are prepared 10001-owned. Nothing
# prepares or chowns them here: their owner, mode and SELinux label are recorded, and the probe runs only if UID
# 10001 can write all three mounts. A skipped probe is "V3 not complete" (the script still exits 0). A 0777
# emptyDir is not shown equivalent to a 0700 local PV: DAC may stop an access before SELinux sees it, so V3 is
# the result under these measurement conditions; the real runner Pod is measured again after V7a. The Pod also carries the annotation anxious.io/run=<this run>, added when it is created (the
# file on disk is hashed without it), so cleanup deletes only this run's Pod.
#
# Changes, in order, each recorded:
#   1. the digest reference docker.io/library/anxious-dev-runner@$DIGEST is tagged on the imported 68-42a1e8b
#      image if it does not exist yet (infra/hosting/README.md: import registers only the tag). Kept.
#   2. namespace native68-measure (Pod Security restricted v1.36, as codex-runner), then its deny-all
#      NetworkPolicy, each only if absent, each checked after creation. Kept: deleting a namespace hangs
#      Terminating on this VM (V2 note); later stages reuse it.
#   3. Pod v3-selinux: server dry-run (admission), create, measure, delete; absence checked.
# The Pod is cleaned up on every exit once its creation was attempted: looked up by name, deleted only when its
# run annotation and UID are this run's; otherwise left alone and reported (rc 7).
#
# Evidence: $EVIDENCE/v3-<UTC>.txt plus sidecars .probe (probe output) and .avc (ausearch output), created
# atomically before the first change. rc: 0 measured, evidence complete, cleaned up (a failed bwrap or thread/start
# is a result, not an rc); 3 refused, nothing changed; 5 a change failed (what remains is listed); 7 the Pod's
# deletion or absence is unconfirmed, or a Pod of that name is not this run's (the delete carries the UID as a
# precondition); 8 the image chain did not match;
# 9 evidence incomplete after a change; 130 interrupted.
set -u
umask 077
here=$(dirname "$0")
. "$here/lib.sh"
. "$here/evidence.sh"

TAG_REF=docker.io/library/anxious-dev-runner:68-42a1e8b
DIGEST=sha256:840679946e8071b5495b9609c6e9f33c0708d9196e712e262361e200d4641801
DIGEST_REF=docker.io/library/anxious-dev-runner@$DIGEST
HOST_ID=sha256:ddd7bad4ea352eff5d905be329e7acf32c5ff415b52c8e9ae4a759e6b714f6c0
NS=native68-measure
POD=v3-selinux
POD_JSON="$here/v3-pod.json"
POD_JSON_SHA=564d176b316604c9ab5d12122b2db74dd5a4bc407d7833d98ddb3a96482c80e2
PROBE="$here/v3-probe.py"
PROBE_SHA=f8cd5964022ee8fbc967d7c57bc094ab76a1d02a8a7c6101ef992a85d8cd966d
SECCOMP_FILE=$SECCOMP_DIR/anxious-runner-userns-v1.json
SECCOMP_SHA=9a6594419eae4089e737c787597d2ed4950fd7e1031e371f490d5b2c935dff78
READY_S=180        # kubectl wait --timeout; the process gets READY_S + 15
PROBE_S=300
CLEANUP_S=150      # whole deletion and absence check, one deadline (each call limited to the time left, +1 s)
PSA_VERSION=v1.36

pod_attempted=0; pod_uid=; incomplete_parts=
tag_attempted=0; ns_attempted=0; np_attempted=0; tag_recorded=0; ns_recorded=0; np_recorded=0
note_incomplete() { incomplete_parts="$incomplete_parts [$1]"; say "evidence incomplete: $1"; }
kt() { s=$1; shift; timeout "$s" "$K3S" kubectl "$@"; }   # an explicit outer limit for long kubectl calls
left() {  # seconds left before the cleanup deadline, capped at $1, at least 1 (timeout 0 would mean no limit)
  r=$(( deadline - $(date +%s) )); [ "$r" -gt "$1" ] && r=$1; [ "$r" -lt 1 ] && r=1; echo "$r"
}

pod_state() {  # $1 limit in seconds (default 30); prints "absent", "ours <uid>", "other <uid>" or "unknown"
  out=$(kt "${1:-30}" -n "$NS" get pod "$POD" -o jsonpath='{.metadata.uid} {.metadata.annotations.anxious\.io/run}' </dev/null 2>&1); s=$?
  if [ "$s" != 0 ]; then
    case "$out" in *NotFound*|*'not found'*) echo absent;; *) echo unknown;; esac; return 0
  fi
  uid=${out%% *}; run=${out#* }
  if [ "$run" = "$RUN" ] && { [ -z "$pod_uid" ] || [ "$uid" = "$pod_uid" ]; }; then echo "ours $uid"; else echo "other $uid"; fi
}
cleanup_pod() {  # rc 0: no Pod of this run remains; 7: not confirmed or not ours
  [ "$pod_attempted" = 1 ] || return 0
  deadline=$(( $(date +%s) + CLEANUP_S ))   # every call below is limited to the time left
  st=$(pod_state "$(left 30)"); say "cleanup: pod $NS/$POD $st"
  case "$st" in
    absent) pod_attempted=0; return 0;;
    other*|unknown) say "cleanup: not deleting a Pod that is not confirmed this run's"; return 7;;
  esac
  uid=${st#ours }
  # The delete itself carries the UID precondition: if the Pod of that name is no longer this UID, the API
  # answers Conflict and deletes nothing (checked by server dry-run on the guest, 2026-11-05).
  dout=$(printf '{"kind":"DeleteOptions","apiVersion":"v1","gracePeriodSeconds":10,"preconditions":{"uid":"%s"}}\n' "$uid" \
    | kt "$(left 60)" delete --raw "/api/v1/namespaces/$NS/pods/$POD" -f - 2>&1); s=$?
  case "$s:$dout" in
    0:*) say "cleanup: delete accepted (uid precondition $uid)";;
    *Conflict*) say "cleanup: delete refused, the Pod of that name is not uid $uid; not touched"; return 7;;
    *NotFound*|*'not found'*) say 'cleanup: already gone at delete';;
    *) say "cleanup: delete request rc $s; following the Pod anyway";;
  esac
  while [ "$(date +%s)" -lt "$deadline" ]; do
    st=$(pod_state "$(left 30)")
    case "$st" in
      absent) say "cleanup: pod $NS/$POD gone"; done_change "pod $NS/$POD (uid $uid) deleted, absence confirmed"; pod_attempted=0; return 0;;
      other*) say "cleanup: a different Pod of that name appeared ($st); not touched"; return 7;;
    esac
    [ "$(date +%s)" -lt "$deadline" ] && sleep "$(left 5)"
  done
  say "cleanup: pod $NS/$POD not confirmed gone within ${CLEANUP_S}s (last: $st)"
  return 7
}
end_run() {  # $1 rc wanted; cleanup first, then the most serious of cleanup, chain and evidence
  want=$1
  cleanup_pod || want=7
  if [ "$want" = 0 ] && [ -n "$incomplete_parts" ]; then want=9; fi
  finish "$want"
}
partial_changes() {  # after a signal: every change that was attempted, looked up again (kept, never undone)
  if [ "$tag_attempted" = 1 ] && [ "$tag_recorded" = 0 ]; then
    d_id=$(image_id "$DIGEST_REF" </dev/null)
    if [ -n "$d_id" ]; then done_change "image reference $DIGEST_REF present after interrupt (id $d_id)"
    else say "interrupt: image reference $DIGEST_REF absent or unconfirmed"; fi
  fi
  if [ "$ns_attempted" = 1 ] && [ "$ns_recorded" = 0 ]; then
    o=$(k get ns "$NS" -o name </dev/null 2>&1); s=$?
    if [ "$s" = 0 ]; then done_change "namespace $NS present after interrupt (kept)"
    else case "$o" in *NotFound*|*'not found'*) say "interrupt: namespace $NS absent";; *) note_incomplete "namespace $NS state unconfirmed after interrupt";; esac; fi
  fi
  if [ "$np_attempted" = 1 ] && [ "$np_recorded" = 0 ]; then
    o=$(k -n "$NS" get networkpolicy deny-all -o name </dev/null 2>&1); s=$?
    if [ "$s" = 0 ]; then done_change "NetworkPolicy $NS/deny-all present after interrupt (kept)"
    else case "$o" in *NotFound*|*'not found'*) say "interrupt: NetworkPolicy $NS/deny-all absent";; *) note_incomplete "NetworkPolicy state unconfirmed after interrupt";; esac; fi
  fi
}
on_signal() { trap '' INT TERM; say 'interrupted'; partial_changes; cleanup_pod || finish 7; finish 130; }
trap on_signal INT TERM

ns_ok() {  # the measurement namespace as required: labels, exactly one NetworkPolicy deny-all, no Cilium policy
  nsj=$(k get ns "$NS" -o json </dev/null 2>/dev/null) || return 1
  npj=$(k -n "$NS" get networkpolicy -o json </dev/null 2>/dev/null) || return 1
  cnp=$(k -n "$NS" get ciliumnetworkpolicy -o name </dev/null 2>/dev/null) || return 1
  [ -z "$cnp" ] || { say "namespace check: CiliumNetworkPolicy present: $(echo $cnp)"; return 1; }
  ccnp=$(k get ciliumclusterwidenetworkpolicy -o name </dev/null 2>/dev/null) || return 1
  [ -z "$ccnp" ] || { say "namespace check: CiliumClusterwideNetworkPolicy present: $(echo $ccnp)"; return 1; }
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
        bad.append("deny-all spec %s" % json.dumps(spec, sort_keys=True))
print("ok" if not bad else "FAILED " + "; ".join(bad))
sys.exit(1 if bad else 0)' "$PSA_VERSION" 2>&1); s=$?
  say "namespace check: $res"
  return $s
}

STAGE=${1:-}
case "$STAGE" in v3b|v3c) ;; *) echo 'usage: v3-selinux.sh v3b|v3c (V3 rerun under SELinux module version 1 or 2)'; exit 2;; esac
start_evidence "$STAGE"
RUN=${log##*/$STAGE-}; RUN=${RUN%.txt}
say "run $RUN"
# The SELinux module as installed now (read-only): name and priority, and the loaded policy's hash.
mods=$(semodule -lfull 2>/dev/null | grep -E '(^| )anxious_runner_bwrap( |$)') || mods='none'
say "selinux module: $mods"
lp=$(sha256sum /sys/fs/selinux/policy 2>/dev/null) && say "loaded policy sha256 ${lp%% *}" || say 'loaded policy sha256 LOOKUP FAILED'
probefile=$(sidecar probe) || refuse 'sidecar probe'
avcfile=$(sidecar avc) || refuse 'sidecar avc'

# --- read-only prechecks (any failure: nothing changed) ---
[ "$(sha256sum "$POD_JSON" 2>/dev/null | cut -d' ' -f1)" = "$POD_JSON_SHA" ] || refuse 'v3-pod.json digest'
[ "$(sha256sum "$PROBE" 2>/dev/null | cut -d' ' -f1)" = "$PROBE_SHA" ] || refuse 'v3-probe.py digest'
grep -Fq "\"image\": \"$DIGEST_REF\"" "$POD_JSON" || refuse 'v3-pod.json does not name the digest reference'
grep -Fq '"annotations"' "$POD_JSON" && refuse 'v3-pod.json already has annotations'
[ "$(sha256sum "$SECCOMP_FILE" 2>/dev/null | cut -d' ' -f1)" = "$SECCOMP_SHA" ] || refuse 'seccomp profile digest'
[ "$(getenforce 2>/dev/null)" = Enforcing ] || refuse 'SELinux is not Enforcing'
say "selinux $(getenforce) policy $(rpm -q selinux-policy container-selinux 2>/dev/null | tr '\n' ' ')"
node=$(k get node "$NODE" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' </dev/null 2>/dev/null) \
  && [ "$node" = True ] || refuse "node $NODE not Ready"
tag_id=$(image_id "$TAG_REF" </dev/null) && [ "$tag_id" = "$HOST_ID" ] || refuse "tag reference id ${tag_id:-none} != host id"
refs=$(timeout 30 "$K3S" ctr -n k8s.io images ls -q </dev/null 2>/dev/null) || refuse 'ctr images ls'
if printf '%s\n' "$refs" | grep -Fxq "$DIGEST_REF"; then
  d_id=$(image_id "$DIGEST_REF" </dev/null) && [ "$d_id" = "$HOST_ID" ] || refuse "digest reference exists with id ${d_id:-none}"
  need_tag=0; say "digest reference present with the host id"
else
  need_tag=1; say "digest reference absent: will tag"
fi
ns_out=$(k get ns "$NS" -o name </dev/null 2>&1); s=$?
if [ "$s" = 0 ]; then
  need_ns=0
  ns_ok || refuse "namespace $NS exists but is not the measurement namespace as required"
  pods=$(k -n "$NS" get pods -o name </dev/null 2>/dev/null) || refuse 'pod list'
  [ -z "$pods" ] || refuse "namespace $NS has pods: $(echo $pods)"
  say "namespace $NS present and as required, no pods"
else
  case "$ns_out" in *NotFound*|*'not found'*) need_ns=1; say "namespace $NS absent: will create";; *) refuse 'namespace lookup';; esac
  ccnp=$(k get ciliumclusterwidenetworkpolicy -o name </dev/null 2>/dev/null) || refuse 'CiliumClusterwideNetworkPolicy lookup'
  [ -z "$ccnp" ] || refuse "a CiliumClusterwideNetworkPolicy exists: $(echo $ccnp)"
fi
avc_date=$(date +%x) && avc_time=$(date +%T) || refuse 'clock'
for t in AVC USER_AVC; do
  out=$(ausearch --input-logs -m "$t" -ts "$avc_date" "$avc_time" </dev/null 2>&1); s=$?
  { [ "$s" = 0 ] || { [ "$s" = 1 ] && case "$out" in *'no matches'*) true;; *) false;; esac; }; } \
    || refuse "ausearch does not accept the window start ($avc_date $avc_time)"
done
say "window starts $avc_date $avc_time (ausearch -ts form, checked)"
# The Pod object as created: the hashed file plus this run's annotation, nothing else.
podobj=$(sed "s|^ \"metadata\": {\$| \"metadata\": {\n  \"annotations\": {\"anxious.io/run\": \"$RUN\"},|" "$POD_JSON") || refuse 'pod object'
[ "$(printf '%s\n' "$podobj" | grep -c '"anxious.io/run"')" = 1 ] || refuse 'run annotation not placed'
printf '%s\n' "$podobj" | python3 -I -c 'import json, sys; json.load(sys.stdin)' || refuse 'pod object is not JSON'

# --- 1. digest reference ---
if [ "$need_tag" = 1 ]; then
  changing
  tag_attempted=1
  timeout 60 "$K3S" ctr -n k8s.io images tag "$TAG_REF" "$DIGEST_REF" </dev/null >/dev/null 2>&1; s=$?
  d_id=$(image_id "$DIGEST_REF" </dev/null)
  [ -n "$d_id" ] && { done_change "image reference $DIGEST_REF tagged (id $d_id)"; tag_recorded=1; }
  [ "$s" = 0 ] || { say "tag rc $s"; finish 5; }
  [ "$d_id" = "$HOST_ID" ] || { say "digest reference id ${d_id:-none} != host id"; finish 8; }
  say "digest reference resolves to the host id"
fi

# --- 2. measurement namespace, then its NetworkPolicy (each verified) ---
if [ "$need_ns" = 1 ]; then
  changing
  ns_attempted=1   # set before the request: a signal from here on looks the object up again
  printf '%s\n' "{\"apiVersion\":\"v1\",\"kind\":\"Namespace\",\"metadata\":{\"name\":\"$NS\",\"labels\":{
  \"anxious.io/purpose\":\"native68-measure\",
  \"pod-security.kubernetes.io/enforce\":\"restricted\",\"pod-security.kubernetes.io/enforce-version\":\"$PSA_VERSION\",
  \"pod-security.kubernetes.io/audit\":\"restricted\",\"pod-security.kubernetes.io/audit-version\":\"$PSA_VERSION\",
  \"pod-security.kubernetes.io/warn\":\"restricted\",\"pod-security.kubernetes.io/warn-version\":\"$PSA_VERSION\"}}}" \
    | k create -f - >/dev/null 2>&1; s=$?
  k get ns "$NS" -o name </dev/null >/dev/null 2>&1 && { done_change "namespace $NS created (kept)"; ns_recorded=1; }
  [ "$s" = 0 ] || { say "namespace create rc $s"; finish 5; }
  np_attempted=1
  printf '%s\n' "{\"apiVersion\":\"networking.k8s.io/v1\",\"kind\":\"NetworkPolicy\",\"metadata\":{\"name\":\"deny-all\",\"namespace\":\"$NS\"},
  \"spec\":{\"podSelector\":{},\"policyTypes\":[\"Ingress\",\"Egress\"]}}" | k create -f - >/dev/null 2>&1; s=$?
  k -n "$NS" get networkpolicy deny-all -o name </dev/null >/dev/null 2>&1 && { done_change "NetworkPolicy $NS/deny-all created (kept)"; np_recorded=1; }
  [ "$s" = 0 ] || { say "NetworkPolicy create rc $s"; finish 5; }
  ns_ok || { say 'the created namespace does not check out'; finish 5; }
fi

# --- 3. Pod: admission dry-run, create, measure, delete ---
dry=$(printf '%s\n' "$podobj" | kt 45 create --dry-run=server -f - -o name 2>&1); s=$?
say "server dry-run rc $s: $(printf '%s' "$dry" | head -3 | tr '\n' ' ')"
[ "$s" = 0 ] || { say 'admission refused the Pod (namespace kept)'; finish 5; }
changing
pod_attempted=1
printf '%s\n' "$podobj" | kt 45 create -f - >/dev/null 2>&1; s=$?
st=$(pod_state)
case "$st" in
  ours*) pod_uid=${st#ours }; done_change "pod $NS/$POD created (uid $pod_uid, run $RUN)"
         [ "$s" = 0 ] || { say "pod create rc $s although the Pod exists: not measured"; end_run 5; };;
  *) say "pod create rc $s, pod $st"; end_run 5;;
esac
kt $((READY_S + 15)) -n "$NS" wait --for=condition=Ready "pod/$POD" --timeout="${READY_S}s" </dev/null >/dev/null 2>&1; ready=$?
say "pod ready rc $ready"
podinfo=$(k -n "$NS" get pod "$POD" -o jsonpath='uid={.metadata.uid} phase={.status.phase} imageID={.status.containerStatuses[0].imageID} containerID={.status.containerStatuses[0].containerID} restarts={.status.containerStatuses[0].restartCount}' </dev/null 2>&1) \
  || note_incomplete 'pod status lookup'
say "pod $podinfo"
chain=ok
case "$podinfo" in
  *"imageID=$DIGEST_REF "*) say "chain pod imageID = digest reference $DIGEST_REF";;
  *"imageID=$HOST_ID "*) say "chain pod imageID = host/CRI id $HOST_ID";;
  *) chain=mismatch; say "chain pod imageID names neither $DIGEST nor $HOST_ID";;
esac
if [ "$ready" = 0 ]; then
  # Volumes as Kubernetes made them (nothing prepares them): owner, mode, and whether UID 10001 can write.
  vols=$(kt 30 -n "$NS" exec "$POD" -- sh -c 'for d in /state /control /tmp; do
    if t=$(mktemp -p "$d" .v3w.XXXXXX 2>/dev/null); then rm -f "$t"; w=writable; else w=NOT-writable; fi
    echo "$d $(stat -c "%u:%g %a %C" "$d" 2>/dev/null || echo "? ? LABEL-UNCONFIRMED") $w"; done' </dev/null 2>&1) || note_incomplete 'volume lookup'
  printf '%s\n' "$vols" | grep -q 'LABEL-UNCONFIRMED' && note_incomplete 'volume owner/mode/label lookup'
  while IFS= read -r l; do [ -n "$l" ] && say "volume $l"; done <<EOF_VOLS
$vols
EOF_VOLS
  cid=$(printf '%s' "$podinfo" | sed -n 's/.*containerID=containerd:\/\/\([0-9a-f]*\).*/\1/p')
  pid=$(timeout 30 "$K3S" crictl inspect -o go-template --template '{{.info.pid}}' "$cid" </dev/null 2>/dev/null)
  case "$pid" in
    ''|*[!0-9]*) note_incomplete 'host view: container pid';;
    *) label=$(tr -d '\0' < "/proc/$pid/attr/current" 2>/dev/null) && [ -n "$label" ] || note_incomplete 'host view: label'
       stat_l=$(grep -E '^(Uid|CapEff|NoNewPrivs|Seccomp):' "/proc/$pid/status" 2>/dev/null | tr '\t\n' '  ') \
         && [ -n "$stat_l" ] || note_incomplete 'host view: status'
       say "host view: pid $pid label ${label:-?}"; say "host view: ${stat_l:-?}";;
  esac
  if [ "$(printf '%s\n' "$vols" | grep -c ' writable$')" != 3 ]; then
    say 'probe not run: a measurement volume is not writable by UID 10001 (see volume lines): V3 NOT COMPLETE'
  else
    pout=$(kt "$PROBE_S" -n "$NS" exec -i "$POD" -- python -I - < "$PROBE" 2>&1); prc=$?
    say "probe rc $prc"
    printf '%s\n' "$pout" > "$probefile" || note_incomplete 'probe sidecar write'
    ph=$(sha256sum "$probefile" 2>/dev/null) && ph=${ph%% *} && [ ${#ph} = 64 ] || note_incomplete 'probe sidecar hash'
    say "probe sidecar $(printf '%s\n' "$pout" | wc -l) lines sha256 ${ph:-?}"
    summary=$(printf '%s\n' "$pout" | grep -E '^(selinux_label|capeff|no_new_privs|seccomp|sandbox_rc|sandbox_stderr|sandbox read_ok|sandbox inner_home|sandbox bash_startup|workspace_marker|t|functions_rc|sandbox2|sandbox2_stderr|mask_after|proc_replay|proc_overmounts|requirements|config|thread_start|thread_start_check|arg0_entries|app_server|app_server_exit|probe) ' | head -80)
    while IFS= read -r l; do [ -n "$l" ] && say "probe $l"; done <<EOF_SUMMARY
$summary
EOF_SUMMARY
    # Host observation (the sandbox has no /proc of its own when Codex falls back): one sandboxed `sleep 20`
    # started through the same `codex sandbox` path, found by its nonce handshake, the container's cgroup and a
    # bwrap ancestor. Namespaces are expected to differ from the container's (the claim); the SELinux label is
    # recorded, not judged (container_t with the same MCS is expected).
    nonce=$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')
    case "$pid" in
      ''|*[!0-9]*) note_incomplete 'host observation: no container pid';;
      *)
        cg=$(sed -n 's/^0:://p' "/proc/$pid/cgroup" 2>/dev/null)
        kt 60 -n "$NS" exec "$POD" -- env HOME=/state/home CODEX_HOME=/state/codex /usr/local/bin/codex sandbox -P native \
          -C /state/workspace -- sh -c 'echo "$1" > /state/workspace/v3-obs-ready; exec sleep 20' obs "$nonce" \
          </dev/null >/dev/null 2>&1 &
        obs_job=$!
        obs_deadline=$(( $(date +%s) + 30 )); r=
        while [ "$(date +%s)" -lt "$obs_deadline" ]; do
          r=$(kt 10 -n "$NS" exec "$POD" -- cat /state/workspace/v3-obs-ready </dev/null 2>/dev/null)
          [ "$r" = "$nonce" ] && break
          sleep 1
        done
        if [ "$r" != "$nonce" ]; then
          note_incomplete 'host observation: the sandboxed sleep did not report ready within 30 s'
        else
          found=; found_chain=
          for sp in $(cat "/sys/fs/cgroup$cg/cgroup.procs" 2>/dev/null); do
            [ "$(cat "/proc/$sp/comm" 2>/dev/null)" = sleep ] || continue
            [ "$(tr '\0' ' ' < "/proc/$sp/cmdline" 2>/dev/null)" = 'sleep 20 ' ] || continue
            a=$sp; chain=
            while [ -n "$a" ] && [ "$a" != 1 ] && [ "$a" != "$pid" ]; do
              chain="$chain $(cat "/proc/$a/comm" 2>/dev/null)"; a=$(awk '{print $4}' "/proc/$a/stat" 2>/dev/null)
            done
            case "$chain" in *bwrap*) found="$found $sp"; found_chain=$chain;; esac
          done
          count=$(echo $found | wc -w)
          if [ "$count" != 1 ]; then
            note_incomplete "host observation: $count matching sandboxed sleep processes (want 1):$found"
          else
            sp=$(echo $found)
            for n in user pid mnt net; do
              a=$(readlink "/proc/$pid/ns/$n" 2>/dev/null); b=$(readlink "/proc/$sp/ns/$n" 2>/dev/null)
              if [ -n "$a" ] && [ -n "$b" ]; then
                [ "$a" != "$b" ] && v=differs || v=SAME
              else
                v=UNKNOWN; note_incomplete "host observation: ns $n lookup"
              fi
              say "host observation ns $n container=$a sandbox=$b $v"
            done
            say "host observation sandbox pid $sp label $(tr -d '\0' < "/proc/$sp/attr/current" 2>/dev/null) start $(awk '{print $22}' "/proc/$sp/stat" 2>/dev/null) ancestry$found_chain"
          fi
        fi
        wait "$obs_job" 2>/dev/null; say "host observation: sandbox exec rc $?";;
    esac
  fi
else
  desc=$(kt 30 -n "$NS" describe pod "$POD" </dev/null 2>&1); s=$?
  [ "$s" = 0 ] || note_incomplete "pod describe rc $s"
  events=$(printf '%s\n' "$desc" | sed -n '/^Events:/,$p' | head -20)
  [ "$s" = 0 ] || events=$(printf '%s\n' "$desc" | head -5)
  conds=$(kt 30 -n "$NS" get pod "$POD" -o jsonpath='{range .status.conditions[*]}{.type}={.status}({.reason}) {end}| waiting={.status.containerStatuses[0].state.waiting.reason}:{.status.containerStatuses[0].state.waiting.message}' </dev/null 2>&1) \
    || note_incomplete 'pod conditions lookup'
  say "pod conditions $conds"
  while IFS= read -r l; do [ -n "$l" ] && say "event $l"; done <<EOF_EVENTS
$events
EOF_EVENTS
fi
cleanup_pod || finish 7
# AVC after the Pod is gone, so its teardown is in the window too.
for t in AVC USER_AVC; do
  out=$(ausearch --input-logs -m "$t" -ts "$avc_date" "$avc_time" </dev/null 2>&1); s=$?
  case "$s" in
    0) printf '%s\n' "$out" >> "$avcfile" || note_incomplete "avc $t sidecar write"
       say "avc $t $(printf '%s\n' "$out" | grep -c "^type=$t ")";;
    1) case "$out" in *'no matches'*) say "avc $t 0";; *) note_incomplete "avc $t lookup";; esac;;
    *) note_incomplete "avc $t lookup";;
  esac
done
ah=$(sha256sum "$avcfile" 2>/dev/null) && ah=${ah%% *} && [ ${#ah} = 64 ] || note_incomplete 'avc sidecar hash'
say "avc sidecar sha256 ${ah:-?}"
[ "$chain" = ok ] || end_run 8
end_run 0
