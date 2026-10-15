# Issue10 accepted rehearsal boundary and evidence — 2026-10-15

This document supersedes earlier undecided-scope statements in the design notes.
Authority: [initial user decisions recorded by PM](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/10#issuecomment-6182177878),
followed by the user decision to defer VAP enforcement to Issue11. The
[PM execution record](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/10#issuecomment-6186364394)
and [reviewer acceptance of the observations](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/10#issuecomment-6186405295)
record the completed rehearsal. Overall approval of the final branch SHA remains
separate from acceptance of these execution results.

## Accepted scope and observed outcome

One synthetic owner's runner Pod was created once in codex-runner-rehearsal and
retained. Host-network diagnostic transport to TCP8080 was authenticated using a
synthetic Secret. Ordinary CODEX_USER_RUNNERS_ENABLE=false and controller
max_pods=0 remain unchanged. No provider/model request or production multi-user
service is claimed. codex-trial and its resources were not reused.

The user-defined isolation requirements are no runner-to-runner communication
and no runner kubectl/API operations. The configuration evidence is default-deny,
host-only ingress TCP8080, runner SA automount=false and no runner RBAC grant.
The observed datapath cases are host→8080 allow and host→8081 deny. Nonhost→8080
and runner→API traffic remain explicitly **unverified**, deferred to Issue11;
configuration evidence and the host probes do not prove those two cases.

VAP/CEL envelope enforcement was excluded from Issue10 after the live Quantity
schema/typeChecking mismatch. The two installed VAP objects remain, but their
bindings were removed. Three CNPs remain. VAP is not an active isolation control
or a passing validation claim in this completion record.

## Execution evidence ledger

Artifact paths below are relative to the shared `agent-worktree-comm/` root;
credentials are not included. PM performed the live operations, and reviewer
independently checked the retained artifacts and closeout state.

| Stage | Observed result and evidence |
|---|---|
| Identity | Namespace UID `f5b2e33f-5166-4163-a60a-5421b8ced42e`; owner `d888737c-4830-4761-9630-b400982f8618`, state `a9617ace-fa4b-4bbc-b70b-2188f0efe3e6`, generation1. Binding in reentry `evidence/result.json`; namespace/reference observations in `evidence/preflight-01.json`. |
| Runner image | Source `4b1b72e598c18aaa368e185ddd1b516b05f5a381`; linux/arm64; `docker.io/library/anxious-s13-runner-rehearsal@sha256:bbec1f6b5933918d79ffa966fad3c0727071ca77acddb198af937887780a7ad1`. The reentry diagnostic image/source284ecc5 is separate; no runner image replacement. |
| Storage | Two dedicated static local Retain PVs on anxious-lookout, native/control PVCs Bound, fixed owner/state markers initialized and verified. Dedicated `/var/lib/anxious-lookout/runner-rehearsal/a9617ace-fa4b-4bbc-b70b-2188f0efe3e6/{native,control}` paths and all PV/PVC identities retained. See [fixed inputs](user-runner-isolated/REHEARSAL-INPUTS.md) and reentry preflight. |
| Secret | Immutable Opaque transport-synthetic UID `41b9ad3d-a0be-47eb-8812-91aee26efdc8`; private token matched the named Secret. No provider credential or token bytes in this ledger. |
| Creation | One persisted POST produced `runner-d888737c483047619630b400982f8618`, UID `835babaa-6f88-4415-af20-d5908bd87b78`, Running on anxious-lookout, IP10.42.0.6. First-run dry-run/create/GET evidence and durable permit/UID/DB receipt were preserved. |
| Recovery | Diagnostic source `284ecc563e43f363f242aebab2165c14b0f4745c`, local image ID `sha256:c2188901e4c2ba85e7063ed4b47fdc6e8c73e7cf2fa041fa27b8bb608a2ab333`. Same Pod/DB/permit, GET-only reentry, no new POST; result `freshPermit=false`, `transport-only-success`. |
| HTTP | On 2026-10-15 06:53:43Z: correct bearer/state200, wrong bearer401, wrong state503, missing state503; each attempts1. Owner/state/Pod UID match; available=false, verification=unverified, reason=not_configured. Reentry `evidence/health.json`. |
| Cilium | Endpoint423, identity59836, desired/realized policy revision21 before/after. Original `cilium-window-reentry/reentry-policy-verdict.raw` contains host→10.42.0.6:8080 allow4 SYNs and host→8081 deny5 SYNs. The five deny events are retransmissions of one source tuple, not five independent probes. Raw rows lack individual wall-clock timestamps; correlation uses capture window, IP/port and order. |
| Restoration | Quota spec/status hard0, used1, RV1540949 at closeout. Both initial and reentry temporary controller bindings withdrawn; remaining RoleBindings0 and matching ClusterRoleBindings0; controller list/create pods=no. VAP bindings absent; two VAP objects and three CNPs retained. Reentry `10-reentry.log` and reviewer live-state/permission evidence. |
| Retention | Pod, both PVs/PVCs and backing data, transport Secret, isolated DB, create permit and bound-UID marker retained. Registry remains runner_unreachable/ambiguous=true, generation1/revision2 with the same Pod UID; settled ensure receipt is retained. Health does not change this conservative lifecycle state. |
| Unverified/deferred | Nonhost→8080 and runner→API datapath probes; VAP envelope/typeChecking repair; provider/native readiness, termination/drain, safe release/recreation and repeated capacity behavior. No inference of these from quota0 or HTTP success. |

Primary evidence directories:

- `artifacts/pm/issue10-oneshot-b8a76eb/`: first creation, preserved uncertainty,
  initial permission/quota restoration and DB inventory.
- `artifacts/pm/issue10-reentry-284ecc5/`: successful tool health/probe evidence,
  original Cilium raw/endpoint snapshots, SHA256 inventories, read-only grants and
  withdrawal. `host-other-port.json` deliberately says policyVerdict=unverified;
  the reviewer-correlated raw deny is the separate policy evidence.
- `artifacts/review/2026-10-15-issue10-results/` and
  `artifacts/review/2026-10-15-issue10-reentry-results/`: independent raw REST,
  UID/spec, digest, verdict and live closeout checks. Earlier source review
  evidence is under `artifacts/review/2026-10-15-issue10-*`.

The first execution's health refusal remains an unconfirmed listener-start race
hypothesis. The subsequent raw-PodList reentry defect was fixed at284ecc5, and the
same retained Pod then passed the tool's full HTTP checks. This distinction is
preserved; no recreate or provider injection was used to obtain success.

## Deferred work — do not start before Issue10 PR

[Issue11](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/11) owns VAP
schema/typeChecking repair, the two unverified datapath probes, native
termination/drain/late-create barrier, safe slot release/recreation, repeated idle
reclaim, measured capacity and operator ChatGPT auth refresh/provider egress.
The stopped VAP WIP and served schema are handoff artifacts, not completed work.
No further Issue11 implementation is authorized by this evidence update.

Quota0 does not delete the retained Pod, prove termination/drain, or release the
capacity reservation. No automatic deletion/recreation is enabled;
releaseToolAvailable remains false. The final branch review and PM-created PR
remain the closing workflow; merge belongs to the human.
