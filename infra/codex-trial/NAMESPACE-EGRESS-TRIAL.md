# #7 namespace egress trial (2026-10-13)

Authority: [PM/user scope](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/7#issuecomment-6149034332).
Backend authors this package; reviewer approves exact source and PM executes on
the established k3s/containerd target. No Docker/host clock comparison is required.
Docker here only runs offline emitters and tests. Do not apply inspection Lists.

The preserved input is `/tmp/issue7-namespace-egress-20261013/` (CNP4, CCNP2,
namespace without tenant=true, all-cluster KNP0, runner replicas0, completed init
Pod, two Bound PVCs). Exact private diff/patches are in
`/tmp/issue7-namespace-egress-candidate-20261013/`. Inputs remain unchanged.

`namespace_egress.py` changes four existing trial CNPs: replace their egress with
`toEntities: [all]`, remove their egressDeny and preserve all other rule fields,
including selectors, ingress, ingressDeny and enableDefaultDeny. It changes only
node-protection's existing namespace NotIn values from `[kube-system]` to
`[kube-system, codex-trial]`; other namespaces and all destination deny entries
remain unchanged. Tenant CCNP is unchanged because its exact label selector does
not select this namespace. A new `trial-namespace-egress-v1` selects every local
endpoint, allows egress to all entities and explicitly denies endpoints with
`k8s:io.kubernetes.pod.namespace=codex-trial`. It does not enforce ingress.

Explicit deny wins over additive allows, including direct Pod traffic and Service
traffic resolved to those endpoint identities. Local loopback is distinct.
Same-namespace API↔DB/runner/tool callbacks will be blocked too; the retained
access-web runner uses m2-hosting API and does not require that exception.
External/other namespace/node access is intentionally broad in this user-approved
trial. Destination ingress/auth may independently reject an allowed egress flow.
No hostNetwork Pod is supported by this trial: namespace endpoint identity is
needed for namespace denial. Do not change Cilium global flags or shared ingress.

## Offline preparation and fresh drift gate

Run shell procedures on the existing operator host with `set +x; umask 077`.
Keep stdout/stderr, resource identities, addresses and policy diffs private.
Define the established host wrapper (normal calls bounded to15s):

```bash
k() { timeout --signal=TERM --kill-after=2s 15s sudo -n /usr/local/bin/k3s kubectl --request-timeout=10s "$@"; }
```

Use the reviewed checkout as `EGRESS_SOURCE`; use immutable Docker test image ID
`sha256:5ddf480f4872b13bcb246f681b7d418eb221f134f83f8de1c3cd753aad16c41e`
as `EGRESS_CHECK_IMAGE`. Run Docker as the operator UID:GID so private inputs remain
readable without changing permissions. For emit, mount the preserved input dir
read-only as `/baseline` and a **new**0700 parent output dir as `/output`, plus
`$EGRESS_SOURCE/infra/codex-trial` read-only as `/checks`:

```bash
docker run --rm --network none --read-only --tmpfs /tmp --cap-drop ALL \
  --security-opt no-new-privileges:true --user "$(id -u):$(id -g)" \
  --mount "type=bind,src=$EGRESS_SOURCE/infra/codex-trial,dst=/checks,readonly" \
  --mount "type=bind,src=$EGRESS_BASELINE,dst=/baseline,readonly" \
  --mount "type=bind,src=$EGRESS_OUTPUT_PARENT,dst=/output" \
  --entrypoint python "$EGRESS_CHECK_IMAGE" -B /checks/namespace_egress.py /baseline /output/policy
```

`bash infra/codex-trial/capture_namespace_egress.sh <new-private-dir>` captures
fresh policy/namespace/runner/Pod/Service/PVC and hosting/agent metadata using
the established absolute k3s path. Run the emitter with the same Docker arguments,
fresh dir read-only as `/fresh`, `--verify /baseline /fresh`. It compares full
specs+UIDs for CNP4/CCNP2 and namespace UID+labels; ignores status-only RV changes.
It refuses any new CNP/CCNP, changed selector or any KNP anywhere. Independently
confirm runner still0, no running trial Pods, PVC/init UID/state preserved,
hosting api/web/postgres Ready, API mapping disabled and original routes healthy.
Refresh before applying; check current namespace labels/unchanged tenant selector
again after the test. A conflicting concurrent policy update stops the trial.
Capture includes current m2-hosting CNPs; the canary emitter separately requires
that fresh `hosting-cnp.json` and `resources.json`. It validates the unchanged
hosting API bridge's exact runner namespace/app/TCP8080 ingress, rejects matching
ingress deny or unrecognized hosting policy selectors, and refuses any trial
Service selecting the source with publishNotReadyAddresses=true. Retain these
hosting ingress/Service inputs unchanged for after-test comparison too.

Every JSON Patch tests exact UID and entire spec/specs. Status changes alone are
allowed; replacement, changed ingress or concurrent rule changes are refused.
Do not hand-edit a failed patch. Re-capture/diff/review only the actual discrepancy.

Before any mutation, server dry-run new namespace CNP and each forward patch:

```bash
k create --dry-run=server -f "$EGRESS_POLICY/namespace-policy.json" -o json
for name in access-state-diagnose-deny-v1 access-web-boundary-v1 access-web-state-init-deny-v1 trial-boundary; do
  k -n codex-trial patch cnp "$name" --type=json --patch-file "$EGRESS_POLICY/$name.forward.json" --dry-run=server -o json
done
k patch ccnp anxious-lookout-node-protection --type=json \
  --patch-file "$EGRESS_POLICY/anxious-lookout-node-protection.forward.json" --dry-run=server -o json
```

Store outputs privately. This checks admission only. Pinned Cilium1.20.2 sanitizer
must also pass all generated `/cases/cilium-cases.json` rules with the validator
ID `sha256:f1e2e559be536f8f2830db6905aa2a38afa2b6be0f0b1d9d8648b32384e37d2d`.
Use the same user mapping to read private0600 files. No credential/runtime image
rebuild is needed for this policy/source-only change.

## Deny realization before relaxation

1. **Create the new namespace policy first** (`k create -f ... -o json`), retaining
   its full result/UID. Existing fixed name must be absent; never adopt/overwrite.
   GET updated CNP generation and Valid=True; confirm the exact rule appears in
   source-node agent `cilium-dbg policy get`. API success/Valid=True is not datapath
   proof. Keep every old restriction unchanged at this point.
2. Render `namespace_egress_probe.py emit <existing-reviewed-imported-API@sha256:...>
   --resources /fresh/resources.json --hosting-policies /fresh/hosting-cnp.json`
   in the same Docker source mount into a private `probe.json`. PM verifies existing
   containerd image provenance/UID10001 and does not import a fixture/tag-only image.
   The result contains one ConfigMap, two300s Pods and one Service. No credentials,
   SA token, PVC, hostPath, native process or controller. Script is mounted from
   the reviewed ConfigMap and serves only a fixed synthetic TCP ACK on18080 for240s.
   Server dry-run and `k create -f "$EGRESS_PROBE" -o json` retaining each UID.
   All four names must have been absent. If create partially succeeds, GET these
   exact names and record which newly created UIDs belong to this attempt.
3. Wait up to60s for peer Ready and source Running with a running probe container.
   Source **must stay Ready=False**: its readiness exec always exits1, from startup.
   Retain Pod/CEP/Service/EndpointSlice GETs.
   Source/peer are `trial-egress-source-v1`/`trial-egress-peer-v1`, label
   source `app=access-web-runner` and peer `app=trial-namespace-egress-probe`.
   This matches the API's existing runner ingress without changing it. The source
   is selected by the old runner CNP and the new namespace policy; peer is selected
   only by the new namespace policy. Tenant selector is false. Confirm peer
   endpoint realized ingress enforcement is disabled (no ingress drop policy),
   and source endpoint realized egress is enabled with new deny at current policy
   revision. Verify peer Pod UID/IP is the exact Ready EndpointSlice targetRef for
   its Service. IPv4 only for this package's listener; an IPv6-only target stops.
   Source is also selected by the existing Runner Service but must never become
   a usable endpoint: its unready state and publishNotReadyAddresses=false exclude
   it from Kubernetes Service routing. It runs no native/HTTP8080/credential/PVC.
   Before traffic, capture fresh `get pods,services,pvc -n codex-trial -o json`
   as `resources.json` and `get endpointslices -n codex-trial -o json` as
   `slices.json` to a new private directory. Run the Docker-mounted tool:
   `namespace_egress_probe.py isolation --resources /fresh/resources.json
   --slices /fresh/slices.json --source-uid <retained-create-UID>`.
   This requires source Running/Ready=False, the exact always-failing readiness,
   unchanged synthetic command, safe matching Service publication, and every
   matching source EndpointSlice entry explicitly ready=false (serving cannot be
   true). Match entries by either source UID or any canonical status.podIP/podIPs
   address, then require both the Pod reference and family-specific routing
   addresses to agree. Missing/stale targetRef for a source address, matching UID
   with another address, ambiguous address families or conditions are refused even
   if the entry claims to be unready. Absence of a source entry is safe.
   Repeat this guard after the connection test. PodReady and Cilium endpoint-ready
   are distinct: actual CEP realization/current revision is still required.
4. Run peer and source's `loopback` mode with bounded `k -n codex-trial exec <pod>
   -c probe -- python -B /probe/namespace_egress_probe.py loopback` and retain exit0.
   This proves both listeners before the deny attempt. Source readiness alone is
   not sufficient. Prepare private0600 `targets.json` with exactly `peerPod`,
   `peerService`, `apiPod`, `apiService` IP values. Select m2-hosting API Pod by the
   current API Service EndpointSlice's Ready targetRef UID, not by guessed labels;
   verify its declared/resolved TCP8080 and retain unchanged serving Pod identity.
5. On the source's actual node agent, run a narrowly filtered existing drop monitor
   during the following deny test. Confirm existing remote `timeout` is available;
   do not install/debug-enable anything. In a second operator shell (no inbox
   watcher), use the verified agent Pod/container and numeric source CEP ID:

   ```bash
   timeout --signal=TERM --kill-after=2s 40s sudo -n /usr/local/bin/k3s kubectl \
     --request-timeout=10s -n kube-system exec "$EGRESS_AGENT" -c cilium-agent -- \
     timeout -s INT 30s cilium-dbg monitor --type drop --related-to "$EGRESS_CEP_ID" \
     > "$EGRESS_EVIDENCE/drop-before.log" 2> "$EGRESS_EVIDENCE/drop-before.err"
   ```

   Require monitor startup before traffic. The remote30s timeout bounds the reader;
   record its exit (124 can be expected at timeout) and reader termination. No
   verbose/hex/payload capture, shared agent changes or permanent collector.
   Run `deny` once using targets via stdin:

   ```bash
   timeout --signal=TERM --kill-after=2s 20s sudo -n /usr/local/bin/k3s kubectl \
     --request-timeout=10s -n codex-trial exec -i trial-egress-source-v1 -c probe -- \
     python -B /probe/namespace_egress_probe.py deny < "$EGRESS_EVIDENCE/targets.json" \
     > "$EGRESS_EVIDENCE/deny-before.jsonl" 2> "$EGRESS_EVIDENCE/deny-before.err"
   ```

   Require source egress **Policy denied** observations matching source endpoint/
   identity, peer destination identity/IP and TCP18080 for both Pod and Service
   attempts (Service can appear as translated backend IP). Preserve actual tuple
   and test interval privately; peer served count must not increase. Timeout,
   unready listener, ingress drop, missing/ambiguous attribution or stale endpoint
   revision does not pass. Record unsupported monitor capability precisely and
   stop before relaxing; do not start the previous Docker clock diagnosis.

Only after this evidence passes may existing restrictions be relaxed. There are
no live trial runtime endpoints at baseline; only these two canaries may run.
The source canary is selected by the existing runner CNP, but its new namespace
deny must already be realized before that CNP is relaxed. No original runner/native
is running and API mapping remains disabled throughout the connection test.

## Relaxation, bounded connection checks, and recovery

Patch CNP4 with the same non-dry-run argv above, one at a time, then node CCNP.
Save each response/exit independently; append the name to a private applied list
only after success. After timeout/ambiguous response GET that exact object and
compare with its saved before/after spec+UID to determine whether it changed.
Never continue from ambiguity. Node exclusion is only applied after namespace deny
has already passed. Do not modify tenant CCNP or delete any old policy.

GET CNP/CCNP and source/peer CEP, source agent policy/endpoint state again.
Require current generations valid, exact desired rules imported, current source
realized revisions and peer ingress still disabled. Then repeat the filtered drop
monitor to `drop-after.log` and run `check` once with the same input. Verify the
existing probe image has `timeout`; do not install tools or substitute native
startup. Use this remote-process limit as well as the host-client limit:

```bash
timeout --signal=TERM --kill-after=2s 45s sudo -n /usr/local/bin/k3s kubectl \
  --request-timeout=10s -n codex-trial exec -i trial-egress-source-v1 -c probe -- \
  timeout --signal=TERM --kill-after=2s 40s python -B /probe/namespace_egress_probe.py check \
  < "$EGRESS_EVIDENCE/targets.json" > "$EGRESS_EVIDENCE/check-after.jsonl" \
  2> "$EGRESS_EVIDENCE/check-after.err"
```

Check mode makes two3s denied connects,
two5s unauthenticated `/healthz` GETs to m2-hosting API Pod/Service, resolves exact
API/`chatgpt.com` DNS names, and performs one CA/SNI-verified TLS443 handshake to
chatgpt.com with **no HTTP/provider/model request**. DNS `getaddrinfo` has an
8s Python alarm, but a blocked libc resolver need not return immediately to Python;
the independent remote timeout bounds the whole process. Preserve missing-case
results on timeout and verify process termination before cleanup.

Pass requires both correlated egress Policy denied flows, unchanged peer served
counts, both loopback controls, both API HTTP200 and DNS/TLS success, actual
source/Service identities and unchanged existing ingress/hosting/PVC/init state.
TCP/IPv4 Pod+Service and system DNS are the tested scope. The rule has no protocol
or port exception, but UDP same-namespace, IPv6, SCTP, ICMP, hostNetwork, node-local
redirect and preexisting established connections are not datapath-tested here.
No endpoints are guessed and no failed model turn is retried.

On connection/deny/isolation success, the PM-requested disposition is to keep
the independently verified egress policies for the existing Codex follow-up.
UID-delete both canary Pods first, verify actual absence (max60s), then UID-delete
their Service/ConfigMap and confirm the original Runner Service has no canary
endpoint, runner still0, mapping disabled and existing hosting/PVC/init/ingress
unchanged. Keep the namespace policy and five changed policies, including the
node exception. Record exact retained generations/UIDs/specs and actual results.
This disposition requires review of this revised source/procedure and successful
actual evidence; do not retain a partially applied or failed trial as success.

On failure, partial/ambiguous apply, or a later decision to end this overlay,
use the following recovery:

1. Keep API mapping disabled and runner0; do not supply credentials or start native
   while recovering. Delete only newly created canary Pods using
   `delete_namespace_egress.sh pods <name> <retained-create-UID>`; wait for actual
   disappearance (max60s). If deletion/identity is ambiguous leave policies intact
   and report exact resource/UID. Preserve private failed evidence.
2. Apply saved rollback patches only to policies verified changed by this attempt,
   node CCNP first then CNP4 in reverse successful order. Use the same patch argv
   with `.rollback.json`. Their UID+entire desired spec tests reject unexpected
   drift. Never restore whole snapshot metadata/status, recreate a policy or
   overwrite concurrent ingress changes. Record each exit/result.
3. Confirm original exact CNP4/node spec+UID and unchanged tenant spec+UID/namespace
   labels; actual agent imported original restrictions, current policy revisions
   and all trial runtime endpoints absent. If runtime was separately authorized
   later, terminate it and verify restored restrictions on any surviving endpoints
   before removing namespace deny. **Only after restoration/termination evidence**,
   remove the new namespace CNP with retained UID using the delete helper.
4. UID-delete only the new Service/ConfigMap with the helper after Pods are gone.
   Confirm all five temporary objects absent and runner0; compare original PVC/
   completed init/hosting deployments/DB/ingress and original routes. Failed wait
   leaves deny in place. Never delete namespace/PVC/DB/old init or reset the ledger.

Retaining the overlay does not itself authorize new credentials/native operations
outside the existing reviewed continuation. This package starts no existing runner
and supplies no auth. Never reapply the old access-web policy renderer during
continuation: its egress/DNS allow-list would change the verified overlay, while
its hosting ingress must stay unchanged.
Actual Codex readiness requires the retained reviewed access-web runtime/import,
private selected source/owner/state/window, API mapping/lifecycle procedure and
existing max3 durable budget. Report missing/expired inputs individually. Network
success alone does not establish entitlement, fixed-model response, tools, browser
success or native resume. Use the existing `ACCESS-WEB-TEST.md` for that next stage.

## Reproduction and interpretation

Docker test command: source read-only at `/checks`, network none/read-only,
`--tmpfs /tmp:rw,exec` (offline shell-transport stubs need execution),
immutable check image above, `python -B -m pytest -p no:cacheprovider
/checks/test_namespace_egress.py -q`. Generated live-snapshot cases are validated
with the pinned validator image at `/cases/cilium-cases.json` under both flag modes.
These prove emit/guard/rollback structure and upstream sanitizer acceptance, not
actual BPF decisions. Original input checksums are retained for independent review.

Primary sources: [Cilium deny precedence](https://docs.cilium.io/en/stable/security/policy/language/#deny-policies),
[namespace selectors](https://docs.cilium.io/en/stable/security/policy/kubernetes/),
[drop monitor](https://docs.cilium.io/en/stable/operations/troubleshooting/),
[EndpointSlice addresses and targetRef](https://kubernetes.io/docs/reference/kubernetes-api/discovery/endpoint-slice-v1/).
