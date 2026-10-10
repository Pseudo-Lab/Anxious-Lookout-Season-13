# Existing m2-hosting: native-disabled M3 operating candidate (#7)

PM executes only after exact source/operating review. These helpers emit resources
or test an isolated restore; no helper applies Kubernetes changes automatically.
Preserve existing origin/basePath, GitHub registration/hosting-config, hosting-api/
hosting-ops, PostgreSQL/PVC, auth accounts/sessions/roles and unrelated applications.
No new DB/OAuth/client IP/trial UI is a bootstrap prerequisite. Native enable,
mapping/source supply/TLS/root-gate changes are outside this rollout.

Runtime API is the reviewed b19 image41403…; web candidate is approved empty-base
edff image02b397…, not prefix b19. Runtime source remains unchanged by these infra
helpers; API/web release metadata must be recorded separately. API/current old M2
image843880… is retained for compatibility/rollback. No uniform-SHA rebuild needed.

## Review inputs and safety gates

Before execution PM captures private exact Deployment/api and web JSON, hosting
config UID/RV/unchanged effective origin/callback/FILE references, relevant Secret
UID/key metadata (no values), DB/PVC/role/revision identity, controller/route/policy
baseline, actual image/CRI refs and eligible-node capacity. Reconcile ownership and
all fixed CM/Job name collisions before create; never adopt/delete foreign objects.
Original target was API/web source684d627, auth0001/no research, API replicas1,
RollingUpdate maxSurge1/maxUnavailable0, requests50m/96Mi, limits250m/256Mi.

API patches preserve that strategy and the complete approved spec except image and
explicit CODEX_PERSONAL_ENABLE=false/account empty/map empty env. Confirm one extra
API Pod can fit (additional50m/96Mi requests,250m/256Mi limits); ops adds25m/64Mi
requests,200m/192Mi limits. Web capacity uses its separately reviewed baseline.
maxUnavailable0 keeps the old ready replica during failed new readiness, but does
not promise zero errors/latency: rolling mixed versions and DB locks need actual
observation. No silent Recreate/scale-down or unreviewed maintenance window.

## Ordered PM procedure

1. Reuse `infra/hosting/db-backup.sh k3s /PRIVATE/new-pre-m3.dump` (sudo as appropriate,
   DB_NAME=hosting). It runs consistent pg_dump --no-owner/privileges -Fc and atomic
   no-overwrite checksum publication. Private parent0700/archive+checksum0600,
   owner-controlled path outside Git/build/shared artifacts. Collisions/partial
   locks are inspected; never clear/reset an unknown writer's lock. Do not copy
   live PGDATA, print private dump/rows or treat a host control backup as DB backup.
2. Run `sudo bash infra/hosting/m3/rehearse.sh ARCHIVE NEW_PRIVATE_REPORT` using
   exact checksum-bound archive. It creates one fresh UID999 readonly/network-none
   PG18.6 container with bounded512Mi tmpfs and no ports/live PVC/credentials;
   clients share only that disconnected network namespace. Actual API-b19 and old
   API684 images must be locally available; --pull=never. This restores auth data,
   reconstructs only frozen clone AUTH grants/role (dump excludes them), runs
   unmodified0004 migration, and verifies every restored auth table row and clone
   role password unchanged. It does **not invalidate clone sessions/transactions**,
   unlike the recovery-specific old restore-check. Actual old/new API images then
   check /healthz,/readyz and unauth /api/auth/me401 on the upgraded isolated DB.
   Receipt/diagnostics stay private at REPORT/REPORT.work. Only UID/label-owned
   Docker resources are cleaned; original archive is never removed. Timeout or
   cleanup ambiguity holds live progression. Disk/memory fit and actual dump
   restore remain operator-specific, not inferred from synthetic tests.
3. Render checker, before, migrate and after **individually** with prepare.py in
   readonly/network-none API image, source directory mounted /checks. Confirm
   immutable CM/checker hash, job app=hosting-ops/SAhosting/tokenless/UID10001,
   old Secret FILE refs and actual existing ops Cilium/DNS/DB applicability before
   creating any Job. Do not replay aggregate hosting/render.sh: it would overwrite
   OAuth/config defaults and recreate unrelated foundation/data resources.
4. Create CM/hosting-m3-verifier-v1 absent, then Job/hosting-m3-before-v1 once.
   Wait within180s for Complete/exit0, source image association and generic checker
   status ok. It uses current API/admin credentials to verify target hosting/
   postgres m2 service, matching API password, frozen auth0001, existing nonsuper
   API role and no research schema in readonly transactions. Missing/changed
   source state/credential/role fails before migration; no creation/reset repair.
   Nonempty URL queries are unsupported and refused before engine/connection,
   including host/user/password/dbname/hostaddr/service/options/SSL query settings.
   After connect, current_database=hosting and current_user/session_user must both
   be postgres for admin or anxious_api for API. Do not edit existing Secrets or
   bypass this check to make unsupported input pass; reconcile exact inputs.
5. Only after backup+actual rehearsal+before/policy/owner evidence, create
   Job/hosting-m3-migrate-v1 once. It runs the existing baked CLI
   `python -m app.migrate --revision 0004_publication`; no code/0001-renderer
   edits on target. Advisory transaction/validation/grants are existing code;
   PGOPTIONS bounds statement120s/lock10s, Job180s/backoff0. Do not mark HTTP/Pod
   running as schema success. Complete/exit0 and frozen004 contract/grants are
   required. Failed/unknown commit preserves evidence and old service; reconcile
   actual marker/contracts before any separately reviewed retry, never rerun/reset.
6. Create Job/hosting-m3-after-v1 once, verify auth0001/research0004 and API effective
   structure/grants using readonly helper, Complete/exit0. It never changes account
   permissions, passwords or sessions. Live concurrent logins can change auth rows;
   do not label before/after live fingerprints as migration damage or impose an
   unapproved login outage. Row preservation is proved in the actual isolated dump
   rehearsal plus transaction/source validation, not by pretending live traffic froze.
7. Re-read API spec/UID/RV and reconcile against the approved original. Render the
   UID/RV-test JSON patch (`prepare.py api --original ... --current ...`) privately;
   API uses image41403 and explicit native=false/map empty. After exact diff/dry-run
   and PM review apply as JSON patch to Deployment/api only. Do not patch hosting
   ConfigMap/origin/OAuth/Secrets/DB/CNP. Observe bounded rollout/new actual Pod
   imageID/readiness/version=b19, old Pod termination and existing HTTP auth/session
   behavior. Unavailable new Pod must leave old ready replica; stop if ownership,
   resource pressure, policy/readiness, version or user-data behavior is ambiguous.
8. **API+004 successful first; web later.** Root web export/import/audit uses the
   exact edff policy below, verified imported reference and actual Pod association.
   Render the existing image-only web patch with that verified ref against approved
   web metadata, review/dry-run and apply only after API ready. Version=edff,
   empty-base assets/API links and actual existing login/CRUD/native-not-configured
   behavior are operator tests. User performs interactive login; no automatic
   paid dispatch, auth/start or user request replay by this preparation.

Use bounded foreground command chunks and preserve each actual command/status/UID.
There is no watcher, automatic Job retry or phase chain. Completed/failed Job/Pod
cleanup requires exact ownership and termination evidence; record missing/unknown
results, retain backups/checker/evidence as appropriate and do not delete namespace,
PVC/DB or source sessions. Original deployment/backup/permission tools stay unchanged.

## Root image audit and provenance

`image_audit.py root-web VERIFIED_IMPORT_REF PRIVATE_EVIDENCE [MINIMAL_POD_JSON]`
runs offline in Docker as the verified evidence owner. Reuse existing reviewed
export/import procedure for **only the root edff image**, preserving existing refs.
Capture local-target/local-manifest/local-config and actual target/manifest/config/
CRI raw JSON with file/blob hashes. The new sealed policy binds independent local
approved ID02b397… to the actual index/config semantics, linux/arm64 platform chain,
same platform manifest+config bytes after import, exact library repository and CRI.
API policy remains b19/41403; root audit never substitutes those constants. Missing
or converted/mismatched bytes hold review, not manual auditor edits/retagging.
Output ref comes from verified actual target; do not assume Docker ID is the
containerd target or that local availability implies registry pullability.

Existing root image source/build/dependency approval is
`artifacts/front-issue7-root-web-applicability-20261010.md` in comm and PM's narrow
root applicability verdict. Actual import/CRI/Pod mapping is still a separate gate.
Web/API release SHAs differ by design and source equivalence; do not restamp them.

## Rollback and limits

**Web first→API second**, if restoring the original app. Reconcile each original
UID/spec and actual current RV, image readiness/route/config and user behavior.
The API rollback emitter tests current M3 image/disable env against original full
spec and restores only original image/env. Other spec changes refuse, requiring
review. Keep new research schema/data/idempotency, original DB/PVC/keys/account/
session/role state; no down migration/DB reset or live restore for ordinary rollback.
If schema/commit is unknown or old image compatibility fails, stop rather than
assume `rollout undo` repairs DB/config. Separate verified backup recovery requires
its own explicit owner review.

Isolated actual old/new image auth readiness does not prove both frontend/API
mixed browser combinations, real OAuth redirect or live load/zero downtime. M3 web
before M3 API can show research404; order avoids that expected interval. M2 web
does not consume/delete M3 pending sessionStorage, even on M2 logout. Pending data
can remain/recover for the same account on later M3; no automatic resend or browser
cleanup is added and tab/browser closure is not guaranteed deletion. Preserve server
idempotency/history. See front's corrected document; current browser combinations
remain actual operator observations, not a falsely claimed fixture success.

TLS/root supported native-origin change and actual server source/whole-lifetime
refresh/endpoint inventory are parallel preparation, not gates for this disabled
rollout. Existing public HTTP/root cannot enable current remote native guard; exact
HTTPS/root opt-in and existing-user impact need separate review before activation.
Selected existing source's compatibility/ownership and all same-grant consumers/
post-test continuity are first private owner metadata checks. Existing independent
grant must be proved, not created/assumed; same-grant coordination beyond current
trial lease requires explicit owning-client implementation/rights. Source supply,
runner startup/provider/model(max3) and rejected public posting remain held.

Validation: `sudo bash infra/hosting/m3/check.sh /tmp/issue7-m3-check-UNIQUE` runs
unit patches/jobs/root-audit refusal and populated synthetic M2 dump→restore→0004,
actual old/new image auth readiness and before/after checker in Docker network-none
contexts. No live DB/DDL/rollout involved. The first external-script runtime failed
without PYTHONPATH=/app; fixed explicit env is now emitted for all Jobs and actual
rehearsal. Initial failures are not passing evidence.

Final author evidence (2026-10-10): Docker unit10 passed; populated synthetic M2
custom dump restored and upgraded0004 with all auth rows and clone role password
preserved; actual API843880(M2) and41403(M3) passed health/ready/auth-me401 on that
upgraded clone; actual mounted before/after checker both returned status ok. Evidence
is private `/tmp/issue7-m3-check-round5/`; no original DB was backed up/migrated by
the author. Synthetic admin input was corrected to satisfy the nonempty-password
preflight. PG readiness now requires final PID1=postgres plus pg_isready, avoiding
the temporary init server shutdown race. No failing earlier run is claimed passed.
