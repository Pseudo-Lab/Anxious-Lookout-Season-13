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

For the development VM, PM's later fence recovery decision on 2026-11-16
replaces the original 8804a97 cleanup target with the new persistent candidate's
runtime and **fence-aware ordinary runner**, using the exact source/digests
recorded in its delivery metadata. Before applying 0008, import the separate
storage-only fallback runtime and ordinary runner from
`2e4da866bb12a1ad2bf2669917cff3e52899a185`. An unsettled fence forbids new turns,
runner replacement and rollback until matching DB terminal settlement. Never
delete control state or infer non-application. Only after proving **zero unsettled
fences** may the old 8804a97 candidate or 2e4da86 health-failure fallback be
selected. If zero cannot be proved, stop and report to PM. Keep 0008 and all data
in every case. The exact image
metadata/archive are relayed through the review inbox; Docker config IDs must
not be mistaken for imported manifest digests. Follow the full stop/binding,
flags/session cleanup and certification steps in
[the development procedure](../infra/hosting/m3/agent-research/DEV-VM.md).

## Verification ownership

The persistent-process replacement follows the original per-turn restart
candidate's failed C1. `runner/research_authority.py` contains
pre-model bootstrap, exact process/dedicated connection and per-request one-use
permit primitives. `runner/research_fence.py` provides a write-ahead fence on the
existing private durable control volume, with file and directory synchronization
before dispatch and exact terminal receipts. `runner/research_persistent.py`
connects these boundaries to Native. Same-thread warm turns retain the native
process and MCP child. Native process/web-mode/thread changes require another
pre-model bootstrap and exact native catalogue verification. Missing proof fails
before turn/start. Real pinned startup/parent/metadata behavior remains a review
gate, not a property established by the unit tests.

No new settlement bearer or Prepare operation is introduced. A confirmed
original callback durably terminalizes only its matching fence and permits the
next mutation in the same turn. An uncertain mutation blocks further writes,
requests native interruption without fabricating a user stop, and preserves the
original failed turn. Ordinary owned API polling obtains the protected fence
through the existing bound runner read. `app/research_settlement.py` try-locks
the same mutation lock in a separate transaction: matching success is applied;
absence becomes a terminal not_applied idempotency tombstone that prevents even
an already-authenticated late Execute. Commit precedes ACK. Every tool/post
adapter passes the common terminal replay guard in Store.mutate.

The existing API-to-runner service authentication carries
`POST /sessions/{conversation}/research-settlement` with the exact immutable
binding, outcome and the Pod UID observed by read. Owner/state/actual Pod/stop
fencing remains in Runner/PooledRunner/BoundRunner; the runner also refuses a
different Pod UID. Duplicate matching terminal ACK is durable and idempotent;
stale or conflicting ACK cannot clear another fence. A terminal receipt is
retained until the next intent so a lost ACK can be reconciled. Neither API tool
callback ingress nor shell egress is broadened. The public owned summary exposes
only optional `storage.state` (checking/applied/not_applied/unknown); fence ids,
fingerprints, native bindings and credentials remain server-only. The F2 event
envelope still awaits actual C2.

The fence uses the existing durable private control PVC, outside native-home
backups. Do not delete it on process/Pod exit, auth cleanup or image rollback.
An execute_intent must be settled under the matching DB lock before new model
admission. An older runner image that ignores it is not safe to admit turns
while an unsettled fence exists; recovery must retain the admission barrier.

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
