# One-shot creation and server-default observation — procedure design

Issue10 user authorization covers the isolated rehearsal type. This document
specifies the intended test sequence; it is not an executable controller wrapper,
RBAC bundle or permission to remove the gate. Concrete source/render/results need
review before PM execution. Target: codex-runner-rehearsal, one synthetic owner,
one deterministic Pod name/generation. Keep production feature off/max_pods0.

## Current gate and obtaining defaults

With the draft's literal false validation and namespace quota0, an expected Pod
cannot be accepted. `kubectl create --dry-run=server` still submits to server
validation/admission/authorization. It does not provide a successful defaulted
object when rejected, and it is not a quota or CEL bypass. Error messages/audit
request objects must not be mislabeled as the final admitted/defaulted object.

First complete the typed v1.36 schema inventory, CEL compilation and independent
per-expression positive/negative tests against synthetic objects. Upstream
source-derived defaults are predictions only. Review that source and a separate
gate-removal candidate before any actual server observation stage. Do not require
a fabricated live positive object to approve its own acquisition: authorize the
limited observation procedure separately after the local typed evidence, then
validate actual defaults before the one allowed persisted creation.

Only after that separate source/procedure review, PM can enter the bounded test
window with the final reviewed policy and temporary quota. Record a server dry-run
using the exact caller and Pod request as the later controller. Capture the
returned JSON privately, after confirming success, and compare the complete spec
with Plan and the reviewed default whitelist. UID/creation time from dry-run are
not a durable Pod identity. Unexpected defaults, schema warnings or refusals stop
before creation. Do not auto-edit policies, normalize new fields or retry creation.
Dry-run repeats are only observation requests, never substituted real POSTs.

## Approved placement A and identities to implement

Run the one-shot controller tool outside the managed namespace in a PM-operated
isolated diagnostic container using host networking, with its dedicated runner-controller SA credential,
exact API IP/CA and isolated disposable registry DB. This avoids admitting an
extra controller Pod through the single-owner runner policy and quota. It does
not imply that Cilium endpoint selectors identify an external diagnostic process.
An external caller cannot satisfy a CNP fromEndpoints SA selector just
by using an SA bearer token: Kubernetes auth identity and Cilium network identity
are different. The user selected A (host-trusted transport) on 2026-10-15.
The revised runner ingress replaces the controller-SA peer rule with host entity
TCP8080 only. It does not authenticate an individual host process. All eligible
local host processes are inside the network trust boundary; bearer/state checks
remain mandatory. Provider access remains closed.

Placement B (separate controller Pod/namespace) is deferred to Issue11. Do not add
a controller Pod or its budget to Issue10. DB must be disposable and separate
from operating app data. The retained controller-SA egress draft does not govern
the host-network diagnostic container and does not grant runner ingress to Pod
callers. Exact rendered policy and execution procedure still require review.

Keep API-SA and synthetic transport token files in a private host directory0700,
each file0600 owned by the diagnostic execution UID; mount only the needed files
read-only. Never put tokens in command arguments, logs or public artifacts.
The confidentiality premise is that other host processes cannot access these
files; mode bits do not isolate processes with the same UID or privileged host
operators. Restrict that UID to the trusted PM execution context. Host network
trust is not evidence of process-level isolation or narrow caller identity.

PM measured allow-localhost=policy is a prerequisite, not proof of enforcement.
During the bounded window record node/runner endpoint identities, effective
ingress/egress policies and policy revision before and after probes. Observe
host→runner TCP8080 allowed; nonhost source→runner8080, host→other runner ports
and runner→API denied. Use reviewed existing diagnostic sources or a separately
reviewed probe procedure; do not create extra Pods or use denied exec to obtain
evidence. Record source identity, destination, protocol/port, timestamp and Cilium
verdict/revision for each probe. A timeout or closed listener alone is not proof
of policy denial. A missing source/probe is an unverified case, not a pass.
Confirm no inherited KNP/CNP/CCNP broadens the intended deny boundary. Host and
runner API access differ: the controller authenticates to API using its SA token;
the runner must have no permitted API route. Do not widen egress to run probes.

Initialize the static native directory before creation using the existing
runner.binding initializer in a reviewed container on node anxious-lookout,
UID/GID10001, network none, only that exact dedicated path and synthetic input
mounted. No initializer Pod or quota exception is needed for static host-mounted
preparation. Verify owner/state markers and preserve partial initialization on
failure. The exact Docker invocation/image/path checks belong to the rendered
execution package; do not run against a caller-selected arbitrary path.

The access-service app can serve authenticated /health without supplying a
provider envelope: startup records unavailable/not_configured and does not call
start_native until a supply callback is invoked. Verify that behavior against the
chosen image in Docker before cluster execution. Never invoke supply or turn
endpoints or inject a ChatGPT credential. Control initialization must explicitly
handle required private operator-account metadata without pretending it is a
provider identity; until specified/tested, do not claim that reason=not_configured
is guaranteed. Binding failure before lifespan yield is not transport success.

## One durable creation permission, not a retry loop

The future dedicated tool must hard-bind namespace UID, Plan owner/state/generation,
image and object references, and refuse any other target. It must persist a
nonreplayable operation marker before external create. No --force or marker reset.
Use the existing controller reserve/ensure boundary with isolated test capacity1
only inside that tool; do not change runtime configuration or an operating DB.
Reinvocation must lookup/report only. Validate namespace UID and workload inventory
before issuing a permit. A crash before POST still consumes the permit. A rejected
or timed-out POST must never generate a second permission automatically.

Proposed temporary quota: change only the rehearsal bootstrap count/pods from0
to1 after policy/render/negative-test approval and no existing Pods. The controller
SA cannot edit quota/policy or delete Pods. PM records original/new quota resource
versions and waits for observed quota state before the test. RBAC may limit named
GET and grant create; it cannot constrain create by name, so final CEL is required.
All temporary changes and the dedicated tool still need exact review; no runnable
commands are supplied before these components exist.

## Observation and restoration

After exactly one persisted create, compare returned/GET Pod UID, labels, complete
spec and PVC/Secret identities; no UID adoption on mismatch. Read /health on8080
using the synthetic bearer and X-Runner-State-Id. Require200 plus matching ownerId,
stateId and `podUid`, available=false and verification=unverified. Wrong bearer
must return401; correct bearer/wrong or missing state must return503 policy refusal.
Record redacted HTTP status/body evidence for these cases within the same policy
observation window, without exposing bearer or SA tokens. A TCP
handshake alone is not success. No session/turn request, provider authentication
or ready/available:true claim is part of this test.

Restore count/pods0 and stop future test invocations under PM's reviewed procedure
on success or failure, record restoration response/status, and retain the existing
Pod, PVs, PVCs, Secret and durable permit as evidence. Quota0 does not delete a live
Pod or prove no in-flight create exists. Revoking test permission likewise does not
prove drain. On uncertainty retain the reservation and stop; no idle deletion,
slot release, replacement, rollback-to-old-DB or replay is part of Issue10.

## Acceptance blockers at this revision

Complete typed CEL/gate removal review, controlled server-default observation,
verification of selected A controller/Cilium/DB placement, image initialization and a durable one-shot
wrapper remain unimplemented or unverified. These are Issue10 work, not Issue11
termination requirements. This procedure must not be reported as an executed test.
