# Issue #7: resolve execution-placement authority before another probe

This is a preparation-only re-scope. It authorizes no new observation, query,
placement change or operator command. The consumed4e0afc8 runtime capture
and9d2761e client-selection observation remain consumed. Accepted selected
runtime is runc; selected client transport is unix. Neither supplies a trusted
mapping of the tool executor or Docker daemon to the retained Node.

## Explicit decision and useful evidence

Decision: can a trusted native operator route on the retained Node, using an
authoritatively mapped Docker execution context, be justified for a separately
reviewed unchanged-guard candidate? Or must that candidate remain held because
the launch/namespace authority is missing?

Required evidence is an existing administrative runtime/deployment launch
record, exported or attested by its responsible platform/runtime operator.
Another inode/offset/transport/hostname match from inside the same opaque tool
environment cannot answer this decision. Do not manufacture provenance from
a file named "attestation", a digest, a self-declared node-host label or a
PM command that merely omitted an explicit wrapper.

PM should establish whether such records and their responsible owner are
available under existing authorized access. Backend will not contact another
person/service or invent an administrative API/SSH endpoint. If the records
are unavailable, record that limitation and hold the native-operator candidate;
do not start another clock-capture cycle to compensate.

## Exact proposed input scope

After independent scope review, consider only one provided private JSON
export, at /private/issue7-placement-records/attestation.json, maximum16KiB,
mode0600 in a caller-owned0700 directory, regular file with no symlink. The
real path and the export's digest must be pinned before any future replay.
Only backend/reviewer offline Docker validation is proposed; no automatic
live collection command exists here. Missing/unverifiable provenance is unknown.

The administrative export must contain only these bounded metadata fields:

| Field | Required meaning |
| --- | --- |
| authorityReference | Responsible runtime/platform owner and verifiable existing source-record reference; no token/key/credential |
| invocationBinding | Source SHA plus existing tool-execution/run reference binding the export to the consumed PM launch record; not just a nearby timestamp |
| expectedNodeReference | Retained Node name/UID pinned by the already approved numeric seed |
| brokerNodeReference | Administrative mapping of that invocation's executor to Node name/UID, or explicitly unknown |
| brokerExecutionBoundary | native_node / managed_environment / unknown, supported by the launch record |
| brokerNamespaceIdentityView | kernel_native / mediated / unknown, supported by runtime configuration provenance; not inferred from inode strings |
| daemonBindingReference | Administrative mapping of the invoking client's selected engine to its deployment record, without socket/context/endpoint values |
| daemonNodeReference | Authoritative engine-to-Node mapping, or explicitly unknown |
| nativeOperatorRoute | available / unavailable / unknown plus an existing trusted route reference; no guessed command, address or credential |
| recordValidity | Existing event/resource version/time coverage supporting these bindings; historical records do not prove current placement |

If the runtime controller and client observation use different invocation
references, the source record must bind each relevant event explicitly. PM's
"same launch context" statement alone cannot collapse them into one identity.
Keep raw Node/run/engine references private; relay only conclusions and paths.
No environment/config dump, process/PID/container enumeration, certificate or
credential content, cgroup/mount contents or new daemon/Kubernetes query is
part of this input scope. Source-record review must verify the issuer/binding
through an already trusted administrative channel; JSON schema validation or
hash equality alone cannot establish authority.

## Outcome matrix

| Authoritative evidence | Decision |
| --- | --- |
| Origin, invocation/engine binding or Node mapping unavailable | Candidate held; physical placement unknown; no new probe |
| Both mapped to retained Node, broker already native/kernel-native | Direct-node relocation is unsupported; re-scope the identity-view/guard model separately, without relaxing it |
| Managed/mediated broker boundary, daemon mapped to retained Node, trusted native route available | Prepare one concrete placement candidate with exact route/source/argv/security review; do not execute it |
| Daemon mapped elsewhere or target Node differs | Existing actor-local target conditions unresolved; no retarget/remote command inferred |
| Native route unavailable or namespace view unknown | Candidate held even if physical Node names match |

This separates physical Node mapping, execution boundary and namespace
identity view. Same Node is not exact time-namespace equality; a managed
boundary is not proof that an alternate route fixes it. No conditional branch
automatically authorizes a clock/actor/numeric call.

## Verification requirements and limits

Future offline input validation must reject untrusted issuer, missing event/
engine binding, wrong Node UID, conflicting or stale record, inferred locality
from unix alone, timestamp-only association, unproved namespace-view label,
missing route and extra secret/config fields. Values stay bounded and private;
unknown cannot become a guessed native route. These are proposed checks, not
dynamic tests already performed for this documentation-only re-scope.

A useful administrative record can select which preparation branch applies.
It cannot itself satisfy the unchanged boot/namespace/250ms actor guards. A
later candidate still requires exact-source/argv review, original controller
protections, one explicitly disposed clock-only test and separate refreshed
numeric/actor authorization if appropriate. No runtime install/switch, daemon/
K3s restart, setns/unshare, host PID/network, namespace mount, clock bridge,
guard relaxation, API-enable/auth/state/PVC-content/Native/turn or final PR/
publication is included. Existing authorization covers this preparation;
there is no renewed routine user-permission request.
