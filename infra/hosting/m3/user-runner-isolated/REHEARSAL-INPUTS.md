# Fixed non-secret inputs for the first rehearsal

These values select the user-authorized synthetic storage/foundation rehearsal.
They are not an execution approval; use the reviewed exact render and record its
SHA/digest before PM creates resources. If a name/path already exists, stop and
investigate rather than adopting it or silently generating another identity.

| Input | Fixed value |
|---|---|
| RUNNER_TEST_NAMESPACE | codex-runner-rehearsal |
| Node hostname label | anxious-lookout |
| RUNNER_SYNTHETIC_OWNER | d888737c-4830-4761-9630-b400982f8618 |
| RUNNER_SYNTHETIC_STATE / RUNNER_STATE_UUID | a9617ace-fa4b-4bbc-b70b-2188f0efe3e6 |
| RUNNER_NATIVE_PV | codex-runner-rehearsal-native-a9617ace |
| RUNNER_CONTROL_PV | codex-runner-rehearsal-control-a9617ace |
| RUNNER_NATIVE_SIZE | 1Gi |
| RUNNER_CONTROL_SIZE | 1Gi |
| RUNNER_STATIC_STORAGE_CLASS | empty string, not omitted |
| RUNNER_APPROVED_ACCESS_MODE | ReadWriteOnce |
| Native PVC | native-synthetic |
| Control PVC | control-synthetic |
| Transport Secret / RUNNER_TRANSPORT_SECRET | transport-synthetic |
| Controller / workload SA | runner-controller / runner-workload |
| PSA enforce/audit/warn version | v1.36 (already literal in foundation) |
| Initial generation / RUNNER_GENERATION | 1 (later dedicated test, not a create grant) |
| Deterministic Pod / RUNNER_POD_NAME | runner-d888737c483047619630b400982f8618 |

The two UUIDs were freshly generated with the host kernel UUID facility for this
source artifact. They identify synthetic state, not existing GitHub/service users.
Reuse these same values across reviewed renders and the later disposable test DB;
do not regenerate on retry or reset the one-shot marker. Any replacement after a
collision/uncertain attempt requires a new reviewed rehearsal plan, not an implicit
second create. This input file grants no operating account access.

1Gi per volume is a small synthetic declared storage request, consistent with the
recorded trial sizes, not a capacity benchmark or actual filesystem quota. PM
reported47Gi free on xfs/noquota; recheck free space before preparation. No resource
sizing or global controller capacity is selected. Static local PV and PVC quantities
must match exactly and both resources must explicitly have empty storage class.

The exact fresh directories are:

- /var/lib/anxious-lookout/runner-rehearsal/a9617ace-fa4b-4bbc-b70b-2188f0efe3e6/native
- /var/lib/anxious-lookout/runner-rehearsal/a9617ace-fa4b-4bbc-b70b-2188f0efe3e6/control

Apply STATIC-VOLUMES.md's ancestor, ownership, symlink, SELinux and stop-on-failure
checks before any mutation. PM reports semanage/restorecon/matchpathcon and the
container_file_t type are present and no existing dedicated fcontext rule was
found. That is a PM observation, not backend host verification; actual labels and
restricted access remain evidence to collect. No parent or existing volume is
relabelled by supplying these inputs.

## Secret input, PM private only

The only omitted Secret template value is RUNNER_PRIVATE_TRANSPORT_TOKEN_BASE64.
PM generates a fresh cryptographically random synthetic token (at least32 ASCII
non-whitespace characters), base64-encodes the exact bytes without an embedded
newline and supplies the value through the approved private file workflow. Do not
put it in source, shell arguments, comments, artifacts, environment dumps or logs.
Do not substitute any provider/ChatGPT credential. Secret name/type/immutable and
owner/state are fixed by the templates; its Kubernetes UID is recorded after
creation, never precomputed. No token is generated or stored by this document.

## Remaining render-only checks

Foundation has no Pod or grants and retains quota0/default deny. Split List files
into individually reviewed objects in the documented order; rendering is not
application. Inspect JSON escaping, unresolved placeholders, cluster-wide PV name
absence, namespace UID, path uniqueness, exact claimRef namespace/name and matching
PV/PVC class/mode/size. No UID goes into claimRef before the API assigns the PVC UID.
Private Secret render requires a redacted structural review without token output.

Placement, image digest, callback routes, CEL compile/defaulting evidence and
one-shot tool are not needed to select these storage names, but remain mandatory
before creating a runner. Do not derive executable runner/CNP/CEL renders solely
from this foundation input sheet. codex-trial remains untouched.
