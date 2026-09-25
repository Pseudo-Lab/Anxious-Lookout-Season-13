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
     cilium install --version "$CILIUM_VERSION" --values infra/k3s/cilium-values.yaml
   sudo env KUBECONFIG=/etc/rancher/k3s/k3s.yaml cilium status --wait
   sudo k3s kubectl get nodes,pods -A -o wide
   ```

6. Inspect the rendered Cilium ConfigMap, especially `allow-localhost: policy`, CNI readiness and selected IPAM range. Do not enable tenant workloads until the tenant policy is applied and realized by Cilium.
7. Render the tenant policy using the current server public IPv4 (OCI NAT address), inspect it, validate server-side and apply it. Keep the rendered host-specific file outside the repository.

   ```bash
   NODE_PUBLIC_IPV4='<current public IPv4>' infra/k3s/render-policy.sh > /tmp/tenant-boundary.yaml
   sudo k3s kubectl apply --dry-run=server -f /tmp/tenant-boundary.yaml
   sudo k3s kubectl apply -f /tmp/tenant-boundary.yaml
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
