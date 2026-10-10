# #7 shared ingress investigation

Fixture-only; production API/runner and existing sessions remain unchanged.
No shared gateway, lifecycle transition, source supply or runtime fork is enabled.

## Reproduction and verdict

Pass the exact public native0.160.1 `models-manager/models.json` to:

```sh
sudo bash infra/hosting/m3/check_shared_events.sh /path/to/pinned/models.json
sudo bash infra/hosting/m3/check_stock_clients.sh
```

The first command intentionally retains an **exit1 failing stock acceptance gate**
while actual native tool roundtrip is unproven. Do not treat the successful synthetic
first stage as a passing stock result. It prints structured observations before exit.
Both Docker cases pin runner6fc, network-none/read-only/tmpfs, UID10001/cap-drop;
mount only fixture code, backend source for legacy methods, and hash-pinned public
models. Native gets a fresh HOME, explicit custom Unix socket and loopback TLS mock
provider. External-token login uses a locally fabricated non-verifiable token,
ephemeral store and fictional account; no genuine credentials, external endpoints,
original daemon/socket/HOME or cluster operations. Fixed model remains gpt-6.1-sol.

`check_owned_events.py` passes. Two distinct synthetic session tokens each invoke
existing `Native.reply_tool` once for their own thread/turn/call. The fixture ingress
gate buffers early tool requests until the correlated turn/start response. Foreign
thread, previous turn, previous connection epoch, duplicate call or server-request
ID, completion-late and disconnected requests cause no storage callback/reply.
Unknown callback outcome is consumed before dispatch and cannot replay. Accepted
completion also passes through existing `Native.receive`; foreign wrong-model,
reroute and completion messages are excluded before that method, with no model
block/cancel/completion pollution. A method+id server request is distinguished from
an id-only response even when numeric IDs coincide.

This is a sequential fixture per ingress owner. It is not a durable/concurrent
production exactly-once service. Actual storage commit uncertainty remains unknown;
at-most-once dispatch is all this gate demonstrates. Trusted mappings/epochs are
fixture setup inputs, not browser-supplied authorization. No RPC whitelist, browser
UID authentication, production listener or auth transition enforcement is implemented.

`stock_tools_probe.py` launches two clients on pinned stock, registers separate
dynamicTools and starts concurrent synthetic turns. The selected custom mock alias
is a test-only route, not evidence that real builtin OpenAI/account routing supports
it. Each model request has `tools=[]`; neither the top-level tools nor recursively
inspected input `additional_tools` contains the corresponding registered fixture
tool. The mock records that absence and returns HTTP400 instead of inventing an
unadvertised tool call. Results: responsePosts=2, missingRegisteredTools=2,
actualStockToolCalls=0, toolOutputs=0, failedTurns=2, completedTurns=0;
no refresh POST, auth.json or real provider contact; owned native shutdown exit0.
Thus actual stock callback ownership and native-generated late events **failed to
be demonstrated**, not proved safe or globally unsupported.

Earlier diagnostic runs used assertion/connection-close on the same missing-tool
condition; final reproducible case uses an explicit HTTP400 and observes failed
turns. Generic registered-tool schema/request acceptance is insufficient. Cached
exact-tag source validates/passes dynamic tools into StartThreadOptions and includes
registration tests, but it does not explain this packaged runtime observation.
Exact source path: `app-server/src/request_processors/thread_processor.rs`,
thread_start_task; `app-server/tests/suite/v2/dynamic_tools.rs`; core tools/spec_plan.rs.
No runtime feature expansion, alternate model, native build or fork was attempted.

The previous stock-client reviewer minor is fixed: foreign thread event observation
is now asserted in `stock_client_probe.py`, in addition to foreign read/private-name
and transport lifecycle checks. Tool-registration persistence remains unproven there.

## Minimum actual change and unresolved conditions

Reusable candidates are the existing API session/request-bound tool authority,
runner durable request reservation and private expectedThreadId checks, correlated
turn/start buffering and `reply_tool` validation, fixed-model/history projection.
The new fixture shows where a shared transport must filter before `receive`, and
adds connection epoch and call/server-id consumption before tool dispatch. Production
must authenticate UID/session, authorize RPC and owned thread before transmission,
filter all supported event/item shapes before publication or mutation, route server
responses only to their originating connection, preserve durable outcome uncertainty,
and close/interrupt only owned work. This small gate covers selected thread/turn
events, not a complete native event projection or RPC authorization layer.

Do not transplant Native's child spawn/close, managed PersonalAuth cache synchronization
or native-home backup ownership into a shared authority. Current embedded TUI lifecycle
participation, all same-grant writers, ordinary disconnect/recovery and selected-account
refresh continuity remain actual operational conditions. Generation numbering is not
itself a product requirement; ensuring no dispatch under another account or damage to
existing authentication is. Separate account/read→turn/start and a gateway-only mutex
do not yet establish that condition while other auth writers remain uncontrolled.

This scope does not justify production shared-path deployment. The immediate alternative
is to keep API70 native-disabled and existing sessions running while review assesses the
packaged-tool gap. Any version/config change or normal-lifecycle authority consolidation
must be separately scoped; do not expand to a new runtime project to conceal the gap.
Frozen access-only is the prior bounded proposal, not continuous #7 completion or an
authorized fallback. Native-generated tool/late-event and actual selected-account
acceptance remain open, along with private state ownership/recovery for shared threads.
