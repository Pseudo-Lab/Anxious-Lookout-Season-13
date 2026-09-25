# Execution evidence — 2026-09-25 UTC

Permanent chronological record: [issue #1](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/1), especially [installation and firewall recovery](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/1#issuecomment-5833811335). PM/root performed host changes and container lifecycle operations; backend authored configuration and inspected DNS metadata/logs read-only. Results below distinguish the initial installation checks, actual reboot verification and remaining limitations.

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

During installation, the existing `hermes` container remained healthy, its `StartedAt` did not change, and internal DNS plus HTTPS returned success/200. Docker was not restarted during installation. Its exposed port 8642 already reset the tested HTTP request before installation; that pre-existing response is not reported as an application health success. After the later host reboot, the container automatically restarted at `2026-09-25T14:34:36.142048522Z`, became healthy, and again passed internal DNS and HTTPS 200 checks.

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

## Actual host reboot verification

The boot ID changed from `572ff9a7-c364-45bc-9c37-6ccb51bcda56` to `e90e21db-5eca-4f3f-8c8e-47b25198cadb`. The root-owned one-time collector began automatically at 14:34:44 UTC. k3s, Docker, firewalld and the firewall synchronizer timer recovered without postboot configuration changes, firewall reloads or Cilium restarts. The node and expected running system/validation Pods were Ready, Cilium forwarding/NAT chains were present, and both CCNPs remained valid.

The timer automatically replaced the old system-Pod IP entries after addresses changed: its 14:35:28 UTC run reported 20 changes. Runtime and permanent API sets matched the current system Pods: `10.42.0.35`, `.93`, `.137`, `.246`; the metrics set contained only `10.42.0.246`. Subsequent synchronizer runs succeeded. Secrets encryption remained enabled with matching hashes, and both retained backup archive checksums verified.

The first automatic matrix reported **14 passed, 1 failed, 8 requiring policy-drop correlation**, and the collector exited failed. The failure was one tenant DNS UDP timeout. Its exact cause is unproven. Later PM probes returned three valid UDP replies; an independent reviewer obtained five valid replies out of five, plus valid replies for direct CoreDNS UDP, tenant B DNS UDP and tenant A DNS TCP. The reviewer also repeated allowed materials/manager access and denied cross-tenant/forbidden-port probes. These follow-up checks required no infrastructure remediation; the first failed run remains in the private log and is not relabelled as a pass.

The reviewer correlated all eight original inconclusive denials to contemporaneous Cilium policy-drop events from the current tenant identity/IP: Docker direct TCP 8642; host TCP 22, 6443, 10250 and 8642; own public IP TCP 8642; OCI metadata TCP 80; and Kubernetes Service TCP 443 translated to host TCP 6443. Events also showed the node guard denying the unlabelled control identity. Thus policy enforcement is supported by packet-drop evidence rather than timeout alone. Independent operational acceptance was approved; final documentation review is tracked on PR #2.

PM reran the unchanged collector at 14:48:34 UTC. The repeat matrix reported **15 passed, 0 failed, 8 requiring policy-drop correlation**. Its exit status was 2, the collector's intentional pending-review result, rather than an automatic service success. The reviewer independently correlated all eight denials in this second run to matching Cilium drop events, completing all 23 matrix cases. The original run and repeat remain in the appended root-only log.

After evidence review, PM disabled the one-time collector and reset only that unit's failed state to inactive; its original exit 1 and repeat exit 2 remain documented and the mode-0600 log was retained. Cleanup removed six labelled validation Pods, six validation Services and the four dedicated tenant/control/private namespaces. No labelled fixtures remained; `platform-system` was preserved. System workloads remained Ready, both CCNPs valid and Docker healthy. No policy or network configuration was changed during these postboot checks or cleanup.

The pre-reboot installation of the one-time collector passed `systemd-analyze verify`; its actual-value environment file, installed validation assets and saved boot ID are root-only. Future backups include those optional recovery artifacts; the first 14:09 archive predates their installation. This verification covers one real reboot and control-plane recovery. It does not establish packet-loss rates, repeated reboot reliability, application/PVC persistence or an off-host backup copy.

## Completed independent network matrix

The reviewer repeated the probes after Cilium dataplane repair at 14:10–14:16 UTC; [the full issue record](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/1#issuecomment-5833890927) contains the results and caveats.

| Probe | Result |
| --- | --- |
| Tenant to DNS UDP/TCP 53 | Valid replies |
| Tenant to external HTTPS / TCP 80 | Certificate verification and HTTP 200 / connected |
| Tenant to materials API TCP 8080, Pod and Service addresses | Allowed |
| Manager to tenant A/B TCP 8080 | Allowed |
| Tenant A/B to each other, direct and Service addresses | Blocked |
| Materials API forbidden TCP 9090 / unrelated private Pod | Blocked; control Pod connected |
| Unapproved control Pod to tenant | Blocked |
| Tenant and unlabelled Pod to node TCP 6443/10250 | Blocked; host controls connected |
| Tenant to Kubernetes Service TCP 443 | Blocked after translation to node TCP 6443 |
| Tenant to Docker direct / node / public address TCP 8642 | Cilium policy denied |
| Tenant to OCI metadata TCP 80 | Cilium policy denied; no metadata body read |

Negative results were correlated with the target SYN's `Policy denied` or `Policy denied by denylist` events rather than inferred from timeouts alone. The Docker port 8642 host control was refused, consistent with the pre-existing failing HTTP endpoint; only the network-policy denial is claimed for that path. This table records the initial network matrix; actual reboot and subsequent repeat evidence are recorded above.

The postboot service-state regression test passed in a PM-managed `bash:5.2` Docker container with network disabled and repository input mounted read-only. It exercises the actual collector helper and confirms that every required inactive unit prevents startup success. This corrects the earlier aggregate `systemctl is-active` behavior; it does not execute the reboot collector or simulate a host reboot.
