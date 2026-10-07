# M3 backend implementation and validation

Issue [#6](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/6), shared branch `task/m3-research-data`. The current checkpoint implements private materials/documents, immutable content versions, owner-scoped relations and atomic retry handling. Publication and real Codex integration are still pending; HTTP CRUD passing does not establish actual Codex tool/resume completion.

## Isolated tests

```sh
sudo docker compose -p anxious-s13-back-m3-test -f backend/compose.m3-test.yml build mock-github
sudo docker compose -p anxious-s13-back-m3-test -f backend/compose.m3-test.yml run --rm test
```

This separate project publishes no ports, uses an internal network and a dedicated named PostgreSQL volume, and has disposable fixture credentials. `RESEARCH_ACCESS_POLICY=approved` is a test-only policy choice, not a user/deployment decision. All existing M2 tests plus the M3 tests run inside Docker. Do not use production accounts or another agent's credentials as fixtures.

## Migration and app rollback

Run `python -m app.migrate --revision 0002_research` only in the reviewed operator container/Job with private ADMIN_DATABASE_URL and API_DATABASE_PASSWORD inputs. Startup never creates schemas. The runner validates/adopts M2 exactly as before, then runs the additive research migration in the same serialized transaction. Failed DDL rolls back. Unversioned/unknown research schemas are rejected. Never downgrade/reset to roll back an application.

Research uses a separate Alembic history after verified `0001_auth`. `auth.alembic_version` remains `0001_auth`; `research.alembic_version` becomes `0002_research`. This lets the unchanged M2 image/readiness and tooling operate during app rollback while preserving M3 data. Research data tables use auth.accounts.id and composite ownership/type foreign keys. The app can append but cannot update/delete content versions or retry records and cannot write either Alembic marker. Operator authority remains absent from the API.

Deploying is not authorized by the development request. An eventual reviewed rollout must record the image SHA, acquire a pre-migration backup, migrate explicitly, deploy API before a dependent UI, and verify both namespaces. Application rollback reverts API/web images to the known M2 release, retaining research schema/data; UI must stop calling M3 endpoints. Returning to M3 then reuses the retained versions and relationships.

## Authorization, retries and policy gates

The default `RESEARCH_ACCESS_POLICY=pending` denies private research with `503 policy_pending`. `approved` and `editors` are implementation capabilities awaiting the PM-recorded user decision; no deployment choice has been made. Unauthenticated/expired sessions get 401; unapproved or disallowed roles get 403. Mutations require exact Origin and the M2 session CSRF token. Foreign IDs get 404. Transport protection applies to research session-cookie requests as well as auth routes. Error responses do not echo rejected content, SQL or credentials.

The owner/key advisory transaction lock serializes identical retry keys. A single transaction stores changes and the success response; failure commits neither. Replay first revalidates current authentication, approval/role and CSRF. A changed payload/operation with the same key conflicts. Item row locking plus expectedVersion prevents lost concurrent writes; reversed undirected relations normalize to one pair and a partial unique index prevents duplicate active links. Archives preserve private content history; active relation traversal hides archived endpoints.

Publications currently return policy_pending and Codex status reports unavailable/unverified. These are explicit incomplete features, not fake success. Publication reference exposure, access roles and historical public releases await user decisions. Sessions must ultimately preserve native Codex records as well as complete displayed tool arguments/results, and keep the visitor's conversations private from public authors. A public document is initial conversation context; it cannot restrict later access to the visitor's own materials or revoke their saved conversation on withdrawal.

## Backup boundary

The existing M2 auth-only dump is insufficient for M3. The reviewed M3 backup must consistently include both `auth` and `research` schemas and their migration markers, alongside each user's original Codex home/rollout/state volume when enabled. Use separate private artifacts and checksums; never mix one account's runner home/credential with another's. Restoring must recreate the app role with reviewed grants and verify owner references, historical content, relations, retry records, original tool input/output and native resume. A DB dump alone cannot demonstrate native Codex recovery. Actual operations and provider resume remain unverified until authorized inputs are supplied.
