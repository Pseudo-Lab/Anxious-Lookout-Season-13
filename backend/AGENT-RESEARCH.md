# Agent research API (#97 / #104)

Current branch: `task/research-ui-cleanup-104`. The approved direction is owner native shell
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

The server registers one canonical public loader at every native thread start.
The pinned protocol cannot add dynamic tools on thread resume, so this preserves
the original thread when moving from an owner page to a public page. Registration
does not grant access: native and API callback boundaries both deny the loader
in owner mode. Caller-supplied tool descriptions/schemas cannot replace it;
owner research operations remain absent from the native catalog.

The shared response validator `app/research_results.py` is preserved for API
success/receipt validation. It validates actual serializer shapes and resource,
version, saved destination and CAS relationships; arbitrary 2xx JSON
is not evidence of a save. It does not prove a database commit by itself.

## Schema and recovery

`0009_research_pats` adds independent hashed PAT principals/issuance receipts/
safe audit and immutable PAT author names on top of `0008_version_authors`.
Current API/ORM requires009. Existing human/Codex provenance and all proposals,
drafts, publications and content remain unchanged. Direct internal agent saves
use the authenticated conversation; external PAT saves use credential ID/name
snapshots internally. #104 omits version provenance from DTOs, including old
idempotent responses/conflicts, without rewriting stored rows. Public snapshots
retain their public owner byline. Schema stays009; #104 has no migration.
Startup never migrates. Migrate only through the reviewed operator job after
backup/writer drain. Never downgrade the schema or delete provenance for rollback.

`0010_research_wiki` (#105, `db/research_0010_wiki.sql`) adds per-version
`summary` and material `bibliography` (CSL-JSON subset) and relation `evidence`
(≤20 entries). It is additive; existing rows read as null/[] with no backfill.
Current API/ORM requires0010 exactly, so an0009-only image cannot serve it:
back up → migrate `--revision 0010_research_wiki` → deploy the0010 image.
Rollback is forward-fix with a0010-compatible image, never a downgrade.

`0011_ui_feedback` (#119, `db/research_0011_ui_feedback.sql`) adds nullable
`research.credentials.access` ('read'|'write') and the SECURITY DEFINER
`research.membership_audit(...)` read (approved-admin session proof, same as
`set_membership`; anxious_api gets EXECUTE only, never a grant on
`auth.permission_audit`). Additive, no backfill: access NULL = a token issued
before0011, which keeps its write authority (D7). The API/ORM requires0011
exactly, so an0010 image is not ready on an0011 DB: back up → migrate
`--revision 0011_ui_feedback` → deploy the0011 image. `downgrade()` refuses;
rollback is forward-fix with an0011-compatible image, and **every such image
must keep enforcing `credentials.access`** (`research_pats.allowed`: a 'read'
token may call only READS) so a read-only token never becomes a writer.
The relation vocabulary lives only in `app/relation_kinds.py` (no DB CHECK; kinds
stay varchar(80)), served at `GET {BASE}/api/research/relation-kinds`. Agent
wiki rules (`app/wiki_rules.py`) render into llm.md and must appear verbatim in
both SKILL.md files (`config_tests/test_research_wiki.py`). `research_lint` is a
read-only operation (PAT READS, runner CLI, owner page scopes). Contract:
`agent-worktree-comm/api-contracts/issue105-research-wiki-v1.1.md`.

An008-only image cannot serve009. A deployment requires an exact reviewed
**009-compatible recovery API+runner image**, immutable manifest digests, and a
real development recovery read/human-save result. Historical MCP candidates and
old benchmark artifacts are superseded and cannot establish r2 recovery safety.
If a private control volume contains `research-fence.json`, startup refuses and
reports for operator review; the replacement does not delete, settle or replay
that old state. The superseded persistent candidate was not deployed according
to review's VM record; verify actual state rather than assuming it is absent.

## Activation and validation

`POST <basePath>/api/research/agent/v1/{operation}` accepts the existing owner
operation JSON with either a purpose-separated turn Bearer or independent PAT.
Cookies/internal callback tokens do not supply agent research authority. Human
APIs retain cookie+CSRF checks. Internal owner admission issues a token only with
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
idempotency replay. Schema009 and internal author records remain unchanged.

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
Under D7 the CLI pins only TCP to the build-reviewed Traefik Service443 while
retaining the public origin's TLS reference identity and HTTP Host, including
IP literals. Namespace/service are trusted image build arguments; the additive
egress renderer resolves the real selector and single numeric backend targetPort
from matching Service/Pod/EndpointSlice inputs. No direct API8080 or fallback is
introduced. The shared listener's complete route/access inventory is finalized
in production, with a stop on an internet-blocked but runner-accessible route.
Under D9 the dev HTTPS fixture and actual-model gate are retired. Production
uses separately approved no-model D1/route, arm64 kernel, TLS and wrong-credential
checks; the user performs the first real platform Codex use. Final compatible
recovery-image B3 remains a no-model development gate.
See [development gates](../infra/hosting/m3/agent-research/DEV-VM.md) and
[production preflight](../infra/hosting/m3/agent-research/R2-PRODUCTION-PREFLIGHT.md).

Back runs Docker pure unit tests only. Review owns PostgreSQL/API concurrency,
full regressions, kernel/TLS/network/secret isolation, F2/e2e and recovery compatible
with the final migrated schema. Earlier MCP unit/latency evidence is not r2 evidence.

## External PATs (B8/F5)

Cookie-only `/api/research/pats` supports issue/list and `DELETE /{id}` revoke.
Issue requires approval, exact Origin/CSRF and UUID Idempotency-Key, with owner
Account UPDATE serialization. Same normalized key/payload returns the existing
ID and token:null before the active10 cap. Only the first committed201 exposes
plaintext; the DB stores a purpose-separated hash. Default expiry30days,1–90,
immutable name1–80. Revoked rows/authors remain. Valid-cookie list/revoke survives
approval loss; new issue/use requires the feature flag. Browser logout does not
revoke independent PATs. Restore marks all imported PATs revoked, retaining rows,
version attribution, audit and content.

All PATs allow private reads and saves for currently approved editors/admins,
including existing DB scope=read rows. Commenters stay public-only, unapproved
accounts have no private access. Old scope rows are unchanged; new rows use the
existing write value. Management DTOs omit scope/effectiveScope/scopeLimitedByRole.
Issue fingerprints keep the historical hash format (default write) and recognize
either #97 scope fingerprint for identical name/expiry ACK-loss checks, ignoring
scope as authority. Private operations never trust caller
owner/author. PAT calls do not fabricate Conversation/F2 rows
or use runner RPC. Account SHARE→mutation advisory→Credential UPDATE locks and
fresh checks precede every action/replay. The separate bounded admission commit
persists per-PAT60/min quota and lastUsed before action; action failure cannot
refund quota. Sanitized audit and mutation commit atomically. Anonymous public/
agent attempts are limited120/IP/min and600/process/min (single-process scope),
using bounded XFF only from configured trusted peers. PAT syntax is masked even
when retired/unobserved and streamed across chunk boundaries; provider-retirement
flags remain unchanged.

Public `/api/research/llm.md` provides configured API addresses, bearer usage,
operation/schema discovery and CAS/mutation guidance without a DB/auth lookup.
Public distribution at `/api/research/client/{pat-client.py,SKILL.md,operations.json}`
provides the POSIX Python3 client and external skill from `backend/client/`.
External clients use the public HTTPS origin/platform CA and a private0700 parent/
0600 single-link owned file, with no token argv/env, proxy, TLS bypass, redirect or
retry. Internal root CLI remains turn-only with its compiled Traefik Service pin.
Use existing public REST without PAT for public content; commenter PAT may also
read it under PAT quota. Full F5 DTO/response-loss and operation contract lives in
`agent-worktree-comm/api-contracts/issue104-research-cleanup-v1.md` (superseding the
affected fields in issue97-pat-r3-v1.md).
