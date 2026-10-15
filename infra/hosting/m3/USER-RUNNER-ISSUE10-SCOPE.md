# Issue10 accepted rehearsal boundary — 2026-10-15

This document supersedes earlier undecided-scope statements in the design notes.
Authority: [user decisions recorded by PM](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/10#issuecomment-6182177878).

## Current task

In a new isolated namespace, create at most one runner Pod for one synthetic
owner through a separately approved test path, then verify controller-to-runner
transport on namespace TCP8080 using a synthetic transport Secret. This is not a
provider/model request or a working multi-user production service. Ordinary
CODEX_USER_RUNNERS_ENABLE=false and controller max_pods=0 stay unchanged.

The proposed name `codex-runner-rehearsal` is a valid DNS namespace label and fits
the current RUNNER_TEST_NAMESPACE placeholder and namespace-name selectors.
This is source compatibility, not a claim that the namespace is absent or created.
PM must confirm absence, apply only reviewed concrete renders and record its UID.
Do not modify codex-trial or reuse any of its PVs/PVCs/Secrets/owner identities.

Storage choice is two dedicated static synthetic native/control PVs, Retain,
bound through the existing static claim template. The requested backing is local
storage on node anxious-lookout with dedicated paths and namespace/name claimRef
reservation. A PV/host-directory procedure is still to be authored and reviewed;
there is no authorization to guess paths, reuse live directories or run commands
from this scope note. Dynamic/WFFC claims are not selected. PM privately injects
a new synthetic transport Secret, never a provider credential.

## Ordered artifacts and execution gates

1. Envelope comparison review, complete typed CEL checks/defaulting evidence and
   a separately reviewed gate-removal proposal. Current literal false gate stays.
2. Static PV templates and host-directory preparation procedure: explicit node,
   dedicated paths, UID/GID/permissions, retention and claim reservation. Review
   the concrete storage inputs before PM performs host or cluster mutations.
3. Dedicated one-shot controller test configuration and placement: exact target,
   durable no-replay behavior, isolated DB connection, workload initialization,
   temporary quota transition and restoration. Do not enable production settings
   or turn an uncertain create into another attempt.
4. PM executes each approved stage and retains evidence; reviewer independently
   verifies outcomes. The exact final SHA still needs the overall PR gate.

Approval of scope does not imply approval of existing unrendered drafts. Bootstrap
quota0, incomplete envelope, controller placement, DB/callback ingress and resource
initialization still need concrete review. Normal creation is currently blocked.

## Required evidence ledger

| Stage | Private evidence PM records |
|---|---|
| Inputs | Authorized context/namespace, source and image digest/platform, synthetic owner/state/generation, approved test configuration |
| Foundation | Namespace UID, PSA labels, quota spec/status, service-account identities, observed effective policies |
| Storage | Host node/path authorization, dedicated-directory metadata, PV UID/source/node affinity/Retain/claimRef, PVC UID/Bound/volumeName |
| Secret | Name/UID/type/immutable, authorized private injection result; never token bytes or base64 data |
| Admission/network | Exact policy/binding names and render digest, type-check/status, RBAC/quota/PSA/CEL denial provenance, Cilium endpoint identities and allowed/denied flow evidence |
| Creation | Durable one-shot permission/receipt, attempt count, exact Pod UID/owner/state/generation, accepted spec and preserved reservation after uncertainty |
| Transport | Caller/target identity and Pod UID, endpoint/port, authenticated synthetic response and wrong-token/identity rejection; no credential values or provider requests |
| Closeout | Restored test restrictions, retained resources/data inventory, unresolved outcomes and reviewer result; no assumed safe release or automatic recreation |

The exact protocol response/negative transport tests and restoration commands
remain part of the dedicated test-path artifact. TCP connect alone cannot prove
transport authentication or owner isolation. A lack of readiness/provider auth is
not a reason to inject real credentials in this rehearsal.

## Deferred work — do not start before Issue10 PR

[Issue11](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/11) owns native
termination/drain/late-create barrier, safe slot release/recreation, repeated idle
reclaim, measured capacity and operator ChatGPT auth refresh/provider egress.
No Issue11 implementation, provider investigation or load fixture is started as
part of this scope update. Existing design notes describing those requirements
remain handoff records, not current Issue10 completion criteria.

Absence of a Pod or denial of future creates still does not prove termination or
drain. Do not connect slot release/recreation, automate idle deletion, or claim
these properties from the single creation test. releaseToolAvailable remains false.
