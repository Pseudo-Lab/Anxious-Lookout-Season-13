# #7 current-auth options: persistent stock topology versus bounded trial

Proposal/comparison, not source read/supply/startup permission. Runtime70/nativeoff,
runner imported-only and completed owner lookup remain unchanged. User selected
current working-session authentication; no independent profile/new login/API key.
Earlier63bd investigation was approved as constraints only, not host runtime work.

## Two distinct outcomes

| Option | Concrete connection | Original session impact | What it could prove |
|---|---|---|---|
| Persistent shared integration | At future normal connection, stock TUI0.160.1 `--remote unix://<selected-private-socket>` and a persistent stock app-server; app gateway drives only owned web threads on that server | Selected active embedded TUIs cannot be moved there by a web callback; safe ordinary-lifecycle consolidation of all same-grant writers is required | Continuous one-owner refresh only after topology/participant/lifecycle plus UID/tool isolation is actually verified |
| Limited access-only/refuse trial | One frozen access-token snapshot from exact selected original file, supplied in memory to isolated web native external-token mode; every refresh callback fails | Original TUIs/cache/refresh stay untouched; no extra renewable refresher | Conditional two-turn/tool/private-PVC same-thread resume before expiry; no long-lived shared renewal or #7 completion |

Option2 is technically meaningful as a **conditional experiment** after small app-side
changes/negative tests, not executable using current PersonalAuth or a new flag.
Eligibility, actual selected-grant/client/routing support and exact source supply
remain unverified. No production adapter or host AuthManager patch is implemented
by this comparison.

## Option1: stock endpoint, not abstract owner delegation

Official CLI describes remote Unix connection for interactive/resume/fork. Official
app-server has a Unix listener. Cached exact0.160.1 TUI resolve_remote_addr() confirms
that code path. [CLI](https://learn.chatgpt.com/docs/cli/reference),
[App Server](https://learn.chatgpt.com/docs/app-server).

Candidate future stock commands, never run against current originals here:

```sh
# After reviewed normal-lifecycle transition, using the selected original home/config.
"$SELECTED_CODEX_01601" app-server --listen "unix://$REVIEWED_PRIVATE_SOCKET"
# Original client's next normal invocation/resume uses the selected same endpoint.
"$SELECTED_CODEX_01601" --remote "unix://$REVIEWED_PRIVATE_SOCKET"
```

Exact executable, source store/path, private socket/peer controls, original config
compatibility and normal session resume are resolved privately. Host supervisor
keeps this one authority alive after terminal clients exit; future original clients
join it, not standalone grant refreshers. It uses the selected original cache through
native managed auth, no cache duplication/returned rotated copy. Rollback cannot
leave two uncoordinated managers on that grant.

PM actual selected three0.160.1 TUIs are not established as consumers of the distinct
0.162.1 daemon. Stock remote capability is not a live retrofit into their embedded
managers. Do not start/adopt another managed owner while they remain writers. Current
TUIs continue; a transition waits for their normal lifecycle and positively established
participation of all same-grant consumers. Unknown consumers/independent writers or
an unreachable normal transition make Option1 unavailable under present constraints.
This is a launch/topology change with real original-session implications, not
permission to patch/restart them. It avoids a speculative native token-export fork.

Web gateway work remains: RPC allowlist/correlation; per-UID durable broker/ledger/
projection metadata; isolated web cwd/read roots and per-thread tool/model restrictions;
only owned thread events/calls/read/resume; no auth/config/command passthrough, other
original thread reads or termination. Closing a web Pod disconnects its connection,
not the authority. Native thread storage would remain at the persistent owner, so its
placement/recovery differs from an isolated native-home PVC and needs explicit review.
No process-global feature edits that alter original TUI capabilities.

Stock account/read plus a separate turn/start does not supply an atomic expected-auth-
generation admission contract. A gateway cannot claim that sequence closes owner
switch races. Original auth mutation restrictions, selected grant/account pinning and
server-supported dispatch binding must be demonstrated; if native changes are needed,
that becomes separately scoped runtime maintenance, not a stock setting or this
comparison's implementation permission. A topology that fixes refresh serialization
alone is not approval of full source-generation/UID/tool isolation.

## Option2: exact boundaries and early stops

Official external-token mode accepts accessToken/account metadata and asks the host
for refresh; it is experimental and assumes a host owning auth lifecycle. Here the
original clients retain renewable ownership and a trial controller would enforce a
finite delegated lifetime, refusing renewal. Documentation of the interface does not
promise this selected grant's long-term reuse, hosted/multi-user support or entitlement.
[External tokens](https://learn.chatgpt.com/docs/app-server#3c-log-in-with-externally-managed-chatgpt-tokens-chatgptauthtokens).

Actual operation is conditional on owner-selected existing source, its effective file
store/authority, supported private single-owner use, source format, sufficiently live
JWT and fixed-model/account/routing checks. If those cannot be established, report
ineligibility; never infer support from a fake JWT or bundled model catalog. Use the
existing Codex route, not SIWC registration/client IDs/api.openai.com grant conversion.

Proposed narrow stages after source-plan review and user's bounded-test decision:

1. PM verifies exact current effective file source privately (auth-related CLI/config
   overrides only if needed; no broad HOME/keyring/account search). Approved B format
   diagnosis can precede supply; it does not establish authority or expiry. No source
   content has been read by back. Reuse completed private platform owner lookup.
2. A new explicit access-only controller validates the selected canonical regular/
   private/single-link file with O_NOFOLLOW/bounded1MB, reader Unix ownership and
   host+descriptor before/after stability. JSON parsing transiently reads the file
   in host reader memory; **no whole cache is exported or stored**. Derive only access
   token, bound account metadata and exp into a frozen in-memory envelope. No refresh/
   ID token in native Pod, env, Secret, DB, browser, message, CLI argument or log.
   Exceptions/stamps/receipt remain private; never echo/tee the credential envelope.
3. Proposed admission policy: access JWT exp present/consistent and at least20min
   remaining; trial deadline≤15min and before JWT expiry with clock-skew margin.
   These are candidate test bounds, not observed token lifetimes. Decoding claims is
   not signature/authentication proof. Invalid/unparseable/too-short/expired/API-key/
   mismatched account/source file refuses **before native launch/reservation**. This
   new application guard is not implemented/proven by current native/probe.
4. Supply only that frozen access envelope over reviewed private operator transport
   to one owned Pod, after owner/state/image/marker validation. A concrete candidate
   is bounded stdin bootstrap (`stdin:true`, `stdinOnce:true`, ttyfalse) via Kubernetes
   TLS attach to the exact recorded Pod UID, consumed once and held in process memory;
   no public credential endpoint/file/env. Envelope fields bind owner/state/trial/
   deadline/account. Exact attach behavior, timeout/partial-input/no-logging and
   sender/receiver implementation must be tested/reviewed before actual supply.
   A small operator controller retains the same snapshot in private memory for the
   two intended supplies across clean Pod replacement; loss/expiry/ambiguous receipt
   stops rather than rereading/refilling or dumping it to a file.
5. Web native uses fresh private home, ephemeral credentials, explicit experimental
   external login, exactlygpt-6.1-sol. No original HOME/control socket/cache mount.
   Callback always immediately returns a redacted error, latches terminal trial
   failure, cancels only an owned active turn and never refreshes/re-reads/resupplies.
   Native auth.openai.com refresh egress is denied; missing tokens cannot fall back
   to managed cache/API key. Account/routing bootstrap401 may fail before any turn.
6. **Refusal alone is insufficient:** default retries made six Responses401 POSTs
   in the fixture. Both HTTP and stream retry counts must be zero and verified in
   the real chosen native configuration.0.160.1 rejects overriding reserved builtin
   openai. A reviewed named configuration alias pointing to the **same authenticated
   original Codex backend**, OpenAI auth/Responses wire API and fixed model could set
   these counters; it must preserve account headers/selected workspace/residency/
   exact FQDN/path. This is not permission for a different provider/billing/model.
   If compatibility/support of that alias is not established, Option2 stops; do not
   silently switch API routes or weaken retry requirements. The loopback alias in
   tests proves retry mechanics only, not real backend compatibility/entitlement.
   [Retry settings](https://learn.chatgpt.com/docs/config-file/config-reference).
7. Dynamic backend_origin/account routing may require authenticated bootstrap network
   work before a model turn. Exact supported original endpoints/routing and Cilium
   rules must be reviewed; no wildcard/unknown-host discovery runner. Keep telemetry
   behavior accounted for. model/list/account-read are not entitlement; only explicit
   successful fixed-model turns can prove that request's access.
8. New defaultoff AccessOnlyTrialAuth must reuse durable request ledger/max3/private
   projection journal/owner-state/thread markers. Do not route it through PersonalAuth
   attach/clean-cache-copyback or loosen exclusive/quiescent flags. Reserve before
   dispatch; 401/expired/model/unknown failures after reservation retain their charge.
   Same-key replay cannot dispatch again; Pod restart preserves trial/deadline/requests.
   Missing/changed state/thread, forged browser fields, failure latch or unclean exit
   refuses. Budget is three explicit trial admissions, not a claim of three total
   provider HTTP/tool round trips. Original TUI work is not this trial ledger.
9. Deadline checks apply at admission/tool/persistence/read boundaries, including
   expiry during pending work. Expiry/refusal makes status unavailable, preserves UI
   input/no autoresend and same consumed ledger. No fresh token/trial/deadline to make
   a failed test pass. Native receiver must deliberately handle external auth mode;
   current app expects managed chatgpt and has no production access-only factory.
10. Two explicit success turns only while enough window remains: save/read owned
    synthetic data; disable admission/quiesce; clean owned Pod replacement; same
    owner/state/PVC/trial/frozen envelope/native thread follow-up. Sender expiry/loss,
    callback/refusal, partial dispatch or unclean shutdown aborts; no slot2 acceptance
    assumed. Quiescent EOF/exit0 within55s is required, no forced kill classified clean.
    Finish disables trial, drops volatile access buffers, retains ledger/tombstones;
    original client/cache/logout are untouched. No refresh on terminal exit.

Immutable access binds the initial account; it never obtains a different account's
new token after source switch. Source stability checks can detect changes at sampled
boundaries, but do not provide atomic original-account-switch/revocation versus
already in-flight model effects. Same-account refresh versus replacement also cannot
be proved solely by timestamps. Report this gap. Freeze/stop-on-detected-source-change
can narrow a bounded test, not solve shared-generation admission or original continuity.
If atomic original lifecycle coupling is required for this test, Option2 is ineligible.
Original TUI refresh can invalidate the frozen access token sooner than exp; native
refusal ends the test, it does not repair original sessions. Nothing here guarantees
later web access after the original terminal closes or the snapshot expires.

## Concrete negative evidence and remaining gates

`check_access_only.sh <exact public0.160.1 models-manager/models.json>` invokes three
owned network-none/read-only Docker fixtures in exact runner6fc. Local synthetic TLS
accounts/routing/model catalog and Responses401, fake JWT, no original auth/client:

| Fixture | Observation | Meaning |
|---|---|---|
| Default retry counterexample | callbacks6, Responses POST6, after-refusal POST5, turnfailed; no managed refresh/cache; exit0 | Callback error is not sufficient to prevent native general retries |
| request/stream retries0 | callback1, POST1, after-refusal0; fixedmodel, turnfailed; no managed refresh/cache; exit0 | Refusal mechanics can be bounded in the configured mock route |
| Expired fake JWT (exp1), retries0 | local login accepted, POST1 before failure, callback1 | Native is not an application expiry admission guard; claim-only local acceptance is not authentication |

These fixtures do not implement/test platform budget, source extraction, new expiry
policy or actual source/routing/support. Exit0 for a **counterexample** means its
unsafe behavior was reproduced, not an approved access-only mode. The old5s EOF
negative remains unchanged. An identical-settings observation with only parent wait
extended50s exited0 after10.1s, no force/no auth.json; that addresses feasibility
within55s in that case, not every in-flight callback/tool state. The local mock cases
closed normally in~0.5s. Native stdio EOF/SIGTERM source arms a45s watchdog whose exit1
is unclean; new mode shutdown timing must preserve this distinction.

Before actual test permission: implement/test chosen envelope/source guard and
partial-supply cleanup; expiry before startup and during tool/IO causes zero new
unauthorized effects; no token value/path/hash/JWT in logs/response/DB/files; denied
callback prevents native resends with exact live config; source/account/routing drift
and no-alias-support fail; same-role foreign UID/state/thread fails; consumed/unknown
reservations survive crash/Pod replacement/expiry; original synthetic reader/writer
continues unchanged through web failure; clean55s shutdown with pending callback.
Source support/authority/TTL still needs private operating evidence after authorization.

PM can explain the choice as: a short frozen-token trial may test two-turn/tool/PVC
resume with expiry/failure stops, while continuous shared renewal needs original
client lifecycle/topology work. A bounded trial result must be recorded separately,
not used to close #7 or create the final PR. Do not execute either option, read/supply
real access, patch/restart originals or adopt the separate daemon from this document.
