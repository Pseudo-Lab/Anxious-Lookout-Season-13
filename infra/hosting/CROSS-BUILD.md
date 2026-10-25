# arm64 release images from the x86_64 development server

Issue [#48](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/48) R3. Production stays linux/arm64. This only changes **where** the arm64 api/web/runner images can be built. Digest recording, `docker save` archives with checksums and `k3s ctr images import` follow [README.md](README.md) "Build and release" unchanged; no registry and no new transfer path is introduced.

## Prerequisite: arm64 emulation on the build host

The development server is x86_64 (Ubuntu 24.04, Docker 29.3.0, BuildKit `default` builder). Without a `qemu-aarch64` binfmt handler, `--platform linux/arm64` builds fail at the first `RUN`. Registering one is a host-wide kernel setting (it affects no container, firewall rule or port) and needs human/PM approval. It was approved and installed on 2026-10-25 (`qemu-user-static` 1:8.2.2+ds-0ubuntu1.18, flags `POF`). Use one of:

```sh
sudo apt install qemu-user-static            # Ubuntu package, persistent via systemd-binfmt
# or, non-persistent until reboot, pinned image:
docker run --privileged --rm tonistiigi/binfmt@sha256:REVIEWED_DIGEST --install arm64
```

Check: `ls /proc/sys/fs/binfmt_misc/qemu-aarch64` exists and `docker run --rm --platform linux/arm64 python:3.13-slim-bookworm@sha256:a1165e… uname -m` prints `aarch64`. The `F` (fix-binary) flag must be set (`grep flags /proc/sys/fs/binfmt_misc/qemu-aarch64`) so BuildKit containers can use it. On the development server the `docker run` check fails with `cannot overwrite digest`: the classic image store (see below) already holds the amd64 image under that index digest and cannot hold a second architecture under it. Run `uname -m` in a built arm64 image instead.

## Build

Use the existing reviewed builder unchanged; it already passes `--platform linux/arm64` for all three roles, refuses a dirty tree and runs `access_image_audit.py` (default architecture `arm64`):

```sh
bash infra/hosting/m3/build_common_chatbot.sh FINAL_APPROVED_SHA /tmp/issue9-chatbot-build-SUFFIX
```

**This needs a Docker daemon with the containerd image store.** The development server has the containerd image store turned off (`/etc/docker/daemon.json`: `"containerd-snapshotter": false`; storage driver `overlay2`). With that setting, BuildKit's `containerimage.digest` is the image config digest. `docker save` then writes an OCI layout whose `index.json` points to a manifest generated at save time, not to that digest. `access_image_audit.py` reads the target digest as an index or manifest, so it refuses the archive. That is correct: it cannot tie the archive to the build output. As a result, `build_common_chatbot.sh` stops after the api build on this host (measured 2026-10-25, below). The cause is the image store, not emulation. Production references are index digests from the containerd image store (`m3/multi-user-runners/rollout/README.md`). Do not switch the host daemon to produce them here: that hides every image the host's other projects have in the classic store. Release archives therefore keep coming from the OCI node. Emulated builds on the development server are for checking that a change builds and installs correctly on arm64.

For a single role outside that script, the arguments are the same as on the OCI node with only the platform made explicit:

```sh
docker buildx build --load --platform linux/arm64 -f backend/Dockerfile --target runtime \
  --build-arg APP_GIT_SHA=FULL_SHA --build-arg APP_BUILT_AT=UTC -t IMAGE .
docker buildx build --load --platform linux/arm64 -f backend/Dockerfile --target runner \
  --build-arg APP_GIT_SHA=FULL_SHA --build-arg APP_BUILT_AT=UTC -t IMAGE .
docker buildx build --load --platform linux/arm64 -f infra/hosting/web.Dockerfile \
  --build-arg WEB_GIT_SHA=FULL_SHA --build-arg WEB_BUILT_AT=UTC --build-arg NEXT_PUBLIC_BASE_PATH= -t IMAGE .
```

The runner stage receives `TARGETARCH=arm64` from BuildKit and installs the unchanged production aarch64 Codex pin set; a missing or mismatched `TARGETARCH` fails the build. Verify before saving:

```sh
docker image inspect --format '{{.Os}}/{{.Architecture}} {{.Config.User}}' IMAGE   # linux/arm64, user 10001:10001 (api, runner) or node (web)
docker run --rm --platform linux/arm64 --network none --entrypoint sha256sum RUNNER_IMAGE \
  /usr/local/bin/codex /usr/local/bin/codex-code-mode-host
# fbbaec80…bceb3e  codex, fbccde22…f4fd8  codex-code-mode-host
```

Then `docker save`, checksum and import exactly as README.md describes.

### Expected digest relationship

Image digests are never identical across releases: `APP_GIT_SHA` / `APP_BUILT_AT` and the `backend/runner` copy (which now contains the amd64 pin table) are image inputs. "Unchanged production pin" is shown by the runner's installed `codex` and `codex-code-mode-host` SHA256 above, which are the same bytes as every earlier production runner, and by `PINS["arm64"]` in `backend/runner/install_codex.py` being byte-identical to the previous constants (`backend/config_tests/test_install_codex.py`).

## Release archives: build on the OCI node

Release archives stay on the OCI node. Emulation is fast enough (below), but the development server's image store cannot produce auditable archives (above). Build natively on the production node as before (same commands; `--platform linux/arm64` is then native). Only the build location differs; the save/checksum/import procedure is the same.

## Base images

Every pinned base digest is a multi-architecture index containing both linux/amd64 and linux/arm64 (checked 2026-10-22 with `docker buildx imagetools inspect --raw`). No architecture-specific digest is pinned.

| Pin | Index type | linux/amd64 | linux/arm64 |
| --- | --- | --- | --- |
| `python:3.13-slim-bookworm@sha256:a1165e27…` | OCI index | `f0408636…` | `55432fc4…` (v8) |
| `node:22-alpine@sha256:0a7108bf…` | OCI index | `2c752226…` | `85cdd100…` (v8) |
| `postgres:18.6-bookworm@sha256:afc7e2d4…` | OCI index | `9551dd5b…` | `67ba68a2…` (v8) |
| `mcr.microsoft.com/playwright:v1.56.1-noble@sha256:f1e7e010…` (test only) | Docker manifest list | `42f02d32…` | `0dbd7541…` |
| `rancher/mirrored-library-traefik:3.7.8@sha256:4299bbed…` | OCI index | `54c59843…` | `170ed55c…` (v8) |

## Native dependencies

`backend/requirements.lock` resolves entirely to binary wheels for cp313 manylinux aarch64 (`pip download --only-binary=:all:` in the pinned Python base, 2026-10-22). Compiled ones: cffi, cryptography, markupsafe, pillow, psycopg-binary, pydantic-core, sqlalchemy. No source build runs under emulation. Confirmed in the measured build below. The api `pip install` log has no wheel builds. The runner image's 38 installed distributions are 23 `py3-none-any` and 15 aarch64 manylinux wheels: one `cp311-abi3-manylinux_2_34_aarch64` and 14 `cp313-cp313-manylinux*_aarch64`. In the image, `platform.machine()` is `aarch64`, Pillow 13.0.0 imports, and psycopg 3.3.6 uses the `binary` implementation. The web image's platform-specific npm packages were resolved by pnpm inside the emulated arm64 build (`pnpm install --frozen-lockfile` 35.0 s), and `next build` completed.

## Measurement

Source `46d2da191ca5deb7062bf71c7c2df28e0fac426c`. Host: x86_64 development server (16 CPUs, 31 GiB), qemu-user-static 8.2.2, Docker 29.3.0 classic image store. The arm64 base layers were not cached before the run. Times are wall clock per `docker buildx build --load --platform linux/arm64`.

| Date | Role | First build | Rebuild, new build args | Slowest step | Image ID (classic store config digest) | User |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-10-25 | api | 46 s | 1 s (same args) | `pip install` 41.2 s | `sha256:1fbb551fcb048d4d34b8995ba4c6ad938e25cca50e187c0eab91b0ee0fde4f02` | 10001:10001 |
| 2026-10-25 | runner | 16 s (api's pip layer cached) | 14 s | `install_codex.py` 14.2 s | `sha256:8e799995db94fc740abdaeb6243704a8bee59a294f9de80abaf9cab771ec6a6e` | 10001:10001 |
| 2026-10-25 | web | 152 s | 105 s | `next build` 99.7 s | `sha256:963777c2cdcc0d737c5ff918f49ed60ea2ade8fe65995f43c4641fe92965fd8b` | node |

All three report `linux/arm64`. The three roles take about 3.5 minutes in total, far below the 30-minute threshold, so emulation speed is no reason to prefer the OCI node. The api ran through `build_common_chatbot.sh` (09:43:22Z). Its audit failed on the image store as described under "Build", so runner and web were built with the single-role commands. The api archive's `docker save` manifest is `sha256:eb6e17cfdead549c1cc2151327ab839d992ae5f0a11a2d1b883ca116bd7f3666`. These are classic-store IDs, not production references, and no archive was imported anywhere.

Runner pin check (`sha256sum` inside the arm64 runner image): `codex` `fbbaec80443919f86dd63648a0b62759cf6f1d0e09310602fde96885e0bceb3e` and `codex-code-mode-host` `fbccde22982e3e679678e203a9c18eee8342fb04b096063d05159f1f80df4fd8`. Both are byte-identical to `PINS["arm64"]` in `backend/runner/install_codex.py` and to the earlier production constants. `codex --version` reports `codex-cli 0.160.1` under emulation.
