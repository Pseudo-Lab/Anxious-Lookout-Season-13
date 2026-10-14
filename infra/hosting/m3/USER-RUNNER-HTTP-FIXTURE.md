# Disposable actual HTTP fixture for runner UI integration

Use only a dedicated test Compose project with synthetic OAuth accounts and
throwaway PostgreSQL. This is FastAPI/PostgreSQL HTTP, not actual Pod creation,
provider authentication or a model-ready runner. Do not apply this overlay to an
operating/deployed project. The production image does not include `tests/` or the
phase setter; the fixture image must be built from the reviewed source.

Use the established `backend/compose.m3-integration.yml` workflow (runtime API,
fixture/mock GitHub, web runtime, gateway and explicit account administration),
adding `-f backend/compose.user-runner-http-test.yml`. Keep a role-specific project
name and gateway port; front uses its 1xxxx band. No backend worker or Kubernetes
client runs. This overlay does not require the legacy offline runner overlay.

Example commands from repository root; environment values refer to locally built,
reviewed images, not operating images:

```sh
# Existing image-build workflow: backend Dockerfile runtime + test targets, web
# runtime built by front. Set API_RUNTIME_IMAGE, M3_FIXTURE_IMAGE, WEB_RUNTIME_IMAGE.
export M3_GATEWAY_PORT=18100
export RUNNER_HTTP_ENABLE=true
# Use a fresh unique project name for each independent fixture run.
docker compose -p anxious-s13-front-runner-http -f backend/compose.m3-integration.yml -f backend/compose.user-runner-http-test.yml up -d db mock-github
docker compose -p anxious-s13-front-runner-http -f backend/compose.m3-integration.yml -f backend/compose.user-runner-http-test.yml run --rm migrate
docker compose -p anxious-s13-front-runner-http -f backend/compose.m3-integration.yml -f backend/compose.user-runner-http-test.yml run --rm runner-migrate
docker compose -p anxious-s13-front-runner-http -f backend/compose.m3-integration.yml -f backend/compose.user-runner-http-test.yml up -d api web gateway
```

`RUNNER_HTTP_ENABLE` is an overlay-only host interpolation variable that sets the
API's `CODEX_USER_RUNNERS_ENABLE`; it defaults false if unset. Changing it requires
recreating the fixture API container. In disabled mode `/api/auth/me` reports false
and the established legacy API behavior remains. The API has no admin DB URL;
only explicit ops-profile migration/fixture services have disposable credentials.

Use the existing mock OAuth and `admin` service to create/approve two separate
synthetic editor/admin accounts. Obtain each account UUID through that account's
normal `/api/auth/me`, not a real user's identity. Unapproved/commenter accounts
are useful for rejection checks and cannot be phase-controlled. The preparation
POST itself requires the actual session, Origin, CSRF and UUID Idempotency-Key.

Then set a deterministic **synthetic** registry phase for one approved account:

```sh
docker compose -p anxious-s13-front-runner-http -f backend/compose.m3-integration.yml -f backend/compose.user-runner-http-test.yml run --rm runner-phase --owner FIXTURE_ACCOUNT_UUID --phase waiting
```

| Fixture phase | HTTP reason | Notes |
|---|---|---|
| preparing | provisioning | Pending entry POST coalesces; no real Pod |
| waiting | capacity_wait | Durable-looking fixture only, not capacity measurement |
| unreachable | runner_unreachable | Synthetic UID/ambiguity; sending locked |
| ready | runner_unreachable | DB READY never establishes real dispatch readiness |
| stopped | idle_stopped | GET only for deterministic display; fresh entry POST may prepare again |
| failed | runner_failed | GET only for display; explicit new entry may prepare again |

Run the initial browser login/entry first and then change phase before the next
GET, or use the approved account before loading its page. Preparing/waiting entry
requests coalesce; a first entry into stopped/failed deliberately starts preparing
again, so set these states after entry to test the stopped/failed banner. The
setter may switch synthetic phases while preserving owner/state/activity/history,
but refuses uncompleted admissions, active requests or any settled create permit.
It never resets a real/uncertain create reservation. For those cases use a fresh
fixture account/project; never clear receipts. No fixture endpoint is added to API.

The phase script guards APP_ENV=test, OAUTH_MODE=mock, RUNNER_HTTP_FIXTURE=true,
the db hostname, disposable database names and exact synthetic admin credentials.
The helper lives in the test-only `tests` package. Test-origin flags and synthetic
phase transitions are not operations evidence. Front should verify GET read-only,
account isolation/late responses, drafts/history preservation, no automatic POST
or send, feature-off compatibility and direct503 preflight boundaries. Provider
success and actual controller readiness remain outside this fixture.

Clean up **only the uniquely named disposable project** after saving browser and
API test evidence. `down --volumes` deletes its synthetic DB; never use an operating
project name or the same command against a shared persistent environment.

```sh
docker compose -p anxious-s13-front-runner-http -f backend/compose.m3-integration.yml -f backend/compose.user-runner-http-test.yml down --volumes
```

## Additional frontend controls

`--account` aliases `--owner`; canonical DB phase names also alias the short
names above. `stopping` binds a synthetic UID and projects runner_unreachable.
`not_prepared` only verifies that a fresh account has no registry; it refuses to
reset an existing row. Use a separate fresh account before its first entry POST
for this case. UID selection is automatic: ready/unreachable/stopping have a
synthetic UID, other phases do not. Arbitrary incompatible phase/UID pairs are
not supported.

Use the same runner-phase service with `--account UUID --inspect` for a read-only
snapshot: phase, generation, podBound, creationReserved, entryReceipts (all owner
entry records), pendingEnsure (all unsettled ensure rows), lastActivity. No API
restart is needed after a setter transaction commits; preflight reads current DB
state for every request. Inspect uses a repeatable-read/read-only transaction.

To test the unbound reserved projection, use `--account UUID --phase provisioning
--creation-reserved` on a separate disposable account. This settles the synthetic
ensure receipt without external creation and projects runner_unreachable. It is
irreversible through this fixture: subsequent phase setters refuse that account.
Do not force reserved → failed/stopped in one test sequence; use independent
accounts/projects for those cases. R1 entryReceipts can increase for distinct
entry keys while one pendingEnsure/state is coalesced; receipts are not Pod counts.

The existing admin permission CLI deletes every session for an account whenever
approval/role is changed. Test eligibility transitions through re-login; an old
session is not retained across this change.
