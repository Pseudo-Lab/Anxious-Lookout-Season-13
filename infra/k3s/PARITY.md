# Production ↔ development parity

Issue [#54](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/54). Production is the OCI aarch64 node. Development is the x86_64 KVM guest `anxious-dev` on the development server ([DEV-VM.md](DEV-VM.md), issue #48). This table is the single place that says how the two differ and whether each difference is allowed. DEV-VM.md and the hosting documents describe how to build each side, and point here for the judgement.

## Classes

- **Must match.** A difference is a defect. Fix the side that drifted, or change both together.
- **May differ.** A development-only difference that does not change what production does. It must not be carried to production.
- **Record.** The production value is unknown, or has to be measured each time. Measure it and fill it in, then reclassify the row as Must match or May differ.

## Rules

1. A change that creates a new difference adds or updates a row here, with its class, in the same PR. A PR that leaves an unclassified difference is not mergeable.
2. A change to the production value of a **Must match** row updates the production procedure documents (`infra/k3s/README.md`, `infra/hosting/README.md`, rollout and cutover documents) in the same PR, and the development side follows in that PR or in the next one.
3. A **May differ** row names its guard: what keeps the development value from reaching production.
4. **Record** rows are not permanent. Whoever next has production access measures them.

## Table

| Item | Production (OCI) | Development VM | Class | Basis |
| --- | --- | --- | --- | --- |
| CPU architecture | aarch64 | x86_64 | May differ: the one intended platform difference | #48 |
| Codex version | 0.160.1 | 0.160.1 | Must match | `backend/runner/install_codex.py` |
| Codex binary pins | `PINS["arm64"]` (codex `fbbaec80…`, host `fbccde22…`) | `PINS["amd64"]` (codex `f34a4d23…`, host `b33e8a52…`), selected by BuildKit `TARGETARCH`, fail-closed | May differ: follows the architecture, and each set is fixed in source | #48 21518bf, R3 |
| Runner / API / web image source and build arguments | same source SHA, production arguments, `linux/arm64` | same source SHA, production arguments, `linux/amd64` | Must match (source and arguments); the platform follows the architecture row | DEV-VM.md "k3s and Cilium" |
| Distribution | Oracle Linux Server 9.7 | Oracle Linux Server 9.7 (minimal) | Must match | #48 R4 |
| Kernel | UEK `6.12.0-204.92.4.2.el9uek` | UEK `6.12.0-204.92.4.2.el9uek` (x86_64, R4 2026-10-25) | Record: patch levels can drift. Compare NVRs first when a Cilium or dataplane symptom appears on one side only | `infra/k3s/VERIFICATION.md`, #48 R4 |
| SELinux / firewalld / NetworkManager | enforcing / active / active | enforcing / active / active | Must match | #48 R4 |
| k3s / Cilium / Cilium CLI, `config.yaml`, `cilium-values.yaml` | `versions.env` (k3s `v1.36.4+k3s1`, Cilium `1.20.2`), repository files | same files, unchanged (`kube-proxy-replacement: false` checked) | Must match | `infra/k3s/README.md`, #48 R4 |
| Node identity | `anxious-lookout` / `simple` / `10.0.0.107`, Pod `10.42.0.0/16`, Service `10.43.0.0/16` | same (guest-private) | Must match: host guards, API certificate SANs and runner inputs depend on it | DEV-VM.md |
| CPU / memory / disk | 2 / ~10.6 GiB / existing | 4 vCPU / 8 GiB / 60 GiB initial | May differ | DEV-VM.md "Resources" |
| Node network | OCI VCN NIC | QEMU SLIRP `net=10.0.0.0/24,dhcpstart=10.0.0.107` (gateway `10.0.0.2`, DNS `10.0.0.3`), `hostfwd` `127.0.0.1:22107 → 22` only, `custom-argv` taint | May differ. Guard: libvirt domain XML exists only on the development server | #48 R4, DEV-VM.md "Network" |
| Guest gateway guards | none | firewalld direct `OUTPUT -d 10.0.0.2/32 -m conntrack --ctstate NEW -j REJECT`; nftables `inet devvm_hostguard` forward chain with `devvm-hostguard.service` | May differ. Guard: SLIRP-specific (QEMU maps `10.0.0.2` to the host loopback); never install on production | #48 f173af2, 9496f73 |
| Docker Engine | `docker-ce` / `docker-ce-cli` 29.6.1, `containerd.io` 2.2.5, buildx 0.35.0, compose 5.3.1 from `docker-ce-stable`; no `daemon.json`; `overlayfs` + `io.containerd.snapshotter.v1` | same versions for x86_64 (observed on `anxious-dev`), same repository, no `daemon.json`, same driver-type checked | Must match | #48 2b453db, R4 |
| `docker-ce-rootless-extras` | 29.6.1-1.el9 (dependency; rootless mode unused) | 29.6.1-1.el9 (DEV-VM.md install command pins it). Observed on `anxious-dev` 2026-10-26: `docker-ce-rootless-extras-29.6.1-1.el9.x86_64`, after aligning the 29.9.2 that R4 installed before the pin was documented. The alignment was `dnf downgrade docker-ce-rootless-extras-29.6.1-1.el9`, exactly one package, with no Docker restart | Must match | #48 ef51c49, #54 review |
| Docker / k3s install order | not recorded | Docker before k3s. Installing Docker after k3s removed the `CILIUM_*` iptables chains while Cilium reported Ready (R4) | Record | #48 9496f73 |
| Minimal-OS additions | not recorded | `tar`, `dnf-plugins-core`; k3s installer with `INSTALL_K3S_SKIP_DOWNLOAD=binary` so that `k3s-selinux` / `container-selinux` install | Record | #48 9496f73 |
| `hosting-boundary` CNP (m2-hosting) | absent: deleted by issue #13 decision C; endpoints use Cilium default allow | absent: `render.sh` no longer emits it (#54). Guests rendered before #54 delete it once | Must match | #13 `DEFAULT-ALLOW.md`, #54 |
| `hosting-access-web-link-v1` CNP | absent | absent. Only the historical #10/#13 deployment renderer (`backend/ops/user_runner_deployment.py`) still emits it; never rerun that renderer | Must match | #13, #54 |
| Firewalld Pod CIDR → TCP 6443 rule | present (runtime and permanent) | present (runtime and permanent, no reload) | Must match | #13 `DEFAULT-ALLOW.md` step 3 |
| `codex-runner` policies | KNP `runner-default-deny`, CNP `runner-callback-draft` (post-EGRESS-REPAIR) | same, from `user-runner-e2e/00-foundation.json` and `40-runner-network.json` | Must match | #13, #16, #48 R4 |
| Runner worker → PostgreSQL | `database-url` files address the postgres-0 Pod IP; update all three when the Pod is recreated | same | Must match. `user-runner-e2e/DB-ROUTE.md` (loopback port-forward) is a pre-#13 workaround, not this path | #16 cutover, #48 R4 |
| Login | GitHub OAuth (`OAUTH_MODE=github`) | same API configuration. One synthetic approved editor and session seeded in the guest database (`00000000-0000-4000-8000-000000000048`, `github_id` `900000000048`, `SYNTHETIC-DEVVM-NOT-A-USER`) | May differ. Guard: the seed is guest-database only, and identifiers mark themselves as synthetic | #48, DEV-VM.md "Synthetic owner seed" |
| Public origin | the reviewed production `M2_PUBLIC_ORIGIN` (numeric public IPv4) | `http://192.88.99.1` (RFC 7526), used only through `curl --resolve 192.88.99.1:80:10.0.0.107` inside the guest | May differ. Guard: never announced on the internet, never queried from outside the guest | #48, DEV-VM.md "Network" |
| Provider credential supply | host supply worker from the operator's Codex login | none; supply, observer and paid turns not run | May differ | #48 R4 |
| Release archive build host | the OCI node (Docker containerd image store) | guest Docker for development images. The development server's own Docker (classic store) cannot produce auditable archives | May differ: release archives stay on the OCI node | #48 R3, #52, `infra/hosting/CROSS-BUILD.md` |
| Host-side changes | none | development server: libvirt/QEMU packages; `qemu-user-static` (arm64 emulation for R3); AppArmor local override `/etc/apparmor.d/local/abstractions/libvirt-qemu` (passt only, harmless with SLIRP, kept) | May differ: development server only | #48 |
| Isolation-check expected differences | n/a | `127.0.0.1:22107` QEMU listener, the AppArmor override file, and QEMU-owned ephemeral UDP egress sockets that SLIRP opens (seen in `ss -lntu`; not a forward) | May differ | #48 R4, DEV-VM.md "Isolation checks" |

## Render output and the live node

`infra/hosting/render.sh` emits no CiliumNetworkPolicy since #54, which matches the live node after issue #13. Applying the current render output to the live node changes no policy object: none is created, and `kubectl apply` does not delete objects absent from its input. `infra/k3s/host/postboot-check.sh` already requires both m2-hosting CNPs to be absent. No production action is needed for #54.
