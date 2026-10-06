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

## Prepared, not applied

`render.sh` emits dedicated namespace/SA/ConfigMap, new Retain StorageClass/PVC, non-root PostgreSQL and web/API resources, exact Host/API boundary and scoped Cilium policies. `render-migration.sh`/`render-approval.sh` emit explicit operator Jobs only. No production Secret values or administrator ID are committed. No script automatically applies these resources.

Existing k3s resources/data/roles, GitHub OAuth apps and Supabase data have not been changed. Real GitHub code/token/user/session/logout flow, production web/API image release/digests, k3s rollout/Traefik routing, resource/surge measurements, and M1/Docker/android service regressions require separate evidence before acceptance. Control-plane backups do not establish application DB recovery.

The rendered workload/Cilium/Traefik/migration/approval resources passed **client dry-run/schema validation** against the existing cluster, with placeholder app digests. This created no resources. Server dry-run after namespace prerequisites, Secret/volume binding, Cilium realization and actual packet/HTTP regressions remain separate operational checks.

## Backup and remaining evidence

An initial logical auth backup restored in a read-only non-root/network-none PostgreSQL container with fresh tmpfs, schema/role/FK invariants passing. The stronger fixture reported **1 live session and 2 transaction rows before invalidation**, **1 retained account / 1 approval audit**, and **0 live sessions after invalidation**. Thus copied credential invalidation was exercised on nonempty tables. The restore container cleaned up its own fresh tmpfs and never mounted the live/test database volume.

Off-host backup destination/retention/RPO/RTO and post-snapshot approval reconciliation remain operational handoff items. Actual cluster application must be preceded by target/diff/baseline/rollback review. A mock/loopback pass is not public HTTPS operating acceptance.
