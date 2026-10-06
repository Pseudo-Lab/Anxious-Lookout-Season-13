# M2 verification record — 2026-10-06

Issue [#4](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/4) is the permanent acceptance record. Results here distinguish Docker validation from real GitHub/cluster operation.

## Backend Docker results

- ARM64 Python 3.13 runtime/test image builds with complete version-pinned runtime/test dependencies. PostgreSQL 18.6 bookworm and Python/Node bases are digest pinned.
- Final suite: **20 passed** (initially 15, extended with real-auth transport regression). Tests cover pending/nonadmin account defaults despite provider site_admin, service UUID/provider ID separation, opaque token hashing, same-origin/CSRF logout and stolen stale-cookie rejection, browser binding/expiry/reuse, simultaneous callback single-use, provider denial/token/user failure, identity rename without merge or permission reset, operator audit/revocation, explicit PostgreSQL insufficient_privilege SQLSTATE, database errors, routes/HEAD/metadata, public HTTP/test-mode rejection, disabled-login web redirect, HTTPS cookie flags, wrong/malformed Host and spoofed forwarded protocol.
- Actual Uvicorn HTTP + mock provider on internal Docker network passed login/approval/session revocation. Explicit test DB stop yielded 503 for a session lookup and readiness while liveness remained 200. DB container restart retained account/approval/audit and readiness recovered on a dedicated **new test volume**.
- Test-only metadata uses all-zero SHA/known timestamp and disposable credentials; it is not production release provenance. Runtime/test images differ by stage and mock provider is absent from runtime stage.
- Initial live-probe invocation failed because direct script execution omitted the package parent from Python path. It was corrected to module execution inside Docker, then all actual process probes passed. No host language toolchain fallback was used.
- pytest emits one Starlette/httpx deprecation warning; assertions passed. It does not indicate actual GitHub authentication.
- One idle Docker sample after validation: API 65.91MiB, DB 27.65MiB. Not a load/surge or cluster capacity benchmark.
- Root Compose validates with actual-format SHA/time. Blank `.env.example` release values intentionally reject accidental unversioned builds.

## Review fix and cumulative web/runtime verification

- Review at 305c6f6 reproduced a checksum-collision cleanup defect in the first backup tool on disposable fixtures. No operational backup/data loss was observed. The fix checks both destinations/symlinks, serializes a destination with an owned mkdir lock, writes private temporaries and uses non-overwriting hard links. Cleanup never deletes public/existing dump/checksum paths. Seven Docker tests cover existing dump/checksum, symlink, producer failure, successful checksum/private mode, competing writers and a checksum created mid-dump.
- Final suite including a real mock-login secret-file precedence/readability case: **28 passed**. The test-only fake producer is absent from runtime stage. Test orchestration forces project name anxious-s13-back-test to prevent `.env` COMPOSE_PROJECT_NAME from reusing deployment DB services.
- The corrected tool also created a real test-DB dump/checksum with explicit project name; checksum verification and isolated restore passed. Docker Bash syntax checks passed for all hosting scripts. Final rendered actual-app-digest workload target passed client dry-run/schema validation; no resources were applied.
- Cumulative source 305c6f6 includes front fixes 91a5500 and Pages workflow's build:pages command. Both runtime API and standalone Next images built for ARM64. Web runtime digest a0171ded3ba12a831394225d336727a87b603c550c4d31fd86c761fbeac0aad4, API runtime digest 272c1363758d404c2d6e6a8e055a46f09a28cad7ce82a32d0a4818f04e564efb. These verify app source at 305c6f6; later test/ops/backup fixes require their own source review and release record.
- Actual Docker Traefik 3.7.8 + those runtime images + PostgreSQL + private mock provider: HTTP checks passed web/API route boundary, 13 referenced immutable assets, no-store version, normalized missing-web 404/JSON missing-API 404, mock login/session and CSRF logout. Chromium passed pending identity, separate account/provider IDs, actual HttpOnly/Lax cookie, no localStorage credentials, 204 logout and 401 guest recovery. Web ran UID/GID 10001 read-only with only /tmp writable; no EACCES observed.
- Initial integration driver incorrectly expected a missing web URL's initial response to be 404 rather than following Next trailing-slash redirect, and expected automatic logout URL navigation rather than the contractual cleared auth/UI state. Both driver assumptions were corrected before recording the successful checks; web source was not changed to satisfy them.
- Pages Docker builder ran the actual build:pages at prefix /Anxious-Lookout-Season-13, produced **2.3 MiB out/** including index/login/404. Chromium static-serving check passed API-disabled login UI and **zero API requests**. No GitHub Pages activation/workflow dispatch/deploy occurred.
- A post-integration idle Docker sample: API 66.02MiB, web 36.72MiB, DB 23.34MiB. API request/limit was adjusted to 96/256Mi; these are not production load/surge capacity evidence.

## Prepared, not applied

`render.sh` emits dedicated namespace/SA/ConfigMap, new Retain StorageClass/PVC, non-root PostgreSQL and web/API resources, exact Host/API boundary and scoped Cilium policies. `render-migration.sh`/`render-approval.sh` emit explicit operator Jobs only. No production Secret values or administrator ID are committed. No script automatically applies these resources.

Existing k3s resources/data/roles, GitHub OAuth apps and Supabase data have not been changed. Real GitHub code/token/user/session/logout flow, production web/API image release/digests, k3s rollout/Traefik routing, resource/surge measurements, and M1/Docker/android service regressions require separate evidence before acceptance. Control-plane backups do not establish application DB recovery.

The rendered workload/Cilium/Traefik/migration/approval resources passed **client dry-run/schema validation** against the existing cluster, with placeholder app digests. This created no resources. Server dry-run after namespace prerequisites, Secret/volume binding, Cilium realization and actual packet/HTTP regressions remain separate operational checks.

## Backup and remaining evidence

An initial logical auth backup restored in a read-only non-root/network-none PostgreSQL container with fresh tmpfs, schema/role/FK invariants passing. The stronger fixture reported **1 live session and 2 transaction rows before invalidation**, **1 retained account / 1 approval audit**, and **0 live sessions after invalidation**. Thus copied credential invalidation was exercised on nonempty tables. The restore container cleaned up its own fresh tmpfs and never mounted the live/test database volume.

Off-host backup destination/retention/RPO/RTO and post-snapshot approval reconciliation remain operational handoff items. Actual cluster application must be preceded by target/diff/baseline/rollback review. A mock/loopback pass is not public HTTPS operating acceptance.
