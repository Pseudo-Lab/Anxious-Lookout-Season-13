# Unit3 design update from PM's measured cluster baseline

Evidence: [PM's authorized read-only baseline, 2026-10-15](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/10#issuecomment-6182026519).
Backend reviewed that record; backend did not independently query the cluster.
The record supersedes earlier statements that the server/API/CNI were unknown.
It does not prove enforcement for any new namespace or authorize application.

| Recorded fact | Design consequence |
|---|---|
| k3s v1.36.4+k3s1, arm64, single node | Build/review arm64-compatible images; PSA version v1.36 is supported as a proposed pin; node failure cannot be treated as redundant storage failover |
| admissionregistration.k8s.io/v1 includes VAP/Binding, no existing admission objects | Propose native CEL ValidatingAdmissionPolicy v1 plus Binding; do not introduce webhook infrastructure without a demonstrated CEL gap |
| Cilium1.20.2, policy default, L7 proxy enabled, existing CiliumNetworkPolicies | Design new namespace isolation with Cilium enforcement and explicit DNS/FQDN route rules; inspect all overlapping policies |
| local-path default Delete; explicit Retain classes exist; all WFFC | Never omit storageClassName. Review explicit Retain class and first-consumer bootstrap before replacing static claims |
| Existing codex-trial has bound native/control PVCs, transport Secret and restrictive policies | Reference its structure read-only through PM; do not reuse its claims, credentials, identities or modify it |

## CEL admission design to render next

Concrete target: admissionregistration.k8s.io/v1 ValidatingAdmissionPolicy and
ValidatingAdmissionPolicyBinding, `failurePolicy: Fail` and binding
`validationActions: [Deny]`. These are cluster-scoped policy objects even though
matching is restricted to the approved isolated namespace; PM must obtain approval
for their creation and review the match scope before applying them.

For the first synthetic owner, prefer a self-contained generated policy with
reviewed literal identity/template values rather than a mutable parameter store.
This avoids a new CRD/parameter authorization surface. Updating owner/generation
then requires a separately reviewed policy revision; it is not a live generation
controller or a drain barrier. If parameters are later introduced, require
`parameterNotFoundAction: Deny`, strict parameter ownership and version checks.

Match the chosen namespace by the control-plane namespace-name label, never only
caller-supplied Pod labels. Main Pod CREATE/UPDATE rules match core/v1 namespaced
`pods`. Require the exact controller service-account identity for CREATE, exact
Pod name/owner/state/generation, approved image digest and workload SA, exact env,
callback and command, volume/mount topology, no init/sidecar containers, no host
namespaces or host paths, no token projection/automount, and the existing Plan's
nonroot/security fields. Deny arbitrary extra fields that change execution.

Separate rules must reject `pods/ephemeralcontainers` UPDATE and unauthorized
CONNECT to `pods/exec`, `pods/attach`, and `pods/portforward`. Do not accidentally
apply the creator identity condition to kubelet `pods/status` updates. Inspect
server defaulting/admission mutation against the exact adapter normalization;
unknown required mutations must be reviewed, not silently accepted. Policy
status/type-check warnings must be resolved before installation acceptance.

Before granting create rights or changing bootstrap quota, require positive tests
for the approved object and negative tests for each identity, image, env, mount,
SA, privilege, extra-container and subresource change. Check missing labels,
policy errors and unauthorized policy editing. Perform tests against actual v1.36
admission in the isolated target after approval; local text review is insufficient.
No final executable CEL expression bundle is claimed by this design update.

## Retain PVC path: candidate with a bootstrap dependency

The existing static template remains a valid separate option. The dynamic option
would remove volumeName, explicitly name an approved Retain StorageClass, preserve
Filesystem/access modes and separate native/control PVCs, then record the actual
PVC UIDs and allocated PV identities. Inspect the selected class's provisioner,
reclaimPolicy, binding mode and resulting PV reclaimPolicy; a familiar class name
is not enough. Retain is a deletion policy, not backup, confidentiality, process
termination or a storage lease fence.

**WFFC conflicts with the current preparation order:** a newly requested claim
can stay Pending until a consumer Pod is scheduled. The current zero-Pod quota
prevents that consumer, and KubePods._references requires Bound plus a known PV
before runner creation. Merely switching the PVC template to a Retain class would
therefore not produce a working creation path.

Proposed resolution for review: a separately authorized, fixed-name restricted
initialization workload consumes only the two synthetic claims under a distinct
initializer identity and narrowly reviewed admission exception. Its isolated
creation stage needs a bounded temporary quota change, with no controller create
grant or provider egress. Record binding UIDs/PVs, initialize the reviewed state
layout, prove initializer termination, then close the initialization exception and
restore bootstrap restrictions before deriving the runner Plan. The initializer
image/command, quota transition and Secret handling are not supplied or approved
yet. An initializer must not impersonate a normal runner to bypass this gate.

Alternative: approved statically prebound volumes, retaining the existing flow.
Do not switch to Immediate binding, remove quota or skip Bound/identity checks as
an unreviewed shortcut. PM/user must select the path before its rendered manifest
and initialization procedure can be finalized.

## Cilium and provider dependencies

Use CiliumNetworkPolicy only for the approved namespace, retain default deny and
review union/overlap with standard NetworkPolicy and cluster-wide Cilium policies.
Provider rules can use exact `toFQDNs.matchName` entries with explicit TCP ports and
a narrowly selected resolver DNS policy. FQDN rules learn IPs through DNS proxy;
they are not HTTP URL/path or provider authentication verification. Account for
DNS answers, TTL, cached IPs and shared addresses in allowed/denied traffic tests.
Do not add wildcard domains or broad world/same-namespace access for convenience.

The supported operator ChatGPT auth refresh path still precedes provider egress
selection. No hostnames or token-copy workaround are invented here. Separate
controller/API transport, callback, resolver and provider routes; test each
allowed flow and unrelated-namespace/metadata/API/provider denial in the actual
Cilium1.20.2 environment. Existing codex-trial policy success is not proof for a
new namespace. Its policies may be inspected by PM without exporting Secret data.

## Scope remains unchanged pending the user's decision

PM has recommended a smaller task, but no scope-change authorization has arrived.
Keep native termination, late-create/drain, safe release and capacity evidence as
unmet gates; do not claim synthetic creation or idle deletion proves them. No
feature activation, controller capacity increase, quota change or cluster apply
is performed by this document. If scope is split, record the accepted boundary
and follow-up issue links before altering completion criteria.

References: [VAP enforcement](https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/),
[WaitForFirstConsumer](https://kubernetes.io/docs/concepts/storage/storage-classes/#volume-binding-mode),
[Cilium DNS-based policies](https://docs.cilium.io/en/stable/security/dns/).
