#!/bin/sh
# #68 dev-VM V3: the runner image under the node's SELinux policy (container_t), in a Pod with the runner's own
# security settings (v3-pod.json, rendered by `python -m ops.native_measure`). Development VM only. Run as root in
# the guest after review cleared this script's SHA-256.
#
# Measures, never secrets: the Pod imageID (last link of the V1 chain), the container's SELinux label and
# capabilities from the host, the in-Pod probe (v3-probe.py: bubblewrap through `codex sandbox`, fake-marker reads,
# the runner's app-server config checks and one offline thread/start, CODEX_HOME/tmp/arg0), and every AVC/USER_AVC
# from the window start. No model call, no network from the Pod (deny-all NetworkPolicy), no credentials.
#
# Changes, in order, each recorded before and after:
#   1. the digest reference docker.io/library/anxious-dev-runner@$DIGEST is tagged on the imported 68-42a1e8b
#      image if it does not exist yet (infra/hosting/README.md: import registers only the tag). Kept.
#   2. namespace native68-measure (Pod Security restricted v1.36, as codex-runner) with a deny-all NetworkPolicy,
#      if absent. Kept: deleting a namespace hangs Terminating on this VM (V2 note); only Pods are deleted.
#   3. Pod v3-selinux created, measured, then deleted; its absence is checked.
# A signal or failure after step 3 started deletes the Pod (and checks) before exiting.
#
# Evidence: $EVIDENCE/v3-<UTC>.txt plus sidecars .probe (probe output) and .avc (ausearch output), created
# atomically before the first change. rc: 0 measured and cleaned up (the measurement results are in the evidence;
# a failed bwrap or thread/start is a result, not an rc); 3 refused, nothing changed; 5 a change failed;
# 7 the Pod could not be deleted or its absence not confirmed; 8 the image chain did not match; 9 evidence
# incomplete after a change; 130 interrupted.
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
PROBE_SHA=1487a00168f9105b1074070f79e181b15a6d88d0572684d87efc09fdc7d6be17
SECCOMP_FILE=$SECCOMP_DIR/anxious-runner-userns-v1.json
SECCOMP_SHA=9a6594419eae4089e737c787597d2ed4950fd7e1031e371f490d5b2c935dff78
READY_S=180

pod_created=0
avc_date=; avc_time=

pod_gone() {  # 0 when the API says NotFound; 1 when it still exists; 2 when the lookup failed otherwise
  out=$(k -n "$NS" get pod "$POD" -o name 2>&1 </dev/null); s=$?
  [ "$s" = 0 ] && return 1
  case "$out" in *NotFound*|*'not found'*) return 0;; *) return 2;; esac
}
delete_pod() {  # delete and confirm; rc 0 confirmed gone, 7 otherwise
  say "deleting pod $NS/$POD"
  k -n "$NS" delete pod "$POD" --wait=true --timeout=120s </dev/null >/dev/null 2>&1
  i=0
  while [ $i -lt 24 ]; do
    pod_gone; g=$?
    if [ "$g" = 0 ]; then say "pod $NS/$POD gone"; done_change "pod $NS/$POD deleted (absence confirmed)"; pod_created=0; return 0; fi
    i=$((i + 1)); sleep 5
  done
  say "pod $NS/$POD NOT confirmed gone (last lookup rc $g)"
  return 7
}
avc_collect() {  # write every AVC and USER_AVC since the window start to the .avc sidecar; print counts
  for t in AVC USER_AVC; do
    out=$(ausearch --input-logs -m "$t" -ts "$avc_date" "$avc_time" </dev/null 2>&1); s=$?
    case "$s" in
      0) printf '%s\n' "$out" >> "$avcfile" || logfail=1; say "avc $t $(printf '%s\n' "$out" | grep -c "^type=$t ")";;
      1) case "$out" in *'no matches'*) say "avc $t 0";; *) say "avc $t LOOKUP FAILED"; return 1;; esac;;
      *) say "avc $t LOOKUP FAILED"; return 1;;
    esac
  done
}
on_signal() {
  trap '' INT TERM
  say 'interrupted'
  if [ "$pod_created" = 1 ]; then delete_pod || finish 7; fi
  finish 130
}
trap on_signal INT TERM

start_evidence v3
probefile=$(sidecar probe) || refuse 'sidecar probe'
avcfile=$(sidecar avc) || refuse 'sidecar avc'

# --- read-only prechecks (any failure: nothing changed) ---
[ "$(sha256sum "$POD_JSON" 2>/dev/null | cut -d' ' -f1)" = "$POD_JSON_SHA" ] || refuse 'v3-pod.json digest'
[ "$(sha256sum "$PROBE" 2>/dev/null | cut -d' ' -f1)" = "$PROBE_SHA" ] || refuse 'v3-probe.py digest'
grep -Fq "\"image\": \"$DIGEST_REF\"" "$POD_JSON" || refuse 'v3-pod.json does not name the digest reference'
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
ns_out=$(k get ns "$NS" -o json </dev/null 2>&1); s=$?
if [ "$s" = 0 ]; then
  need_ns=0
  for l in 'enforce' 'audit' 'warn'; do
    printf '%s' "$ns_out" | grep -Fq "\"pod-security.kubernetes.io/$l\": \"restricted\"" || refuse "namespace $NS lacks $l=restricted"
  done
  printf '%s' "$ns_out" | grep -Fq '"anxious.io/purpose": "native68-measure"' || refuse "namespace $NS is not the measurement namespace"
  k -n "$NS" get networkpolicy deny-all -o name </dev/null >/dev/null 2>&1 || refuse "namespace $NS has no deny-all NetworkPolicy"
  pods=$(k -n "$NS" get pods -o name </dev/null 2>/dev/null) || refuse 'pod list'
  [ -z "$pods" ] || refuse "namespace $NS has pods: $(echo $pods)"
  say "namespace $NS present (restricted, deny-all, no pods)"
else
  case "$ns_out" in *NotFound*|*'not found'*) need_ns=1; say "namespace $NS absent: will create";; *) refuse 'namespace lookup';; esac
fi
avc_date=$(date +%x) && avc_time=$(date +%T) || refuse 'clock'
for t in AVC USER_AVC; do
  out=$(ausearch --input-logs -m "$t" -ts "$avc_date" "$avc_time" </dev/null 2>&1); s=$?
  { [ "$s" = 0 ] || { [ "$s" = 1 ] && case "$out" in *'no matches'*) true;; *) false;; esac; }; } \
    || refuse "ausearch does not accept the window start ($avc_date $avc_time)"
done
say "window starts $avc_date $avc_time (ausearch -ts form, checked)"

# --- 1. digest reference ---
if [ "$need_tag" = 1 ]; then
  changing
  timeout 60 "$K3S" ctr -n k8s.io images tag "$TAG_REF" "$DIGEST_REF" </dev/null >/dev/null 2>&1 || { say 'tag failed'; finish 5; }
  done_change "image reference $DIGEST_REF tagged"
  d_id=$(image_id "$DIGEST_REF" </dev/null) && [ "$d_id" = "$HOST_ID" ] || { say "digest reference id ${d_id:-none} != host id"; finish 8; }
  say "digest reference resolves to the host id"
fi

# --- 2. measurement namespace ---
if [ "$need_ns" = 1 ]; then
  changing
  printf '%s\n' '{"apiVersion":"v1","kind":"List","items":[
 {"apiVersion":"v1","kind":"Namespace","metadata":{"name":"native68-measure","labels":{
  "anxious.io/purpose":"native68-measure",
  "pod-security.kubernetes.io/enforce":"restricted","pod-security.kubernetes.io/enforce-version":"v1.36",
  "pod-security.kubernetes.io/audit":"restricted","pod-security.kubernetes.io/audit-version":"v1.36",
  "pod-security.kubernetes.io/warn":"restricted","pod-security.kubernetes.io/warn-version":"v1.36"}}},
 {"apiVersion":"networking.k8s.io/v1","kind":"NetworkPolicy","metadata":{"name":"deny-all","namespace":"native68-measure"},
  "spec":{"podSelector":{},"policyTypes":["Ingress","Egress"]}}]}' \
    | k create -f - >/dev/null 2>&1 || { say 'namespace create failed'; finish 5; }
  done_change "namespace $NS with deny-all NetworkPolicy created (kept)"
fi

# --- 3. Pod, measurement, deletion ---
changing
if ! k create -f "$POD_JSON" </dev/null >/dev/null 2>&1; then
  say 'pod create failed'
  pod_gone || { pod_created=1; delete_pod || finish 7; }
  finish 5
fi
pod_created=1
done_change "pod $NS/$POD created"
k -n "$NS" wait --for=condition=Ready "pod/$POD" --timeout="${READY_S}s" </dev/null >/dev/null 2>&1; ready=$?
say "pod ready rc $ready"
podinfo=$(k -n "$NS" get pod "$POD" -o jsonpath='uid={.metadata.uid} phase={.status.phase} imageID={.status.containerStatuses[0].imageID} containerID={.status.containerStatuses[0].containerID} restarts={.status.containerStatuses[0].restartCount}' </dev/null 2>&1)
say "pod $podinfo"
chain=ok
case "$podinfo" in
  *"imageID=$DIGEST_REF "*) say "chain pod imageID = digest reference $DIGEST_REF";;
  *"imageID=$HOST_ID "*) say "chain pod imageID = host/CRI id $HOST_ID";;
  *) chain=mismatch; say "chain pod imageID names neither $DIGEST nor $HOST_ID";;
esac
if [ "$ready" = 0 ]; then
  cid=$(printf '%s' "$podinfo" | sed -n 's/.*containerID=containerd:\/\/\([0-9a-f]*\).*/\1/p')
  pid=$(timeout 30 "$K3S" crictl inspect -o go-template --template '{{.info.pid}}' "$cid" </dev/null 2>/dev/null)
  case "$pid" in ''|*[!0-9]*) say "host view: container pid LOOKUP FAILED";;
    *) say "host view: label $(tr -d '\0' < /proc/$pid/attr/current 2>/dev/null || echo LOOKUP-FAILED)"
       say "host view: $(grep -E '^(Uid|CapEff|NoNewPrivs|Seccomp):' /proc/$pid/status 2>/dev/null | tr '\t\n' '  ')";;
  esac
  timeout 300 "$K3S" kubectl -n "$NS" exec -i "$POD" -- python -I - < "$PROBE" > "$probefile" 2>&1; prc=$?
  say "probe rc $prc, $(wc -l < "$probefile") lines, sha256 $(sha256sum "$probefile" | cut -d' ' -f1)"
  # Summary lines into the main evidence (read in this shell, so a failed write is never lost in a subshell).
  summary=$(grep -E '^(selinux_label|capeff|sandbox_rc|sandbox_stderr|sandbox read_ok|workspace_marker|requirements|config|thread_start|thread_start_check|arg0_entries|app_server_exit|probe) ' "$probefile" | head -40)
  while IFS= read -r l; do [ -n "$l" ] && say "probe $l"; done <<EOF_SUMMARY
$summary
EOF_SUMMARY
else
  events=$(k -n "$NS" describe pod "$POD" </dev/null 2>&1 | sed -n '/^Events:/,$p' | head -20)
  while IFS= read -r l; do [ -n "$l" ] && say "event $l"; done <<EOF_EVENTS
$events
EOF_EVENTS
fi
delete_pod || finish 7
avc_collect || { say 'AVC collection incomplete'; finish 9; }
say "avc sidecar sha256 $(sha256sum "$avcfile" | cut -d' ' -f1)"
[ "$chain" = ok ] || finish 8
finish 0
