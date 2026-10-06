# M2 deployment and recovery

This directory prepares reviewable deployment for issue [#4](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/4). **PM owns actual operation; no script automatically applies k3s resources.** Back authors tools/code and runs isolated Docker checks. See PREAPPLY.md for the current public-IP HTTP/Alembic sequence and VERIFICATION.md for actual results; prepared YAML is not a live pass.

## Build and release

Set actual RELEASE_SHA and RELEASE_BUILT_AT in an ignored worktree `.env` copied from `.env.example`. Compose project is `anxious-s13-back`, loopback API/web/gateway ports default to 28080/23000/28000. Root Compose commits no container_name, mounts no Docker socket and exposes no PostgreSQL port.

```sh
sudo docker compose build api web
```

Web build uses locked pnpm dependencies, NEXT_PUBLIC_BASE_PATH, WEB_GIT_SHA and WEB_BUILT_AT. `version:write` creates public/version.json before standalone build. Runner copies standalone/static/public and explicitly binds PORT=8080/HOSTNAME=0.0.0.0. Read-only root + /tmp relies on front's no-ISR/current dynamicParams handling. Google Fonts/registry access is required during build, not a web runtime egress permission. Node and Python/PG bases are digest pinned. API metadata is validated at build/startup. Public config changes require rebuilding web; secrets never become NEXT_PUBLIC values.

Docker and k3s containerd stores differ. Record each web/API image digest, platform, SHA/build timestamp and deployment YAML/configuration as one release bundle. Retain the previous bundle and `docker save` image archives with checksums. For a reviewed single-node application, operator can import archives:

```sh
sudo /usr/local/bin/k3s ctr -n k8s.io images import /PRIVATE/RELEASE/images.tar
```

Use actual digest-qualified image names returned by containerd when rendering. Do not assume a Docker build alone made images available to kubelet. No embedded/private registry or host firewall change is introduced.

## Private configuration inputs

Ignored `.local/hosting/` contains private root-readable provisioning files. Create credentials with an approved operator mechanism; never paste values in messages/issues. Root Compose expects db-admin-password, api-database-password, api-database-url, admin-database-url, auth-transaction-key and github-client-secret. URL files use `postgresql+psycopg://` and the proper internal DB service/database. Disabled OAuth still uses an empty github-client-secret file and transaction key may be empty. Real mode requires owned GitHub OAuth app/ID and secret plus a valid Fernet key; do not reuse the disposable test fixture key/passwords.

k3s Secret references (resources are not committed with values):

For Docker Compose file-backed secrets, preserve a root-owned mode-0700 `.local/hosting` parent and set the mounted API/ops files to UID/GID 10001 mode 0400; PG password file uses UID/GID 999 mode 0400. File-backed Compose secrets retain bind-file ownership rather than applying requested uid/gid modes. The private parent prevents host users traversing these files while the actual mounted non-root process can read them. Kubernetes instead projects mode-0440 Secrets with the matching fsGroup. Verify readability inside the reviewed container before startup; do not solve it by putting passwords in the image or broadly opening the parent directory.

| Secret | Keys / mount |
| --- | --- |
| hosting-db-admin | password → PG /run/secrets/password |
| hosting-api | database-url, transaction-key, github-secret → API /run/secrets |
| hosting-ops | database-url (privileged), api-password → migration/approval only |

Create these only after target/diff/baseline/rollback review. Secret file modes/fsGroup permit the actual non-root process to read them. API never mounts operator/superuser credentials. Retain private recovery credential/key inputs separately from the DB logical dump.

## Reviewed k3s sequence

1. Collect current node/Pod/resource metrics, existing Docker started-at/health/DNS/HTTPS response and `/android-agent` routes/representative assets plus feedback/skills probes. Retain before/after responses. Confirm M1 node guard/tenant policy/dataplane still realized. No broad firewall exception is required for exec probes.
2. Supply actual immutable images and render outside the repo:

   ```sh
   M2_PUBLIC_ORIGIN='ACTUAL_REVIEWED_HTTP_ORIGIN' \
     WEB_IMAGE='ACTUAL_WEB@sha256:ACTUAL_DIGEST' API_IMAGE='ACTUAL_API@sha256:ACTUAL_DIGEST' \
     bash infra/hosting/render.sh > /tmp/m2-hosting.yaml
   API_IMAGE='ACTUAL_API@sha256:ACTUAL_DIGEST' \
     bash infra/hosting/render-migration.sh > /tmp/m2-auth-migration.yaml
   ```

3. Review resource list/diff and server dry-run in the approved namespace sequence before real apply. New namespace m2-hosting, new m2-postgres-retain StorageClass, SA, ConfigMap, PostgreSQL StatefulSet/PVC/headless Service, web/API Deployments/Services, owned IngressRoute and owned CNP only. Default StorageClass and existing PVs/namespaces/apps remain untouched. Establish policies before workload exposure and observe policy realization. Do not interpret absent namespace/Secret/PVC prerequisites as a completed server dry-run.
4. On explicit application authorization, create dedicated namespace/retaining storage/policy/config/secrets, start PG, wait for ready/volume ownership, run explicit **auth-alembic-0001** Job, then web/API rollout. Cluster-scoped Namespace/StorageClass bootstrap is separate from the namespaced SA; wait for authorized namespace creation before namespaced server dry-runs. Check actual role login/schema as well as pg_isready. No automatic API-startup migrations or DB resets. Request budgets/surge are initial values; measure them on this node.
5. IngressRoute selects only the actual numeric public IPv4 Host from mandatory M2_PUBLIC_ORIGIN. Existing hostless `/android-agent` resource is not edited: the web rule excludes both `/android-agent` and `/android-agent/`. API matcher is exact `/api` or `/api/` prefix; `/apis` goes to web. Dashboard/TLS/hostless root fallback are not added. Browser uses the actual public origin. Host matching ignores port, so verify existing entrypoint/ServiceLB external port matches the exact AUTH_ORIGIN; actual auth separately enforces Host/protocol/port. Verify unspecified Host/IP cannot expose M2 root/API and compare original existing routes.
6. Validate status/versions/web assets/content-type/cache/real 404, role/DB behavior, resource/restart readings and M1 authorized-source controls + policy drop evidence for denials. Ready alone or timeout without evidence is insufficient.

API egress permits approved DB and CoreDNS plus public IPv4 HTTPS excluding private/reserved CIDRs. Real provider URLs are hardcoded; DNS FQDN enforcement is not claimed because existing Cilium L7 proxy remains off. Existing all-non-system node/API/metadata/own-public-IP guard remains authoritative. Web/PG have no runtime egress; operator migration has only DNS/DB. Tenant labels/materials-api/agent-manager labels are not reused.

## Data persistence and rollback

PG 18 data mounts /var/lib/postgresql with PGDATA /var/lib/postgresql/18/docker. Non-root UID/GID 999 and volume fsGroup are required. New PVC requests 10Gi and uses a new Retain class; StatefulSet retention is Retain. local-path is node-local, cannot promise requested capacity as a filesystem quota or automatic expansion/HA. Monitor actual disk/WAL/backup growth and node availability. Do not remove namespace/PVC/PV or swap PG major as cleanup/rollback.

Normal rollback reapplies previous compatible web/API images and matching public config/Ingress settings, checks actual SHA/assets/readiness, and retains compatible DB schema/credentials. Rollback order keeps M1/DB protection active; if a new release fails before public exposure, withdraw only its owned route/deployments. `rollout undo` alone does not restore ConfigMaps/Secrets/routes. DB historical restore is a separate reviewed incident process, not ordinary application rollback. No automatic down migrations or major downgrade.

The one-shot Job runs `python -m app.migrate --revision 0001_auth` using SQLAlchemy's privileged connection and Alembic. An advisory lock serializes runs in one DB and one transaction covers DDL/grants/version. Empty DB creation and verified legacy-v1 adoption are supported; existing account/permission/audit/session/transaction rows and role passwords are retained. Altered/partial schemas fail without stamp/repair. Application readiness requires both v1 and exact Alembic revision. API can SELECT version markers but cannot update them. Downgrade and unknown revision targets are rejected; failed/completed Job recreation is an inspected PM action, never a DB reset.

## Backup and isolated restore

```sh
# Approved Docker DB, private new file. Use k3s mode for the owned postgres-0 instead.
sudo env COMPOSE_FILE=/ABSOLUTE/docker-compose.yml BACKUP_PROJECT_NAME=anxious-s13-back DB_NAME=hosting \
  bash infra/hosting/db-backup.sh docker /PRIVATE/auth-new.dump
sudo bash infra/hosting/restore-check.sh /PRIVATE/auth-new.dump
```

Backup is PostgreSQL consistent `pg_dump -Fc` without owner/privilege credentials and mode 0600 + checksum. M1's control-plane backup and naive live PGDATA copying are not DB backups. Role provisioning/grants use the versioned migration/recovery input, with operator credentials held separately.

The backup tool rejects both existing dump/checksum (including symlinks), takes an atomic per-destination lock, writes in its own private temporary directory and publishes with non-overwriting hard links. Cleanup deletes only its own temporary files; existing/public files are never removed on failure. A killed process can leave a lock, or an interrupted publication can leave a partial pair; operators must verify no writer is live and inspect/verify artifacts before manual cleanup or reuse. Failure is not reported as a completed backup. Dedicated Docker collision/failure/concurrency tests cover this boundary.

Restore checker creates a new non-root read-only/network-none PG container with only fresh tmpfs, no host ports/live PVC/host network. It restores atomically with errors fatal, checks auth schema/role/FK invariants and exact Alembic revision when present, and removes sessions/transactions in the copy. Legacy v1 is recognized with a notice requiring the reviewed Alembic upgrade before new app readiness; unsupported revisions fail. It prints counts, not identity/secret data, and deletes only its own container. Actual recovery must provision/re-grant DB roles, invalidate all restored sessions/transactions, reconcile post-snapshot approvals/revocations from trusted operational records, then validate/reopen auth. Backups can contain identity/session hashes/short-lived encrypted verifier data and must stay private/encrypted before any off-host transfer.

Daily + pre-schema-change backups and seven verified retained copies are proposed defaults; off-host destination, retention, RPO/RTO and operator recovery testing need operational handoff. No automatic off-host transfer is started. Host-only backups do not cover host loss.

## Actual GitHub vs public operation

Mock tests do not need an external app/secret. Actual GitHub flow requires the owned app, exact registered public-IP callback/client ID and private injected secret. PM confirms actual public IPv4/port/origin before application. Never change an existing Supabase callback without owner approval. k3s config initially disables OAuth; the user-authorized temporary public HTTP exception requires ALLOW_PUBLIC_IP_HTTP=true and the exact numeric globally routable IPv4 origin. Registration and real GitHub roundtrip are separate acceptance results.

Public domain/DNS/TLS is subsequent work. HTTP cookies omit Secure for browser compatibility but retain HttpOnly/Lax/scoped paths. HTTPS remains the default and restores Secure; exact-origin session hashes force re-login on HTTPS/domain/port transition without resetting account permissions. Audited approvals, persistence and recovery readiness are still required. Mock/public-HTTP verification does not establish HTTPS acceptance.

### Actual acceptance address and optional local development

Actual acceptance uses **http://<actual-public-IP>[:port]/** and callback **http://<actual-public-IP>[:port]/api/auth/github/callback**. Port 80 is omitted. PM obtains operational values and owner registration; fixture addresses are not deployment inputs. Web build keeps NEXT_PUBLIC_BASE_PATH empty and WEB_BUILD=standalone. Neither localhost nor SSH forwarding is a prerequisite for this acceptance.

The optional protected local Compose development gateway remains http://127.0.0.1:28000/. A separately reviewed local development setup can use:

```sh
ssh -N -L 28000:127.0.0.1:28000 VERIFIED_SSH_USER@VERIFIED_SERVER
```

Use AUTH_ORIGIN=http://127.0.0.1:28000 and explicit ALLOW_INSECURE_LOOPBACK=true only for that optional local origin; keep its gateway loopback bound. This is independent of the selected public-IP k3s acceptance and does not substitute for actual browser/provider evidence.

Real-auth endpoints validate incoming Host/protocol/port against configured AUTH_ORIGIN. Forwarded protocol is trusted only from explicit AUTH_TRUSTED_PROXY_CIDRS combined with the ingress-only-from-Traefik Cilium policy. HTTP is allowed only for the explicit selected origin; do not weaken source/Host/Origin/CSRF validation or add a broad hostless route. Review origin, callback and routing together on later TLS transition.

For k3s operator account changes, `render-approval.sh` emits an ops-labelled non-root Job with the private operator credential. Supply verified GITHUB_ID, APPROVED, ROLE, ACTOR and REASON plus API_IMAGE, inspect it, then use `kubectl create -f` only under the approved operation. No ID/default administrator is selected by the script. It uses generateName for an auditable one-shot execution; retain status/log outcome and remove only the completed owned Job if desired. The direct CLI remains available for audit text outside the renderer's safe ASCII literal set.

## Reproduce cumulative runtime/Pages checks

After building the backend test stage, `sudo bash infra/hosting/check-public-routes.sh` evaluates the renderer's exact rules with real Docker Traefik on a fresh internal network. It checks API boundaries, unknown-Host root/API denial, an existing hostless android fixture, rejected origins and the explicit migration/approval commands. It contacts no public fixture IP and applies no cluster resource. Real cluster coexistence and port admission remain PM checks.

After building web/API for an actual release SHA, run backend validation first to create only the dedicated test DB and role. The integration overlay reuses that test project, supplies mock-mode API settings and binds a test-only gateway to loopback 28100. Inside its network, the driver origin is 127.0.0.1:8080; it is not an actual GitHub callback registration.

```sh
sudo docker build -f infra/hosting/browser.Dockerfile -t anxious-hosting-browser-test .
sudo env WEB_RUNTIME_IMAGE=ACTUAL_WEB_IMAGE API_RUNTIME_IMAGE=ACTUAL_API_IMAGE \
  docker compose -p anxious-s13-back-test -f infra/hosting/compose.test.yml \
  -f infra/hosting/compose.integration.yml up -d api web gateway
sudo docker compose -p anxious-s13-back-test -f infra/hosting/compose.test.yml \
  run --rm --no-deps test python -m tests.wait_gateway
sudo docker compose -p anxious-s13-back-test -f infra/hosting/compose.test.yml \
  run --rm --no-deps test python -m tests.gateway_probe
sudo docker run --rm --network container:anxious-s13-back-test-gateway-1 anxious-hosting-browser-test
```

Gateway checks use the real runtime services with a mock identity provider, not a mocked frontend API. The HTTP driver follows web slash redirects; browser checks auth/guest state rather than requiring a noncontractual logout URL navigation. The web process is UID 10001/read-only with /tmp only. Keep these results separate from Cilium and real provider checks.

The browser driver requests a fresh disposable mock-provider identity per run, so the approved identity used by the restart/restore seed cannot incorrectly turn the expected pending UI into an approved account. Only the mock-provider identity input is varied; actual API/session/DB/CSRF behavior remains real.

Pages out/ generation is independently reproduced with the builder target:

```sh
sudo docker build -f infra/hosting/web.Dockerfile --target builder \
  --build-arg WEB_BUILD=pages --build-arg NEXT_PUBLIC_BASE_PATH=/Anxious-Lookout-Season-13 \
  --build-arg WEB_GIT_SHA=ACTUAL_SHA --build-arg WEB_BUILT_AT=ACTUAL_UTC \
  -t anxious-hosting-pages-test .
```

This invokes the same build:pages command as the preserved workflow and produces /webapp/out. The static browser fixture uses front's read-only pages-static.mjs and backend browser_pages.mjs on an isolated container network. It verifies API-disabled UI/no API requests, not any GitHub Pages deployment or external cache behavior. Cleanup stops only these owned validation containers; test volume deletion is explicit and never part of production rollback.
