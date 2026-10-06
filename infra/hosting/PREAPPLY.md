# M2 pre-application review packet

Issue #4; shared branch task/web-api-hosting. This packet is **not application authorization**. Code/Docker verification is complete as recorded in VERIFICATION.md; live GitHub and k3s/network/rollback checks remain unexecuted.

## App release verified in Docker

Final app build source: **d1d10a0176f621c0d5bd8b568968005d7b6520a3** (includes front 91a5500). Built from a clean source at 14:34:58 UTC on 2026-10-06 with that SHA/time in both image metadata. This supersedes the earlier 305c6f6 image pairing; the historical result remains in VERIFICATION.md. Subsequent readiness/driver/documentation amendments do not alter runtime Dockerfiles or application code and are recorded separately.

- web: anxious-hosting-web:d1d10a0176f621c0d5bd8b568968005d7b6520a3; local OCI digest sha256:9ef19d77ad1c7b105aeffcca6223d1e2cb0379c2b1436a31b5589fd689fb0d71
- API: anxious-hosting-api:d1d10a0176f621c0d5bd8b568968005d7b6520a3; local OCI digest sha256:bc03d01170fb5a9f73d2c884adf291c5707c09320ff878c51e4f32c5bbaf5b5c
- PG: postgres:18.6-bookworm@sha256:afc7e2d441324c0388fa80c3d24f733b4194a4eb7f47dd8ee2b08eb1a24a647c

Before apply, operator saves/checksums/imports app images into k3s containerd and confirms actual digest-qualified names/platform in that store. Do not render an assumed Docker tag/digest mapping or run a Pod before the import record is verified. No registry or firewall change is part of the handoff.

Build input mapping: root context at d1d10a0; backend/Dockerfile **runtime** target with APP_GIT_SHA=d1d10a0 full SHA and APP_BUILT_AT=2026-10-06T14:34:58Z. Web uses infra/hosting/web.Dockerfile default WEB_BUILD=standalone, NEXT_PUBLIC_BASE_PATH empty, WEB_GIT_SHA/full SHA and WEB_BUILT_AT/same timestamp, locked pnpm9.15.9+pnpm-lock.yaml, Node pinned digest. NEXT_TELEMETRY_DISABLED=1 and the final WEB_BUILD switch are included in this rebuild. The API's d1 test-stage fake producer never enters runtime. Renderer resource budget (API 96Mi request/256Mi limit) is an orchestration input in the reviewed target, not an image layer. Both images inspected ARM64 and returned exactly the above SHA/time through version.json/release.json.

## Target and expected diff

Renderer creates only namespace m2-hosting, SA hosting, ConfigMap hosting-config (OAuth disabled; HTTPS test Host), new StorageClass m2-postgres-retain, PostgreSQL StatefulSet/headless Service/new data-postgres-0 PVC, API/web Deployments/ClusterIP Services, owned hosting IngressRoute and hosting-boundary CNP. Explicit auth-migration-v1 and generated operator-approval Jobs are separate inspected actions.

Default StorageClass, existing PVs/volumes, M1 policies/Cilium Helm/firewall/Traefik defaults, Docker hermes and android-agent-download resources are not changed. No public DB port/Dashboard/hostless root/TLS route, no token mount, no app privilege label inherited from materials-api/agent-manager. API receives only its own low-privilege credential. PG/operator Secrets are separate, with no values committed.

Review actual rendered output and namespace prerequisites, then operator server dry-run/diff. At 13:30:47 UTC the preliminary placeholder workload target passed client schema validation; at 14:26:10 UTC the target with actual 305c6f6 app digests and updated API resource budget passed client schema validation; at **14:43:51 UTC** the final target using the rebuilt d1d10a0 digests above passed client schema validation. None created resources or proves namespace, Secret, PV binding, image import, probes or realized policy readiness. Policies must be applied and realized before exposure/workloads are trusted. Global node/API/metadata/own-public-IP guard stays installed.

## Baseline obtained read-only

2026-10-06 14:21 UTC: Traefik ServiceLB's node IPv4 `/android-agent/` returned HTTP 200 HTML. Probing 127.0.0.1:80 earlier was refused because this listener is not a loopback endpoint; the collector now resolves the actual ServiceLB IP. hermes remained running/healthy, StartedAt 2026-09-25T14:34:36.142048522Z. k3s, Docker, firewalld and firewall-sync timer remained active. No new live auth namespace/DB/PVC was created by validation. Detailed before-state is in the operator-private baseline directory relayed to PM, not committed with host/network details.

Refresh baseline immediately before approved apply. Add representative android assets/feedback/skills responses and hermes DNS/HTTPS positive controls, then compare unchanged after apply. M1 network regression requires authorized controls and Cilium packet-drop evidence; the current ready/policy snapshot is not such a pass.

## Proposed operation and rollback boundaries

1. After reviewer/PM target review and explicit operation authorization, verify app archives/import, private Secret inputs and disk/CPU/memory/surge budget. Render actual names/digests; confirm new resources only.
2. Establish new namespace/SA/Retain storage/policies/config/secrets; wait for policy realization. Create PG and verify correct owned PVC/modes, pg_isready plus actual app-role/schema check after explicit migration. Never mount or initialize an existing host directory/volume.
3. Run inspected migration Job with operator credential, wait for completion; start web/API, wait for rollout/exec probes and collect actual resource values. Config initially disables OAuth; public HTTP cannot issue real sessions. Do not call mock/disabled configuration actual GitHub acceptance.
4. Verify dedicated Host m2.invalid web/API, exact API boundary, assets/version/cache/404, unspecified Host exposure, DB isolation and all existing service/M1 regressions.
5. Create/check/restore-test logical backup and record private credential/grant recovery inputs. Update to a second reviewed app release, then restore previous web/API/config bundle while **retaining DB/schema/data** and recheck actual version/assets/readiness/routes. First-install failure withdraws only owned public route/app Deployments; DB/PVC/SC/policies are retained for inspection. No namespace/PVC/PV deletion, broad pruning, down migration or PG downgrade.

Normal rollback is previous app images + matching public settings/routes, not live DB historical restoration. Any DB incident restore requires separate authorization, a verified backup, re-provisioned roles/grants, invalidated sessions/transactions and trusted approval reconciliation with auth kept disabled until complete.

## Secret-free real-login verification procedure

Concrete protected staging origin: **http://127.0.0.1:28000**. Exact callback: **http://127.0.0.1:28000/api/auth/github/callback**. The reviewed staging Compose gateway binds server loopback; verifier forwards it over SSH:

```sh
ssh -N -L 28000:127.0.0.1:28000 VERIFIED_USER@VERIFIED_SERVER
```

Browser uses the local origin. The owner verifies OAuth app ownership, client ID and callback registration separately, then supplies only a private secret file location through the approved provisioning path. No secret values in messages/issues. API config uses that exact origin and explicit loopback-only HTTP verification flag. Real auth validates incoming Host/protocol; do not simply enable real OAuth on the public HTTP k3s route. Compose staging data/service creation itself requires its own reviewed target/data boundary; it has not been started with real credentials here. Alternative cluster protected access must satisfy the same Host/protocol/proxy source checks and be reviewed before use.

Actual GitHub success/denial/logout and persistent account/session checks remain a distinct acceptance record. Public DNS/domain/TLS and real public operating sessions are subsequent work. Off-host backup destination, retention/RPO/RTO and operator recovery schedule remain handoff decisions.

## Sequential reviewed command plan and surge evidence to collect

Final workload file prepared for review: /tmp/m2-final-d1d10a0-target.yaml (actual refs above); JSON stages /tmp/m2-stage-00-bootstrap.json through /tmp/m2-stage-05-exposure.json plus /tmp/m2-stage-00-serviceaccount.json are generated from its client-decoded JSON stream, with one copy of each resource. **Bootstrap contains only cluster-scoped Namespace/StorageClass**; the ServiceAccount is its own namespaced stage. CNP, config, database, apps and exposure remain separate. Do not apply the full renderer output at once. A successful Namespace server dry-run does not create that namespace. Future authorized sequence:

```sh
# Operator authorization + image/import/private-input checks first.
sudo k3s kubectl apply --dry-run=server -f /tmp/m2-stage-00-bootstrap.json
sudo k3s kubectl diff -f /tmp/m2-stage-00-bootstrap.json
# Only after explicit namespace/storage creation authorization:
sudo k3s kubectl apply -f /tmp/m2-stage-00-bootstrap.json
# Require the deliberately created namespace to exist before any namespaced server check.
sudo k3s kubectl get namespace m2-hosting
sudo k3s kubectl apply --dry-run=server -f /tmp/m2-stage-00-serviceaccount.json
sudo k3s kubectl diff -f /tmp/m2-stage-00-serviceaccount.json
sudo k3s kubectl apply -f /tmp/m2-stage-00-serviceaccount.json
# Server dry-run/diff each subsequent stage in that now-existing namespace before apply.
sudo k3s kubectl apply --dry-run=server -f /tmp/m2-stage-01-policy.json
sudo k3s kubectl diff -f /tmp/m2-stage-01-policy.json
sudo k3s kubectl apply -f /tmp/m2-stage-01-policy.json
# Wait for Cilium to accept/realize policy; inspect exact owned rules before workloads.
sudo k3s kubectl apply -f /tmp/m2-stage-02-config.json
# Inject reviewed Secret files privately; never paste generated Secret YAML in messages.
sudo k3s kubectl apply -f /tmp/m2-stage-03-database.json
sudo k3s kubectl -n m2-hosting rollout status statefulset/postgres --timeout=180s
# Render, server-dry-run/diff and run explicit migration Job with actual API ref.
sudo k3s kubectl apply -f /tmp/m2-auth-migration.yaml
sudo k3s kubectl -n m2-hosting wait --for=condition=complete job/auth-migration-v1 --timeout=120s
sudo k3s kubectl apply -f /tmp/m2-stage-04-apps.json
sudo k3s kubectl -n m2-hosting rollout status deployment/api --timeout=300s
sudo k3s kubectl -n m2-hosting rollout status deployment/web --timeout=300s
# Inspect probes/version/volumes/policy realization before public route.
sudo k3s kubectl apply -f /tmp/m2-stage-05-exposure.json
```

These commands are proposals, not commands executed in this session. CLI path is /usr/local/bin/k3s on this host. An absent namespace/credential/precondition is a stop condition, not permission to bypass validation. Refresh baseline immediately before an approved run; original state contains no m2-hosting resources, so diff must be additions only. First-install rollback removes only the owned route and app Deployments after inspection, preserving CNP/PG/SA/SC/PVC and any initialized data; a later rollout restores the previous image/settings bundle.

Review correction: the old combined Namespace/SC/SA bootstrap failed server dry-run at the SA because m2-hosting did not exist. The prepared split cluster-only file now passes server dry-run without creating resources; SA passes client schema check only. SA and later namespaced server dry-run/diff are deliberately deferred until authorized Namespace creation. No temporary namespace was created just to make validation pass.

Initial steady request sum: 250m CPU/480Mi memory; limits 1250m/1280Mi. Updating API/web **one controller at a time**, waiting for old Pods to disappear and new minReady stability, gives a nominal maximum of web2/API1/DB1: requests 350m/608Mi, limits 1750m/1792Mi. Concurrent rollouts/overlapping terminating Pods can exceed this estimate, so do not stack updates. The host has 2 CPU and existing system/apps; limits are not throughput or spare-capacity guarantees. Before and during approved rollout, record node allocatable/requests, top metrics, available memory/disk, Pending/FailedScheduling events, OOM/restarts and old/new Pod counts; verify existing app latency/responses and all probes. No actual cluster surge measurement has been performed, and these arithmetic estimates are not a pass.
