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

**Runner stop observer (until #61 retires it).** The observer's proof is pinned to the boot ID in `/etc/anxious-lookout/issue16/observer/inputs.json` (`host.boot_id`). After a reboot that value is stale. The observer then refuses every stop with `reconciliation_required`, and a stopping runner stays `runner_unreachable` (#66). Refreshing the input only fixes stops that are **not yet anchored**. A stop anchored before the reboot keeps its old `(node_name, node_uid, boot_id)`, and `Observer.anchor` keeps refusing it whatever the input says. Before the issue16 timers resume, check these read-only prerequisites. All of them must hold before anything changes:

```sh
cat /proc/sys/kernel/random/boot_id
k3s kubectl get node anxious-lookout -o jsonpath='{.metadata.uid} {.status.nodeInfo.bootID}{"\n"}'   # bootID equals the line above
readlink /proc/1/ns/pid /proc/1/ns/cgroup
python3 -c 'import json,sys; h=json.load(open(sys.argv[1]))["host"]; print(h["boot_id"], h["node_uid"], h["host_pid_namespace"], h["host_cgroup_namespace"])' /etc/anxious-lookout/issue16/observer/inputs.json
```

```sql
-- database hosting, read-only: every open stop and its anchor
SELECT owner_id, generation, pod_uid, node_uid, boot_id FROM runner_termination.stops WHERE completed_at IS NULL;
```

1. The Node API `bootID` equals `/proc/sys/kernel/random/boot_id`, and the Node UID equals the input `node_uid`.
2. The PID/cgroup namespace links equal `host_pid_namespace`/`host_cgroup_namespace`.
3. Every open stop has `boot_id` NULL (not anchored) or equal to the current boot.

If any prerequisite fails, stop: keep the timers stopped (or stopped again), change nothing, and request a separate reviewed recovery in an issue. This applies in particular to an open stop anchored to a previous boot. Never edit an existing stop row, set the input back to an old boot to match it, delete a runner Pod, or edit the registry.

If all of them hold and the input `boot_id` differs from the current boot, run steps 1, 2 and 5 of the [#66 runbook](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/66#issuecomment-6408328794):
1. stop the three issue16 timers and services;
2. replace only the input's `host.boot_id` (and `bootId` in `/private-rollout/values.json`), keeping a `.pre66`-style backup;
3. restart the timers.

This changes only the observer's own input, never a stop row.

## Optional one-reboot collector

Install `host/postboot-check.sh` root-owned mode0700 at `/usr/local/sbin/anxious-k3s-postboot-check.sh` and its service under `/etc/systemd/system/`. Replace any installed previous collector; no old `validation/run.sh` installation is needed. Create root-owned mode0600 `/etc/anxious-lookout/postboot.env` from the example with actual Docker container/network values. Save the before-boot-ID file.

Run `systemctl daemon-reload`, then enable the one-shot service **without starting it**. After reboot it waits up to five minutes for base services, checks Cilium chains, current policy presence/absence, firewall configuration and Docker DNS/HTTPS. It neither mutates policies nor starts a monitor nor invokes the obsolete issue-1 matrix. It does not require metrics-server availability because metrics access may be degraded under this decision.

Inspect `systemctl status anxious-k3s-postboot-check.service` and root-only `/var/log/anxious-k3s-postboot.log`. Exit 1 means baseline failure. Exit 2 means baseline passed but API/runner flow, policy-spec comparison and external Docker checks remain pending; it is intentionally not successful reboot acceptance. Disable the one-shot after evidence collection and record the final checks in the issue. No successful reboot is implied by container tests.
