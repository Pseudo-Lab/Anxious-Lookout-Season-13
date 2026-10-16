# Issue #13: default-allow transition (decision C)

PM executes only after source/plan review. Target: single node `anxious-lookout`, private API `10.0.0.107:6443`, Pod CIDR `10.42.0.0/16`, public firewalld zone. No backend/RBAC changes. Do not change codex-runner or codex-trial policy objects. The old A-plan is superseded.

Run the following in a root Bash shell with `set -euo pipefail`, `umask 077`, `jq`, `firewall-cmd` and `/usr/local/bin/k3s` available. Keep the same shell and private snapshot path for rollback. Do not use firewalld reload. Stop on errors, retain evidence and use rollback for completed steps; do not blindly continue. A failed read is not evidence of absence.

## 0. Snapshot and preflight

Verify the actual node/context, namespace UIDs, API Pod labels/service account, public zone interfaces and entire policy inventory. Confirm no additional policy selects API egress or changes the intended default-allow behavior. Confirm both old ipsets are referenced only by the exact public-zone rich rules below, in runtime and permanent views (`--list-all-zones`). If another reference exists, stop and report that baseline difference instead of deleting it. Confirm the new CIDR rule is absent before adding it (so rollback owns its removal).

```bash
snapshot=$(mktemp -d /var/tmp/issue13-default-allow.XXXXXX)
k() { /usr/local/bin/k3s kubectl "$@"; }
api_rule='rule family="ipv4" source ipset="anxious-k3s-api-clients" port port="6443" protocol="tcp" accept'
metrics_rule='rule family="ipv4" source ipset="anxious-k3s-metrics-clients" port port="10250" protocol="tcp" accept'
cidr_rule='rule family="ipv4" source address="10.42.0.0/16" port port="6443" protocol="tcp" accept'
k get nodes -o wide
k get ccnp,cnp,networkpolicy -A -o json > "$snapshot/all-policies.json"
k get ccnp anxious-lookout-node-protection anxious-lookout-tenant-boundary -o json > "$snapshot/ccnp.json"
k -n m2-hosting get cnp hosting-boundary hosting-access-web-link-v1 -o json > "$snapshot/hosting-cnp.json"
# Replay actual specs, not old templates or stale last-applied annotations.
for file in ccnp hosting-cnp; do
  jq 'del(.metadata) | .items |= map(del(.metadata.uid,.metadata.resourceVersion,.metadata.creationTimestamp,.metadata.managedFields,.status,.metadata.annotations."kubectl.kubernetes.io/last-applied-configuration"))' \
    "$snapshot/$file.json" > "$snapshot/$file-replay.json"
done
for ns in codex-runner codex-trial; do
  k -n "$ns" get cnp,networkpolicy -o json > "$snapshot/$ns.json"
done
firewall-cmd --list-all-zones > "$snapshot/firewall-runtime.txt"
firewall-cmd --permanent --list-all-zones > "$snapshot/firewall-permanent.txt"
for name in anxious-k3s-api-clients anxious-k3s-metrics-clients; do
  firewall-cmd --info-ipset="$name" > "$snapshot/$name-info.txt"
  firewall-cmd --ipset="$name" --get-entries > "$snapshot/$name-runtime.txt"
  firewall-cmd --permanent --ipset="$name" --get-entries > "$snapshot/$name-permanent.txt"
done
cp -a /etc/firewalld "$snapshot/firewalld"
cp -a /etc/systemd/system/anxious-k3s-firewall-sync.{service,timer} "$snapshot/"
cp -a /usr/local/sbin/anxious-k3s-firewall-sync.py "$snapshot/"
systemctl show anxious-k3s-firewall-sync.timer anxious-k3s-firewall-sync.service > "$snapshot/sync-state.txt"
```

Also preserve existing PM `artifacts/pm/issue13-apiserver-access/ccnp-*.json`. Expected old sets are `hash:ip` with default options; compare saved info before using the rollback commands below. Take a control-plane backup and save Docker health/external response. Save any installed postboot collector/env before replacing it with this reviewed revision.

## 1. Retire exact-IP grants and reconciler

The metrics set entries and rule are also removed: without the reconciler/identity guard, retaining a stale Pod IP would grant a later occupant access. No substitute TCP10250 exception is authorized here. Metrics scrape/`kubectl top` degradation is an explicit possible outcome, to record separately.

```bash
systemctl disable --now anxious-k3s-firewall-sync.timer
systemctl stop anxious-k3s-firewall-sync.service
for view in runtime permanent; do
  opts=(); [[ "$view" == runtime ]] || opts+=(--permanent)
  firewall-cmd "${opts[@]}" --zone=public --remove-rich-rule="$api_rule"
  firewall-cmd "${opts[@]}" --zone=public --remove-rich-rule="$metrics_rule"
  for name in anxious-k3s-api-clients anxious-k3s-metrics-clients; do
    entries=$(firewall-cmd "${opts[@]}" --ipset="$name" --get-entries)
    for entry in $entries; do
      firewall-cmd "${opts[@]}" --ipset="$name" --remove-entry="$entry"
    done
  done
done
rm /etc/systemd/system/anxious-k3s-firewall-sync.service /etc/systemd/system/anxious-k3s-firewall-sync.timer /usr/local/sbin/anxious-k3s-firewall-sync.py
systemctl daemon-reload
```

Verify both units are inactive and no installed script remains. Record failures rather than treating `is-active` nonzero alone as proof of removal; inspect `systemctl show` and the exact installed paths. Empty, unreferenced ipset definitions remain in both views: this host's firewall-cmd supports creation/deletion only in permanent configuration. Keeping empty definitions avoids a reload and permits exact rollback. No entries or allow rules remain.

## 2. Remove only the four selected policies

```bash
k delete ccnp anxious-lookout-node-protection anxious-lookout-tenant-boundary
k -n m2-hosting delete cnp hosting-boundary hosting-access-web-link-v1
```

Wait for Cilium endpoint policy realization and record relevant endpoint policy revisions. Do not delete any policy in codex-runner/codex-trial. This removes global isolation outside namespaces with retained selecting policies.

## 3. Grant Pod CIDR API access

```bash
firewall-cmd --zone=public --add-rich-rule="$cidr_rule"
firewall-cmd --permanent --zone=public --add-rich-rule="$cidr_rule"
firewall-cmd --zone=public --query-rich-rule="$cidr_rule"
firewall-cmd --permanent --zone=public --query-rich-rule="$cidr_rule"
```

No reload, trusted zone, 10250 CIDR grant or cloud/public API opening.

## 4. Acceptance (record actual results)

- Query the four deleted objects with `--ignore-not-found -o name`; commands must succeed with empty output. Inventory all policies again. Compare codex-runner/trial `{kind, metadata.name, spec}` sorted by kind/name with the saved snapshots (ignore resourceVersion/status). All must match.
- Verify runtime/permanent sets empty and exact-IP rules absent; inspect Cilium forwarding/NAT chains and status. Verify sync files/units gone. Install the reviewed postboot collector if one was previously installed, to avoid old boot requirements.
- From the current `m2-hosting` API Pod (verify UID, `app=hosting-api`, image and service account), test **10.0.0.107:6443** using the mounted CA/token. Use a verified-TLS request for `/api/v1/namespaces/codex-runner` with the bearer token read inside the Pod. Require HTTP200 and the expected namespace UID. Never print the token or disable certificate verification. A host request is not a substitute; a timeout or 403 does not pass. Use the actual runtime mount paths from the deployment (`/run/runner-kube/`), not a copied administrator kubeconfig.
- API→runner TCP8080 remains permitted by retained `runner-callback-draft` ingress selecting m2-hosting/app=hosting-api. Source egress becomes unrestricted only if inventory confirms no other selecting policy. Validate actual connection and callback, not only policy syntax. The removed hosting link also stops restricting API ingress; record this intended C change.
- Confirm runner DNS/approved HTTPS egress and retained denied destinations using successful authorized-source controls and Cilium drop evidence. Codex-trial policies must not be edited to make tests pass.
- Before retrying prepare, check the settled ensure receipt/reservation; do not reset state or create a duplicate runner. Complete login → own runner → one Codex response → logout → recorded Pod UID absent. Existing storage/registry retention behavior is unchanged.
- Check Docker health, DNS/HTTPS and external response against baseline. Record `kubectl top`/metrics-server scrape outcome separately; failure does not authorize reopening 10250. No full-scope approval from source-only tests.

Example API probe after setting `api_pod` to the verified current Pod name. This
runs Python inside that Pod; only the namespace name/UID and status are printed.
Compare the UID to the preflight namespace inventory.

```bash
k -n m2-hosting exec -i "$api_pod" -c api -- python -B - <<'PY'
import json, pathlib, ssl, urllib.request
context = ssl.create_default_context(cafile='/run/runner-kube/ca.crt')
token = pathlib.Path('/run/runner-kube/token').read_text().strip()
request = urllib.request.Request(
    'https://10.0.0.107:6443/api/v1/namespaces/codex-runner',
    headers={'Authorization': 'Bearer ' + token})
# Ignore proxy environment: prove the direct node path.
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                   urllib.request.HTTPSHandler(context=context))
with opener.open(request, timeout=10) as response:
    assert response.status == 200
    result = json.load(response)
assert result['kind'] == 'Namespace' and result['metadata']['name'] == 'codex-runner'
print('HTTP200', result['metadata']['name'], result['metadata']['uid'])
PY
```

## Rollback (inverse order, within this change)

Stop new application prepare attempts before rollback. Preserve runner/registry state and do not delete runner Pods, PVCs or credentials as a network rollback. For a partial transition, reverse only completed changes using the snapshot.

1. Remove the newly added broad grant in both views **before** restoring old exact-IP exceptions:

   ```bash
   firewall-cmd --zone=public --remove-rich-rule="$cidr_rule"
   firewall-cmd --permanent --zone=public --remove-rich-rule="$cidr_rule"
   ```

2. Restore the actual saved guards/hosting policies and wait for Cilium realization before allowing old IP sets:

   ```bash
   k apply -f "$snapshot/ccnp-replay.json"
   k apply -f "$snapshot/hosting-cnp-replay.json"
   ```

   Confirm restored specs match snapshots. Retained runner/trial policies remain untouched. Re-establish original guard enforcement evidence before the next step; do not use the repository templates as substitutes.

3. Repopulate the two retained empty sets and restore rules in each view, then restore installed files. Confirm both sets still exist with their saved options before proceeding; do not reload firewalld to work around unexpected missing sets.

   ```bash
   for view in runtime permanent; do
     opts=(); [[ "$view" == runtime ]] || opts+=(--permanent)
     for name in anxious-k3s-api-clients anxious-k3s-metrics-clients; do
       entries=$(cat "$snapshot/$name-$view.txt")
       for entry in $entries; do
         firewall-cmd "${opts[@]}" --ipset="$name" --add-entry="$entry"
       done
     done
     firewall-cmd "${opts[@]}" --zone=public --add-rich-rule="$api_rule"
     firewall-cmd "${opts[@]}" --zone=public --add-rich-rule="$metrics_rule"
   done
   cp -a "$snapshot/anxious-k3s-firewall-sync.service" "$snapshot/anxious-k3s-firewall-sync.timer" /etc/systemd/system/
   cp -a "$snapshot/anxious-k3s-firewall-sync.py" /usr/local/sbin/
   systemctl daemon-reload
   systemctl enable --now anxious-k3s-firewall-sync.timer
   ```

   The recorded pre-change timer was active but its service **already failed** because the live guard selector differs from the script's validator. This rollback restores that known degraded baseline, not a working reconciler. Check current IP ownership before restoring saved entries; if addresses changed, leave the affected grant absent and report incomplete rollback rather than grant a new occupant access. No sync-script correction is included. Restore the previous collector/env if replaced, and report all remaining failures. Do not replace all of `/etc/firewalld` or reload it; unrelated rules must remain intact.
