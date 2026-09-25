# Single-node k3s infrastructure

Issue [#1](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/1) is the execution log and acceptance record. This directory contains the intended configuration; a file's presence alone does not establish that a live check passed. See `VERIFICATION.md` for measured results.

## Layout and boundaries

- Existing Docker and its data remain in place; k3s uses its own containerd.
- Docker networks: `172.17.0.0/16`, `172.18.0.0/16`; host VCN: `10.0.0.0/24`.
- k3s Pods: `10.42.0.0/16`; Services: `10.43.0.0/16`; Cilium cluster-pool IPAM uses the same Pod range.
- Cilium replaces Flannel and the built-in kube-router policy controller. kube-proxy remains enabled.
- Traefik and ServiceLB retain k3s defaults, using ports 80/443; no application route or TLS certificate is provisioned by this milestone. The Kubernetes API is administrative and must not be opened to the public internet.
- IPv4 only. Adding IPv6 requires a corresponding policy and host-firewall review.
- One node has no high availability. Local-path PVCs depend on this host's disk. Cilium network isolation is not VM-level isolation for mutually hostile code.

`versions.env` pins released versions. `config.yaml` is copied to `/etc/rancher/k3s/config.yaml`; private credentials never belong in this repository. `cilium-values.yaml` is passed to the pinned Cilium chart. Installation is operator-controlled, not a repository hook.

## Installation sequence

1. Record CPU, memory, disk, kernel/architecture, routes, Docker network subnets, published ports, firewalld zones/rules, iptables/nftables rules, SELinux mode, NetworkManager configuration and existing service probes. Save host configuration backups outside the repository with root-only permissions.
2. Check pinned k3s/Kubernetes compatibility against the selected Cilium release; verify release artifact checksums. Install k3s and Cilium CLI with the versions in `versions.env`, initially without starting k3s.
3. Keep SELinux enforcing with the k3s SELinux policy package. Configure NetworkManager not to manage CNI interfaces (`cali*`, `flannel*`, `cni*`, `cilium*`, `lxc*`); inspect existing unmanaged settings before editing them. Do not restart the host network connection from the remote session.
4. Install `config.yaml` with mode 0600. Preserve Docker's daemon configuration, networks and firewall rules. Keep firewalld enabled. Add only the forwarding and host rules proven necessary for Cilium; never flush iptables or change the global FORWARD policy to ACCEPT. Record exact added rules and their rollback in the issue.
5. Start k3s; the node can remain NotReady until Cilium is installed. Use the root-owned `/etc/rancher/k3s/k3s.yaml` kubeconfig. Install the pinned chart with the supplied values:

   ```bash
   source infra/k3s/versions.env
   sudo env KUBECONFIG=/etc/rancher/k3s/k3s.yaml \
     /usr/local/bin/cilium install --version "$CILIUM_VERSION" --values infra/k3s/cilium-values.yaml
   sudo env KUBECONFIG=/etc/rancher/k3s/k3s.yaml /usr/local/bin/cilium status --wait
   sudo /usr/local/bin/k3s kubectl get nodes,pods -A -o wide
   ```

6. Inspect the rendered Cilium ConfigMap, especially `allow-localhost: policy`, CNI readiness and selected IPAM range. Do not enable tenant workloads until the tenant policy is applied and realized by Cilium.
7. Render the tenant policy using the current server public IPv4 (OCI NAT address), inspect it, validate server-side and apply it. Keep the rendered host-specific file outside the repository.

   ```bash
   NODE_PUBLIC_IPV4='<current public IPv4>' infra/k3s/render-policy.sh > /tmp/tenant-boundary.yaml
   sudo /usr/local/bin/k3s kubectl apply --dry-run=server -f /tmp/tenant-boundary.yaml
   sudo /usr/local/bin/k3s kubectl apply -f /tmp/tenant-boundary.yaml
   ```

8. Create tenant namespaces from `policies/tenant-namespace.yaml`, with a different name for each user. Change the ServiceAccount namespace in the same document. Apply policies before scheduling workloads and check Cilium endpoint policy realization before granting a user access. Run the verification matrix below.

## Tenant policy contract

The cluster-wide policy selects namespaces labelled `anxious-lookout.io/tenant=true`. Those labels and all network policies are managed only by the platform operator. Users receive neither Kubernetes credentials nor permission to create workloads, edit namespace labels/policies, or select alternate service accounts. The default ServiceAccount has no token mount; workload specs must also set `automountServiceAccountToken: false`.

Both traffic directions default to deny. Allowed paths are:

| Source | Destination | Ports |
| --- | --- | --- |
| Tenant Pod | CoreDNS in `kube-system`, label `k8s-app=kube-dns` | TCP/UDP 53 |
| Tenant Pod | `platform-system`, label `app.kubernetes.io/name=materials-api` | TCP 8080 |
| `platform-system`, label `app.kubernetes.io/name=agent-manager` | Tenant Pod | TCP 8080 |
| Tenant Pod | Public IPv4 destinations except private/reserved ranges and this server's public IP | All |

The API and manager labels/ports are future application contracts; this milestone tests them with temporary fixtures, not an implemented backend. The materials API must authenticate each run and enforce tenant-level data access. Network policies cannot implement row-level authorization.

Host, remote-node and Kubernetes API identities are explicitly denied on tenant egress. Metadata/link-local and this server's public-IP destination are also denied. `allow-localhost=policy` removes the implicit local-host ingress exemption; tenant HTTP/TCP probes from kubelet are therefore not automatically permitted. Use exec probes or deliberately add a narrowly scoped probe policy.

CIDR policy applies to external peers; labelled cluster endpoints are controlled by endpoint selectors. Excluding private CIDRs does not prevent the explicitly allowed CoreDNS/materials API paths. Egress deny rules take precedence over allow rules. Other administrators can still widen non-denied policy paths, so review every additional policy.

Tenant namespace Pod Security Admission is `restricted` at version 1.36. Run as a non-root UID, drop all capabilities, disable privilege escalation and use `RuntimeDefault` seccomp. No hostNetwork/hostPID/hostPath/Docker socket or privileged containers. Tenant storage and runtime credential separation remain application-runtime work, not completed by a network policy.

If the server public IP, internal routes, Docker networks or proxies change, update policy exclusions and repeat the bypass tests. New public addresses exposing internal services must be added to both the CIDR exception and explicit deny list. Outbound internet is deliberately available; a user-accessible external relay can reach any service that is already public. Protect administrative services at their host/cloud firewall and authentication boundary as well.

## Docker, host and firewall coexistence

Cilium policies protect selected Pod endpoints. They do not replace the host firewall or secure existing Docker-published services globally. Keep Docker's rules and its existing service behavior intact. Confirm k3s startup, Cilium reconciliation, firewalld reload and Docker restart do not invalidate forwarding rules; do not perform a disruptive Docker restart without recording its effect and recovery.

Do not make the complete Pod CIDR a trusted host zone as a shortcut: that grants broad access to host services. An inbound Kubernetes API exception, if needed by Cilium bootstrap, should be limited to the actual source and port; public API access remains closed. Public 80/443 exposure also depends on OCI NSG/security-list rules. A Traefik 404 establishes only an ingress listener, not deployed application health or configured HTTPS.

Before uninstalling, follow Cilium's k3s removal instructions: remove Cilium interfaces and associated rules before using the k3s uninstall/killall scripts. Blindly running those scripts with Cilium active can disrupt host networking. Preserve snapshots and separately review rollback; do not remove Docker.

## Backups and recovery

The single-server default datastore is SQLite. A recoverable control-plane backup contains `/var/lib/rancher/k3s/server/db`, the matching `/var/lib/rancher/k3s/server/token`, `/etc/rancher/k3s`, version pins, and any operator host configuration changes. Preserve the server token because it is required to decrypt bootstrap data. Never paste the token, kubeconfig, Secrets or unencrypted archives into issues.

For a consistent offline SQLite backup, stop k3s during a recorded maintenance window, copy the database and token with their modes, then restart k3s even if copying fails. Stopping k3s alone does not quiesce application containers: application data needs its own consistent backup method. local-path files under `/var/lib/rancher/k3s/storage` are separate from the control-plane database. Future PostgreSQL should use database-aware backups; tenant volumes require a coordinated snapshot/archive procedure.

Store archives root-only, checksum them, encrypt before transferring off-host, and establish retention. A local archive is only protection against some operational mistakes, not host/disk loss. No remote backup destination is assumed by this configuration.

Validate a backup by restoring a copy in an isolated container or disposable host, with no live cluster/network access, and checking SQLite integrity and expected Kubernetes resource records. That check is narrower than a full replacement-host recovery. Full recovery requires the same pinned k3s/Cilium versions, restored token/database and volume data, policy reapplication, then the verification matrix. Never restore over the live database to test a backup.

For a host reboot, first save issue progress and a root-owned recovery note, ensure an out-of-band console or independent resume mechanism exists, and record the boot ID. Reconnect after reboot, compare the boot ID and repeat health/service/policy probes. A k3s process restart is not a host reboot test.

## Verification matrix

Use temporary tenant A/B namespaces plus platform fixtures. Record Pod IPs and a successful probe from an authorized source before interpreting a timeout as a policy block. Prefer matching Cilium policy-denied monitor events or counters; "connection refused" alone proves no policy isolation. Remove temporary workloads after recording results.

| Check | Required result |
| --- | --- |
| Node, Cilium agent/operator, DNS, ingress | Ready; values match pins and configuration |
| Tenant DNS UDP/TCP, internet HTTPS and non-HTTPS egress | Allowed |
| Tenant A to B and B to A, direct Pod and ClusterIP | Blocked |
| Tenant to materials API TCP 8080 | Allowed |
| Tenant to materials API unapproved port and unrelated platform Pod | Blocked |
| Manager to tenant TCP 8080; unrelated source to same port | Allowed; blocked respectively |
| Tenant to live Docker container IP and host/private/public address on 8642 | Blocked |
| Tenant to host admin/API ports and Kubernetes service | Blocked |
| Tenant to `169.254.169.254:80` | Blocked before fetching metadata |
| Restricted namespace with privileged/hostNetwork Pod spec | Admission denied (dry-run) |
| Existing Docker container state, internal DNS/HTTPS, service response | Matches pre-install baseline |
| Backup isolated restore | Integrity check plus expected resources |
| After host reboot | New boot ID; cluster, Docker, policy and retained data recovered |

Measure memory/CPU after steady-state and again with two tenant workloads. Do not infer production Codex capacity from an idle infrastructure check. Run representative Codex workloads in the later agent-runtime milestone.

## References

- [Cilium k3s installation](https://docs.cilium.io/en/stable/installation/k3s/)
- [Cilium layer 3 policy and localhost handling](https://docs.cilium.io/en/stable/security/policy/layer3/)
- [Cilium deny policies](https://docs.cilium.io/en/stable/security/policy/deny/)
- [K3s requirements](https://docs.k3s.io/installation/requirements)
- [K3s backup and restore](https://docs.k3s.io/datastore/backup-restore)
- [Docker packet filtering](https://docs.docker.com/engine/network/packet-filtering-firewalls/)

L7 proxying is explicitly disabled in the initial Cilium values. If later adding DNS-aware FQDN or HTTP policy, enable the necessary proxy support, review its resource overhead and repeat the network checks before applying those policies.

## Additional node identity protection and system-Pod firewall access

`render-policy.sh` emits both the tenant boundary and `anxious-lookout-node-protection`. The latter selects every Pod namespace except `kube-system`, including unlabelled namespaces, and explicitly denies host/node/API, metadata and server-public-IP egress without otherwise changing default access. This protects against a former system Pod IP being reused by a tenant. It also applies to `platform-system`: a future agent manager needing Kubernetes API access requires a separately reviewed service-account-scoped exception, which is not granted here.

On this host, firewalld blocks Pod access to the host API/kubelet even when Cilium permits it. A whole-Pod-CIDR input exception was rejected during approval review. The minimal alternative combines the identity guard with exact trusted-system-Pod IP sets. Do not enable those firewall exceptions until Cilium has realized the guard and a non-system Pod's node/API traffic has a matching policy-denied event, including a Pod using a misleading system ServiceAccount name in a non-system namespace.

`host/firewall-sync.py` reads the guard and kube-system Pod inventory, then reconciles two operator-created firewalld `hash:ip` sets in both runtime and permanent views:

- `anxious-k3s-api-clients`: fixed trusted system service accounts, for TCP 6443 only.
- `anxious-k3s-metrics-clients`: metrics-server only, for TCP 10250 only.

Only non-hostNetwork, nondeleting Running/Pending Pods with an IPv4 inside the Pod CIDR qualify. Old IPs are removed before new ones are added. An API/read error retains the current rules without adding entries. The script checks the exact namespace selector shape and unqualified entity deny, but that is not a substitute for live policy-realization checks. Cluster-admin changes to the guard must follow the rollback ordering below.

Install the Python script root-owned mode 0700 at `/usr/local/sbin/anxious-k3s-firewall-sync.py` and the supplied service/timer units under `/etc/systemd/system/`, after container-based script tests and operator review. The timer reconciles every 30 seconds. firewalld owns the two sets and source-set-to-port rich rules; create them explicitly in runtime and permanent configuration and record the exact zone and rules in the issue. No global FORWARD ACCEPT, broad trusted zone or Docker daemon change is part of this mechanism.

Rollback order is mandatory: stop/disable the timer, remove the two owned firewall input allow rules and sets in runtime and permanent views, then remove the node-protection guard if needed. Never remove the guard while IP-based input allowances remain. Keep the tenant boundary active during rollback. Disabling or replacing Cilium likewise requires removing these exceptions first.

## Repeatable validation and online backup commands

The fixture generator and runner never execute automatically. The operator creates `platform-system` if absent, renders `validation/manifests.sh`, applies the output, waits for all fixture Pods and Cilium policy revisions, then runs `validation/run.sh` with `NODE_PRIVATE_IPV4`, `NODE_PUBLIC_IPV4` and `DOCKER_SERVICE_IPV4`. All actual probes run in the disposable Python Pods. Record the resolved `python:3.12-alpine` image digest with results; it is a test fixture, not a production runtime pin.

The runner requires a successful authorized-source control before calling a denied connection a PASS. Host/API/metadata controls are intentionally also protected by the global guard, so those cases report INCONCLUSIVE until matched with Cilium drop evidence. Exit 1 means unexpected behavior; exit 2 means additional policy evidence is needed. Neither is silently treated as a completed matrix. Cleanup deletes only the resources bearing `anxious-lookout.io/validation=issue-1` and the four dedicated validation namespaces; leave an existing `platform-system` intact.

`backup.sh /var/backups/anxious-lookout/k3s` now provides an **online** SQLite backup using the database backup API, without stopping k3s or Docker. It includes the matching token, `cred`, TLS material, config, host configuration and policy exports in a root-only archive. Avoid concurrent token/certificate/encryption-key rotation while backing up. The earlier offline-copy method remains a recovery option, not the script's implementation. The backup directory must be a canonical absolute root-owned path. The script does not delete old snapshots: keep the last seven verified copies plus an encrypted off-host copy before manually pruning.

Run `validation/restore-check.py` in a disposable `python:3.12-alpine` container with `--network none --read-only --tmpfs /tmp` and only the archive mounted at `/backup.tar.gz:ro` plus the checker mounted read-only. It restores the SQLite files into container-local temporary storage and checks integrity, active policy records and recovery inputs. Do not mount the live k3s directory. A subsequent isolated k3s server boot can validate the restored API state more fully; it must use a separate copied data directory, no host network, no published ports and no agent. Archive verification does not establish that application PVCs were backed up or that off-host disaster recovery exists.

On this host, a firewalld reload removed Cilium-managed iptables chains even while `cilium status` remained healthy. After any controlled firewalld reload, restore and verify Cilium's dataplane:

```bash
sudo /usr/local/bin/k3s kubectl -n kube-system rollout restart daemonset/cilium
sudo /usr/local/bin/k3s kubectl -n kube-system rollout status daemonset/cilium --timeout=180s
sudo env KUBECONFIG=/etc/rancher/k3s/k3s.yaml /usr/local/bin/cilium status --wait
```

Then check `CILIUM_FORWARD`/masquerade rules and run DNS, internet, tenant isolation, host/API and Docker regression checks. Agent health alone does not prove those rules exist. Expect a short network interruption during the agent rollout; schedule the change and keep recovery access. This is an explicit operator recovery procedure, not an unverified automatic reload hook. Do not assume changing `FlushAllOnReload` will preserve every Cilium rule without separate testing.
