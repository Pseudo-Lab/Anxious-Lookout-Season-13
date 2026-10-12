# Issue #7: actor-local timing alternative — design only

A bounded alternative is feasible under trusted local elapsed-timer semantics:
give each actor its own deadlines and use causally earlier local anchors for
coordinator limits. Administrative executor/daemon mapping is not a universal
prerequisite. This replaces a safety argument; it is not permission to delete
the current namespace guard. No executable change or trial is proposed here.
The corrected proof is conditional on the JOINT contract below. The current
session has not qualified a backend providing it; mathematical refinement
does not make the design implementation/execution-ready.

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

H/C critical cutoffs must use the joint D/X conversion below; unadjusted
anchor+18s is insufficient when D advances faster. W retains exact PID1+18s.
Each phase starts once on H before its ACK, then C admits after validating
the grant's remaining lifetime. Earlier start alone does not prove earlier
expiry when H/C rates differ.
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

The CLOCK-D1 finding is valid: epsilon on X alone does not bound D's faster
aging. Replace the earlier constant-epsilon claim with a joint contract.

For every supported actor i in {H,C,D}, over intervals within a declared
180-reference-second horizon, require monotonic CLOCK_BOOTTIME readings with:

```text
(1-rho_i)*elapsed - a_i <= recordedDelta_i
recordedDelta_i <= (1+rho_i)*elapsed + a_i
0 <= rho_i < 1
```

a_i is the TOTAL interval uncertainty: both endpoint quantization plus bounded
read/admission uncertainty, not a per-sample error used once. Include suspend;
backwards steps/epoch changes or unbounded rate/read error are unsupported.
Offsets cancel within each actor. No offset/skew sample establishes rho_i.

With z_X = now_X-originalAnchor_X and beta_DX=(1+rho_D)/(1-rho_X):

```text
realElapsedSinceAnchor <= (z_X+a_X)/(1-rho_X)
D_age_since_query <= D_elapsed_since_earlier_anchor
                  <= U_DX(z_X) = beta_DX*(z_X+a_X)+a_D
remainingLower_D_by_X = TTL - U_DX(z_X)
jointEpsilon_X(z_X) = (beta_DX-1)*z_X + beta_DX*a_X + a_D
```

The causal anchor precedes query-begin; the same bound applies to D age since
the later holder start. Require remainingLower>=15/10 respectively, plus the
whole command/kill/postcheck reserve. Require U_DX(z_X)<18 for critical
admission. Local threshold for D remaining r is
K_X(TTL-r)=(TTL-r-a_D)/beta_DX-a_X; critical cutoff is anchor_X+K_X(18).
Fixed D-stage g likewise maps to at most K_X(g); do not reuse an unadjusted
g-second local cap. Nonpositive caps refuse. Round U upward and caps downward
with integer/rational arithmetic; no float underestimation. W independently
retains10/6/3s socket/TLS checks, including after CA preparation.

Joint constants apply for the entire horizon and include BOTH clocks' errors.
Before admitting ANY qualified profile, its coverage proof must establish:

```text
B_operation = (120+a_H)/(1-rho_H)
B_all = B_anchor_start + B_operation + B_termination
U_contract >= B_all + strictly-positive coverage guard
```

B_anchor_start bounds the oldest request/read anchor before H's immutable arm
epoch; B_termination bounds all relevant cancellation/kill/final-receipt
uncertainty after it. Neither is a guessed zero. Verify every interval used
in U (H/C/D lease and critical anchors, request-to-ACK aging, delayed/historical
receipts and cleanup-controller replacement) against the qualified coverage,
including suspension. Any pre-arm/startup anchor straddling activation must be
included; a later activation cannot retrospectively qualify that sample.
An additional path-specific interval longer than B_all must enlarge the bound
or refuse. rho<1 alone is insufficient: rho_H0.9/a_H0 makes B_operation1200s,
so a180s-only profile is inadmissible before any business request.

Finite-horizon validity also requires a trusted fail-closed lifetime mechanism,
separate from a post-horizon numerical sample: suspend-aware cancellation and
irreversible expiry/invalidation must take effect before covered rights end,
and remain latched across an arbitrarily long pause/resume. Alternatively a
backend must supply an independently justified broader lifetime contract.
Do not use the same now/elapsed reading whose error guarantee ended at U to
certify that it is still within U. No such lifetime mechanism is implemented
or qualified here; inability to supply it disqualifies the profile.

After suspension or uncertain coverage, H/C admit no business request/message
unless the already-qualified backend proves the interval remained covered;
expired/unknown validity is terminal for that epoch. A new qualification
cannot renew old tickets, ACKs, ownership deadlines or120s. Cleanup remains
under the original proven budget or unknown/handoff; no clock self-certification
or fresh observation loop is introduced by this precondition.

For an illustrative profile only, rho_H=rho_C=rho_D=0.01, a_H=0.020s and
a_C=a_D=0.002s. It is not a claim about this platform. H's120-local-second
epoch under that contract has B_operation approximately121.232324s;
180s admits it ONLY if proven anchor-start/termination/all-interval coverage
and a positive guard fit the remaining approximately58.767676s. No current
evidence supplies those values. Global75/45/120 remain H-local limits; C must
translate H grants, not treat C's75/120 as H deadlines. A horizon violation
never renews an epoch; expired/suspended work refuses before business.

Reviewer counterexample: TTL30, X rate0.99, D rate1, elapsed20.05s gives
z_X=19.8495. Old epsilon0.1 claims10.0505 remaining; the joint zero-read-error
bound gives9.95, so10s admission refuses. With fastest D1.01/slowest X0.99
and a_X0.020/a_D0.002, the bound is9.72709596, below actual9.7495.

Illustrative H precision edges (not executed clocks):

| Recorded z_H | Joint assertion |
| --- | --- |
|14.68 /14.69 | remainingLower15.00103030 /14.99082828;15s passes /refuses |
|19.58 /19.59 | remainingLower10.00204040 /9.99183838;10s passes /refuses |
|17.62 /17.63 | D_age upper17.99836364 /18.00856566;18s bound passes /refuses |

Exact18 equality refuses; an actual local timeout must expire at its
downward-rounded cap, so equality at that cap also admits no new call.
The table isolates inequalities, not complete permissions: passing one edge
still requires all bindings, current tickets and whole-operation reserves.

Preserve Job30s mapping inside D using the collector's realtime/BOOTTIME pair,
and the worker's existing250ms realtime-versus-BOOTTIME consistency checks.
C can compare validated D-labelled records to each other, but not to C's clock.
Host dispatch receipts are checked only in H's phase/epoch; actual18s admission
is proved by W. Never call completed(holderStart_W, deadline_H, admitted_H).

## Cancellation, recovery and changing predicates

H epoch E binds its boot/local clock instance, source, attempt nonce, immutable
arm/main/total deadlines and ownership journal. begin(E,phase,seq) is accepted
only for the next expected phase with no open/failed phase; it starts H's
clamped cap once, BEFORE ACK. Duplicate begin/seq/ACK does not reset a timer:
reject it, never mint a fresh grant. ACK binds E/phase/seq and its original
remaining H duration r_H; C pins its request-time C sample before sending begin.

At receipt, C computes an upper bound on H aging since that earlier request:
U_HC(z_C)=beta_HC*(z_C+a_C)+a_H. Grant remaining lower bound is r_H-U_HC(z_C).
Require it to cover admission and the entire proposed operation/kill reserve;
otherwise emit NO business request. A delayed ACK after H expiry cannot pass
this joint bound. C never re-stamps a duplicate ACK or advances on it.
Grant/reserve units are H-valued: compare against the fixed allowed H reserve,
or translate a C-valued duration with U_HC, never compare it untranslated.
Qualified rho/a are immutable contract inputs, never supplied by an ACK.

Every H business handler checks E/phase/strict sequence, current local main/
phase/critical/lease caps and one-use action state before admission. Queued
expired messages are rejected without invoking kubectl/exec/create. Phase end
requires completion within H's original cap and never grows parent deadlines;
failed/expired phases admit only cleanup. H remains the final gate even if C
violates its own processing limit. The check/syscall race noted below is not
an assertion of atomic remote API commit before expiry.
The1s dispatch phase bounds ONE worker admission, not completion of its10s
batch. It closes only on an in-cap admission/claim; the admitted operation
keeps its separate bounded exec/postcheck reservation inside H's main cap.
No close/reset mints a second worker right. Late/duplicate admission refuses.
A synchronous completion may return historical H-admission evidence after
the1s phase closed. It is data, not a new ACK/grant: validate the H-labelled
admission and D-labelled result, never interpret it with C's now.

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
contract; zero offsets or a small RTT sample do not establish it. Candidate
backend is direct Linux CLOCK_BOOTTIME via audited native reads/watchdogs:
C/D already use clock_gettime_ns in pinned code/images; H needs a qualified
native carrier rather than assigning /proc/uptime a bound whose view/backend
has not been qualified. This does not diagnose that view as mediated.
No current record qualifies that new H carrier or the JOINT rate/read bounds.
Native syscall choice alone is not a guarantee of a chosen1% error profile.
The [Linux clock manual](https://man7.org/linux/man-pages/man2/clock_gettime.2.html)
describes BOOTTIME as suspend-aware MONOTONIC, which is subject to frequency
adjustment; resolution is implementation-dependent. Those API semantics do
not supply the numerical joint rate/read guarantee required by this proof.

Before implementation, a trusted kernel/runtime/backend contract must justify
rho/a over the finite horizon, with source/version, enforced read uncertainty
and suspend-safe cancellation. Equal-rate rho=0 is allowed only if a trusted
common elapsed-clock contract is established, not inferred from boot labels
or samples. No backend qualification is claimed here. The exact unresolved
capability is a guaranteed joint elapsed-rate/precision bound for H/C/D.
Existing pinned code/image and kernel timer contracts can support qualification;
they do not by themselves prove unknown runtime rate behavior. If no such
guarantee is available, hold this design rather than assume constants or add
an empirical clock-sampling/administrative-export loop.

| Counterexample | Required result |
| --- | --- |
| H/C offsets differ by hours, elapsed timers valid | Local conservative bounds only; never cross-subtract |
| Collector/W namespace mismatch despite zero offsets | Refuse lease/network; retain workload identity guard |
| Collector starts before owned-holder acknowledgement | Reject causal anchor; no policy/worker |
| Delayed capture consumes local TTL or18s window | Refuse; no new DNS/address/holder |
| API/holder restart, boot/epoch change or backwards clock | Invalidate binding/ticket; no elapsed reset |
| Old response/phase/nonce or repeated deletion journal | Reject replay; no repeated action |
| Unbounded clock slowdown/error budget unsupported | No certified lease/deadline acceptance; hold the design |
| rho_H0.9 with180s-only qualification |1200s operation envelope does not fit; profile refused before business |
| Old anchor or final receipt lies outside qualified horizon | Reject that profile/interval; no retrospective qualification |
| Arbitrarily long suspend exceeds numerical coverage | Trusted expiry/invalidation must stay latched; same-clock self-certification cannot authorize a new request |
| Fastest supported D /slowest H/C and15/10/18 rounding edges | Use joint U; reject boundary failures before a business call, independently of W's later refusal |
| Suspend before phase expiry or ACK arrives after it | BOOTTIME includes pause; original H deadline and joint ACK aging reject; no new business request/call |
| Duplicate ACK/begin or queued expired phase command | No timer/grant reset; C emits none and H invokes no business command |
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
Gate order is mandatory: first bind A response to API UID/container/restart/
request nonce and W receipt to owned Job/Pod/image/PID1/nonce; then compare raw
A/W boot+namespace keys BEFORE any cross-workload subtraction. Revalidate W's
own instance/key immediately before using the A lease. Same Node or C's key
does not substitute; stale/restarted/mismatched bindings reject first.

Next is design/proof review, then only if accepted implementation and synthetic
offset/delay/restart/replay tests, exact-source review and a new explicit numeric
disposition. Callback TCP/provider TLS plus verified cleanup supports a later
reviewed address-bound native/bootstrap step; it is not HTTP/auth/web success.
No API-enable, original credentials/state/PVC content, Native/turn or final
PR/publication is authorized by this analysis.
