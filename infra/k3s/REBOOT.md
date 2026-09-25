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

## Optional one-reboot evidence service

To retain evidence if the active agent session ends, install `host/postboot-check.sh` as root-owned mode 0700 at `/usr/local/sbin/anxious-k3s-postboot-check.sh`, and copy `validation/run.sh` plus `validation/probe.py` to root-owned `/usr/local/lib/anxious-k3s-validation/`. Install `host/anxious-k3s-postboot-check.service` under `/etc/systemd/system/` and create root-owned mode 0600 `/etc/anxious-lookout/postboot.env` with the current `NODE_PRIVATE_IPV4`, `NODE_PUBLIC_IPV4`, original `DOCKER_CONTAINER` name and `DOCKER_NETWORK_NAME`. Do not put credentials in it. Preserve the fixtures and before-boot-ID file.

Run `systemctl daemon-reload`, then **enable without starting** the one-shot unit. Its next-boot run waits up to five minutes for cluster recovery, checks the unchanged policy/firewall/Docker state, executes container probes and captures Cilium drop events into root-only `/var/log/anxious-k3s-postboot.log`. It never creates fixtures, changes policies, reloads firewalld or restarts Cilium. Exit 2 explicitly means a human/agent must correlate drop evidence; it is not automatic success. The collector also checks DNS/HTTPS inside the existing Docker service; its external service baseline still needs the normal follow-up check.

After reconnect, inspect `systemctl status anxious-k3s-postboot-check.service` and the private log; record reviewed results in the issue. Disable the one-time unit after evidence collection. A failed or pending check requires follow-up and must not be reported as a passed reboot.

`host/postboot.env.example` provides the environment-file fields; its `198.51.100.10` address is a documentation placeholder, not a usable server address. Replace it before installation. In the current session the agent orchestration itself runs on this host and will terminate on reboot; retain the independent recovery instructions and arrange a subsequent connection before initiating the restart. No completed host reboot is implied by passing the isolated restore.
