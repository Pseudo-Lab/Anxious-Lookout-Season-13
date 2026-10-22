# Single-node k3s infrastructure

The active network baseline is [issue #13 decision C](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/13#issuecomment-6205986419), dated 2026-10-16. [DEFAULT-ALLOW.md](DEFAULT-ALLOW.md) contains the ordered transition, rollback and live acceptance procedure. Source changes do not establish that the live transition passed. Issue #1 and `VERIFICATION.md` retain historical evidence, including the earlier global isolation policy.

## Layout and installation

- Existing Docker uses its own networks (`172.17.0.0/16`, `172.18.0.0/16`) and data; k3s uses containerd.
- Host VCN: `10.0.0.0/24`; Pods: `10.42.0.0/16`; Services: `10.43.0.0/16`.
- Cilium replaces Flannel and kube-router policy; kube-proxy remains enabled. IPv4 only.
- One node has no high availability; local-path PVCs depend on its disk.
- Development and rehearsal use an x86_64 KVM guest with the same pins and configuration: [DEV-VM.md](DEV-VM.md).

Record routes, Docker health, firewall rules, SELinux/NetworkManager configuration and recovery access before installation. `versions.env` pins versions; install `config.yaml` as `/etc/rancher/k3s/config.yaml` mode 0600, preserve Docker configuration and keep SELinux enforcing with the k3s policy package. Exclude CNI interfaces from NetworkManager management without restarting the remote host connection.

Start k3s and install the pinned Cilium chart using `cilium-values.yaml` and the root-owned kubeconfig. Inspect CNI/IPAM and `allow-localhost: policy`; check node, DNS and Cilium readiness. No public Kubernetes API access, trusted Pod zone, global FORWARD ACCEPT, Docker restart or iptables flush is authorized by this baseline. Traefik/ServiceLB use 80/443; listener health alone does not prove application TLS or availability.

`render-policy.sh` now emits an empty Kubernetes List. It cannot delete existing guards; use the explicit transition procedure. The old templates in `policies/`, `host/firewall-sync.py`, its service/timer, and the issue-1 fixture matrix in `validation/` are historical artifacts, not current installation instructions. Do not reinstall the sync units or apply those policy templates.

## Active network contract

The two CCNPs `anxious-lookout-node-protection` and `anxious-lookout-tenant-boundary`, and m2-hosting CNPs `hosting-boundary` and `hosting-access-web-link-v1`, are removed. Endpoints without another selecting policy use Cilium default allow. This intentionally removes the previous global tenant/node deny guarantee. Inventory other policies rather than assuming all namespaces have no restrictions.

The existing `codex-runner` KNP/CNP and `codex-trial` policies remain unchanged. Apply and realize the namespace's reviewed policies before scheduling runner workloads. The retained runner CNP permits m2-hosting `app=hosting-api` ingress on TCP8080. Namespace policies still enforce the runner's allowed egress and explicit denies; the host firewall grant does not override Cilium denies. Restricted PSA, non-root workloads and tokenless runner ServiceAccounts remain required. Network policy does not implement application authentication or data ownership.

Firewalld public zone permits Pod CIDR `10.42.0.0/16` to TCP6443 in runtime and permanent configuration. This is the explicit issue-13 user decision superseding the earlier rejection of a Pod CIDR API exception. It grants no other host port and no public internet API access. Both old API and metrics ipset rules and entries and the installed sync script/service/timer are retired. No replacement 10250 grant is added: metrics scraping may fail and must be recorded separately.

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
