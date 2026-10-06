# M2 deployment and recovery

This directory prepares reviewable deployment for issue [#4](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/4). **No script automatically applies k3s resources.** See VERIFICATION.md for actual results; prepared YAML is not a live pass.

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
   WEB_IMAGE='ACTUAL_WEB@sha256:ACTUAL_DIGEST' API_IMAGE='ACTUAL_API@sha256:ACTUAL_DIGEST' \
     bash infra/hosting/render.sh > /tmp/m2-hosting.yaml
   API_IMAGE='ACTUAL_API@sha256:ACTUAL_DIGEST' \
     bash infra/hosting/render-migration.sh > /tmp/m2-auth-migration.yaml
   ```

3. Review resource list/diff and server dry-run in the approved namespace sequence before real apply. New namespace m2-hosting, new m2-postgres-retain StorageClass, SA, ConfigMap, PostgreSQL StatefulSet/PVC/headless Service, web/API Deployments/Services, owned IngressRoute and owned CNP only. Default StorageClass and existing PVs/namespaces/apps remain untouched. Establish policies before workload exposure and observe policy realization. Do not interpret absent namespace/Secret/PVC prerequisites as a completed server dry-run.
4. On explicit application authorization, create dedicated namespace/retaining storage/policy/config/secrets, start PG, wait for ready/volume ownership, run explicit auth-migration-v1 Job, then web/API rollout. Check actual role login/schema as well as pg_isready. No automatic API-startup migrations or DB resets. Request budgets/surge are initial values; measure them on this node.
5. IngressRoute selects only Host m2.invalid. Existing hostless `/android-agent` resource is not edited. API matcher is exact `/api` or `/api/` prefix; `/apis` goes to web. Dashboard/TLS/hostless root fallback are not added. CLI can use Host header; browser can use a hosts entry. This does not select DNS/domain. Verify unspecified Host/IP cannot accidentally expose M2 app and compare original existing routes.
6. Validate status/versions/web assets/content-type/cache/real 404, role/DB behavior, resource/restart readings and M1 authorized-source controls + policy drop evidence for denials. Ready alone or timeout without evidence is insufficient.

API egress permits approved DB and CoreDNS plus public IPv4 HTTPS excluding private/reserved CIDRs. Real provider URLs are hardcoded; DNS FQDN enforcement is not claimed because existing Cilium L7 proxy remains off. Existing all-non-system node/API/metadata/own-public-IP guard remains authoritative. Web/PG have no runtime egress; operator migration has only DNS/DB. Tenant labels/materials-api/agent-manager labels are not reused.

## Data persistence and rollback

PG 18 data mounts /var/lib/postgresql with PGDATA /var/lib/postgresql/18/docker. Non-root UID/GID 999 and volume fsGroup are required. New PVC requests 10Gi and uses a new Retain class; StatefulSet retention is Retain. local-path is node-local, cannot promise requested capacity as a filesystem quota or automatic expansion/HA. Monitor actual disk/WAL/backup growth and node availability. Do not remove namespace/PVC/PV or swap PG major as cleanup/rollback.

Normal rollback reapplies previous web/API images and matching public config/Ingress settings, checks actual SHA/assets/readiness, and retains compatible DB schema/credentials. Rollback order keeps M1/DB protection active; if a new release fails before public exposure, withdraw only its owned route/deployments. `rollout undo` alone does not restore ConfigMaps/Secrets/routes. DB historical restore is a separate reviewed incident process, not ordinary application rollback. No automatic down migrations or major downgrade.

## Backup and isolated restore

```sh
# Approved Docker DB, private new file. Use k3s mode for the owned postgres-0 instead.
sudo env COMPOSE_FILE=/ABSOLUTE/docker-compose.yml DB_NAME=hosting \
  bash infra/hosting/db-backup.sh docker /PRIVATE/auth-new.dump
sudo bash infra/hosting/restore-check.sh /PRIVATE/auth-new.dump
```

Backup is PostgreSQL consistent `pg_dump -Fc` without owner/privilege credentials and mode 0600 + checksum. M1's control-plane backup and naive live PGDATA copying are not DB backups. Role provisioning/grants use the versioned migration/recovery input, with operator credentials held separately.

Restore checker creates a new non-root read-only/network-none PG container with only fresh tmpfs, no host ports/live PVC/host network. It restores atomically with errors fatal, checks auth schema/role/FK invariants and removes sessions/transactions in the copy. It prints counts, not identity/secret data, and deletes only its own container. Actual recovery must provision/re-grant DB roles, invalidate all restored sessions/transactions, reconcile post-snapshot approvals/revocations from trusted operational records, then validate/reopen auth. Backups can contain identity/session hashes/short-lived encrypted verifier data and must stay private/encrypted before any off-host transfer.

Daily + pre-schema-change backups and seven verified retained copies are proposed defaults; off-host destination, retention, RPO/RTO and operator recovery testing need operational handoff. No automatic off-host transfer is started. Host-only backups do not cover host loss.

## Actual GitHub vs public operation

Mock tests do not need an external app/secret. Actual GitHub flow requires an owned app, exact registered callback/client ID, private injected secret and a protected loopback origin. Never change an existing Supabase callback without owner approval. A single loopback Compose gateway plus SSH forwarding provides web/API under one origin; set AUTH_ORIGIN to that exact loopback address and enable only loopback insecure verification. Production k3s config initially disables OAuth and uses HTTPS origin, never weakens cookie security for public HTTP. Real callback verification is a separate acceptance record.

Public domain/DNS/TLS is subsequent work. Public real-session operation requires HTTPS origin/callback/Secure cookies, owned provider setup, audited approvals, persistence and recovery readiness. M2 local/mock verification is not public HTTPS operation completion.

For k3s operator account changes, `render-approval.sh` emits an ops-labelled non-root Job with the private operator credential. Supply verified GITHUB_ID, APPROVED, ROLE, ACTOR and REASON plus API_IMAGE, inspect it, then use `kubectl create -f` only under the approved operation. No ID/default administrator is selected by the script. It uses generateName for an auditable one-shot execution; retain status/log outcome and remove only the completed owned Job if desired. The direct CLI remains available for audit text outside the renderer's safe ASCII literal set.
