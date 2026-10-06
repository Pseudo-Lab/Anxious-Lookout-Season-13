# Hosting API

Issue [#4](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/4) owns scope and acceptance. The API uses FastAPI, PostgreSQL and an opaque server session. Next.js remains a separate Node service. No Supabase connection or bulletin-board/M3 API is included.

## Docker validation

From the repository root:

```sh
sudo bash infra/hosting/validate.sh
```

This builds pinned ARM64 dependencies and runs the `anxious-s13-back-test` project on an internal network. Credentials in `compose.test.yml` are disposable fixtures, never deployment credentials. The test project publishes no ports; its named PostgreSQL volume survives the deliberate stop/start probe. It does not use host/k3s DB data. Cleanup is explicit:

```sh
sudo docker compose -f infra/hosting/compose.test.yml down --volumes
```

Do not use that command on deployment volumes. The test fixture's mock provider is packaged only in the Docker `test` stage. Real OAuth is not validated by these tests.

## API and settings

- `/healthz` tests process liveness; `/readyz` and `/api/health` test database connectivity/schema version. DB lookup failures yield 503, never an inferred logout. A request with no session can return 401 without a DB lookup.
- `/api/version` contains immutable validated image metadata.
- `/api/auth/github/start` creates state + PKCE; callback consumes a browser-bound transaction once, verifies GitHub `/user` identity, creates a commenter/unapproved account if new, and issues a fresh opaque session.
- `/api/auth/me` returns service `accountId` UUID separately from `githubId`, display login, server role/approval and a session CSRF nonce. Provider token/email/site_admin never becomes service authority.
- Logout requires exact configured Origin and X-CSRF-Token on live sessions; clears server session and cookie. Session TTL 8h, OAuth transaction TTL 5min. Approval/role operations revoke all account sessions and record audit.
- No API handles role assignment, ownership via client IDs, first-user admin, inferred Supabase linking, provider token persistence or schema initialization in startup.

Settings read `<NAME>_FILE` when present, otherwise `<NAME>`. Deployment uses private files/Secrets, not image layers. Required: DATABASE_URL. Enabled OAuth also requires GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET and a Fernet AUTH_TRANSACTION_KEY. `OAUTH_MODE=disabled` permits initial DB/status checks without an external app. Disabled login redirects to the web's error page. `mock` is accepted only with `APP_ENV=test`; alternate provider URLs are forbidden in real GitHub mode. AUTH_ORIGIN is fixed configuration, never inferred from Host/Forwarded headers. Public HTTP origin is rejected. `ALLOW_INSECURE_LOOPBACK=true` is limited to explicit localhost/127.0.0.1/::1 origins for protected verification.

Public browser API and cookie paths include APP_BASE_PATH when configured. Default is empty for the dedicated Host. The committed Compose gateway/k3s renderer use root web/API; changing base also requires corresponding routing and a new web build. Uvicorn disables access logging to keep callback code/state out of logs and does not trust proxy headers. Responses use no-store; provider exceptions/credentials/SQL parameters are not printed.

Real auth additionally checks request Host/protocol against AUTH_ORIGIN. X-Forwarded-Proto is used only from configured AUTH_TRUSTED_PROXY_CIDRS; k3s restricts API ingress to Traefik endpoints before trusting the Pod source CIDR. Public HTTP and spoofed headers from other sources cannot issue real sessions. Protected loopback verification preserves the exact configured loopback Host. Mock transport remains confined to test mode.

## Migration and operator authority

`python -m app.migrate` runs **inside an explicit operator container**, with ADMIN_DATABASE_URL and API_DATABASE_PASSWORD private inputs. It serializes migration v1, creates the API role only if absent, grants auth DML, and does not rotate an existing password. The app role can create identities/update display login, but cannot set role/approval or read/write permission audit. A migration/schema mismatch is an error, not an automatic downgrade.

After a verified GitHub account has identified itself, an operator may run:

```sh
sudo docker compose --profile ops run --rm admin \
  --github-id VERIFIED_NUMERIC_ID --approved true --role admin \
  --actor VERIFIED_OPERATOR_REFERENCE --reason 'Approved bootstrap decision'
```

No actual administrator ID is supplied by this repository. Obtain verified identity and authorization before choosing one. The operator credential is separate from API credentials and absent from the running API. The command atomically changes permissions, writes before/after audit and revokes prior sessions. Re-login is required. It does not create an account from an assumed ID or copy existing Supabase profiles. Backup/restore and deployment instructions are in [infra/hosting](../infra/hosting/README.md).
