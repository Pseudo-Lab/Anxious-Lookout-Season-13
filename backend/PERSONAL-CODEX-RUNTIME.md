# Personal runtime configuration — review required before actual operation

This document covers the loopback Compose candidate. The later
[existing K3s HTTPS `/codex-trial` candidate](../infra/codex-trial/README.md)
provides new trial DB/state, route-local access/encoding, explicit remote opt-in,
staged review and policy-preserving rollback. Use that target's separate artifacts;
neither Compose proof nor its old images authorize the K3s deployment.

Issue7 uses the existing server ChatGPT account, native0.160.1 managed file cache and exactly `gpt-6.1-sol`. This package supplies configuration artifacts; it does not establish personal-use support, actual source authority, external refresh ownership, network policy or model entitlement. PM operates reviewed artifacts with private inputs. No actual source or service/provider start was performed in author validation.

## Two file sets; one private project/database/origin

Bootstrap is self-contained `backend/compose.codex-bootstrap.yml`. It does not extend a test/integration compose and contains no native runner, platform UUID, control directory or provider-network interpolation. It defines GitHub mode and private FILE inputs itself, with runner enable=false and mapping empty. GitHub identification/approval therefore can precede platform UUID assignment (R7-P1).

After real binding and full source/lifetime review, add `backend/compose.codex-personal.yml`. The overlay enables only the designated UUID and adds one runner with pinned model/auth mode. Keep all bootstrap project/database/origin/image/capacity inputs identical. Never include this overlay before UUID verification or invent an ID to satisfy interpolation.

`backend/codex-personal.env.example` is an intentionally empty input interface. Store actual env/input files outside the repository/build context/shared artifacts with private permissions. Bootstrap needs project/API+frontend image refs, database name/password file, gateway port, GitHub client/input directory, API input directory, ops input directory, approved GitHub-egress network and CPU/memory limits. The enable phase additionally needs verified platform UUID, runner image, control directory, approved provider-egress network and runner limits. No unmeasured resource defaults or fixture password/image is supplied.

The API, migration and admin containers use the same `PERSONAL_API_IMAGE` runtime input. No test/dummy executable or test image is needed. Build/stamp/verify exact reviewer-approved source SHA and immutable image IDs as described in PERSONAL-CODEX-TEST.md. Ops stays explicitly profiled and never runs from API startup.

## Private file interfaces

| Consumer | Input | In-container reference |
|---|---|---|
| DB only | PERSONAL_DB_PASSWORD_FILE | /run/secrets/postgres-password; POSTGRES_PASSWORD_FILE |
| API only | PERSONAL_API_INPUT_DIR/database-url | DATABASE_URL_FILE=/run/personal-api/database-url |
| API only | PERSONAL_GITHUB_INPUT_DIR/client-secret, transaction-key | GITHUB_CLIENT_SECRET_FILE, AUTH_TRANSACTION_KEY_FILE under /run/personal-github |
| ops only | PERSONAL_OPS_INPUT_DIR/admin-url, api-password | ADMIN_DATABASE_URL_FILE, API_DATABASE_PASSWORD_FILE under /run/personal-ops |
| API after enable | PERSONAL_API_INPUT_DIR/runners.json and transport-token | Single verified UUID mapping to http://personal-runner:8080 and /run/personal-api/transport-token |
| runner only | The same transport-token file | /run/secrets/runner-token (read-only) |
| runner only | PERSONAL_CONTROL_DIR | /run/personal-control (writable for private atomic refresh/ledger) |

API/ops runtime UID is10001. Their directories/files must be accessible only to their reviewed operator/runtime identity; mode0700/0600. Runner control must satisfy the canonical/non-symlink/single-link/UID10001 checks in runner/auth.py, be separate from API/GitHub/ops input roots and native state, and contain only the approved grant/cache/ledger interfaces. API never mounts provider control or state. Private mount sources use create_host_path=false to avoid silently creating an empty directory for a missing file.

The pinned PostgreSQL image initially reads its password-file under its standard entrypoint, including the root→postgres handoff. PM must verify that the selected secret file is readable at that stage without weakening input privacy. Its data volume is project-scoped `personal-db-data`, not a fixture or existing-production volume. PostgreSQL18 uses the /var/lib/postgresql mount. Matching admin/app URLs and API password are private operator responsibilities; never rotate an existing role password merely to make migration pass. Fresh-project/source invariants must be checked before migrations.

## Network contract and target-dependent conditions

Only the gateway publishes a host port, fixed to127.0.0.1 at the chosen private port. Auth origin derives from that same loopback port. No API/DB/runner host ports, container_name, privileged mode, Docker socket or host HOME are declared. Gateway routes only web/API and excludes `/_fixture/` and `/api/internal/` paths. Native tool callbacks use direct internal API DNS, not the gateway.

R7-CFG1: router PathPrefix exclusions alone do not cover proxy/ASGI decode differences. The pinned gateway entrypoint explicitly refuses the seven ambiguous encoded path characters (slash/backslash/null/semicolon/percent/question mark/hash). This includes `%2F/%2f` and outer `%25` double encoding before any upstream route. The structure checker rejects missing/relaxed/duplicate encoding flags. [Traefik3.7's entrypoint guidance](https://doc.traefik.io/traefik/v3.7/reference/install-configuration/entrypoints/#encoded-characters) distinguishes path checks from query parameters; normal GitHub callback/search query encodings remain allowed. Private internal callbacks reach the API directly and do not use the browser entrypoint.

The data and web-api networks are internal; ops/DB join only data. In enable mode the API and runner share internal api-runner; the runner never joins data/web-api/ingress/GitHub-egress. The gateway alone joins the uncredentialed ingress bridge to serve its loopback listener.

GitHub-egress and provider-egress are **external** network references: Compose will not create them and missing resources fail startup. They are distinct required private input names. [Docker's network contract](https://docs.docker.com/reference/compose-file/networks/) describes external lifecycle and internal network isolation. External names are attachment interfaces, not destination-policy evidence or approval flags. The structure checker rejects declared network-name aliases and obvious control/API mount overlap; actual network IDs, filesystem aliases and policies require private target verification.

Before actual startup PM/review must verify or request back-owned target-specific artifacts for:

- The selected Linux ARM64 host/engine version, available capacity and per-service limits; no impact on existing services/agents.
- Reviewed external networks are project-private and have separate actual IDs, with no alias to data/ingress/default or another trial. Enforce least-required outbound destinations and default deny, including denied metadata/private/nonrequired destinations and DNS/IPv6 behavior.
- API egress matches code-fixed GitHub token/user HTTPS endpoints. Browser authorization remains the operator's private browser flow.
- Provider/auth/refresh destinations match the pinned native managed-auth behavior and selected source. This package does not invent a universal provider domain/IP list. Target policy/DNS/TLS evidence is required; rejected destinations consume no permission to widen rules without review.
- Policy applies to the native child, which uses an environment whitelist; container proxy variables alone are not sufficient. Docker bridge creation or a readiness label alone cannot prove enforcement.

No universal host iptables/CNI/firewall rules are applied by these files. Without concrete host/controller/network/endpoint evidence, those enforcement artifacts cannot be authored or verified safely, and live execution remains held. If the target requires Kubernetes/CNI rather than these Docker interfaces, back must provide/review matching target configuration; PM must not improvise a conversion. This is separate from credential/source/full-lifetime refresh proof under the existing server account.

## Operator render and check — no services start

The real env/JSON is private and can contain identifiers/paths even though secret contents are FILE inputs. Never paste complete config/inspect output into messages or issues. The following variables denote private input/resource interfaces, not supplied values. Use environment inputs that match the reviewed target; guard them before commands.

```sh
: "${TRIAL_BOOTSTRAP_ENV_FILE:?Private reviewed bootstrap env required}"
: "${TRIAL_ENABLED_ENV_FILE:?Private reviewed enable env required}"
: "${TRIAL_PRIVATE_RENDER:?Private JSON path required}"
: "${TRIAL_API_IMAGE:?Reviewed runtime image required}"
sudo docker compose --env-file "$TRIAL_BOOTSTRAP_ENV_FILE" --profile ops \
  -f backend/compose.codex-bootstrap.yml config --format json > "$TRIAL_PRIVATE_RENDER"
sudo docker run --rm --network none --read-only --user 10001:10001 \
  --mount "type=bind,src=$TRIAL_PRIVATE_RENDER,dst=/run/private-render.json,readonly" \
  --entrypoint python "$TRIAL_API_IMAGE" -m ops.check_codex_config \
  --mode bootstrap --config /run/private-render.json
# After verified UUID/binding and source/target review:
sudo docker compose --env-file "$TRIAL_ENABLED_ENV_FILE" --profile ops \
  -f backend/compose.codex-bootstrap.yml -f backend/compose.codex-personal.yml \
  config --format json > "$TRIAL_PRIVATE_RENDER"
sudo docker run --rm --network none --read-only --user 10001:10001 \
  --mount "type=bind,src=$TRIAL_PRIVATE_RENDER,dst=/run/private-render.json,readonly" \
  --entrypoint python "$TRIAL_API_IMAGE" -m ops.check_codex_config \
  --mode enabled --config /run/private-render.json
```

Prepare render output with private permissions accessible to UID10001; use a private parent directory and umask077, not a shared /tmp/world-readable file. These commands validate declared structure only. They read no secret contents, perform no network request and never start API/native/provider. They cannot verify actual source binding, map contents, target permissions, immutable images, model access, actual egress enforcement or external refresh ownership.

PM applies migrations/admin/API bootstrap with only the bootstrap set after its conditions pass; actual provider startup only uses the enabled set after the separate gate. Follow PM-RUNBOOK's max3/stop/restart/cleanup/restore ordering. Control/native/request ledgers remain authoritative; never reset or duplicate trial data to regain slots.

## Author synthetic verification

Docker Compose renders are produced by `sudo bash backend/config_tests/render-personal.sh <DISPOSABLE_SYNTHETIC_DIR>` under a cleared environment. The script starts no services and rejects missing/blank inputs. Assertions run inside Docker with no network, mounted synthetic renders/tests/checker, using backend/config_tests/test_personal_compose.py. Checks cover UUID-free bootstrap, stage consistency, private FILE loading, routing paths, runtime/ops identity, isolated networks, and unsafe-render refusal. Application code/model/refresh behavior remains covered by the separate da22ae1 checkpoint, not inferred from this configuration test.

Author verification: **31 configuration cases passed** under Docker network-none, plus18 missing/blank render rejections. The final credential-free `runtime` build packages the checker; both installed `python -m ops.check_codex_config` modes passed, and runtime release/absence-of-test/dummy checks passed. The pinned PostgreSQL image's sourced FILE helper passed with synthetic data without initialization/server start. Initial standalone FILE-loader cases failed only because the inspection container lacked PYTHONPATH=/app; that test environment was corrected and final31 passed. No actual target/network policy/DB/provider integration is inferred; no runner-stage build or service startup was performed. The runtime check image's zero release SHA is explicitly synthetic, not a deployment artifact. Evidence: `agent-worktree-comm/artifacts/back-issue7-runtime-config/`.

After independent R7-CFG1, the updated configuration suite passes34, including rejection of missing/relaxed/duplicate encoding flags. `probe-personal-proxy.sh` exercises the exact pinned Traefik image with Uvicorn mock upstreams in one network-none namespace, extracting the actual rendered production command. Only listen/upstream ports change for the fixture; no host ports, real services/DB/GitHub/provider/auth data are used. Its28 HTTP cases include positive web/API/callback and encoded query controls; plain/encoded-letter exclusions; upper/lower/multiple encoded slash, double encoding, other reserved encodings; and POST callback rejection. Blocked requests assert no upstream log increment. Both ephemeral containers are removed by EXIT trap. This closes the prior gap between Boolean route evaluation and actual proxy/ASGI decoding, without claiming production authentication bypass or live execution approval.
