# Issue7 login UID / runner / conversation connection package

Source/mock checkpoint only. PM operates after independent source/package review,
new exact image/target artifacts and source-lifetime readiness. Existing HTTPS
API8fa/webedff/nativeoff and original DB/PVC/Secrets remain unchanged. One issue,
shared branch/PR; public posting remains held.

## Identity boundaries and reuse

| Identity | Authority / lifetime |
|---|---|
| Login user UID | Server-authenticated auth.accounts UUID/accountId; never browser-selected |
| Login session | Origin-bound cookie hash/expiry/CSRF/permissions; re-login may retain the same account UUID |
| Conversation | Owned research.conversations.id; existing server ownership and tool capability checks |
| Native thread | Existing private broker.json session→thread/start/resume; server-only confirmation |
| Runner stateId | Durable storage-generation UUID in private mapping and native marker; not provider authentication |
| Pod UID | Auxiliary current instance from Downward API metadata.uid; may change without changing user/state/thread |
| Unix UID / provider account | File permissions10001 / separately selected ChatGPT account-grant; neither is the login UUID |

Reuse existing owner-scoped DB queries, public browser API, full conversation/tool
history, model gpt-6.1-sol/native0.160.1, projection checks, reservations/lease and
total max3 ledger. No new DB/OAuth app, usage-analysis storage, browser-selected
runner/model/thread or multi-user provider grant is introduced.

## Narrow source correction

Previously API checked ownerId only after receiving the runner response. Same-owner
wrong storage could execute before response rejection. Real private mapping now
requires stateId alongside url/tokenFile; API sends X-Runner-State-Id on every call.
Runner validates bearer/header and owner/state marker before route/native effects,
then API validates ownerId/stateId on the response. Health A→submit B is refused
before B accept/tools/broker/ledger mutation even with the same owner/token.

Runner requires RUNNER_STATE_ID outside fixture APP_ENV=test. Marker validation
precedes auth/native startup and is repeated at requests/native admission/history/
persistence/tools. Missing/changed markers do not initialize/adopt a volume or
start another thread. Observed runtime failure blocks the instance and prevents
an asserted clean cache handoff. Marker copies are not cryptographic Pod attestation;
trusted images, actual PVC/CNI and operator ownership remain necessary.

API retains nativeThreadId in its existing private JSON cache and sends
expectedThreadId on follow-ups. Public summary/items do not expose this field.
Missing/wrong established session/thread refuses before ledger consumption;
resume response changing thread refuses before turn/start, consuming its already
reserved attempt. Legacy cached turns without a verified thread binding need
scoped reconciliation; no guessed thread/reset. Browser UID/state/thread overrides
are rejected. First use with an empty history still starts normally.

Private mapping shape (actual values outside Git/messages):

```json
{"<verified-account-uuid>": {"url": "http://<reviewed-runner-service>:8080", "tokenFile": "/run/runner/transport-token", "stateId": "<durable-state-uuid>"}}
```

RUNNER_POD_UID is optional metadata in code and must come from fieldRef(metadata.uid)
in actual K3s. PM compares it to actual Ready Pod/image/PVC; it is not a permanent
key or credential. No runner SA token/RBAC is added for UID discovery.

## Source review, then exact images/target

Current actual API8fa/runnerb19 do not implement the new protocol. Source/package
review precedes new API/runner Docker builds at the reviewed SHA, installed CLI pin,
sealed import/CRI/actual Pod policies and UID/RV enable/disable patches. These images
and exact K3s apply artifacts are not yet produced by this source checkpoint.
Webedff and prior HTTPS/DB/PV evidence are reusable; do not rebuild for equal labels,
edit old image-policy constants, inject code into live containers or repeat storage.

Use existing root HTTPS/hosting DB/role/Secrets. Candidate state/control are the
verified same-node codex-trial Retain pair; retain namespace/PVC/PV. Exact root API↔
runner selectors/Service/Cilium/callback, node/image/capacity and shutdown policy
need target review. The old prefix/new-DB renderer lacks this protocol's inputs and
is not a root enable package. PM does not convert it in the field.

Private inputs: trusted current account UUID and existing editor/admin tool authority,
one durable state UUID and transport token, exact images/PVC/node, reviewed provider
routing/DNS, supported private-use and source/refresh lifetime evidence. One Recreate
runner/no surge/one native process, tokenless/non-root10001/read-only/cap-drop,
separate native/control, no public runner or host HOME/socket, exact internal callback.
Allow enough graceful shutdown time; unclean exit stays refused, never forced clean.

First-use initialization is an explicit reviewed network-isolated loader operation
on the selected **empty** native private root. Input JSON has exactly ownerId/stateId
in private0700/0600 storage. Container command:

```sh
python -m runner.binding --root /native/private --inputs /private/binding.json --initialize
```

Only account-id/state-binding.json are written. Populated/partial/previously bound
roots, symlinks and wrong Unix ownership refuse. Subsequent validation omits
--initialize and never rewrites. Retain markers/broker/projection/native/control/
ledger across replacement; no source HOME chmod/chown or native-only budget reset.
API mapping/marker/RUNNER_STATE_ID must agree; existing HTTPS/Secure/exact-origin/
root-opt-in/trusted-proxy/sole-owner gates remain. Native startup requires source
readiness first because it can authenticate/refresh even before a turn.

## Selected-source readonly diagnosis

Owner/PM selects the already existing logical server profile/file; no HOME/keyring
discovery, new login, original token output/copy/refresh/logout/session stop.
Backend/PM resolve opaque internals privately, not the user. `ops.source_metadata`
reads only that regular/private/single-link file, O_RDONLY/O_NOFOLLOW, bounded1MB,
expected Unix UID and before/after file stability. No path/token/hash/JWT/identity/
timestamp values are returned. PM also verifies canonical host path/parent and
host inode/mtime/ctime/size before/after; file bind mount can retain a stale inode.
Do not treat a stable read as absence of external writers.

In network-none/read-only Docker with only exact file readonly mounted, matching
reader Unix UID and no original HOME, use the container command:

```sh
python -m ops.source_metadata --source /source/auth.json
```

Format-only needs no user-supplied provider ID. Optional --binding compares private
expected-profile JSON providerAccountId/sourceUid. This is declared-field consistency,
not authentication. Absence prints bindingProvided=false/providerBindingMatches=false.
Exit0 means metadata read, **not admission**: authenticated=false, sourceAuthoritative=
not_established, refreshOwnership=owner_evidence_required, modelAccess=not_tested.
Invalid format/binding/permissions/links/unstable read fails without values. Keyring
extraction or compatibility changes require source review, never guessed conversion.

Owner-only minimum remains the selected existing source's authority/grant independence,
all same-grant consumers/writers during and after testing, and authoritative latest
cache owner/retention/original-host continuity. Actual selected workspace routing
and private-use support are separate operating facts. Neither UID, file metadata,
lease nor a true declaration proves these. Native syncs only private control,
not the original host. Same-grant original clients needing latest tokens require
owning-client coordination review before supply/startup. No API-key/model/UI fallback.

## Minimal actual test: two turns, total ceiling3

After exact reviews/readiness, PM starts only the bound owner runner. Verify actual
Pod UID/image/PVC and private owner/state/model health plus public status. Health/
metadata/config is not entitlement. Original HTTPS/auth/data baseline is preserved.

1. Same authenticated owner creates one conversation, explicitly saves/reads small
   synthetic material/document through tools. Verify completed fixed model, owned
   DB effects, full IO, private session↔thread and durable ledger increment.
2. Same account reconnects/relogs in and reads that conversation. Exact old-key
   replay causes no dispatch/ledger increment. Close admission/quiesce and cleanly
   replace only owned Pod: old process gone, nativeActive=false, latest control
   retained, same state/PVC/owner and new Pod UID. Validate without init, then one
   explicit same-conversation follow-up: same native thread/history/model, second
   ledger increment. Wrong/missing state/thread or unclean exit stops progression.

Slot3 is only an explicitly selected additional check, never automatic retry.
Failures/unknown acceptance/failed resume consume reserved attempts. Preserve same
trial/grant/ledger and original expiry≤1h; no fresh UUID/cache/control restore to
refill. Other-user requests must deny without dispatch; fixture authorization is
not permission to bind this grant to another UID. Model/auth/reroute errors stop.

Stop admission→quiesce→clean owned runner stop→latest-cache continuity verification→
local cleanup/tombstones. Unknown state preserves/stops for review. Recovery disables
map/owner/ordinary+root enable before restoring reviewed native-disabled API/config,
terminates only owned runner, retains data/native/control/ledger/original HTTPS/TLS.
No DB/schema downgrade, original-cache rewrite or automatic new thread.

## Author checks

`sudo bash backend/tests/check-user-uid.sh`: current readonly source in cached Docker
test image, isolated network-none PostgreSQL, fixture adapter subprocesses only.
**65 passed**: A-health/B-submit zero accept/tools/broker/ledger changes, same-thread
restart, startup/runtime marker refusal, private bookmark/forgery, resume-response
thread mismatch, metadata value suppression and existing owner/model/max3 cases.
No vendor native binary/provider/actual Pod/auth proof. Owned PG removed by EXIT.
Existing TestClient HTTPX deprecation warning is nonblocking; dependencies unchanged.

Compose render/structure: **35passed**,20 missing/blank rejections including state UUID,
no services started. Bootstrap needs no owner/runner/state. Legacy Compose is interface
regression coverage, not a new-project/DB request or current K3s apply artifact.
