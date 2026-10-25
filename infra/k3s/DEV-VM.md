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
sudo apt install --no-install-recommends qemu-system-x86 qemu-utils libvirt-daemon-system virtinst passt
```

`libvirt-daemon-system` defines a `default` NAT network on `192.168.122.0/24`. That range lies inside an existing Docker network (`192.168.112.0/20`), and host Docker already occupies `172.17.0.0/16`–`172.31.0.0/16` and most `192.168.0.0/16` /20 blocks. Do not use, start or autostart the libvirt default network:

```sh
sudo virsh net-destroy default 2>/dev/null || true
sudo virsh net-autostart default --disable
```

**Ubuntu 24.04, libvirt 10.0.0, passt backend: AppArmor override.**

- **Why it is needed:** Ubuntu's `libvirt-daemon-system` 10.0.0 (checked with `10.0.0-2ubuntu8.19`) ships an `abstractions/libvirt-qemu` that grants only `/usr/bin/passt Cx -> passt,`. The per-VM profile `libvirt-<UUID>` is missing two permissions:
  - `rm` on `/usr/bin/passt`, which an exec of the PIE binary needs
  - access to the passt socket that QEMU connects to
- **Symptom:** `virsh start` fails with `Child process (passt …) unexpected fatal signal 11`. The real failure is `execve("/usr/bin/passt") = -1 EACCES`, an AppArmor `file_mmap` denial. The signal 11 is libvirt's error-reporting child dying afterwards. Confirm the denial with `journalctl -k | grep DENIED`.
- **Fix:** add the two rules to the local override, which the abstraction already includes with `include if exists`. Do not edit the packaged file. The override survives package updates.

```sh
sudo install -d -m 0755 /etc/apparmor.d/local/abstractions
sudo tee /etc/apparmor.d/local/abstractions/libvirt-qemu >/dev/null <<'EOF'
# issue #48: libvirt 10.0.0 abstraction grants only Cx; exec of PIE passt also needs rm
/usr/bin/passt rm,
owner @{run}/libvirt/qemu/passt/* rw,
EOF
```

- **When it applies:** libvirt builds the per-VM profile when the guest starts, so the rules take effect at the next `virsh start`.
- **Expected after start:** `anxious-dev` is running, passt runs under its child profile in enforce mode, `127.0.0.1:22107` is listening, and `journalctl -k` shows no new AppArmor `DENIED` lines.
- **Rollback:** delete `/etc/apparmor.d/local/abstractions/libvirt-qemu`, then stop and start the guest.
- **Not needed** on hosts whose libvirt abstraction already grants these rules, or with a non-passt network backend.

Take the host baseline (see Isolation checks) **after** these packages are installed and **before** the guest is created.

## Network

Recommended: libvirt user-mode networking with the `passt` backend. It creates no host bridge, no host iptables/nft rule and no listening port except an explicit loopback forward, so the host Docker networks, `central-traefik` (`:12345`) and other projects are unaffected. Host bridging is not available: the host uplink is Wi-Fi (`wlp4s0`).

```xml
<interface type='user'>
  <backend type='passt'/>
  <model type='virtio'/>
  <ip family='ipv4' address='10.0.0.107' prefix='24'/>
  <portForward proto='tcp' address='127.0.0.1'>
    <range start='22107' to='22'/>
  </portForward>
</interface>
```

- Guest address `10.0.0.107/24` mirrors the production VCN address; `10.0.0.0/24` is not used anywhere on the host. Confirm the guest default route with `ip route` after first boot and record it.
- SSH reaches the guest only from the host loopback: `ssh -p 22107 opc@127.0.0.1`.
- Ingress tests (Traefik 80/443 inside the guest) run from inside the guest with `curl --resolve`, or through an additional loopback-only forward on a free high port. Do not forward 80/443 or bind `0.0.0.0`.
- Public origin for `infra/hosting/render.sh`: `M2_PUBLIC_ORIGIN=http://192.88.99.1`. `192.88.99.0/24` is the deprecated 6to4 relay anycast block (RFC 7526). It is not announced on the internet, and render.sh accepts it because it is outside the private and documentation ranges render.sh rejects. Use it only from inside the guest as a name-pinned target, for example `curl --resolve "192.88.99.1:80:127.0.0.1" http://192.88.99.1/`, so that no packet for it leaves the guest. Never query it from the host or from outside the guest. Optionally add a guest blackhole route (`ip route add blackhole 192.88.99.1/32`, or the NetworkManager equivalent) so that an unpinned request fails locally.
- Fallback when passt cannot carry a needed case: a dedicated libvirt NAT network on `10.0.0.0/24` with `<forward mode='nat'/>` and no DHCP range outside it. This one-time step adds libvirt-owned `LIBVIRT_*` rules and a `virbr` device to the host; record that diff separately as the network setup, then re-take the baseline before guest work.

## Guest creation

1. Download the Oracle Linux 9.7 x86_64 ISO from Oracle and verify its published SHA256.
2. Create the guest without a libvirt network:

   ```sh
   sudo virt-install --name anxious-dev --memory 8192 --vcpus 4 --cpu host-passthrough \
     --disk path=/var/lib/libvirt/images/anxious-dev.qcow2,size=60,format=qcow2,bus=virtio \
     --location /PATH/OracleLinux-R9-U7-x86_64-dvd.iso --os-variant OS_VARIANT \
     --network none --graphics none --console pty,target_type=serial --extra-args 'console=ttyS0'
   ```

   Then add the `passt` interface above with `sudo virsh edit anxious-dev` (or install with it directly if the installed `virt-install` accepts `--network passt,...`). Set `OS_VARIANT` to the closest Oracle Linux 9 name `osinfo-query os` lists.
3. In the guest: minimal install, hostname `simple`, SELinux enforcing, firewalld and NetworkManager enabled. Enable `ol9_UEKR8`, install the production kernel NVR if available for x86_64, reboot into it and record `uname -r`.
4. Record `getenforce`, `firewall-cmd --get-active-zones`, `nmcli device` and `uname -m` (`x86_64`).
5. Take snapshot `base-os` (below).

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
- **Login:** the API is configured as in production (`OAUTH_MODE=github`) but has no reachable GitHub OAuth app for the guest-only origin. So the owner's session is seeded in the guest database: one `auth.accounts` row (role `editor`, approved) and one `auth.sessions` row, whose `token_hash` is `app.main.session_digest(token, "http://192.88.99.1")`. The owner then calls `POST /api/.../runner` with that session cookie, the session's CSRF token and an `Idempotency-Key`, through `curl --resolve`.
- **Everything after login is the production code path:** registry entry, controller, PV/PVC/Secret, binding and Pod. The Pod is expected to reach Running with health `not_configured`. No provider credential exists.

The older #10 one-shot rehearsal (`user-runner-isolated/`) is not the current path. Its `initialize-rehearsal.sh` and `IMAGE-INITIALIZER-PACKAGE.md` take the image platform (`amd64` on this VM), but its fixed rehearsal PV/namespace values are historical.

Image audits take the architecture explicitly: `access_image_audit.py --architecture=amd64 ARCHIVE METADATA INSPECT REPOSITORY SHA` and `common_chatbot_image_audit.py ROLE --architecture amd64 …`. The default stays `arm64`, and production output keys are unchanged (`arm64Manifest`; amd64 runs report `amd64Manifest`). `image_audit.py` accepts `--architecture=` too, but its `POLICY` holds only approved production arm64 image IDs, so it has no development-VM role.

The audits need a Docker daemon that uses the containerd image store. On such a daemon the build metadata's `containerimage.digest` is the OCI index that `docker save` writes. The development server's daemon has the containerd image store turned off (`/etc/docker/daemon.json`: `"containerd-snapshotter": false`). There, `containerimage.digest` is the image config digest, and `docker save` builds a new manifest at save time. So the audits refuse every archive built there, for either architecture (measured in [CROSS-BUILD.md](../hosting/CROSS-BUILD.md)). Images built inside the guest, whose Docker has the containerd image store on, are audited normally with `--architecture=amd64`. For archives built on the development server, take the image reference from the guest after import (`k3s ctr --namespace k8s.io images ls`, column `DIGEST`, the manifest in the archive's `index.json`). Do not change the host daemon setting: changing it hides every image the host's other projects have in the classic store.

Take snapshot `k3s-ready` after Cilium is Ready.

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

Expected difference: none, apart from the `passt` process and its loopback forward port(s). Container uptimes changing order is not a difference; a restarted container is.

## Resources

4 vCPU / 8 GiB / 60 GiB is the initial value only. Measure guest CPU, memory and disk after web/API/PostgreSQL and one runner are running, and adjust with `virsh setvcpus` / `virsh setmaxmem` / `qemu-img resize` while shut off. The host has 16 CPUs and 31 GiB RAM shared with other projects; do not exceed 8 vCPU / 12 GiB without asking.
