# Issue #7: corrective path after retained runtime evidence — proposal only

The completed capture at /tmp/issue7-runtime-metadata.etKOJT used4e0afc8.
Its original runtimeMetadataStatus remains unknown: the name parser rejected
Docker's extra terminal LF. The corrected offline parser accepts the exact
retained selected files after matching budget/completion and ownership checks.
This is a corrected interpretation of that capture, not a new capture or a
rewrite of its original outcome.

DefaultRuntime and selected controller runtime both read runc. Runtime names
are io.containerd.runc.v2 and runc. Selected required isolation fields pass.
The original clock guard still rejects namespace equality alone; boot/type
and250ms pass. Neither runtime names nor zero offsets establish namespace
equivalence, runtime suitability or a configuration cause. Explicitly
selecting runc is unsupported as a corrective change to this already-runc
observation. The other listed name supplies no isolation or suitability proof.

## Next minimal prerequisite: operator/controller placement assessment

The remaining boundary is the broker's own-process namespace versus the
Docker-created controller's own-process namespace. Prior provenance and this
capture exclude a simple whitespace/reader substitution as the correction.
Before another clock-only attempt, establish the operator launch placement
and Docker execution placement from existing deployment/launch records.
Ask PM to identify whether the reviewed root broker was launched directly
on the retained Node or through an outer execution environment, and whether
Docker executes locally on that same Node. These are unanswered placement
questions; the capture is not proof of an outer container or remote daemon.
Use already-known operator/configuration provenance first. This proposal
authorizes no new PID/container/daemon/config queries to fill missing records.

If those records establish a placement discrepancy, prepare an exact source/
argv proposal to run the unchanged broker from a trusted direct operator on
the retained Node with its local Docker execution context. Treat that as a
reviewed operator-placement change, not an automatic profile switch. Verify
image/source provenance, target Node and unchanged controller protections:
networknone, fixed immutable image, user0:0, read-only rootfs, capdropALL,
no-new-privileges, source/evidence readonly and no credential/PVC mounts.
No setns/unshare, namespace mount, host PID/network or daemon-default change.

If existing records already show that placement, or required provenance or
protections cannot be established, stop this candidate and re-scope. Do not
promise it fixes the mismatch or prepare an execution command from guesses.
Any additional observation must first have its own bounded metadata scope,
ownership constraints, source/argv and independent execution review.

## Acceptance and remaining holds

Only a separately approved single fresh clock-only capture may test a
concrete placement candidate. Preserve10s startup/collection/parsing/writes,
bounded owned-ID teardown, private completion evidence, exact boot/namespace
and250ms guards, and host/save/done only. Complete evidence without guard
acceptance is a refusal, not an invitation to try another placement/runtime.

Even controller guard acceptance would not establish collector/holder
actor-local equality or authorize numeric replay. A later numeric disposition
requires fresh complete baseline/owner/image/policy/clock gates and the
original stage/main/cleanup budgets. No arm, Kubernetes call, production
mutation, runtime install/restart, clock bridge, API-enable, credentials,
Native/turn or final PR/publication is included here.

Retained stop stdout consists of the --time deprecation warning followed by
the created CID, with exit0. It acknowledges that selected-ID stop; it does
not independently establish present absence. No extra query/stop is needed
to interpret this response. Retain the original private files unchanged.
