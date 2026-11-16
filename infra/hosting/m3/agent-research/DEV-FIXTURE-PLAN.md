# #97 r2 model-free development fixture — source review candidate

Code/local DB approval is limited to `cb0159b`; this plan adds no execution
permission. PM requested source preparation in
`messages/back/2026-11-17-pm-issue97-next-dev-fixture.md`. Review approves this
source/plan first; PM then approves the development window and review executes.
Actual research grant, model turn and production work remain zero. Do not send
`turn/start`, seed a fake active grant, call a provider or enable research flags.

## Scope and exact differences

| Temporary item | Exact change | Restoration |
| --- | --- | --- |
| Measurement Pod | `codex-runner/issue97-r2-dev`, generated from `ops.native_measure.measurement_pod` with the reviewed runner manifest digest; same UID10001, resources, capdrop, no privilege escalation, read-only root, seccomp and host namespace settings. Fresh emptyDirs replace PVCs; sleep command; no environment, Secret, projected token or Service. Deadline900s. | UID-precondition DELETE; confirm Pod/process/emptyDirs absent. Existing runner Pods/ledgers untouched. |
| Measurement labels | purpose=`issue97-r2-dev`, managed=`user-runner`; no owner/state/generation or registered runner name. | Pod deletion. This explicit **dev-only** label is needed for existing broker/API ingress selectors. It does not establish a registry identity or broker authority. Review controller behavior before creation; if an envelope/VAP refuses it, stop rather than relaxing policy. |
| Pod name resolution | Only this Pod's hostAliases map `research97.dev.invalid` and negative-control `wrong97.dev.invalid` to198.18.97.2. | Pod deletion; no node/host DNS or `/etc/hosts` change. |
| Synthetic world path | Guest-only Docker `issue97-r2-relay`, private network namespace, veth `i97r2host`198.18.97.1/30 ↔ `i97r2peer`198.18.97.2/30, peer default route via198.18.97.1. L4 bytes → existing guest Traefik10.0.0.107:443 only. | Stop/remove exact container ID; delete exact saved veth ifindex/iflink; verify connected route absent. No host OS/development-server route or firewall change. |
| TLS | New `m2-hosting/issue97-r2-dev` Secret, Middleware and IngressRoute. One-hour CA/leaf, exact synthetic DNS SAN, websecure443, API8080 backend, `/api/internal` excluded, encoded reserved characters refused. | Delete each exact UID; verify original HTTP/other routes and TLS configuration unchanged. No default TLS store/controller mutation. |
| API origin | UID/RV-guarded JSON Patch of only the api container's direct AUTH_ORIGIN entry to `https://research97.dev.invalid`. Image/flags/envFrom/other env/spec preserved. Existing HTTP route is retained. | Fresh GET + `origin_restore(current, baseline)` refuses UID/spec drift, then emits UID/current-RV-guarded original env restoration (including valueFrom or absence). |
| Trust | Only `research_dev_probe https --ca ...` trusts the generated CA. Public CA file in the measurement workspace; no OS/global trust change or CLI verification bypass. | Pod/private fixture directory deletion. Unmodified production CLI must still refuse this CA. Actual CLI TLS work needs a separately reviewed dev trust overlay later. |

The managed-label exception is confined to this development fixture. It is not
the production diagnostic Pod described in R2-PRODUCTION-PREFLIGHT.md. D1 CNP,
additive CNP/CCNP/KNP, VAP/RBAC, runner namespace, Pod privilege, node sysctls,
SELinux and firewalld are unchanged. The relay container alone uses a private
network-namespace `ip_unprivileged_port_start=0`, to bind443 as UID10001 without
capabilities. It has no credentials, host mounts or host network/PID/IPC.

198.18.97.2 is a **candidate synthetic identity**, not assumed to be Cilium world.
First prove world443 and policy realization. If Cilium classifies it as host,
remote-node, cluster or another identity, or the guest firewall denies it, stop
and report. Do not add host/remote-node/8080 allow, firewall rules or a relay
through an internal Service. Success here is never OCI hairpin evidence.

## Baseline and execution preconditions

Review records the enrolled `anxious-dev` domain UUID, guest identity/architecture,
context and operator identity. A hostname alone cannot distinguish this guest
from production. Confirm no user activity/controller operation and reserve a
bounded window; GET inventory is separate from exec/create/patch permission.
Use the existing approved management path/VAP exception, never impersonation.

Record privately (0700 directory, files0600): API Deployment and referenced
configuration, API/runner image source and manifest digests, origin/basePath,
effective research flags, all selecting network policies, D1 spec, namespace
labels, VAP/binding, endpoint identities/policy revisions, routes/Service443 and
original object UIDs/RVs. Never publish full env/Secrets/kubeconfig. The API
must already be008-compatible/ready. Per the
[PM recovery decision](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/97#issuecomment-6924958948),
this window's recovery target is the **current baseline API8804a97 + DB0008**,
with the original origin/config/routes and temporary-object removal. Record its
actual manifest digest again; the preflight observed
`sha256:a7e03d1e8d1e71a2ed4ec9d9377f5e358e81041165a46ee3d37d497ad08bb8f1`.
This window does not replace the API image or migrate/restore the DB.

**First, before fixture changes**, certify that baseline using a new synthetic
browser session: read the synthetic owner's research resource, save a new version
through the ordinary human cookie+CSRF API, then read it back and verify the
author is `human`. Record source/digest, owner/resource/version IDs, HTTP results
and safe author evidence. Keep the test data only under that synthetic owner;
never publish session/CSRF secrets. Failure stops fixture setup and is reported.
This is a human save, with no model turn or research grant.

The imported2e4da86 runtime/ordinary runner has **not** been certified by boot or
human read/save. That separate image-switch certification is outside this window
and remains the **B3 final-PR gate** in [DEV-VM.md](DEV-VM.md#b3--recovery-certification-before-final-pr-approval).
Image presence and the baseline certificate above cannot satisfy that gate.

Require empty basePath and disabled RESEARCH_AGENT_API_ENABLE /
CODEX_RUNNER_RESEARCH_API_ENABLE / RUNNER_RESEARCH_API_ENABLE in their actual
configuration sources. No existing `research-fence.json`; report rather than
delete it. No object/container/link/name/address collision, no overlapping route
for198.18.97.0/30, and guest ip_forward already1. Missing tools/permissions or a
policy discrepancy stop the run; do not install packages/change system config.

Build ordinary runtime/runner from the final reviewed fixture SHA in Docker,
record actual module hashes, import only the measurement image, and capture its
manifest digest. Config IDs are separate. The API is not replaced by this plan.
Run the existing image_check; source/pure tests cannot prove kernel behavior.

## Emit files; no automatic apply

`backend/ops/research_dev_fixture.py` emits files only. Execute in the reviewed
runtime container, with the private API snapshot mounted read-only and a private
output mount. For example, container arguments are:

```sh
python -B -m ops.research_dev_fixture --api-baseline /inputs/api.json \
  --runner-image "$R2_REVIEWED_RUNNER_DIGEST" --out /output/new-fixture
```

Inspect every generated object and patch. Private `objects.private.json` contains
the leaf key; never attach it to evidence. The CA signing key is not retained.
Regenerate only within an approved window if the one-hour certificates expire.
Use create-only requests for the three absent TLS objects and the Pod. A collision
or changed baseline RV is a refusal, not a reason to apply/overwrite automatically.

## A — actual kernel file-root probe, after baseline certification

Create the generated Pod with fresh volumes. Save its UID/spec and wait for the
endpoint to realize the unchanged policy. Confirm no controller adoption/change.
Exec as the container's existing UID10001 (do not supply a different user):

```sh
python -B -m ops.research_dev_probe inode
```

The source refuses nonempty `/state` or `/control`, any broker mount and another
UID. It creates only synthetic canaries and token-free placeholder markers
(`available:false`, never a research credential). It uses actual pinned
`codex sandbox -P native --include-managed-config -C /state/workspace`:

1. An old sandbox opens the exact grant file/FD and proves readonly access.
2. Parent atomically replaces it with a fresh inode while that sandbox stays alive.
3. Old FD and old path must still see only the old marker and the same device/
   inode; a fresh sandbox must see the new marker. Reopening the authorized FD
   through `/proc/self/fd` may be refused or expose only that same old inode and
   marker, both before and after replacement. The virtual parent may refuse
   listing or expose only the allowed filename `turn.json` (including an empty
   filtered list). Original parent/sibling canaries, outside symlink, private
   home/CODEX_HOME/control and external parent process/root/FD reads must fail.
   The trusted parent holds a positively read control-canary FD across the probe;
   its `/proc/<parent>/fd/<control-fd>` and FD listing remain inaccessible. Parent
   FD numbers are passed only as non-secret locators and are not inherited.
4. Only fixed boolean checks leave the sandbox; marker values/private data never
   become output. Process groups are bounded/killed and synthetic files removed.

All checks must be true. A namespace EPERM, no ready handshake or timeout is
unexecuted/failed, never a pass. No privileged fallback. Record Pod UID, kernel,
pin, seccomp/SELinux/caps/NoNewPrivs and namespace IDs alongside the result.
This direct sandbox probe has **no live app-server/provider/arg0 or turn**. It
cannot close #68's entire inventory/root-capability gate. Real parent/sibling
process/arg0/provider isolation, in-turn env, login shell/apply_patch and active
token echo/retired redaction remain separate gates with their own controls.

## B — relay and TLS health, still no credentials

Prepare the generated TLS objects and origin patch only after A passes. Apply
the origin patch with its UID/RV tests, wait for API readiness, and record the
new Pod UID. The old HTTP origin's sessions do not authenticate to this HTTPS
origin. This phase does not seed login sessions or call OAuth; any later browser
flow needs a fresh synthetic HTTPS-bound session under separate execution scope.

In the **guest** only, the reviewed relay command is:

```sh
docker run -d --name issue97-r2-relay --network none --read-only \
  --user 10001:10001 --cap-drop ALL --security-opt no-new-privileges \
  --sysctl net.ipv4.ip_unprivileged_port_start=0 --pids-limit 16 \
  --memory 64m --cpus 0.2 --entrypoint python "$R2_REVIEWED_RUNTIME_DIGEST" \
  -B -m ops.research_dev_relay
```

Record immutable container ID, PID and network namespace before changing links.
Verify that PID still belongs to that ID before each nsenter command. Its source
binds within the new private namespace, accepts at most4 queued connections,
forwards to one fixed destination, logs no payload and ends within600s. It does
not terminate TLS or interpret Authorization. With the prechecked absent names:

```sh
ip link add i97r2host type veth peer name i97r2peer
ip link set i97r2peer netns "$R2_RELAY_PID"
ip addr add 198.18.97.1/30 dev i97r2host
ip link set i97r2host up
nsenter -t "$R2_RELAY_PID" -n ip addr add 198.18.97.2/30 dev i97r2peer
nsenter -t "$R2_RELAY_PID" -n ip link set i97r2peer up
nsenter -t "$R2_RELAY_PID" -n ip route add default via 198.18.97.1
```

Copy only the public CA to `/state/workspace/issue97-ca.pem` in the measurement
Pod; create the ordinary workspace/home/codex directories if A removed them.
First run the TLS health probe **outside** the sandbox to classify node path /
TLS / API errors. Then run the identical source within the real native profile:

```sh
codex sandbox -P native --include-managed-config -C /state/workspace -- \
  python -I -c "$R2_REVIEWED_PROBE_SOURCE" https --ca /state/workspace/issue97-ca.pem
```

The trusted outer launcher reads `R2_REVIEWED_PROBE_SOURCE` from the exact
image's `/app/ops/research_dev_probe.py` before entering the sandbox. The code
argument contains only reviewed stdlib source, no credential; `/app` does not
need a readable-root exception. Record the command/source hash. Set HOME/CODEX_HOME
only for the trusted outer launcher as in the inode probe's fixed ENV.

Correlate UTC/request window, source endpoint/Pod UID/revision, resolved
peer198.18.97.2:443, Cilium identity world and allow, TLS CA/SNI/hostname verification,
public certificate fingerprint and API health200/status=ok. Use bounded existing
`cilium-dbg monitor` observations, not a new watcher/Hubble setting. Traefik remains
the TLS endpoint; record its route and public API connection separately. A timeout
alone is not a Cilium verdict. No direct8080/8081 or insecure TLS fallback.

Negative controls: without `--ca`, platform trust must fail; with `--ca` plus
`--wrong-name`, hostname verification must fail (exit1 and SSLCertVerificationError).
The positive probe must exit0, not just open TCP. Probe sends no auth/cookie/body,
has no proxy or redirect/retry path, and prints only verified peer/cert/status.
Check public `/api/internal` and encoded variants still reject using the existing
review route corpus; do not weaken encoding middleware to make them route.

## C — internal service wrong-purpose refusals

Run `research_dev_probe internal` outside, then the same `python -I -c
"$R2_REVIEWED_PROBE_SOURCE" internal` in the sandbox profile above.
It sends only fixed missing/malformed/never-issued research-shaped credentials,
fixed synthetic IDs and no refresh/attest. Expected broker8080 statuses are401
(missing) /403 (other); callback8081 is401 (missing) /404 (unknown hash/C/R).
Missing-auth401 distinguishes the exact callback listener from a blanket404.
Network failure/503 is not an authentication pass. No response body/header is
printed. Check source/target endpoints and positive reachability in the same window.

Original-auth positive controls, if available, run through an already authorized
trusted service path **outside** this Pod/sandbox and do not copy credentials or
provider envelopes to it. Broker readiness alone proves reachability, not successful
auth. With no approved original-auth control, mark that comparison unverified.
An unsigned malformed token is not a signed wrong-audience test; a never-issued
research-shaped token is not an actual retired/expired/active token. Those cases,
runner service auth and current C/R/owner/public combinations remain pending the
later explicitly budgeted gate. Do not forge live status to claim them here.

## Restoration, including failure paths

1. Stop all bounded probes; ensure no native/model activity was started. Save only
   fixed safe evidence, never private TLS manifests/env/response bodies.
2. Fresh GET api Deployment. In the runtime container call the emitter with
   `--api-baseline /inputs/api.json --restore-current /inputs/current.json --out
   /output/new-restore`. It emits a patch only if current UID/spec match the
   original plus the exact origin change. Apply its current-RV tests. Any other
   drift stops automated restoration and is reported to PM; do not overwrite it.
3. Wait for original8804a97 origin/readiness/image/config to return with DB0008.
   Confirm original HTTP route and origin-bound session behavior using approved
   read-only probes. Track/logout the window's new synthetic browser session
   through the ordinary human auth flow; leave the certified synthetic resource
   versions with their owner as evidence. No schema rollback, backup restore,
   provenance deletion or surgical data cleanup occurs. The baseline certification
   is an expected human write, not a zero-data-change fixture claim.
4. DELETE temporary IngressRoute, Middleware, TLS Secret and measurement Pod using
   each saved UID in `DeleteOptions.preconditions.uid`; Pod grace is bounded.
   Use the review operator's existing Kubernetes client, no policy/RBAC change.
   GET must confirm each absent. Do not delete an identically named replacement.
5. Stop/remove the saved relay container ID. Verify saved veth ifindex/iflink
   before deletion; confirm198.18.97.0/30 route gone, no process/socket/container
   remains. The namespace sysctl disappears with the container.
6. Remove private fixture key/CA/output and workspace copies after verifying
   Secret/Pod deletion; retain only hashes, public cert and redacted evidence.
   Compare baseline routes/config/policies/flags/endpoint revision and existing
   runner/ledger identities. Record every residual or failed restoration to PM.
   Record imported image references separately; retain them only if the approved
   window allows that cache, otherwise remove only newly imported references
   absent from baseline. Never prune shared layers/images or baseline recovery.

## Approval report

Submit final source SHA, pure counts/source hashes, exact proposed changes above,
the admitted deviations and baseline-certification prerequisites to review. Review's source
approval is followed by PM's dev-window approval; this document is neither.
Execution report separates kernel inode, world classification, TLS health, API
path and internal-auth observations from all remaining limitations. Update
PARITY.md only with the dev-only planned difference, and later with measured
values. The production preflight remains a separate user-approved action.
