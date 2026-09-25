# Host reboot acceptance procedure

A successful process restart is not a server reboot. This procedure is an operator checklist; it neither reboots automatically nor claims the check has passed. Keep the issue current before disconnecting the active session.

## Before reboot

1. Push current infrastructure changes and post the current validation results, remaining checks and backup checksum to issue #1. Keep credentials and archives private.
2. Complete `backup.sh` and an isolated restore check. Confirm the backup archive exists outside the working checkout and is root-only. It still resides on this host unless separately transferred.
3. Ensure OCI console/serial recovery access or another independent reconnect path is available. Do not depend on the current agent process surviving reboot.
4. Confirm boot-enabled units and retain the dedicated validation fixtures until after recovery:

   ```bash
   sudo systemctl is-enabled k3s docker firewalld anxious-k3s-firewall-sync.timer
   sudo systemctl is-active k3s docker firewalld anxious-k3s-firewall-sync.timer
   sudo /usr/local/bin/k3s kubectl get nodes
   sudo /usr/local/bin/k3s kubectl get pods -A -o wide
   sudo env KUBECONFIG=/etc/rancher/k3s/k3s.yaml /usr/local/bin/cilium status --wait
   ```

5. Save the old boot ID and a root-only recovery note containing the checkout path, commit SHA, issue URL, archive path and pending commands. It should point to this document and the firewall recovery section in `README.md`.

   ```bash
   sudo install -d -m 0700 /var/lib/anxious-lookout/reboot-check
   sudo sh -c 'umask 077; cat /proc/sys/kernel/random/boot_id > /var/lib/anxious-lookout/reboot-check/before-boot-id'
   ```

6. The authorized operator initiates the reboot when ready with `sudo systemctl reboot`. Record the initiation time in the issue first. Session disconnection is expected and is not evidence of successful recovery.

## After reconnect

1. Compare `/proc/sys/kernel/random/boot_id` with `/var/lib/anxious-lookout/reboot-check/before-boot-id`. They must differ. Record the UTC time and new boot ID in the private recovery note.
2. Repeat unit status, node/Pod readiness and Cilium status checks above. The exact system-Pod IP sets must refresh automatically when system Pod addresses change:

   ```bash
   sudo systemctl status anxious-k3s-firewall-sync.timer --no-pager
   sudo journalctl -u anxious-k3s-firewall-sync.service -b --no-pager -n 30
   sudo firewall-cmd --ipset=anxious-k3s-api-clients --get-entries
   sudo firewall-cmd --ipset=anxious-k3s-metrics-clients --get-entries
   sudo /usr/local/bin/k3s kubectl get ccnp anxious-lookout-node-protection anxious-lookout-tenant-boundary
   ```

3. Inspect Cilium forwarding/NAT chains, not only the agent's Ready state. If a boot-time firewall reload removed them, follow the Cilium rollout recovery in `README.md`, record that manual recovery was necessary, and do not call unattended recovery complete until the ordering issue is resolved.
4. Repeat DNS over UDP/TCP, TLS internet access, materials API allow, tenant A/B isolation, unrelated platform denial, node/Docker/API/metadata/public-address denial with policy-drop evidence. Use the retained fixtures and `validation/run.sh`; set the current Docker container IP rather than assuming it stayed fixed.
5. Compare the existing Docker service's health, DNS/HTTPS checks and externally exposed response with the saved pre-install baseline. A pre-existing failing HTTP endpoint need not become healthy, but it must not be represented as a new infrastructure success.
6. Confirm the backup file/checksum remains present and the policies survived. If application/PVC persistence is out of scope, state that explicitly rather than claiming a data-volume restore.
7. Update the issue and `VERIFICATION.md` with the new boot ID comparison result, all checks, any manual intervention and remaining limitations. Then remove only the dedicated validation fixtures. Do not delete an existing `platform-system` namespace during cleanup.
