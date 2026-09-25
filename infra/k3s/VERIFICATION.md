# Execution evidence — 2026-09-25 UTC

Permanent chronological record: [issue #1](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/1), especially [installation and firewall recovery](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/1#issuecomment-5833811335). PM/root performed host changes and container lifecycle operations; backend authored configuration and inspected DNS metadata/logs read-only. Results below distinguish completed checks from pending acceptance.

## Installed and observed

| Item | Result |
| --- | --- |
| Host | ARM64, 2 CPU, approximately 10.6 GiB RAM, UEK Linux 6.12 |
| k3s | `v1.36.4+k3s1`; node `anxious-lookout` Ready; own containerd |
| Cilium | `1.20.2`, CLI `v0.20.1`; agent/operator Ready |
| CNI/controller selection | Flannel and k3s network-policy controller disabled; kube-proxy retained |
| Effective Cilium config | `kube-proxy-replacement=false`, `allow-localhost=policy` checked in ConfigMap; CLI autodetection warning did not reflect the effective kube-proxy setting |
| System workloads | CoreDNS, metrics-server, local-path-provisioner, Traefik and ServiceLB Ready; Traefik install jobs completed |
| Host security | SELinux remains enforcing; k3s SELinux policy installed; secrets encryption enabled; kubeconfig root-only |
| Network ranges | Docker `172.17.0.0/16` and `172.18.0.0/16`; VCN `10.0.0.0/24`; Pod `10.42.0.0/16`; Service `10.43.0.0/16` |
| Host firewall | firewalld active, original FORWARD DROP and Docker rules retained; no blanket Pod-CIDR host-input allowance |
| System API access | Separate exact-IP system Pod sets for TCP 6443 and metrics TCP 10250; guarded root-owned synchronizer and 30-second timer installed |

Both tenant boundary and all-non-system-Pod node guard were applied and accepted by Cilium. Independent review observed live `Policy denied by denylist` events for tenant and unlabelled control Pod SYNs to node API/kubelet ports; a host-side control established those ports were listening. Node protection does not depend on a tenant label or on a Pod retaining its old IP. A misleading `coredns` ServiceAccount name in a tenant namespace was tested in the mocked selector test, not as a live impersonation fixture.

The tenant default ServiceAccount could not read kube-system Secrets (`kubectl auth can-i`: no). Server-side dry-run admission rejected privileged/hostNetwork Pod specifications in the restricted tenant namespace; no privileged test Pod was created.

## Failures found and corrections

- The initial renderer included an apostrophe inside an error parameter expansion that Bash rejected. It was fixed; live initial policy application used an explicitly rendered and inspected template while the fix was made.
- The first firewall synchronizer assumed `PodList`; kubectl returned generic `List`. The script failed before mutations. It now accepts either list shape and validates each Pod kind. Independent reviewer container tests passed 32 assertions.
- System Pods initially could not reach node API/kubelet through firewalld. Broad Pod-CIDR and bare recycled-Pod-IP allowances were rejected during approval review. Temporary individual rules were removed. The deployed solution combines the verified namespace identity guard and scoped exact-IP system sets.
- A firewalld reload removed Cilium's iptables forwarding/NAT chains while agent health still appeared normal. A Cilium DaemonSet rollout restored the chains, after which tenant DNS resolution and HTTPS returned success/HTTP 200. No additional forwarding policy was applied. Future controlled reloads require this dataplane check and recovery procedure.

## Existing Docker regression

The existing `hermes` container remained healthy, its `StartedAt` did not change, and internal DNS plus HTTPS returned success/200. Docker was not restarted. Its exposed port 8642 already reset the tested HTTP request before installation; that pre-existing response is not reported as an application health success.

## Backup and restore

PM created `/var/backups/anxious-lookout/k3s/control-plane-20260925T140916Z.tar.gz` through the online SQLite backup API. The root-only archive includes datastore, server token, TLS/credential/encryption material, host configuration and policy snapshots. The backup script never prints credentials.

The isolated Python archive verifier passed SQLite integrity, nonempty Kine data, active tenant/node policy records and presence of required recovery files. The stronger disposable k3s restore also passed: the isolated API became ready, both CCNPs and four expected namespaces were present, and the decrypted `k3s-serving` Secret data hash matched the live cluster. The test ran without host networking, published ports, an agent or privileged mode, using copied data only; cleanup removed the test container, anonymous volumes and copy. This is a control-plane backup, not a PostgreSQL or tenant-volume backup, and no off-host backup transfer has been claimed.
The first API checker run falsely reported readiness failure because `k3s kubectl` was not a valid invocation inside the official k3s container image, although direct `kubectl` showed the API ready. After that run exited and cleaned up, container commands were corrected to `kubectl`; the complete rerun exited 0. Host invocations retain `/usr/local/bin/k3s kubectl`.

## Resource sample

PM's 14:12 UTC sample, with disposable validation workloads present:

| Metric | Observed |
| --- | --- |
| Node CPU (`kubectl top`) | 868m, 43% |
| Node memory (`kubectl top`) | 5,186 MiB, 47% |
| Cilium / operator memory | 147 / 53 MiB |
| CoreDNS / metrics-server memory | 15 / 22 MiB |
| Traefik / local-path memory | 22 / 10 MiB |
| Host available memory | 6,072 MiB |
| Root disk available | Approximately 125 GiB |

This is a point-in-time infrastructure sample, not a representative Codex capacity benchmark.

## Remaining acceptance evidence

- Complete the full positive/negative matrix, including DNS UDP/TCP, materials API direct/Service, tenant A/B direct/Service, unauthorized ports/platform destinations and Docker/metadata/public-IP paths. Timeouts without a positive control or correlated drop event remain inconclusive.
- Execute a real host reboot, establish a changed boot ID and re-run readiness, dataplane, policy, retained-state and Docker checks. `REBOOT.md` and the one-time collector preserve the procedure/evidence; creating those files is not reboot verification.
- Record final script syntax checks, new checker runtime results and independent final review before completing the milestone.
