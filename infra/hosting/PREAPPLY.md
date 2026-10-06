# M2 pre-application review packet

Issue #4; shared branch task/web-api-hosting. This packet is **not application authorization**. Code/Docker verification is complete as recorded in VERIFICATION.md; live GitHub and k3s/network/rollback checks remain unexecuted.

## App release verified in Docker

App source: 305c6f69099cafc199f640316419d5b07fc49633 (includes front 91a5500). Web/API actual immutable build metadata uses that source SHA. Ops/backup/test fixes after it are reviewed separately; do not label the app images as built from a later documentation/ops-only SHA.

- web: anxious-hosting-web:305c6f69099cafc199f640316419d5b07fc49633; local OCI digest sha256:a0171ded3ba12a831394225d336727a87b603c550c4d31fd86c761fbeac0aad4
- API: anxious-hosting-api:305c6f69099cafc199f640316419d5b07fc49633; local OCI digest sha256:272c1363758d404c2d6e6a8e055a46f09a28cad7ce82a32d0a4818f04e564efb
- PG: postgres:18.6-bookworm@sha256:afc7e2d441324c0388fa80c3d24f733b4194a4eb7f47dd8ee2b08eb1a24a647c

Before apply, operator saves/checksums/imports app images into k3s containerd and confirms actual digest-qualified names/platform in that store. Do not render an assumed Docker tag/digest mapping or run a Pod before the import record is verified. No registry or firewall change is part of the handoff.

## Target and expected diff

Renderer creates only namespace m2-hosting, SA hosting, ConfigMap hosting-config (OAuth disabled; HTTPS test Host), new StorageClass m2-postgres-retain, PostgreSQL StatefulSet/headless Service/new data-postgres-0 PVC, API/web Deployments/ClusterIP Services, owned hosting IngressRoute and hosting-boundary CNP. Explicit auth-migration-v1 and generated operator-approval Jobs are separate inspected actions.

Default StorageClass, existing PVs/volumes, M1 policies/Cilium Helm/firewall/Traefik defaults, Docker hermes and android-agent-download resources are not changed. No public DB port/Dashboard/hostless root/TLS route, no token mount, no app privilege label inherited from materials-api/agent-manager. API receives only its own low-privilege credential. PG/operator Secrets are separate, with no values committed.

Review actual rendered output and namespace prerequisites, then operator server dry-run/diff. Current client dry-run with placeholder app digests passed; it does not prove namespace, Secret, PV binding, image import, probes or realized policy readiness. Policies must be applied and realized before exposure/workloads are trusted. Global node/API/metadata/own-public-IP guard stays installed.

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
