# Isolated preparation templates — not execution policy

Review these templates and their concrete rendered target before PM uses them.
They are not a deployable runner: no Pod, workload, Role/RoleBinding, credential
minting, admission implementation or network allow rule is included. Keep feature
off and max_pods0. Never apply to an existing operating namespace. The three
`.json.in` files contain deliberately unresolved inputs; do not apply them raw or
perform unchecked textual substitution. JSON-aware rendering must preserve types
and reject unfilled/unknown inputs. Rendered documents need a separate review.

## Required decisions and inputs

| Input | Evidence / approval needed |
|---|---|
| Target context and new namespace | User authorization for namespace creation in the identified isolated cluster; PM records context and namespace UID after creation |
| PSA version | Template pins v1.36 from PM's measured baseline; verify the target before rendering |
| Storage provisioning | User authorization for provisioning method, capacity, backing system and retention; storage administrator supplies two distinct synthetic PVs with no user data |
| Native/control PV names and class | Inspect PV UID, volume identity, claimRef, reclaim policy, mode, class and capacity; both must be dedicated to this rehearsal |
| Requested sizes/access mode | Explicit approved values supported by both PVs; access mode alone is not a process/lease fence |
| Owner/state | Fresh synthetic UUIDs, not actual account identities; same labels on claims and Secret |
| Secret input | Authorized private injection of a newly generated synthetic transport token, at least32 ASCII non-whitespace characters, base64 encoded; never ChatGPT/provider credentials |

PM obtained authorized read-only baseline on 2026-10-15: k3s v1.36.4, VAP v1,
Cilium1.20.2 and Retain local-path StorageClasses. PSA v1.36 is pinned.
CEL VAP v1 is now the proposed admission path; it is not installed here.
See ../USER-RUNNER-BASELINE-2026-10-15.md for the evidence and WFFC bootstrap
dependency. PM owns further cluster reads; do not access root kubeconfig or
modify the existing codex-trial namespace. Target namespace/storage choice and
rendered resources still need approval.

## Storage scope

PM's 2026-10-15 review requirements retain the static PV path. Dynamic PVCs
are not selected. A future proposal must separately review first-consumer
initialization, admission/RBAC/network/quota ordering and PV UID capture.

`10-static-claims.json.in` is **only the static preexisting-volume binding path**.
It does not create PVs, choose a storage driver, use a default dynamic class, or
approve a provisioner. volumeName is mandatory and the native/control PV names
must differ. Explicit class must match both PVs; an empty string is valid only if
the approved static volumes have no class. If dynamic provisioning is selected,
stop and request a separately reviewed provisioning manifest; do not delete
volumeName or guess a storage class in this file.

Use retained synthetic PVs already reserved for these exact future claims (with
namespace/name claimRef under the storage administrator's reviewed procedure).
Do not bind a released volume from another owner or alter an old claim UID.
Prebinding can bypass normal selection constraints: inspect the actual backing
volume and identity before creation, then verify resulting binding and PVC UID.
Creating claims can affect PV binding and is a mutation even though no Pod runs.
Do not delete/recreate claims to fix an unexpected binding.

## Application order after review and authorization

1. PM confirms exact new namespace name is absent in the chosen context and that
   the rendered namespace metadata contains only the approved target/version.
   Existing namespace -> stop, do not apply/overwrite its labels. Confirm selected
   context privately; never dump kubeconfig credentials.
2. Create the foundation objects in listed order: Namespace, zero-Pod quota,
   default-deny policy, two no-automount service accounts. Use individual reviewed
   objects in that order; a List is not transactional. On partial failure stop and
   inspect what exists rather than continuing with storage/Secret. PM may use
   `kubectl --context <approved> create -f <reviewed-single-object.json>` from its
   authorized diagnostic container; do not use apply to reconcile unknown objects.
3. Read back namespace UID, quota status/hard, both service accounts and every
   NetworkPolicy. Check inherited RBAC; no new controller grants are made here.
   The zero-Pod object-count quota is a bootstrap stop, not a selected production
   Pod cap or proof of isolation. Keep it in place. CNI enforcement remains a
   separate check; creating default-deny does not demonstrate packet denial.
4. Only after storage approval and the two PV checks above, create the rendered
   claims, inspect Bound/PV identity and record resulting PVC UIDs. Pending is not
   success. Do not grant controller create rights, run an initialization Pod or
   remove the quota to force progress.
5. Inject/create the rendered immutable Secret through the approved private file
   mechanism (mode0600, no shell argument token, no output dump, no commit or
   artifact attachment). The template contains no actual token. Avoid client-side
   apply annotations containing the Secret data. Inspect name/UID/type/immutable
   metadata only using USER-RUNNER-ISOLATION-PLAN.md; secure private-file cleanup is
   PM's normal credential handling, not namespace/PVC deletion.
6. Record all immutable identities for a later Plan. These new PVCs are not yet
   initialized native/control state and cannot establish provider readiness.
   A separately reviewed initialization/admission/RBAC/transport fixture and
   negative tests must precede any permission to create a runner or remove quota.

No unconditional `down`, namespace delete, Secret rotation, PV reclaim or force
cleanup is supplied. Retain partial objects and evidence for PM's explicit
recovery plan. Inventory outputs contain private identifiers; share aggregates.

## Dependencies still closed

The supported operator ChatGPT authentication **refresh** path remains unresolved.
Its investigation result is a prerequisite for provider egress design and native
readiness testing. Do not add provider allow rules, copy live tokens into this
Secret, or treat synthetic transport authentication as provider authentication.
Default deny includes DNS/callback/provider until individually reviewed.

Native admission/completion/termination and accepted-create drain need the separate
evidence protocol described in USER-RUNNER-ISOLATION-PLAN.md. These preparation
objects cannot prove it. No slot release or enablement command is supplied.

Backend will run later lock/pool measurement fixtures in isolated Docker using
synthetic data and send source SHA, settings, sample counts, errors and
p50/p95/p99/max to PM/review. No operating cluster load test or production capacity
selection follows from approval of this preparation bundle.

References: [object-count quotas](https://kubernetes.io/docs/concepts/policy/resource-quotas/#object-count-quota)
and [reserving a PersistentVolume](https://kubernetes.io/docs/concepts/storage/persistent-volumes/#reserving-a-persistentvolume).
Quota admission/enforcement must be verified on the actual cluster; neither the
API object's presence nor this local JSON check proves it.
