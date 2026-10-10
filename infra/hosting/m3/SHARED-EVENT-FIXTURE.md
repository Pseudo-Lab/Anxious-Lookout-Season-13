# #7 shared ingress and transport investigation

Current verdict: a separate same-release companion fixture **passes actual tool
roundtrip on stdio and shared Unix**. The original image was blocked by a common
fixture/package code-mode precondition, not Unix transport incompatibility. Registered tools
are present in native metadata and code-mode prompt declarations. The original mock
expected direct function definitions, which is incorrect for the pinned gpt-6.1-sol
catalog (`tool_mode=code_mode_only`). With the supported code-mode wire, both stdio
and Unix return `code-mode host is disabled`; enabling the flag only in the blank
stdio fixture exposes the missing `/usr/local/bin/codex-code-mode-host` dependency.
Adding the official pinned ARM64 companion only to an untagged candidate image and
enabling the host only in blank fixtures yields1 stdio/2 Unix callbacks, exact session
authority and completed turns. No production gateway/config/image is enabled.

## Reproduction

All commands require the exact public native0.160.1 `models-manager/models.json`
(SHA fd219bd9f061278275f528939f82f54d2eb97df4b25c23b022adbe48813d920b).

```sh
# Two Unix clients, supported code-mode mock, existing host-disabled config.
sudo bash infra/hosting/m3/check_shared_events.sh /path/to/pinned/models.json --legacy-history --legacy-tools --code-mode-wire
# Single client on the existing adapter's stdio transport, same config/model.
sudo bash infra/hosting/m3/check_shared_events.sh /path/to/pinned/models.json --legacy-history --legacy-tools --stdio --code-mode-wire
# Only the fixture's host flag changes: required companion executable is absent.
sudo bash infra/hosting/m3/check_shared_events.sh /path/to/pinned/models.json --legacy-history --legacy-tools --stdio --code-mode-wire --enable-code-mode-host
# Original incorrect direct-function mock remains a negative comparison.
sudo bash infra/hosting/m3/check_shared_events.sh /path/to/pinned/models.json --legacy-history --legacy-tools --stdio
sudo bash infra/hosting/m3/check_stock_registration.sh
sudo bash infra/hosting/m3/check_stock_clients.sh
# Build/check the separately authorized candidate from official same-release asset.
sudo bash infra/hosting/m3/build_code_mode_fixture.sh /path/to/verified/asset-directory
sudo bash infra/hosting/m3/check_code_mode_candidate.sh /path/to/verified/asset-directory /path/to/pinned/models.json
```

Original-image tool acceptance commands retain **exit1**, following the successful
synthetic stage. Candidate checks now exit0 with actual stock calls, not a substitute
synthetic result. The registration probe's exit0 asserts binary pin/cleanup only; reported
registration observations are not all enforced as regression expectations.

Docker pins runner6fc, network-none/read-only/tmpfs, UID10001/cap-drop. Mounts contain
fixture code, backend source for legacy methods and public hash-pinned models only.
Fresh private HOME/custom Unix socket or owned stdio process; no original HOME,
credential/socket/daemon/process, cluster or real provider. External-token login
uses fabricated non-verifiable credentials/fictional account in ephemeral storage;
all reachable provider responses are loopback TLS mock. Model remains gpt-6.1-sol.

## Cause and observed comparison

| Case | Observation | Acceptance |
| --- | --- | --- |
| Historical Unix direct-function mock | tools=[]; no declared direct fixture function; callback0/failed2 | exit1; incorrect mock assumption |
| Single stdio direct-function mock | same absence/callback0/failed1; native metadata contains registered tool | exit1; not Unix-only |
| Correct code-mode mock, host disabled, stdio | exec advertised; own nested tool declared in prompt; custom result says host disabled; callback0 | exit1 |
| Correct code-mode mock, host disabled, Unix | each thread's own declaration, without the other's; both exec results say host disabled; metadata contains both registrations; callback0/failed2 | exit1 |
| Correct code-mode mock, stdio host enabled | exec advertised and own declaration; spawn fails because companion executable is absent; callback0 | exit1 |
| Same-release companion candidate, stdio | actual native tool request/callback/output1, completed turn1, precise session/request token | exit0 |
| Same-release companion candidate, Unix | concurrent original/web native tool requests/callbacks/outputs2, completed turns2, separate declarations and precise tokens | exit0 |

The mock now checks code-mode exec advertisement and the fixture tool declaration
in its advertised description before emitting the supported custom_tool_call with input
`text(await tools.<owned_fixture_tool>(...));`. Source tests use that wire. It never
invents an unadvertised direct function call to force acceptance. Tool result failure
causes explicit mock HTTP400; native cleanup exits0 and no auth.json persists.

The direct-function check inspected structured tool names; nested declarations in
exec's description were missed. Thus historical missingRegisteredTools counts are
**direct-wire observer failures**, not proof that native dropped registration. The
correct-wire observations distinguish direct names=[] from prompt/metadata presence.
Tool rows in state SQLite remain0 in these cases; session metadata does contain the
registered tools. Database observer limitations do not override that evidence.
Scanning all model input for names caused a diagnostic false positive when negative
probe code mentioned a foreign tool. The final parser restricts declaration checks
to advertised exec descriptions, excluding user/model history and deliberate probes.

Exact pinned source/cached public catalog support the causal chain:

- `models-manager/models.json`: gpt-6.1-sol has tool_mode=code_mode_only.
- `core/src/tools/mod.rs` requested_tool_mode: model metadata takes precedence over
  feature-derived defaults. Turning off code-mode feature defaults is not a model
  fallback to direct tool calls.
- `core/src/tools/spec_plan.rs` is_hidden_by_code_mode_only/register_code_mode_executors:
  nested-capable tools are hidden from direct model specs and declared through exec.
- `core/src/thread_manager.rs`: features.code_mode_host=false selects the disabled
  provider; `code-mode/src/remote_session.rs` returns the observed disabled error.
- `core/tests/suite/code_mode.rs`: dynamic tools can be invoked from exec code;
  `core/tests/common/responses.rs` gives the custom_tool_call SSE shape.
- Existing `backend/runner/app.py` also sets features.code_mode_host=false;
  `backend/runner/install_codex.py` installs only the pinned codex executable. The
  tested image lacks the companion at the path reported by native.

Executable pin was verified by the registration diagnostic: native0.160.1 SHA
fbbaec80443919f86dd63648a0b62759cf6f1d0e09310602fde96885e0bceb3e.
No model/catalog change, new version, native source build or runtime fork was used.
The host-enabled flag affects only a blank investigation process, not production.

### Companion provenance and permissions

The [official same-release asset](https://github.com/openai/codex/releases/download/rust-v0.160.1/codex-code-mode-host-aarch64-unknown-linux-musl.tar.gz)
is published in [rust-v0.160.1](https://github.com/openai/codex/releases/tag/rust-v0.160.1).
GitHub release API metadata reports size25999294 and archive SHA
e5e027e6689efda2e3570aa600179f0ebb18632803350e152ed6c9b97dcf9741, matching the
download. The installer accepts exactly one regular named member, verifies ELF64
little-endian AArch64 (machine183), and installs mode0755. Binary SHA is
fbccde22982e3e679678e203a9c18eee8342fb04b096063d05159f1f80df4fd8. This verifies
official-release/HTTPS/digest provenance; a Sigstore signature was not verified.

`Dockerfile.code-mode-fixture` inherits sealed runner6fc, verifies original native
hash and adds only that public companion. Build network=none, no tag/registry push;
original tag is checked before/after. Observed candidate image ID
afba3cfcef2810198dd22ba6278a3d462ea2a708f320cedb443651de63335230 (arm64,
USER10001:10001), manifest ca33f4821a672e452a581463af4206979e865d53bd71a3740f8f031ddc1d6d89,
config35142287899f56d9a3d629f5c31af57120188512f467aefadaae696f7f1d73b1.
Rebuild attestations may yield a different ID; record each new candidate separately.

Candidate final check invokes the helper as UID10001 in network-none/readonly/tmpfs
Docker and completes real native-generated item/tool/call -> existing Native.reply_tool
-> mocked platform storage -> custom-tool output -> model completion. The standard
threadId/turnId/callId/tool/namespace shape is observed. Each thread advertises only
its own synthetic tool; callback headers/body carry its exact session/request token.
Candidate SHA/mode are reported. Source API/runner and original images remain untouched.

Actual code-mode permission probes on all three fixture threads show shell exec_command,
foreign fixture tool, require, process and fetch unavailable. Docker continues to
deny external networking and rootfs writes; only fresh /tmp is writable, and code
mounts are read-only. This does not prove every file/network capability or deployment
Cilium enforcement. No real builtin command/network request was attempted by the probe.
Helper enables JavaScript evaluation and nested dispatch; production must preserve
the existing permitted tool set and denied capabilities, not assume startup grants
the whole tool namespace. Original file/cache access is not required or mounted.

[Official OpenAI CLI documentation](https://learn.chatgpt.com/docs/cli/reference)
also distinguishes app-server listener transport from local/remote Code Mode host.
Version-specific behavior here is established by the pinned source and executions.

Historical canonical/legacy registration, historyMode=legacy/ephemeral=false and
minimal-startup comparisons still failed with the old direct mock. They cannot
exclude configuration explanations involving required code-mode support. Minimal
startup also produced8 other mock POSTs of unidentified purpose. The original broad
refreshPosts counter was renamed nonResponsesPosts; it does not prove auth refresh.

## Ingress fixture and EVENT-R1

`check_owned_events.py` passes. Existing Native.reply_tool sends each synthetic
session/request token exactly once to its mocked API. Gate buffering waits for the
correlated turn/start response. Foreign thread/previous turn/old epoch/duplicate
call or server ID/completion-late/disconnected messages cause no callback/reply.
Unknown callback outcome is consumed before dispatch and never automatically replayed.
Accepted own completion uses Native.receive; foreign model/reroute/completion events
are rejected before downstream effects. Client RPC matching distinguishes a server
method+id request from a numeric-id response.

EVENT-R1 is closed independently at3254b1f: nested turn.id for started/updated/completed,
flat turnId for tool/reroute; competing shape/missing/empty/non-string IDs rejected
before buffering/closing/downstream. Normal own events pass. Reviewer's unchanged
conflicting.py returns accepted=false/gateClosed=false/previous-model-block=false.
This remains a synthetic parser fix, not observed malformed native traffic.

The previous stock-client foreign-event assertion minor is fixed. That authless
transport/privacy fixture passes, including raw peer foreign read/event counterexample.
Candidate's normal stock tool ownership/response now passes the bounded mock case.
Replaying its captured requests after completion/into an old epoch/foreign gate is
rejected; these deliberate replays are synthetic. Native-generated late events remain unproven.
Gate is sequential/investigation-only, without browser UID/RPC authorization, complete
event projection, durable concurrency or production exactly-once guarantees.

## Minimum correction and remaining conditions

The minimum package/config correction is now validated in the candidate fixture:
same pinned companion plus required code-mode host flag and correct mock wire.
Production still needs a scoped change to the runner installer/package and Native
configuration with these pins and permission checks. It has not been made or deployed.
The existing native-only installer and host-disabled config remain on original runner6fc.

The correction must preserve the existing permitted tool set and denial of shell,
other builtin capabilities and other-owner callbacks. A successful evaluator startup
would not by itself demonstrate that boundary, actual account entitlement or shared
refresh continuity. Do not develop a gateway to conceal a missing stock dependency.
If same-pin support cannot be supplied, report that limit; version/model changes or
forks remain outside this scope. Frozen access-only remains a separate bounded
proposal, not continuous #7 completion or an automatically selected fallback.

Reusable candidates are durable request reservations, expectedThreadId and private
mapping, session/request tool authority, correlated buffering, model/history checks.
Shared production needs UID/session RPC authorization, filtering before Native.receive,
connection/turn/call correlation and durable uncertainty, correctly routed server
responses, and close/interrupt limited to owned work. Child spawn/close, PersonalAuth
managed cache sync and native-home backup ownership cannot simply transfer to a shared
server. Actual original client participation/all writers/selected-account dispatch
and refresh/lifecycle continuity/private shared-thread recovery remain separate gates.
Generation numbering is not itself a product requirement; correct account execution
and preservation of existing authentication are. API70 nativeoff/runner Pod0 and
original sessions remain unchanged. Final PR/public-post hold is preserved.
