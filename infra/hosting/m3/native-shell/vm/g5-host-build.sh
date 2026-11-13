#!/bin/sh
# #68 G5 step 1 (deployment design r2 §1): build the runner image for owner …5805's (a′) deployment from one
# committed source SHA on the development server (host Docker, never sudo), keep the build's own gate output, check
# the image, and save the runner archive. Changes nothing on the VM; the guest import is g5-import.sh.
#
#   infra/hosting/m3/native-shell/vm/g5-host-build.sh <full source sha> <new output dir>
#   infra/hosting/m3/native-shell/vm/g5-host-build.sh --classify <build log> <build rc>   (read-only helper)
#   infra/hosting/m3/native-shell/vm/g5-host-build.sh --gate-log <build log>              (read-only helper)
#
# The v1-host-build.sh pattern (5424e546…), runner target only. The context is `git archive <sha>`, never the working
# tree. The build runs with --progress=plain into <out>/build-runner.log (created new) under `timeout 2700`, so the
# RUN gate's own `runner image check ok` line is kept. A gate step reported CACHED fails closed (no line from this
# build). After the build: the gate is run again on the image, the v1 content checks, and the two Codex binaries'
# sha256 against install_codex.py:25-26 (amd64).
#
# A failed build prints exactly one classification line, `retryable: …` or `not retryable: …`. Retryable is only a
# network failure (connection error or timeout) while the install_codex.py step is the running one; a checksum
# mismatch, an HTTP error, a gate failure or any other step is not. This script never retries; each attempt is a new
# invocation with a new output dir (PM decision: at most 2 more attempts, only on `retryable`).
#
# rc: 0 built and every check ok; 2 usage; 3 refused before building; 5 the build (not the outer timeout) or save
#     failed; 8 built but a check failed (gate log, gate rerun, content, binary pins); 124 the outer timeout.
set -u
umask 077
GATE_STEP='RUN --network=none python -m runner.image_check'
INSTALL_STEP='RUN python /app/runner/install_codex.py'
CODEX_PIN=f34a4d2301892ae96c90097786bfe5dc269f187b6f69faf42a7b357b8c081e35    # backend/runner/install_codex.py:25 (amd64 codex_sha256)
HOST_PIN=b33e8a5283f3c65c2a0aca6d43a59cfe850f624d8fa16992e3cad4fcc27c14e1     # backend/runner/install_codex.py:26 (amd64 host_sha256)
RELEASE=https://github.com/openai/codex/releases/download/rust-v0.160.1
URLS="$RELEASE/codex-x86_64-unknown-linux-musl.tar.gz $RELEASE/codex-code-mode-host-x86_64-unknown-linux-musl.tar.gz"
BUILD_TIMEOUT=2700
NETWORK='URLError|TimeoutError|timed out|socket\.timeout|ConnectionResetError|ConnectionRefusedError|ConnectionAbortedError|RemoteDisconnected|IncompleteRead|Temporary failure in name resolution|Name or service not known|Network is unreachable'

usage() { echo 'usage: g5-host-build.sh <full sha> <output dir> | --classify <log> <rc> | --gate-log <log>'; exit 2; }

# BuildKit plain progress: a step header is `#<n> [<stage> <i>/<k>] <instruction>`; its output lines start `#<n> `.
step_ids() {  # the ids of the steps whose header contains the fixed text $2
  awk -v t="$2" '$1 ~ /^#[0-9]+$/ && substr($2, 1, 1) == "[" && index($0, t) {print $1}' "$1" | sort -u
}
step_lines() { awk -v id="$2" '$1 == id' "$1"; }
last_step() { awk '$1 ~ /^#[0-9]+$/ && substr($2, 1, 1) == "[" {id = $1} END {print id}' "$1"; }
count() { grep -c . || true; }

gate_check() {  # rc 0 and one `gate: ok` line, or rc 1 and one `not retryable: …` line
  ids=$(step_ids "$1" "$GATE_STEP")
  [ "$(printf '%s\n' "$ids" | count)" = 1 ] || { echo 'not retryable: the image_check step is not exactly one step in the log'; return 1; }
  lines=$(step_lines "$1" "$ids")
  if printf '%s\n' "$lines" | grep -qx "$ids CACHED"; then echo 'not retryable: gate step cached'; return 1; fi
  ok=$(printf '%s\n' "$lines" | grep -E "^$ids [0-9.]+ runner image check ok\$" | count)
  failed=$(printf '%s\n' "$lines" | grep 'runner image check failed' | count)
  done=$(printf '%s\n' "$lines" | grep -E "^$ids DONE " | count)
  if [ "$ok" = 1 ] && [ "$failed" = 0 ] && [ "$done" = 1 ]; then echo 'gate: ok (one line, step executed in this build)'; return 0; fi
  echo "not retryable: gate output not accepted (ok lines $ok, failed lines $failed, done $done)"; return 1
}

classify() {  # one line; never a retry for anything but a network failure in the running install_codex.py step
  ids=$(step_ids "$1" "$INSTALL_STEP")
  if [ "$(printf '%s\n' "$ids" | count)" != 1 ]; then echo 'not retryable: the install_codex step is not exactly one step in the log'; return 0; fi
  lines=$(step_lines "$1" "$ids")
  if printf '%s\n' "$lines" | grep -q 'checksum mismatch'; then echo 'not retryable: checksum mismatch'; return 0; fi
  if printf '%s\n' "$lines" | grep -q 'HTTPError'; then echo 'not retryable: http error'; return 0; fi
  if printf '%s\n' "$lines" | grep -qE "^$ids (DONE|CACHED)"; then echo 'not retryable: a later step failed'; return 0; fi
  if [ "$(last_step "$1")" != "$ids" ]; then echo 'not retryable: another step failed'; return 0; fi
  if [ "$2" = 124 ]; then echo 'retryable: timeout in install_codex'; return 0; fi
  if printf '%s\n' "$lines" | grep -qE "$NETWORK"; then echo 'retryable: network in install_codex'; return 0; fi
  echo 'not retryable: install_codex failed without a network marker'
}

case "${1:-}" in
  --classify) [ $# = 3 ] && [ -f "$2" ] || usage; classify "$2" "$3"; exit 0 ;;
  --gate-log) [ $# = 2 ] && [ -f "$2" ] || usage; gate_check "$2"; exit $? ;;
esac
[ $# = 2 ] || usage
sha=$1; out=$2
echo "$sha" | grep -Eqx '[0-9a-f]{40}' || { echo 'refuse: not a full commit sha'; exit 3; }
[ "$(git cat-file -t "$sha" 2>/dev/null)" = commit ] || { echo 'refuse: unknown commit'; exit 3; }
[ ! -e "$out" ] || { echo "refuse: $out exists"; exit 3; }
short=$(echo "$sha" | cut -c1-7)
tag="68-$short"
ref="anxious-dev-runner:$tag"
built=$(date -u -d "@$(git show -s --format=%ct "$sha")" +%Y-%m-%dT%H:%M:%SZ)  # commit time, UTC (write_release)
mkdir -m 0700 "$out" "$out/context" || { echo "refuse: cannot create $out"; exit 3; }
( umask 022; git archive "$sha" | tar -x -C "$out/context" ) || { echo 'git archive failed'; exit 3; }
log="$out/build-runner.log"
( set -C; : > "$log" ) 2>/dev/null || { echo "refuse: cannot create $log"; exit 3; }
echo "g5 host build: $(date -u +%Y-%m-%dT%H:%M:%SZ) source $sha tag $tag script $(sha256sum "$0" | cut -d' ' -f1)"

for url in $URLS; do  # measured only; decides nothing
  r=$(curl -sSL -o /dev/null --max-time 900 -w '%{http_code} %{size_download} %{time_total}' "$url" 2>/dev/null); s=$?
  echo "download probe ${url##*/}: curl rc $s, http/bytes/seconds ${r:-none}"
done

echo "build start $(date -u +%Y-%m-%dT%H:%M:%SZ) (timeout ${BUILD_TIMEOUT}s)"
timeout "$BUILD_TIMEOUT" docker build --progress=plain --platform linux/amd64 -f "$out/context/backend/Dockerfile" \
  --target runner --build-arg "APP_GIT_SHA=$sha" --build-arg "APP_BUILT_AT=$built" -t "$ref" "$out/context" >> "$log" 2>&1
b=$?
echo "build end $(date -u +%Y-%m-%dT%H:%M:%SZ) rc $b; log sha256 $(sha256sum "$log" | cut -d' ' -f1)"
if [ "$b" != 0 ]; then
  classify "$log" "$b"
  if [ "$b" = 124 ]; then echo 'result rc=124'; exit 124; fi
  echo 'result rc=5'; exit 5
fi
gate_check "$log" || { echo 'result rc=8'; exit 8; }

docker save -o "$out/anxious-dev-runner-$tag.tar" "$ref" || { echo 'save failed'; echo 'result rc=5'; exit 5; }
id=$(docker image inspect -f '{{.Id}}' "$ref") && [ -n "$id" ] || { echo 'inspect failed'; echo 'result rc=5'; exit 5; }
echo "docker.io/library/$ref|anxious-dev-runner-$tag.tar|$id" > "$out/images.txt" || { echo 'images.txt write failed'; exit 5; }
( cd "$out" && sha256sum "anxious-dev-runner-$tag.tar" > archives.sha256 ) || { echo 'archives.sha256 write failed'; exit 5; }
rm -rf "$out/context"
cat "$out/images.txt" "$out/archives.sha256"

bad=
rerun=$(docker run --rm --network none --user 10001:10001 --entrypoint python "$ref" -m runner.image_check 2>&1); s=$?
echo "gate rerun on the image: rc $s: $rerun"
[ "$s" = 0 ] && [ "$rerun" = 'runner image check ok' ] || bad="$bad gate-rerun"

# The v1-host-build.sh content checks (L41-70), unchanged, plus the binaries' sha256 printed.
docker run --rm --network none --entrypoint sh "$ref" -c '
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
  for f in /usr/local/bin/codex /usr/local/bin/codex-code-mode-host; do echo "binary $(sha256sum "$f" | cut -d" " -f1) $f"; done
' > "$out/checks.txt" 2>&1 || { cat "$out/checks.txt"; echo 'content check run failed'; echo 'result rc=8'; exit 8; }
cat "$out/checks.txt"
want_config=$(git show "$sha:backend/runner/codex-etc/config.toml" | sha256sum | cut -d' ' -f1)
want_req=$(git show "$sha:backend/runner/codex-etc/requirements.toml" | sha256sum | cut -d' ' -f1)
[ "$(grep -c '^pin .* True$' "$out/checks.txt")" = 2 ] || bad="$bad pin"
grep -q '^bwrap bubblewrap ' "$out/checks.txt" || bad="$bad bwrap"
grep -qx 'link codex' "$out/checks.txt" || bad="$bad link"
grep -qx "etc $want_config 0:0 644 /etc/codex/config.toml" "$out/checks.txt" || bad="$bad config"
grep -qx "etc $want_req 0:0 644 /etc/codex/requirements.toml" "$out/checks.txt" || bad="$bad requirements"
grep -qx 'etcdir 0:0 755' "$out/checks.txt" || bad="$bad etcdir"
grep -qx 'policy files ok' "$out/checks.txt" || bad="$bad policy"
grep -qx 'git absent' "$out/checks.txt" || bad="$bad git"
grep -qx 'user 10001:10001' "$out/checks.txt" || bad="$bad user"
grep -qx "binary $CODEX_PIN /usr/local/bin/codex" "$out/checks.txt" || bad="$bad codex-binary"
grep -qx "binary $HOST_PIN /usr/local/bin/codex-code-mode-host" "$out/checks.txt" || bad="$bad host-binary"
[ -z "$bad" ] || { echo "check failed:$bad"; echo 'result rc=8'; exit 8; }
echo "tag $tag"
echo "host id $id"
echo "archive sha256 $(cut -d' ' -f1 "$out/archives.sha256") anxious-dev-runner-$tag.tar"
echo 'result rc=0'
