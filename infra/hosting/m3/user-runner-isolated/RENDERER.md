# Offline preparation renderer

`ops.runner_render` renders only the four reviewed preparation templates below.
It never invokes kubectl or a network client and has no apply mode. `--check` is
required and means validate/render files, not Kubernetes server dry-run. This tool
is not a Kubernetes schema validator or CEL renderer. Use exact reviewed source
and image; do not feed modified/unreviewed templates under the same filenames.

Non-secret inputs are in rehearsal-values.json, matching REHEARSAL-INPUTS.md.
Unknown or missing keys, duplicate JSON keys, non-string values, unresolved or
unknown placeholders, invalid UUID/names, changed namespace/class/access mode or
sizes are refused. Only storageClassName's input is allowed empty. Strings remain
strings and numbers/bools/null in templates retain their types. The accepted
namespace is codex-runner-rehearsal, class empty, access mode RWO and sizes1Gi.
This limited scope does not approve arbitrary future targets or capacities.

Secret input is the **raw synthetic token**, not base64:32–256 printable ASCII
non-whitespace bytes with no newline. Renderer base64 encodes it internally. The
input must be a regular single-link file owned by the invoking UID, mode0600,
with no symlink component. The file value is never an argument/environment value.
Only its private path is supplied. Existing private credential-generation rules
still apply; no provider credential is permitted.

## PM invocation inside Docker

Build/use a reviewed runtime image containing this ops module; image digest/source
provenance is part of the execution record. Mount the reviewed template directory
read-only, an existing owner-private input directory read-only, and a fresh output
parent (mode0700) writable. Run as the same host UID that owns the token and output
parent; do not relax token permissions to accommodate a default image UID.
No kubeconfig, host filesystem root, Docker socket or network is needed.

```sh
# RUNNER_RENDER_IMAGE: reviewed image digest with ops.runner_render
# RUNNER_PRIVATE_INPUT: existing absolute mode0700 directory containing token(mode0600)
# RUNNER_RENDER_PARENT: existing absolute mode0700 directory for new output subdirs
# Directories and variables contain paths only, never Secret values.
docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
  --user "$(id -u):$(id -g)" --tmpfs /tmp:rw \
  --mount "type=bind,src=$PWD/infra/hosting/m3/user-runner-isolated,dst=/templates,readonly" \
  --mount "type=bind,src=$RUNNER_PRIVATE_INPUT,dst=/private-in,readonly" \
  --mount "type=bind,src=$RUNNER_RENDER_PARENT,dst=/output" \
  --entrypoint python "$RUNNER_RENDER_IMAGE" -B -m ops.runner_render --check \
  --template /templates/00-foundation.json.in \
  --template /templates/05-static-volumes.json.in \
  --template /templates/10-static-claims.json.in \
  --template /templates/20-transport-secret.json.in \
  --values /templates/rehearsal-values.json \
  --secret-file /private-in/token \
  --output /output/review --private-output /output/private
```

Both output directories must not exist, must be different/disjoint, and must have
existing parents without symlinks. Renderer creates them0700 and files0600. Every
object gets an ordered prefix:00 Namespace,01 quota,02 default-deny,03–04 SAs,
05–06 PVs,07–08 PVCs,09 Secret. Template argument order does not alter this order.
Do not run directory-wide kubectl apply: review each object and follow the approved
foundation/host/PV/PVC/private Secret execution sequence.

`review/` contains9 ordinary objects plus `09-...redacted.json` for Secret shape
review. The redacted token marker is deliberately unusable and this file must
**never** be applied. `private/` contains only the actual Secret object and its
private checksum index. Each directory contains sha256.json with exact byte
hashes for its own object files; the actual Secret hash appears only in private/.
The checksum index is not an object to apply and is not self-hashed.

stdout is only a generic success count; failure text contains no inputs/paths.
Do not print/cat the private output or attach it to evidence. Share only reviewed
non-secret objects, redacted copy and their hash index. PM records private Secret
file checksum privately; publication of a token hash is unnecessary.

All validation/rendering completes before output creation. Invalid inputs produce
no outputs. An OS/I/O failure during writing can leave a private partial output;
stop and preserve it for investigation, do not automatically delete/retry/overwrite.
Existing output paths always refuse. A new output directory does not authorize a
second namespace/PV/Pod creation or token rotation. Kubernetes validation/admission,
SELinux access and actual bindings remain later reviewed steps.
