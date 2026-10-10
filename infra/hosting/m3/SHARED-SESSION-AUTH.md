# #7 selected current-session auth: applicability and minimum change

2026-10-10 investigation/proposal; runtime70/live nativeoff and runner imported-only
remain unchanged. The user explicitly selected the **current PM/back working-session
Codex authentication**. Independent-profile selection is superseded, not requested
again. Platform owner lookup already succeeded privately; reuse its result without
repeating A or deriving a GitHub login from email. One issue/branch, fixedgpt-6.1-sol,
owner/state/thread/tool separation, two explicit turns/total3 remain.

## Conclusion for the actual selected clients

An added app-server with a copied renewable cache is not a shared auth owner. The
selected active TUI processes have their own auth-management path; the observed
separate daemon has not been established as their owner. A new file lock/broker
outside those processes cannot serialize refreshes that their existing AuthManagers
do not delegate to it. A read-only access-token snapshot adds no refresh writer,
but cannot guarantee renewal after the original clients terminate normally.

The smallest coherent target is **one host auth authority used by original clients
and isolated web native processes**, delegating short-lived access tokens only to
web runtime through the native external-token protocol. This preserves private web
native HOME/PVC/thread/tool configuration. It needs an actual owner-side refresh /
access-delegation interface; adding just the web-side callback cannot create that
interface inside currently running TUIs. Current stock selected runtime provides
no established live hook for this authority. There is no demonstrated immediate
implementation meeting all constraints with all current TUI auth paths unchanged.

Minimum required original-runtime change is an auth-owner delegation interface and
participation by every selected same-grant consumer, including lifecycle after
normal terminal exit. If that cannot be introduced without reconnecting a selected
client, migration waits for its **normal** lifecycle and a coordinated future launch;
no forced stop/restart/logout, hot patch, new login or presumed quiescence. Original
working sessions continue. This is a technical prerequisite, not another request to
approve using their authentication or to select an independent profile.

## Actual metadata and version boundaries

| Item | Evidence | Interpretation |
|---|---|---|
| Selected PM/back/review live exe | PM host observation:0.160.1, no explicit daemon/listen flags, no CODEX_HOME env override | These are the selected version; launch config overrides remain unresolved |
| Selected Unix connections | PM:12 connections, each observed socketpair owned on both sides by the same PID | Consistent with embedded clients; not proof of all writer absence |
| Separate control listener | PM:app-server-daemon0.162.1, UID1000, real listener/socket | Not linked to the three TUIs; not adopted as their AuthManager |
| PATH-installed launcher | Local symlink0.162.0; executable SHA7993e8a93e93611bdb9b1129c22dcb2036d52f5065f548aa841468cccf36046a | Installation metadata, not active selected-client version |
| Exact known local config | Readonly network-none Docker: store override unset; default OpenAI provider; no selected-profile/custom-provider override | Does not resolve CLI/system requirements or private active auth state |
| Known default file entry | Metadata only:regular, UID1000,0600,single link | Candidate for approved B after exact effective source is established; content not read |

PM evidence: `/tmp/issue7-shared-runtime-metadata/`, kept private. Back's sandbox
process ancestry is codex-linux-san, so sandbox-local socket target observations do
not override PM's host listener proof. Back did not connect to the original socket,
read auth contents, alter source permissions, or stop/refresh any original client.
Do not repeat broad metadata collection or enumerate HOME/keyring/accounts.

## Official support versus source proof

Official OpenAI documentation describes managed ChatGPT auth and experimental
external `chatgptAuthTokens` with host-supplied access tokens and refresh callback.
It also documents stdio and Unix/WebSocket app-server transports. These interfaces
alone do not identify the active TUI's auth owner or guarantee shared lifecycle.
[App Server](https://learn.chatgpt.com/docs/app-server).

Codex CLI/IDE can share cached login storage; storage can be file/keyring/auto/
ephemeral. Sharing storage does not establish a common in-process refresh lock.
[Authentication](https://learn.chatgpt.com/docs/auth).

Renewal requires retaining latest rotating tokens and serializing same-session
refresh. This lifecycle principle does not authorize applying SIWC registration,
client IDs or `/v1/responses` grant semantics to an existing Codex-managed grant.
No new SIWC registration/API key/provider substitution is proposed.
[Accounts and sessions](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions).

Cached official source rust-v0.160.1 / commit
`d27764b82f7118f674371e6d6e76271d9d606edb`, archive SHA
`a5b35cb05cbeedda98217b089c684eb2c56177e717b3ef9ec01163e9b75cd61e`
was rechecked. Source files in its codex-rs tree establish:

- login/src/auth/manager.rs2035–2060: auth snapshot cached until explicit reload;
  each AuthManager contains `refresh_lock: Semaphore`. Constructors create one
  permit per manager;2849/2888 acquire that local semaphore. No external helper
  lock causes another running TUI to join it.
- manager.rs1128–1150 and app-server/src/external_auth.rs18–104: external access auth
  is in-memory/ephemeral and replacement requested through
  account/chatgptAuthTokens/refresh, with10s timeout. No exported refresh credential
  is needed on the web-native side, but a real host owner must answer.
- tui/src/daemon_startup.rs: explicit embedded launches do not discover/start a
  daemon; launch overrides can exclude attachment. New remote-transport capability
  is not a retrofit hook into an already running embedded TUI.
- app-server-transport/src/transport/mod.rs: the correct default rendezvous is
  app-server-control/app-server-control.sock. A distinct daemon at that address
  does not prove selected clients use it.
- app-server/src/request_processors/account_processor/workspace_routing.rs157–190:
  account/read can resolve workspace routing beyond requesting refresh=false.
  Do not treat that RPC as file-only or network-free diagnosis. The experimental
  stable workspace/routing field requires actual supported-version validation.

All source observation was static. No original native account/login/refresh/turn
RPC was sent. AuthManager's stock external callback interface helps the future
web process; it does not expose an owner-side token-export/refresh service from an
unchanged running managed TUI.

## Candidate comparison and minimum implementation boundary

| Candidate | Benefit | Blocking requirement for selected actual runtime |
|---|---|---|
| Attach web gateway to existing common managed app-server | No provider credentials leave owner; reuse one AuthManager | Selected TUIs must actually share that server; current observation does not establish this. Shared server thread/tool/config isolation and persistent owner lifecycle also need proof |
| Isolated web app-server + external access-token callback to original authority | Retains existing owner-private PVC/home, model/tool features and broker/projection structure; no renewable cache in Pod | Original authority must expose latest access+serialized refresh and every consumer must participate; current TUIs lack an established owner-side hook |
| Read latest original file; refuse web refresh | Does not add an independent refresh writer | Source stability is not owner coordination; post-terminal-expiry continuity unavailable. Not the requested complete solution |
| Copy same grant into PersonalAuth / new managed daemon | Existing implementation code is available | Violates shared-refresh constraint; private lock does not cover original consumers. Not used |

Prefer isolated external auth **after authority integration**; attaching directly to
a shared conversational daemon is not a shortcut: process-wide feature changes may
affect original threads, unrelated notifications must never reach web clients, and
closing a web adapter must not terminate the original daemon or original turns.
The selected existing separate0.162.1 daemon is not provisioned/repurposed here.

The minimum code work, once its owner-side integration is concrete:

1. Host runtime auth delegation component uses the selected original AuthManager as
   sole renewable owner. Protected local IPC and an independent transport credential
   admit only the platform-bound web gateway. No browser token endpoint or arbitrary
   host path/config/login/logout/refresh request passthrough. Parent/source/client
   authority and generation are private metadata, not self-declared approval flags.
2. New explicit runner auth mode (defaultoff) requests short-lived access/account
   metadata, uses native chatgptAuthTokens and services only its matching refresh
   callback through that same authority within bounded time. No refresh/ID token
   supplied to Pod, original file rewritten by web or copied cache mounted. Unknown
   owner/revoked/expired/failed handoff refuses; no fallback to managed PersonalAuth.
3. Reuse owner/state/PVC/bookmark binding, projection journal, permanent request
   reservations/max3 and model/reroute checks. Factor these from PersonalAuth's
   renewable-cache attach/clean-copyback lifecycle into a credential-free guard;
   preserve versioned old mode behavior and UID-R1 sticky failures. Update account/
   updated expected auth mode deliberately; current receiver only accepts chatgpt.
   Never remove the barrier or reset budgets to make external mode fit.
4. Observe/pin provider authority/account generation privately before admission.
   Detect source switch/revocation at owner, atomically refuse wrong generation
   **before** native/model/tool effects; a health-read→submit race is not solved by
   matching email or callback previousAccountId alone. Demonstrate actual owner-side
   atomic support instead of declaring a gate around a separate unchecked dispatch.
5. Graceful shutdown disconnects only the owned web child/connection and retains
   thread/budget/latest authority continuity. No original logout/revocation, cache
   copy-back or forced original process stop. Authority persists after original TUI
   exits normally and accepts future originals through the same coordinated path.
6. Backend authors new exact API/runner mode/config/policy/UID-RV enable/disable;
   PM reviews resolved authority inputs and applies after independent approval.
   Public browser APIs/errors remain existing scoped contracts; frontend is notified
   only if they change. No DB/email schema, usage-analysis storage or model change.

A standalone broker loading original auth.json with its own AuthManager does not
satisfy step1 while the current embedded clients remain independent writers. A new
flock, bool or socket existence check cannot substitute for original participation.
The minimum host-runtime integration location/activation path must be resolved
before source supply or implementing a fake owner callback as production.

## Credential-free probes and required verification

Exact executable schema generation ran in network-none/read-only Docker with only
the selected installed binary or sealed runner image and fresh temporary HOME.
Native0.160.1 and installed0.162.0 both expose external login and refresh request
shapes. Schema directories are /tmp/issue7-pinned-runtime-schema-01601 and
/tmp/issue7-current-runtime-schema-0162; installed0.162 proves compatibility surface
only and is not used to infer selected TUI behavior.0.162.1 daemon was not executed.

Synthetic pinned0.160.1 external-token probe locally accepted initialize/login RPC
with fake JWT and wrote no auth.json. Account/read in network-none did not complete
its required metadata path. Even login-only EOF exceeded the probe's5s bound and
its **owned test child** was killed (exit-9); clean external shutdown is unverified.
This is partial/negative evidence, not a passing auth/availability/lifecycle check.
It made no model/thread/turn call and mounted no original auth. Reproduction and
exact findings: artifacts/back-issue7-shared-auth-investigation.md and
artifacts/back-issue7-shared-auth-probe.py in comm. Do not copy this fake-token probe
into an actual auth supply step or loosen production shutdown gates from it.

Meaningful future isolated tests: same-owner simultaneous refresh requests delegate
once; fresh generation delivered to every consumer; wrong owner/state/account or
source switch between health and submit causes zero dispatch/tool/ledger effects;
no renewable token in child env/files/DB/responses/logs; original client continues
through web failure/Pod replacement/EOF; original client normal exit does not orphan
refresh authority; stale cache/revocation/crash fails closed; exact same-thread
resume and two turns/total3 count including failed reservations; shutdown under
in-flight external callback within55s without forced clean. Only after owner-side
fixture + exact native compatibility do PM actual original-continuity/model/tool/
replacement checks follow. Existing stage1/source67+1 proof is reused, not confused
with new shared-auth implementation approval.

No active client integration, real cache diagnosis/supply, copied grant, provider
execution, runner start or actual model/tool/resume is claimed by this proposal.
Public posting/final PR remains held. Proceed through same#7 branch with owning
runtime applicability review; never ask for an independent profile again.
