# Literal-owner admission and Cilium route drafts

User scope/storage decisions are now recorded in
[Issue10 scope](../USER-RUNNER-ISSUE10-SCOPE.md), superseding pending-decision
wording below. These drafts still require complete envelope and render review.

These are source drafts, not approved rendered manifests. Do not apply them to
codex-trial or any cluster without exact render review. Namespace/storage/Secret
decisions are recorded in the scope document; placement A is now user-approved.
No runtime, grants, capacity, quota or provider access is enabled by this commit.
The foundation's default deny and quota0 remain unchanged.

## Admission draft

admission.json.in contains two VAPs and their Deny bindings. Both use Fail and the
immutable namespace-name label, not an objectSelector. The main policy handles
CREATE/UPDATE of pods; the second unconditionally denies ephemeralcontainers
UPDATE and exec/attach/portforward CONNECT. Neither matches pods/status, so the
creator restriction does not apply to kubelet status writes. Policies/bindings
are cluster-scoped even though matches are namespace-scoped. The controller and
workload receive no edit rights to them. No parameter resource is used; introducing
one requires a new review and parameterNotFoundAction Deny.

The **main policy intentionally contains a literal false draft gate**. It cannot
admit even the expected Pod. Do not remove that gate in a renderer or an operator
command. It stays until a follow-up source review supplies the complete template
envelope and validates it against Kubernetes v1.36 defaulting. The listed identity,
image, volume and privilege checks are concrete candidate CEL, not proof that
command/env/callback/mount/security overrides and all extra fields are excluded.
Missing fields/errors fail closed but are not an acceptable substitute for clean
schema/type-checking. Exact env including downward UID, command/args, ports,
volumeMounts/subpaths, Secret item mode, emptyDir limits, resource defaults,
annotations, scheduling/runtimeClass and container security overrides still need
an exhaustive comparison with runner_kube.Plan. Therefore this draft is not the
final admission implementation or a usable positive creation path.

Placeholder values embedded in CEL need **CEL literal escaping as well as JSON
encoding**; plain string substitution is not a renderer. Reject namespace/name
syntax violations, noncanonical UUIDs/generation, non-digest images and unresolved
inputs. No secret contents appear in the policy. The actual controller identity
assumes the two SAs from the preparation bundle share the chosen namespace;
changing controller placement requires another render review.

## Network route drafts

network.json.in provides three CNPs for Cilium1.20.2:

- Controller: egress to kube-apiserver entity on the approved API port and to the
  runner-workload SA on TCP8080; no inbound listener allowance.
- Runner transport/callback: selected A permits inbound TCP8080 only from the
  local host entity to the runner-workload SA selector; outbound to the approved
  callback namespace/SA/port. The previous controller-SA ingress peer is replaced,
  not retained, so nonhost controller Pods have no runner ingress grant here.
- Resolver: runner DNS only to the reviewed resolver namespace/app identity,
  TCP/UDP53 with one exact callback DNS name. No wildcard name or provider FQDN.

Provider remains closed: no provider/world/FQDN allow entry. The standard
foundation deny plus these additive rules must be reviewed against all inherited
KNP/CNP/CCNP rules. Absence here does not prove another policy cannot permit it.
The kube-apiserver entity is used because node addresses are not ordinarily
selected by CIDR rules. Verify the configured literal IP, actual Cilium entity
identity, service translation and port; keep TLS IP SAN checks independent.
The host ingress exception is limited to the user-approved A transport test;
do not add host/world egress when an expected API route fails. The retained
controller-SA egress rule is inactive for the host-network diagnostic caller and
does not constrain host traffic. B placement is deferred to Issue11. See
[one-shot procedure](ONE-SHOT-PROCEDURE.md) for the host trust boundary and probes.

This is not a complete controller/API service deployment network: DB connectivity,
callback destination ingress and actual component placement remain unresolved.
No broad DB or API inbound exception is introduced to fill that gap. Callback
scheme/port/certificate must match the reviewed Plan. If ClusterFirst resolution
uses search suffix queries, enumerate each required exact name after observation;
do not change the DNS policy to `*`. Existing resolver labels must be confirmed
by PM. Application-level auth is still necessary even on allowed network paths.

## Admission negative-test acceptance matrix (future execution)

| Case | Required observation after full envelope review |
|---|---|
| Expected Plan and creator | Positive allowed request with no CEL evaluation warning |
| Wrong namespace scope | Policy does not match an unrelated namespace; test only in a separate authorized disposable namespace |
| Wrong Pod name/owner/state/generation/image | Named policy and appropriate validation message reject |
| Creator/SA changed | Identity check rejects independently of RBAC |
| Add/change labels, ownerRef/finalizer/annotation | Template boundary rejects; Pod labels cannot escape namespace match |
| Wrong PVC/Secret, projected token, hostPath | Volume boundary rejects |
| Extra normal/init/ephemeral container | Relevant main/subresource policy rejects |
| Env/command/args/callback/mount/subPath override | Completed envelope rejects; currently a documented missing check behind gate |
| Privileged/host network/token automount/security override | Reject; distinguish PSA from CEL evidence |
| CONNECT exec/attach/portforward | Named subresource policy rejects authorized test caller |
| Kubelet status update | Neither policy matches; actual authorized status write succeeds |
| Controller/workload edits policy/binding | RBAC denies; no policy-edit privilege granted |
| Missing parameter (if later introduced) | Fail/Deny rejects, never an allow fallback |

Run schema/type-check first and inspect policy status warnings; no warnings may be
ignored. First record the unchanged bootstrap RBAC/quota0 refusal as a **bootstrap
control**, not CEL success. The present draft gate proves only unconditional
refusal, not any of the individual checks or the positive baseline.

Only after a follow-up envelope review and separate authorization, use a disposable
policy-test target with a bounded quota and narrowly authorized test identity so
that intended requests reach CEL. Record request verb/resource/subresource,
identity, policy/binding names, validation message and result. A generic403,
quota refusal, PSA refusal, schema rejection or auth can-i result is **not** CEL
negative evidence. For dangerous cases shadow-evaluate exact expressions against
v1.36 typed objects before server tests; do not relax PSA simply to manufacture
CEL evidence. Record PSA-blocked cases as such. Dry-run does not persist a Pod but
still exercises server admission; do not assume it bypasses quota/RBAC.

For positive/negative network tests use later approved synthetic endpoints and
record Cilium policy revision/endpoint identity plus observed flows: controller→API,
controller→runner, runner→callback and exact DNS succeed; other SAs/namespaces,
other ports, direct-IP provider, unrelated names, metadata and runner→API fail.
Test both DNS transports and search-suffix handling. Quota0 currently prevents
these test Pods; no traffic tests are claimed in this source review.

After every test, retain private evidence, restore the reviewed bootstrap
restrictions under PM's plan, and never infer old-process termination or accepted
create drain from denial of new requests. No slot release is provided here.

References: [VAP](https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/),
[Cilium entity policies](https://docs.cilium.io/en/stable/security/policy/layer3/),
[Cilium DNS policies](https://docs.cilium.io/en/stable/security/dns/).
