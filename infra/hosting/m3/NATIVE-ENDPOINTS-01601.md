# Pinned native0.160.1 static endpoint inventory / reuse evidence

Issue7 investigator: backend, 2026-10-10. Source inspection and file hashes only;
no auth cache/source supplied, native executable started or provider request made.
P1 HTTPS preparation is independent of actual native credential readiness.

Official source tag `rust-v0.160.1` annotated object
`c3e23d4c4385619ecec78408766e46b7fa7dd9ad` resolves to commit
`d27764b82f7118f674371e6d6e76271d9d606edb`. Downloaded official commit archive
SHA256 `a5b35cb05cbeedda98217b089c684eb2c56177e717b3ef9ec01163e9b75cd61e`.
Version installer selects the official ARM64 release URL and verifies executable
SHA256 `fbbaec80443919f86dd63648a0b62759cf6f1d0e09310602fde96885e0bceb3e`.
The actual existing b19 runner image's installed file hash matches. This is
release/tag/installer association plus executable pin, not a source rebuild or
proof of every provider-side redirect.

## Defaults and conditional paths in the selected adapter

Adapter sends model_provider=openai, forced_login_method=chatgpt and file store,
fresh CODEX_HOME, no imported config. Child environment is PATH/LANG/HOME/
CODEX_HOME only, so host endpoint/API-key/proxy/client-id overrides are not passed.
Apps/plugins/browser/shell/image/multi-agent/unbounded retries are off. The model
remains exactly gpt-6.1-sol; no alternative native auth mode is selected.

| Purpose / destination | Pinned source evidence and limits |
|---|---|
| Managed cache refresh: auth.openai.com TCP443, POST /oauth/token | [login auth manager](https://github.com/openai/codex/blob/d27764b82f7118f674371e6d6e76271d9d606edb/codex-rs/login/src/auth/manager.rs), REFRESH_TOKEN_URL and request_chatgpt_token_refresh. Uses pinned native client ID/JSON encoding/resourceNone. Adapter never calls this endpoint directly |
| Inference and model catalog: chatgpt.com TCP443; /backend-api/codex/responses, /backend-api/codex/models | [provider defaults](https://github.com/openai/codex/blob/d27764b82f7118f674371e6d6e76271d9d606edb/codex-rs/model-provider-info/src/lib.rs), [responses](https://github.com/openai/codex/blob/d27764b82f7118f674371e6d6e76271d9d606edb/codex-rs/codex-api/src/endpoint/responses.rs), [models](https://github.com/openai/codex/blob/d27764b82f7118f674371e6d6e76271d9d606edb/codex-rs/codex-api/src/endpoint/models.rs). Responses HTTP streaming / WSS share443; configured pin is not entitlement |
| Account/workspace routing bootstrap: chatgpt.com TCP443, /backend-api/wham/accounts/check | [workspace resolver](https://github.com/openai/codex/blob/d27764b82f7118f674371e6d6e76271d9d606edb/codex-rs/app-server/src/request_processors/account_processor/workspace_routing.rs) calls BackendClient.get_accounts_check; [backend client](https://github.com/openai/codex/blob/d27764b82f7118f674371e6d6e76271d9d606edb/codex-rs/backend-client/src/client.rs). Discovery may occur before a model turn |
| Selected workspace inference backend: response-derived exact HTTPS origin | [workspace provider routing](https://github.com/openai/codex/blob/d27764b82f7118f674371e6d6e76271d9d606edb/codex-rs/model-provider/src/workspace_routing.rs) can replace scheme/host/port from discovered backend_origin, preserving endpoint path. This static inspection cannot name the selected account's actual backend. Do not substitute wildcard *.openai.com/*.chatgpt.com or API-key defaults |
| Analytics: chatgpt.com TCP443, /backend-api/codex/analytics-events/events | [analytics client](https://github.com/openai/codex/blob/d27764b82f7118f674371e6d6e76271d9d606edb/codex-rs/analytics/src/client.rs), [app-server configuration](https://github.com/openai/codex/blob/d27764b82f7118f674371e6d6e76271d9d606edb/codex-rs/app-server/src/analytics_utils.rs). Adapter does not set analytics.enabled=false; queue exists unless disabled by config. Do not silently omit this possibility from lifecycle analysis |
| Default release metrics: ab.chatgpt.com TCP443, /otlp/v1/metrics | [OTEL config](https://github.com/openai/codex/blob/d27764b82f7118f674371e6d6e76271d9d606edb/codex-rs/otel/src/config.rs), [config defaults](https://github.com/openai/codex/blob/d27764b82f7118f674371e6d6e76271d9d606edb/codex-rs/config/src/types.rs) default metrics_exporter=Statsig in release. Current adapter does not override it. Source presence/default is not an observed network call |
| Native revoke/login/browser endpoints | Manager also defines auth.openai.com/oauth/revoke and login source has auth issuer routes, but adapter makes no login/logout RPC. Local cleanup deletes only private cache and retains ledger; it does not revoke provider/original sessions. These routes are not newly granted merely because constants exist |
| api.openai.com/v1 / SIWC token endpoints / GitHub release download | API-key inference and SIWC direct refresh are not this selected managed-cache mode; JWT namespace URLs are not network destinations. Release download is build-only. Existing API's GitHub OAuth HTTPS destinations belong to API/browser, not runner egress |

The static default candidate DNS set is auth.openai.com, chatgpt.com and the
conditional default metrics host ab.chatgpt.com. It is **not an approved live
providerHosts list**: account/workspace routing may select another exact origin,
actual CNI/DNS/TLS/port/redirect/metrics policy needs review, and telemetry may
instead be disabled through a separately reviewed adapter configuration change.
No guessed extra hostname or permissive discovery runner is used. TCP443 Cilium
toFQDNs cannot express URL path restrictions or prove provider-internal behavior.
If runtime attempts an unreviewed destination, deny/stop and review the source
reason; do not widen policy automatically. Static investigation removes the need
to ask the user to research these code defaults, while actual selected routing
remains a concrete private fact to resolve before native startup.

## Existing runner/web applicability

`git diff b19..8fa -- backend/runner backend/requirements.lock backend/Dockerfile`
is empty. Existing runner image files app.py/auth.py/projection.py/install_codex.py
match current source byte-for-byte. AST comparison of codex_policy excluding
personal_origin_enabled confirms all other module bindings unchanged; the runner
imports MODEL/CodexFailure/ERRORS/error_reason, not the root-origin admission
function. Runner release remains b19; image index55bf19f9… and actual installed
executable hash above reuse prior import/CRI evidence. No runner rebuild merely
for API release-label equality and no native process was started.

`git diff edff..8fa -- frontend infra/hosting/web.Dockerfile .dockerignore` is
empty. Front edff root image02b39712… has independently verified actual import/
Pod association. HTTP→HTTPS uses the same root paths and no frontend runtime
change. Browser cookie/OAuth tests remain actual acceptance work; source equality
does not claim browser continuity.

## Refresh proof: backend-known versus owner-only

Backend code establishes that PersonalAuth lease covers only its private control;
native auth manager has in-process refresh serialization, not cooperation with
unrelated original CLI/IDE processes. Clean exit updates only private control,
then removes runtime cache; local cleanup removes control cache too. There is no
original-host copy-back. Different CODEX_HOME/files or one account ID do not prove
different renewable grants. Current shared evidence contains no selected server
cache/grant lifecycle metadata; GitHub metadata is unrelated.

Only owner/PM can establish the minimal remaining facts:

1. The already selected server source is compatible managed file auth with the
   required account binding, or name the actual store/format incompatibility;
   attest whether its renewable grant is independent of existing active clients.
   Do not send cache values/private IDs/path in messages or create a new login.
2. For that grant, identify every consumer/refresh writer during the trial and
   after clean exit, and the authoritative latest-cache owner/retention/continuity
   path. If original clients share the grant and cannot cooperate under original
   no-modification/no-stop constraints, report that exact conflict. Current source
   cannot solve it with declaration flags. Whole-lifetime coordination would need
   owning-client code/operations review before supply/startup.

Actual personal-use topology and selected workspace routing are additional P3
operating facts. None are blanket requests for user reapproval of the existing
task, and none block independent native-disabled HTTPS preparation.
