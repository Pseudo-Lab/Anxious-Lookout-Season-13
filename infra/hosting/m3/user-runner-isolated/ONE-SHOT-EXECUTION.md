# Issue10 one-shot execution and evidence contract

Execution record updated after PM's 2026-10-15 rehearsal and reviewer acceptance.
The observed results and later scope decisions below supersede the original VAP
setup plan in this document; that plan is retained as historical procedure and
must not be replayed. Further execution requires a separately reviewed scope and
exact artifacts. Keep operating feature=false,
max_pods=0. The dedicated tool alone uses isolated capacity1. No provider, extra
Pod, exec/attach into runner, deletion, release, reset or replacement is included.

## Fixed provenance and credentials

Runner image source is 4b1b72e, already initialized/imported by PM. Plan.image is
`docker.io/library/anxious-s13-runner-rehearsal@sha256:bbec1f6b5933918d79ffa966fad3c0727071ca77acddb198af937887780a7ad1`.
Do not rebuild or replace that runner image for this tool. Build a **separate**
exact-source runtime target diagnostic image containing ops.runner_one_shot and
ops.runner_host_probe, record its source SHA/local image ID, and use that local
image ID with run-one-shot.sh. No native binary is needed. Its host network
placement is the approved host-trusted boundary, not an individual process identity.

Use three distinct absolute, nonsymlink directories owned by the trusted PM UID,
mode0700: credentials (read-only mount), durable permit (read/write), evidence
(read/write). Evidence must start empty for each run. Never reset/remove/replace
permit directory, DB or Pod to gain another attempt; preserve them through failure.
Bootstrap, run and host probe need only the credentials for that action; use
separate credential directories to avoid mounting admin credentials in the run.
All secret files mode0600, same UID, single-link regular files; no values in args,
env, logs, public artifacts or shell tracing. The tool refuses symlinks/FIFOs and
URL query overrides. Protect same-UID processes and root as trusted operators.
CA file is public certificate material but lives with the private mounts.

Fixed API: https://10.0.0.107:6443, matching IP SAN in CA-verified certificate,
explicit CA /private/ca.crt. Host-network /health targets only the fresh GET's
IPv4 PodIP in 10.42.0.0/16 with exact UID/spec/nodeName anxious-lookout. PM must
confirm this cluster PodCIDR before execution; drift stops for review, not a CLI
override. Every HTTP operation uses the existing socket deadline transport,
no proxy/redirect/retry, 10s aggregate/2s socket budget and max2MiB response.

## Disposable DB bootstrap

PM starts a dedicated PostgreSQL18.6 container with a fresh private volume, no
operating app mounts or DB network, publishing only 127.0.0.1:25410. Use a private
POSTGRES_PASSWORD_FILE mount, database runner_rehearsal_a9617ace, and record the
container/image/volume identity. Existing port/container/volume collisions stop.
`start-one-shot-db.sh` provides that invocation for a reviewed local PostgreSQL
18.6 image ID, an empty retained data directory0700 and a private password file0600
(same nonroot PM UID, private parent0700). It uses that UID inside PostgreSQL too.
Record the official image digest/version before running; do not pass the runner
or diagnostic image ID. Its container name is anxious-s13-pm-issue10-db and it
joins only Docker's default bridge, never the operating app's networks.

```sh
bash infra/hosting/m3/user-runner-isolated/start-one-shot-db.sh \
  "$postgres_image_id" "$disposable_db_data" "$postgres_password_file"
docker exec anxious-s13-pm-issue10-db pg_isready -U postgres -d runner_rehearsal_a9617ace
```

Do not use any operating PostgreSQL instance. Do not delete the DB after the test;
its dispatch receipts are evidence. Validate process readiness before bootstrap.

Prepare bootstrap credentials files admin-dsn and api-password. DSN must be
postgresql+psycopg, user postgres, literal127.0.0.1, port25410, fixed DB above,
password>=16, no query. Generate a separate strong API role password in the file.
The tool verifies an empty DB and no existing anxious_api role, durably consumes
its database-bootstrap marker, then uses existing auth/runtime migrations. Partial
bootstrap cannot be rerun/adopted. It inserts one approved synthetic account and
fixed state, enters generation1 using Registry.enter, and stores a read-only
binding marker outside runner_runtime. No state override in the operating service.

```sh
bash infra/hosting/m3/user-runner-isolated/run-one-shot.sh bootstrap-db \
  "$diagnostic_image_id" "$bootstrap_credentials" "$permit_dir" "$bootstrap_evidence"
```

For run, mount only database-dsn (same endpoint/db, role anxious_api with matching
password), sa-token, transport-token and ca.crt. Use an expiring runner-controller
SA token created by PM, never admin Kubernetes credentials. The named immutable
Secret's token must equal the private transport-token. Do not copy Secret JSON to
public evidence. The code only records reference UIDs, never Secret data or DSNs.

## Exact render, digest review and bounded window

Historical initial plan: the user later excluded VAP from Issue10. PM removed
both VAP bindings before the single persisted creation. See the observed results
below; do not reapply these VAP steps or reopen quota1 for existing-Pod recovery.

1. Record namespace/PVC/PV/Secret UIDs, initialized storage evidence, imported
   image, no Pod, current quota RV and all matching KNP/CNP/CCNP/VAP/bindings.
   Verify no inherited broad allow, namespace has no tenant label. Review effective
   RBAC union for runner-controller; 30-one-shot-rbac.json adds only named GET,
   Pod list/create and exact namespace/PV GET. RBAC cannot constrain create by
   name: final CEL must do so. No delete/update/exec/quota/policy permissions.
2. Render using ops.runner_policy, pinned inventory/template and these exact
   values, replacing quotaResourceVersion with the fresh observed value:

   ```json
   {"image":"docker.io/library/anxious-s13-runner-rehearsal@sha256:bbec1f6b5933918d79ffa966fad3c0727071ca77acddb198af937887780a7ad1","callback":"https://rehearsal-callback.invalid:443/research/tools","callbackNamespace":"codex-runner-callback-disabled","callbackServiceAccount":"callback-disabled","callbackPort":"443","quotaResourceVersion":"OBSERVE_NOW"}
   ```

   Existing renderer rejects the placeholder; it must be a positive integer.
   Callback remains synthetic/nonexistent; no callback request is made.
3. First phase: review all exact file SHA256s, diff against observed objects,
   schema/type-check results, RBAC and rollback artifacts. Explicitly inventory
   every old unconditional false policy/binding that also matches the namespace.
   No blanket delete or force apply: if an old object has a different name, its
   UID/RV and exact replacement/removal must be separately recorded and reviewed.
   If same name, reviewed replacement must preserve concurrency preconditions.
4. Second phase (PM): rehash exact reviewed files, reobserve UID/RV, install exact
   CNPs then VAP/bindings including permanent subresource denial. Verify type
   checking has no errors, selectors/bindings active and every matching gate's
   disposition accounted for. Use reviewed create for absent objects or
   resourceVersion-conditional replace for existing ones. Drift stops. Review
   matching Cilium policy union and realized endpoint policy before probes.
5. Conditional replace quota with quota-1.json, wait for observed spec/status hard
   count/pods1 with used0. Start the bounded Cilium observer described below.
   Run the tool once. It consumes a fsynced exclusive-create permit before any
   cluster request; a crash before POST still consumes it. It checks fixed
   namespace/reference UIDs and inventory, reserves the Controller's durable DB
   receipt, server-dry-runs the exact Plan under the same SA, rejects warnings or
   non-whitelisted spec defaults, then sends at most one persisted POST. It records
   returned UID durably and validates GET UID/spec/references and /health.

   ```sh
   bash infra/hosting/m3/user-runner-isolated/run-one-shot.sh run \
     "$diagnostic_image_id" "$run_credentials" "$permit_dir" "$run_evidence"
   ```

   No manual kubectl create substitutes for this path. A failed/ambiguous attempt
   never retries POST. Reinvocation with the **same permit/DB** and a fresh evidence
   directory only looks up/reconciles the reserved identity and probes health;
   missing Pod, lost receipt, mismatched UID or partial marker refuses. Never
   restore a previous DB or substitute a fresh permit. Keep old evidence.
6. Run host-other-port probe in the same observation window, using the successful
   run evidence directory. Its /private mount may be an empty private directory.

   ```sh
   bash infra/hosting/m3/user-runner-isolated/run-one-shot.sh host-probe \
     "$diagnostic_image_id" "$empty_credentials" "$permit_dir" "$run_evidence"
   ```

7. On success **or any failure**, PM re-reads quota RV, re-renders in a new output
   directory, verifies only the intended quota0 artifact/digest and conditionally
   replaces. Never use the stale quota-0.json from step2. Confirm spec/status hard0
   and record used (a retained Pod may keep used1). Retain Pod/PV/PVC/Secret/DB and
   permit; quota0 is not deletion/drain proof. End the test window and revoke the
   temporary caller grant via reviewed exact RBAC binding removal/expiry. No
   automatic cleanup rollback or permission renewal on uncertainty.

## Cilium probes and evidence

PM confirmed no reviewed nonhost source and no permitted runner exec. Therefore
nonhost→runner8080 and runner→API are explicitly **unverified**, deferred to #11
per messages/back/2026-10-15-pm-probe-source-inventory-answer.md. Do not touch
codex-trial or other operating Pods. No simulated verdict replaces these cases.
Hubble is disabled; do not enable it for this test.

For host8080 and host8081, PM uses the existing Cilium agent only as a read-only
observer, not a new traffic source. Before running, record kube-system/cilium-m4n55
Pod UID/node/container/image, validate cilium-dbg help in that exact image, and
review these commands against installed capabilities. A renamed/replaced agent
requires fresh identity evidence. Agent exec is distinct from forbidden runner
exec. No mutation of agent configuration or policy is included.

```sh
kubectl -n kube-system exec cilium-m4n55 -- cilium-dbg endpoint list -o json
kubectl -n kube-system exec cilium-m4n55 -- cilium-dbg endpoint get "$runner_endpoint_id"
# Separate bounded foreground capture; not an inbox watcher or permanent monitor.
timeout 90s kubectl -n kube-system exec cilium-m4n55 -- \
  cilium-dbg monitor --type policy-verdict -v
```

Capture endpoint get before/after each window, including runner Pod labels/IP,
security identity, desired/realized policy revision and enforcement. Correlate
monitor timestamp, src/dst identity/IP, TCP port and allow/drop verdict with
health.json/host-other-port.json. Record raw files and SHA256s. The observer may
need a separately reviewed invocation adjustment if installed help differs;
never claim an absent event as success. Policy trace is optional simulation only
and not required by this package. No Cilium changes or new probe source required.

The host-other-port tool sends one bounded TCP8081 connect. connected is a failure
of expected isolation; timeout/refusal is only socket evidence. It always emits
policyVerdict=unverified until reviewer correlates an actual policy drop. For
host8080, authenticated HTTP200 proves application transport but requires matching
Cilium identity/verdict/revision to claim the network-policy observation. Do not
infer either verdict from allow-localhost=policy alone.

## Evidence contract and completion

Record wrapper/tool SHA and diagnostic image separately from runner source/image.
Retain private evidence directories0700/files0600 and hash an inventory containing:

- Exact rendered inputs/index and observed-before/after policy/RBAC/quota objects
  with UID/RV; timestamps; namespace/reference UID checks and initialization/import.
- database-bootstrap.json, create-permit.json and pod-uid.json retained in permit;
  private DB inventory showing one owner/state/generation and settled ensure receipt.
  No database password/DSN or raw Secret/SA credential in exports.
- server-dry-run.json, create-response.json (only on first successful create),
  get-pod.json, health.json and result.json. These contain Pod manifests/identity,
  not credentials. Health statuses200/401/503/503 and identity/unavailable/unverified
  are recorded; native readiness/provider/supply/turn are not claimed.
- Host probe timestamps/outcome, endpoint identity/policy revisions and raw verdict
  capture, plus reviewer correlation. Record nonhost8080 and runnerAPI as
  unverified: no approved source, Issue11. Any missing host verdict remains
  unverified and must be reported, never converted to deny.
- Fresh quota0 restore digest/response/status and end-window time, retained
  resources, errors/partial outcomes. Tool failure emits a fixed redacted error;
  capture exit status without shell tracing credential preparation.

result.json transport-only-success is not overall Issue10 approval. PM/reviewer
combine the above artifacts, explicitly carry the two agreed deferred probes,
and assess any other missing live evidence. No #11 lifecycle/release assertion.

Command references: [Cilium monitor](https://docs.cilium.io/en/latest/cmdref/cilium-dbg_monitor/)
and [policy troubleshooting](https://docs.cilium.io/en/stable/security/policy/troubleshooting/).
Installed-image help and live endpoint evidence take precedence over newer docs;
these references support the read-only command family, not this cluster's results.

## Local validation

Docker test image 54b5608ca7caec4a427ae497c00ef2afd9faa38c9ed5e6c845ec35bf7a50805d:
60 tests passed including the existing 24 host-wrapper tests and real disposable
PostgreSQL integration. The DB ran network-none with no published port, nonroot
caller UID, read-only root, dropped capabilities, private password file and a
fresh retained /tmp bind directory; no operating DB was contacted. Tests cover
exclusive permit concurrency, unsafe private files, DSN query overrides, spec
and UID drift, dry-run failure/default mismatch/lost POST response, retained DB
receipt, lookup-only rerun and refusal after simulated lost DB receipt, four
health status cases and no inference of policy denial from socket timeout.
After adding preflight evidence snapshots, the one-shot suite reran network-none:
35 passed, 1 dedicated-DB test skipped (DB integration already passed above).
Shell syntax checks and RBAC JSON parsing passed. These local checks alone did
not establish live Kubernetes or Cilium behavior; subsequent PM results follow.

## Existing-Pod recovery after the first observation (2026-10-15)

The user subsequently deferred VAP enforcement to #11; PM removed its two
bindings and retained the policy objects. Do not execute the earlier VAP/quota1
setup again for recovery. Keep quota0, existing Pod, runner image, DB and permit.
Only PM's separately reviewed temporary RBAC/token restoration is needed, followed
by lookup/health/probe and binding withdrawal. No bootstrap, new permit, POST or
Pod replacement. Build the updated **diagnostic** image from the reviewed fix SHA;
the existing runner image/source remains unchanged.

The reentry fix recognizes missing TypeMeta only within a verified v1 PodList.
Explicit foreign kind/version still fails; direct GET/POST must still carry Pod
TypeMeta. UID/spec/reference checks remain unchanged. Regression exercises real
retained PostgreSQL + existing permit + actual Port/preflight/lookup/health with
wire-shaped list items lacking TypeMeta, and checks all API calls are GET.

Initial authenticated GET /health now permits at most10 connection attempts in
an aggregate10s window, with up to1s spacing and each request constrained by the
remaining budget. Only ConnectError/ConnectTimeout are retried. HTTP status,
response parsing, read timeout and identity mismatch fail immediately. Other auth
cases are single GETs, and evidence records attempts per case. This addresses a
possible listener-start race; it does not prove the first refused run's exception.

Use `31-one-shot-reentry-rbac.json` for this recovery window: new Role/RoleBinding
runner-one-shot-reentry and ClusterRole/ClusterRoleBinding
runner-rehearsal-reference-read-reentry grant only namespaced Pod list/named GET,
named PVC/Secret GET and exact namespace/PV GET. They grant no create or mutation.
The original create-grant binding must stay absent. PM checks the effective SA
RBAC union, verifies the new names are absent before creating reviewed objects,
and records UIDs/digests. If any exists, stop and review it rather than overwrite.
Remove the two reentry bindings after observation (record their UIDs); keep quota0
and do not restore the original create-grant binding. The unchanged runner SA
still has no API grant; all these read permissions belong to runner-controller.

## Observed execution and closeout — 2026-10-15

PM completed the original create and the corrected reentry; reviewer accepted the
[reentry results and host observations](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/10#issuecomment-6186405295).
The [PM result record](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/10#issuecomment-6186364394)
and [scope/evidence ledger](../USER-RUNNER-ISSUE10-SCOPE.md) carry the scope limits.
This is execution evidence, not approval of the final branch SHA or PR.

| Observation | Result |
|---|---|
| Retained Pod | `runner-d888737c483047619630b400982f8618`, UID `835babaa-6f88-4415-af20-d5908bd87b78`, Running, node anxious-lookout, IP10.42.0.6. One persisted create; no replacement. |
| Runner image | Source4b1b72e; `docker.io/library/anxious-s13-runner-rehearsal@sha256:bbec1f6b5933918d79ffa966fad3c0727071ca77acddb198af937887780a7ad1`. |
| Reentry diagnostic | Source284ecc5; local image ID `sha256:c2188901e4c2ba85e7063ed4b47fdc6e8c73e7cf2fa041fa27b8bb608a2ab333`, linux/arm64, image user10001:10001. Existing DB/permit/UID retained; all API requests GET, freshPermit=false, transport-only-success. |
| HTTP06:53:43Z | Correct bearer/state200; wrong bearer401; wrong state503; missing state503. Each attempts1. Exact owner/state/Pod UID; available=false, verification=unverified, reason=not_configured. |
| Cilium window06:53–06:55Z | Endpoint423, security identity59836, desired/realized revision21 unchanged before/after. Original raw: remote ID host→8080 allow4 SYNs (L3-L4), host→8081 deny5 SYNs (match none). Five deny events share the same source tuple and are retransmissions of one connection attempt. |
| Quota/permissions | At06:55:10Z quota RV1540949, spec/status hard0, used1. Reentry RoleBinding UIDe876ed1c-26ce-4df0-a1b6-d9adfedad552 and ClusterRoleBinding UID0991a2de-c743-4a05-9290-e7250c26d257 withdrawn. Initial bindings also absent; remaining matching rb/crb0; controller list/create pods=no. |
| VAP scope | User deferred enforcement/typeChecking repair to #11. Both VAP objects and three CNPs retained; VAP bindings absent. No warning-free VAP or active envelope enforcement claim. |
| Retained evidence/resources | Pod, two Retain PVs/PVCs/backing directories, immutable transport Secret, private isolated DB and durable permit/UID markers preserved. Registry runner_unreachable/ambiguous=true/gen1/rev2 and settled ensure receipt unchanged; health does not mark native readiness or release capacity. |

Raw policy rows have no individual wall-clock timestamp. Reviewer correlated the
bounded capture window, endpoint identity/revision, IP/port and event order with
health/probe timestamps. The probe JSON correctly remains
`socketOutcome=not-connected, policyVerdict=unverified`; the separate original raw
deny plus reviewer correlation establishes the host8081 policy observation. No
socket timeout alone was counted as a denial. Nonhost→8080 and runner→API remain
unverified/#11 because no approved traffic source existed. Host probes do not
substitute for runner-to-runner or runner-to-API datapath tests.

Shared evidence paths (relative to agent-worktree-comm):

- `artifacts/pm/issue10-oneshot-b8a76eb/`: first dry-run/create/GET, DB inventory,
  initial health uncertainty and restoration. The initial health refusal's exact
  exception is not established; a listener-start race remains a hypothesis.
- `artifacts/pm/issue10-reentry-284ecc5/evidence/`: preflight-01.json, get-pod.json,
  health.json, result.json and host-other-port.json; same Pod/permit/DB recovery.
- `artifacts/pm/issue10-reentry-284ecc5/cilium-window-reentry/`: original
  reentry-policy-verdict.raw and endpoint before/after evidence. Hash inventories
  are sha256.txt and cilium-window-reentry.sha256 in the parent directory.
- `artifacts/pm/issue10-reentry-284ecc5/10-reentry.log` and `applied/`: granted
  read-only RBAC identities, can-i evidence, binding withdrawal and quota closeout.
- `artifacts/review/2026-10-15-issue10-results/` and
  `artifacts/review/2026-10-15-issue10-reentry-results/`: independent raw REST,
  digest/UID/spec, verdict and live restriction checks.

Reviewer reported no remaining minor/question backlog at the documentation
checkpoint. Code remains284ecc5; this ledger update introduces no runtime or
cluster mutation. Final branch approval and PM PR creation are subsequent steps.
