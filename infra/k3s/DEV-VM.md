# x86_64 development VM

Issue [#48](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/48). Production stays on the OCI aarch64 node. Development and procedure rehearsal run in one KVM guest on the x86_64 development server, so k3s, Cilium, firewalld and SELinux never touch that server's own Docker containers, firewall or ports. This document specifies the guest; it does not authorize host package installation, which is a human/PM action.

## Difference from the production node

Architecture is the only intended difference. Record the actual values in the issue after creation.

| Item | Production (OCI) | Development VM |
| --- | --- | --- |
| Architecture | aarch64 | **x86_64** |
| Distribution | Oracle Linux Server 9.7 | Oracle Linux Server 9.7 |
| Kernel | UEK `6.12.0-204.92.4.2.el9uek` | Same UEK 6.12 NVR when published for x86_64; otherwise record the exact NVR used |
| SELinux / firewalld / NetworkManager | enforcing / active / active | enforcing / active / active |
| k3s / Cilium / Cilium CLI | `versions.env` | `versions.env` (same file, unchanged) |
| `/etc/rancher/k3s/config.yaml` | `config.yaml` | `config.yaml` (same file, node name `anxious-lookout`) |
| Pod / Service CIDR | `10.42.0.0/16` / `10.43.0.0/16` | same |
| Node address / hostname | `10.0.0.107` / `simple` | `10.0.0.107` / `simple` (guest-private, see Network) |
| CPU / memory / disk | 2 / ~10.6 GiB / existing | 4 vCPU / 8 GiB / 60 GiB qcow2 initial value |
| Runner / API / web images | linux/arm64 | linux/amd64 built from the same source (`backend/runner/install_codex.py` amd64 pin set) |

Kernel patch level can drift between the two even within the UEK 6.12 line. Compare kernel NVRs first when a Cilium or dataplane symptom appears in only one of them.

The guest deliberately reuses the production node identity (`anxious-lookout`, `simple`, `10.0.0.107`) so that operational documents and their host guards run unchanged. To keep the two apart for humans, set `/etc/motd` and the shell prompt to `DEV-VM x86_64` and name the libvirt domain `anxious-dev`. Never copy production kubeconfig, k3s token, backups or Secrets into the guest; create fresh ones.

## Host prerequisites (human, once)

The development server is Ubuntu 24.04 with `/dev/kvm` present and libvirt not installed. Installing it is a host change and needs approval:

```sh
sudo apt install --no-install-recommends qemu-system-x86 qemu-utils libvirt-daemon-system virtinst
```

`passt` is optional. Install it only to try the passt backend, which does not work on this host (see Network).

`libvirt-daemon-system` defines a `default` NAT network on `192.168.122.0/24`. That range lies inside an existing Docker network (`192.168.112.0/20`), and host Docker already occupies `172.17.0.0/16`–`172.31.0.0/16` and most `192.168.0.0/16` /20 blocks. Do not use, start or autostart the libvirt default network:

```sh
sudo virsh net-destroy default 2>/dev/null || true
sudo virsh net-autostart default --disable
```

**Ubuntu 24.04, libvirt 10.0.0, passt backend: AppArmor override.**

This override is needed only when the passt backend is chosen. The default SLIRP backend (see Network) does not need it. The development server still has the 2026-10-25 application; it is harmless with SLIRP and stays in place.

- **Why it is needed:** Ubuntu's `libvirt-daemon-system` 10.0.0 (checked with `10.0.0-2ubuntu8.19`) ships an `abstractions/libvirt-qemu` that grants only `/usr/bin/passt Cx -> passt,`. The per-VM profile `libvirt-<UUID>` is missing two permissions:
  - `rm` on `/usr/bin/passt`, which an exec of the PIE binary needs
  - access to the passt socket that QEMU connects to
- **Symptom:** `virsh start` fails with `Child process (passt …) unexpected fatal signal 11`. The real failure is `execve("/usr/bin/passt") = -1 EACCES`, an AppArmor `file_mmap` denial. The signal 11 is libvirt's error-reporting child dying afterwards. Confirm the denial with `journalctl -k | grep DENIED`.
- **Fix:** add the two rules to the local override, which the abstraction already includes with `include if exists`. Do not edit the packaged file. The override survives package updates.

```sh
f=/etc/apparmor.d/local/abstractions/libvirt-qemu
sudo install -d -m 0755 /etc/apparmor.d/local/abstractions /var/backups/issue48-apparmor
# Keep whatever the file already holds (rules for other guests); back it up outside apparmor.d.
if sudo test -e "$f"; then sudo cp -a "$f" "/var/backups/issue48-apparmor/libvirt-qemu.$(date -u +%Y%m%dT%H%M%SZ)"; fi
# Append the marked block once; never rewrite existing lines.
sudo grep -qx '# BEGIN issue #48 passt' "$f" 2>/dev/null || sudo tee -a "$f" >/dev/null <<'EOF'
# BEGIN issue #48 passt
# libvirt 10.0.0 abstraction grants only Cx; exec of PIE passt also needs rm
/usr/bin/passt rm,
owner @{run}/libvirt/qemu/passt/* rw,
# END issue #48 passt
EOF
sudo cat "$f"   # check: earlier content unchanged, one marked block at the end
```

- **When it applies:** libvirt builds the per-VM profile when the guest starts, so the rules take effect at the next `virsh start`.
- **Expected after start:**
  - `journalctl -k | grep DENIED` shows no new denial that names `/usr/bin/passt` or `/run/libvirt/qemu/passt/`.
  - Other denials seen on the development server are unrelated and harmless: libvirtd `ptrace` reads, and `signal` denials when the guest stops.
  - `anxious-dev` running, passt under its child profile in enforce mode, and `127.0.0.1:22107` listening are necessary but not sufficient. The network works only when the guest gets `10.0.0.107` by DHCP (`ip -4 address` on the console) and `ssh -p 22107 opc@127.0.0.1` reaches sshd.
- **Rollback:** remove only the marked block and keep every other line, then stop and start the guest:

  ```sh
  sudo sed -i '/^# BEGIN issue #48 passt$/,/^# END issue #48 passt$/d' /etc/apparmor.d/local/abstractions/libvirt-qemu
  ```

  If the file did not exist before (no backup under `/var/backups/issue48-apparmor/`) and is now empty, delete it.
- **First application on the development server (2026-10-25):** it was made before the markers existed. It consists of three lines: `# issue #48: libvirt 10.0.0 abstraction grants only Cx; exec of PIE passt also needs rm`, `/usr/bin/passt rm,` and `owner @{run}/libvirt/qemu/passt/* rw,`. Roll it back by removing exactly those three lines, or replace them with the marked block above so the standard rollback applies.
- **Not needed** on hosts whose libvirt abstraction already grants these rules, or with a non-passt network backend.

Take the host baseline (see Isolation checks) **after** these packages are installed and **before** the guest is created.

## Network

Recommended: QEMU's built-in user-mode networking (SLIRP), declared through libvirt's QEMU command-line passthrough. It runs inside the QEMU process and creates no host bridge, no host iptables/nft rule, no extra process and no listening port except one loopback forward. So the host Docker networks, `central-traefik` (`:12345`) and other projects are unaffected. Host bridging is not available: the host uplink is Wi-Fi (`wlp4s0`). The passt backend does not work on this host (below).

libvirt 10.0's `<portForward>` element is passt-only. So the guest NIC is declared as a single QEMU `-netdev user` with `hostfwd`, and the domain has **no** `<interface>` element. Remove any existing one; otherwise libvirt adds a second NIC. Verified on 2026-10-25 (issue #48 R4):

```xml
<domain type='kvm' xmlns:qemu='http://libvirt.org/schemas/domain/qemu/1.0'>
  <!-- … no <interface> element … -->
  <qemu:commandline>
    <qemu:arg value='-netdev'/>
    <qemu:arg value='user,id=devnet,net=10.0.0.0/24,dhcpstart=10.0.0.107,hostfwd=tcp:127.0.0.1:22107-10.0.0.107:22'/>
    <qemu:arg value='-device'/>
    <qemu:arg value='virtio-net-pci,netdev=devnet,mac=52:54:00:e3:56:b1,bus=pcie.0,addr=0x3'/>
  </qemu:commandline>
</domain>
```

- The `xmlns:qemu` declaration must be on the `<domain>` element, or libvirt drops the passthrough.
- The device sits on the q35 root bus `pcie.0`, slot `0x3`, which was free on `anxious-dev`. Check `virsh dumpxml anxious-dev` for a free root slot on another guest. A libvirt-managed bridge such as `pci.6` cannot be used: libvirt removes empty bridges, and the guest then fails before boot.
- `-set netdev.hostnet0.hostfwd=…` on a libvirt `<interface type='user'>` does not work either: it fails before boot with `there is no netdev hostnet0 defined`.
- The MAC address is the guest's existing one. Keep it stable so NetworkManager keeps its connection profile.
- With `<qemu:commandline>`, libvirt marks the domain as tainted (`custom-argv`) in its log and in `virsh dominfo`. That is accepted for the development VM only.

Measured guest network (NIC `enp0s3`):

| Item | Value |
| --- | --- |
| Address | `10.0.0.107/24` by SLIRP DHCP (`dhcpstart`), NetworkManager `dynamic` |
| Default route | `default via 10.0.0.2 dev enp0s3 proto dhcp` |
| DNS | `nameserver 10.0.0.3` (SLIRP resolver, which forwards to the host's resolver) |
| Outbound | HTTPS works, for example `https://get.k3s.io` returns HTTP 200, and dnf reaches the Oracle Linux repositories |
| Host side | only QEMU listening on `127.0.0.1:22107` (backlog 1; no other new listener) |

- Guest address `10.0.0.107/24` mirrors the production VCN address and node identity, so host guards (`initialize-rehearsal.sh`) and the API certificate SANs run unchanged. `10.0.0.0/24` exists only inside QEMU's SLIRP and is not used anywhere on the host. SLIRP keeps the gateway `.2` and DNS `.3` inside the subnet, unlike passt's off-link `192.168.0.1`.
- **Inbound:** SLIRP gives no inbound path except `hostfwd`.
- **Outbound to the host:** QEMU maps connections to the gateway address `10.0.0.2` onto the host's `127.0.0.1`. Left as is, the guest could reach every loopback-bound service on the development server: other projects' containers published on `127.0.0.1`, the anxious hosting ports and so on. Close that path inside the guest for **new** connections only.
  - SSH must keep working: the hostfwd SSH session's guest-side peer is `10.0.0.2` (`SSH_CONNECTION: 10.0.0.2 <port> 10.0.0.107 22`), so its replies go to `10.0.0.2`. A plain `-d 10.0.0.2 -j REJECT` cuts SSH (seen in R4).
  - Routing via `10.0.0.2` and DNS on `10.0.0.3` are unaffected.

  **Guest shell (OUTPUT), verified in R4 2026-10-25.** Add the rule to the runtime and permanent configurations separately. **Never run `firewall-cmd --reload`:** on this platform a reload removed Cilium's iptables chains (`README.md`).

  ```sh
  sudo firewall-cmd --direct --add-rule ipv4 filter OUTPUT 0 -d 10.0.0.2/32 -m conntrack --ctstate NEW -j REJECT
  sudo firewall-cmd --permanent --direct --add-rule ipv4 filter OUTPUT 0 -d 10.0.0.2/32 -m conntrack --ctstate NEW -j REJECT
  ```

  Verify with a positive control. Before adding the rule, `curl -sS -m 5 -o /dev/null -w '%{http_code}\n' http://10.0.0.2:5001/v2/` returns `200` (the host's existing registry, read only). Do not use a port without first confirming that it is open. After adding the rule:
  - the same request fails (curl exit 7)
  - an existing SSH session stays up
  - a new `ssh -p 22107 opc@127.0.0.1` succeeds
  - `curl -sSI https://get.k3s.io` returns 200
  - `ip route` still shows `default via 10.0.0.2`
  - name resolution through `10.0.0.3` works

  The rule was added after snapshot `base-os`. After reverting to `base-os`, add it again.

  **Pods and containers (FORWARD): draft, not yet verified by review.** Pod (Cilium) and Docker container traffic leaves through FORWARD, not OUTPUT. A firewalld direct rule in iptables `FORWARD` does not reliably catch it, because Cilium (`CILIUM_FORWARD`) and Docker (`DOCKER-USER`, `DOCKER-FORWARD`) insert ACCEPT jumps at the top of that chain, and the result depends on start order. The draft uses a separate nftables base chain instead. It runs at hook priority `-10`, before the iptables-nft filter chains at priority 0, and its reject is final whatever later chains accept. firewalld does not manage or flush foreign nftables tables. It needs no reload, and it can be loaded before or after k3s:

  ```sh
  sudo tee /etc/nftables/devvm-hostguard.nft >/dev/null <<'EOF'
  # issue #48, development VM only: workloads must not open connections to the
  # SLIRP gateway, which QEMU maps to the host's 127.0.0.1. Replies stay allowed.
  table inet devvm_hostguard
  delete table inet devvm_hostguard
  table inet devvm_hostguard {
  	chain forward {
  		type filter hook forward priority -10; policy accept;
  		ip daddr 10.0.0.2 ct state new counter reject with icmpx type admin-prohibited
  	}
  }
  EOF
  sudo tee /etc/systemd/system/devvm-hostguard.service >/dev/null <<'EOF'
  [Unit]
  Description=DEV-VM only (issue #48): refuse new workload connections to the SLIRP host gateway
  Before=docker.service k3s.service

  [Service]
  Type=oneshot
  RemainAfterExit=yes
  ExecStart=/usr/sbin/nft -f /etc/nftables/devvm-hostguard.nft
  ExecStop=/usr/sbin/nft delete table inet devvm_hostguard

  [Install]
  WantedBy=multi-user.target
  EOF
  sudo systemctl daemon-reload && sudo systemctl enable --now devvm-hostguard.service
  ```

  The file's first two lines make reloading idempotent. To verify, run the 5001 positive control from a Pod (for example a one-off `curlimages/curl` Pod) and from a Docker container (`docker run --rm`, default bridge), before and after enabling the unit. Also check:
  - `nft list table inet devvm_hostguard` counters increase
  - Pod DNS, Pod HTTPS to `https://get.k3s.io` and Cilium health stay unchanged
  - the chain is still present after `systemctl restart k3s` and `systemctl restart docker`

  Rollback: `sudo systemctl disable --now devvm-hostguard.service`, then delete the two files and `daemon-reload`.

- Never add `restrict=on` to the netdev: it also cuts the guest off from the internet.
- SSH reaches the guest only from the host loopback: `ssh -p 22107 opc@127.0.0.1`.
- Ingress tests (Traefik 80/443 inside the guest) run from inside the guest with `curl --resolve`, or through an additional loopback-only forward on a free high port. Do not forward 80/443 or bind `0.0.0.0`.
- Public origin for `infra/hosting/render.sh`: `M2_PUBLIC_ORIGIN=http://192.88.99.1`. `192.88.99.0/24` is the deprecated 6to4 relay anycast block (RFC 7526). It is not announced on the internet, and render.sh accepts it because it is outside the private and documentation ranges render.sh rejects. Use it only from inside the guest as a name-pinned target, for example `curl --resolve "192.88.99.1:80:127.0.0.1" http://192.88.99.1/`, so that no packet for it leaves the guest. Never query it from the host or from outside the guest. Optionally add a guest blackhole route (`ip route add blackhole 192.88.99.1/32`, or the NetworkManager equivalent) so that an unpinned request fails locally.
- Fallback when SLIRP cannot carry a needed case: a dedicated libvirt NAT network on `10.0.0.0/24` with `<forward mode='nat'/>` and no DHCP range outside it. This one-time step adds libvirt-owned `LIBVIRT_*` rules and a `virbr` device to the host; record that diff separately as the network setup, then re-take the baseline before guest work.

**Why not passt on this host.** On the development server (Ubuntu 24.04, kernel 7.0, AppArmor fine-grained `af_unix` mediation `features/network_v9/af_unix`), noble's passt `0.0~git20240220` cannot accept QEMU's connection. Inside libvirt's `//passt` child profile, `accept4` on its listening socket returns `EACCES`. The kernel audit log records nothing, and noble's `abstractions/passt` has only `network unix stream,`. passt does not handle that failure and continues with fd -1. Its listening socket is edge-triggered, so it never retries, and the connection stays in the socket's queue forever. The visible symptom is `Recv-Q 1` on `/run/libvirt/qemu/passt/*.socket`, and the guest has TX but RX 0 and no IPv4 address. The evidence is the issue #48 comment "R4 네트워크 차단 원인 확정". Making passt work would need a patched packaged AppArmor policy or a newer passt, so the development VM uses SLIRP instead.

## Guest creation

1. Download the Oracle Linux 9.7 x86_64 ISO from Oracle and verify its published SHA256.
2. Create the guest without a libvirt network:

   ```sh
   sudo virt-install --name anxious-dev --memory 8192 --vcpus 4 --cpu host-passthrough \
     --disk path=/var/lib/libvirt/images/anxious-dev.qcow2,size=60,format=qcow2,bus=virtio \
     --location /PATH/OracleLinux-R9-U7-x86_64-dvd.iso --os-variant OS_VARIANT \
     --network none --graphics none --console pty,target_type=serial --extra-args 'console=ttyS0'
   ```

   Then add the `xmlns:qemu` declaration and the `<qemu:commandline>` block above with `sudo virsh edit anxious-dev`, without adding an `<interface>` element. Set `OS_VARIANT` to the closest Oracle Linux 9 name `osinfo-query os` lists.
3. In the guest: minimal install, hostname `simple`, SELinux enforcing, firewalld and NetworkManager enabled. Enable `ol9_UEKR8`, install the production kernel NVR if available for x86_64, reboot into it and record `uname -r`.
4. Record `getenforce`, `firewall-cmd --get-active-zones`, `nmcli device` and `uname -m` (`x86_64`).
5. Add the guest-shell host-gateway rule (Network, "Outbound to the host") and verify it with the 5001 positive control, so `base-os` already contains it. The existing `anxious-dev` received it after `base-os`.
6. Take snapshot `base-os` (below).

## k3s and Cilium

Follow [README.md](README.md) "Layout and installation" in the same order as production: record routes/firewall/SELinux/NetworkManager state, install `config.yaml` as `/etc/rancher/k3s/config.yaml` mode 0600 with the k3s SELinux policy package, exclude CNI interfaces from NetworkManager, start k3s pinned to `K3S_VERSION`, then install Cilium `CILIUM_VERSION` with `cilium-values.yaml` and CLI `CILIUM_CLI_VERSION`. The installers select the x86_64 artifacts of the same pinned versions; nothing in `versions.env`, `config.yaml` or `cilium-values.yaml` changes.

The guest runs Docker Engine next to k3s, as the production node does. The production runner path needs it: the #16/#18 controller, observer, supply and restore units are rootful `docker run --pull=never <provisioner local ID>` invocations ([rollout README](../hosting/m3/multi-user-runners/rollout/README.md)). Install the Docker Engine major version recorded on the OCI node (`docker version` there), from Docker's RHEL repository for x86_64, before k3s. Turn on the containerd image store (`/etc/docker/daemon.json`: `{"features": {"containerd-snapshotter": true}}`), as on production, so that local image IDs are OCI index digests. Then follow this README's Docker preservation steps as written.

Then apply the hosting procedure as written: `infra/hosting/render.sh` output server dry-run, web/API rollout and one runner Pod, using linux/amd64 images built from the same source and imported with `k3s ctr images import` exactly as `infra/hosting/README.md` describes. Build them inside the guest with its Docker, using the production arguments (for example `docker build -f backend/Dockerfile --target runner --build-arg APP_GIT_SHA=FULL_SHA --build-arg APP_BUILT_AT=UTC -t IMAGE .`, and `--target provisioner` for the runner workers). `docker save` the result and import it into k3s. Building on the development server with `--platform linux/amd64` also works, but see the image-store note below. `build_common_chatbot.sh` stays arm64-only because it produces production artifacts.

The one runner Pod comes from the current production path, the #16/#18 pool rollout. Follow [rollout/README.md](../hosting/m3/multi-user-runners/rollout/README.md):

1. Render `values.json` with guest values.
2. Apply `resources.json`.
3. Issue the worker SA tokens.
4. Install the units.
5. Start restore once.
6. Run one finite `issue16-controller.service` for one entered owner.

Differences from production in that procedure:

- **Guest-observed values:** `values.json` takes the guest's own namespace/node/source UIDs, pod CIDR, extracted k3s CLI path (the hash directory differs on x86_64) and guest image references.
- **Synthetic identities:**
  - `providerAccountId` is a synthetic identifier. It is never a credential, and supply is not run.
  - The single owner is a synthetic approved editor. The allowlist holds only that owner.
- **Login:** the API is configured as in production (`OAUTH_MODE=github`) but has no reachable GitHub OAuth app for the guest-only origin. So the owner's session is seeded in the guest database. `APP_ENV=test` with `OAUTH_MODE=mock` is not used, because it moves the API configuration further from production. See "Synthetic owner seed" below.
- **Everything after login is the production code path:** registry entry, controller, PV/PVC/Secret, binding and Pod. The Pod is expected to reach Running with health `not_configured`. No provider credential exists.

The older #10 one-shot rehearsal (`user-runner-isolated/`) is not the current path. Its `initialize-rehearsal.sh` and `IMAGE-INITIALIZER-PACKAGE.md` take the image platform (`amd64` on this VM), but its fixed rehearsal PV/namespace values are historical.

Image audits take the architecture explicitly: `access_image_audit.py --architecture=amd64 ARCHIVE METADATA INSPECT REPOSITORY SHA` and `common_chatbot_image_audit.py ROLE --architecture amd64 …`. The default stays `arm64`, and production output keys are unchanged (`arm64Manifest`; amd64 runs report `amd64Manifest`). `image_audit.py` accepts `--architecture=` too, but its `POLICY` holds only approved production arm64 image IDs, so it has no development-VM role.

The audits need a Docker daemon that uses the containerd image store. On such a daemon the build metadata's `containerimage.digest` is the OCI index that `docker save` writes. The development server's daemon has the containerd image store turned off (`/etc/docker/daemon.json`: `"containerd-snapshotter": false`). There, `containerimage.digest` is the image config digest, and `docker save` builds a new manifest at save time. So the audits refuse every archive built there, for either architecture (measured in [CROSS-BUILD.md](../hosting/CROSS-BUILD.md)). Images built inside the guest, whose Docker has the containerd image store on, are audited normally with `--architecture=amd64`. For archives built on the development server, take the image reference from the guest after import (`k3s ctr --namespace k8s.io images ls`, column `DIGEST`, the manifest in the archive's `index.json`). Do not change the host daemon setting: changing it hides every image the host's other projects have in the classic store.

Take snapshot `k3s-ready` after Cilium is Ready.

## Synthetic owner seed (development VM only)

This applies only to the development VM's guest database. Never run it against the production database or any database holding real accounts.

Every identifier marks itself as synthetic:

| Field | Value |
| --- | --- |
| `auth.accounts.id` | `00000000-0000-4000-8000-000000000048` |
| `github_id` | `synthetic-devvm-48` |
| `login` | `SYNTHETIC-DEVVM-NOT-A-USER` |
| `role` / `is_approved` | `editor` / `true` |

1. Take snapshot `pre-synthetic-owner` (see Snapshots) before seeding.
2. In the guest, generate the session token and CSRF token with `python -c 'import secrets; print(secrets.token_urlsafe(32))'`. Write them to a root-only `0600` file in the guest, never to argv, shell history or logs.
3. Compute `token_hash` inside the API image, reading the token from that file: `app.main.session_digest(token, "http://192.88.99.1")`.
4. Insert one account and one session with a one-hour expiry, and put the owner UUID in the rollout `allowlist`:

   ```sql
   INSERT INTO auth.accounts (id, github_id, login, role, is_approved)
   VALUES ('00000000-0000-4000-8000-000000000048', 'synthetic-devvm-48', 'SYNTHETIC-DEVVM-NOT-A-USER', 'editor', true);
   INSERT INTO auth.sessions (token_hash, account_id, csrf_token, expires_at)
   VALUES ('<token_hash>', '00000000-0000-4000-8000-000000000048', '<csrf>', now() + interval '1 hour');
   ```

5. Call `POST /api/research/codex/runner` once through `curl --resolve 192.88.99.1:80:127.0.0.1`, with:
   - header `Cookie: anxious_session=<token>`
   - header `x-csrf-token: <csrf>`
   - header `Idempotency-Key: <uuid>`
   - body `{}`

   Expect `202`.

**Removal:**

- **Full removal (preferred):** shut the guest down and `virsh snapshot-revert anxious-dev pre-synthetic-owner`. Rows are not deleted one by one, because the account is referenced by `runner_runtime.registry` and other durable runner records.
- **To keep the runner evidence instead:**
  1. Request termination with `DELETE /api/research/codex/runner` using the same session, and let the observer finish.
  2. Revoke the account:

     ```sql
     DELETE FROM auth.sessions WHERE account_id = '00000000-0000-4000-8000-000000000048';
     UPDATE auth.accounts SET is_approved = false, role = 'commenter'
       WHERE id = '00000000-0000-4000-8000-000000000048' AND github_id = 'synthetic-devvm-48';
     ```

  3. Delete the guest token file.

## Snapshots

Take a snapshot before every policy transition, reboot rehearsal, migration rehearsal or k3s/Cilium version change, and name it after the step (`pre-default-allow`, `pre-reboot`, `pre-0005`). Shut the guest down first so the k3s SQLite datastore and PostgreSQL PVC are consistent on disk:

```sh
sudo virsh shutdown anxious-dev            # wait for state "shut off"
sudo virsh snapshot-create-as anxious-dev pre-STEP --description 'STEP, commit SHA'
sudo virsh start anxious-dev
sudo virsh snapshot-list anxious-dev
sudo virsh snapshot-revert anxious-dev pre-STEP   # rollback, guest shut off
```

Keep at least `base-os`, `k3s-ready` and the latest pre-step snapshot; delete older ones with `virsh snapshot-delete`. Snapshots live inside the qcow2 image on the host disk; they are rehearsal state, not backups of anything production.

## Isolation checks

The rehearsal must leave the host unchanged. Capture before the guest work and after it, then diff:

```sh
docker ps --format '{{.Names}} {{.Status}} {{.Ports}}' | sort
sudo iptables-save | grep -v '^#' | sed -E 's/\[[0-9]+:[0-9]+\]//'
sudo nft list ruleset
ss -lntu
ip -br address; ip route
```

Expected difference: none, apart from the AppArmor local override file (`/etc/apparmor.d/local/abstractions/libvirt-qemu`, see Host prerequisites) and the `127.0.0.1:22107` loopback listener. Container uptimes changing order is not a difference; a restarted container is.

## Resources

4 vCPU / 8 GiB / 60 GiB is the initial value only. Measure guest CPU, memory and disk after web/API/PostgreSQL and one runner are running, and adjust with `virsh setvcpus` / `virsh setmaxmem` / `qemu-img resize` while shut off. The host has 16 CPUs and 31 GiB RAM shared with other projects; do not exceed 8 vCPU / 12 GiB without asking.
