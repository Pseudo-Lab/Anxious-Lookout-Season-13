# Issue 13: runner DNS/provider egress repair

PM reported DNS failure after successful image repair: both chatgpt.com and the
internal API hostname fail resolution, including raw UDP to kube-dns. Absence of
observed drop events does not prove packet delivery or establish the proxy as the
root cause. This change removes the runner's L7 DNS/FQDN dependency as requested;
live results must establish whether it resolves the blockage.

Only `codex-runner/runner-callback-draft` **spec** changes, using
`40-runner-network.json`. The existing `runner-default-deny` KNP remains unchanged.
No Pod, Secret, volume, registry, supply ledger, API Deployment or global Cilium
configuration change is included. No restart/recreation is required.

## Effective policy

- Select all endpoints in codex-runner, rather than only runner-workload SA.
- Retain ingress from m2-hosting/app=hosting-api TCP8080 and local host TCP8080.
- Explicitly deny ingress/egress between codex-runner endpoints and all endpoints
  in codex-runner or codex-trial, independent of other allow rules. Trial policy
  objects remain unchanged. A future runner namespace must be included before use.
- Allow all other egress without DNS proxy rules or FQDN cache dependencies.
  This deliberately allows internal services, host ports other than6443, metadata
  and internet destinations; these are no longer claimed as denied by this policy.
- Explicitly deny kube-apiserver identity on all ports, including the Kubernetes
  service path, and TCP6443 to every identity/destination. The latter also covers
  node/public addresses classified as host/world. It also blocks non-API uses of
  TCP6443. No egress DNS/HTTPS proxy or arbitrary relay may be claimed to enforce
  transitive destination isolation; the requirement here is direct network paths.

Cilium deny rules override allows, including KNP allows
([Cilium deny policy reference](https://docs.cilium.io/en/stable/security/policy/deny/)).
An empty endpoint selector in this namespaced CNP selects codex-runner endpoints;
it does not select other namespaces. Broad egress does not open ingress from
unrelated endpoints. Inventory all policies selecting these endpoints: any other
L7 redirects or denies must be reported rather than silently removed.

## PM preflight and apply (after review)

Keep browser turns, supply and logout paused while applying and checking policy
realization; keep the existing running Pod and UID. Save namespace UID, Pod UID,
node/IP/containerID/restartCount, the Cilium endpoint ID/identity/revision, current
CNP and KNP and trial policy specs. Confirm the CNP is the expected object and not
terminating. Preserve a control-source API→node6443 success for comparison with
runner denial. Inspect the named CNP's current spec and policy inventory before
applying; do not overwrite unexpected concurrent work.

Root shell, `set -euo pipefail; umask 077`, `jq` installed; set `source` to the
reviewed checkout. `work` is a new private directory. Commands print no secrets:

```bash
work=$(mktemp -d /var/tmp/issue13-egress.XXXXXX)
k() { /usr/local/bin/k3s kubectl "$@"; }
k -n codex-runner get cnp runner-callback-draft -o json > "$work/before.json"
k -n codex-runner get networkpolicy -o json > "$work/knp-before.json"
k -n codex-trial get cnp,networkpolicy -o json > "$work/trial-before.json"
jq -e '.metadata.uid and .metadata.resourceVersion and (.metadata.deletionTimestamp == null)' "$work/before.json" >/dev/null
jq --slurpfile target "$source/infra/hosting/m3/user-runner-e2e/40-runner-network.json" '
  [{op:"test",path:"/metadata/uid",value:.metadata.uid},
   {op:"test",path:"/metadata/resourceVersion",value:.metadata.resourceVersion},
   {op:"test",path:"/spec",value:.spec},
   {op:"replace",path:"/spec",value:$target[0].spec}]' \
  "$work/before.json" > "$work/forward-patch.json"
k -n codex-runner patch cnp runner-callback-draft --type=json \
  --patch-file "$work/forward-patch.json" --dry-run=server -o name
k -n codex-runner patch cnp runner-callback-draft --type=json \
  --patch-file "$work/forward-patch.json" -o name
```

A UID/RV/spec test failure requires rereading and checking the difference; never
remove the tests or use a blanket namespace apply. Do not apply the historical
`user_runner_deployment` resources.json: it restores the old FQDN policy and the
retired hosting link. Do not alter Cilium agent settings or firewalld.

## Live acceptance before resupply

1. Wait for the target Cilium endpoint's desired and realized policy revisions to
   converge and verify the new CNP's realized rules, including both denies. Record
   absence of DNS L7 redirects for this endpoint. Sanitizer/server dry-run success
   alone does not prove dataplane behavior.
2. Verify identical Pod UID/containerID/restartCount and unchanged KNP/trial specs,
   registry/receipt/ledger/Secret bindings. No Pod restart is expected.
3. Inside the existing runner, test DNS UDP **and TCP53** to kube-dns, resolution of
   chatgpt.com and api.m2-hosting.svc.cluster.local, then verified TLS to
   chatgpt.com:443 with a finite timeout and no credential. A returned HTTP error
   may prove TLS connectivity but is not provider authentication/model success.
   Verify API→runner TCP8080 and the internal callback path are still reachable;
   an unauthenticated denial is not an application success.
4. From runner, directly probe node10.0.0.107:6443 and the Kubernetes Service
   (actual ClusterIP:443), plus the node/public API address when reachable from a
   control source. Require matching Cilium policy-denied evidence, not just a
   timeout. Confirm the API Pod can still reach the same API destination with its
   existing authorized credentials. Never mount administrator credentials in runner.
5. Verify runner↔runner denial for existing known runner endpoints in both
   directions with live policy verdicts and successful authorized-source controls.
   Do not execute code inside or mutate codex-trial merely to create a test. If a
   second authorized source/runner probe is unavailable, record that live direction
   as pending and ask PM to arrange a separately scoped test; do not claim a pass
   from absence of a listener or create another Pod under this procedure. The
   existing quota/UID/receipt remain intact. Record Cilium identity-based policy
   evaluation for same-namespace peers separately from actual packet evidence.
6. Only after positive paths and required deny checks, retry the existing explicit
   service supply with its approved snapshot, preserving ledger history and sticky
   blocks. Complete one actual response then logout/UID deletion. A policy change
   cannot promise credential/provider success; report the exact stage on failure.

Keep evidence private/redacted. Full issue13 approval remains dependent on live
results. If DNS still fails, report DNS/proxy/dataplane evidence without resetting
ledger or replacing the Pod.

## Rollback

Pause turns/supply, preserve all existing state and snapshot the current CNP. Check
its UID still equals `before.json`, current spec equals the reviewed candidate and
it is not terminating. Use the **fresh** resourceVersion to conditionally restore
only the saved original spec:

```bash
k -n codex-runner get cnp runner-callback-draft -o json > "$work/current.json"
jq -e --slurpfile old "$work/before.json" \
  --slurpfile target "$source/infra/hosting/m3/user-runner-e2e/40-runner-network.json" \
  '.metadata.uid == $old[0].metadata.uid and .spec == $target[0].spec and (.metadata.deletionTimestamp == null)' \
  "$work/current.json" >/dev/null
jq --slurpfile old "$work/before.json" '
  [{op:"test",path:"/metadata/uid",value:.metadata.uid},
   {op:"test",path:"/metadata/resourceVersion",value:.metadata.resourceVersion},
   {op:"test",path:"/spec",value:.spec},
   {op:"replace",path:"/spec",value:$old[0].spec}]' \
  "$work/current.json" > "$work/reverse-patch.json"
k -n codex-runner patch cnp runner-callback-draft --type=json \
  --patch-file "$work/reverse-patch.json" -o name
```

Wait for realization and compare original specs, KNP/trial policies and Pod UID.
Rollback restores the original DNS-blocked baseline; it is not recovery to a
working provider path. Any divergence is reconciliation work, not permission to
replace policies/Pods or clear receipts.
