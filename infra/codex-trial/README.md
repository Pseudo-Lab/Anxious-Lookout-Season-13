# Existing K3s /codex-trial private HTTPS candidate (#7)

This is an **emit-only, independently reviewable candidate** for `task/m3-codex-login`.
PM operates after exact source/image/procedure review and supplies actual inputs privately.
No command in `render.py` connects to Kubernetes, a database or provider. Never apply
the aggregate `review` List as a shortcut around the stages below.

The observed controller is Traefik3.7.8, actual running digest
`sha256:4299bbed850421258fc5448c2e0e6ad350981d4d335a68de11b92448aedbefe5`.
It already exposes web80 and websecure443 via K3s ServiceLB and enables TLS on
websecure. The new objects reuse that controller and listener. They do not change
its Deployment, Service, shared entrypoint options, access logs, original certificate
store/renewal, existing `m2-hosting` workloads/DB/roles/sessions/PVC, or the unrelated
HTTPS app route. A fixed `codex-trial` namespace and a new retain StorageClass/PVC
hold a new `codex_trial` database. Refuse adoption of an unrelated existing namespace,
StorageClass, resource name, PV or populated state; document ownership before reuse.

## Route and image contract

The canonical origin has HTTPS and no path/default port. The trial path is
`/codex-trial`. No StripPrefix occurs. API receives the entire path and sets
`APP_BASE_PATH=/codex-trial`. Exact `/codex-trial/api` or its slash prefix selects
API; `/codex-trial/apis` selects web. Web matches only exact `/codex-trial` or its
slash prefix; Next owns the bare-prefix trailingSlash redirect. Exact/internal slash
prefix and exact/fixture slash prefix are excluded. Both routes attach new local
encoding and access middlewares; all seven encoded reserved character flags are
explicitly false. Query encoding is preserved. Routes have no root/hostless fallback.

The frontend image must be rebuilt at the final cumulative source SHA with
`NEXT_PUBLIC_BASE_PATH=/codex-trial`. Its declared `webBasePath` input is a required
contract, **not evidence** of build behavior. PM verifies digest, UID10001/read-only
runtime, actual `/codex-trial/version.json`, links, assets and API URL prefixes.
The backend and optional pinned runner require the same exact final source SHA in
`/app/release.json`; tag-only references are refused. Previous edff9ce P0 images and
an empty-base web image do not prove or satisfy this new deployment.

Callback: `<origin>/codex-trial/api/auth/github/callback`; successful303 goes to
`/codex-trial/`, failed303 to `/codex-trial/auth/login/?auth_error=...`. Session
cookie is Secure/HttpOnly/SameSite=Lax, Path `/codex-trial/api`; transaction cookie
uses `/codex-trial/api/auth/github`. Old-origin sessions are not imported. Pending
GitHub accounts remain unapproved until an explicit trial-only operator decision.

## Private inputs and admission conditions

`input.example.json` is synthetic and cannot be deployed. Replace image digests,
origin, actual TLS Secret generation, client ID, the proven private client source
address(es), and controller source CIDRs in a mode0600 JSON outside Git/messages.
Use one canonical namespace/resolver suffix as emitted; no existing database URL.

The proposed access layer is `ipAllowList` using **RemoteAddr**, without an
`ipStrategy` that accepts caller headers. Renderer requires individual public
/32 or /128 client addresses, never allow-all/private/pod CIDR bypasses. This does
not assert that the current ServiceLB preserves client identity. The observed
Service is LoadBalancer with externalTrafficPolicy=Cluster and no configured
Deployment hostNetwork; these settings alone prove neither preservation nor loss.
Before full ingress
apply, PM must prove the chosen client's observed peer, an allowed-browser test
and denied-source/header-spoof counterexamples. If source preservation is unsuitable,
keep ingress absent and review another private access method on existing443.
Do not alter shared `externalTrafficPolicy`/forwarded-header trust just to pass a
 trial test. Missing access evidence blocks full ingress only, not artifact preparation.

Existing GitHub mode/client references were observed read-only; registration,
callback eligibility and authorized Secret reuse were not verified. Privately
compare the selected client's registered callback with the exact new callback
before bootstrap. An incompatible existing registration needs a separate trial
OAuth client or a separately authorized owner change; do not modify the working
app registration automatically. No extra repository/token scopes are requested.

Create these **new namespace-local** Secrets after the operator checks:

| Secret | FILE keys | Consumers |
|---|---|---|
| trial-db-admin | password | new Postgres only |
| trial-api | database-url | API only |
| trial-github | client-secret, transaction-key | API only |
| trial-ops | admin-url, api-password | explicit migration/permission Jobs only |
| trial-tls-<generation> | tls.crt, tls.key (kubernetes.io/tls) | existing Traefik through trial route only |
| trial-transport (enable only) | runners.json, transport-token | API mapping and single runner transport |

Use private root/child directories0700 and regular files0600/0400 without
symlinks/hardlinks. Run `check_inputs.py` **before any migration/permission Job**.
It checks only new trial DNS/DB, admin/API roles, distinct matching passwords,
GitHub secret presence and Fernet key, without printing credentials. Actual
callback ownership, source data and reachable service identity remain operator
checks. API role URL must use `anxious_api`; ops uses `postgres`; both use
`postgres.codex-trial.svc.cluster.local/codex_trial`. Never copy the old DB URL.

Example emit/preflight (inside Docker, actual directories remain private):

```bash
umask 077
docker run --rm --network none --read-only --tmpfs /tmp \
  --user "$(id -u):$(id -g)" \
  --mount "type=bind,src=$TRIAL_SOURCE_DIR/infra/codex-trial,dst=/checks,readonly" \
  --mount "type=bind,src=$TRIAL_PRIVATE_DIR,dst=/private,readonly" \
  --entrypoint python "$TRIAL_REVIEWED_API_IMAGE" \
  /checks/check_inputs.py /private/inputs
# Repeat render for each selected part; all outputs go into the same private dir.
docker run --rm --network none --read-only --tmpfs /tmp \
  --user "$(id -u):$(id -g)" \
  --mount "type=bind,src=$TRIAL_SOURCE_DIR/infra/codex-trial,dst=/checks,readonly" \
  --mount "type=bind,src=$TRIAL_PRIVATE_DIR,dst=/private,readonly" \
  --entrypoint python "$TRIAL_REVIEWED_API_IMAGE" \
  /checks/render.py /private/config.json --part foundation > "$TRIAL_PRIVATE_DIR/foundation.json"
```

The renderer accepts `foundation`, `policy`, `data`, `migrate`, `web`, `access-probe`, `app`, `ingress`, `runner`,
or aggregate `review`. Fresh bootstrap config has **no `runner` object**, no UUID,
no source auth/control mount, no remote/provider enable. Approval can be rendered
with `/checks/render_approval.py /private/config.json /private/decision.json`:
decision contains privately verified `githubId`, boolean `approved`, `role`,
`actor`, `reason`. It emits a single trial-only generateName Job, same reviewed
API image and ops Secret. Shape validation does not authorize an identity.

`render.py --part foundation` now needs **no input file** and emits only fixed
Namespace/StorageClass/SA/bootstrap CNP, without dummy OAuth/access/source fields.
`render.py --part policy` without a file emits only bootstrap CNP for a reviewed
repair of the existing trial-owned policy. With a real validated input file,
policy includes the selected bootstrap/enabled rules. App/runner/ingress/migration
and aggregate review still require the full validated input; they cannot use this
prerequisite interface to bypass their gates. Preserve existing resource identities
and dry-run/diff a policy-only repair; do not recreate namespace/storage/SA.

## Cilium1.20.2 semantic validation and closed directions

Actual PM execution of the previous b19fafe policy reached Valid=False with
`rule must have at least one of Ingress, IngressDeny, Egress, EgressDeny`.
The loader had both allow arrays empty; structural equality and Kubernetes server
dry-run accepted it but did not run Cilium's semantic sanitizer. The fixed source
uses explicit `ingressDeny: [{fromEntities: [all]}]` / `egressDeny:
[{toEntities: [all]}]` for completely closed directions. There are no `{}` allow
placeholders. Existing necessary direction allow-lists remain unchanged.

Loader denies both directions; web/Postgres deny egress; ops denies ingress;
bootstrap runner denies egress until the separately reviewed enable policy replaces
it with exact allowed destinations. Explicit denies take precedence over additive
allow policies on those selected trial endpoints. Other namespace/selectors and
shared controller settings remain untouched. This precedence is stronger than
an empty allow list plus default-deny and is intentional for wholly closed roles.

The pinned Docker validator in `cilium-validator/` imports upstream Ciliumv1.20.2
and calls the actual `Rule.Sanitize()` used by operator and agent. It verifies
bootstrap/enabled full policy and fails the old loader/full-policy expressions,
under both non-default-deny flag settings. Run tests only through Docker:

```bash
sudo docker build -t anxious-s13-back-cilium-validator:1.20.2 infra/codex-trial/cilium-validator
# A synthetic output directory must already be writable by the Docker fixture UID.
sudo docker run --rm --network none --read-only --tmpfs /tmp \
  --mount "type=bind,src=$TRIAL_SOURCE_DIR/infra/codex-trial,dst=/checks,readonly" \
  --entrypoint python <existing-test-image> /checks/cilium-validator/cases.py \
  > "$TRIAL_SYNTHETIC_DIR/cilium-cases.json"
sudo docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --mount "type=bind,src=$TRIAL_SYNTHETIC_DIR,dst=/cases,readonly" \
  anxious-s13-back-cilium-validator:1.20.2 /cases/cilium-cases.json
```

This is real-version library validation, not actual agent/datapath execution.
After exact fix review PM applies only the repaired owned CNP, waits for Valid=True
on the updated generation, checks rendered spec and actual agent policy/revisions
and records real denied traffic/endpoint realization before PV operations advance.
Failure or ambiguous/stale status preserves policy/namespace/SC/SA and evidence;
do not create PV/Job/Secret/native state to make validation proceed. No global
agent flags, other policies or allow-all exceptions are changed.

`policy_probe.py emit <audited-b19fafe-API-ref>` supplies an independently reviewable
no-credential canary stage for that traffic evidence: three bounded150s TCP-only
Pods (two controls plus trial-state-loader), a script ConfigMap, and a separate
control-role policy allowing only those same-namespace peers on TCP18080. It mounts
no PVC/Secret and overrides the API startup with Python only. The loader remains
selected by the repaired explicit deny; the new control policy does not select it.
Control-to-control and each server's loopback ACK are positive controls. Control
to loader and loader to control must fail; failed connections alone are not proof,
so PM must correlate actual ingress/egress policy drops/endpoint identity/revision
and unchanged destination served-counts. Review/execute this stage before PVs,
then delete all three canary Pods and confirm termination before removing its
temporary control policy/ConfigMap. Keep trial-boundary and all evidence. Missing
drop attribution, server readiness, or expired canary lifetime closes the stage.
Only internal IPv4/loopback targets are accepted; an IPv6 datapath needs separate
applicable evidence. No Cilium agent or public/provider endpoint is started.

Old checksum-pinned prerequisite emitters reproduce the rejected policy and cannot
be used for that repair. Freeze their evidence and identify a new source fingerprint
and review package. Already imported b19fafe runtime images and their provenance
remain historical valid artifacts; the policy-only source change does not alter
backend/frontend runtime payload. Applicability must explicitly distinguish new
policy source from an approved runtime/PV-template release, with exact unchanged
helper/runtime evidence; do not silently accept an arbitrary newer/older release.

Primary evidence: [Cilium1.20.2 sanitizer](https://github.com/cilium/cilium/blob/v1.20.2/pkg/policy/api/rule_validation.go),
[Cilium deny precedence](https://docs.cilium.io/en/stable/security/policy/deny/).

## Staged operator procedure (actual execution still held)

1. Capture private read-only ownership/resource reservations/current image IDs,
   old HTTP/other HTTPS app probes, TLS generation and source-owner boundaries.
   Verify node allocatable/requests/available disk before reserving new 5Gi DB,
   optional1Gi native +1Gi control. Emitted steady limit total is API256Mi,
   web512Mi, DB512Mi; optional runner512Mi and transient ops192Mi. Existing
   workloads are not stopped to meet these limits. Confirm matching deployed
   controller digest/CRD fields and Cilium version/resolver before exact review.
2. Independently review exact manifests/images/private input ownership. Run
   server dry-run/diff for authorized new objects only; namespaced dry-run cannot
   validate a namespace that has not been created. After review PM creates the
   namespace/SA/retain StorageClass and realizes `trial-boundary` **before Pods**.
   Verify Cilium endpoint selectors/policy revision and all additive cluster
   policies; Cilium availability/CRD presence alone is not realization evidence.
   The separate access validation below can proceed with new web only; no DB,
   OAuth/API or provider credentials are needed for that non-sensitive stage.
3. Supply reviewed new DB/API/GitHub/ops Secrets; preflight private input URLs and
   passwords. Apply `data`, wait for the new Postgres Pod/PVC. Verify actual PV
   owner/path and readiness. Run only `trial-migrate-0004`; verify auth0001 plus
   research0004 and actual API grants. No automatic migration, reset or downgrade.
   Leave failed Job evidence; correct cause before any explicit retry.
4. Apply provider-disabled `app`; verify source SHA on API, prefix image behavior,
   API/readiness and actual Cilium paths. DNS policy permits exact service FQDN
   and GitHub token/user FQDNs443, not arbitrary Internet/private services.
   Pods set ndots1 so dotted GitHub/service/native names resolve absolutely first,
   avoiding denied search-suffix queries. Verify DNS search/ndots behavior using the emitted **fully qualified** names
   and recorded Cilium resolver before assuming the allow-list resolves them.
   Check denied DB access from web/runner, denied provider from web/API, denied
   old namespace/services, default-deny including preparatory loader Pods.
5. Supply only a reviewed **trial-owned generation** TLS Secret and approved
   access artifact. Perform the restricted access-probe procedure below after
   its own exact review/PM execution authorization, then apply full `ingress`
   only when source/deny controls and OAuth inputs have passed. Existing HTTP
   stays HTTP. Test actual TLS chain/SAN/expiry, canonical HTTPS callback success
   and failure, Secure cookie/path, origin+CSRF, untrusted forwarding, exact API
   boundary, all plain/encoded bypass counterexamples and normal query encoding.
   Record permitted-browser and denied-source results without cookies/codes.
   Probe old HTTP and unrelated HTTPS controls before/after. No actual model
   request is made in this bootstrap verification.
6. User logs into this fresh trial; PM verifies GitHub identity and explicitly
   approves trial editor/admin as authorized, using the trial-only permission Job.
   Re-login after role/session revocation; no production account/session mutation.
   Without native enable the Codex status remains safely not configured.

### Restricted actual-source validation before full ingress

This stage has its own exact review and PM execution authorization. It does not
require opening the full candidate route to obtain its proof. Render/apply new
`web` only under realized trial policy and new validated trial TLS, then
`access-probe`: identical local middleware/ACL but **only exact
`/codex-trial/version.json`** reaches web. No UI catchall/API/GitHub/provider route
is opened. A missing actual allowed source/TLS generation stops this stage before
apply; no synthetic source/default ACL is substituted.

The actual user/operator at the selected allowed public source performs three
requests to that non-sensitive HTTPS version URL: no forwarded header, forged
X-Forwarded-For, forged X-Real-IP. They preserve TLS/SAN verification and report
status/version SHA without credentials. A PM-controlled client from a **different
actual public source** repeats those three cases and must get403 with no web
delivery. These are six distinct source/header combinations; changing headers on
one client cannot substitute for the second source. If a second source is not
available, record its cases **not performed**, remove the probe route and leave
full ingress absent. Requests use only version/access checks, never paid dispatch.

PM should first check the controller's existing private Prometheus metrics9100:
observed args already enable it, and service labels default true in3.7. Capture
only `traefik_service_requests_total` for the actual new trial-web Kubernetes
service label (establish the label from the realized config, do not guess it).
Confirm one permitted version request increments that counter, then snapshot
before/after each denied case: expected delta0 while that control has no other
trial-web traffic. Detect controller restarts/counter resets; invalid or missing
observations do not pass. No shared metrics/access-log configuration is changed.
If the current counter is unavailable, leave this evidence pending instead of
turning on shared logs or equating a missing metric with zero requests.

Correlate these private service counters and any authorized narrowly scoped
Cilium/Hubble/socket metadata at existing443 and
Traefik, without payloads/cookies/codes or shared logs/config changes. Establish
actual request delivery/non-delivery; absence of a generic log is not proof.
Record observer/source, timestamps, status, current ACL generation and the
specific metadata boundary in private operational evidence. A valid response
through the default RemoteAddr ACL plus these negatives demonstrates its peer
boundary; ServiceLB settings or a403 alone do not prove NAT. If deeper diagnosis
is needed, correlate actual peer/NAT via authorized private flow/socket inspection.
If evidence or caller identity is ambiguous, fail closed and delete only the
probe route. Never add a private/pod peer range to make it pass.

After successful controls, remove `trial-access-probe` before opening the full
reviewed trial route; verify deletion and unaffected existing routes. BasicAuth
or a same-origin private tunnel remains an **unselected, separately reviewed**
alternative, without automatic fallback or shared listener changes.

## Separate native enable (not satisfied by UI access)

The current source-auth/refresh lifetime proof and pinned-native endpoint inventory
remain missing. Do not fill guessed provider endpoints or launch a credentialed
runner to discover them. Private support classification, source format/owner,
same renewable grant exclusivity for the **whole trial and afterwards**, actual
platform/provider binding, max3 durable reservations and original-host
non-interference must pass the existing reviewed personal procedure first.
Booleans/configuration flags and a `procedureRef` are references, not evidence.

Only after those gates may a private config add `runner` with exactly `owner`
(canonical platform UUID), `image` (reviewed0.160.1 digest), `providerHosts`
(actual reviewed exact DNS inventory, no wildcards/IPs), `procedureRef`.
Renderer then produces one Recreate runner, isolated `trial-native` and
`trial-control` Retain PVCs, transport references, trial-only DNS/FQDN443 policy,
and exact prefixed API tool callback. No hostPath/Docker socket/API-key mount.

Keep public admission absent while applying the new reviewed policy/PVCs and
seeding control. An explicitly reviewed, non-root, tokenless
`app=trial-state-loader` Pod may create `/control/private` mode0700, owner10001,
and receive only reviewed grant/cache/control files0600, not a mounted original
HOME. Its default-deny policy must already be realized. Do not snapshot or restore
the mutable ledger from native backups. Confirm loader termination before serving.
Native root is `/native/private` so UID10001 creates/owns the private subdirectory
instead of trying to chmod a root-owned PVC mount; control is a different PVC.
No root initializer/chown of the host source is emitted. State seeding/source
coordination remains a private separately reviewed procedure, not this renderer.

Privately check `runners.json` contains **only** the verified owner mapped to
`http://runner.codex-trial.svc.cluster.local:8080` with
`tokenFile=/run/runner/transport-token`; same separate token mounts at runner
`/run/secrets/transport-token`. Verify exact image/CLI hash offline. Start the
reviewed runner only within the explicit live procedure; startup can involve native
authentication and therefore is not a harmless bootstrap check. Apply reviewed
enabled `app` and re-open the retained trial route only after readiness/policy.

The new API remote gate requires personal-test, explicit enable, exact configured
HTTPS `CODEX_PERSONAL_REMOTE_ORIGIN`, `/codex-trial`, Secure-cookie/trusted-proxy
settings and personal-private scope. These do not bypass single-owner platform
authorization, native grant/max3, model quarantine or any unsupported-use hold.
No browser model override, account creation, API-key fallback or paid replay.

## TLS generations, refresh and rollback (K7-P1)

The original owner performs renewal independently. PM securely supplies a fresh
approved chain/key snapshot into a new **trial** TLS Secret; never write the
source archive/current symlinks, original app Secret or ACME/challenge route.
Do not dump Secret YAML or key into artifacts/issues/logs. Privately verify
chain trust, SAN origin, key/leaf public-key match, notBefore/notAfter and enough
remaining lifetime for the trial. The previously observed public archive expires
2026-10-15T19:56:04Z; its existence does not establish the live Secret generation
or renewal status. A trial snapshot does not auto-renew. PM must schedule its
private validity check/update or end the trial before expiration; no watcher/cron
is installed by this implementation.

For each approved new generation use `trial-tls-<generation>` and `kubectl create
secret tls ... --cert=<private-snapshot-chain> --key=<private-snapshot-key> -n
codex-trial`. Check key/chain outside printed output. Re-render **ingress only**
with that generation reference after private validation, review/dry-run, then
switch the trial route. Verify new handshake trust/SAN/expiry/fingerprint on the
intended existing443 and permitted source. Retain the previous unexpired trial
generation. If validation fails, close trial admission; switch back only to an
independently revalidated still-valid previous generation. Never fall back to
insecure HTTP, expired TLS, shared owner writes or a allow-all access middleware.

Resource rollback order is mandatory:

1. Delete only `IngressRoute/trial` and any `IngressRoute/trial-access-probe`
   in `codex-trial`; confirm absence and blocked
   new browser requests. This blocks admission without touching other routes.
2. Disable remote admission/mapping in the trial config and terminate **all trial
   API/web/runner/ops/state-loader workloads**, explicitly scale/delete their
   controllers/Jobs so they cannot recreate Pods. Wait for confirmed termination
   of every corresponding Pod. Retain runner shutdown outcome; forced termination
   preserves ambiguity/tombstones and does not promote auth or replay a request.
3. Scale trial Postgres to0 and verify its Pod termination; retain DB PVC/Secret
   references and private data/native/control/dispatch ledger. Preserve failed
   Job/state evidence privately. Do not delete namespace/StorageClass/PVs/PVCs.
4. **Only after no trial Pods/controllers can still run**, remove obsolete trial
   middleware and `trial-boundary` policy. A failed wait leaves policy in place.
   Never delete deny policy first or use a broad namespace cleanup. Secret
   cleanup is separately reviewed; preserve valid generations for rollback.
5. Verify original HTTP/other HTTPS routes/workloads/DB and original Codex host
   before/after non-interference. Changing/restoring native backups never clears
   ledger reservations, model quarantine or original-owner proof requirements.

For an image rollback with trial Pods still alive keep all deny policies. Stop
admission, stop Pods, revalidate the previous approved image/compatible schema and
restart only trial workloads under realized policy. Never downgrade/reset schema
or reconstruct lost control state to recover dispatch slots.

## Reproducible bounded tests and their limits

```bash
sudo bash infra/codex-trial/check.sh /tmp/issue7-trial-review <existing-test-image>
```

Renderer/private-input tests run in Docker. A one-hour synthetic certificate and
same observed Traefik digest exercise actual TLS/ASGI, route-local all7 middleware,
prefix boundaries, internal/fixture rejection with zero upstream calls, normal
query/callback handling, peer allow-list/header spoof and old HTTP/other HTTPS
control routes. The fixture changes only provider representation (Kubernetes CRD
to equivalent file-provider objects), service DNS to loopback mocks, synthetic TLS
files, and actual source ACL to one synthetic loopback address. TLS entrypoint and
reserved-path default options match the observed controller. **Kubernetes CRD
translation, ServiceLB/client-IP, Cilium realization, real certificate/OAuth and
actual Next behavior are distinct operational evidence**, not inferred from this
fixture. Next bare-prefix redirect here is a mock; front's prefix60/162 validates
actual Next separately. No actual credentials/services/cluster/model calls occur.

Primary references: [Traefik3.7 route-local encoding](https://doc.traefik.io/traefik/v3.7/reference/routing-configuration/http/middlewares/encodedcharacters/),
[Cilium DNS/FQDN policies](https://docs.cilium.io/en/stable/security/dns/),
[GitHub callback matching](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps#redirect-urls),
[Traefik3.7 service metrics](https://doc.traefik.io/traefik/v3.7/reference/install-configuration/observability/metrics/).
