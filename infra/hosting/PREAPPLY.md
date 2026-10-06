# M2 public IPv4 / Alembic pre-application packet

Issue [#4](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/4), branch `task/web-api-hosting`. The public-IP HTTP and Alembic decision supersedes the old mandatory loopback/SSH plan. **PM owns actual operation**; back provides code, isolated Docker evidence and this proposed sequence, review independently checks it. Source/plan approval at 4d57d92 covered the previous scope and does not approve these amendments or establish live acceptance.

## Required operator inputs

PM confirms the actual public IPv4, external port and exact browser origin before rendering. Use `http://<actual-public-IP>` for port 80 (omit `:80`) or `http://<actual-public-IP>:<port>`. No operational IP/port has been chosen by the renderer or by a test fixture. Web build is standalone with empty NEXT_PUBLIC_BASE_PATH: web `/`, API `/api`. GitHub callback is exactly that origin plus `/api/auth/github/callback`.

Before actual GitHub flow, obtain the owned OAuth app's client ID and confirm its exact callback registration. Supply private root-readable paths for database admin password, API database password/URL, operator database URL, transaction key and GitHub client secret; never send values in issues/messages or build arguments. Do not alter existing Supabase callbacks/apps. Registration acceptance and actual GitHub roundtrip remain to be verified; mock results do not predict provider acceptance.

Set `ALLOW_PUBLIC_IP_HTTP=true` only with the reviewed numeric globally routable IPv4 origin. Domain/private/metadata/reserved HTTP origins and noncanonical/default port forms are rejected. Real mode still validates Host/protocol/port; forwarded protocol requires the selected Traefik sources and scoped ingress policy. Traefik Host rules select the IP; Host itself does not enforce port, so PM must verify the existing entrypoint/ServiceLB external port matches the configured origin. No global Traefik/firewall changes are proposed.

The user authorized temporary unencrypted HTTP. HTTP session/transaction cookies retain HttpOnly, SameSite=Lax and scoped paths, omit Secure and have no Secure-name prefix. HTTPS remains the default and restores Secure. Session hashes bind to the exact origin: HTTPS/domain/port changes reject earlier tokens, preserving account/permission/audit rows and requiring login again. Legacy unbound session rows are preserved by migration but do not authenticate on the new code. Changing origin also requires reviewing GitHub registration, Ingress and web public settings together.

## Release provenance

The historical d1d10a0 API image lacks Alembic and these origin changes and **must not be used for this target**. Its previous successful runtime results remain historical in VERIFICATION.md. Build the new API from the final reviewed clean commit using backend/Dockerfile runtime target, full APP_GIT_SHA and UTC APP_BUILT_AT. Build/reconfirm web with infra/hosting/web.Dockerfile, WEB_BUILD=standalone, NEXT_PUBLIC_BASE_PATH empty, full WEB_GIT_SHA and WEB_BUILT_AT. Locked dependencies and pinned bases remain mandatory.

Clean source **c3191629e5a484131b5b5cfc4c8ba1f19c5cefc0**, built at **2026-10-06T16:35:21Z**, was used for both new linux/arm64 images with the exact targets/args above. The worktree was clean before both builds. Actual `/api/version` and `/version.json` HTTP responses returned exactly that full SHA/time and no-store. Local Docker RepoDigests:

| Image | Local OCI digest |
| --- | --- |
| anxious-hosting-api:c3191629e5a484131b5b5cfc4c8ba1f19c5cefc0 | sha256:5f611d76daec8589ff255780affb2d7c45d6b661bda9f64aaba1e7c141811930 |
| anxious-hosting-web:c3191629e5a484131b5b5cfc4c8ba1f19c5cefc0 | sha256:1e2dd644e574b28f99ed2f3a7212eecefae8e132c04dbbd6cf27ca56add3f727 |

Runtime HTTP and Chromium mock-provider checks passed with these images, PostgreSQL and real Docker Traefik; both app containers were UID/GID 10001, read-only with /tmp only. The new runtime API also completed the explicit Alembic command on the existing disposable test DB under 128Mi/0.2CPU/non-root/read-only restrictions. These are Docker evidence, not actual GitHub or cluster application.

Record each exact SHA/time/platform and OCI digest, archive/checksum and verified k3s containerd import name as one release bundle. Docker build availability does not imply kubelet availability. PM supplies actual immutable refs to render.sh and render-migration.sh after verifying archives/import names; neither archive/import nor actual target rendering with the still-unconfirmed operational origin has been performed here. Subsequent documentation-only provenance commits do not change these runtime inputs. BuildKit could not auto-capture Git metadata in this worktree; explicit source args, clean Git checks and actual HTTP payloads establish the recorded mapping, not an unverified automatic Git attestation.

## Owned target and safe stages

Only new `m2-hosting` Namespace, `m2-postgres-retain` StorageClass, hosting ServiceAccount/ConfigMap, scoped CNP, owned PostgreSQL StatefulSet/headless Service/PVC, API/web Deployments/Services, owned IngressRoute and explicit operator Jobs. No existing volume/default SC/M1 policy/Cilium setting/firewall/Docker hermes/android-agent resource changes. API uses only its restricted credential; operator and PostgreSQL credentials stay separate. No automatic schema migration, seed approval or administrator selection.

```sh
M2_PUBLIC_ORIGIN='ACTUAL_REVIEWED_HTTP_ORIGIN' \
  WEB_IMAGE='ACTUAL_WEB@sha256:ACTUAL_DIGEST' API_IMAGE='ACTUAL_API@sha256:ACTUAL_DIGEST' \
  bash infra/hosting/render.sh > /PRIVATE/RELEASE/workloads.yaml
API_IMAGE='ACTUAL_API@sha256:ACTUAL_DIGEST' \
  bash infra/hosting/render-migration.sh > /PRIVATE/RELEASE/migration.yaml
```

Review actual output/diff and refresh the baseline before application. Split the rendered stream without dropping/duplicating resources into: (00) cluster-scoped Namespace/StorageClass; (00-SA) namespaced ServiceAccount; (01) CNP; (02) ConfigMap/private Secrets; (03) database; (04) apps; (05) route. **Do not combine the absent Namespace and namespaced resources in one server dry-run.** Successful dry-run does not create the namespace. SA and subsequent server dry-run/diff wait for explicitly authorized Namespace creation and an existence check.

PM's proposed execution:

1. Record image import/private inputs/disk and resource capacity; collect existing `/android-agent` assets/feedback/skills and Docker hermes health/StartedAt/DNS/HTTPS baseline, plus M1 authorized-source controls.
2. Server dry-run/diff stage 00, then approved apply; require Namespace existence. Server dry-run/diff/apply SA and policy, wait for realized Cilium policy. Retain global node/API/metadata/own-public-IP protections.
3. Review/apply ConfigMap and private Secrets; start only new owned PG/PVC and verify volume ownership plus readiness. OAuth initially disabled. No existing directory/PVC initialization.
4. Server dry-run/diff the inspected **auth-alembic-0001** Job with new API ref. Run `python -m app.migrate --revision 0001_auth` using the ops credential; wait for completion, retain sanitized result and verify exact auth.alembic_version plus real app-role readiness. A failed Job stops app rollout. Re-run requires inspecting and removing/recreating only the failed/completed owned Job, without clearing DB data.
5. Roll out API/web one controller at a time and wait for old Pods to disappear/minReady. Check probes/version/security context/policy and actual CPU/memory/restart/Pending/OOM/disk values before exposing the owned route.
6. Expose exact numeric IP Host web `/` and API exact `/api` or `/api/` prefix. Web excludes `/android-agent` and `/android-agent/` entirely, leaving the existing route intact. `/apis` goes to web. Unspecified Host/IP must not acquire a root/API fallback. Compare baseline and existing app responses.
7. After verified app ownership/registration/private injection, set real OAuth config and verify actual public-browser GitHub success/denial/session/CSRF/logout and pending account persistence. Isolated fixtures are not this acceptance result.
8. Create/check logical backup and restore-test in a fresh isolated DB; record separate role/grant/key recovery inputs. Verify later app-release rollback using previous compatible images/config/routes with DB/PVC/schema retained.

Useful explicit wait after the approved migration apply:

```sh
sudo /usr/local/bin/k3s kubectl -n m2-hosting wait \
  --for=condition=complete job/auth-alembic-0001 --timeout=120s
```

These are reviewed-plan commands, not operations executed by back. No new public-IP server dry-run, Secret/PVC/Pod creation, Cilium realization, GitHub registration/roundtrip or cluster rollout is claimed.

## Migration, recovery and rollback

Alembic revision `0001_auth` creates an empty DB schema or adopts only structurally verified original v1 tables. The serialized SQLAlchemy transaction uses a PostgreSQL advisory lock and commits DDL/grants/version together. Missing or altered legacy contracts fail without stamping/repairing them. Repeat/concurrent runs retain existing data and DB role password. Only explicit reviewed revision is accepted; no `head`, blind stamp, startup migration, automatic downgrade or reset. Restricted API role has SELECT on both version markers, no version/role/approval/audit writes.

Backups include both auth.schema_version and auth.alembic_version. Restore checker requires 0001_auth when an Alembic marker exists; original legacy v1 is recognized with a required reviewed upgrade before new API readiness. Unsupported/multiple markers fail. It invalidates sessions/transactions only in the fresh copy, retains account/approval/audit rows and never mounts the live PVC. Production recovery must re-provision grants, invalidate copied credentials and reconcile later approvals/revocations before reopening auth.

Normal rollback restores previous compatible app images and exact public settings/routes while retaining database/storage/policies. An initial install failure withdraws only owned route/app Deployments after inspection. No Namespace/PVC/PV removal, broad prune, PostgreSQL major downgrade or Alembic downgrade. The older v1 application is schema-compatible with additive Alembic metadata, but its old unbound session hashes require login again; returning to its HTTP-origin restrictions requires matching disabled/compatible config and a separately reviewed access plan.

Initial steady request sum remains 250m CPU/480Mi, limits 1250m/1280Mi. Sequential web surge (web2/API1/DB1) is nominal 350m/608Mi, limits 1750m/1792Mi; overlapping terminating Pods can exceed it. These calculations do not replace PM's actual node/load/latency measurement. Daily/pre-schema backup plus seven verified copies remain proposed defaults; off-host destination/retention/RPO/RTO/operator recovery testing need handoff. Domain/DNS/TLS comes later and does not convert mock/public-HTTP results into HTTPS acceptance.
