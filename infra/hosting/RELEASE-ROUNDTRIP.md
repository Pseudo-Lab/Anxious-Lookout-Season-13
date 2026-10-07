# M2 compatible update and explicit previous-image rollback

Issue #4, PM-authorized remaining acceptance. Current live application source is 684d627, probe configuration 1617f7a; GitHub OAuth is enabled and an approved administrator exists. **Preserve current ConfigMap/Secrets, DB/PVC/accounts/audits/sessions, probes, policies and routes.** PM executes reviewed operations; back provides this candidate and procedure. The original full renderer defaults to disabled OAuth and is not an update/rollback tool for the active system.

## Candidate and original release mapping

The candidate is a clean approved **1617f7ac996a78cb0662eb7968148eb504d7ce76** rebuild at **2026-10-07T08:43:09Z**, linux/arm64, standalone web with empty base. Application/dependency/Dockerfile paths compare byte-identically against 684d627 in Git; no application change or migration is needed. New SHA/time metadata creates genuinely different immutable images and observable release versions. This is a release exercise using the same compatible application, not a same-digest restart or a product feature change.

| Release | API local Docker RepoDigest | Web local Docker RepoDigest | HTTP source/time |
| --- | --- | --- | --- |
| Original | anxious-hosting-api@sha256:843880452e3b53c62c46fc932356263461b43aeed23b0b3f46896f36b1767e6f | anxious-hosting-web@sha256:199a065195bfff1f8f8b67668dcd57c0ad7c4a53073bb1729380f38674ee4cfa | 684d627db464b2e531cf3fce3633e8f75aa93d44 / 2026-10-06T16:49:41Z |
| Candidate | anxious-hosting-api@sha256:cedb57e5e371f0433df8bea1070320a751bf8caf2fc13bfb8183b969aece2157 | anxious-hosting-web@sha256:f63661d7d177c46079006978404f23dbd8b3bf453caab2b0368d81f0399191d7 | 1617f7ac996a78cb0662eb7968148eb504d7ce76 / 2026-10-07T08:43:09Z |

Build commands use backend/Dockerfile runtime with APP_GIT_SHA/APP_BUILT_AT and infra/hosting/web.Dockerfile standalone, NEXT_PUBLIC_BASE_PATH empty, WEB_GIT_SHA/WEB_BUILT_AT. Both tags end `:1617f7ac996a78cb0662eb7968148eb504d7ce76-20261007T084309Z`. Locked dependencies/pinned bases unchanged. BuildKit cannot automatically capture Git metadata in this worktree; explicit clean-source checks/args and actual HTTP payloads establish mapping.

Private candidate archive/checksum: `/tmp/m2-back-update-1617-20261007T084309Z/images.tar{,.sha256}`, parent0700/files0600. It contains only these candidate images (no test stage/provider/live secrets), 155368448 bytes. PM checks checksum and imports it into k3s containerd, then records actual imported digest-qualified names/platform/manifest mapping. Local Docker RepoDigests are not proof that kubelet can use a particular name. Retain the already-verified original refs/images and their bundle; do not substitute the known migration-defect images c319162/79367ab or pre-Alembic d1d10a0.

Archive SHA256: `59f686f0d14d485ce7cc41481e7b4a17ac39c5e0b794b01912f425081d4431fd`. Build platform-manifest digests are API `af33b999cd7d0080c7a041ad50f589115189658e409d38a83fc8bd80dfe4142f`, web `d2679970ac6f05f7929951d18c95b176b3af8d9980e0d7e4c9011647d20b5b9b`; record containerd's actual index/platform identity rather than silently substituting a mutable tag.

## Isolated compatibility proof

`verify-release-roundtrip.sh` creates a fresh Docker project/DB/mock provider/private evidence volume, no host ports/live credentials. Actual old/candidate immutable runtimes use API250m/256Mi and web500m/512Mi, UID10001/read-only, current exec probe commands/deadlines. A mock account starts pending/commenter, is explicitly audited/approved admin by the test operator, and reauthenticates after session revocation. The private disposable cookie is never printed.

Checks cover original pair → candidate API/original web → candidate pair → candidate API/original web (web rollback) → original pair. Exact expected HTTP SHA/time/no-store, 13 immutable assets, API/web404, admin UUID/role/approval/CSRF/session, every auth row and Alembic revision are compared in each phase. Candidate Chromium only reads root/login/errors/versions, so it does not alter the seeded authentication rows. After final image rollback the retained fixture session logs out successfully. This demonstrates compatible code/state, not real GitHub user-cookie continuity or Kubernetes rolling-surge behavior.

```sh
sudo env \
 CANDIDATE_API_IMAGE=anxious-hosting-api@sha256:cedb57e5e371f0433df8bea1070320a751bf8caf2fc13bfb8183b969aece2157 \
 CANDIDATE_WEB_IMAGE=anxious-hosting-web@sha256:f63661d7d177c46079006978404f23dbd8b3bf453caab2b0368d81f0399191d7 \
 CANDIDATE_SHA=1617f7ac996a78cb0662eb7968148eb504d7ce76 \
 CANDIDATE_BUILT_AT=2026-10-07T08:43:09Z \
 bash infra/hosting/verify-release-roundtrip.sh
```

Only test containers/network are removed; fresh disposable volumes are retained. No unchanged app/Pages/migration adversarial suite is relabeled as repeated. All language execution stays inside Docker.

## PM baseline and image-only update

Before operation, capture actual current API/web Deployment refs and pod imageIDs plus HTTP version payloads, current rollout strategy/probes/resources/env and secret references. Save Deployment/ConfigMap baseline and Secret UID/resourceVersion metadata privately; never output Secret values, raw session/state/cookie/CSRF or account identifiers to issues/messages. Record DB revision, account/admin/audit counts and a private fingerprint of UUID/role/approval/audits (exclude mutable last-login timestamps); current backup/isolated restore remains separate PM evidence. Legitimate user login/logout can change session counts; distinguish that activity from reset or revocation.

Require baseline refs to resolve to the original verified 684d627 images; stop on unexpected drift. Refresh host capacity and existing-service/M1 positive controls; ensure no other rollout is active. Import verified candidate bundle and compare names/digests/platform/source/time. Reviewer approves this exact candidate/plan before PM mutation.

Generate four **image-only strategic-merge patches** using exact imported candidate refs and exact baseline original refs (each digest qualified):

```sh
IMAGE="$CANDIDATE_API_REF" bash infra/hosting/render-image-patch.sh api > /PRIVATE/RELEASE/api-candidate.json
IMAGE="$CANDIDATE_WEB_REF" bash infra/hosting/render-image-patch.sh web > /PRIVATE/RELEASE/web-candidate.json
IMAGE="$ORIGINAL_API_REF" bash infra/hosting/render-image-patch.sh api > /PRIVATE/RELEASE/api-original.json
IMAGE="$ORIGINAL_WEB_REF" bash infra/hosting/render-image-patch.sh web > /PRIVATE/RELEASE/web-original.json
sudo /usr/local/bin/k3s kubectl -n m2-hosting patch deployment api --type=strategic \
 --patch-file=/PRIVATE/RELEASE/api-candidate.json --dry-run=server -o json > /PRIVATE/RELEASE/api-candidate-dryrun.json
```

Inspect the server result against the live baseline: only the named container image differs. In particular replicas/strategy/resources/volumes/env/secret refs/10s API probes must match. Compare ConfigMap and Secret metadata before/after throughout. Do not apply full historical YAML or use rollout undo to restore stale template fields. A patch does not update the previous full-apply image annotation; preserve this image-only release plan as the current operating record and review any future full apply for drift.

Then apply API candidate patch, wait for rollout, old Pods gone and at least60s Ready/restart stability before touching web:

```sh
sudo /usr/local/bin/k3s kubectl -n m2-hosting patch deployment api --type=strategic --patch-file=/PRIVATE/RELEASE/api-candidate.json
sudo /usr/local/bin/k3s kubectl -n m2-hosting rollout status deployment/api --timeout=300s
```

Verify API candidate version/time, health/probes/resources and existing web original version. Record actual surge Pod counts/requests/CPU/memory/throttling/restarts/events and existing service responses. Once stable, server-dry-run/inspect/apply the web candidate patch, rollout/old-Pod removal/60s stability, and verify candidate web/API metadata, browser root/login/status/assets/404. OAuth mode/start callback/state/PKCE and approved account/audit/revision remain current; do not log sensitive redirect parameters. Real authenticated logout/relogin is user-browser evidence when available, distinct from anonymous automation and mock preservation proof.

## Explicit original-image rollback

Keep the same origin/OAuth client/private key/DB/probe configuration. Server-dry-run/inspect **web-original** image patch, apply/wait/old-Pod removal/60s stability; API remains candidate during this compatible mixed phase. Then server-dry-run/inspect/apply **api-original**, wait/observe likewise. Verify actual old HTTP SHA/time/assets/readiness and original resolved imageIDs; compare ConfigMap/Secret metadata and DB account UUID/roles/approval/audits/revision with baseline, retaining active sessions unless independent user action/expiry explains changes.

If the API candidate fails before web update, restore only API original; if web candidate fails, restore web original then API original. Never overlap controllers or continue acceptance while an earlier step is unstable. No database restore/reset/down migration, role/key/password rotation, ConfigMap OAuth disable, probe rollback, PVC/PG/route/policy reapply or namespace deletion. Both original and candidate use reviewed compatible 0001_auth; a rollback to known defect images is prohibited.

Initial steady requests remain250m/480Mi. Sequential API surge nominal300m/576Mi, web surge350m/608Mi; terminating Pods can exceed nominal counts. PM measures real node/surge/service behavior rather than treating arithmetic or Docker tests as a live pass. Submit evidence of distinct candidate digest/version and exact original recovery, preserved operational state and existing-service/M1 results for independent review. This packet does not approve final PR/M2 completion or human merge.
