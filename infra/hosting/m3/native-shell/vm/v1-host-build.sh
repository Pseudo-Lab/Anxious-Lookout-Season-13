#!/bin/sh
# #68 dev-VM V1 (host part): build the runtime, runner and provisioner images for the development VM from one
# committed source SHA, save them as archives and check the runner image's #68 contents. Runs on the development
# server (host Docker, never sudo). Changes nothing on the VM; the guest import is v1-import.sh.
#
#   infra/hosting/m3/native-shell/vm/v1-host-build.sh <full source sha> <output dir>
#
# The build context is `git archive <sha>`, never the working tree. Output: one archive per image, archives.sha256,
# images.txt (reference|archive|host image ID) and checks.txt (runner image contents). rc: 0 built and checked;
# 2 usage; 3 refused before building; 5 a build or save failed; 8 built but a content check failed.
set -u
umask 077
sha=${1:-}; out=${2:-}
[ -n "$sha" ] && [ -n "$out" ] || { echo 'usage: v1-host-build.sh <full sha> <output dir>'; exit 2; }
echo "$sha" | grep -Eqx '[0-9a-f]{40}' || { echo 'refuse: not a full commit sha'; exit 3; }
[ "$(git cat-file -t "$sha" 2>/dev/null)" = commit ] || { echo 'refuse: unknown commit'; exit 3; }
[ ! -e "$out" ] || { echo "refuse: $out exists"; exit 3; }
short=$(echo "$sha" | cut -c1-7)
tag="68-$short"
built=$(date -u -d "@$(git show -s --format=%ct "$sha")" +%Y-%m-%dT%H:%M:%SZ)  # commit time, UTC (write_release)
mkdir -m 0700 "$out" "$out/context" || { echo "refuse: cannot create $out"; exit 3; }
# The build context keeps the repository's ordinary modes (umask 022): COPY carries file modes into the image,
# and the runner (UID 10001) must read /app. Only the output directory itself stays private.
( umask 022; git archive "$sha" | tar -x -C "$out/context" ) || { echo 'git archive failed'; exit 3; }
echo "v1 host: $(date -u +%Y-%m-%dT%H:%M:%SZ) source $sha tag $tag"

for target in runtime runner provisioner; do
  ref="anxious-dev-$target:$tag"
  docker build -q --platform linux/amd64 -f "$out/context/backend/Dockerfile" --target "$target" \
    --build-arg "APP_GIT_SHA=$sha" --build-arg "APP_BUILT_AT=$built" -t "$ref" "$out/context" >/dev/null \
    || { echo "build $target failed"; exit 5; }
  docker save -o "$out/anxious-dev-$target-$tag.tar" "$ref" || { echo "save $target failed"; exit 5; }
  id=$(docker image inspect -f '{{.Id}}' "$ref") && [ -n "$id" ] || { echo "inspect $target failed"; exit 5; }
  echo "docker.io/library/$ref|anxious-dev-$target-$tag.tar|$id" >> "$out/images.txt" || { echo 'images.txt write failed'; exit 5; }
done
( cd "$out" && sha256sum anxious-dev-*-"$tag".tar > archives.sha256 ) || { echo 'archives.sha256 write failed'; exit 5; }
[ "$(wc -l < "$out/images.txt")" = 3 ] && [ "$(wc -l < "$out/archives.sha256")" = 3 ] || { echo 'chain outputs incomplete'; exit 5; }
rm -rf "$out/context"
cat "$out/images.txt" "$out/archives.sha256"

# Runner image contents (#68): pinned Codex binaries, bubblewrap, the codex-linux-sandbox link, the /etc/codex
# policy files exactly as committed, and no git (Codex would run it outside the sandbox in the cwd).
runner="anxious-dev-runner:$tag"
docker run --rm --network none --entrypoint sh "$runner" -c '
  python -c "import runner.install_codex as p, hashlib
pin = p.pinned()
for path, key in ((\"/usr/local/bin/codex\", \"codex_sha256\"), (\"/usr/local/bin/codex-code-mode-host\", \"host_sha256\")):
    print(\"pin\", path, hashlib.sha256(open(path, \"rb\").read()).hexdigest() == pin[key])"
  echo "bwrap $(bwrap --version)"
  echo "link $(readlink /usr/local/bin/codex-linux-sandbox)"
  echo "etcdir $(stat -c "%u:%g %a" /etc/codex)"
  for f in /etc/codex/config.toml /etc/codex/requirements.toml; do echo "etc $(sha256sum "$f" | cut -d" " -f1) $(stat -c "%u:%g %a" "$f") $f"; done
  python -c "import runner.native_policy as p; p.check_files(\"/state\"); print(\"policy files ok\")"
  command -v git >/dev/null && echo "git present" || echo "git absent"
  echo "user $(id -u):$(id -g)"
' > "$out/checks.txt" 2>&1 || { cat "$out/checks.txt"; echo 'content check run failed'; exit 8; }
cat "$out/checks.txt"
want_config=$(git show "$sha:backend/runner/codex-etc/config.toml" | sha256sum | cut -d' ' -f1)
want_req=$(git show "$sha:backend/runner/codex-etc/requirements.toml" | sha256sum | cut -d' ' -f1)
bad=
[ "$(grep -c '^pin .* True$' "$out/checks.txt")" = 2 ] || bad="$bad pin"
grep -q '^bwrap bubblewrap ' "$out/checks.txt" || bad="$bad bwrap"
grep -qx 'link codex' "$out/checks.txt" || bad="$bad link"
grep -qx "etc $want_config 0:0 644 /etc/codex/config.toml" "$out/checks.txt" || bad="$bad config"
grep -qx "etc $want_req 0:0 644 /etc/codex/requirements.toml" "$out/checks.txt" || bad="$bad requirements"
grep -qx 'etcdir 0:0 755' "$out/checks.txt" || bad="$bad etcdir"
grep -qx 'policy files ok' "$out/checks.txt" || bad="$bad policy"
grep -qx 'git absent' "$out/checks.txt" || bad="$bad git"
grep -qx 'user 10001:10001' "$out/checks.txt" || bad="$bad user"
[ -z "$bad" ] || { echo "content check failed:$bad"; exit 8; }
echo 'result rc=0'
