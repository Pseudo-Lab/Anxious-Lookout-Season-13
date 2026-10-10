# Existing root HTTPS P1 — exact emit and application rollback

Issue7, shared task/m3-codex-login. PM operates after this exact package review.
P1 is native-disabled; P2 credential/source/support/refresh ownership does not
block preparing P1 or its certificate/version-only stage. Original DB/auth/roles/
PVC/PG/Secret and root web edff are retained. No migration/role/session deletion,
new OAuth app/DB/prefix or PC IP input is emitted.

## Source / immutable image

API runtime built from approved root-gate source
`8fa785480e73b88903d3263ebe51159961f30d2e`, actual builtAt
`2026-10-10T08:45:10Z`, Linux ARM64, image repository
`docker.io/library/anxious-s13-root-api-8fa7854`:

```text
local index / Docker ID: sha256:ddd324c1078ffee5c3deef2f3fc21543f94edb25bb9ac9828c68121266fdfde3
arm64 manifest: sha256:0956d997fc3dce9c7506786f4feab711c20f5445a7b6eeabbc19aed3f40aa26e
config: sha256:9bb81cfdb32d7911a21fc0a75a30d485e3a1658de1038e8c882b6f3ea6c73cbe
archive SHA256: 23190af8f67c912627dc9e217bb1d64027d240963dd6cc7d06805218c1a5be05
```

Exact Docker runtime build, explicit release SHA, baked codex_policy hash88480abb…,
test/fixture exclusion and OCI descriptor bytes were verified offline. BuildKit's
automatic VCS discovery warning did not establish provenance; clean explicit
source SHA/tree and baked release/hash checks establish the recorded build inputs.
An earlier build with an incorrectly fixed future timestamp was superseded before
export/policy selection. Only the above final08:45:10 image is approved-candidate.
Private archive/local descriptor evidence: `/tmp/issue7-root-p1-prepare/`.

`image_audit.py` adds a sealed `root-api` policy with this source/index/repository;
existing `api` b19 and `root-web` edff policies remain intact. PM uses the established
Docker archive import/descriptor/CRI procedure for this exact new role. Preserve
archive checksum and actual target/manifest/config bytes. Run in reviewed Docker:

```sh
python /checks/image_audit.py root-api "$ROOT_ACTUAL_REFERENCE" /private/new-api-chain
```

This is a container command; actual mounts are readonly and network none. The
directory contains local-target/local-manifest/local-config, imported target/
manifest/config and cri.json. The canonical imported reference must additionally
equal https_prepare.ROOT_API before patching. If import transforms the target,
stop for back/review rather than retagging or editing policy constants. Later
rerun with the actual minimal Pod association file as final argument. No K3s
import/CRI/Pod association for this new image has yet been claimed by back.

Operation-tool branch SHA will be later than8fa; runtime source still8fa.
Changes since8fa are infra/tests/docs only, so distinguish source/build/tool SHAs.
Do not rebuild merely to make API/runner/web release SHA labels match. Existing
runner source/required policy bindings and web/build/dependencies are unchanged;
source-equivalence and installed CLI hash evidence is reused. Runner is not
required for P1 and no runner process is started.

## Exact input, new resource inventory and emitted delta

`https_prepare.py` reads a private baseline JSON with keys `api`, `config`,
`httpRoute`, `ingresses`. These are full original API Deployment, original
hosting-config, original hosting HTTP IngressRoute and the observed Ingress List.
Use current captures and freeze this baseline before operations; do not overwrite
it after transition. Initial private reproduction used PM's actual final rollout
API/config and P0 route/Ingress captures; offline emit passed, no apply.

The emitter requires existing b19 API, one RollingUpdate replica(maxSurge1/
maxUnavailable0), original config reference, root HTTP origin, GitHub mode,
trusted-proxy CIDRs and native/map off. It refuses changed origin/image/route,
new unknown Ingress scope, hidden direct auth overrides/native env/duplicate env.
Host derives only from the original canonical origin. It seals the observed
Android/ACME paths and separate reports host, and excludes Android and ACME
boundaries explicitly from all new root routes.

| Part / resource | Behavior |
|---|---|
| config / ConfigMap hosting-root-https-v1 | New immutable generation. Copy original data; HTTPS origin/empty base; insecure flags false; ordinary/root native false, owner/map empty |
| encoding / Middleware hosting-root-encoding-v1 | Seven encoded ambiguity flags false; route-local, no shared controller/listener edit |
| redirect / Middleware hosting-root-redirect-v1 | Temporary HTTPS redirect; GET/HEAD only by route match |
| tls-probe / IngressRoute hosting-root-tls-probe-v1 | websecure, exact existing Host+/version.json, GET/HEAD only, web8080 only; no API/full UI; priority21 |
| https-route / IngressRoute hosting-root-https-v1 | websecure priority20 API8080 excluding /api/internal; priority10 root web8080 excluding API/fixture; both exclude Android/ACME |
| api / existing Deployment api JSON patch | UID/RV/full-spec tests; replace image/envFrom/explicit disable env only. Original probes/resources/FILE refs/security/volumes/strategy unchanged |
| http / existing IngressRoute hosting JSON patch | UID/RV/full-spec tests; temporary redirect only for GET/HEAD non-API/non-fixture/non-Android/non-ACME root. HTTP API and mutating requests have no root route |
| rollback-api, rollback-http | Require same UID and exact expected P1 spec; restore original image/config reference/env and original HTTP spec |

TLS input is the name of a new namespace-local `hosting-root-tls-<generation>`.
It is a **planned resource name**, not proof a Secret exists or is valid. Secret
creation itself is not emitted. The reviewed TLS snapshot procedure supplies the
owner-approved chain/key through private input without source Secret/ACME/renewal
changes. Check valid/SAN/key-match/trust/lifetime and no name collision before
create. Do not adopt existing similarly named objects or replace an old generation.

Container emission pattern (redirect output only to private files):

```sh
python /checks/https_prepare.py config --snapshot /private/baseline.json --tls-name hosting-root-tls-v1
python /checks/https_prepare.py tls-probe --snapshot /private/baseline.json --tls-name hosting-root-tls-v1
python /checks/https_prepare.py api --snapshot /private/baseline.json --tls-name hosting-root-tls-v1 --current /private/current-api.json
python /checks/https_prepare.py http --snapshot /private/baseline.json --tls-name hosting-root-tls-v1 --current /private/current-http.json
```

Encoding/redirect/https-route and rollback parts use the same interface. Each
initial api/http patch is emitted from the frozen baseline and fresh current
resource. JSON test operations enforce UID/RV/spec at application time. Current
config/Ingress/middleware/Service/CNP/TLS source identities/specs are rechecked
against the separately frozen captures before each stage; emitter is offline and
cannot observe live drift. Captures/emitted ConfigMap contain private client/host
metadata and do not go into Git or shared message bodies.

## PM execution: independent metadata stage, then origin transition

0. Verify approved tool SHA, image/local archive provenance, source equivalence,
   current baseline/backup recency, Secret/config/PG/PVC identities, one-replica
   surge capacity and effective existing Cilium policy/Traefik peer trust. New
   route paths/policy generations/collisions require renewed exact review.
1. Create the approved new TLS generation and encoding middleware only, using
   server dry-run/private diff first. Create the **version-only** tls-probe route.
   Existing HTTP API/config stays unchanged. Verify actual serving CA/SAN/
   fingerprint/notAfter against the selected source and new target generation,
   edff version, unknown path/API/UI/POST version rejection, Android HTTPS and
   HTTP ACME preservation. This phase does not require ChatGPT source or a new
   GitHub login, and does not prove callback registration/private native support.
2. Before the full transition, PM must verify the existing registered GitHub
   callback accepts exact HTTPS root `/api/auth/github/callback`, communicate
   required re-login/write pause and establish the concrete rollback window.
   Native ownership inputs remain a separate P2/P3 gate. If callback/impact is
   unresolved, leave full HTTPS/API transition unapplied and retain HTTP baseline.
3. Import/audit new API on the actual scheduled node. Create immutable config and
   redirect middleware after collision checks/dry-run. Finish in-flight writes/
   OAuth flows; no new turn auto-replay. Apply HTTP redirect-only patch first so
   old HTTP auth/API is closed before origin switches. Mutating requests are
   refused rather than redirected. This introduces a bounded service transition
   window; no zero-outage/session-continuity claim.
4. Emit fresh UID/RV API patch and server dry-run/diff; apply then wait new API
   Ready/version8fa/actual Pod association. RollingUpdate surge retains original
   resources/strategy; mixed old/new replicas may reject auth during the window.
   Keep full HTTPS route absent until Ready. If timeout/unknown, stop and rollback
   the application boundary; do not widen auth trust or replay migration.
5. Delete only owned tls-probe route and confirm absence. Dry-run/diff/create full
   HTTPS route. This is not a public native route: every enable/map remains off.
   Original other-app and ACME objects are not patched; only the listed new
   objects and original hosting HTTP/API objects are touched.
6. Verify TLS/version/assets and GitHub success/failure through the normal user
   browser. Auth start writes OAuthTransaction, so no fabricated readonly probe.
   Confirm Secure/HttpOnly/SameSite cookie with `/api`, Origin/CSRF and trusted/
   untrusted forwarding, same existing account UUID/role and data. Test HTTP API/
   POST rejection and internal/fixture encoded counterexamples. Keep tokens/
   codes/cookies/full Locations private. Native status remains not_configured.
   Verify unrelated routes, PG/PVC/Secret identity and actually realized middleware
   and controller/CRD translation. File-provider fixture success is not live CNP/
   CRD proof. Record observed outages/unfinished requests instead of assuming
   traffic-free success. User interactive result and independent review remain.

P0's prior HTTPS verify=0/404 establishes current client connection/trust only;
it is not evidence that the new target Secret/route serves the selected leaf.
Native private access enforcement may be prepared separately; P1 does not claim
that a public GitHub-authenticated platform satisfies personal native support.

## Rollback / unknown handling

Retain frozen originals and UID/RV metadata, previous app images/config, new TLS
generations and all evidence. No rollback deletes data/research schema or auth
rows. Do not resume expired TLS, reset budget/cache or perform schema downgrade.

- Before origin change: remove only the UID-owned tls-probe route if aborting,
  verify absence/HTTP and unrelated routes unchanged. Remove unused owned
  middleware only after references disappear; retain TLS generation for review.
- During/after origin change: native admission is already closed; remove only
  owned full HTTPS and/or probe routes first, confirm no HTTPS API admission.
  Emit rollback-api against fresh current API and exact desired spec. Restore
  original b19/config/env and wait readiness. Then emit rollback-http against
  exact desired HTTP spec; restore original hosting routes. This opens HTTP only
  after the matching HTTP-origin API is Ready. Root web remains edff throughout.
- If API patch never applied, original API still matches baseline: do not call
  rollback-api on a state it refuses; verify actual original API/config then
  restore only the modified HTTP route. If a stage never applied, skip its undo.
- Resource/spec drift, partial rollout or ambiguous ownership means preserve and
  stop, not unconditional kubectl rollout undo/delete/scale or editing tests to
  pass. Exact reconciliation goes back to back/review. New ConfigMap/TLS and
  unused middlewares remain until later scoped cleanup; original resources stay.

HTTP cookies cannot become HTTPS sessions and the reverse is also refused by the
unchanged origin-bound digest. Same-host cookie names/path mean changing schemes
can replace the browser cookie while DB rows remain. Rollback may require another
normal login; retaining data is not a promise of browser session continuation.

## Author verification

Final `check_https.sh` in Docker: **50 unit cases**, **62 full-root pinned
Traefik/TLS/ASGI cases**, **15 version-only cases**. No host ports/network, live DB,
actual credentials or native process. Root mock invokes the baked new API only
for missing-transaction callback refusal before DB/provider; normal OAuth success
and startup/DB readiness are not proven. Encoding7 and plain/encoded-letter
internal/fixture paths, forwarded spoof, normal encoded query, old HTTP controls,
Android/ACME delegation, HTTPS cookie/path and temporary redirects are covered.

Initial proxy run exposed invalid Method(GET,HEAD) v3 syntax; source now uses
Method(GET)||Method(HEAD). Early redirect assertions incorrectly assumed one
temporary status; observed302/307 both meet the GET/HEAD-only contract with exact
Location/upstream0. Permanent redirects and HTTP API/POST delivery remain denied.
Only final50+62+15/exit0 counts as passing. Earlier iterations are diagnostics.
