# Existing root HTTPS → login-owner runner package (#7)

Runtime source: approved `70fbad43084d1b7dd435c83d9ad3d6ffc2106e63`.
This later infra checkpoint does not change runtime source or rebuild webedff.
Tools emit JSON only; PM executes after independent package review. Actual owner,
source file, provider inventory/grant, import and enable remain separate evidence.
Public posting and final PR remain held.

## Exact images and retained baseline

| Role | OCI index / Docker ID | arm64 manifest | config |
|---|---|---|---|
| uid-api | `5a2a95d93810bcfc035bb883d1096376d9f2cf6f48fc99f88f1f8c308c308566` | `d83ea9cfcb908001144dccd8407161394d649e40854f214dabdc1922e854b3c7` | `061d705b7b0aa2e00449c25c7dfaf4ccbe969cc71cad4757a12de32f23d489a9` |
| uid-runner | `6fc02fa74a1b61b51301d57b3a54a263744e020dc1052d5070ada73bfdc33f87` | `5e2bf636e42b5fa36be7a58659d6d2501a3f6502b24925b3ff40d6908d78db0e` | `310bd514ff0b31e3a6b610fff779f972aa054c352852eefa77da01804d96afc9` |

All digests use `sha256:`. References are respectively
`docker.io/library/anxious-s13-uid-api-70fbad4@sha256:<index>` and
`docker.io/library/anxious-s13-uid-runner-70fbad4@sha256:<index>`.
BuiltAt API `2026-10-10T11:10:43Z`, runner `2026-10-10T11:10:45Z`.
Both bake release SHA70 and approved source hashes; no tests/dummy adapter in runtime.
Native0.160.1 installed SHA256
`fbbaec80443919f86dd63648a0b62759cf6f1d0e09310602fde96885e0bceb3e`.
Baked binding checks execute Python only; no vendor/provider startup.

Keep original hosting API Deployment UID, root webedff, Services, HTTPS/HTTP routes,
TLS generation/encoding, GitHub credentials/session key, hosting DB/auth0001 and
research0004, original Secret UIDs/RVs, PVCs and original hosting/trial policies.
No new DB, OAuth app, ingress or RBAC. `owner_prepare.py` seals the approved Retain
claim/PV UIDs, exact claimRefs/Bound/RWO/node pair; it never creates storage.
A replacement claim or mismatched state is refused. Reuse the completed PV proof.

## Private operator inputs and emit interface

Capture fresh GET `m2-hosting/api` Deployment and active immutable
`m2-hosting/hosting-root-https-v1` ConfigMap, plus metadata-only trial-native/control
PVCs and their PVs. Snapshot JSON has exactly the relevant `api`, `config`, `pvcs`
(Kubernetes List) and `pvs` (List). No Secret values/TLS key in this snapshot.
Keep original snapshot unchanged for all four guarded API actions; current GET is
provided separately. Save inputs and output privately, no shell tracing/console dump.

Owner selection uses the user's **existing** GitHub login, not a guessed UUID:
execute reviewed `owner_lookup.py` as stdin Python source inside the current API Pod,
with separate private stdin JSON `{"githubLogin":"<selected-login>"}`. The tool uses
its existing Settings/DB credentials, starts a READ ONLY transaction with 5s query /
2s lock limits, and accepts exactly one existing approved editor/admin. No writes,
promotion or account creation. Redirect its ownerId result to a private file; compare
to the authenticated browser accountId privately. Never select the provider opaque
account ID as this owner UUID. This tool is infra, not baked into the old/live image.

Private renderer inputs:

```json
{"ownerId":"<lookup-account-uuid>","stateId":"<one-durable-storage-uuid>","providerHosts":["<reviewed-exact-dns-name>"],"procedureRef":"<reviewed-procedure-reference>"}
```

Provider names/procedureRef are operator-reviewed facts, not discovery/defaults or
wildcards. `.invalid` examples in tests cannot be used as actual routing. Retain the
same owner/state for every later render/replacement. New transport token is private
random ≥32 characters, distinct from provider credentials, copied identically to two
new Secrets only. Original Secrets remain unchanged:

- `m2-hosting/hosting-owner-runner-input-v1`: keys `runners.json` (emit `api-input`), `transport-token`.
- `codex-trial/owner-runner-transport-v1`: key `transport-token`.

Create via kubectl `--from-file`, never literal credentials/command-line tokens;
use `--dry-run=client -o json` redirected privately and server dry-run before apply.
Inventory existing names first: collisions refuse; don't overwrite a prior generation.
Secret metadata/provenance plus consistent same-token private check belong in evidence.

Run every Python emit/lookup/test via Docker or the intended Pod container. Example
container command with private files mounted, reader Unix UID matching those files:

```sh
python /checks/owner_prepare.py upgrade --snapshot /private/snapshot.json --current /private/current-api.json
python /checks/owner_prepare.py config --snapshot /private/snapshot.json --inputs /private/inputs.json
```

All outputs go to private files. `upgrade`, `rollback-upgrade`, `enable`, `disable`
are JSON Patch arrays with current raw UID/RV/spec tests. Only Kubernetes empty
EnvVar omission is normalized for comparison; current raw spec remains the guard.
Every other drift/replacement refuses. Don't remove guards or rebase live drift in
PM worktree. Other parts emit Kubernetes objects except `api-input` emits mapping.

## Ordered PM operations

1. Import both reviewed archives into target K3s containerd using prior approved
   import procedure. Retain local descriptor bytes, imported exact ref target /
   arm64 manifest / config and CRI status. Audit `uid-api` / `uid-runner` sealed roles:
   `python /checks/image_audit.py uid-api <exact-ref> <private-audit-directory>`.
   No import or CRI results are claimed by local archive checks. If import produces
   another index, preserve evidence and stop; do not silently change pinned refs.
2. Refresh metadata-only baseline, compare original HTTPS/data boundaries. Emit
   `upgrade`, server dry-run exact guarded patch, then reviewed native-disabled API
   rollout. This stage needs no owner/grant/storage writes. Verify new Ready API
   Pod→CRI association with `uid-api`, release70 and original HTTPS/nativeoff flags.
   Root web/routes/TLS/DB/PVC/Secrets stay unchanged. Emit/server dry-run
   `rollback-upgrade` from its actual GET to verify API8fa recovery before enabling.
3. Lookup owner as above; retain one state UUID. Dry-run all generated owner objects,
   inspect the resolved input/diff for review. Check source/grant lifetime and exact
   provider DNS/private-use support before source supply **or runner startup**.
   Metadata exit0 is not authenticated/admitted. Existing login/profile and whether
   another CLI/IDE uses it are minimum owner facts; PM establishes authoritative
   latest-cache ownership/retention/host continuity privately. Do not create another
   login, discover HOME, stop original clients or mark declarations true to pass.
4. First-use only: apply `init-policy` (explicit both-direction deny), ensure no
   native/control consumers, then apply `init` Job. The API image has no vendor CLI;
   Job mounts native PVC only, initializes empty `/native/private` account/state
   markers as10001, no auth/control/token/SA/network. Existing/partial/populated root
   refuses. Remove only completed owned Job/Pod, retaining markers; never replay init
   after binding. Exact input/render and explicit first-use readiness are prerequisites.
5. Source supply requires the separately reviewed exact selected source handoff to
   `/control/private` (grant/cache) with10001/0700/0600, original source untouched,
   max3 and original expiry≤1h. Use existing reviewed source procedure only if it
   actually fits selected source; missing source facts are unresolved, not permission
   to copy a cache. No generic source-discovery/copy command is provided here. Private
   validation (`python -m runner.auth --control /control/private --validate`) does not
   start vendor or prove no external writers. Preserve ledger/projection/cache across
   replacement; no fresh trial/cache/expiry to refill.
6. Apply `policy`, transport Secret, `service`, then reviewed `runner` Deployment.
   `hosting-owner-runner` label avoids the existing trial-runner deny selector;
   `owner-state-init` has its own deny. Namespace-qualified API↔runner8080, exact DNS
  53 and provider FQDN443 only on runner. Original hosting API broad public443 egress
   remains; additive link does **not** narrow that existing policy. Check actual
   Cilium status/rule acceptance/effective selectors and denied unrelated/public
   runner paths before admission. Upstream sanitizer pass is not live dataplane proof.
7. Runner is one Recreate Pod, verified Retain pair/node, no public Service, tokenless
   SA trial,10001/read-only/cap-drop/seccomp/tmp,55s grace. Native starts during lifespan
   and can authenticate/refresh before a turn: step3/5 must already pass. No liveness
   restart loop. Readiness probes internal bearer/state `/health`, no dispatch; refuses
   unavailable/wrong binding. Verify actual Ready Pod/CRI with `uid-runner`, Downward
   Pod UID, marker/mapping state and owned PVC; model/access remains untested until
   explicit acceptance. Save health privately, never token/cache.
8. Create reviewed immutable `config` and API input Secret, emit/server dry-run
   `enable` from fresh upgraded-nativeoff GET, apply guarded patch and verify owner
   status/exact-origin/root opt-in. Immediately emit/server dry-run `disable` from
   actual GET. Recheck original auth/HTTPS/data/Secret identities. Browser contract
   unchanged; no frontend source/rebuild needed.
9. Execute the two-turn plan in `backend/USER-UID-CONNECTION.md`, total ceiling3.
   First explicit owner save/read, then no-auto-resend reconnect/replay. Close admission
   before clean replacement; preserve original grant/ledger and same state/PVC/thread,
   confirm old process gone/nativeActive=false/latest control then new Pod UID and
   same-thread follow-up. Wrong state/thread/user and model/auth/unknown turn status
   stop; don't reset flags or silently start a new conversation. UID-R1 failure latches
   until reviewed reconciliation even after marker restore. Failed reservations count.

## Disable / recovery

Before any runner stop/replacement, emit `disable` from actual active GET and apply
reviewed guarded patch: upgraded API nativeoff, original HTTPS config, no owner mapping
mount. Wait new API rollout/quiescence; separately confirm old admitted work terminated
cleanly. No API admission while replacing runner. On normal test replacement keep
runner Deployment and both policies/Service/transport Secrets, quiesce and use runner-stop/runner-start to replace
only its owned Pod; validate markers without initialization and re-enable using the
same private inputs after new-instance checks. A normal shutdown persists current
control cache and nativeActive=false; unclean/unknown state refuses restart.

For final disable: after nativeoff/quiescence stop only owner-runner-v1 Deployment
(replica0 with reviewed runner-stop patch), wait old Pod/process gone;
verify latest-cache continuity before any local cleanup. Never rollback control/cache
from a backup. Retain policies until no owned Pod remains, then remove only the newly
owned Service/ConfigMap/Secrets/policies/Job after scoped metadata checks. No namespace,
PVC/PV, original hosting policy/Secret/DB removal. Retain marker/broker/ledger/projection
and tombstones. If runtime70 itself needs recovery, `rollback-upgrade` restores exact
API8fa snapshot only from disabled state. DB/schema/web/TLS never rolled back.

Unreviewed drift, shutdown barrier or owner/source inconsistency stops recovery for
scoped review; tools do not force unlock/clear nativeActive or erase evidence.

## Author verification

`sudo bash infra/hosting/m3/check_owner.sh /tmp/issue7-owner-check-<new-id>`:
100 Docker unit regressions (readonly owner query and four raw guarded transitions),
Cilium1.20.2 actual upstream Rule.Sanitize three rules in both default modes (2 cases),
both exact baked images binding success/wrong-state/reinitialization refusal. No
provider/vendor/actual cluster operations. Prior approved source67+review counter1
and Compose35/20 proof reused. Local archive descriptor hashes checked separately;
actual import/CRI/Pod/source/turn/replacement remains PM plus independent review.

Runner replacement uses `runner-stop` → wait old owned Pod/process completely gone
and nativeActive=false/current cache retained → `runner-start`; never manual Pod
DELETE with replicas1 (Recreate applies to rollouts, not that deletion overlap).
Both are raw UID/RV/spec-guarded replica patches, preserving defaulted fields. Before
first lifecycle operation, extend private snapshot with `runnerDryRun` (server dry-run
of exact emission) and `runner` (first actual GET). Review emission→dry-run diff and
require their specs identical; retain this baseline unchanged. Extra/defaulted spec
fields cannot be guessed in code or adopted from later drift. Missing baseline refuses.
This explicitly reviewed defaulted baseline is trusted input, not independent proof
of the admission controller. Lifecycle baseline UID must match current UID.
Only perform disable/quiesce after the explicit turn has completed and no in-flight
API/native work remains; an unknown/active turn stops replacement rather than killing
it and claiming clean handoff. Final replica0 also uses `runner-stop`. `runner-start`
does not itself prove old process exit/source readiness: PM checks both first.
