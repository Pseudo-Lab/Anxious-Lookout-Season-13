# Issue #7: actor-local timing alternative — design only

A bounded alternative is feasible under trusted local elapsed-timer semantics:
give each actor its own deadlines and use causally earlier local anchors for
coordinator limits. Administrative executor/daemon mapping is not a universal
prerequisite. This replaces a safety argument; it is not permission to delete
the current namespace guard. No executable change or trial is proposed here.

Current code mixes controller/holder clocks in original()/stage(), sends
controller deadlineCs to the host, and ages the collector's lease using
self.now(). That explains why host_clock(), holder and lease identity checks
are safety predicates today. Merely making those fields observational leaves
unsafe arithmetic in place. All those paths, dispatch receipt validation and
cleanup recovery must change together in any reviewed implementation.

## Owners and ordering

| Actor | Authoritative local limits |
| --- | --- |
| Host broker H | Own immutable arm epoch: main75s, cleanup/full operation120s; fixed stage/command caps and owned-resource journal |
| Docker controller C | Own epoch and local processing caps; never interprets H/A/W absolute timestamps using its own now |
| API DNS collector A | Query begin/end and raw TTL lease in its own clock domain; bound to the selected API UID/container/restart/Node |
| Holder/worker W | Own PID1 start, first connect<18s, batch10s, PID1 lifetime28s, Job30s and DNS expiry; final socket/TLS admission |

Keep the existing exact collector/holder boot and time-namespace match before
using A timestamps in W. The workload clocks are not bridged. If those views
differ, refuse even with matching offsets, small skew or the same Node UID.
Keep Node/Pod/container/CEP/agent/image/policy bindings and boot checks.

H begins the original phase before acknowledging it; C records its local
original anchor after that acknowledgement and before asking H to create the
holder. The verified holder exists before any collector request. Thus:

```text
H original anchor -> C original anchor -> Job create -> W PID1 start -> A DNS query
```

Both H and C use their own anchor+18s as conservative critical cutoffs;
W retains its exact PID1+18s check. Each phase starts once on H before its ACK,
then C admits its own fixed stage budget. H's cap therefore starts earlier.
Fixed bounds3/6/2/1/2/2/1/1s and all handshake/transport costs still count;
insufficient time refuses instead of extending18s.

Wire messages carry attempt/phase/sequence and fixed duration-cap requests,
not foreign absolute deadlines. H clamps to its existing local main/stage/
cleanup deadlines. A phase ACK is not an authorization after its local expiry;
queued/replayed commands cannot start a new phase or reset its clock.

## Lease freshness without cross-domain subtraction

Parse one fresh collector response structurally without C's now: exact nonce/
request binding, API instance/resolver binding, DNS query ID/peer, raw packet,
begin<=end, query duration<=3s and minimum CNAME/A TTL capped60s. No DNS before
verified holder, cache relabelling, second address or refresh. In workload
domain D, the lease remains E_D = queryBegin_D + TTL; W uses this unchanged.

For X=H or C, define the conservative coordinator expiry locally:

```text
E_X = originalAnchor_X + TTL - epsilon_X
remainingLower_X = E_X - now_X
```

The anchor precedes query-begin causally; no A-minus-C or W-minus-H calculation
occurs. epsilon_X covers timer precision and a justified elapsed-time error
bound, never one inferred from sampled skew. Require15s before initial/policy
admission and10s for final scope/program, with command/kill/postcheck reserves
also fitting. W independently requires10/6/3s immediately before callback,
provider TCP and TLS, including the check after CA-context creation.

Example with reliable clocks and arbitrary constant offsets: H anchor10,
C anchor1000 and query begin3002 in D. At the same later event H elapsed5s,
C elapsed<5s and D now3004; TTL30s gives coordinator remaining<=25s and
workload remaining28s. Foreign absolute values are incomparable; the earlier
local anchor yields a stricter duration bound. Large constant offsets do not
authorize anything and do not lengthen the lease.

Preserve Job30s mapping inside D using the collector's realtime/BOOTTIME pair,
and the worker's existing250ms realtime-versus-BOOTTIME consistency checks.
C can compare validated D-labelled records to each other, but not to C's clock.
Host dispatch receipts are checked only in H's phase/epoch; actual18s admission
is proved by W. Never call completed(holderStart_W, deadline_H, admitted_H).

## Cancellation, recovery and changing predicates

H remains final authority for business admission and local bounded child
termination, including kill grace. C cannot spend the45s cleanup reserve or
reset H's120s epoch. A replacement controller receives cleanup-only rights
under the original H session and remaining budget; new C clock starts cannot
renew the operation. Loss of the original H epoch means unknown/handoff,
not reconstruction from another domain or business retry.

Retain UID/RV/spec conditional deletion, one persisted delete-attempt per
owned Job/CNP and independent false-health restoration. Unknown create is
never adopted by name. TTL expiry does not remove CNP; cleanup is mandatory.
Insufficient cleanup time or uncertain deletion is unknown, not restored.

The proposed safety-role change is specific: H/C/workload namespace equality
would cease to authorize foreign absolute-clock arithmetic, because that
arithmetic is removed. Cross-tool namespace comparisons become observations;
actor-local epoch stability, workload A/W equality, boot/identity checks,
causal ordering and deadline admission remain predicates. Current code keeps
all existing guards until this replacement is concretely reviewed.
The host/controller250ms absolute-skew gate also needs an explicit replacement:
it assumes the old shared domain. Fresh phase ACKs and local expiration would
replace that timing argument, not a normalized offset or sampled-skew bridge.
The workload's existing250ms realtime/BOOTTIME consistency predicate remains.

## Assumptions and falsifiable refusals

Local timers must reliably bound elapsed seconds, including suspend, within a
stated error budget. Arbitrary slow/frozen/emulated clocks are outside this
contract; zero offsets or a small RTT sample do not establish it. A proposal
must name the trusted timer backend and justify epsilon, not assume an unknown
rate bound. This is the basic clock trust any TTL/deadline mechanism needs,
not administrative Node placement. Existing trusted code/images/kernel clock
contracts are relevant; a JSON label or another metadata collector is not.

| Counterexample | Required result |
| --- | --- |
| H/C offsets differ by hours, elapsed timers valid | Local conservative bounds only; never cross-subtract |
| Collector/W namespace mismatch despite zero offsets | Refuse lease/network; retain workload identity guard |
| Collector starts before owned-holder acknowledgement | Reject causal anchor; no policy/worker |
| Delayed capture consumes local TTL or18s window | Refuse; no new DNS/address/holder |
| API/holder restart, boot/epoch change or backwards clock | Invalidate binding/ticket; no elapsed reset |
| Old response/phase/nonce or repeated deletion journal | Reject replay; no repeated action |
| Unbounded clock slowdown/error budget unsupported | No certified lease/deadline acceptance; hold the design |
| TLS preparation leaves<3s or local skew exceeds250ms | No TLS handshake admission |
| C stalls/restarts at74s or H phase expires | H refuses business call; original cleanup reserve only |
| H dies or cleanup exceeds120s | Unknown/handoff; no claim of restoration |

As today, userspace checks cannot atomically prevent descheduling between a
last check and syscall. Late/partial/timeout results stay unknown; do not claim
zero late packets from a pre-call check alone. These are proposed proof/tests,
not dynamic passes or authority to execute them now.

## What advances the working web test

This removes the demonstrated unrelated host/controller namespace prerequisite
through a new timing composition, while retaining actual workload transport/
lease/ownership checks. It adds no runtime switch, privileged namespace entry
or new administrative collector. Short TTL/slow stages may still refuse.

The exact remaining runtime fact is whether the selected API collector and
owned holder expose the same stable workload clock domain. Existing approved
Node/Pod/container bindings and holder/collector self receipts can answer that
inside a separately authorized attempt; current evidence does not prove it.
If they differ, this design does not bridge them. Reliable local timer support
is likewise indispensable if the platform cannot provide the basic contract.

Next is design/proof review, then only if accepted implementation and synthetic
offset/delay/restart/replay tests, exact-source review and a new explicit numeric
disposition. Callback TCP/provider TLS plus verified cleanup supports a later
reviewed address-bound native/bootstrap step; it is not HTTP/auth/web success.
No API-enable, original credentials/state/PVC content, Native/turn or final
PR/publication is authorized by this analysis.
