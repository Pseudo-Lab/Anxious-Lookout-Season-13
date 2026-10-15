# Plan to admission envelope audit — gate retained

Follow-up implementation: [gate-removal candidate](GATE-REMOVAL-CANDIDATE.md)
and [generated synthetic disposition rows](schema/dispositions.synthetic.json).
The historical gaps below are addressed by that separately reviewed generator;
this original draft gate remains unchanged. Synthetic evidence is not live proof.

Scope: Plan.manifest and KubePods._pod at 0e143a0 versus the candidate validations
in admission.json.in. This is a review matrix, not complete CEL implementation.
All findings below are masked by the intentional false draft gate, which is
unchanged. No gate-removal patch or cluster execution accompanies this audit.

## Every field emitted by Plan

Candidate means a textual expression exists, not compiled/typed/tested CEL.
A repeated list's cardinality, unique keys and element contents must all match;
checking one element with exists is not exact equality. API schema constraints
must not be confused with validations this policy itself proves.

| Plan fields (including nested members) | Current candidate coverage | Required before gate removal |
|---|---|---|
| apiVersion, kind | Pod/v1 resource rule, no value comparison | Validate actual resource/schema mapping and reject wrong request versions before comparing |
| metadata name, namespace | Exact placeholders | Canonical owner-derived runner name and namespace validated in renderer |
| labels managed/owner/state/generation | Four labels/exact values | Validate UUIDs/generation and exact labels after mutation; negative missing/extra labels |
| serviceAccountName, automountServiceAccountToken | Exact workload SA/false | Validate deprecated alias/defaults and forbid token injection |
| restartPolicy=Never, terminationGracePeriodSeconds=55 | Missing | Exact values, reject shortened/extended grace and restart behavior |
| hostNetwork/hostPID/hostIPC=false | Candidate false/absent | Validate nil/false semantics and forbid other namespace-sharing overrides |
| enableServiceLinks=false, dnsPolicy=ClusterFirst | Missing | Exact values; no alternate dnsConfig/resolver/hostAliases |
| pod security runAsUser/runAsGroup=10001, runAsNonRoot=true, seccompProfile.type=RuntimeDefault | Candidate selected members | Forbid supplementalGroups/fsGroup/sysctls/SELinux/AppArmor/seccomp profile overrides not present in Plan; distinguish unsupported fields from safe defaults |
| containers: exactly one name=runner, digest image, imagePullPolicy=IfNotPresent | Count/name/image only | Exact pull policy and reviewed literal digest, no image substitution |
| command: uvicorn, runner.app:create_app, --factory, --host 0.0.0.0, --port 8080, --workers 1, --no-access-log, --no-proxy-headers | Missing | Exact ordered command; args absent/empty, no workingDir/lifecycle/probes/stdin/tty override |
| env APP_ENV=operator-service; RUNNER_ACCOUNT_ID/STATE_ID; RUNNER_STATE_DIR=/state; RUNNER_TOKEN_FILE=/transport/token | Missing | Exact name/value set, no duplicates or extra env; no valueFrom on literal entries |
| env CODEX_MODEL=gpt-6.1-sol; CODEX_AUTH_MODE=access-service; CODEX_EXECUTION_SCOPE=operator-access-service | Missing | Exact literals; no envFrom or auth mode override |
| env CODEX_ACCESS_CONTROL_DIR=/control; CODEX_OPERATOR_ACCOUNT_FILE=/control/operator-account; RESEARCH_TOOL_CALLBACK_URL=reviewed callback | Missing | Exact paths/HTTPS callback; validate URL separately, no credentials/query/fragment |
| env RUNNER_POD_UID via valueFrom.fieldRef(apiVersion=v1, fieldPath=metadata.uid) | Missing | Only this fieldRef, no literal UID, Secret/resourceFieldRef/configMap alternative |
| ports: name=runner, containerPort=8080, protocol=TCP | Missing | Single exact port, no hostIP/hostPort override |
| container security allowPrivilegeEscalation=false, readOnlyRootFilesystem=true, capabilities.drop=[ALL] | Candidate plus add empty/privileged false | Prevent container runAsUser/group/root/seccomp/SELinux override of pod context; enumerate all v1.36 security members |
| mounts native→/state, control→/control, transport→/transport readOnly=true, tmp→/tmp | Missing | Exact4 mounts, no volumeDevices/subPath/subPathExpr/mountPropagation/recursive read-only or alternate mounts; determine absent/readOnly=false serialization |
| volumes native/control with exact PVC claimName | Name/type/claim exists | Exact two claims, reject source extras/readOnly changes, no duplicate/missing volumes |
| transport Secret secretName, defaultMode=292, items=[{key:token,path:token}] | Name/source only | Exact mode/items, optional absent/false, no alternate item mode/path/key |
| tmp emptyDir medium=Memory, sizeLimit=64Mi | Name/source only | Exact medium and semantically exact quantity; no disk-backed/large/unbounded emptyDir |

Other volume sources, extra containers and projected/hostPath volumes are partly
constrained by the candidate; this is not yet an exhaustive schema-field denylist.
The adapter's final dictionary comparison rejects extra spec members after its
small normalization list. CEL sees typed Kubernetes objects: do not assume it can
iterate object keys as if they were maps. A v1.36 schema-member inventory plus
explicit absence/default checks or a proven typed-object comparison strategy is
required. New API fields and feature gates must trigger a fresh review.

## Fields not emitted by Plan but material to the boundary

| Field group | Current behavior / proposed admission constraint |
|---|---|
| metadata annotations | Adapter refuses nonempty; candidate does not check. Require absent/empty before creation, including network/admission annotation tricks |
| ownerReferences/finalizers | Candidate empty checks; adapter rejects ownership/deletion and finalizers. Server UID/resourceVersion/timestamps/managedFields are evidence metadata, not caller-authorized execution overrides |
| resources | Plan omits container and Pod-level resource declarations. Adapter permits only empty container resources map. Candidate missing. Do not authorize limits/requests/claims by omission; resource sizing needs an explicit Plan change and measured review |
| scheduling/runtime | nodeSelector/affinity/runtimeClassName/priorityClassName/schedulingGates/topologySpreadConstraints/resourceClaims/overhead not in Plan; require absence unless explicitly reviewed |
| nodeName | Adapter removes it on lookup as scheduling metadata. That does not authorize client-selected nodes at CREATE. Deny creator-supplied nodeName; distinguish scheduler binding from later UPDATE, and do not grant controller pods/binding |
| schedulerName/priority/preemptionPolicy | Adapter permits default-scheduler/0/PreemptLowerPriority. Confirm admission output and any global default PriorityClass; do not silently authorize other values |
| tolerations | Adapter permits <=2 entries chosen from not-ready/unreachable NoExecute Exists300. It does not enforce uniqueness. Future admission should require the exact observed unique permitted set; duplicate allow must not be copied automatically |
| termination message | Adapter normalizes /dev/termination-log and File. Reject alternative path/policy; verify defaults before whitelist |
| controller/initializer Pod | Namespace-wide false gate also rejects these. Their placement and any separate policy are a distinct design; do not exempt by a forgeable Pod label or weaken runner envelope |

## v1.36.4 source defaults versus actual observations

These are **upstream source expectations**, not PM cluster measurements. K3s
configuration, feature gates and admission plugins may differ. No stored or
server-dry-run runner object has been obtained in this audit.

Core defaults source identifies default-scheduler and termination-message path/
policy additions. Plan already specifies restart, DNS, grace, enableServiceLinks
and Secret mode; their defaults should not override explicit values. Limits may
induce resource requests, so resource mutation needs review rather than a blanket
empty-map normalization. [Core v1.36.4 defaults](https://github.com/kubernetes/kubernetes/blob/v1.36.4/pkg/apis/core/v1/defaults.go).

DefaultTolerationSeconds can insert not-ready/unreachable NoExecute tolerations;
its flag defaults are300seconds but can be configured. Require actual output
before accepting that pair in a final policy. [Admission source](https://github.com/kubernetes/kubernetes/blob/v1.36.4/plugin/pkg/admission/defaulttolerationseconds/admission.go).

Priority admission derives priority/preemption behavior from PriorityClass/default
configuration. Adapter's 0/PreemptLowerPriority assumptions are not evidence of
this cluster's result. [Priority admission](https://github.com/kubernetes/kubernetes/blob/v1.36.4/plugin/pkg/admission/priority/admission.go).

The serviceAccount alias, empty resources map, nodeName, UID, resourceVersion,
creationTimestamp and managedFields also require stage classification (conversion/
serialization/admission/scheduler/server metadata). They are not all core default
functions. Record omission versus empty/zero explicitly in actual captured objects.
Do not accept newly observed annotations, projected tokens, imagePullSecrets or
LimitRange/resource mutations without reconciling both admission and adapter.

## Evidence needed for a separate gate-removal proposal

1. Resolve approved literal inputs; generate Plan and typed schema inventory with
   no secrets. Cross-check every row above and every unrecognized v1.36 field.
2. Implement complete CEL checks with literal escaping and matching API types.
   Compile/type-check separately from the false gate; reject unknown/missing
   inputs and capture per-expression results for mutations. An unconditional
   false result proves nothing about the other expressions.
3. Obtain PM-authorized server-defaulted evidence in a reviewed disposable test
   arrangement. Current quota0 and draft gate can prevent this; do not bypass
   either. The test plan must specify its own authorization and observation path.
4. Compare candidate accepted object with KubePods._pod, including defaults and
   scheduling transitions. Prove positive acceptance and named negative checks;
   separate RBAC/quota/PSA/schema denials from CEL results.
5. Submit gate-removal as its own reviewed source change after full evidence is
   available. Do not remove the gate now, even in the proposed rendered artifact.

## Separate placement work

Keep controller/initializer placement, DB egress, callback destination ingress,
resolver search names and Cilium API entity observations out of envelope exceptions.
PM reported kube-system/k8s-app=kube-dns, DNS10.43.0.10 TCP/UDP53 and API10.0.0.107:
6443 as candidate paths. These do not verify the API certificate IP SAN or prove
Cilium policy identity/traffic. No placement or CNP changes are made here.
