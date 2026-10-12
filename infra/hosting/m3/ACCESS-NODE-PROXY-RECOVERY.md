# Issue #7: one-node BPF tproxy trial, preparation only

Branch `task/m3-codex-login`, runtime/image source
`91577194a5a9343778b68fcaa258d5941f92aa5a`. This package does not change the
production images, any CNP/CCNP, DNS names, FQDN rules, default-deny, or API P1.
The preceding same-image L4-success/L7-failure and aggregate counter delta0
support a redirect-path experiment. They do not establish an iptables defect
or prove that no DNS reached the proxy. A successful trial also combines mode
change with agent recreation, so it cannot separate those causes.

**No operational authorization is included.** Backend prepares and tests source
offline. PM first gets independent review of this package, then may execute a
separately approved P0 read-only collection. PM uses those results to obtain
the required availability/privilege approval before P1/P2. Prior Pod-only or
web rollout approval does not authorize a Cilium replacement.

## Change, impact and limits

The sole retained configuration object to create is:

```yaml
apiVersion: cilium.io/v2
kind: CiliumNodeConfig
metadata:
  namespace: kube-system
  name: access-dns-bpf-tproxy-trial-v1
spec:
  nodeSelector:
    matchLabels:
      kubernetes.io/hostname: <fresh exact retained hostname>
  defaults:
    enable-bpf-tproxy: "true"
```

The renderer substitutes the hostname only after exactly one matching Node
with the retained Node UID is verified. It refuses existing matching configs
and any pre-existing object with this name. Creation uses `create`, never
`apply`/adoption. The [official per-node procedure](https://docs.cilium.io/en/stable/configuration/per-node-config/)
requires Cilium Pod recreation for changes to take effect. This package
normally recreates the selected daemonset agent **twice**: once for true, once
after deleting the owned config to restore false. It never edits the daemonset,
global ConfigMap, Helm values, labels, policy, other NodeConfig, or other Pods.
No cordon/drain, privileged Kubernetes Pod, packet capture, monitor stream,
endpoint list, or DN12 source is introduced.

The impact is **all workloads on the selected Node**, potentially the whole
cluster on a single-node installation. Policy regeneration, DNS proxy/L7
connections, networking and callback availability may be disrupted. Existing
connection/socket/cache/process state cannot be restored exactly. A diagnostic
holder also joins the original Runner Service briefly, becomes Ready without
an application server, and does not listen on8080; callback traffic selecting
that endpoint can fail. API mapping remains disabled and Runner replicas0.
PM must include these effects in the availability decision, plus the Node's
current workload metadata inventory. Do not restart unrelated workloads to
make a failed gate pass.

Both Ready windows are120s from the corresponding conditional delete. P1
Docker attached execution is bounded to15s+1s kill; the owned-container wrapper reserves
up to100s including creation, inspection and exact cleanup. A P2 operator
session has a600s hard budget. Individual
Kubernetes/exec commands use8s request timeout and12s wall timeout+1s kill.
Reserve at least240s for rollback: if insufficient time remains, skip DNS and
rollback immediately. On uncertain create/delete/exec outcome, reconcile
read-only metadata and stop further experimental mutations; do not replay.
Read-only observations may be repeated within the stated deadline.

## Files and offline invocation

`access_node_proxy_prepare.py` never accesses Kubernetes; it emits or checks
JSON from private evidence. Actions are `select`, `preflight`, `emit`,
`delete-agent`, `delete-config`, `health`, `dns-job`, `dns-scope`, `disposition`.
Its CLI takes `ACTION --bundle /evidence/<bundle>.json`. Run it **inside Docker**
with the reviewed API test image, source directory mounted read-only at
`/checks`, private metadata mounted read-only at `/evidence`, networknone,
read-only root, all capabilities dropped, and `PYTHONPATH=/checks:/app`.
No host Python. Emit-only containers write JSON to operator-owned exclusive
0600 files through host stdout. Source is mounted from the reviewed SHA.

The test image currently used is
`sha256:e1cd49c5e8d36fae0689e727c06459529bbc67e823e08385602ea534c6397450`.
PM must confirm its local provenance before using it operationally; this is
a host tooling image, not a new cluster deployment or production image.

Private bundles are assembled from the following saved raw files, using
Docker/jq rather than hand-altering metadata. All lists must be complete raw
API lists with nonempty resourceVersion and no continuation token. Preserve
full original DS/Pod containers. Typed NodeList/PodList/CiliumNodeConfigList
can omit nested TypeMeta; the guard interprets types in copies and rejects
conflicting explicit types. It permits only the exact standard serviceaccount
admission mount/projection described below. Other differences are unsupported,
never normalize the DS from a Pod to bypass comparison.

| Action | Bundle inputs / output |
| --- | --- |
| select | expectedNode{name,uid,hostname}, nodes, daemonset, agents → exact initial agent identity |
| preflight | select inputs plus crd,nodeConfigs,configMap,policies,version,settings,independentApiServer,independentApiGetPassed,snapshot,inputs,images,baseline,endpoints → receipt |
| emit | receipt,capability → NodeConfig, **not permission** |
| delete-agent | receipt,nodes,daemonset,agents,stage(apply/rollback),replacement(rollback) → raw DELETE path+DeleteOptions UID/RV |
| delete-config | receipt,current,owned{uid},emitted → raw DELETE path+DeleteOptions UID/RV |
| health | receipt,nodes,daemonset,agents,settings,status,configMap,policies,snapshot,inputs,images,endpoints,flag,excluded(UIds),nodeConfigs,ownedConfig{uid}(true) → Ready agent identity+container signature |
| dns-job | receipt,snapshot,inputs,images,nonce(32 lowercase hex) → original-label holder Job |
| dns-scope | receipt,replacement(health output),job,pod,cep,agent,services,production,globalPolicy,policies,reference,endpoint,nonce → fresh production L7/ACK scope |
| disposition | stage,elapsed(integer seconds),result(ok/failed/pending) → bounded next action; DNS success and failure both cleanup/rollback |

`policies` is exactly the current production Runner CNP, API P1 CNP and node
protection CCNP. UID+spec fingerprints ignore incidental policy status RV
updates; source-rendered Runner and API P1 specs must match. No clone policy
is created. Current ConfigMap and DS full witnesses must remain unchanged.

`endpoints` contains exactly `api` and `dns`, for the **already selected** API
and CoreDNS Pods on this Node, assembled from fresh exact Pod/CEP/agent GETs
and one numeric local endpoint GET each. Each entry has `podUid`, `cepUid`,
`attachment`, `agentUid`, `state`, `policy` (the native endpoint status policy).
Bind the CEP Pod owner UID/IP/attachment and agent Node before assembling.
Only selected endpoint metadata is read, never an endpoint list. Both desired
and realized revisions must agree and bepositive. Hashes cover the remaining
realized policy fields, not revision counters. For fixed Cilium1.20.2,
[GetPolicyModel](https://github.com/cilium/cilium/blob/v1.20.2/pkg/endpoint/api.go)
assigns both `build` and `policy-revision` from the same realized revision,
and both desired fields from the same desired revision. The
[EndpointPolicy model](https://github.com/cilium/cilium/blob/v1.20.2/api/v1/models/endpoint_policy.go)
describes policy-revision as agent-local. Each side's build must be a positive
exact integer equal to its revision, and desired/realized revisions must be
equal. Only after these checks are both aliases omitted from cross-agent
content fingerprints. They can change when the agent restarts; this does not
permit mismatched same-capture revision/build, policy ID/identity/CIDR/allow/
deny/enforcement, L4 content or any other field change.
[Repository](https://github.com/cilium/cilium/blob/v1.20.2/pkg/policy/repository.go)
initializes its in-memory revision to1 and increments it on policy updates;
revision numbers are not a policy-content identity across agent processes.

Receipt endpointWitnessFormat is `cilium-1.20.2-stable-policy-v2`. Old hash
receipts are rejected by health; regenerate the complete preflight receipt
from its preserved original raw bundle under this source, then re-evaluate
the preserved health bundle with that derived baseline in memory. Do not
edit raw evidence, drop fields there, blindly transform an old hash or rewrite
historical refusal results. Independent review must verify the new baseline
derivation and full restored health before declaring restoration complete.

After the stored restoration gate is independently closed, the shortest
proposed continuation is a fresh P0 under this witness format, verify the
existing successful P1 Node/boot/kernel/program/lifecycle binding, then one
reviewed trial with the corrected health gate and original-label single DNS
batch from the P2 procedure below. If the prior P1 binding changed, stop and
reconcile its prerequisite before further mutation. Do not repeat an unchanged
failed trial, bypass health, or add a new diagnostic framework. Record the
concrete changed-source operating disposition through PM first. Both success
and failure still clean the Job and default rollback; four DNS replies do not
authorize persistent true, original credential supply, API enable or Native
execution. Those existing holds remain for the subsequent user-visible flow.

Only the outer L4 ingress/
egress row order is ignored, by sorting complete row JSON in comparison
copies and before hashing. [Cilium1.20.2 L4Policy.GetModel/Filters](https://github.com/cilium/cilium/blob/v1.20.2/pkg/policy/l4.go)
appends filters from Go maps without sorting the output rows. This changes
serialization order, not policy priority. Every row field, nested array order,
embedded rule string and duplicate multiplicity remains exact; missing/extra/
altered rows or any other policy field still refuse. Raw evidence is never
rewritten, and equal positive revisions alone remain insufficient.
After each recreation, resolve
the new local endpoint ID from the exact CEP and recheck attachments, Pod/CEP
UIDs, fresh agent UID, realization and original policy. If selected Pods/CEPs
changed or serialized policy differs, stop/handoff; do not claim restoration.

## P0 — reviewed read-only preflight, no DNS/BPF loads

Use an existing trusted root-owned absolute `kubectl` path and existing host
SSH/console session. Establish that its existing kubeconfig endpoint is
`https://127.0.0.1:6443` or `https://[::1]:6443`, accessible from that host
without Cilium Service/overlay networking. `config view --minify` below prints
**only server URL**, not raw config, certificates, tokens or credentials.
Do not change endpoints, trust options, RBAC or kubeconfig. Prove a fresh
host Node GET passes before proceeding. If this independent route/console
cannot be established, no trial. API/host failure can still defeat recovery.

Create a fresh root-owned0700 evidence directory with umask077. Entire wrapper
must execute under the same UID owning its private files (sudo the whole
wrapper when using trusted root kubeconfig). Never rerun into used files.

```bash
bash node_proxy_readonly.sh /trusted/absolute/kubectl cilium-placeholder /private/p0 objects
```

`cilium-placeholder` is a syntactically valid unused argument for objects mode;
no Pod exec occurs. This fixes raw GET argv for NodeList, installed CRD,
daemonset, Cilium PodList, kube-system NodeConfigList, cilium-config, the three
production policies, raw Runner ServiceList, and the server URL. Each output
and stderr is bounded <=2MiB; exit code is saved, failures stop the collection.
Allow at most180s for the objects stage and fresh current baseline metadata.
Do not print private JSON to public comments/messages.

Use the retained hostname/UID from the approved storage/Node record. Run
`select` on the saved objects **before any agent exec**. It demands one exact
Node and DS-owned agent. Then use its actual Pod name:

```bash
bash node_proxy_readonly.sh /trusted/absolute/kubectl cilium-SELECTED /private/p0-agent agent
```

This fixes version, status, and five **running-daemon** `config get` queries:
EnableBPFTProxy, EnableIPv4, EnableIPv6, InstallIptRules, EnableEndpointRoutes.
Each scalar must be exactlytrue/false, with the file whitespace trimmed in
Docker. Extract exact version1.20.2 from `cilium-dbg version` output, retain
full raw witness privately. Do not substitute init config files for effective
daemon values. [config-get source](https://github.com/cilium/cilium/blob/v1.20.2/cilium-dbg/cmd/config_get.go)
reads running daemon configuration, normalizing field names.

The revised profile requires the official pinned Cilium image on both config
init and agent:
`quay.io/cilium/cilium:v1.20.2@sha256:2939231d0d3e3ebddcd80fffa168b7ddcc78fdf0dc864d1c8c126ff523c54f01`.
Config init is `cilium-dbg build-config`; agent is `cilium-agent` with only
`--config-dir=/tmp/cilium/config-map`. Init must contain **exactly one**
K8S_NODE_NAME spec.nodeName fieldRef with apiVersionv1, no literal value.
Missing/duplicate/literal/wrong fieldRefs refuse `select` and `preflight`.

Two source-backed argument forms are accepted. The original explicit source
order/dest args use the shared emptyDir at `/tmp/cilium/config-map`. The actual
stored P0 form has absent/empty args and mounts that emptyDir at `/tmp` in
both init and agent. The latter requires exactly one CILIUM_K8S_NAMESPACE
metadata.namespace fieldRef in config init, resolved to kube-system by the
fixed DS/Pod namespace. [build-config defaults](https://github.com/cilium/cilium/blob/v1.20.2/cilium-dbg/cmd/build-config.go)
set NodeName from K8S_NODE_NAME, Dest=/tmp/cilium/config-map and ordered sources
config-map:cilium-config then cilium-node-config:$CILIUM_K8S_NAMESPACE, with
empty allow/deny lists. [resolver](https://github.com/cilium/cilium/blob/v1.20.2/pkg/option/resolver/resolver.go)
resolves the ConfigMap namespace from that same env and merges later sources
over earlier keys, applying filters. [Hive env binding](https://github.com/cilium/cilium/blob/v1.20.2/vendor/github.com/cilium/hive/hive.go)
uses CILIUM_SOURCE/DEST/NODE_NAME/ALLOW_CONFIG_KEYS/DENY_CONFIG_KEYS; these
overrides and all envFrom are refused. The [release Dockerfile](https://github.com/cilium/cilium/blob/v1.20.2/images/cilium/Dockerfile)
sets HUBBLE_SERVER/INITSYSTEM, with a [scratch runtime final stage](https://github.com/cilium/cilium/blob/v1.20.2/images/runtime/Dockerfile);
neither defines those startup overrides. No image entrypoint shell is used.

The default form also allows only the observed exact auxiliary envs:
KUBERNETES_SERVICE_HOST=127.0.0.1/PORT=6443; agent-only
CILIUM_CLUSTERMESH_CONFIG=/var/lib/cilium/clustermesh/, GOMEMLIMIT from
limits.memory divisor1, KUBE_CLIENT_BACKOFF_BASE=1/DURATION=120. Node and
namespace refs remain exact and env names unique. Unknown CLI/env/filter/
shell, different image, or a shadowing config-directory mount is unsupported.
No live DS edit adds arguments or changes evidence.

Actual raw Pod containers differ from the DS only by the appended readonly
kube-api-access-* mount at /var/run/secrets/kubernetes.io/serviceaccount.
The comparison validates cilium SA, exactly one generated projection with
defaultMode420, token path token/expiration3607/no alternate audience,
kube-root-ca.crt ConfigMap mapping and namespace downwardAPI; original
non-token volumes and every other container field must still match. Only
comparison copies omit that one mount; raw objects stay unchanged. This is
the standard [serviceaccount admission implementation](https://github.com/kubernetes/kubernetes/blob/v1.34.1/plugin/pkg/admission/serviceaccount/admission.go),
not proof of this cluster's Kubernetes version. No token/Secret contents are
read. Any other admission delta refuses. The current cilium-config must also
keep clean-cilium-state/clean-cilium-bpf-state absent/false/empty. Agent
recreation re-executes existing mount/sysctl/CNI init containers; include
those Node effects in PM availability/recovery review.

The stored `/tmp/issue7-node-proxy-p0.bHOGJS/select.bundle.json` passes offline
select under this revision with raw bundle hash unchanged. This resolves a
source interpretation gap; it is not fresh/full P0 readiness. Agent version/
effective flags/status/endpoint and independent recovery-access evidence were
not collected after the original refusal and remain required. CRD v2 must be
served/Established, namespaced, with string defaults and object selector.
Current effective flag must befalse; an alreadytrue installation is not this
trial. IPv4/installiptablestrue and endpointRoutesfalse must match baseline.
IPv6 mode is recorded and preserved. Unknown image ID/version, kernel/boot/
machine metadata, matching CNC (including empty selector), multi-agent or
multi-node match, unhealthy Node, or baseline drift stop before P1/P2.

Refresh the existing API70 Ready1/updated1, mappingdisabled, Runner0/Pod0,
retained storage and expiry/window metadata using the reviewed existing
access preflight. Do not execute state probes or read original auth/native/PVC
contents. Feed the unchanged reviewed clean direct DNS render as `baseline`.
Collect selected API/CoreDNS endpoint witnesses using:

```bash
timeout --signal=TERM --kill-after=1s 12s /trusted/absolute/kubectl --request-timeout=8s \
  -n kube-system exec cilium-SELECTED -c cilium-agent -- cilium-dbg endpoint get SELECTED_NUMERIC_ID -o json
```

Use IDs1..65535 from each exact CEP, never arbitrary numbers/list output.
Collect Node workload inventory via API **metadata-only** projection of
namespace/name/UID/Node/phase/Ready, never Pod env/Secret contents. Inventory
is for PM availability scope, not a health-success boolean. Emit a P0 receipt
only when all checks pass. Review actual P0 before authorizing later stages.

The fixed inventory command (12s wall/8s API timeout, <=2MiB private output)
is:

```bash
timeout --signal=TERM --kill-after=1s 12s /trusted/absolute/kubectl --request-timeout=8s \
  get pods -A --field-selector=spec.nodeName=EXACT_RETAINED_NODE \
  -o 'go-template={{range .items}}{{.metadata.namespace}} {{.metadata.name}} {{.metadata.uid}} {{.spec.nodeName}} {{.status.phase}} {{range .status.conditions}}{{if eq .type "Ready"}}{{.status}}{{end}}{{end}}{{"\n"}}{{end}}'
```

## P1 — separately approved ephemeral kernel capability mutation

Kernel version alone does not prove helper support. The
[fixed Cilium socket branch](https://github.com/cilium/cilium/blob/v1.20.2/bpf/lib/proxy.h)
uses skc_lookup_tcp/sk_lookup_udp/sk_assign/sk_release in skb context. The
probe builds four tiny **BPF_PROG_TYPE_SCHED_CLS** programs for v4/v6 TCP/UDP,
including null handling, TCP state checks, assign and release. It issues only
BPF_PROG_LOAD and immediately closes each successful FD. No map creation,
attachment, pin, test-run, packet/socket send, listener, or live Cilium program
change occurs. The [Linux UAPI](https://github.com/torvalds/linux/blob/v6.6/include/uapi/linux/bpf.h)
defines the helper IDs/struct layout and load/close lifecycle. It remains a
privileged kernel mutation. Full Cilium program load/attach support is still
unknown and must be checked by Ready/health/policy gates after reload.

After P0 review and **explicit P1 approval**, PM executes the following on
the selected Node's independently reached Docker host. Bind Node machine ID
(hash only), bootID and kernel to the readonly host files before the first
load. No host Python and no new privileged cluster Pod:

Copy only the reviewed binding metadata to a fresh operator-owned0700 P1
directory as binding.json0600. Invoke the entire wrapper as that owner:

```bash
bash run_node_proxy_probe.sh /trusted/absolute/docker /REVIEWED_SOURCE/infra/hosting/m3 /private/p1 probe
```

The wrapper claims the directory once, creates a random fresh name/owner
label, uses Docker cidfile, restartno, init/networknone/read-only/lognone,
the fixed tooling image, BPF+NET_ADMIN only and relaxed seccomp. Existing
machine/boot/source/binding paths are readonly mounts. Creation10s+1s kill
is separate from `start --attach`15s+1s kill. Full ID/name/owner label/image
must match before start. It never relies on `--rm` or client exit to infer
process termination. [Docker create](https://docs.docker.com/reference/cli/docker/container/create/)
does not start the container, and [kill](https://docs.docker.com/reference/cli/docker/container/kill/)
targets the owned full ID during cleanup.

On completion, timeout, transport failure or handled TERM/INT/HUP, an EXIT
trap reconciles identity, sends exact-ID SIGKILL if Running, confirms stopped,
removes that same exited ID without force/prune, and requires successful
empty daemon list responses for full ID, exact name and owner label. Every
inspect/kill/remove/list has5s+1s kill bounds; allow100s for the complete
wrapper and cleanup. All argv/outcomes, identity, stdout/stderr and final
probe.lifecycle.json remain private. Never wrap the entire cleanup-capable
wrapper in the old15s timeout. A rejected ownership proof cannot kill/remove
that object. Unacknowledged create with no recovered ID is unknown/handoff,
even if current lists are empty (creation may still be in flight).

Missing final receipt, daemon failure, stillRunning, failed absence checks,
or an unhandled wrapper SIGKILL/host failure is unknown and prohibits P2.
The wrapper cannot guarantee termination when the daemon/host fails. PM must
handoff the saved name/nonce/CID/current outcomes through independent host
operator access; no broad cleanup or automatic retry. A normal completed
probe also undergoes exact cleanup/absence verification. Successful JSON
from a still-running/unknown container never grants NodeConfig creation.

For regression only, `selfcheck` mode runs a fixed TERM-ignoring shell/sleep
under UID10001/capdropALL/default seccomp with no BPF/machine/boot/source
mounts and a1s attach timeout. It uses the same identity/kill/remove/absence
path and always sets p2Eligiblefalse. It cannot be a capability receipt.

Declare both capabilities and relaxed seccomp in the P1 privilege decision.
Effective capabilities must be exactly BPF+NET_ADMIN. Old kernels/architectures,
permission errors, verifier disagreement, timeout or mismatch are **unknown**;
stop before NodeConfig/reload, do not retry or add SYS_ADMIN/privileged fallback.
No raw verifier log is printed. Confirmed container termination releases its
unpinned load FDs, including on failure. Docker backend tests only mock the
BPF syscall and use a capability-free real container substitute for timeout.

P1 success requires four loads closed with the exact generated program hashes
and selected Node/boot/kernel binding, **plus** probe.lifecycle.json modeprobe,
exact owned ID/name/nonce/tool image, childExit0/probeCompletedtrue,
containerAbsentVerifiedtrue/p2Eligibletrue. Assemble capability from the
guarded probe.stdout with its lifecycle object in Docker/jq; bare old helper
JSON, selfcheck or an unknown cleanup receipt is refused by `emit`.
Refresh P0 identity/settings/conflicts
within60s immediately before creation; repeat source comparisons, compare
binding with P1. A changed Node UID/boot invalidates the probe. A passed probe
does not grant P2 or guarantee networking availability.

## P2 — separately approved trial, cleanup and default rollback

PM must hold explicit selected-node/all-workload availability approval,
independent host access, and a reviewed600s operating/rollback budget. Backend
does not execute this stage. Save all request/response/status files0600 and
consume each mutation once. The offline CLI emits every mutation body; PM
must retain the fresh GETs and raw responses supporting its bundle.

1. `emit` verifies receipt+P1 capability, emits the sole CNC. Fresh same-name
   GET must report absent; matching CNC list must still equal P0. Execute:
   `timeout --signal=TERM --kill-after=1s 12s /trusted/absolute/kubectl --request-timeout=8s create -f /private/cnc.json -o json`.
   Save acknowledged created UID as `owned`. No adoption; uncertain creation
   without owned UID is stop/handoff, not permission to delete by name.
2. Fresh GET the created CNC and compare exact spec/UID. `delete-agent` with
   stageapply emits a request only for the initial DS-owned agent UID/RV on
   the retained Node and unchanged DS. Save request path+body. Run conditional
   raw delete **once**:

   ```bash
   timeout --signal=TERM --kill-after=1s 12s /trusted/absolute/kubectl --request-timeout=8s \
     delete --raw=/api/v1/namespaces/kube-system/pods/cilium-EXACT_INITIAL -f /private/apply-delete-options.json
   ```

   Use the path/body emitted by the guard, not a generic label delete or
   rollout restart. ResourceVersion/UID conflict stops; no mutation retry.
3. Using independent host API, identify one nonterminating replacement Pod
   with the unchanged DS UID/spec and Node. Exclude initial UID; save its
   fresh identity even if Pending/notReady. Bound old-Pod disappearance and
   new Ready to120s total from delete. `kubectl wait --for=condition=Ready`
   alone is insufficient; fresh GET after wait must confirm UID. If old UID
   remains or replacement identity is ambiguous at timeout, remove the owned
   CNC if safe, then stop/handoff rather than delete a guessed Pod.
4. Before Job creation, collect flags/status and selected API/CoreDNS
   realization on the **new agent**, plus fresh application/policy/config
   metadata. `health` flagtrue/excludedinitial must pass: running daemon
   flagtrue, remaining four flags unchanged, exact initial imageID, Ready,
   Cilium/Kubernetes statusOk, endpoint realization and application baseline.
   Preserve this identity+container signature for DNS. Wrong/ineffective flag
   or exec failure leads directly to default rollback, no DNS.
5. Prepare a fresh nonce and `dns-job`. It uses actual immutable Runner2948,
   original `app=access-web-runner`, retained DNS/node settings, UID10001,
   no auth/PVC mounts/SA token, only two existing holder metadata fieldRefs,
   backoff0/deadline50. No clone CNP. Check fresh raw ServiceList: exactly the
   original access-web-runner-v1 Service selects it; unknown Service selector
   or publishNotReadyAddressestrue fails. Prepare scope/programs before create
   where possible; create once, save Job UID and sole owned Pod UID.
6. Get exact Pod/CEP/new agent and one numeric native endpoint response.
   `dns-scope` requires unchanged fresh agent container signature; Pod/image/
   holder owner matching; production policy UID/spec fingerprints; both spec
   and realized six original rows; defaultdenyboth; exact two DNS names;
   matching positive policy revision and proxy ACK; exactly two egress DNS53
   rows with one allocated port. Store original production witness `reference`
   from the earlier reviewed original-label endpoint; no event stream/list.
   A fresh Ready Pod without these rows/ACK does not permit DNS.
7. Use `access_dns_contrast_exec.py program --phase node --nonce ...` with
   job,pod,holderreceipt,scope and the unchanged direct baseline. This reuses
   exact PID1/UID/start/nonce checks and the atomic `/dev/shm` single-use claim.
   First send must occur before28s from holder start, batch finishes before38s
   (helper alarm10s), host wrapper12s+1s kill. Execute `run_dns_contrast_exec.sh`
   once under the trusted operator UID/root-owned private directory. Only the
   four fixed absolute A UDP/TCP queries run: chatgpt.com and
   api.m2-hosting.svc.cluster.local,2s each, libc0/HTTP0/auth0/native0.
   Transport failure/partial output consumes the attempt; no DNS retries or
   fresh holder rerun under this approval. Even four normal replies default
   to rollback. Result must carry the exact fresh context and be rechecked
   against post-exec metadata before classifying it. Timeout is observed
   failure; transport/owner mismatch is unknown, not DNS success.
8. Exact Job cleanup using `access_dns_contrast_prepare.py delete` (node Job
   is whitelisted), saved created Job UID/current RV and conditional raw
   DELETE. Confirm owned Pod gone, Job absent, Runner0/Pod0, Service endpoints
   returned to baseline. If cleanup API fails, record owned UID and handoff;
   continue safe NodeConfig removal/restoration when API still works, without
   relaunching queries. DNS success/failure/timeout all follow this cleanup.
9. **Default rollback**, even on success: fresh owned CNC GET then
   `delete-config`, conditional raw DELETE at emitted exact CNC path with
   UID/RV+exact spec. Confirm owned config absent and CNC list equals P0.
   Using independent API, obtain current replacement Pod UID, DS owner/Node,
   unchanged template; do **not** require agent exec or Ready. Pass fresh
   replacement identity to `delete-agent` stagerollback; conditional delete
   once at emitted path with its **new** UID/RV, never initial UID. This is
   the second and last planned agent replacement. If apply delete definitely
   never happened and original agent is still healthy, only remove owned CNC
   and verifyfalse/baseline; do not invent a second target. Ambiguity is handoff.
10. Within120s from rollback delete, identify one third agent UID (exclude
    both initial and trial agent), wait Ready, fresh GET, running daemon
    flagfalse, other flags unchanged, initial imageID, statusOk, exact current
    API/CoreDNS realization on the new agent and original Pod/CEP attachments,
    unchanged DS/CM/all three policies/CNC baseline/API70 mappingdisabled,
    Runner0/Pod0/Job0/Service baseline. `health` flagfalse with both excluded
    UIDs must pass. Object deletion alone is not restored. No new DNS batch
    is needed or authorized during restore.

## Stop/handoff and evidence disposition

The trusted host API remains available when agent exec/Ready fails, enabling
owned-CNC removal and exact replacement identification. If that API is down,
Node UID/boot/DS changed, current Pod UID is ambiguous/replaced, conditional
delete conflicts, or rollback Ready/health fails, stop automatic operations.
Do not force delete, restart again, expand capability/policy, cordon/drain,
change kubeconfig, or restart unrelated workloads. PM's previously established
host SSH/console operator must take recovery ownership with saved current
object UIDs, requests/outcomes, last effective config/status and remaining
deadline. This package cannot guarantee automatic recovery when the host/API
itself fails. A third restart or persistent true requires new disposition.

Submit only safe derived summaries to PM/review: exact source SHA, P0
pass/refusal reasons, P1 load-only context result, owned object IDs privately,
two replacement receipts, initial/trial/restored flag+health gates, DNS outcome
or unknown, cleanup and application baseline. Keep raw metadata private.
Document timeouts/unfinished cleanup as such, not as restored. Actual evidence
must be independently reviewed. A successful DNS trial permits only a later
proposal for persistent mode change/deployment; default rollback remains.

All existing original auth/state/PVC, access window, API enable, Native/turn,
final PR/public posting holds remain. DN12 stays paused. This package does
not establish source-auth readiness or issue #7 completion.
