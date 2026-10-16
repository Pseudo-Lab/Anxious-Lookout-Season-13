# Host reboot acceptance under issue-13 decision C

This is an operator checklist, not reboot authorization or evidence of success. Historical 2026-09-25 results remain in `VERIFICATION.md`.

## Before reboot

1. Finish the reviewed `DEFAULT-ALLOW.md` transition and record its SHA/results in issue #13. Save private policy snapshots, firewall configuration and Docker baseline.
2. Run `backup.sh` and an isolated restore check; keep credentials/archives private and ensure independent console/reconnect access.
3. Verify `systemctl is-enabled k3s docker firewalld` and `systemctl is-active k3s docker firewalld`, node/Cilium/DNS readiness and API health. The retired firewall-sync units must not be reinstalled or enabled. No old issue-1 fixture Pods are required.
4. Save `/proc/sys/kernel/random/boot_id` to root-owned mode0600 `/var/lib/anxious-lookout/reboot-check/before-boot-id` (directory mode0700). Record checkout/SHA, issue URL, archive/checksum and pending commands in a private recovery note.
5. Only the authorized operator initiates `systemctl reboot`. Session disconnection is not proof of recovery.

## After reconnect

Compare boot IDs, verify units/node/Cilium readiness and Cilium iptables forwarding/NAT chains. Follow README recovery if necessary and record manual intervention. Run `DEFAULT-ALLOW.md` acceptance again: removed guards remain absent, runner/trial policy specs match snapshots, Pod CIDR TCP6443 exists in both firewall views, retired ipsets are empty and unreferenced, API-Pod API access and actual runner flow work. Record metrics scraping separately (no 10250 replacement grant). Verify Docker health, internal DNS/HTTPS and external response against baseline. Verify backup checksum and report PVC persistence limitations. A failed probe cannot be erased by later success without recording recovery.

## Optional one-reboot collector

Install `host/postboot-check.sh` root-owned mode0700 at `/usr/local/sbin/anxious-k3s-postboot-check.sh` and its service under `/etc/systemd/system/`. Replace any installed previous collector; no old `validation/run.sh` installation is needed. Create root-owned mode0600 `/etc/anxious-lookout/postboot.env` from the example with actual Docker container/network values. Save the before-boot-ID file.

Run `systemctl daemon-reload`, then enable the one-shot service **without starting it**. After reboot it waits up to five minutes for base services, checks Cilium chains, current policy presence/absence, firewall configuration and Docker DNS/HTTPS. It neither mutates policies nor starts a monitor nor invokes the obsolete issue-1 matrix. It does not require metrics-server availability because metrics access may be degraded under this decision.

Inspect `systemctl status anxious-k3s-postboot-check.service` and root-only `/var/log/anxious-k3s-postboot.log`. Exit 1 means baseline failure. Exit 2 means baseline passed but API/runner flow, policy-spec comparison and external Docker checks remain pending; it is intentionally not successful reboot acceptance. Disable the one-shot after evidence collection and record the final checks in the issue. No successful reboot is implied by container tests.
