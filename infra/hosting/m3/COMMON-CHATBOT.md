# Issue #9: request page scope and per-user runner verification

The API accepts an optional page descriptor on each new message. Missing descriptors
mean general conversation with no storage tools. The broker resolves the resource,
saves its page/snapshot/permission scope before dispatch, and binds callbacks to the
same owner, session, request and live login. Browser navigation does not change an
accepted request. Exact idempotent replay returns that accepted response; another
descriptor with the same key conflicts. Explicit storage CRUD remains available.

The native catalog stays registered in the same thread. Our pinned Codex 0.160.1
(`d27764b82f7118f674371e6d6e76271d9d606edb`) has `dynamic_tools` in
`app-server-protocol/src/protocol/v2/thread.rs` ThreadStartParams, but no replacement
field in ThreadResumeParams, ThreadSettingsUpdateParams or TurnStartParams. The
[app-server documentation](https://learn.chatgpt.com/docs/app-server) describes
thread-start dynamic tools persisting in rollouts. Every new turn receives the
current effective tools and page snapshot, and both runner and API enforce its
type/resource limits. Earlier model memory persists; it does not grant tool access.

Platform request metadata lives under `native_record.platformRequests` in the
existing JSONB column. This is broker-authored metadata, never accepted from a
runner snapshot. Refresh preserves it and associates a native turn with the
accepted request; the public user message displays the original question plus
its page/capability. Old native-only history remains accessible. The session's
old creation `context` is historical and grants nothing to a general turn.
No schema migration, data deletion, credential export or new tool is required.

## Isolation support and limits

`CODEX_RUNNERS_FILE` is a static account UUID -> URL, token file, state UUID mapping.
Canonical owner IDs, distinct canonical destinations, state IDs and transport
secrets are required. Duplicate JSON keys and owner aliases are refused. The
transport verifies both owner and state on every response. Each adapter leases
one bound volume and starts its own native subprocess with separate HOME,
CODEX_HOME, workspace, native history and dispatch ledger. A state marker mismatch
fails before launch and remains a refusal after detection. Tool authority is
derived from the conversation owner and current request, never a body owner ID.
Unmapped accounts cannot borrow a runner.

Two-user tests run two actual adapters and two separate subprocesses of the offline
JSON-lines native fixture. They submit through the API, roundtrip storage callbacks,
verify distinct documents/histories, reject wrong transport secret/response owner,
and test an unmapped third account. Additional existing tests cover volume leases,
state swaps, process replacement, callback turn/login/owner binding and revocation.
These are source/fixture evidence, not two real authenticated provider accounts.

The current personal execution gate still permits only the explicitly enabled
personal owner on the approved origin. The live deployment has one user. Static
mapping infrastructure can isolate multiple provisioned runners, but this change
does not enable multiple production owners or auto-provision signup, runner Pods,
volumes, provider credentials or renewed access windows. Multi-account operation
would require its own approved provisioning/admission policy and real account
verification. Model pin, existing access expiry/budget and network policy remain.

For a future multi-user release, required inputs include each approved platform
owner UUID, private runner endpoint, unique transport token file, durable state
UUID/volume binding and separately authorized provider identity/access window.
Required work includes an admission policy for multiple owners, provisioning and
deprovisioning/credential rotation, per-user capacity and expiry/status handling,
and real two-account end-to-end isolation verification. Endpoint aliases must be
checked operationally too: URL canonicalization cannot prove DNS names resolve
to distinct dedicated servers. Response owner/state verification remains mandatory.

## Docker verification

From the back worktree, use an isolated project and disposable database:

```sh
docker build --target test -f backend/Dockerfile \
  --build-arg APP_GIT_SHA="$(git rev-parse HEAD)" \
  --build-arg APP_BUILT_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  -t anxious-s13-back-issue9-test .
docker compose -p anxious-s13-back-issue9-test \
  -f backend/compose.m3-test.yml -f backend/compose.page-chat-test.override.yml \
  up -d db mock-github
docker compose -p anxious-s13-back-issue9-test \
  -f backend/compose.m3-test.yml -f backend/compose.page-chat-test.override.yml \
  run --rm -T test pytest -q -p no:cacheprovider /app/tests
docker compose -p anxious-s13-back-issue9-test \
  -f backend/compose.m3-test.yml -f backend/compose.page-chat-test.override.yml \
  down -v
```

## Deployment review plan

This is an application change proposal, not authorization to apply it. PM executes
only after source and the final artifact/guarded patch package are reviewed. The
final shared branch must contain the matching front, API and runner changes.

1. Build API/runtime, runner (same native pin) and root web from the final reviewed
   source SHA. Record source labels, immutable references, Docker/OCI descriptors,
   archive checksums and imported CRI/Pod associations with the existing audit
   tools. Never substitute a fixture or assume a mutable tag is equivalent.
2. Take fresh private baseline Deployment UID/resourceVersion/full spec and
   existing owner/state/mapping associations. Confirm no running or ambiguous
   turn. Verify current DB revision/grants/readiness, runner state and restorable
   quiescent backup; preserve native/control PVCs, transport secrets and overlays.
3. Prepare exact guarded image-only forward/rollback patches for the existing
   root web, API and `access-web-runner-v1`. Independently review server dry-runs
   and restart/capacity/maintenance handling before execution. Existing #7
   renderers have sealed baseline/image assumptions: do not pass new references
   to them as if already approved, initialize another volume or silently adjust
   policies. Publish an updated reviewed patch/provenance package at that stage.
4. Keep admission unavailable throughout the coordinated rollout. A mixed old
   API/new runner can consume a turn before denying its missing scope; a new API
   cannot promise scope enforcement by an old runner. Do not admit a turn until
   all three artifacts/readiness and recorded owner/state bindings match.
   Operator admission controls must use reviewed existing procedures; this
   implementation does not change production flags or auth windows.
5. Check root origin/base path, auth/status/readiness and preserved conversation
   records without model dispatch. Separate fixture evidence from any explicitly
   authorized real general/page/tool turn; no automatic retry for an ambiguous
   turn. Existing auth must independently be available for real checks. No new
   supply, rearm or extra live user is part of this source work.
6. On failure, keep admission unavailable, collect safe evidence, and apply the
   reviewed reverse image patches after quiescence. Retain DB and native/control
   history; never database-downgrade or restore a ledger to replenish budget.
   Do not resume a #9 scoped request with old code or replay it automatically.

Final images, imported references, fresh baseline patches and live outcome are
separate evidence still required before production execution/final rollout approval.

The concrete emit-only package and PM sequence are documented in
[COMMON-CHATBOT-OPERATIONS.md](COMMON-CHATBOT-OPERATIONS.md). It preserves the
existing reconnection authorization: the reviewed same-account/source/environment
supply procedure can be executed by PM within that authority, without requiring
repeated user permission. This preparation performs no supply or automatic rearm.
