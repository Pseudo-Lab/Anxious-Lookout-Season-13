# Issue 14 Bash boundary investigation (2026-10-16)

Bash remains disabled. Do not treat enabling `features.shell_tool` or selecting
workspace-write as proof of command isolation. PM decides whether this patch should
include Bash after reviewing these facts and alternatives.

## Reproduced facts

The approved pinned runner image locally identified as
`sha256:e8b91b0c4291c2412f5ad58796034e01931d8b2a120a0a21ec7f9b40ea2454de`
contains Codex 0.160.1, Bash, coreutils `timeout`, util-linux `prlimit`/`unshare`,
and Python. It does not contain `bwrap`, `curl`, or `jq`.

In an isolated Docker container with `--network none --read-only --cap-drop ALL
--security-opt no-new-privileges` and the default UID 10001:

- `codex sandbox linux --help` exits 101: the sandbox launcher reports bubblewrap
  unavailable (neither system bwrap nor bundled codex-resources/bwrap).
- `unshare --user --map-root-user -- true` exits 1, Operation not permitted.

The second result is a Docker default-seccomp/kernel observation, not a live k3s
result. k3s RuntimeDefault can differ by runtime version/profile and host userns
settings. Installing bubblewrap is necessary but cannot alone establish that an
unprivileged nested sandbox will work. A PM-owned disposable restricted Pod probe
must test actual bwrap namespace creation and fail-closed behavior. Do not relax
Pod privileges, add SYS_ADMIN, use host namespaces, or change CNP as a shortcut.

A candidate runner layer would install `bubblewrap curl jq ca-certificates` via apt
with `--no-install-recommends`; explicitly retain `bash coreutils util-linux` and
existing Python. Package addition alone is not shipped here: it would expose no
working Bash and has not passed the actual isolation probe. PM owns production
build/import/transition. A future Dockerfile change must be paired with enforcement.

## Enforcement gaps

- Pod manifests currently set UID/GID 10001, non-root, RuntimeDefault seccomp,
  read-only rootfs, no privilege escalation, drop ALL and no host namespaces.
  `Plan.manifest()` does **not** currently specify CPU/memory requests or limits.
  Do not claim those bounds exist. Adding them changes immutable planned Pod
  manifests, so roll through STOPPED/binding handling and reverify adoption.
- Native storage has a 5 GiB filesystem bound; private control is separate 1 GiB,
  and tmpfs is 64 MiB. This does not bound CPU, process count or execution time.
- The adapter reserves one native turn per owner, not one shell command. Native
  tool parallelism/background descendants require their own enforced serialization.
- `receive()` has a 120-second per-message wait; it is not a hard command deadline.
  A prompt or model-supplied timeout is not enforcement. `timeout` wrappers can be
  bypassed if native shell can invoke arbitrary commands without that wrapper.
- Workspace-write bounds writes; it does not by itself deny reads of `/control`,
  `/transport`, `/state/codex`, broker metadata, sibling process `/proc` files or
  inherited descriptors. The current same-UID layout contains authentication and
  transport data. No adversarial non-disclosure proof exists for enabling shell.
- Existing CNP permits runner-to-provider traffic for Codex. Bash needs a separate
  enforced no-network namespace/profile; retaining CNP alone is not deny-network.

## Alternatives for PM/user decision

1. Defer Bash; ship owner DB tools, native web search and timing first. This is the
   current implemented state. No sandbox or CNP weakening is required.
2. Build a distinct constrained command executor with only the workspace mounted,
   no auth/control/transport volumes or shared process namespace, no network,
   one command lease, server-set wall deadline plus descendant termination, and
   CPU/memory/PID/output bounds. A runner-side subprocess under the same UID with
   a timeout alone is insufficient. Prefer a separate container/process boundary;
   its API and Pod lifecycle must be designed and reviewed as additional scope.
3. Continue native shell only if a restricted k3s bwrap probe succeeds and a verified
   native permissions profile hides every private path/process/descriptor. Also
   prove command serialization, unmodifiable deadline and full descendant cleanup.
   Lack of any one proof keeps shell disabled. Sandbox flags alone do not satisfy
   this alternative.

The official configuration reference describes named filesystem permissions and
network settings, but those descriptions are not enforcement evidence for this
image/kernel combination: https://developers.openai.com/codex/config-reference/.
No live Pod, credentials, network policy, existing binding or production image was
changed by these investigations.
