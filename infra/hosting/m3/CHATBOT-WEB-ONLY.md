# Issue #9 UI refinement — proposed web-only operator supplement

Use only after reviewer approval of the exact operator supplement, Front web source
and its actual immutable archive/import chain. Front is still working; no new
source SHA/image is currently approved by this document. PM owns live execution.
This supplement does not reuse or relax the three-role maintenance/resume actions.
API/runner remain the approved e9f5943 images/specs and reconnected instances;
only the existing web Deployment image is replaced. No hold/restart/supply/rearm,
DB/native data read/backup/restore, windows, reservations or provider call is needed.

## Provenance and local artifact, after Front source approval

Record distinct source SHAs: API/runner=e9f59434997bd9ca376f72e83d12e93b36eb9dbe,
web=EXACT_APPROVED_FRONT_SHA, operator=EXACT_REVIEWED_TOOLS_SHA. Do not report that all
three deployed images have the new SHA. Backend inputs must be unchanged from the
approved application baseline; a backend diff requires a different scope/review.

Back creates a clean detached /tmp/issue9-chatbot-source-SUFFIX tree at that exact
approved Front SHA, then:

```sh
bash infra/hosting/m3/build_chatbot_web_only.sh EXACT_APPROVED_FRONT_SHA /tmp/issue9-chatbot-web-UNIQUE /tmp/issue9-chatbot-source-SUFFIX
```

The wrapper requires clean source/tools, ancestry from e9f5943 and no backend diff.
It builds only root-base-path linux/arm64 production web, preserving current web
Dockerfile/lockfile selection from the exact approved source. It records metadata,
inspect, archive/source/layers audit and a closed web-release.json containing only
webSourceSha/web. The previously approved API Docker image is used only as offline
Python stdlib auditor with no native startup/credentials. It builds/restarts no
API/runner, pushes/imports nothing and labels actual web source. build.json records
the distinct source/tool SHAs. Package/source/artifact review precedes PM import.

PM imports only the reviewed web archive through the existing reviewed import
procedure, then runs existing common_chatbot_image_audit.py **web** with its actual
web SHA, build directory, imported descriptor/config/CRI evidence and exact
imported web ref. If imported target differs, preserve local web-release.json and
create a separate imported-web-release.json from the measured imported target;
arm64 manifest/config/source must still match. API/runner exact refs remain pinned.
After rollout rerun audit with actual web Pod association and read /version.json.
The API /api/version must continue reporting e9f5943.

## Fresh baseline/reference and process preservation inputs

Use a fresh private0700 directory/files0600, set+x/umask077 and existing trusted
kubectl wrappers/context. Read metadata/spec/status only. No source auth or Secret
data is an input. Use the original reviewed/sealed baseline.json of the three-role
rollout: its old full specs/configs/storage are compiled and checked. The new tool
reconstructs the exact deployed e9f5943 specs from that sealed baseline; it does
not silently seal an unrelated current Deployment as approved.

snapshot.json has exactly these logical inputs:

- checkedAt: actual fresh capture UTC, within120s; refresh each render.
- objects: api/web/runner Deployment objects from fresh GETs.
- pods: api and runner Pod Lists, exactly one live Ready Pod each.
- references: a combined resource-object list (composition with jq, no new reader
  code). Required inputs are both immutable hosting ConfigMaps; metadata-only
  hosting-api, hosting-access-web-input-v1 and access-web-transport-v1 Secrets;
  trial-native/control PVCs and their existing Retain PVs; api/web/runner Services;
  the seven existing codex-trial/m2-hosting CiliumNetworkPolicies named in
  chatbot_web_only.REQUIRED_REFS. Additional relevant Services/policies can be
  included consistently to detect drift. Secret objects must contain only
  apiVersion/kind/metadata, with namespace/name/uid/resourceVersion metadata.

Use the existing reviewed metadata-only Secret capture mechanism; never save a
full Secret JSON and filter it afterward. Fixed projection for already safely
captured metadata is
`{apiVersion,kind,metadata:{namespace:.metadata.namespace,name:.metadata.name,uid:.metadata.uid,resourceVersion:.metadata.resourceVersion}}`.
Other reference objects retain metadata and full spec, ConfigMaps also data/
immutable, PVC/PV status for existing Bound/Retain/UID/node checks. References are
normalized/hash-compared by the tool; Secret UID/RV rotations are refused.
CiliumNetworkPolicy retains every declared spec/specs field without converting
between representations or dropping array rules/order. At least one is required;
spec must be a nonempty object, specs a nonempty list of nonempty objects. Both
may be present and are both digested. Empty/null/wrong types are refused even if
the other field is valid. A change to either representation or its contents/order
is reference drift. Do not transform the original policy input to make it pass.
Before initial seal, PM compares these actual inputs against reviewed post-rollout/
reconnection reference evidence. Unexpected drift is a handoff, not a new baseline.

known-pods.json is `{api:<reviewed API Pod List>,runner:<reviewed runner Pod List>}`
composed from the verified existing post-resume/reconnection Pod evidence.
It is not copied from an unknown new replacement just to pass a guard. Fresh
API/runner processes must match those recorded Pod UID/name/containerID/imageID,
startedAt/restartCount/ReplicaSet owner; exact approved Deployment specs and
readiness also must match. If a process legitimately changed, stop and obtain
review of that new baseline instead of adopting it automatically.

Initial offline emit-only capture (read-only /checks and private /inputs):

```text
python -B /checks/chatbot_web_only.py seal --baseline /inputs/baseline.json
  --snapshot /inputs/snapshot.json --known-pods /inputs/known-pods.json
```

Redirect to preserved.json and keep it unchanged. This proves container/Pod
continuity and reference equality, not continuous identity of a native child PID
or provider entitlement. PM retains original inputs/Pod associations for review.

## Upgrade and recovery

From fresh inputs, render with the exact approved Front source argument:

```text
python -B /checks/chatbot_web_only.py upgrade --baseline /inputs/baseline.json
  --snapshot /inputs/snapshot.json --preserved /inputs/preserved.json
  --release /inputs/imported-web-release.json --approved-web-sha EXACT_APPROVED_FRONT_SHA
```

The output targets only m2-hosting/web and tests exact Deployment UID/current RV/
full spec before replacing the spec whose sole diff is the web image. Closed
release shape refuses API/runner entries, wrong source/repository/tag and drift.
Use the reviewed jq .patch extraction and existing server dry-run/apply procedure.
PM checks the exact diff and rechecks preserved API/runner/references immediately
before applying. A stale RV or unapproved drift requires new capture/review,
never force. Multi-resource snapshots are not atomic: the web JSON Patch cannot
atomically test the other resources. Before/after checks must detect external
concurrent changes and stop/handoff; do not claim a cross-resource transaction.

After web rollout, verify web Ready/version/root origin/base path, audited Pod
chain and Front acceptance without sending a question. Recollect snapshot and
render rollback (without applying) to check the preserved API/runner/reference
invariants still hold with the new web spec. Read existing reviewed local health/
owner status before/after; auth expiry/budget changes may occur naturally and are
reported separately, never automatically supplied/rearmed for this UI operation.
Retain actual Pod/start/restart/ref comparison evidence. No DB/PVC/native reader or
old maintenance preservation anchor is used by this supplement.

If web fails or UI regresses, render **rollback** with the same inputs/source:

```text
python -B /checks/chatbot_web_only.py rollback --baseline /inputs/baseline.json
  --snapshot /inputs/snapshot.json --preserved /inputs/preserved.json
  --release /inputs/imported-web-release.json --approved-web-sha EXACT_APPROVED_FRONT_SHA
```

Rollback permits an unhealthy new web but still requires preserved Ready API/
runner and exact new web spec/UID/RV. It changes only web image to the approved
e9f5943 web pin, without invalidating sessions or restoring older data/ledgers.
Dry-run/review/apply that exact patch, then verify old web Ready/version and the
same API/runner/container/reference checks. Unrelated drift refuses recovery;
report it to back rather than applying a broad replacement or restarting native.
