# Agent research API (#97)

Implementation branch: `task/agent-research-api-97`. The frontend contract is
`agent-worktree-comm/api-contracts/issue97-agent-research-v1.md`. This checkpoint
implements the storage/authorship slice; the MCP lifetime and native history
transition require the separate C1/C2 gates. It is not a production release.

Document and material save/update tools call the same graph-locked Store paths as
human saves. Post `research_post_save` validates Markdown through the existing
block converter, references/images/slug rules, and immutable version writer.
Expected head and explicit draft revision checks happen before a write. A head
conflict includes the current authorized resource; a draft conflict includes its
current draft. Idempotency preserves the first saved version. Post publication
heads and active human drafts do not change. Existing proposal creation helpers
remain only for retained-data fixtures; the tool catalog/dispatcher exposes no
new proposal operation. Existing list/get/accept/discard routes keep their records.

Version authors are assigned by Store context: human by default, Codex plus the
authenticated conversation id at the internal callback. Caller-supplied author
fields are rejected. Private version responses expose `author.kind` and
`author.conversationId`; publication materials strip that metadata so a public
snapshot does not expose a private conversation identifier.

## Schema and recovery

Run `python -B -m app.migrate --revision 0008_version_authors` only in the reviewed
operator migration container/job after a coordinated backup and writer drain.
Startup does not migrate. The additive migration adds author kind/conversation/
reserved credential columns to versions and post_versions. Retained versions
default to human; existing proposals/drafts/publications/content are untouched.
There is no credential/PAT API in this phase. Immutable-version UPDATE/DELETE
grants remain forbidden. Readiness checks the complete prior contracts plus the
frozen additive columns/checks; restore also retains the original Markdown
backup validation and revokes old browser/tool grants.

The prior main image rejects the new marker and extra columns. Do not describe
it as a research-compatible rollback. Before deployment, build and record a
**0008-compatible recovery image** from a reviewed SHA, with its immutable digest,
and validate it on the development VM: apply 0008, start that compatible image,
read existing research and append a human version. Keep the matching DB/image/
runner-home backups. Application rollback must preserve the schema and data and
use a compatible image; never downgrade or delete provenance to make an older
image start. The compatible image and exact recovery procedure are deployment
prerequisites and are not yet certified by this development checkpoint.

For the development VM, PM fixed the exact cleanup targets on 2026-11-16: keep
the `8804a971aa908586b451baac3341029bbc003805` candidate runtime and ordinary
runner (or a later explicitly recorded review-passed candidate), including on
C1 latency failure. Before applying 0008, import the separate storage-only
fallback runtime and ordinary runner from
`2e4da866bb12a1ad2bf2669917cff3e52899a185`. Select that pair only if the candidate
API fails startup/health. Keep 0008 and all data in either case. The exact image
metadata/archive are relayed through the review inbox; Docker config IDs must
not be mistaken for imported manifest digests. Follow the full stop/binding,
flags/session cleanup and certification steps in
[the development procedure](../infra/hosting/m3/agent-research/DEV-VM.md).

## Verification ownership

The persistent-process replacement is being built after the original per-turn
restart candidate failed C1. `runner/research_authority.py` contains independent
pre-model bootstrap, exact process/dedicated connection and per-request one-use
permit primitives. `runner/research_fence.py` provides a write-ahead fence on the
existing private durable control volume, with file and directory synchronization
before dispatch and exact terminal receipts. These modules are **not wired into
the runtime yet**. The active bridge still uses the previous restart candidate.
No new settlement bearer or Prepare operation is introduced. The API-driven
settlement wire is undergoing review before runtime integration.

The local tests use fake native events plus real local Unix/process IPC and
storage fault injection, without a model, API or DB. They check duplicate JSON
keys, sender credentials (including an inherited-descriptor writer), nonce
consumption, process exit/generation, event/frame ordering, permit tombstones,
revoke/claim ordering, unsettled drain blocking and durable fence receipts. They
do not prove the pinned binary's pre-model MCP readiness/parent structure,
metadata/approval behavior, deployed isolation or remote transaction settlement.

Back runs Docker pure unit tests. `config_tests/test_agent_saves.py` uses fake Store
and post functions to verify dispatch/CAS/draft non-mutation boundaries; it does
not establish transaction isolation, SQL constraints, real authentication or MCP.
Review runs `tests/test_agent_research_saves.py` with PostgreSQL, checks the upgrade
on populated pre-0008 data, recovery signatures/grants and all existing suites,
then the actual pinned Codex development VM C1/C2 path. Back never starts the
fixed-name integration compose stacks. Actual MCP approval/history/resume, stream
and interrupt behavior remain unverified until review supplies evidence.
