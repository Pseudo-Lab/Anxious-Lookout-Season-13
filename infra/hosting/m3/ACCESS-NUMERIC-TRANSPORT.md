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
operation120s includes45s cleanup reserve; main operations must **finish** by
75s, not merely start before it. `run_numeric_step.sh` admission reserves the
maximum active timeout plus kill allowance before a claim/call, and rechecks
after reading the source immediately before the claim. Reservations are:
collector6s; create/read13s; bounded prepare/result6s; worker45s (13s execution
plus32s postexec ownership read13s, numeric native read13s and result check6s).
Thus elapsed74s refuses collector, worker and every new preparation/read/
create/result call. Modebudget aliases budget-create; budget-read,
budget-prepare and budget-result cover the other stages. Call each gate before
its corresponding command and keep that command within its reserved bound.
The host BOOTTIME uptime gate leaves the45s cleanup reserve intact; inability
to complete full cleanup/false health within it is unknown/handoff, not restored.
Each directory is claimed
once, root-owned0700, private source/outcome files0600 and exclusive. Existing
claim/results or transport ambiguity consumes the attempt. Do not extend the
start timestamp, choose another address, relaunch a holder or refresh DNS.

Holder Job deadline30s/backoff0; PID1 ends at28s from its own kernel start.
First worker execution and actual callback connect must start before18s;
the cutoff and10s remaining budget are rechecked after socket preparation,
immediately before connect. Batch alarm10s; each callback,
provider TCP and TLS stage has3s socket timeout. Remote effective deadline is
the minimum of PID1+28s, actual Job start+30s and DNS lease expiry. Before
callback require10s remaining, before provider TCP6s, before TLS3s. The last
TLS admission occurs **after** CA context creation and flag validation,
immediately before wrap_socket/ClientHello, checking ownership/clock again.
An8s context delay leaving2s causes handshake0 and normal socket closure.
Context
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
   Before each create call modebudget-create with the same unused placeholders
   (13s reservation); before bounded source/check commands budget-prepare,
   before reads budget-read and before outcome validation budget-result.
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
6. Save the dispatch-stage deadline once as described below, then worker once:
   `bash run_numeric_step.sh worker /trusted/absolute/kubectl access-runner-numeric-tls-v1-EXACT /private/worker.py /private/run`.
   The wrapper checks the saved1s dispatch deadline before claim and again
   immediately before its sole timeout/kubectl invocation, recording historical
   host admission. It then waits synchronously for the entire batch under
   12s+1kill; do not wrap that batch in1s or apply the postbatch clock to the
   dispatch deadline. Missing/expired dispatch input refuses before invocation.
   Remote own PID1/UID/start/command hash/nonce are checked,
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

Each ordinary GET/delete uses8s API/12s wall+1kill and private bounded output.
The short pre-worker critical-path groups below use tighter outer bounds;
those override longer per-command defaults and never omit raw binding checks.
Main
stage ends no later than75s from arm; reserve45s for cleanup/baseline, whole
120s. If API/transport/ownership or cleanup deadline fails, stop and handoff
saved nonce/UID/specHash/request/outcome and remaining Job/CNP explicitly to
PM's trusted host API operator. Do not claim restoration because TTL expired
or Job exited. Unacknowledged create cannot be deleted by guessed name.

## Validation and next gate

### Fixed sequential holder critical path

Before arm/holder start: build/cache images; validate fresh full baseline and
API/CoreDNS owner/service/slice sources; prepare static holder/collector source,
raw-reading argv and policy/program templates; identify the original native
reference and prepare source imports. No provider address is resolved early.
Dynamic raw inputs and validation are not precomputed or skipped.

After actual holder PID1 start, the dependency chain is fixed:

| Group | Outer bound including kill | Required work / stop behavior |
| --- | --- | --- |
| original |3s| Independent raw owner/PodList/Service/CEP/agent reads, then CEP-derived numeric endpoint GET and original-scope check; late→no collector |
| collector |6s| One fixed5s+1kill API exec, no retry; late→no later collection/create/provider |
| binding |2s| Parallel current API/service/owner bindings, recompute exact equality; late→no CNP |
| lease |1s| Parse complete raw A/CNAME response, TTL/clock and render exact /32; late→no CNP |
| create |2s| One conditional new CNP create, save acknowledged identity; uncertain/late→cleanup/handoff |
| realization |2s| Independent fresh raw Pod/CEP/agent/policy reads, then CEP-derived numeric endpoint GET and exact scope; late→no worker |
| emit |1s| Recheck full scope and emit trusted worker bytes; late→no worker |
| dispatch |1s admission only| Wrapper records/checks host admission before sole transport invocation; batch completion is handled separately below, remote actual first-connect guard remains authoritative |

`access_numeric_timing.py --stage GROUP --holder /evidence/holder.json` in
Docker returns one absolute stageDeadlineNs and its conservative integer
stageDeadlineCs. Preserve it once. For original through emit, bound each
group to remainingMs, including its kill allowance; only independent reads
may run concurrently and each complete raw response must be preserved.
CEP lookup must precede GET of its returned numeric endpoint ID on the exact
bound agent; both dependent calls and subsequent bundle checks count within
the same saved original/realization deadline. Imports,
tool startup, JSON assembly and validation count toward both the absolute18s
holder cutoff and their group, never run outside a saved deadline. After the
group (original through emit), call the same command with
`--completed /evidence/saved-stage.json`;
missing/late/completion refusal enters cleanup **before** the next create or
send. Never regenerate a deadline or widen a timeout to make a slow path fit.
The host75s reservations apply in addition to these18s checks.

Dispatch has explicit last-stage handling. Run the timing admission command
inside the same reviewed Docker tooling environment, with holder receipt at
/evidence/holder.json and metadata mount writable only for private output:

```bash
# Run INSIDE Docker, save stdout once to /evidence/dispatch.stage.json:
python -B /checks/access_numeric_timing.py --stage dispatch --holder /evidence/holder.json
```

On the trusted operator host, umask077 and noclobber remain required. Extract
the saved value once, then invoke the existing synchronous wrapper:

```bash
umask 077
set -o noclobber
jq -er '.stageDeadlineCs | select(type == "number" and . == floor)' /private/run/dispatch.stage.json > /private/run/dispatch.deadlineCs
bash run_numeric_step.sh worker /trusted/absolute/kubectl access-runner-numeric-tls-v1-EXACT /private/worker.py /private/run
```

The bootstrap/import/JSON/extraction/wrapper startup must fit the saved1s
dispatch window. Wrapper checks conservative host BOOTTIME centiseconds,
requires a private0600 regular deadline file, checks main45s reservation,
and refuses an expired or more-than1s-remaining deadline before its claim.
It rechecks inside the child and writes worker.dispatch.json just before
exec of timeout. This is **host admission evidence**, not a remote launch
acknowledgement. Descheduling after the check cannot authorize late remote
traffic: the worker rechecks actual first connect against PID1+18s and the
10s/end/lease guards. A missing receipt or transport failure is unknown,
consumes the one attempt and goes to cleanup; no retry.

Wait synchronously for the bounded batch, preserving worker.stdout/stderr/
exit, without a1s enclosing timeout. Afterward perform the reserved fresh
owner read13s and dependent numeric endpoint read13s. Validate dispatch
receipt and result together within the single remaining6s validation bound
(including both tooling startups), not an extra budget. Run INSIDE Docker:

```bash
python -B /checks/access_numeric_timing.py --stage dispatch --holder /evidence/holder.json --completed /evidence/dispatch.stage.json --dispatched /evidence/worker.dispatch.json
```

This validates the recorded admission timestamp against the original saved
deadline; it deliberately does not compare batch finish to that1s deadline.
Generic dispatch completion without --dispatched refuses. All
fresh postexec owner/native/receipt/result checks share the reserved32s, including
remote entered/finishedNs and effective batch/end/lease limits. The wrapper
12s+1kill, remote10s batch, host75s main/45s cleanup and whole120s limits are
unchanged. A legitimate2s batch can complete after its dispatch deadline;
that does not weaken any remote or result deadline.

The sum of listed maxima is18s, so spending every maximum cannot pass the
strict first-send cutoff; this is intentional refusal, not guaranteed
completion. Prepare static work earlier and require observed stages to be
faster; if startup/import/cluster realization cannot fit, the attempt sends
no worker traffic and closes without another holder/address/mode trial.
There is no assumption that a single-node cluster meets these bounds.

Offline `test_numeric_timing.py` runs the actual holder/owner/parser/binding/
native scope/program/conditional deletion helpers with scripted I/O delays.
Observed normal trace: original1.8s, collector2.8s, binding3.6s, lease4.2s,
create4.5s, realization5.3s, emit5.8s, dispatch6.1s since holder start. Actual
helper CPU in the first author run was6.053ms. These include scripted I/O
and tooling costs; they are **not measured live latency**.14s delay injected
at each pre-worker stage refuses before the next step; pre-create failure
keeps CNP0/provider0, post-create failure deletes only its exact owned CNP;
Job cleanup occurs in every case. No raw binding check is removed. The
separate same-command admission fixtures refuse at elapsed74s before any
local claim/kubectl call, and delayed CA context never starts TLS. An actual
wrapper fixture adapted from reviewer test_numeric_dispatch_gap.py runs a
2s fake kubectl: batch succeeds, generic1s completion rejects as expected,
and the historical dispatch receipt validates. Expired saved dispatch makes
no kubectl call/claim; replay makes no second call. Socket preparation delayed
to the actual18s cutoff makes callback/provider0 and closes the socket.

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
