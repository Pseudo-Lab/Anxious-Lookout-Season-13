# Isolated namespace deployment policy and evidence plan — design review

Current scope: see [accepted Issue10 boundary](USER-RUNNER-ISSUE10-SCOPE.md).
Termination/drain, release, capacity and provider follow-up are now Issue11;
the sections below retain their design history, not Issue10 execution authority.

Status: proposed design, not an installed policy bundle or an activation approval.
This is unit3's first reviewable artifact. Apply nothing to an operating namespace.
Keep CODEX_USER_RUNNERS_ENABLE=false and controller max_pods=0. An isolated test
namespace and synthetic owner volumes/accounts are required for later rehearsal.
PM records the chosen context/namespace and reviewed source/image privately.

## Policy boundary to implement before a rehearsal

| Boundary | Required policy | Evidence before execution |
|---|---|---|
| Namespace | Dedicated runner namespace; restricted Pod Security enforcement; no unrelated workloads or privileged exemptions | Namespace labels, actual admission configuration and negative Pod tests |
| Controller RBAC | Namespaced pods create; named pods get; named PVC/transport Secret get only | Effective permissions, all RoleBindings plus inherited cluster grants |
| Runner identity | Dedicated service account, no API grants, token automount false | Effective permissions and actual admitted Pod spec |
| Template admission | Deny mismatched owner/state/generation, deterministic name, image digest, callback, service account, env, volumes/mounts, security fields; deny extra containers/init/ephemeral containers, host access and projected API tokens | Server-side denial tests for each changed field, including update/subresources |
| State | Preexisting separate native/control PVCs; immutable transport Secret; exact UID and PV identity tied to owner/state | Metadata against the reviewed Plan, storage isolation tests |
| Network | Default deny ingress/egress for all runner namespace Pods; only reviewed caller→runner, callback, resolver and provider paths | Effective CNI enforcement and positive/negative traffic tests |

The controller role must not have list/watch/delete/patch/update, exec/attach/logs,
proxy, PVC provisioning, policy editing, RBAC escalation or token minting. A later
termination actor needs its own separately reviewed rights; do not expand this
creator identity preemptively. Top-level Pod create cannot be constrained by
resourceNames, so named GET grants do not replace admission enforcement.

Admission must match the entire isolated namespace independently of labels the
creator supplies. Pin both enforcement configuration and authorized identity.
Missing parameters, unavailable validator and evaluation errors must reject the
request; policy bindings must enforce Deny, not merely Warn/Audit. Verify supported
API/version against the recorded baseline: CEL VAP v1 is now the proposed path
(see USER-RUNNER-BASELINE-2026-10-15.md), pending concrete policy approval.
No permissive fallback is allowed. The current adapter checks objects after the
server response, which cannot undo execution of a maliciously mutated Pod; the
pre-creation admission boundary is therefore mandatory.

Network allow rules are additive: inspect every policy selecting these Pods,
including a policy that admits all traffic. Avoid broad same-namespace allowance.
Current Plan has no reviewed provider route or end-to-end native dispatch path;
keep these closed until that interface is reviewed. DNS allowance, if needed,
only targets the chosen resolver/ports. Standard NetworkPolicy alone is not a
provider hostname allowlist. Confirm CNI capabilities before selecting an egress
proxy or a CNI-specific policy. A policy object's existence is not enforcement.

## PM read-only inspection commands

Run the approved kubectl/openssl tools in a diagnostic container, with the existing
authorized kubeconfig mounted read-only. Do not print kubeconfig, Secret data,
private keys or bearer tokens. Set these variables from the reviewed isolated
inventory, never from a user's request. Namespace names alone do not prove that
a target is disposable: PM verifies context/namespace UID against its inventory.
All command output is private evidence. Missing access is an unresolved check,
not a reason to grant broad privileges or assume success.

```sh
# Existing isolated target and its recorded identities; placeholders must be replaced.
RUNNER_CONTEXT='<reviewed-isolated-context>'
RUNNER_NS='<reviewed-isolated-namespace>'
RUNNER_CONTROLLER_SA='<controller-service-account>'
RUNNER_WORKLOAD_SA='<runner-service-account>'
RUNNER_NATIVE_PVC='<native-pvc>'
RUNNER_CONTROL_PVC='<control-pvc>'
RUNNER_TRANSPORT_SECRET='<transport-secret>'
RUNNER_POD='<deterministic-runner-name>'

kubectl --context "$RUNNER_CONTEXT" version -o json
kubectl --context "$RUNNER_CONTEXT" get namespace "$RUNNER_NS" -o json
kubectl --context "$RUNNER_CONTEXT" -n "$RUNNER_NS" get networkpolicies -o yaml
kubectl --context "$RUNNER_CONTEXT" -n "$RUNNER_NS" get roles,rolebindings -o yaml
kubectl --context "$RUNNER_CONTEXT" get clusterroles,clusterrolebindings -o yaml
kubectl --context "$RUNNER_CONTEXT" -n "$RUNNER_NS" get serviceaccount "$RUNNER_WORKLOAD_SA" -o json
kubectl --context "$RUNNER_CONTEXT" api-resources --api-group=admissionregistration.k8s.io
kubectl --context "$RUNNER_CONTEXT" get validatingadmissionpolicies,validatingadmissionpolicybindings -o yaml
kubectl --context "$RUNNER_CONTEXT" get validatingwebhookconfigurations,mutatingwebhookconfigurations -o yaml
kubectl --context "$RUNNER_CONTEXT" -n "$RUNNER_NS" get pvc "$RUNNER_NATIVE_PVC" "$RUNNER_CONTROL_PVC" -o json
kubectl --context "$RUNNER_CONTEXT" -n "$RUNNER_NS" get secret "$RUNNER_TRANSPORT_SECRET" -o go-template='{{.metadata.name}} {{.metadata.uid}} {{.type}} immutable={{.immutable}}{{"\n"}}'
kubectl --context "$RUNNER_CONTEXT" -n "$RUNNER_NS" get pod "$RUNNER_POD" -o json
```

The last command can return NotFound before a test creates a Pod; that proves
neither termination nor slot availability. Secret output intentionally excludes
its data and annotations. Compare its UID/type/immutable flag with Plan; content
binding remains a separate authorized adapter check, not a console dump. Inspect
PVC Bound status, volumeName, UID and owner/state labels against Plan, then use
`kubectl ... get pv <recorded-volume-name> -o json` only for the two recorded PVs.
Check retention and storage fencing behavior, not just accessModes declarations.
Do not infer provider authentication or storage contents from metadata.

To inspect effective permissions, PM may use the following authorization reviews
only if its existing identity has impersonation authority. These create transient
review requests, not workload resources; they are not ordinary resource GETs.
An impersonation denial does not mean the target service account lacks permission.
Use the exact namespace of each identity if the controller lives elsewhere.

```sh
kubectl --context "$RUNNER_CONTEXT" -n "$RUNNER_NS" auth can-i --list --as="system:serviceaccount:$RUNNER_NS:$RUNNER_CONTROLLER_SA"
kubectl --context "$RUNNER_CONTEXT" -n "$RUNNER_NS" auth can-i --list --as="system:serviceaccount:$RUNNER_NS:$RUNNER_WORKLOAD_SA"
kubectl --context "$RUNNER_CONTEXT" -n "$RUNNER_NS" auth can-i create pods --as="system:serviceaccount:$RUNNER_NS:$RUNNER_CONTROLLER_SA"
kubectl --context "$RUNNER_CONTEXT" -n "$RUNNER_NS" auth can-i get "secrets/$RUNNER_TRANSPORT_SECRET" --as="system:serviceaccount:$RUNNER_NS:$RUNNER_CONTROLLER_SA"
kubectl --context "$RUNNER_CONTEXT" -n "$RUNNER_NS" auth can-i delete pods --as="system:serviceaccount:$RUNNER_NS:$RUNNER_CONTROLLER_SA"
kubectl --context "$RUNNER_CONTEXT" -n "$RUNNER_NS" auth can-i create pods --subresource=exec --as="system:serviceaccount:$RUNNER_NS:$RUNNER_CONTROLLER_SA"
```

Expected creator results: create Pod/named Secret GET yes; delete/exec no. Review
the exec check as resource=pods, subresource=exec with no resource name. TYPE/NAME
syntax selects a named resource; it does not select a subresource.
Review
all effective rules, including cluster grants, arbitrary Secret GET and other
namespaces; these four samples are not a full authorization proof. Runner should
have no workload/credential privileges. Default discovery permissions alone are
not permission to read workload data.

For API TLS follow USER-RUNNER-CONTROLLER.md's API IP and certificate verification
section using the same path/CA as the controller. Record actual server version,
CNI implementation/version/enforcement configuration and the namespace UID. Obtain
CNI evidence from the cluster administrator if it is outside PM's read scope.
Read-only inspection cannot prove packet denial, template rejection, delayed
creation rejection or native termination. Those require a separately reviewed
isolated test fixture with controlled mutations; none are authorized by this plan.

## Next evidence interfaces and unresolved release boundary

The following is a proposed evidence contract, not a boolean --verified switch:

- Admission record: owner/state/generation/Pod UID, durable request ID and original
  admission time. Persist before dispatch; replay must not dispatch twice.
- Completion: immutable original request ID, matching execution identity and
  original durable completion time; reconcile before considering idle. Replayed
  evidence cannot advance last_activity. Missing completion remains unresolved.
- Stop: close new admission for this generation durably, reconcile pending work,
  then prove the actual old process is dead and storage ownership cannot overlap.
  Pod deletion/NotFound, timeout, node partition, a new flock, and a signed status
  message from an untrusted workload are insufficient alone.
- Late-create: deny old-generation creation at an authoritative admission boundary
  and account for requests admitted before that boundary changed. A webhook or
  CEL rejection of future requests does not drain already admitted requests.
  Kubernetes resourceVersion and one GET are not such a drain certificate.
- Release: atomically consume verified termination/drain evidence against current
  fence and retain a terminal tombstone, outbox and all request/completion history.
  Only then allow a new generation. Missing evidence leaves capacity held.

Current source lacks an authoritative drain/partition termination provider and a
reviewed evidence-to-registry adapter. Do not wire existing verified=True methods
to operator assertions or raw Pod status. Selection of a concrete barrier protocol
and its failure/restart tests is the next design review, before a release tool.

## Planned load evidence, not a capacity recommendation

Use synthetic owners and a disposable DB. Measure whole owner transaction duration,
DB checkout wait, connections held, each HTTP operation, retries (must be zero),
request refusal latency and cross-owner progress. Separate lookup-existing,
create success, absent reserved, slow connect/TLS, headers/body trickle and delayed
POST response. Include same-owner contention, independent owners and simultaneous
capacity reservation. Freeze input workload and record versions/image SHA.

Report p50/p95/p99/max plus sample count and errors at each tested concurrency;
verify global held-slot invariants and one-create receipts while injecting worker
crash/DB reconnect/timeout. Do not multiply a single observed average into a safe
capacity number. Per-request HTTP deadlines do not bound queueing or the whole
multi-request transaction. Propose Pod and execution caps only after measuring
memory/CPU/storage/provider limits alongside DB occupancy, with PM/user decision.
No numeric limit or operational enablement is selected by this design.

## References

- [Kubernetes RBAC](https://kubernetes.io/docs/reference/access-authn-authz/rbac/): named-resource permissions and create restrictions.
- [Validating Admission Policy](https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/): policy failure handling and binding enforcement actions.
- [NetworkPolicy](https://kubernetes.io/docs/concepts/services-networking/network-policies/): additive policies, supporting network plugin requirement and enforcement limitations.

## Preparation bundle and provider dependency

The separately reviewable `user-runner-isolated/README.md` and three JSON templates
provide namespace/zero-Pod bootstrap quota/default-deny/service accounts, an
explicit static-PV claim path, and an immutable synthetic transport Secret.
They require concrete target/storage/Secret inputs and user authorization before
PM applies anything. PM has now recorded k3s1.36.4/Cilium1.20.2 baseline;
CEL VAP v1 is the proposed admission path. Retain StorageClass provisioning is
also under review with a separate first-consumer initialization dependency.
See USER-RUNNER-BASELINE-2026-10-15.md. No controller grants or runner workload
are included in these templates.

Supported operator ChatGPT authentication refresh remains an unresolved prerequisite
for provider egress design. Keep provider routes closed until that investigation
and the resulting route are reviewed. Backend's load fixtures run only in isolated
Docker; PM/review assess recorded results, not an unapproved operating-cluster run.
