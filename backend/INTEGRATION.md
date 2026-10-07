# Isolated front/API fixture

Optional actual API/adapter tool-path and failure fixture: [OFFLINE-INTEGRATION.md](OFFLINE-INTEGRATION.md). Real provider verification remains separate.

Use the shared task SHA in your own worktree. This is a disposable test recipe, not deployment or real OAuth/Codex validation. It never reuses back's test DB/volume. It explicitly uses RESEARCH_ACCESS_POLICY=approved only as a fixture; publication writes stay policy_pending and Codex stays unavailable/unverified without an authorized account runner.

```sh
export API_RUNTIME_IMAGE=anxious-s13-front-it-api
export WEB_RUNTIME_IMAGE=anxious-s13-front-it-web
export M3_FIXTURE_IMAGE=anxious-s13-front-it-fixture
export M3_GATEWAY_PORT=18100
sudo docker build --target runtime -f backend/Dockerfile -t "$API_RUNTIME_IMAGE" \
  --build-arg APP_GIT_SHA=REVIEWED_40_CHAR_SHA --build-arg APP_BUILT_AT=UTC_TIMESTAMP .
sudo docker build --target test -f backend/Dockerfile -t "$M3_FIXTURE_IMAGE" \
  --build-arg APP_GIT_SHA=REVIEWED_40_CHAR_SHA --build-arg APP_BUILT_AT=UTC_TIMESTAMP .
sudo docker build -f infra/hosting/web.Dockerfile -t "$WEB_RUNTIME_IMAGE" \
  --build-arg WEB_GIT_SHA=REVIEWED_40_CHAR_SHA --build-arg WEB_BUILT_AT=UTC_TIMESTAMP \
  --build-arg NEXT_PUBLIC_BASE_PATH= .
sudo -E docker compose -p anxious-s13-front-it -f backend/compose.m3-integration.yml up -d db mock-github
sudo -E docker compose -p anxious-s13-front-it -f backend/compose.m3-integration.yml run --rm migrate
sudo -E docker compose -p anxious-s13-front-it -f backend/compose.m3-integration.yml up -d api web gateway
```

Use UTC_TIMESTAMP in `YYYY-MM-DDTHH:MM:SSZ` form. Images built with a fixture zero SHA are development-only and cannot be used as release provenance. All services use a project-specific network/volume. DB/API/provider are on an internal bridge; gateway additionally has a browser bridge so its explicit `127.0.0.1:18100` publish works. API runtime has no operator fixture credentials. Ops migrate/admin services hold only disposable fixture inputs. Fixture endpoints exist only in this test compose, not the production route/config/image.

Visit `http://127.0.0.1:18100`. The mock GitHub authorization endpoint is reachable through `/_fixture/github/authorize` on the same gateway; token/user requests remain private. A fresh browser gets a distinct provider identity; subsequent sign-ins in that browser reuse the same identity so approval and mandatory re-login work. Provider restart resets its in-memory fixture identities; restart it only together with a fresh fixture DB/browser setup. This is not real GitHub authentication.

After the browser's first successful login, read its fixture githubId from `/api/auth/me`, then:

```sh
sudo -E docker compose -p anxious-s13-front-it -f backend/compose.m3-integration.yml run --rm admin \
  --github-id VERIFIED_FIXTURE_ID --approved true --role editor \
  --actor fixture:front-it --reason 'Verified disposable test identity'
```

The operator command invalidates existing app sessions; sign in again in the same browser. A different browser context remains a distinct unapproved account until explicitly approved. No first-user admin or automatic approval exists.

Browser tests run inside Docker. For a host loopback ORIGIN use the browser test container's `--network host`; set `ORIGIN=http://127.0.0.1:18100` and empty BASE_PATH. API mutations must retain this exact Origin and the auth/me CSRF nonce. Never use a front UI mock (`__mock`) to claim an actual API response. Session endpoints require migration 0003_sessions; storage-only 0002 leaves them not_ready. Same-key/payload retries retain original expectedVersion and return the original 202 without dispatching twice.

Backend smoke recipe uses the same compose with project `anxious-s13-back-m3-integration-smoke` and port 28110, no web image, and Docker-only `tests.integration_probe` with private fixture cookie files. It checks real runtime storage/version/relation/retry/conflict, pending publication, unavailable sessions, separate browser ownership and logout. It does not prove browser Markdown rendering or front UI behavior; front validates those separately.

Cleanup only your disposable project after preserving evidence:

```sh
sudo -E docker compose -p anxious-s13-front-it -f backend/compose.m3-integration.yml down --volumes
```

That destroys only this explicitly named test project's disposable volume. Never substitute an operating/deployed project.
