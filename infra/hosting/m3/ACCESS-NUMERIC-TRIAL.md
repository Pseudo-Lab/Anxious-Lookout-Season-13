# Issue #7: complete bounded numeric entrypoint

This composes ACCESS-NUMERIC-TRANSPORT.md without extending its scope. PM
operates it only from the exact source SHA approved for this entrypoint.
Approval of individual helpers alone does not authorize this composition.

## Invocation and inputs

On the retained Node's trusted root operator host, prepare a fresh empty0700
directory. Use the successful typed numeric.bundle.json as a **seed of pinned
identities and expected baseline**, never the failed preflight.bundle.json or
empty receipt.json. No saved live result is trusted as a current gate.

```bash
install -d -m 700 /private/numeric-NEW
bash /reviewed/checkout/infra/hosting/m3/run_numeric_trial.sh \
  /reviewed/checkout /trusted/absolute/kubectl \
  /private/current/numeric.bundle.json /private/numeric-NEW EXACT_REVIEWED_40_HEX_SHA
```

The checkout must be clean at that SHA. Docker/tooling imagee1cd49c, local
API diagnostic image and Runner2948 must already exist; pull=never. The
controller has networknone/read-only/capdropALL/no-new-privileges, reads only
the source and private metadata mount and receives no kubeconfig, Docker
socket, original credentials or PVC mounts. Language code executes inside
this persistent Docker process. The host broker uses bash/coreutils and
fixed kubectl/Docker commands, with no host Python, eval or generated shell.

Seed expectedNode/receipt/inputs/images and snapshot resource names are pinned
authorities. The controller generates a new128-bit nonce. It refreshes typed
Node/DS/agent/CRD/NodeConfig/config/policy/Service/Pod/Job data and individual
PVC/PV metadata GETs. It follows EndpointSlice targetRef UID → exact Pod →
ReplicaSet → Deployment for API and DNS, validates exact numeric/named-port
mapping, then CEP attachment → selected agent numeric endpoint GET. It reads
the running agent version/status/five flags and makes an explicit independent
loopback API Node GET. Full preflight and false-health are recomputed and
compared to the pinned baseline. Failed/foreign/partial/paginated inputs stop.

The actual API Pod's spec/imageID must match its current Deployment's immutable
api image. This is the current service image, distinct from the diagnostic
tooling image in images.api. Fresh Node.status.images must contain that exact
API reference and Runner2948 reference; missing/truncated cache evidence
refuses before arm. Local Docker inspections separately verify the diagnostic
tool images exist. Actual holder image/spec/UID remains checked before any
collector or worker call. The host broker independently reports boot/time
namespace and its BOOTTIME centiseconds. The controller compares both
identities and clock skew<=250ms before arm and again before cleanup calls.
Host/controller/collector/holder Node boot and time namespace must match;
no off-node tooling or stale image/clock substitution.

## Execution and budgets

Only after refreshed guards and static holder source rendering does the host
arm once. The kernel holder start, complete receipt and raw ownership are
acquired under the3s original group, including reads before the receipt is
known. Before the kernel receipt is known, its deadline is conservatively
anchored at Job create dispatch (which precedes holder start), then tightened
to the actual holder start if needed. Original six native rows are validated and saved as the pre-addition
reference. Full raw binding checks remain, including CEP-before-numeric-GET.

Collector is synchronous and one-use. The broker supplies its absolute saved
group deadline through collector.deadlineCs; the wrapper tightens its5s exec
bound to remaining time minus1s kill, never lengthens it. Full current binding/
baseline is recomputed after collection, excluding only the exact owned
diagnostic Job/Pod from production Runner0 accounting. All original baseline
checks remain. If this acquisition cannot fit2s, it refuses before CNP creation.

Lease/policy parsing, one owned CNP create, current realization, program emit
and saved1s dispatch admission use the existing fixed stage bounds. Dynamic
raw reads, imports, assembly and validation count inside saved deadlines.
Only independent reads are batched; dependent reads remain ordered.

The broker also receives each absolute group deadline and limits its wait
for the controller. A stalled validation cannot consume the main cleanup
reserve while waiting for another request. Completed groups clear only their
own current bound; an active deadline may be tightened but never extended.
Dispatch clears its admission wait before the synchronous batch; the wrapper
still checks the saved private deadline and records historical admission.
Remote first-connect<18s and batch/end/lease checks remain authoritative.

Worker12s+1kill and reserved postchecks13+13+6s remain within main75s. Dispatch
receipt and result validation share the last6s. Group/main/batch expiration,
transport ambiguity, missing receipt or invalid raw input consumes the
attempt and stops further business calls. There is no second query, address,
holder, CNP create or worker. No production Service/policy change occurs.

## Ownership, cleanup and results

Private request/input/stdout/stderr/exit files and save-once stage/ownership
journals preserve the complete transcript. A full successful create response
records UID and acknowledged defaulted specHash, even if too late to admit
another stage. An unacknowledged create is never adopted/deleted by name.

Cleanup has the same arm+120s deadline, including its full45s reserve and
Docker recovery startup. It obtains current exact owned Job/spec/UID and
issues one UID/RV conditional DELETE; confirms Job absent, app RunnerPods0
and existing Runner Service EndpointSlices empty; then deletes only the
exact owned CNP and confirms absence. Finally it reacquires full false-health
against the pre-arm baseline. No deletion retry, label mass-delete, force,
prune, production restart or node mutation occurs.

Before each DELETE it persists delete-attempt-KIND.json. If the controller or
broker fails after arm, the host stops only its explicitly named controller
and starts one cleanup-only controller from the original journal/deadline.
That path cannot arm or run another diagnostic. It refuses to repeat any
journalled deletion attempt. Remaining time insufficient, uncertain create,
foreign/replaced object, pending Pod disappearance, transport/delete ambiguity
or failed full health yields unknown_handoff with owned identities/nonce and
unacknowledged intents preserved. These records do not assert current presence.

Inspect outcome.json and broker-main-outcome.json; after recovery, inspect
recovery.json/broker-cleanup-outcome.json or broker-handoff.json. Exit0 only
means the controller protocol completed, not that transport or cleanup passed.
Only cleanup.status=verified asserts full false-health restoration. Callback
TCP and verified provider TLS remain transport results, never HTTP/auth/web
or Native success. Preserve evidence for independent outcome review.

## Offline evidence and limits

test_numeric_trial.py exercises the complete controller using real baseline/
owner/native/parser/program/result/delete helpers with substituted raw
transport and scripted time. It includes valid completion, pre-arm image
refusal, binding/create delay, unacknowledged create, wrong numeric endpoint,
late dispatch and ambiguous cleanup. Root Docker tests run the actual shell
broker and Wire controller with Docker/kubectl CLI substitutes inside the
network-none test container; image refusal and one unacknowledged create make
no retry. A stalled controller is stopped at its saved deadline; the real
cleanup-only controller issues one exact conditional DELETE and rechecks full
false-health. Separate real-wrapper tests cover >1s worker completion and
absolute collector cancellation/refusal.

These establish executable composition and refusal logic, not measured live
latency or a promise that3s/2s groups will fit. A bounded refusal is an expected
stop. No live DNS/TLS/CNP/cleanup outcome is claimed. PM must preserve actual
preflight/stage/transport/postcheck/cleanup evidence. All DN12/Node/BPF/auth/
state/PVC-content/API-enable/Native/turn/final-publication holds remain.
