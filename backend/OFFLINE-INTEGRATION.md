# Offline runner UI integration

Optional test-only extension of [INTEGRATION.md](INTEGRATION.md), issue #6. It connects the actual API/router, actual adapter and storage callback to the offline protocol executable. No model credential, provider/inference, operating resources or real Codex resume is used. API reports verification=fixture; the assistant reply explicitly says Offline fixture answer.

Each role uses its own project, images, port, temporary directory and volumes. Front example: anxious-s13-front-it/18100; reviewer selects its own project/3xxxx port; backend evidence used anxious-s13-back-m3-offline/28120. Never attach another role's DB/token/home/project. First complete the base recipe with0003 and two explicitly approved disposable browser accounts; from each authenticated auth/me obtain accountId UUID (not githubId). Build the new backend/Dockerfile test stage under your own M3_FIXTURE_IMAGE tag. Retain base API_RUNTIME_IMAGE/WEB_RUNTIME_IMAGE/M3_FIXTURE_IMAGE/M3_GATEWAY_PORT variables.

```sh
export OFFLINE_ACCOUNT_A=VERIFIED_APPROVED_ACCOUNT_UUID_A
export OFFLINE_ACCOUNT_B=VERIFIED_APPROVED_ACCOUNT_UUID_B
export OFFLINE_FIXTURE_DIR=$(mktemp -d /tmp/m3-offline-front-XXXXXX)
chmod 0777 "$OFFLINE_FIXTURE_DIR"
sudo -E docker compose -p anxious-s13-front-it \
  -f backend/compose.m3-integration.yml -f backend/compose.m3-offline.yml \
  run --rm fixture-setup
sudo -E docker compose -p anxious-s13-front-it \
  -f backend/compose.m3-integration.yml -f backend/compose.m3-offline.yml \
  up -d api offline-a offline-b web gateway
```

Only this new empty staging directory starts writable so UID10001 can create private/ (mode0700), mapping/token files (mode0600). Setup rejects unknown/unapproved/same account IDs, a non-fixture DB name, non-test environments, model credential inputs, populated output or symlinks. Do not rerun setup or rotate tokens under running adapters. API reads the private map; each runner mounts only its own token and named state volume, on a different internal network connected only to API. No runner ports, DB/other-runner network, Docker socket, external network or model credentials are supplied. Owner markers and exclusive volume leases remain enforced.

Overlay API uses the guarded test-image factory calling the unchanged production app/router and setting fixture verification. The guarded runner factory calls the actual adapter with CODEX_BIN=/usr/local/bin/codex-fixture and CODEX_MODEL=fixture-no-provider. No __mock UI route or production API fault switch is added; runtime stage excludes these factories/controls/executable.

| Message text | Expected observable behavior |
| --- | --- |
| Any ordinary text | Owned research_document_save via actual tool/callback path; full tool input/output and raw item GET; offline assistant reply. |
| /fixture/unrecorded | Protocol fails before native input recording; poll failed, source=platform/status=not_recorded. A new explicit ordinary message succeeds and retains the earlier input/raw platformInput. |
| /fixture/reject | Test-only intake409 before native work; platform reserves202, poll failed/codex_rejected with token revoked. New explicit input uses current version; original key/body still returns original202. |
| /fixture/hold | Protocol waits5 seconds; submit to another session of that same account during this interval for busy/codex_rejected. Account B's separate runner remains independent. |

Use actual cookie/Origin/CSRF/UUID Idempotency-Key. Discard one202 response, poll until settled, retry the same original key/text/expectedVersion; exactly one tool document is created. Repeat after browser refresh, retaining that tuple. Verify full tool JSON, platform labels, B's404 for A's session/document and B's own independent conversation. Do not automatically replay ambiguous requests.

Quiesce a hold before restarting actual API/adapter processes:

```sh
sudo -E docker compose -p anxious-s13-front-it \
  -f backend/compose.m3-integration.yml -f backend/compose.m3-offline.yml \
  restart api offline-a offline-b
```

Reconnect the same authenticated browser, read original history, replay the old tuple without another document, then send a new explicit message using current version. This resumes the original offline fixture thread. Avoid restarting mock-github; its browser identity map is in-memory.

Backend validation: Docker targeted9 (new guard/setup plus affected protocol) passed. Separate real HTTP stack passed full IO, failed-input retention, rejection/busy, reconnect/replay and two-account isolation. API+both adapters were restarted; original history, old-key no-dispatch and explicit followup passed. Runtime controls exclusion and runner network/mount/credential exclusion passed. New UI scenarios remain front/review validation; real provider is unverified.

Cleanup only your explicitly owned disposable project after preserving evidence:

```sh
sudo -E docker compose -p anxious-s13-front-it \
  -f backend/compose.m3-integration.yml -f backend/compose.m3-offline.yml \
  down --volumes
```

This deletes that project's fixture DB/runner volumes. Preserve the environment variables until cleanup. Remove only its generated temporary staging directory separately via a Docker/operator procedure owning UID10001 files; never substitute an operating/native home.
