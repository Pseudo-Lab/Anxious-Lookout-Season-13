# M3 backend implementation and validation

Issue [#6](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/6), shared branch `task/m3-research-data`. The current checkpoint implements private materials/documents, immutable content versions, owner-scoped relations, atomic retry handling and personal conversation/tool broker prototypes. Publication policy and real Codex provider integration remain pending; HTTP CRUD/fixture/native initialization passing do not establish real-model storage/resume acceptance.

## Isolated tests

```sh
sudo docker compose -p anxious-s13-back-m3-test -f backend/compose.m3-test.yml build mock-github
sudo docker compose -p anxious-s13-back-m3-test -f backend/compose.m3-test.yml run --rm test
```

This separate project publishes no ports, uses an internal network and a dedicated named PostgreSQL volume, and has disposable fixture credentials. `RESEARCH_ACCESS_POLICY=approved` is a test-only policy choice, not a user/deployment decision. All existing M2 tests plus the M3 tests run inside Docker. Do not use production accounts or another agent's credentials as fixtures.

## Migration and app rollback

Run `python -m app.migrate --revision 0002_research` only in the reviewed operator container/Job with private ADMIN_DATABASE_URL and API_DATABASE_PASSWORD inputs. Startup never creates schemas. The runner validates/adopts M2 exactly as before, then runs the additive research migration in the same serialized transaction. Failed DDL rolls back. Unversioned/unknown research schemas are rejected. Never downgrade/reset to roll back an application.

Research uses a separate Alembic history after verified `0001_auth`. `auth.alembic_version` remains `0001_auth`; `research.alembic_version` becomes `0002_research`. This lets the unchanged M2 image/readiness and tooling operate during app rollback while preserving M3 data. Research data tables use auth.accounts.id and composite ownership/type foreign keys. The app can append but cannot update/delete content versions or retry records and cannot write either Alembic marker. Operator authority remains absent from the API.

`/readyz` deliberately checks M2 compatibility. `/api/research/health` separately checks the actual reviewed research table/column/default/PK/FK/CHECK/unique/index contract, constraint/index validation and immediate timing, schema access, required DML and prohibited destructive/immutable effective table AND column privileges. Private research calls perform that same check. A matching migration marker alone is insufficient. Re-running migration rejects incomplete schema or grants without silently repairing them. The immutable `002_research_contract.json` and `003_sessions_contract.json` accompany frozen DDL; they are not generated from mutable ORM models at startup. Catalog/grant reads are batched without caching away subsequent corruption; the measured empty-list SQL count is 18 including conversation schema, down from the reviewed 66-query checkpoint. This is an observed query count, not a latency/load SLA.

Personal session APIs require explicit `--revision 0003_sessions`, a non-destructive follow-up in research's history. Asking for the known minimum 0002 after 0003 verifies/retains 0003 rather than downgrading. M2's marker remains unchanged. Complete tool arguments/results are displayed as inert JSON; the original native home is retained separately. Browser/runner fixture reconnect and process restart were verified; real native/provider resume is unverified. See [runner prototype](runner/README.md) for protocol/auth/isolation/lifecycle inputs and planning estimates.

Deploying is not authorized by the development request. An eventual reviewed rollout must record the image SHA, acquire a pre-migration backup, migrate explicitly, deploy API before a dependent UI, and verify both namespaces. Application rollback reverts API/web images to the known M2 release, retaining research schema/data; UI must stop calling M3 endpoints. Returning to M3 then reuses the retained versions and relationships.

## Authorization, retries and policy gates

The default `RESEARCH_ACCESS_POLICY=pending` denies private research with `503 policy_pending`. `approved` and `editors` are implementation capabilities awaiting the PM-recorded user decision; no deployment choice has been made. Unauthenticated/expired sessions get 401; unapproved or disallowed roles get 403. Mutations require exact Origin and the M2 session CSRF token. Foreign IDs get 404. Transport protection applies to research session-cookie requests as well as auth routes. Error responses do not echo rejected content, SQL or credentials.

The owner/key advisory transaction lock serializes identical retry keys. A single transaction stores changes and the success response; failure commits neither. Replay first revalidates current authentication, approval/role and CSRF. A changed payload/operation with the same key conflicts. Item row locking plus expectedVersion prevents lost concurrent writes; reversed undirected relations normalize to one pair and a partial unique index prevents duplicate active links. Archives preserve private content history; active relation traversal hides archived endpoints.

Publications currently return policy_pending and Codex status reports unavailable/unverified. These are explicit incomplete features, not fake success. Publication reference exposure, access roles and historical public releases await user decisions. Sessions must ultimately preserve native Codex records as well as complete displayed tool arguments/results, and keep the visitor's conversations private from public authors. A public document is initial conversation context; it cannot restrict later access to the visitor's own materials or revoke their saved conversation on withdrawal.

## Backup boundary

The existing `infra/hosting/db-backup.sh` already makes a full DB dump, including auth and research, with atomic private archive/checksum publication. It uses --no-privileges; the existing restore-check verifies auth invariants only. M3 recovery must additionally validate research structure/data, explicitly restore reviewed application grants and preserve each user's matching original Codex home/rollout/state volume when enabled. Use separate private artifacts and checksums; never mix one account's runner home/credential with another's. Verify owner references, historical content, relations, retry records, original tool input/output and native resume. A DB dump alone cannot demonstrate native Codex recovery. Actual operations and provider resume remain unverified until authorized inputs are supplied.
