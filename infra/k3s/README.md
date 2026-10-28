# Single-node k3s infrastructure

The active network baseline is [issue #13 decision C](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/13#issuecomment-6205986419), dated 2026-10-16. [DEFAULT-ALLOW.md](DEFAULT-ALLOW.md) contains the ordered transition, rollback and live acceptance procedure. Source changes do not establish that the live transition passed. Issue #1 and `VERIFICATION.md` retain historical evidence, including the earlier global isolation policy.

## Layout and installation

- Existing Docker uses its own networks (`172.17.0.0/16`, `172.18.0.0/16`) and data; k3s uses containerd.
- Host VCN: `10.0.0.0/24`; Pods: `10.42.0.0/16`; Services: `10.43.0.0/16`.
- Cilium replaces Flannel and kube-router policy; kube-proxy remains enabled. IPv4 only.
- One node has no high availability; local-path PVCs depend on its disk.
- Development and rehearsal use an x86_64 KVM guest with the same pins and configuration: [DEV-VM.md](DEV-VM.md).
- Production ↔ development differences and whether each is allowed: [PARITY.md](PARITY.md). A change that creates a new difference adds a row there.

Record routes, Docker health, firewall rules, SELinux/NetworkManager configuration and recovery access before installation. `versions.env` pins versions; install `config.yaml` as `/etc/rancher/k3s/config.yaml` mode 0600, preserve Docker configuration and keep SELinux enforcing with the k3s policy package. Exclude CNI interfaces from NetworkManager management without restarting the remote host connection.

Start k3s and install the pinned Cilium chart using `cilium-values.yaml` and the root-owned kubeconfig. Inspect CNI/IPAM and `allow-localhost: policy`; check node, DNS and Cilium readiness. No public Kubernetes API access, trusted Pod zone, global FORWARD ACCEPT, Docker restart or iptables flush is authorized by this baseline. Traefik/ServiceLB use 80/443; listener health alone does not prove application TLS or availability.

`render-policy.sh` now emits an empty Kubernetes List. It cannot delete existing guards; use the explicit transition procedure. The old templates in `policies/`, `host/firewall-sync.py`, its service/timer, and the issue-1 fixture matrix in `validation/` are historical artifacts, not current installation instructions. Do not reinstall the sync units or apply those policy templates.

## Active network contract

The two CCNPs `anxious-lookout-node-protection` and `anxious-lookout-tenant-boundary`, and m2-hosting CNPs `hosting-boundary` and `hosting-access-web-link-v1`, are removed. Endpoints without another selecting policy use Cilium default allow. This intentionally removes the previous global tenant/node deny guarantee. Inventory other policies rather than assuming all namespaces have no restrictions.

The existing `codex-runner` KNP/CNP and `codex-trial` policies remain unchanged. Apply and realize the namespace's reviewed policies before scheduling runner workloads. The retained runner CNP permits m2-hosting `app=hosting-api` ingress on TCP8080. Namespace policies still enforce the runner's allowed egress and explicit denies; the host firewall grant does not override Cilium denies. Restricted PSA, non-root workloads and tokenless runner ServiceAccounts remain required. Network policy does not implement application authentication or data ownership.

Firewalld public zone permits Pod CIDR `10.42.0.0/16` to TCP6443 in runtime and permanent configuration. This is the explicit issue-13 user decision superseding the earlier rejection of a Pod CIDR API exception. It grants no other host port and no public internet API access. Both old API and metrics ipset rules and entries and the installed sync script/service/timer are retired. No replacement 10250 grant is added: metrics scraping may fail and must be recorded separately.

## Cilium DNS proxy (#60)

`cilium-values.yaml` sets `l7Proxy: true` (user decision (a) on #60), the same as production. `toFQDNs` rules, such as the codex-broker's explicit `fqdn` switch for `auth.openai.com` and `chatgpt.com`, need the DNS proxy: it reads the DNS answers of endpoints with `rules.dns` and allows the learned IPs. With `l7Proxy: false` those rules are not enforced. The proxy runs inside cilium-agent. `envoy.enabled: false` keeps the cilium-envoy DaemonSet absent; no HTTP/Kafka L7 rules are used. L3/L4 policy meaning, kube-proxy and `allow-localhost: policy` do not change. For an endpoint whose policy has `rules.dns`, **all** of its DNS goes through the proxy, cluster-internal names included (`kubernetes.default.svc`, Service names). If the proxy path fails, that endpoint cannot resolve anything, including the Kubernetes API name. Endpoints without `rules.dns` are not proxied.

**Production already runs this.** A read-only check on 2026-10-26 found `enable-l7-proxy: "true"`, `external-envoy-proxy: "false"`, `enable-hubble: "false"` and no cilium-envoy DaemonSet. The repository file said `false` from #1 until #60, which was a drift from the live value. Production needs no upgrade or agent restart: before applying FQDN policies there, check those values only, using the `get cm` line below.

The development VM was installed from the old file and was switched with the procedure below on 2026-10-26. Its FQDN result is in "Development VM result" below; the follow-up is #70. The procedure also applies to any cluster installed from a file older than #60. Run it in a maintenance window with no runner turns in progress:

```sh
k() { sudo /usr/local/bin/k3s kubectl "$@"; }
cm() { k -n kube-system get cm cilium-config -o json | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["data"],sort_keys=True,indent=0))'; }
cm > /tmp/cilium-config-before.json
sudo env KUBECONFIG=/etc/rancher/k3s/k3s.yaml /usr/local/bin/cilium upgrade --version 1.20.2 --values cilium-values.yaml
cm > /tmp/cilium-config-after.json; diff /tmp/cilium-config-before.json /tmp/cilium-config-after.json   # only the intended keys
k -n kube-system rollout restart ds/cilium deploy/cilium-operator    # the chart rolls neither on a ConfigMap change
k -n kube-system rollout status ds/cilium --timeout=300s; k -n kube-system rollout status deploy/cilium-operator --timeout=300s
sudo /usr/local/bin/k3s kubectl -n kube-system exec ds/cilium -c cilium-agent -- cilium-dbg status | grep -E '^(Cilium|KubeProxyReplacement|Proxy Status|Hubble):'   # Proxy Status OK, Envoy: embedded
sudo /usr/local/bin/k3s kubectl -n kube-system get cm cilium-config -o jsonpath='{.data.enable-l7-proxy} {.data.external-envoy-proxy} {.data.kube-proxy-replacement}{"\n"}'   # true false false
sudo /usr/local/bin/k3s kubectl -n kube-system get ds cilium-envoy    # NotFound
```

- **Rendered-config diff before any restart.** Restart only if the diff shows nothing but the intended keys. Otherwise run the upgrade again with the previous values, check that the diff against `cilium-config-before.json` is empty, and stop.
- **Keep `kubeProxyReplacement: false` pinned.** `cilium-values.yaml` sets it, and the upgrade above uses that file, not `--reuse-values`.
  - The CLI still prints "Auto-detected kube-proxy has not been installed / Cilium will fully replace all functionalities of kube-proxy", because k3s embeds kube-proxy and has no DaemonSet to detect. The pinned file value wins.
  - On the development VM the Helm release values had held `kubeProxyReplacement: true` while the live ConfigMap said `false`. A `--reuse-values --set <one key>` upgrade re-rendered `true` and changed a second key (#60, 2026-10-27). That shows the drift between the stored values and the live config on that VM; it does not show that every `--reuse-values` upgrade flips KPR.
  - With `--reuse-values`, always add `--set kubeProxyReplacement=false` and still diff.
- **Restart the operator too.** On the development VM an operator left running from before the DNS-proxy switch kept marking CiliumNetworkPolicies `Valid=False` until it was restarted (#60). Production's operator started in the same rollout that enabled `l7-proxy` (2026-09-25) and its CNPs are all Valid.
- `cilium upgrade` only updates the ConfigMap. The agent reads `enable-l7-proxy` at start, so the explicit `rollout restart` replaces the agent Pod once (single node; 13 s on the development VM, 2026-10-26). Established connections keep working in the BPF datapath, but new endpoints and policy changes wait until the agent is Ready.
- Then check the dataplane, not only agent health: `cilium status` OK, `iptables-save | grep -c '^:CILIUM_FORWARD'` is 1, Pod DNS works, and Traefik and the API still answer.
- **Acceptance (one endpoint, one policy set, both outcomes):**
  - Record every policy that selects the endpoint.
  - Both allowed names (`auth.openai.com`, `chatgpt.com`) connect.
  - A non-allowed name and a direct IP are each recorded together with the DNS answer, the destination IP and the policy drop (`cilium-dbg monitor --type drop` in the agent).
  - `toFQDNs` allows the IPs learned from the allowed names' DNS answers, so the direct-IP sample must not be one of those IPs.
  - A timeout alone is inconclusive.
- **Cost:** record cilium-agent memory and CPU before and after, measured the same way. A rollout can replace the agent Pod instead of restarting its container, so record:
  - the Pod UID, creation time and container restart counts before and after;
  - the number of rollouts.

  This separates the expected replacement from an unexpected restart.
- **Hubble** stays disabled. If a verification window uses it, enable only the agent part for that window: `cilium hubble enable --relay=false --ui=false`. The plain command enables relay by default. Afterwards:
  - `cilium hubble disable`;
  - check that `cilium-config` `enable-hubble` is `false` or absent and that no `hubble-relay` or `hubble-ui` Deployment exists;
  - record the temporary difference.
- **Rollback:** set `l7Proxy: false` and run the same `cilium upgrade` with the diff and both restarts. FQDN egress (`toFQDNs`) then stops working. The broker's default `world` egress does not depend on the proxy.

### Development VM result: the DNS proxy path fails (#60, follow-up #70)

The broker's FQDN egress was measured on the development VM with Cilium 1.20.2. This VM matches production in KPR `false`, tunnel (vxlan), iptables masquerade and host legacy routing.
- **Symptom:** with the broker's `fqdn` policy, **every** lookup from the broker times out over UDP and TCP to CoreDNS (`10.43.0.10`), cluster names included. So TokenReview and Pod GET fail too.
- **What was observed:**
  - The proxy receives and forwards the queries (received = forwarded, denied 0).
  - Host-to-CoreDNS queries work.
  - Neither Cilium nor nftables shows a drop.
  - Host conntrack shows the proxy's upstream query `[UNREPLIED]`.
  - Firewalld is not confirmed as the cause.
- **It depends on the policy path:** deleting only the broker CNP made all lookups and the API work at once. Restoring the identical CNP brought the failure back.
- **Tried without effect:**
  - **The stale operator:** restarting it made the CNPs `Valid=True` but did not fix DNS.
  - **Candidate 1, `dnsProxy.enableTransparentMode=false`:** applied as a single-key change, then restored. It changed nothing observable: all 6 lookups timed out at baseline, while applied and after restore. This does not rule transparent mode out of a combined cause.
- **Decision (user, 2026-10-29):**
  - The broker defaults to `egress: world`, which has no `rules.dns` and so no proxy path. `fqdn` stays an explicit switch.
  - Candidate 2 (`bpf.tproxy=true`) and restoring the domain restriction are **[#70](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/70)**: its plan, gates and restore (`--set bpf.tproxy=null`, not `false`) are recorded there.
  - Production has the same values, so the same failure is likely there for any `rules.dns` endpoint until #70 resolves it; a development result alone does not prove it. Changing production values is a separate user decision.

## Validation and reboot

Follow `DEFAULT-ALLOW.md`: verify removed objects, unchanged runner/trial policies, both firewall views, API-Pod authenticated API access, actual runner TCP8080/login/Codex response/logout deletion, and Docker regressions. An API response alone is not the end-to-end gate. Retained runner-deny checks need an authorized-source control plus Cilium policy-drop evidence; a timeout alone is inconclusive. The historical issue-1 tenant matrix is no longer an acceptance requirement for default-allow namespaces.

Use [REBOOT.md](REBOOT.md) for reboot evidence. The collector checks current infrastructure and policy presence, then exits 2 to require live runner/API follow-up; it does not assert that policy contents or the application path passed.

## Backups and recovery

`backup.sh /var/backups/anxious-lookout/k3s` uses SQLite's online backup API without stopping k3s or Docker. It includes matching token, credential/TLS material, config, host configuration, and CCNP/CNP/KNP exports. Avoid concurrent credential rotation. Archives are root-only, credential-bearing, and exclude application/PVC data. Keep seven verified snapshots and an encrypted off-host copy before manual pruning. A local archive does not protect against host loss.

Run `validation/restore-check.py` in a disposable `python:3.12-alpine` container with `--network none --read-only --tmpfs /tmp`, mounting only the archive at `/backup.tar.gz:ro` and checker read-only. It verifies SQLite integrity, nonempty Kine and recovery inputs, without requiring the retired guards. It does not validate policy semantics. `restore-check.sh` additionally boots an isolated copied datastore API and compares the serving Secret hash; it inventories restored policies/namespaces. Compare that inventory with the backup's intended baseline separately. Never mount or restore over the live datastore for a test. Full recovery also requires version pins, token, application data and live network checks.

On this host a firewalld reload removed Cilium-managed iptables chains despite healthy Cilium status. The transition uses explicit runtime and permanent changes, **no reload**. If a separately authorized reload breaks forwarding, the operator can restore the dataplane with:

```bash
sudo /usr/local/bin/k3s kubectl -n kube-system rollout restart daemonset/cilium
sudo /usr/local/bin/k3s kubectl -n kube-system rollout status daemonset/cilium --timeout=180s
sudo env KUBECONFIG=/etc/rancher/k3s/k3s.yaml /usr/local/bin/cilium status --wait
```

Expect a network interruption. Check `CILIUM_FORWARD` and `CILIUM_POST_nat`, DNS/HTTPS, runner isolation, API access and Docker regressions afterwards; agent health alone is insufficient. Keep independent recovery access. Do not uninstall Cilium/k3s or remove Docker as part of this change.
