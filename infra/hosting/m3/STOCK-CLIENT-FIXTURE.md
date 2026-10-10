# #7 pinned stock two-client contract checkpoint

Authless synthetic stock0.160.1 Unix server, two private clients labelled original
and web, exact runner6fc. This is not a production gateway, original-client lifecycle
transition, authenticated authority or provider/model/tool execution.

`sudo bash infra/hosting/m3/check_stock_clients.sh` runs network-none/readonly/tmpfs,
UID10001/cap-drop/no-new-privileges, only readonly probe mounted. New private fixture
HOME/CODEX_HOME/workdirs/control socket; no original HOME/auth/source/socket. Clients
use standard masked WebSocket frames over AF_UNIX and version-specific JSON-RPC.
No external listener or host port. Only the owned test server is terminated afterward.

## Observed contract

| Check | Result | Limit |
|---|---|---|
| Two clients initialize one Unix stock server | passed | Not a connection to current original TUIs/daemon |
| Raw web client reads original thread by ID | **allowed**, private synthetic name exposed | Stock endpoint trusts connected client; no platform UID ownership boundary |
| Original thread events visible at raw web connection | **observed** | Tests thread/name/start metadata, not every event/server request |
| Web disconnect while original stays connected | original read still succeeds | No authenticated/in-flight original turn or tool |
| Web reconnect/resume same named/persisted thread | passed | Authless local metadata only, not native model resume acceptance |
| Original disconnect while web stays connected | server alive; web metadata RPC works | No renewable credential or refresh tested |
| Both disconnect | stock server stays alive | Authority persistence/renewal is not proved by PID survival |
| Two distinct dynamicTools registrations | requests accepted | Persistence observer did not establish distinct stored tool rows; actual invocation/routing untested |
| Owned server cleanup | SIGTERM/graceful exit0 | No original server shutdown |

An earlier untouched empty web thread returned -32600 `no rollout found` on resume.
This was preserved as a failed condition, not silently claimed successful. Naming
its synthetic thread through stock thread/name/set before disconnect materialized
metadata and made this case resumable. Cached source update_thread_metadata and
fresh-thread persistence behavior are consistent with this observation. It does not
make an empty thread a completed model conversation.

## Exact boundary needing an app gateway

Raw connection is not an acceptable web authorization interface. The observed
foreign thread/read and private-name/event exposure identify the first concrete
boundary: platform-authenticated owner/session→private server thread mapping and
RPC/event filtering **before forwarding to stock or returning data**. Block foreign
thread/read/resume/name/update/archive, arbitrary thread/list and auth/config/process
operations. Filter unowned thread events before model/error/tool handling; a foreign
reroute must not interrupt an original turn or block a web trial. Close only the web
connection/owned threads, never the shared authority/original client's connection.

No such gateway is implemented/approved by this fixture. Stock authenticated endpoint
access alone does not implement per-platform-UID authorization. Dynamic tools can
be supplied per thread according to source thread_start_inner, but registered request
acceptance is not evidence of correct callback owner/turn routing. Actual tool-response
correlation/late events and cross-owner negative tests remain next isolated gates;
this unit does not need a runtime fork to describe the boundary and does not assume one.

## Auth-generation support checked separately

Exact0.160.1 `app-server generate-json-schema --experimental` ran in network-none
Docker/fresh temporary HOME, no original source. Experimental TurnStartParams has
threadId/input/model/permissions/etc but no expectedAuthGeneration/owner generation/
platform UID admission parameter. Cached protocol/v2/turn.rs agrees with that surface.
This is a version-specific interface observation, not exhaustive absence of every
upstream/private integration possibility. No synthetic or actual auth switch was
made. Separate account/read then turn/start has no demonstrated atomic auth-owner
precondition here. Caller-added metadata/field is not treated as enforcement.

Therefore stock provides a usable persistent transport and local metadata reconnect;
UID/event/tool gateway policy and authenticated generation/dispatch binding remain
unresolved. Current embedded TUI0.160.1 participation, all writers and original normal
refresh/termination continuity remain actual topology conditions. The distinct observed
0.162.1 daemon is not adopted. Old comparisons/source/stage1/owner lookup reused,
Option2 guard/actual test not started. API70/nativeoff/runnerPod0, total3/two-turn,
finalPR/public posting hold unchanged.
