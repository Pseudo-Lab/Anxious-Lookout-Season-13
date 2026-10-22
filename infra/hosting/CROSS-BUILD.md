# arm64 release images from the x86_64 development server

Issue [#48](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/48) R3. Production stays linux/arm64. This only changes **where** the arm64 api/web/runner images can be built. Digest recording, `docker save` archives with checksums and `k3s ctr images import` follow [README.md](README.md) "Build and release" unchanged; no registry and no new transfer path is introduced.

## Prerequisite: arm64 emulation on the build host

The development server is x86_64 (Ubuntu 24.04, Docker BuildKit `default` builder). It currently has **no** `qemu-aarch64` binfmt handler, so `--platform linux/arm64` builds fail at the first `RUN`. Registering one is a host-wide kernel setting (it affects no container, firewall rule or port) and needs human/PM approval. Use one of:

```sh
sudo apt install qemu-user-static            # Ubuntu package, persistent via systemd-binfmt
# or, non-persistent until reboot, pinned image:
docker run --privileged --rm tonistiigi/binfmt@sha256:REVIEWED_DIGEST --install arm64
```

Check: `ls /proc/sys/fs/binfmt_misc/qemu-aarch64` exists and `docker run --rm --platform linux/arm64 python:3.13-slim-bookworm@sha256:a1165e… uname -m` prints `aarch64`. The `F` (fix-binary) flag must be set (`grep flags /proc/sys/fs/binfmt_misc/qemu-aarch64`) so BuildKit containers can use it.

## Build

Use the existing reviewed builder unchanged; it already passes `--platform linux/arm64` for all three roles, refuses a dirty tree and runs `access_image_audit.py` (default architecture `arm64`):

```sh
bash infra/hosting/m3/build_common_chatbot.sh FINAL_APPROVED_SHA /tmp/issue9-chatbot-build-SUFFIX
```

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
docker image inspect --format '{{.Os}}/{{.Architecture}} {{.Config.User}}' IMAGE   # linux/arm64 10001:10001
docker run --rm --platform linux/arm64 --network none --entrypoint sha256sum RUNNER_IMAGE \
  /usr/local/bin/codex /usr/local/bin/codex-code-mode-host
# fbbaec80…bceb3e  codex, fbccde22…f4fd8  codex-code-mode-host
```

Then `docker save`, checksum and import exactly as README.md describes.

### Expected digest relationship

Image digests are never identical across releases: `APP_GIT_SHA` / `APP_BUILT_AT` and the `backend/runner` copy (which now contains the amd64 pin table) are image inputs. "Unchanged production pin" is shown by the runner's installed `codex` and `codex-code-mode-host` SHA256 above, which are the same bytes as every earlier production runner, and by `PINS["arm64"]` in `backend/runner/install_codex.py` being byte-identical to the previous constants (`backend/config_tests/test_install_codex.py`).

## Alternative: build on the OCI node

If emulated builds are too slow, build natively on the production node as before (same commands; `--platform linux/arm64` is then native). Only the build location differs; the save/checksum/import procedure is the same.

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

`backend/requirements.lock` resolves entirely to binary wheels for cp313 manylinux aarch64 (`pip download --only-binary=:all:` in the pinned Python base, 2026-10-22). Compiled ones: cffi, cryptography, markupsafe, pillow, psycopg-binary, pydantic-core, sqlalchemy. No source build runs under emulation. The web image's platform-specific npm packages are resolved by pnpm inside the emulated arm64 build; they are confirmed by the first measured cross build below.

## Measurement

| Date | Host | Role | Cold build | Warm rebuild | Result |
| --- | --- | --- | --- | --- | --- |
| — | x86_64 dev server, qemu | api / runner / web | not measured | not measured | blocked on binfmt approval |

Record the first emulated build of all three roles here (wall clock from `build_common_chatbot.sh`, cold cache), together with the resulting digests. If any role takes more than about 30 minutes cold, prefer the OCI-node alternative for releases and keep emulation for occasional checks.
