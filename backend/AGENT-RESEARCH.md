# Agent research API (#97, r2)

Branch: `task/agent-research-api-97`. The approved direction is owner native shell
calling the public research HTTPS API using the `anxious-research` skill/CLI.
MCP, per-turn process restarts, persistent peers, mutation fences, settlement/ACK
and benchmark images are removed. This removal checkpoint does not yet enable
research API credentials or certify a release.

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
