# Issue #7: one anonymous numeric callback/TLS test — preparation only

The two redirect modes produced four DNS timeouts, with independently verified
default rollback. This candidate tests the existing callback and provider
transport without Runner DNS. No NodeConfig, BPF, Cilium restart, DNS L7
removal, production Runner update, auth/Native call or frontend change occurs.
Preparation is accepted; **live collection, temporary policy and provider
traffic require this exact source/procedure review and PM disposition**.

Runtime remains source91577194a5a9343778b68fcaa258d5941f92aa5a and Runner image
`docker.io/library/anxious-s13-access-runner-9157719@sha256:2948da59599ba7842406d3ce49fccf716f47010fb713a43721c95eeb3f08d2e7`.
The worker overrides its entrypoint and mounts no state/credentials/SA token.
Only existing two holder metadata fieldRefs are supplied. UID10001/capdropALL/
read-only/no host network/PID/shared process namespace are unchanged.

## Exact change and result limits

One Job and one CNP share fixed name `access-runner-numeric-tls-v1` in
codex-trial. The Job uses original app=access-web-runner plus one fresh
128-bit lowercase hex `issue7-network-check` nonce. A complete fresh PodList
must show that app+nonce selects precisely the owned diagnostic Pod, no other
Pod. Pre-existing fixed names/nonce collisions stop; create never adopts.

After its address lease is validated, the sole CNP is:

```yaml
apiVersion: cilium.io/v2
kind: CiliumNetworkPolicy
metadata:
  namespace: codex-trial
  name: access-runner-numeric-tls-v1
spec:
  endpointSelector:
    matchLabels:
      app: access-web-runner
      issue7-network-check: <nonce>
  enableDefaultDeny: {ingress: false, egress: false}
  egress:
    - toCIDR: [<selected public IPv4>/32]
      toPorts:
        - ports: [{port: "443", protocol: TCP}]
```

This is an additive, temporary egress expansion for one diagnostic endpoint.
Original production CNP/P1/CCNP, DNS L7 names/toFQDNs/default-deny/host-deny
stay exact. The /32 can be a shared CDN address: it is **not CNP domain
enforcement**. Immutable worker uses normal CA/CERT_REQUIRED/hostname check
and SNI chatgpt.com; no other hostname, address, port, HTTP request or client
credential is supported. Existing source/policy ownership is unchanged.

The original-label holder becomes a no-server endpoint of existing Runner
Service/access-web-runner-v1 until cleanup. Fresh raw ServiceList must prove
only that Service selects it, with exact app selector and no publish-not-ready
or UID/name selector ambiguity. Runner replicas0/API mappingdisabled remain;
callback traffic selecting this temporary endpoint can still fail. Include
that effect in the operating decision. It does not listen on8080.

Callback TCP pass is not callback HTTP/app/auth success. Provider verified TLS
pass is not provider HTTP/auth, stock Codex connectivity or a web turn. Both
passing supports only a later reviewed address-bound bootstrap and pinned
native resolver compatibility test. Address leasing is not yet production
architecture. Source/auth/state/window/API-enable/Native holds remain.

## Clock and lifetime contract

Source/manifests/program templates and baseline metadata are prepared **before
the short DNS lease**. Fresh P0 stable-policy-v2/false full health is required
before arming the operation; preserve raw sources and current API/Coredns
bindings. The initial supported collector shape has exactly one ready API
endpoint and one ready CoreDNS endpoint on the retained Node. Multiple or
different endpoints are unsupported, not a reason to select arbitrary ones.

Arm host budget after these preparations, before the holder create. Whole
operation120s includes45s cleanup reserve; no collection/new create/worker may
start after75s. `run_numeric_step.sh arm/budget/collector/worker` makes that
clock gate executable using host BOOTTIME uptime. Each directory is claimed
once, root-owned0700, private source/outcome files0600 and exclusive. Existing
claim/results or transport ambiguity consumes the attempt. Do not extend the
start timestamp, choose another address, relaunch a holder or refresh DNS.

Holder Job deadline30s/backoff0; PID1 ends at28s from its own kernel start.
First worker execution must start before18s. Batch alarm10s; each callback,
provider TCP and TLS stage has3s socket timeout. Remote effective deadline is
the minimum of PID1+28s, actual Job start+30s and DNS lease expiry. Before
callback require10s remaining, before provider TCP6s, before TLS3s. Context
clock is BOOTTIME in the same time namespace and Node boot. Realtime is only
used to map the Job start timestamp; a >250ms realtime/BOOTTIME discrepancy
refuses. No wall-clock-only TTL admission.

Lease uses minimum TTL across every required CNAME and final A record,
starting conservatively from query-begin BOOTTIME, capped at60s. Initial
selection and policy creation require15s remaining; final scope/program10s.
Collector/tool/holder boot/time namespace must match; an off-node Docker
tooling host refuses. Preparation must not burn TTL resolving early.

Checks cannot atomically prevent a process being descheduled between checking
and a syscall. Late/partial/transport results are unknown; never report success
after a deadline. TTL expiry **does not remove CNP**. Cleanup/handoff below is
mandatory even when no connection was sent or worker was terminated.

## Fixed collector — no resolve before operating review

Collector location is the existing selected Pod of Deployment/api in
m2-hosting, containerapi, same retained Node. No Pod or env is added there;
no original process, auth, state or credential file is read. Obtain exact
Service/api and CoreDNS Service/kube-dns, their EndpointSlices and selected
Pod→ReplicaSet→Deployment owner chains. API selector/endpoint/IP8080 and
CoreDNS selector/endpoint/IP53 must match, all ready, no terminating entries,
no unbound second endpoint. Save raw GETs; collector binding includes Service
UID/RV/IPs and selected API UID/containerID/restartCount/imageID/state.

`access_numeric_prepare.py binding --bundle ...` checks the full baseline and
these raw objects. Generate static collector program from that exact binding
using actioncollector; it rechecks the binding against collectorBaseline.
Only after holder/pre-collection ownership/reference metadata is ready,
invoke this fixed argv under the trusted root operator:

```bash
bash run_numeric_step.sh collector /trusted/absolute/kubectl api-EXACT_POD /private/collector.py /private/run
```

The wrapper executes exactly:
`kubectl --request-timeout=8s -n m2-hosting exec api-EXACT_POD -c api -- python -B -c <reviewed program>`
with5s wall+1s kill. Program alarm3s, one numeric AF_INET/TCP connection to
captured CoreDNS Service IP53, one framed DNS query for chatgpt.com/A/IN/RD,
no EDNS/search/libc/public resolver/fallback/retry. Socket closes on all exits.
Response≤4096bytes and echoed ID/question, QR/opcode/TC/rcode/count/bounds/
compression are validated. Answer section must form one unambiguous acyclic
chatgpt.com CNAME chain ending in1..8 A records, all public unicast IPv4.
TTL0, private/reserved/loopback/link-local/multicast, unrelated answers,
unsupported answer type/class, loops/partial/trailing data refuse. Select the
numerically smallest address once, even with multiple answers.

Raw DNS response base64/ID, peer, binding, begin/end clock, realtime, boot/time
namespace remain **root-private evidence**, never public output or a packet
stream. Post-exec fresh API container/UID and service/owner bindings must
recompute identically; bindingAfter==binding is required before lease/CNP.
Transport failure, substituted target/UID or late response stops without
another query. Unknown provenance/TTL is a refusal, not a fabricated lease.

## Emit/check interface — Docker only

Use reviewed API tooling imagee1cd49c with networknone/capdropALL/read-only,
source mounted at/checks, private metadata at/evidence, PYTHONPATH=/checks:/app.
Run `python -B /checks/access_numeric_prepare.py ACTION --bundle ...` **inside
Docker**, never host Python. Source must be the exact reviewed checkout.
CLI lease/policy/scope/program verifies actual tool boot/time namespace and
current BOOTTIME against the collector. Operator cannot pass an arbitrary
`now` through the CLI; old/different namespace lease refuses.

| Action | Bundle and result |
| --- | --- |
| holder | Existing fresh full false-health bundle+receipt/snapshot/inputs/images/services/pods/nonce → fixed Job; no DNS lease yet |
| binding | Above plus callbackService/callbackSlices/apiPod/apiReplicaSet/dnsService/dnsSlices/dnsPod/dnsReplicaSet/dnsDeployment → immutable collector binding |
| precollect | Original-policy holder/job/pod/pods/cep/agent/reference/endpoint/receipt/signature/production/globalPolicy/policies/services → fresh original scope; no lease/CNP yet |
| collector | collectorBaseline,precollection,nonce,binding → fixed source string, checks both bindings and original scope; no command executes |
| lease | collectorBaseline,nonce,binding,bindingAfter,capture → validated one-address lease |
| policy | Same collection proof plus lease → sole /32 CNP |
| scope | Same plus job/pod/pods/cep/agent/agentSignature/reference/endpoint/production/globalPolicy/policies/services/temporary/ownedPolicy/receipt → current native scope |
| program | scope bundle plus holderReceipt → exact trusted remote worker source |
| result | Saved scope/lease plus current postexec raw objects/result/transportExit → safe outcomes; never web success |
| delete | current,owned{uid,specHash} → conditional DeleteOptions; only fixed Job/CNP names |

`selected_exact` factors the existing owner/image/spec validator; actual object
names/namespaces are not rewritten to masquerade as a different diagnostic.
Before supplemental policy, retain the current original-label native reference
and owner/pin/image/service proof. Scope later validates singleton exact
Job/Pod/CEP/agent, unchanged agent signature/production policy fingerprints,
temporary owned UID/spec/nonce and matching desired/realized snapshot. The
sole provider443 filter must include precisely the extra CNP UID reference
and exact /32 selector; removing those in comparison copies must reproduce
all original six L4 rows, including host deny and exact DNS rules. Extra
allow/deny/filter/ref/subnet/selector or missing original rule refuses. DNS
rows/proxy ACK/positive same allocated port are preserved despite worker DNS0.
Unknown native serialization is unsupported before sends. The CIDR selector
is based on fixed [CIDR selectors](https://github.com/cilium/cilium/blob/v1.20.2/pkg/policy/api/cidr.go)
and [IPv4 label conversion](https://github.com/cilium/cilium/blob/v1.20.2/pkg/labels/cidr.go).

## Ordered operation and exact cleanup

1. Before arming: complete reviewed fresh false/v2 P0, capture exact sources,
   static programs/templates and API/CoreDNS bindings. Fixed Job/CNP names
   must GETabsent; reject nonce collision. No resolve yet.
2. Arm once: `bash run_numeric_step.sh arm /trusted/absolute/kubectl - - /private/run`.
   Before each create call modebudget with the same unused placeholders.
3. Create Job once, save acknowledged UID/full defaulted specHash. Find only
   owned Running/Ready Pod; source command/image/nonce/pin/reference/Service
   must pass before collection. No DNS/connect is performed by PID1. If
   insufficient readiness time, cleanup with no collector/worker invocation.
4. Single collector call, recheck API/service bindings after, parse/lease in
   Docker. Only now emit/create CNP once; save acknowledged UID/specHash.
   Short TTL/unknown outcome stops; no alternative address/query/holder.
5. Fresh raw temporary CNP, PodList singleton, exact Pod/CEP/agent and numeric
   native endpoint response → scope. Emit trusted program0600; preserve the
   raw same-instance proof. Clock/ownership/policy/callback binding must pass.
6. Worker once:
   `bash run_numeric_step.sh worker /trusted/absolute/kubectl access-runner-numeric-tls-v1-EXACT /private/worker.py /private/run`.
   Wrapper12s+1kill; remote own PID1/UID/start/command hash/nonce are checked,
   atomic private /dev/shm claim consumed, numeric sockets only. Callback
   failure skips provider entirely. Provider uses normal TLS with fixed SNI,
   certificate/hostname validation; verification failure and deadline have
   no retry. No HTTP bytes/auth/Native/state access. Close both sockets.
7. Saved result and fresh postexec owner/pin/agent signature/temp policy and
   production fingerprints → result checker. Deadline or transport ambiguity
   is unknown and consumes the attempt, never replay. First TCP pass alone is
   not API/app success; TLS pass not provider/web success.
8. Cleanup on every outcome: fresh exact Job GET plus owned UID/specHash,
   emitted conditional UID/RV DeleteOptions → raw DELETE once. Bound owner
   Pod disappearance; confirm all matching app+nonce Pods0, Runner0 and
   Service endpoints returned to empty baseline. Then fresh owned CNP GET
   plus UID/specHash → conditional raw DELETE once; confirm absent. No label
   mass-delete, adoption, force/prune or new restart. Existing production/
   API/Service/config and false full health must still pass.

Each GET/delete uses8s API/12s wall+1kill and private bounded output. Main
stage ends no later than75s from arm; reserve45s for cleanup/baseline, whole
120s. If API/transport/ownership or cleanup deadline fails, stop and handoff
saved nonce/UID/specHash/request/outcome and remaining Job/CNP explicitly to
PM's trusted host API operator. Do not claim restoration because TTL expired
or Job exited. Unacknowledged create cannot be deleted by guessed name.

## Validation and next gate

Tests must include malformed/stale DNS chain/TTL/clock, target/nonce/UID
substitution, foreign cleanup, singleton/native rule failures, callback
failure→provider0, TLS/clock failure and no replay. Actual Runner2948
networknone substitute checks ownPID1/atomic claim, numeric calls, socket
closure and real default CA configuration; simulated TLS is not live success.
No live collector/provider/policy result is claimed by these tests.

After both anonymous routes pass and exact cleanup is independently verified,
propose a separate minimal address-bound web bootstrap with stock-client
compatibility/expiry/owner/session checks. This package does not authorize
that step or permanent IP pinning. DN12 remains paused and all previous
credential/state/native/API/final PR/public posting holds stay in force.
