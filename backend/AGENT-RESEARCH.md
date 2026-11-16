# Agent research API (#97, r2)

Branch: `task/agent-research-api-97`. The approved direction is owner native shell
calling the public research HTTPS API using the `anxious-research` skill/CLI.
MCP, per-turn process restarts, persistent peers, mutation fences, settlement/ACK
and benchmark images are removed. The API/CLI implementation is a review
candidate, with credential issuance disabled by default, not a certified release.

Document/material/post direct saves, immutable versions, CAS, idempotency, server
Codex conversation authors, retained proposals and human drafts are preserved.
Publication/withdrawal remain human-only. The ordinary internal callback now
accepts only shell authorization and the narrow public `research_page_context`;
owner research operations cannot use it. Public modes have no shell or research
API grant and can read only their admitted snapshot/direct references.

The shared response validator `app/research_results.py` is preserved for API
success/receipt validation. It validates actual serializer shapes and resource,
version, saved destination, CAS and provenance relationships; arbitrary 2xx JSON
is not evidence of a save. It does not prove a database commit by itself.

## Schema and recovery

`0008_version_authors` is additive and remains required. Existing versions default
to human; direct agent saves use the original authenticated conversation. Reserved
credential IDs remain null until a later credential feature. No PAT endpoint is
implemented. Existing proposals/drafts/publications/content remain unchanged.
Startup never migrates. Migrate only through the reviewed operator job after
backup/writer drain. Never downgrade the schema or delete provenance for rollback.

An old main image rejects008. A deployment requires an exact reviewed
**008-compatible recovery API+runner image**, immutable manifest digests, and a
real development recovery read/human-save result. Historical MCP candidates and
old benchmark artifacts are superseded and cannot establish r2 recovery safety.
If a private control volume contains `research-fence.json`, startup refuses and
reports for operator review; the replacement does not delete, settle or replay
that old state. The superseded persistent candidate was not deployed according
to review's VM record; verify actual state rather than assuming it is absent.

## Activation and validation

`POST <basePath>/api/research/agent/v1/{operation}` accepts the existing owner
operation JSON with a purpose-separated turn Bearer. It does not accept cookies,
PATs or internal callback tokens as research authority. Human APIs retain their
cookie+CSRF checks. Owner admission issues a token only with
`RESEARCH_AGENT_API_ENABLE=true` and the dynamic user-runner transport selected.
Runner/controller/API Pod-rendering configuration must separately set
`CODEX_RUNNER_RESEARCH_API_ENABLE=true`, which renders
`RUNNER_RESEARCH_API_ENABLE=true` in the Pod. Both flags default false. Enabling
them or deploying the new network profile requires the independent gates below.

The native profile allows network under unchanged D1 and one exact read-only
`/state/research-api/turn.json` root. No directory/writable root or Pod security
grant is added. Placeholder/grant changes use fresh inode atomic replacement.
Plaintext is never persisted in API metadata or injected into prompts/env/argv.
The runner journals it before file exposure, invalidates authority in memory on
reader refusal/completion/interrupt/EOF and clears the file in worker cleanup.
Private raw working native files may contain an echoed token; they remain
inaccessible to the user/API/sandbox and must be invalid before preservation.

Authentication does candidate lookup (connection returned), bound live runner
status (connection returned), then a fresh locked transaction in Account→mutation
lock→Conversation order. No final-transaction RPC/nested checkout occurs. Status
uses a nonblocking local service-auth read, never Native RPC/callback or refresh.
The authorization deadline is15s, positive status freshness5s, pool wait at most3s
(early refusal if it cannot fit), SQL3s/lock1s/status HTTP2s. Already observed
requests can finish under the original C/R; this is not an atomic native drain.

Token hard TTL is min(30min, original login expiry), independent of overall turn
length. Expiry is401 research_token_expired; no renewal/retry/replay. New admission,
terminal refresh, interrupt, logout, role change and restore revoke hashes under
conversation locks. Every call rechecks fresh login/role/scope and binding before
idempotency replay. Schema008 and original authors remain unchanged.

API mutations and strict safe receipts share one commit. Failed actions roll back
their savepoint before a fixed failure receipt; unknown commit is never success.
`GET <basePath>/api/research/sessions/{id}/research-calls` uses ordinary owned cookie
authentication and reads those records without refreshing native history/writing.
Refresh preserves API-owned receipts/grants; #90 SSE remains DB-write-free.
Current and retired research token syntax is always redacted. A durable usage
marker keeps a fixed124-character streaming syntax holdback across restart,
including uncovered retired tokens, without changing #95/#99 provider flags.

The root-owned CLI exposes `schema [operation]` and JSON stdin operation calls.
It reads the fixed file, verifies HTTPS443 against the explicit platform CA,
ignores proxy/CA/keylog env, follows no redirect and has a60s wall limit without
retry. See the root-owned `anxious-research` skill for direct-save/CAS guidance.

The approved B0 is recorded on #97:
https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/97#issuecomment-6920408220
It authorizes implementation, not live credentials, VM/production execution or
model calls. Current actual model allowance is zero. File-only read roots and
old namespace/FD isolation require actual pinned-kernel proof before grants.
The development TLS fixture requires separate source/work/recovery review.
OCI hairpin and the exact effective #62 D1 policy require independent evidence.
See [development gates](../infra/hosting/m3/agent-research/DEV-VM.md) and
[production preflight](../infra/hosting/m3/agent-research/R2-PRODUCTION-PREFLIGHT.md).

Back runs Docker pure unit tests only. Review owns PostgreSQL/API concurrency,
full regressions, actual pin/kernel/TLS/network/secret isolation, F2/e2e and008
compatible recovery. Earlier MCP unit/latency evidence is not r2 evidence.
